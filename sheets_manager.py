"""
Google Sheets manager — manages the upload queue and logging.
Uses a service account for authentication via gspread.
"""
import logging
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Timezone WIB (UTC+7)
WIB = timezone(timedelta(hours=7))

class SheetsManager:
    """Manages Google Sheets for video upload queue and logging."""

    def __init__(self):
        self.sheet = None
        self.fb_sheet = None
        self.ideas_sheet = None
        self._init_sheet()

    def _get_credentials(self):
        """Helper to get Google service account credentials."""
        return Credentials.from_service_account_file(
            config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )

    def _init_sheet(self):
        """Initialize connection to Google Sheets and ensure both sheets exist."""
        try:
            creds = self._get_credentials()
            client = gspread.authorize(creds)
            spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)

            # Get or create Main queue sheet (YouTube)
            try:
                self.sheet = spreadsheet.worksheet("Queue")
            except gspread.exceptions.WorksheetNotFound:
                logger.info("Sheet 'Queue' not found, creating it...")
                self.sheet = spreadsheet.add_worksheet("Queue", 1000, 10)
                
            # Get or create Facebook queue sheet
            try:
                self.fb_sheet = spreadsheet.worksheet("Queue_FB")
            except gspread.exceptions.WorksheetNotFound:
                logger.info("Sheet 'Queue_FB' not found, creating it...")
                self.fb_sheet = spreadsheet.add_worksheet("Queue_FB", 1000, 10)
                
            # Get or create Ideas sheet
            try:
                self.ideas_sheet = spreadsheet.worksheet("Ideas")
            except gspread.exceptions.WorksheetNotFound:
                logger.info("Sheet 'Ideas' not found, creating it...")
                self.ideas_sheet = spreadsheet.add_worksheet("Ideas", 1000, 4)

            self._ensure_headers_exist()
            logger.info("Connected to Google Sheets successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets: {e}")
            raise

    def _ensure_headers_exist(self):
        """Add headers to both sheets if they are empty."""
        # Setup Queue Sheet (YouTube)
        if not self.sheet.get_all_values():
            headers = [
                "Timestamp",
                "Filename",
                "Drive Link",
                "Title",
                "Description",
                "Tags",
                "Status",
                "YouTube Link",
                "Scheduled Date",
                "Channel",
            ]
            self.sheet.append_row(headers)
            
        # Setup Queue Sheet (Facebook)
        if not self.fb_sheet.get_all_values():
            headers = [
                "Timestamp",
                "Filename",
                "Drive Link",
                "Title",
                "Description",
                "Tags",
                "Status",
                "Facebook Link",
                "Scheduled Date",
                "Page",
            ]
            self.fb_sheet.append_row(headers)
            
        # Setup Ideas Sheet
        if not self.ideas_sheet.get_all_values():
            headers = [
                "Timestamp",
                "Prompt",
                "Generated Idea",
                "Status/Notes"
            ]
            self.ideas_sheet.append_row(headers)

    def add_video(
        self, filename: str, drive_link: str, channel: str = "", status: str = "pending", platform: str = "youtube"
    ) -> int:
        """
        Add a new video entry to the sheet.

        Returns:
            Row number of the new entry.
        """
        if not channel:
            channel = config.DEFAULT_CHANNEL
            
        target_sheet = self.fb_sheet if platform == "facebook" else self.sheet
            
        # Determine the Scheduled Date based on the queue summary
        summary = self.get_queue_summary(platform=platform)
        now_dt = datetime.now(WIB)
        
        # If today's slot is filled up, schedule for the next available day
        # For simplicity, we fallback to tomorrow if remaining <= 0
        if summary["remaining_today"] > 0:
            scheduled_date = now_dt.strftime("%Y-%m-%d")
            # Status can remain 'pending' to allow manual edits. We only label it
            # 'scheduled' if it's explicitly pushed to tomorrow by the scheduler.
            # But the date itself acts as the queue bucket.
        else:
            scheduled_date = (now_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        row = [now_str, filename, drive_link, "", "", "", status, "", scheduled_date, channel]
        
        result = target_sheet.append_row(row, value_input_option="USER_ENTERED")
        
        try:
            # Extract exact row from 'updates.updatedRange' (e.g., "'Queue'!A11:J11")
            updated_range = result.get("updates", {}).get("updatedRange", "")
            import re
            match = re.search(r'[A-Z]+(\d+)', updated_range)
            if match:
                row_num = int(match.group(1))
            else:
                row_num = len(target_sheet.get_all_values())
        except Exception:
            row_num = len(target_sheet.get_all_values())

        logger.info(f"Added video '{filename}' to {platform} at row {row_num} (channel: {channel})")
        return row_num

    def update_metadata(
        self, row: int, title: str, description: str, tags: str, platform: str = "youtube"
    ):
        """Update the Groq-generated metadata for a video row."""
        col = config.SHEET_COLUMNS
        target_sheet = self.fb_sheet if platform == "facebook" else self.sheet
        target_sheet.update_cell(row, col["title"], title)
        target_sheet.update_cell(row, col["description"], description)
        target_sheet.update_cell(row, col["tags"], tags)
        logger.info(f"Metadata updated for row {row} on {platform}: '{title}'")

    def update_status(self, row: int, status: str, platform: str = "youtube"):
        """Update the status of a video entry."""
        col = config.SHEET_COLUMNS
        target_sheet = self.fb_sheet if platform == "facebook" else self.sheet
        target_sheet.update_cell(row, col["status"], status)
        logger.info(f"Row {row} on {platform} status → '{status}'")

    def set_youtube_link(self, row: int, youtube_link: str, platform: str = "youtube"):
        """Set the video link after successful upload (YouTube or Facebook)."""
        col = config.SHEET_COLUMNS
        target_sheet = self.fb_sheet if platform == "facebook" else self.sheet
        target_sheet.update_cell(row, col["youtube_link"], youtube_link)
        self.update_status(row, "uploaded")
        logger.info(f"Row {row} YouTube link → {youtube_link}")

    def set_scheduled_date(self, row: int, date_str: str, platform: str = "youtube"):
        """Set the scheduled upload date."""
        col = config.SHEET_COLUMNS
        target_sheet = self.fb_sheet if platform == "facebook" else self.sheet
        target_sheet.update_cell(row, col["scheduled_date"], date_str)
        self.update_status(row, "scheduled", platform=platform)

    def get_pending_videos(self, platform: str = "youtube") -> list[dict]:
        """
        Get all videos with status 'pending', ordered by timestamp (FIFO).

        Returns:
            List of dicts with row number and video data.
        """
        target_sheet = self.fb_sheet if platform == "facebook" else self.sheet
        all_rows = target_sheet.get_all_values()
        pending = []

        for i, row in enumerate(all_rows[1:], start=2):  # skip header
            if len(row) >= 7 and row[6].strip().lower() == "pending":
                pending.append({
                    "row": i,
                    "timestamp": row[0],
                    "filename": row[1],
                    "drive_link": row[2],
                    "title": row[3],
                    "description": row[4],
                    "tags": row[5],
                    "status": row[6],
                    "youtube_link": row[7] if len(row) > 7 else "",
                    "scheduled_date": row[8] if len(row) > 8 else "",
                    "channel": row[9] if len(row) > 9 else config.DEFAULT_CHANNEL,
                })

        return pending

    def get_scheduled_videos(self, date_str: str = None, platform: str = "youtube") -> list[dict]:
        """
        Get all videos scheduled for a specific date.
        If no date given, use today (WIB).
        """
        if date_str is None:
            date_str = datetime.now(WIB).strftime("%Y-%m-%d")

        target_sheet = self.fb_sheet if platform == "facebook" else self.sheet
        all_rows = target_sheet.get_all_values()
        scheduled = []

        for i, row in enumerate(all_rows[1:], start=2):
            if len(row) >= 9 and row[6].strip().lower() == "scheduled":
                if date_str == "all" or row[8].strip() == date_str:
                    scheduled.append({
                        "row": i,
                        "timestamp": row[0],
                        "filename": row[1],
                        "drive_link": row[2],
                        "title": row[3],
                        "description": row[4],
                        "tags": row[5],
                        "status": row[6],
                        "youtube_link": row[7] if len(row) > 7 else "",
                        "scheduled_date": row[8],
                        "channel": row[9] if len(row) > 9 else config.DEFAULT_CHANNEL,
                    })
        return scheduled

    def count_uploads_today(self, platform: str = "youtube") -> int:
        """Count how many videos have been uploaded today (WIB)."""
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        target_sheet = self.fb_sheet if platform == "facebook" else self.sheet
        all_rows = target_sheet.get_all_values()
        count = 0

        for row in all_rows[1:]:
            if len(row) >= 7 and row[6].strip().lower() == "uploaded":
                if row[0].startswith(today):
                    count += 1

        return count

    def get_queue_summary(self, platform: str = "youtube") -> dict:
        """Get a summary of the current queue."""
        target_sheet = self.fb_sheet if platform == "facebook" else self. sheet
        all_rows = target_sheet.get_all_values()
        summary = {
            "total": len(all_rows) - 1,
            "pending": 0,
            "scheduled": 0,
            "uploaded": 0,
            "failed": 0,
        }

        for row in all_rows[1:]:
            if len(row) >= 7:
                status = row[6].strip().lower()
                if status in summary:
                    summary[status] += 1

        summary["uploads_today"] = self.count_uploads_today(platform=platform)
        
        max_uploads = config.MAX_UPLOADS_PER_DAY_FACEBOOK if platform == "facebook" else config.MAX_UPLOADS_PER_DAY_YOUTUBE
        
        summary["remaining_today"] = max(
            0, max_uploads - summary["uploads_today"]
        )

        return summary
