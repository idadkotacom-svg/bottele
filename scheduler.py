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
from groq_metadata import generate_metadata

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))


class Scheduler:
    """Manages the video upload queue with viral hour scheduling."""

    def __init__(self):
        self.sheets = SheetsManager()
        self.drive = DriveUploader()
        self._youtube_cache = {}  # channel_name -> YouTubeUploader
        self.temp_dir = config.TEMP_DIR

    def _get_youtube(self, channel_name: str = None) -> YouTubeUploader:
        """Get or create YouTube uploader for a specific channel."""
        channel = channel_name or config.DEFAULT_CHANNEL
        if channel not in self._youtube_cache:
            self._youtube_cache[channel] = YouTubeUploader(channel)
        return self._youtube_cache[channel]

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
        Process the upload queue. Only uploads if:
        1. Current time is within a scheduled window
        2. Daily limit not reached
        3. There are pending videos

        Returns:
            List of results for each processed video.
        """
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        tomorrow = (datetime.now(WIB) + timedelta(days=1)).strftime("%Y-%m-%d")

        uploads_today = self.sheets.count_uploads_today()
        remaining = config.MAX_UPLOADS_PER_DAY - uploads_today

        logger.info(
            f"Queue check — Uploads today: {uploads_today}/"
            f"{config.MAX_UPLOADS_PER_DAY}, Remaining: {remaining}"
        )

        if remaining <= 0:
            logger.info("Daily upload limit reached.")
            self._schedule_remaining(tomorrow)
            return []

        # Check if now is a scheduled upload time
        if not self.is_upload_time():
            next_time = self.get_next_upload_time()
            logger.info(f"Not upload time yet. Next: {next_time}")
            return []

        # Get videos to process (scheduled for today first, then pending)
        scheduled = self.sheets.get_scheduled_videos(today)
        pending = self.sheets.get_pending_videos()
        to_process = scheduled + pending

        if not to_process:
            logger.info("No videos to process.")
            return []

        # Upload only 1 video per scheduled time slot
        video = to_process[0]
        result = self._process_single(video)
        results = [result]

        if result.get("success"):
            remaining -= 1

        # Schedule remaining if limit reached
        if remaining <= 0:
            self._schedule_remaining(tomorrow)

        return results

    def force_upload(self) -> list[dict]:
        """
        Force process queue regardless of schedule (for /upload command).
        Respects daily limit only.
        """
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        tomorrow = (datetime.now(WIB) + timedelta(days=1)).strftime("%Y-%m-%d")

        uploads_today = self.sheets.count_uploads_today()
        remaining = config.MAX_UPLOADS_PER_DAY - uploads_today

        if remaining <= 0:
            self._schedule_remaining(tomorrow)
            return []

        scheduled = self.sheets.get_scheduled_videos(today)
        pending = self.sheets.get_pending_videos()
        to_process = scheduled + pending

        results = []
        for video in to_process[:remaining]:
            result = self._process_single(video)
            results.append(result)
            if result.get("success"):
                remaining -= 1
                if remaining <= 0:
                    break

        if remaining <= 0:
            self._schedule_remaining(tomorrow)

        return results

    def _process_single(self, video: dict) -> dict:
        """
        Process a single video: generate metadata if missing,
        download from Drive, upload to YouTube.
        """
        row = video["row"]
        filename = video["filename"]

        logger.info(f"Processing row {row}: '{filename}'")

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

            # Step 3: Upload to YouTube (using channel from video data)
            channel = video.get("channel", config.DEFAULT_CHANNEL)
            self.sheets.update_status(row, "uploading")

            yt = self._get_youtube(channel)
            result = yt.upload(
                file_path=local_path,
                title=video["title"],
                description=video.get("description", ""),
                tags=video.get("tags", ""),
            )

            # Step 4: Update sheet with YouTube link
            self.sheets.set_youtube_link(row, result["youtube_link"])

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
                "youtube_link": result["youtube_link"],
            }

        except Exception as e:
            logger.error(f"Failed to process row {row}: {e}")
            self.sheets.update_status(row, "failed")
            return {
                "success": False,
                "row": row,
                "filename": filename,
                "error": str(e),
            }

    def _schedule_remaining(self, date_str: str):
        """Schedule all remaining pending videos for a future date."""
        pending = self.sheets.get_pending_videos()
        for video in pending:
            self.sheets.set_scheduled_date(video["row"], date_str)
            logger.info(
                f"Scheduled '{video['filename']}' for {date_str}"
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
        summary = self.sheets.get_queue_summary()
        next_time = self.get_next_upload_time()
        is_upload = self.is_upload_time()

        schedule_str = " → ".join(config.UPLOAD_SCHEDULE_HOURS)
        now_str = datetime.now(WIB).strftime("%H:%M WIB")

        msg = (
            "📊 **Upload Queue Status**\n\n"
            f"📹 Total videos: {summary['total']}\n"
            f"⏳ Pending: {summary['pending']}\n"
            f"📅 Scheduled: {summary['scheduled']}\n"
            f"✅ Uploaded: {summary['uploaded']}\n"
            f"❌ Failed: {summary['failed']}\n\n"
            f"📤 Uploads today: {summary['uploads_today']}/{config.MAX_UPLOADS_PER_DAY}\n"
            f"🔄 Remaining today: {summary['remaining_today']}\n\n"
            f"🕐 Schedule: `{schedule_str}` WIB\n"
            f"⏰ Now: {now_str}\n"
            f"⏭️ Next upload: {next_time}\n"
            f"{'🟢 Upload window ACTIVE' if is_upload else '🔴 Waiting for next window'}"
        )

        return msg
