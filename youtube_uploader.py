"""
YouTube upload module — uploads videos to YouTube via Data API v3.
Uses OAuth2 for authentication (required for YouTube uploads).
"""
import logging
import os
import json
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

    def __init__(self):
        self.creds = self._authenticate()
        self.service = build("youtube", "v3", credentials=self.creds)

    def _authenticate(self) -> Credentials:
        """Authenticate with YouTube using OAuth2."""
        creds = None
        token_file = config.YOUTUBE_TOKEN_FILE

        # Load existing token
        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)

        # Refresh or get new token
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing YouTube token...")
                creds.refresh(Request())
            else:
                logger.info("Starting YouTube OAuth2 flow...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    config.YOUTUBE_CLIENT_SECRETS_FILE, SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save token for future use
            with open(token_file, "w") as f:
                f.write(creds.to_json())
            logger.info("YouTube token saved.")

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

        Args:
            file_path: Local path to the video file.
            title: Video title.
            description: Video description.
            tags: Comma-separated tags string.
            category: YouTube category ID (default from config).
            privacy: Privacy status (public/private/unlisted).

        Returns:
            dict with keys: video_id, youtube_link
        """
        if category is None:
            category = config.YOUTUBE_CATEGORY
        if privacy is None:
            privacy = config.YOUTUBE_PRIVACY

        # Parse tags
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        body = {
            "snippet": {
                "title": title[:100],  # YouTube limit
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
            chunksize=10 * 1024 * 1024,  # 10 MB chunks
        )

        logger.info(f"Uploading to YouTube: '{title}'...")

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

        logger.info(f"Upload complete: {youtube_link}")

        return {
            "video_id": video_id,
            "youtube_link": youtube_link,
        }
