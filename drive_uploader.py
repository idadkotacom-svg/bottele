"""
Google Drive upload module — uploads video files to a specified Drive folder.
Uses OAuth2 for authentication (same flow as YouTube).
"""
import io
import logging
import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

import config

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_FILE = str(config.CREDENTIALS_DIR / "drive_token.json")


class DriveUploader:
    """Handles uploading files to Google Drive via OAuth2."""

    def __init__(self):
        self.creds = self._authenticate()
        self.service = build("drive", "v3", credentials=self.creds)
        self.folder_id = config.GOOGLE_DRIVE_FOLDER_ID

    def _authenticate(self) -> Credentials:
        """Authenticate with Google Drive using OAuth2."""
        creds = None

        # Load existing token
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

        # Refresh or get new token
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing Drive token...")
                creds.refresh(Request())
            else:
                logger.info("Starting Drive OAuth2 flow...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    config.YOUTUBE_CLIENT_SECRETS_FILE, SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save token for future use
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
            logger.info("Drive token saved.")

        return creds

    def upload(self, file_path: str, mime_type: str = "video/mp4") -> dict:
        """
        Upload a file to Google Drive.

        Args:
            file_path: Local path to the file to upload.
            mime_type: MIME type of the file.

        Returns:
            dict with keys: file_id, web_view_link, file_name
        """
        file_path = Path(file_path)
        file_name = file_path.name

        file_metadata = {
            "name": file_name,
            "parents": [self.folder_id],
        }

        media = MediaFileUpload(
            str(file_path),
            mimetype=mime_type,
            resumable=True,
            chunksize=10 * 1024 * 1024,  # 10 MB chunks
        )

        logger.info(f"Uploading '{file_name}' to Google Drive...")

        request = self.service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"Upload progress: {progress}%")

        file_id = response.get("id")
        web_view_link = response.get("webViewLink", "")

        logger.info(f"Upload complete: {file_name} → {web_view_link}")

        return {
            "file_id": file_id,
            "web_view_link": web_view_link,
            "file_name": file_name,
        }

    def download(self, file_id: str, destination: str) -> str:
        """
        Download a file from Google Drive to local path.

        Args:
            file_id: Google Drive file ID.
            destination: Local path to save the file.

        Returns:
            Local file path.
        """
        request = self.service.files().get_media(fileId=file_id)
        fh = io.FileIO(destination, "wb")
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"Download progress: {progress}%")

        fh.close()
        logger.info(f"Downloaded to: {destination}")
        return destination
