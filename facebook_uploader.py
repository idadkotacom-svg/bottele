"""
Facebook Uploader Module
Interacts with the Facebook Graph API to upload Reels to a specific Page.
"""
import os
import time
import requests
import logging

import config

logger = logging.getLogger(__name__)

class FacebookUploader:
    """Manages video uploads to Facebook Reels using the Graph API."""

    def __init__(self):
        self.access_token = config.FB_PAGE_ACCESS_TOKEN
        self.page_id = config.FB_PAGE_ID
        self.api_version = "v19.0"
        self.base_url = f"https://graph.facebook.com/{self.api_version}"

    def is_configured(self):
        """Check if FB credentials are set in .env."""
        return bool(self.access_token and self.page_id)

    def upload_reel(self, file_path: str, description: str) -> dict:
        """
        Uploads a video as a Reel to the Facebook Page using the 3-step process.
        Returns a dict: {"success": bool, "id": str, "url": str, "error": str}
        """
        if not self.is_configured():
            return {"success": False, "error": "FB_PAGE_ACCESS_TOKEN or FB_PAGE_ID is missing in .env"}

        try:
            # Check file sizes
            file_size = os.path.getsize(file_path)
            
            # Step 1: Initialize the upload session
            logger.info("FB Upload Step 1: Initialize session")
            init_url = f"{self.base_url}/{self.page_id}/video_reels"
            init_payload = {
                "upload_phase": "start",
                "access_token": self.access_token
            }
            init_res = requests.post(init_url, data=init_payload).json()
            
            if "error" in init_res:
                logger.error(f"FB Init Error: {init_res['error']}")
                return {"success": False, "error": init_res["error"].get("message", str(init_res["error"]))}
                
            video_id = init_res.get("video_id")
            upload_url = init_res.get("upload_url")
            
            if not video_id or not upload_url:
                return {"success": False, "error": "Failed to get video_id or upload_url from FB API"}

            # Step 2: Upload the actual video file
            logger.info(f"FB Upload Step 2: Uploading file ({file_size} bytes)")
            headers = {
                "Authorization": f"OAuth {self.access_token}",
                "offset": "0",
                "file_size": str(file_size)
            }
            with open(file_path, "rb") as f:
                upload_res = requests.post(upload_url, headers=headers, data=f).json()
                
            if "error" in upload_res:
                logger.error(f"FB File Upload Error: {upload_res['error']}")
                return {"success": False, "error": upload_res["error"].get("message", str(upload_res["error"]))}

            # Wait a few seconds for FB backend to process the chunks
            time.sleep(3)

            # Step 3: Publish the Reel
            logger.info("FB Upload Step 3: Publishing Reel")
            publish_payload = {
                "upload_phase": "finish",
                "access_token": self.access_token,
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": description
            }
            publish_res = requests.post(init_url, data=publish_payload).json()
            
            if "error" in publish_res:
                logger.error(f"FB Publish Error: {publish_res['error']}")
                return {"success": False, "error": publish_res["error"].get("message", str(publish_res["error"]))}
                
            fb_success = publish_res.get("success", False)
            if not fb_success:
                return {"success": False, "error": "Facebook API returned success=False during publish"}

            published_url = f"https://www.facebook.com/reel/{video_id}"
            logger.info(f"Successfully published FB Reel: {published_url}")
            
            return {
                "success": True,
                "id": video_id,
                "url": published_url,
                "error": None
            }

        except Exception as e:
            logger.error(f"Facebook upload exception: {e}")
            return {"success": False, "error": str(e)}
