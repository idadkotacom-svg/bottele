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
from drive_uploader import DriveUploader
from sheets_manager import SheetsManager
from groq_metadata import generate_metadata
from scheduler import Scheduler

# Logging setup
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Initialize modules
drive = DriveUploader()
sheets = SheetsManager()
sched = Scheduler()


# ─── Command Handlers ──────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    msg = (
        "🎬 **Video Upload Pipeline Bot**\n\n"
        "Kirim video ke saya dan saya akan:\n"
        "1. 📁 Upload ke Google Drive\n"
        "2. 🧠 Generate judul, deskripsi & tags via Groq AI\n"
        "3. 📺 Upload ke YouTube (max 6/hari)\n\n"
        "**Commands:**\n"
        "/status — Lihat status antrian\n"
        "/queue — Lihat jadwal upload hari ini\n"
        "/upload — Trigger upload manual\n"
        "/help — Tampilkan pesan ini\n\n"
        "💡 Kirim video atau file video langsung ke chat ini!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command — show queue summary."""
    try:
        msg = sched.get_status_message()
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /queue command — show today's scheduled uploads."""
    try:
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
            msg += f"{i}. {status_icon} `{title}`\n"

        msg += f"\n📊 Total: {len(videos)} video"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /upload command — manually trigger queue processing."""
    await update.message.reply_text("🔄 Memproses antrian upload...")

    try:
        results = sched.process_queue()

        if not results:
            summary = sheets.get_queue_summary()
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

    try:
        # Step 1: Download from Telegram
        local_path = str(config.TEMP_DIR / file_name)

        tg_file = await context.bot.get_file(file.file_id)
        await tg_file.download_to_drive(local_path)

        logger.info(f"Downloaded from Telegram: {local_path}")
        await message.reply_text("✅ Download selesai! Mengupload ke Drive...")

        # Step 2: Upload to Google Drive
        drive_result = drive.upload(local_path)
        await message.reply_text(
            f"✅ Uploaded ke Drive!\n"
            f"🔗 {drive_result['web_view_link']}\n\n"
            f"🧠 Generating metadata via Groq AI...",
        )

        # Step 3: Add to Google Sheets
        row = sheets.add_video(
            filename=file_name,
            drive_link=drive_result["web_view_link"],
        )

        # Step 4: Generate metadata via Groq
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
            tomorrow = (
                __import__("datetime")
                .datetime.now(
                    __import__("datetime").timezone(
                        __import__("datetime").timedelta(hours=7)
                    )
                )
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
        if os.path.exists(local_path):
            os.remove(local_path)


# ─── Scheduled Upload Job ──────────────────────────────────────────


async def scheduled_upload_job(context: ContextTypes.DEFAULT_TYPE):
    """Background job that runs periodically to process the queue."""
    logger.info("Running scheduled upload job...")

    try:
        results = sched.process_queue()

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

    # Video / file handler
    app.add_handler(
        MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video)
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
