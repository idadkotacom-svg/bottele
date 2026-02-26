"""
Configuration module — loads environment variables and defines constants.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

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
}

# === YouTube ===
YOUTUBE_CLIENT_SECRETS_FILE = os.getenv(
    "YOUTUBE_CLIENT_SECRETS_FILE",
    str(CREDENTIALS_DIR / "client_secrets.json"),
)
YOUTUBE_TOKEN_FILE = str(CREDENTIALS_DIR / "youtube_token.json")
YOUTUBE_CATEGORY = os.getenv("YOUTUBE_CATEGORY", "22")  # People & Blogs
YOUTUBE_PRIVACY = os.getenv("YOUTUBE_PRIVACY", "public")

# === Scheduler ===
MAX_UPLOADS_PER_DAY = int(os.getenv("MAX_UPLOADS_PER_DAY", "6"))
SCHEDULER_INTERVAL_MINUTES = int(os.getenv("SCHEDULER_INTERVAL_MINUTES", "30"))

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
