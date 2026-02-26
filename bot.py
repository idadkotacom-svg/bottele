"""
Telegram Bot — main entry point for the video upload pipeline.

Commands:
    /start   — Welcome message & help
    /status  — View upload queue status
    /queue   — View today's scheduled uploads
    /upload  — Manually trigger queue processing

Send a video or file to the bot to add it to the pipeline.
"""
import asyncio
import logging
import os
import re
from pathlib import Path

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config

# Logging setup
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Lazy-initialized modules (only created when first needed)
_drive = None
_sheets = None
_sched = None


def get_drive():
    """Lazy init Google Drive uploader."""
    global _drive
    if _drive is None:
        from drive_uploader import DriveUploader
        _drive = DriveUploader()
    return _drive


def get_sheets():
    """Lazy init Google Sheets manager."""
    global _sheets
    if _sheets is None:
        from sheets_manager import SheetsManager
        _sheets = SheetsManager()
    return _sheets


def get_scheduler():
    """Lazy init Scheduler."""
    global _sched
    if _sched is None:
        from scheduler import Scheduler
        _sched = Scheduler()
    return _sched


def _google_not_configured() -> str | None:
    """Check if Google credentials are configured. Returns error message or None."""
    import json
    sa_path = config.GOOGLE_SERVICE_ACCOUNT_FILE
    try:
        with open(sa_path, "r") as f:
            data = json.load(f)
            if not data.get("client_email"):
                raise ValueError("Missing client_email")
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return (
            "⚠️ **Google belum di-setup!**\n\n"
            "Untuk menggunakan fitur ini, kamu perlu:\n"
            "1. Buat Service Account di Google Cloud Console\n"
            "2. Download JSON key\n"
            "3. Simpan ke `credentials/service_account.json`\n"
            "4. Isi `GOOGLE_SHEET_ID` dan `GOOGLE_DRIVE_FOLDER_ID` di `.env`\n\n"
            "📖 Lihat README.md untuk panduan lengkap."
        )
    if not config.GOOGLE_SHEET_ID:
        return "⚠️ `GOOGLE_SHEET_ID` belum diisi di file `.env`"
    if not config.GOOGLE_DRIVE_FOLDER_ID:
        return "⚠️ `GOOGLE_DRIVE_FOLDER_ID` belum diisi di file `.env`"
    return None


# Per-user active channel
_user_channels: dict[int, str] = {}


def _get_active_channel(user_id: int) -> str:
    """Get the active channel for a user."""
    return _user_channels.get(user_id, config.DEFAULT_CHANNEL)


