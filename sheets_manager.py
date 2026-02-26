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

            # Get or create Main queue sheet
            try:
                self.sheet = spreadsheet.worksheet("Queue")
            except gspread.exceptions.WorksheetNotFound:
                logger.info("Sheet 'Queue' not found, creating it...")
                self.sheet = spreadsheet.add_worksheet("Queue", 1000, 10)
                
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
        # Setup Queue Sheet
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
        self, filename: str, drive_link: str, channel: str = "", status: str = "pending"
    ) -> int:
        """
        Add a new video entry to the sheet.

        Returns:
            Row number of the new entry.
        """
        if not channel:
            channel = config.DEFAULT_CHANNEL
        now = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")
        row = [now, filename, drive_link, "", "", "", status, "", "", channel]
        self.sheet.append_row(row, value_input_option="USER_ENTERED")
        row_num = len(self.sheet.get_all_values())
        logger.info(f"Added video '{filename}' at row {row_num} (channel: {channel})")
        return row_num

    def update_metadata(
        self, row: int, title: str, description: str, tags: str
    ):
        """Update the Groq-generated metadata for a video row."""
        col = config.SHEET_COLUMNS
        self.sheet.update_cell(row, col["title"], title)
        self.sheet.update_cell(row, col["description"], description)
        self.sheet.update_cell(row, col["tags"], tags)
        logger.info(f"Metadata updated for row {row}: '{title}'")

    def update_status(self, row: int, status: str):
        """Update the status of a video entry."""
        col = config.SHEET_COLUMNS
        self.sheet.update_cell(row, col["status"], status)
        logger.info(f"Row {row} status → '{status}'")

    def set_youtube_link(self, row: int, youtube_link: str):
        """Set the YouTube link after successful upload."""
        col = config.SHEET_COLUMNS
        self.sheet.update_cell(row, col["youtube_link"], youtube_link)
        self.update_status(row, "uploaded")
        logger.info(f"Row {row} YouTube link → {youtube_link}")

    def set_scheduled_date(self, row: int, date_str: str):
        """Set the scheduled upload date."""
        col = config.SHEET_COLUMNS
        self.sheet.update_cell(row, col["scheduled_date"], date_str)
        self.update_status(row, "scheduled")

    def get_pending_videos(self) -> list[dict]:
        """
        Get all videos with status 'pending', ordered by timestamp (FIFO).

        Returns:
            List of dicts with row number and video data.
        """
        all_rows = self.sheet.get_all_values()
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

    def get_scheduled_videos(self, date_str: str = None) -> list[dict]:
        """
        Get all videos scheduled for a specific date.
        If no date given, use today (WIB).
        """
        if date_str is None:
            date_str = datetime.now(WIB).strftime("%Y-%m-%d")

        all_rows = self.sheet.get_all_values()
        scheduled = []

        for i, row in enumerate(all_rows[1:], start=2):
            if len(row) >= 9 and row[6].strip().lower() == "scheduled":
                if row[8].strip() == date_str:
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

    def count_uploads_today(self) -> int:
        """Count how many videos have been uploaded today (WIB)."""
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        all_rows = self.sheet.get_all_values()
        count = 0

        for row in all_rows[1:]:
            if len(row) >= 7 and row[6].strip().lower() == "uploaded":
                if row[0].startswith(today):
                    count += 1

        return count

    def get_queue_summary(self) -> dict:
        """Get a summary of the current queue."""
        all_rows = self.sheet.get_all_values()
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

        summary["uploads_today"] = self.count_uploads_today()
        summary["remaining_today"] = max(
            0, config.MAX_UPLOADS_PER_DAY - summary["uploads_today"]
        )

        return summary
