"""
Scheduler module — manages the upload queue with timed viral hour uploads.
Uploads videos at scheduled times (default: 21:00, 00:00, 03:00 WIB)
targeting US/EU peak hours. Max 3 uploads per day.
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import config
from sheets_manager import SheetsManager
from drive_uploader import DriveUploader
from youtube_uploader import YouTubeUploader
from facebook_uploader import FacebookUploader
from groq_metadata import generate_metadata

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))


class Scheduler:
    """Manages the video upload queue with viral hour scheduling."""

    def __init__(self):
        self.sheets = SheetsManager()
        self.drive = DriveUploader()
        self._youtube_cache = {}  # channel_name -> YouTubeUploader
        self._facebook_uploader = FacebookUploader()
        self.temp_dir = config.TEMP_DIR

    def _get_youtube(self, channel_name: str = None) -> YouTubeUploader:
        """Get or create YouTube uploader for a specific channel."""
        channel = channel_name or config.DEFAULT_CHANNEL
        if channel not in self._youtube_cache:
            self._youtube_cache[channel] = YouTubeUploader(channel)
        return self._youtube_cache[channel]
        
    def _get_facebook(self) -> FacebookUploader:
        """Get the Facebook Uploader instance."""
        return self._facebook_uploader

    def is_upload_time(self) -> bool:
        """
        Check if current time is within a scheduled upload window.
        Returns True if we're within ±5 minutes of a scheduled time.
        """
        now = datetime.now(WIB)
        current_minutes = now.hour * 60 + now.minute

        for time_str in config.UPLOAD_SCHEDULE_HOURS:
            try:
                h, m = map(int, time_str.split(":"))
                scheduled_minutes = h * 60 + m

                # Check if within ±30 minute window to prevent stuck pending videos
                diff = abs(current_minutes - scheduled_minutes)
                # Handle midnight wrap (e.g., 23:58 vs 00:00)
                if diff > 720:  # more than 12 hours
                    diff = 1440 - diff

                if diff <= 30:
                    return True
            except (ValueError, AttributeError):
                continue

        return False

    def get_next_upload_time(self) -> str:
        """Get the next scheduled upload time as a string."""
        now = datetime.now(WIB)
        current_minutes = now.hour * 60 + now.minute

        upcoming = []
        for time_str in config.UPLOAD_SCHEDULE_HOURS:
            try:
                h, m = map(int, time_str.split(":"))
                scheduled_minutes = h * 60 + m
                diff = scheduled_minutes - current_minutes
                if diff < 0:
                    diff += 1440  # next day
                upcoming.append((diff, time_str))
            except (ValueError, AttributeError):
                continue

        if upcoming:
            upcoming.sort()
            return upcoming[0][1] + " WIB"
        return "N/A"

    def process_queue(self) -> list[dict]:
        """
        Process the upload queue for both platforms. Only uploads if:
        1. Current time is within a scheduled window
        2. Daily limit not reached per platform
        3. There are pending videos
        """
        if not self.is_upload_time():
            next_time = self.get_next_upload_time()
            logger.info(f"Not upload time yet. Next: {next_time}")
            return []

        results = []
        for platform in ["youtube", "facebook"]:
            res = self._process_platform_queue(platform)
            results.extend(res)
            
        return results

    def force_upload(self) -> list[dict]:
        """
        Force process queue regardless of schedule (for /upload command).
        Respects daily limit.
        """
        results = []
        for platform in ["youtube", "facebook"]:
            res = self._process_platform_queue(platform, force=True)
            results.extend(res)
            
        return results

    def _process_platform_queue(self, platform: str, force: bool = False) -> list[dict]:
        """Process the upload queue for a specific platform."""
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        tomorrow = (datetime.now(WIB) + timedelta(days=1)).strftime("%Y-%m-%d")

        uploads_today = self.sheets.count_uploads_today(platform=platform)
        
        if platform == "youtube":
            max_uploads = config.MAX_UPLOADS_PER_DAY_YOUTUBE
        else:
            max_uploads = config.MAX_UPLOADS_PER_DAY_FACEBOOK
            
        remaining = max_uploads - uploads_today

        logger.info(
            f"Queue check {platform} — Uploads today: {uploads_today}/"
            f"{max_uploads}, Remaining: {remaining}"
        )

        if remaining <= 0:
            logger.info(f"Daily upload limit reached for {platform}.")
            self._schedule_remaining(tomorrow, platform)
            return []

        # Get videos to process (scheduled for today first, then pending)
        scheduled = self.sheets.get_scheduled_videos(today, platform=platform)
        pending = self.sheets.get_pending_videos(platform=platform)
        to_process = scheduled + pending

        if not to_process:
            logger.info(f"No videos to process for {platform}.")
            return []

        results = []
        
        # LIMIT:
        # YouTube ignores upload time windows and processes all up to quota immediately.
        # Facebook respects the upload time window unless forced.
        if platform == "youtube":
            limit = remaining
        else:
            limit = remaining if force else 1
        
        for video in to_process[:limit]:
            result = self._process_single(video, platform)
            results.append(result)
            if result.get("success"):
                remaining -= 1
                if remaining <= 0:
                    break

        # Schedule remaining if limit reached
        if remaining <= 0:
            self._schedule_remaining(tomorrow, platform)

        return results

    def _process_single(self, video: dict, platform: str) -> dict:
        """
        Process a single video: generate metadata if missing,
        download from Drive, upload to platform.
        """
        row = video["row"]
        filename = video["filename"]

        logger.info(f"Processing row {row} on {platform}: '{filename}'")

        try:
            # Step 1: Generate metadata if title is empty
            if not video.get("title", "").strip():
                logger.info(f"Generating metadata for '{filename}'...")
                metadata = generate_metadata(filename)
                self.sheets.update_metadata(
                    row,
                    metadata["title"],
                    metadata["description"],
                    metadata["tags"],
                    platform=platform
                )
                video.update(metadata)

            # Step 2: Download from Google Drive to temp
            drive_link = video["drive_link"]
            file_id = self._extract_drive_id(drive_link)

            if not file_id:
                raise ValueError(f"Could not extract Drive file ID from: {drive_link}")

            local_path = str(self.temp_dir / filename)
            logger.info(f"Downloading from Drive: {file_id}")
            self.drive.download(file_id, local_path)

            # Step 3: Upload to platform
            channel = video.get("channel", config.DEFAULT_CHANNEL)
            self.sheets.update_status(row, "uploading", platform=platform)

            video_link = ""

            if platform == "youtube":
                # Calculate publish_at based on scheduled_date
                scheduled_date_str = video.get("scheduled_date", "")
                if not scheduled_date_str:
                    scheduled_date_str = datetime.now(WIB).strftime("%Y-%m-%d")
                    
                slot_index = self.sheets.count_uploaded_for_date(scheduled_date_str, platform)
                if slot_index >= len(config.UPLOAD_SCHEDULE_HOURS):
                    slot_index = len(config.UPLOAD_SCHEDULE_HOURS) - 1
                    
                time_str = config.UPLOAD_SCHEDULE_HOURS[slot_index]
                
                # Combine date and time
                publish_local = datetime.strptime(f"{scheduled_date_str} {time_str}", "%Y-%m-%d %H:%M")
                publish_local = publish_local.replace(tzinfo=WIB)
                
                # YouTube API requires publishAt to be >= 15 minutes in the future
                if publish_local <= datetime.now(WIB) + timedelta(minutes=15):
                    publish_local = datetime.now(WIB) + timedelta(minutes=20)
                    
                publish_at_iso = publish_local.isoformat()
                
                yt = self._get_youtube(channel)
                result = yt.upload(
                    file_path=local_path,
                    title=video["title"],
                    description=video.get("description", ""),
                    tags=video.get("tags", ""),
                    publish_at=publish_at_iso
                )
                video_link = result["youtube_link"]
            elif platform == "facebook":
                fb = self._get_facebook()
                desc = f"{video['title']}\n\n{video.get('description', '')}"
                result = fb.upload_reel(
                    file_path=local_path,
                    description=desc
                )
                if not result["success"]:
                    raise Exception(f"Facebook Graph API Error: {result.get('error')}")
                video_link = result["url"]

            # Step 4: Update sheet with link
            self.sheets.set_youtube_link(row, video_link, platform=platform)

            # Step 5: Clean up temp file
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
                    logger.info(f"Cleaned up temp file: {local_path}")
            except PermissionError:
                logger.warning(f"Could not remove temp file: {local_path}")

            return {
                "success": True,
                "row": row,
                "filename": filename,
                "youtube_link": video_link,
            }

        except Exception as e:
            logger.error(f"Failed to process row {row}: {e}")
            self.sheets.update_status(row, "failed", platform=platform)
            return {
                "success": False,
                "row": row,
                "filename": filename,
                "error": str(e),
            }

    def _schedule_remaining(self, date_str: str, platform: str):
        """Schedule all remaining pending videos for a future date."""
        pending = self.sheets.get_pending_videos(platform=platform)
        for video in pending:
            self.sheets.set_scheduled_date(video["row"], date_str, platform=platform)
            logger.info(
                f"Scheduled '{video['filename']}' for {date_str} on {platform}"
            )

    @staticmethod
    def _extract_drive_id(drive_link: str) -> str:
        """Extract file ID from a Google Drive link."""
        if not drive_link:
            return ""

        # Handle various Drive link formats
        if "/file/d/" in drive_link:
            parts = drive_link.split("/file/d/")[1]
            return parts.split("/")[0].split("?")[0]

        if "id=" in drive_link:
            return drive_link.split("id=")[1].split("&")[0]

        return drive_link.strip()

    def get_status_message(self) -> str:
        """Generate a human-readable status message."""
        yt_summary = self.sheets.get_queue_summary(platform="youtube")
        fb_summary = self.sheets.get_queue_summary(platform="facebook")
        
        next_time = self.get_next_upload_time()
        is_upload = self.is_upload_time()

        schedule_str = " → ".join(config.UPLOAD_SCHEDULE_HOURS)
        now_str = datetime.now(WIB).strftime("%H:%M WIB")

        msg = (
            "📊 **Upload Queue Status**\n\n"
            f"📺 <b>YouTube</b>:\n"
            f"📹 Total: {yt_summary['total']} | ⏳ Pending: {yt_summary['pending']} | 📅 Scheduled: {yt_summary['scheduled']}\n"
            f"📤 Uploads today: {yt_summary['uploads_today']}/{config.MAX_UPLOADS_PER_DAY}\n\n"
            f"📘 <b>Facebook</b>:\n"
            f"📹 Total: {fb_summary['total']} | ⏳ Pending: {fb_summary['pending']} | 📅 Scheduled: {fb_summary['scheduled']}\n"
            f"📤 Uploads today: {fb_summary['uploads_today']}/{config.MAX_UPLOADS_PER_DAY}\n\n"
            f"🕐 Schedule: `{schedule_str}` WIB\n"
            f"⏰ Now: {now_str}\n"
            f"⏭️ Next upload: {next_time}\n"
            f"{'🟢 Upload window ACTIVE' if is_upload else '🔴 Waiting for next window'}"
        )

        return msg