# ─── Command Handlers ──────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    msg = (
        "🎬 **Video Upload Pipeline Bot**\n\n"
        "Kirim video ke saya dan saya akan:\n"
        "1. 📁 Upload ke Google Drive\n"
        "2. 🧠 Generate judul, deskripsi & tags via Groq AI\n"
        "3. 📺 Upload ke YouTube (max 3/hari)\n\n"
        "⏰ **Jadwal Upload (Viral Hours):**\n"
        "• 21:00 WIB → 🇬🇧🇪🇺 Europe sore\n"
        "• 00:00 WIB → 🇺🇸 USA East siang\n"
        "• 03:00 WIB → 🇺🇸 USA West siang\n\n"
        "**Commands:**\n"
        "/status — Lihat status antrian & jadwal\n"
        "/queue — Lihat video dalam antrian\n"
        "/upload — Force upload sekarang\n"
        "/help — Tampilkan pesan ini\n\n"
        "💡 Kirim video kapan saja, bot akan upload di jam viral!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command — show queue summary."""
    err = _google_not_configured()
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    try:
        msg = get_scheduler().get_status_message()
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /channel command — switch active YouTube channel."""
    user_id = update.effective_user.id
    args = context.args

    if not args:
        # Show current channel and list
        active = _get_active_channel(user_id)
        channels_list = "\n".join(
            f"  {'\u2705' if c == active else '\u25cb'} `{c}`"
            for c in config.YOUTUBE_CHANNELS
        )
        await update.message.reply_text(
            f"📺 **Active channel:** `{active}`\n\n"
            f"**Channels tersedia:**\n{channels_list}\n\n"
            f"Gunakan: `/channel nama_channel`",
            parse_mode="Markdown",
        )
        return

    target = " ".join(args).strip()

    # Match by name (case-insensitive)
    matched = None
    for ch in config.YOUTUBE_CHANNELS:
        if ch.lower() == target.lower():
            matched = ch
            break

    # Match by index (1-based)
    if matched is None:
        try:
            idx = int(target) - 1
            if 0 <= idx < len(config.YOUTUBE_CHANNELS):
                matched = config.YOUTUBE_CHANNELS[idx]
        except ValueError:
            pass

    if matched is None:
        channels_list = ", ".join(f"`{c}`" for c in config.YOUTUBE_CHANNELS)
        await update.message.reply_text(
            f"❌ Channel `{target}` tidak ditemukan.\n"
            f"Channels tersedia: {channels_list}",
            parse_mode="Markdown",
        )
        return

    _user_channels[user_id] = matched
    await update.message.reply_text(
        f"✅ Channel switched ke **{matched}**\n"
        f"Video berikutnya akan di-upload ke channel ini.",
        parse_mode="Markdown",
    )


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /queue command — show today's scheduled uploads."""
    err = _google_not_configured()
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    try:
        sheets = get_sheets()
        scheduled = sheets.get_scheduled_videos()
        pending = sheets.get_pending_videos()

        videos = scheduled + pending

        if not videos:
            await update.message.reply_text("📭 Tidak ada video dalam antrian.")
            return

        msg = "📋 **Antrian Upload:**\n\n"
        for i, v in enumerate(videos[:20], 1):
            status_icon = {
                "pending": "⏳",
                "scheduled": "📅",
                "uploading": "📤",
                "uploaded": "✅",
                "failed": "❌",
            }.get(v["status"], "❓")

            title = v.get("title") or v["filename"]
            ch = v.get("channel", config.DEFAULT_CHANNEL)
            msg += f"{i}. {status_icon} `{title}` \u2192 {ch}\n"

        msg += f"\n📊 Total: {len(videos)} video"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /upload command — manually trigger queue processing."""
    err = _google_not_configured()
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    await update.message.reply_text("🔄 Force upload — mengabaikan jadwal...")

    try:
        results = get_scheduler().force_upload()

        if not results:
            summary = get_sheets().get_queue_summary()
            if summary["remaining_today"] <= 0:
                await update.message.reply_text(
                    "⚠️ Limit upload harian tercapai (6/hari).\n"
                    "Video pending sudah dijadwalkan untuk besok."
                )
            else:
                await update.message.reply_text(
                    "📭 Tidak ada video pending dalam antrian."
                )
            return

        for r in results:
            if r["success"]:
                await update.message.reply_text(
                    f"✅ **Uploaded!**\n"
                    f"📹 `{r['filename']}`\n"
                    f"🔗 {r['youtube_link']}",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    f"❌ **Failed:** `{r['filename']}`\n"
                    f"Error: {r.get('error', 'Unknown')}",
                    parse_mode="Markdown",
                )

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ─── Video/File Handler ────────────────────────────────────────────


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming video or document (video file)."""
    message = update.message

    # Determine if it's a video or a document
    if message.video:
        file = message.video
        file_name = message.video.file_name or f"video_{file.file_unique_id}.mp4"
        file_size = file.file_size
    elif message.document:
        file = message.document
        file_name = file.file_name or f"file_{file.file_unique_id}"
        file_size = file.file_size

        # Check if it's a video file
        mime = file.mime_type or ""
        if not mime.startswith("video/"):
            await message.reply_text("⚠️ Kirim file video saja (MP4, MKV, etc.)")
            return
    else:
        return

    # File size info
    size_mb = (file_size or 0) / (1024 * 1024)

    await message.reply_text(
        f"📥 **Menerima video:**\n"
        f"📄 `{file_name}`\n"
        f"📏 {size_mb:.1f} MB\n\n"
        f"⏳ Mengunduh dari Telegram...",
        parse_mode="Markdown",
    )

    # Check Google config before proceeding
    err = _google_not_configured()
    if err:
        await message.reply_text(err, parse_mode="Markdown")
        return

    try:
        # Step 1: Download from Telegram
        local_path = str(config.TEMP_DIR / file_name)

        tg_file = await context.bot.get_file(file.file_id)
        await tg_file.download_to_drive(local_path)

        logger.info(f"Downloaded from Telegram: {local_path}")
        await message.reply_text("✅ Download selesai! Mengupload ke Drive...")

        # Step 2: Upload to Google Drive
        drive_result = get_drive().upload(local_path)
        await message.reply_text(
            f"✅ Uploaded ke Drive!\n"
            f"🔗 {drive_result['web_view_link']}\n\n"
            f"🧠 Generating metadata via Groq AI...",
        )

        # Step 3: Add to Google Sheets
        active_ch = _get_active_channel(update.effective_user.id)
        sheets = get_sheets()
        row = sheets.add_video(
            filename=file_name,
            drive_link=drive_result["web_view_link"],
            channel=active_ch,
        )

        # Step 4: Generate metadata via Groq
        from groq_metadata import generate_metadata
        caption = message.caption or ""
        metadata = generate_metadata(file_name, extra_context=caption)
        sheets.update_metadata(
            row,
            metadata["title"],
            metadata["description"],
            metadata["tags"],
        )

        # Step 5: Clean up temp file
        if os.path.exists(local_path):
            os.remove(local_path)

        # Step 6: Check if we can upload now or need to schedule
        summary = sheets.get_queue_summary()
        if summary["remaining_today"] > 0:
            status_msg = (
                f"📺 Video siap upload ke YouTube!\n"
                f"Ketik /upload untuk upload sekarang.\n"
                f"📊 Sisa slot hari ini: {summary['remaining_today']}"
            )
        else:
            from datetime import datetime, timezone, timedelta
            tomorrow = (
                datetime.now(timezone(timedelta(hours=7)))
                .strftime("%Y-%m-%d")
            )
            sheets.set_scheduled_date(row, tomorrow)
            status_msg = (
                f"📅 Limit harian tercapai!\n"
                f"Video dijadwalkan untuk: {tomorrow}"
            )

        await message.reply_text(
            f"✅ **Pipeline selesai!**\n\n"
            f"📄 File: `{file_name}`\n"
            f"📝 Title: {metadata['title']}\n"
            f"🏷️ Tags: {metadata['tags']}\n\n"
            f"{status_msg}\n\n"
            f"💡 Kamu bisa edit metadata di Google Sheets sebelum upload.",
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(f"Error processing video: {e}", exc_info=True)
        await message.reply_text(f"❌ Error: {e}")

        # Clean up on error
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
        except PermissionError:
            logger.warning(f"Could not remove temp file (in use): {local_path}")


# ─── URL/Link Handler ──────────────────────────────────────────────────────

# Supported URL patterns
URL_PATTERN = re.compile(
    r'(https?://(?:www\.)?'
    r'(?:youtube\.com/(?:watch|shorts)|youtu\.be/|'
    r'tiktok\.com/|vm\.tiktok\.com/|'
    r'instagram\.com/(?:reel|p)/|'
    r'twitter\.com/.+/status/|x\.com/.+/status/|'
    r'facebook\.com/.+/videos/|'
    r'douyin\.com/|v\.douyin\.com/|'
    r'xiaohongshu\.com/|xhslink\.com/|'
    r'bilibili\.com/|b23\.tv/|'
    r'kuaishou\.com/|v\.kuaishou\.com/|'
    r'threads\.net/)'
    r'[^\s]+)',
    re.IGNORECASE
)


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming URL — download video via yt-dlp then pipeline."""
    message = update.message
    text = message.text or ""

    # Extract URL from message
    match = URL_PATTERN.search(text)
    if not match:
        return  # Not a supported video URL

    url = match.group(0)

    # Check Google config
    err = _google_not_configured()
    if err:
        await message.reply_text(err, parse_mode="Markdown")
        return

    await message.reply_text(
        f"🔗 **Link detected!**\n"
        f"`{url}`\n\n"
        f"⏳ Downloading video via yt-dlp...",
        parse_mode="Markdown",
    )

    local_path = None
    try:
        import yt_dlp

        # yt-dlp options: best quality, mp4 format
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': str(config.TEMP_DIR / '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'max_filesize': 500 * 1024 * 1024,  # 500 MB max
        }

        # Download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            local_path = ydl.prepare_filename(info)
            video_title = info.get('title', 'video')
            duration = info.get('duration', 0)

        if not os.path.exists(local_path):
            await message.reply_text("❌ Download gagal — file tidak ditemukan.")
            return

        file_name = os.path.basename(local_path)
        size_mb = os.path.getsize(local_path) / (1024 * 1024)

        duration_str = ""
        if duration:
            mins, secs = divmod(int(duration), 60)
            duration_str = f"\n⏱️ Duration: {mins}:{secs:02d}"

        await message.reply_text(
            f"✅ **Download selesai!**\n"
            f"🎬 `{video_title}`\n"
            f"📏 {size_mb:.1f} MB{duration_str}\n\n"
            f"📁 Uploading ke Google Drive...",
            parse_mode="Markdown",
        )

        # Continue pipeline: Drive → Sheets → Groq
        drive_result = get_drive().upload(local_path)
        await message.reply_text(
            f"✅ Uploaded ke Drive!\n"
            f"🔗 {drive_result['web_view_link']}\n\n"
            f"🧠 Generating metadata via Groq AI...",
        )

        active_ch = _get_active_channel(update.effective_user.id)
        sheets = get_sheets()
        row = sheets.add_video(
            filename=file_name,
            drive_link=drive_result["web_view_link"],
            channel=active_ch,
        )

        # Use original video title as context for Groq
        from groq_metadata import generate_metadata
        extra = message.caption or ""
        metadata = generate_metadata(
            file_name, extra_context=f"Original title: {video_title}. {extra}"
        )
        sheets.update_metadata(
            row,
            metadata["title"],
            metadata["description"],
            metadata["tags"],
        )

        # Clean up temp
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
        except PermissionError:
            pass

        # Status
        summary = sheets.get_queue_summary()
        sched = get_scheduler()
        next_time = sched.get_next_upload_time()

        if summary["remaining_today"] > 0:
            status_msg = (
                f"📺 Video dijadwalkan upload di `{next_time}`\n"
                f"Atau ketik /upload untuk force upload sekarang.\n"
                f"📊 Sisa slot hari ini: {summary['remaining_today']}"
            )
        else:
            status_msg = (
                f"📅 Limit harian tercapai!\n"
                f"Video dijadwalkan untuk besok."
            )

        await message.reply_text(
            f"✅ **Pipeline selesai!**\n\n"
            f"📄 File: `{file_name}`\n"
            f"📝 Title: {metadata['title']}\n"
            f"🏷️ Tags: {metadata['tags']}\n\n"
            f"{status_msg}\n\n"
            f"💡 Kamu bisa edit metadata di Google Sheets sebelum upload.",
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(f"Error processing URL: {e}", exc_info=True)
        await message.reply_text(f"❌ Error: {e}")
        try:
            if local_path and os.path.exists(local_path):
                os.remove(local_path)
        except (PermissionError, Exception):
            pass

# ─── Scheduled Upload Job ──────────────────────────────────────────


async def scheduled_upload_job(context: ContextTypes.DEFAULT_TYPE):
    """Background job that runs periodically to process the queue."""
    logger.info("Running scheduled upload job...")

    try:
        results = get_scheduler().process_queue()

        chat_id = config.TELEGRAM_CHAT_ID
        if not chat_id:
            logger.warning("TELEGRAM_CHAT_ID not set, skipping notifications.")
            return

        for r in results:
            if r["success"]:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"✅ **Auto-uploaded!**\n"
                        f"📹 `{r['filename']}`\n"
                        f"🔗 {r['youtube_link']}"
                    ),
                    parse_mode="Markdown",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"❌ **Auto-upload failed:** `{r['filename']}`\n"
                        f"Error: {r.get('error', 'Unknown')}"
                    ),
                    parse_mode="Markdown",
                )

    except Exception as e:
        logger.error(f"Scheduled job error: {e}", exc_info=True)


# ─── Save Chat ID ──────────────────────────────────────────────────


async def save_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Middleware to save the chat ID for scheduled notifications."""
    chat_id = str(update.effective_chat.id)

    if config.TELEGRAM_CHAT_ID != chat_id:
        config.TELEGRAM_CHAT_ID = chat_id

        # Also save to .env for persistence
        env_path = config.BASE_DIR / ".env"
        if env_path.exists():
            content = env_path.read_text()
            if "TELEGRAM_CHAT_ID=" in content:
                lines = content.split("\n")
                lines = [
                    f"TELEGRAM_CHAT_ID={chat_id}" if l.startswith("TELEGRAM_CHAT_ID=") else l
                    for l in lines
                ]
                env_path.write_text("\n".join(lines))
            else:
                with open(env_path, "a") as f:
                    f.write(f"\nTELEGRAM_CHAT_ID={chat_id}\n")


# ─── Main ──────────────────────────────────────────────────────────


def main():
    """Start the Telegram bot."""
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set! Check your .env file.")
        return

    logger.info("Starting Video Upload Pipeline Bot...")

    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(CommandHandler("channel", cmd_channel))

    # Video / file handler
    app.add_handler(
        MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video)
    )

    # URL handler (YouTube, TikTok, Instagram, Twitter/X)
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(URL_PATTERN) & ~filters.COMMAND,
            handle_url,
        )
    )

    # Chat ID saver (runs on every message)
    app.add_handler(
        MessageHandler(filters.ALL, save_chat_id),
        group=1,
    )

    # Scheduled upload job
    if config.SCHEDULER_INTERVAL_MINUTES > 0:
        job_queue = app.job_queue
        job_queue.run_repeating(
            scheduled_upload_job,
            interval=config.SCHEDULER_INTERVAL_MINUTES * 60,
            first=60,  # First run after 1 minute
        )
        logger.info(
            f"Scheduler enabled: every {config.SCHEDULER_INTERVAL_MINUTES} minutes"
        )

    logger.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
