"""
YouTube upload module — uploads videos to YouTube via Data API v3.
Supports multiple channels with separate OAuth2 tokens.
"""
import logging
import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import config

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubeUploader:
    """Handles uploading videos to YouTube via the Data API v3."""

    def __init__(self, channel_name: str = None):
        """
        Initialize uploader for a specific channel.

        Args:
            channel_name: Channel name (must match one in YOUTUBE_CHANNELS).
                          If None, uses the default channel.
        """
        self.channel_name = channel_name or config.DEFAULT_CHANNEL
        self.token_file = config.get_channel_token_file(self.channel_name)
        self.creds = self._authenticate()
        self.service = build("youtube", "v3", credentials=self.creds)

    def _authenticate(self) -> Credentials:
        """Authenticate with YouTube using OAuth2."""
        creds = None

        # Load existing token
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)

        # Refresh or get new token
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info(f"Refreshing YouTube token for '{self.channel_name}'...")
                creds.refresh(Request())
            else:
                logger.info(
                    f"Starting YouTube OAuth2 flow for '{self.channel_name}'...\n"
                )
                flow = InstalledAppFlow.from_client_secrets_file(
                    config.YOUTUBE_CLIENT_SECRETS_FILE, SCOPES
                )
                
                # Check if running on Render (headless server)
                if os.environ.get("RENDER"):
                    logger.info("Detected Render environment — using OOB (Console) OAuth flow.")
                    # Use run_console to print the URL and ask for the authorization code
                    creds = flow.run_console()
                else:
                    logger.info("Detected Local environment — using Local Server OAuth flow.")
                    creds = flow.run_local_server(port=0, open_browser=False)

            # Save token for future use
            with open(self.token_file, "w") as f:
                f.write(creds.to_json())
            logger.info(f"YouTube token saved for '{self.channel_name}'.")

        return creds

    def upload(
        self,
        file_path: str,
        title: str,
        description: str = "",
        tags: str = "",
        category: str = None,
        privacy: str = None,
    ) -> dict:
        """
        Upload a video to YouTube.

        Returns:
            dict with keys: video_id, youtube_link
        """
        if category is None:
            category = config.YOUTUBE_CATEGORY
        if privacy is None:
            privacy = config.YOUTUBE_PRIVACY

        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tag_list,
                "categoryId": category,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            file_path,
            mimetype="video/mp4",
            resumable=True,
            chunksize=10 * 1024 * 1024,
        )

        logger.info(f"Uploading to YouTube ({self.channel_name}): '{title}'...")

        request = self.service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"YouTube upload progress: {progress}%")

        video_id = response["id"]
        youtube_link = f"https://youtu.be/{video_id}"

        logger.info(f"Upload complete ({self.channel_name}): {youtube_link}")

        return {
            "video_id": video_id,
            "youtube_link": youtube_link,
        }
