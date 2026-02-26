"""
Configuration module — loads environment variables and defines constants.
"""
import os
from pathlib import Path
from datetime import timezone, timedelta
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Timezone WIB (UTC+7)
WIB = timezone(timedelta(hours=7))

# === Paths ===
BASE_DIR = Path(__file__).parent
TEMP_DIR = BASE_DIR / "temp"
CREDENTIALS_DIR = BASE_DIR / "credentials"
TEMP_DIR.mkdir(exist_ok=True)

# === Telegram ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# === Groq ===
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"

# === Google Service Account ===
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    str(CREDENTIALS_DIR / "service_account.json"),
)

# === Google Drive ===
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")

# === Google Sheets ===
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")

# Sheet column mapping (1-indexed)
SHEET_COLUMNS = {
    "timestamp": 1,
    "filename": 2,
    "drive_link": 3,
    "title": 4,
    "description": 5,
    "tags": 6,
    "status": 7,
    "youtube_link": 8,
    "scheduled_date": 9,
    "channel": 10,
}

# === YouTube ===
YOUTUBE_CLIENT_SECRETS_FILE = os.getenv(
    "YOUTUBE_CLIENT_SECRETS_FILE",
    str(CREDENTIALS_DIR / "client_secrets.json"),
)
YOUTUBE_CATEGORY = os.getenv("YOUTUBE_CATEGORY", "22")  # People & Blogs
YOUTUBE_PRIVACY = os.getenv("YOUTUBE_PRIVACY", "public")

# === YouTube Channels ===
# Format: comma-separated channel names
_channels_raw = os.getenv("YOUTUBE_CHANNELS", "default")
YOUTUBE_CHANNELS = [c.strip() for c in _channels_raw.split(",")]
DEFAULT_CHANNEL = YOUTUBE_CHANNELS[0]

def get_channel_token_file(channel_name: str) -> str:
    """Get the token file path for a specific channel."""
    safe_name = channel_name.lower().replace(" ", "_")
    return str(CREDENTIALS_DIR / f"youtube_token_{safe_name}.json")

# === Facebook ===
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN", "")
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "")

# === Scheduler ===
MAX_UPLOADS_PER_DAY_YOUTUBE = int(os.getenv("MAX_UPLOADS_PER_DAY_YOUTUBE", "6"))
MAX_UPLOADS_PER_DAY_FACEBOOK = int(os.getenv("MAX_UPLOADS_PER_DAY_FACEBOOK", "6"))
SCHEDULER_INTERVAL_MINUTES = int(os.getenv("SCHEDULER_INTERVAL_MINUTES", "5"))

# === Upload Schedule (Viral Hours WIB) ===
# Default: 21:00, 00:00, 03:00 WIB — targeting US/EU peak hours
# Format: comma-separated "HH:MM" in WIB
_schedule_raw = os.getenv("UPLOAD_SCHEDULE_HOURS", "21:00,00:00,03:00")
UPLOAD_SCHEDULE_HOURS = [s.strip() for s in _schedule_raw.split(",")]

# === Groq Prompt Template ===
METADATA_PROMPT_TEMPLATE = """You are a YouTube SEO expert. Given the video filename below, generate compelling metadata for a YouTube video.

Filename: {filename}

Respond in this EXACT JSON format (no markdown, no extra text):
{{
  "title": "Catchy, SEO-friendly title (max 100 chars)",
  "description": "Engaging description with relevant keywords (200-500 chars). Include a call to action.",
  "tags": "tag1, tag2, tag3, tag4, tag5"
}}
"""
