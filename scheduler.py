"""
Scheduler module — manages the upload queue and ensures max 6 uploads/day.
Processes pending and scheduled videos from Google Sheets.
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
    """Manages the video upload queue with daily limit enforcement."""

    def __init__(self):
        self.sheets = SheetsManager()
        self.drive = DriveUploader()
        self.youtube = None  # Lazy init (OAuth2 requires browser)
        self.temp_dir = config.TEMP_DIR

    def _get_youtube(self) -> YouTubeUploader:
        """Lazy initialize YouTube uploader."""
        if self.youtube is None:
            self.youtube = YouTubeUploader()
        return self.youtube

    def process_queue(self) -> list[dict]:
        """
        Process the upload queue. Upload pending/scheduled videos
        up to the daily limit.

        Returns:
            List of results for each processed video.
        """
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        tomorrow = (datetime.now(WIB) + timedelta(days=1)).strftime("%Y-%m-%d")

        uploads_today = self.sheets.count_uploads_today()
        remaining = config.MAX_UPLOADS_PER_DAY - uploads_today

        logger.info(
            f"Queue check — Uploads today: {uploads_today}, "
            f"Remaining: {remaining}"
        )

        if remaining <= 0:
            logger.info("Daily upload limit reached. Scheduling remaining for tomorrow.")
            self._schedule_remaining(tomorrow)
            return []

        # Get videos to process (scheduled for today first, then pending)
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

        # Schedule any remaining pending videos for tomorrow
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

            # Step 3: Upload to YouTube
            self.sheets.update_status(row, "uploading")

            yt = self._get_youtube()
            result = yt.upload(
                file_path=local_path,
                title=video["title"],
                description=video.get("description", ""),
                tags=video.get("tags", ""),
            )

            # Step 4: Update sheet with YouTube link
            self.sheets.set_youtube_link(row, result["youtube_link"])

            # Step 5: Clean up temp file
            if os.path.exists(local_path):
                os.remove(local_path)
                logger.info(f"Cleaned up temp file: {local_path}")

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
        # https://drive.google.com/file/d/FILE_ID/view
        if "/file/d/" in drive_link:
            parts = drive_link.split("/file/d/")[1]
            return parts.split("/")[0].split("?")[0]

        # https://drive.google.com/open?id=FILE_ID
        if "id=" in drive_link:
            return drive_link.split("id=")[1].split("&")[0]

        # Could be a raw file ID
        return drive_link.strip()

    def get_status_message(self) -> str:
        """Generate a human-readable status message."""
        summary = self.sheets.get_queue_summary()

        msg = (
            "📊 **Upload Queue Status**\n\n"
            f"📹 Total videos: {summary['total']}\n"
            f"⏳ Pending: {summary['pending']}\n"
            f"📅 Scheduled: {summary['scheduled']}\n"
            f"✅ Uploaded: {summary['uploaded']}\n"
            f"❌ Failed: {summary['failed']}\n\n"
            f"📤 Uploads today: {summary['uploads_today']}/{config.MAX_UPLOADS_PER_DAY}\n"
            f"🔄 Remaining today: {summary['remaining_today']}"
        )

        return msg
