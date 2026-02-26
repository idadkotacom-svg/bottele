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
import tempfile
import re
from datetime import datetime
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import WIB
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
    user_id = update.effective_user.id
    active_ch = _get_active_channel(user_id)
    channels_list = ", ".join(f"<code>{c}</code>" for c in config.YOUTUBE_CHANNELS)

    msg = (
        "🎬 <b>Auto YouTube Uploader Bot</b> 🚀\n\n"
        "<b>📺 INFO CHANNEL:</b>\n"
        f"• Aktif saat ini: <code>{active_ch}</code>\n"
        f"• Tersedia: {channels_list}\n"
        "<i>(Ganti tujuan pakai /channel nama_channel sebelum kirim video)</i>\n\n"
        "<b>📥 CARA UPLOAD (Pilih salah satu):</b>\n"
        "1. <b>Kirim File Video</b> langsung ke chat ini (.mp4, .mov, dll)\n"
        "2. <b>Kirim Link Sosmed!</b> Bot akan otomatis download tanpa watermark dari:\n"
        "   👉 YouTube (Shorts/Video normal)\n"
        "   👉 TikTok\n"
        "   👉 Instagram (Reels)\n"
        "   👉 X / Twitter\n\n"
        "<b>⚙️ OTOMATISASI PIPELINE:</b>\n"
        "Setelah dikirim, ini yang bot lakukan:\n"
        "1. ☁️ Backup video ke Google Drive\n"
        "2. 🧠 Groq AI membuat Judul, Deskripsi & Auto-Tags SEO\n"
        "3. 📝 Dicatat di Google Sheets (Sesuai Platform)\n"
        "4. 📅 Masuk antrian scheduler\n\n"
        "<b>⏰ JADWAL VIRAL (Max 6x/hari):</b>\n"
        "• 21:00 WIB → 🇬🇧🇪🇺 Europe sore\n"
        "• 00:00 WIB → 🇺🇸 USA East siang\n"
        "• 03:00 WIB → 🇺🇸 USA West siang\n\n"
        "<b>🛠️ COMMANDS:</b>\n"
        "/platform — Ganti target upload (YouTube / Facebook)\n"
        "/queue — Cek antrian & estimasi jam upload\n"
        "/status — Ringkasan quota harian\n"
        "/upload — Bypass jadwal & upload paksa 1 video sekarang\n"
        "/channel — Menu pindah channel (Khusus YouTube)\n"
        "/ask — Brainstorming ide dengan Groq AI & otomatis save ke Sheets\n"
    )
    await update.message.reply_text(
        msg, 
        parse_mode="HTML", 
        disable_web_page_preview=True
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command — show queue summary."""
    err = _google_not_configured()
    if err:
        await update.message.reply_text(err, parse_mode="HTML")
        return
    try:
        msg = get_scheduler().get_status_message()
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ask command to brainstorm with Groq."""
    if not context.args:
        await update.message.reply_text(
            "❓ <b>Cara Penggunaan:</b>\n"
            "<code>/ask [pertanyaan/ide]</code>\n\n"
            "Contoh:\n"
            "<code>/ask Berikan 5 ide konten YouTube Shorts tentang kucing lucu yang viral</code>",
            parse_mode="HTML"
        )
        return
        
    prompt = " ".join(context.args)
    
    wait_msg = await update.message.reply_text("🧠 <i>Groq sedang berpikir...</i>", parse_mode="HTML")
    
    try:
        from groq_metadata import ask_groq
        response = ask_groq(prompt)
        
        # Save prompt and response in context for callback
        context.user_data["last_ask_prompt"] = prompt
        context.user_data["last_ask_response"] = response
        
        keyboard = [
            [InlineKeyboardButton("💾 Simpan ke Sheet 'Ideas'", callback_data="save_idea")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if len(response) > 4000:
            await wait_msg.delete()
            # Send chunks, but only add keyboard to the last chunk
            for x in range(0, len(response), 4000):
                chunk = response[x:x+4000]
                if x + 4000 >= len(response):
                    await update.message.reply_text(chunk, reply_markup=reply_markup)
                else:
                    await update.message.reply_text(chunk)
        else:
            await wait_msg.edit_text(response, parse_mode="HTML", reply_markup=reply_markup)
            
    except Exception as e:
        logger.error(f"Error in /ask command: {e}")
        await wait_msg.edit_text(f"❌ Terjadi kesalahan: {e}", parse_mode="HTML")

async def ask_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback from /ask inline keyboard."""
    query = update.callback_query
    await query.answer()

    if query.data == "save_idea":
        prompt = context.user_data.get("last_ask_prompt")
        response = context.user_data.get("last_ask_response")
        
        if not prompt or not response:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("⚠️ Data ide sudah kedaluwarsa, silakan buat ide baru.")
            return
            
        try:
            sheets = get_sheets()
            sheets.save_idea(prompt, response)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("✅ Ide berhasil disimpan ke tab <b>Ideas</b> di Google Sheets!", parse_mode="HTML")
        except Exception as e:
            await query.message.reply_text(f"❌ Gagal menyimpan ide: {e}")


# Per-user active platform ("youtube" or "facebook")
_user_platforms: dict[int, str] = {}

def _get_active_platform(user_id: int) -> str:
    """Get the active platform for a user. Default is youtube."""
    return _user_platforms.get(user_id, "youtube")

async def cmd_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /platform command — switch active target (youtube vs facebook)."""
    user_id = update.effective_user.id
    args = context.args

    if not args:
        active = _get_active_platform(user_id)
        platforms = ["youtube", "facebook"]
        platform_list = "\n".join(
            f"  {'\u2705' if p == active else '\u25cb'} <code>{p}</code>"
            for p in platforms
        )
        await update.message.reply_text(
            f"🎯 <b>Active Platform:</b> <code>{active}</code>\n\n"
            f"<b>Platform tersedia:</b>\n{platform_list}\n\n"
            f"Gunakan: <code>/platform [nama]</code>",
            parse_mode="HTML"
        )
        return

    new_platform = args[0].lower()
    if new_platform not in ["youtube", "facebook"]:
        await update.message.reply_text(
            f"❌ Platform tidak dikenal: <code>{new_platform}</code>\n"
            "Gunakan 'youtube' atau 'facebook'.",
            parse_mode="HTML"
        )
        return

    _user_platforms[user_id] = new_platform
    await update.message.reply_text(
        f"✅ <b>Platform berhasil diubah!</b>\n"
        f"Target upload sekarang: <code>{new_platform}</code>",
        parse_mode="HTML"
    )

async def cmd_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /channel command — switch active YouTube channel."""
    user_id = update.effective_user.id
    args = context.args

    if not args:
        # Show current channel and list
        active = _get_active_channel(user_id)
        channels_list = "\n".join(
            f"  {'\u2705' if c == active else '\u25cb'} <code>{c}</code>"
            for c in config.YOUTUBE_CHANNELS
        )
        await update.message.reply_text(
            f"📺 <b>Active channel:</b> <code>{active}</code>\n\n"
            f"<b>Channels tersedia:</b>\n{channels_list}\n\n"
            f"Gunakan: <code>/channel nama_channel</code>",
            parse_mode="HTML",
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
        channels_list = ", ".join(f"<code>{c}</code>" for c in config.YOUTUBE_CHANNELS)
        await update.message.reply_text(
            f"❌ Channel <code>{target}</code> tidak ditemukan.\n"
            f"Channels tersedia: {channels_list}",
            parse_mode="HTML",
        )
        return

    _user_channels[user_id] = matched
    await update.message.reply_text(
        f"✅ Channel switched ke <b>{matched}</b>\n"
        f"Video berikutnya akan di-upload ke channel ini.",
        parse_mode="HTML",
    )


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /queue command — show today's scheduled uploads."""
    err = _google_not_configured()
    if err:
        await update.message.reply_text(err, parse_mode="HTML")
        return
    try:
        sched = get_scheduler()
        sheets = get_sheets()
        scheduled = sheets.get_scheduled_videos()
        pending = sheets.get_pending_videos()

        videos = scheduled + pending

        if not videos:
            await update.message.reply_text("📭 Tidak ada video dalam antrian.")
            return

        # Calculate estimated times
        now = datetime.now(WIB)
        current_minutes = now.hour * 60 + now.minute
        schedule_minutes = sorted(
            [int(t.split(":")[0]) * 60 + int(t.split(":")[1]) for t in config.UPLOAD_SCHEDULE_HOURS]
        )
        
        summary = sheets.get_queue_summary()
        remaining_today = summary['remaining_today']
        
        # Find next available slot index today
        next_slot_idx = 0
        for i, m in enumerate(schedule_minutes):
            if m > current_minutes:
                next_slot_idx = i
                break
                
        msg = "📋 <b>Antrian Upload:</b>\n\n"
        
        for i, v in enumerate(videos[:20]):
            status_icon = {
                "pending": "⏳",
                "scheduled": "📅",
                "uploading": "📤",
                "uploaded": "✅",
                "failed": "❌",
            }.get(v["status"], "❓")

            import html
            title = html.escape(v.get("title") or v["filename"])
            ch = html.escape(v.get("channel", config.DEFAULT_CHANNEL))
            
            # Estimate time
            if v["status"] in ("pending", "scheduled"):
                if i < remaining_today:
                    # Uploads today
                    slot_idx = (next_slot_idx + i) % len(schedule_minutes)
                    slot_min = schedule_minutes[slot_idx]
                    time_str = f"{slot_min // 60:02d}:{slot_min % 60:02d} WIB"
                    est = f" (Hari ini {time_str})"
                else:
                    # Uploads tomorrow or later
                    days_ahead = (i - remaining_today) // len(schedule_minutes) + 1
                    slot_idx = (i - remaining_today) % len(schedule_minutes)
                    slot_min = schedule_minutes[slot_idx]
                    time_str = f"{slot_min // 60:02d}:{slot_min % 60:02d} WIB"
                    if days_ahead == 1:
                        est = f" (Besok {time_str})"
                    else:
                        est = f" (H+{days_ahead} {time_str})"
            else:
                est = ""

            msg += f"{i+1}. {status_icon} <code>{title}</code> \u2192 {ch}{est}\n"

        msg += f"\n📊 Total: {len(videos)} video"
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /upload command — manually trigger queue processing."""
    err = _google_not_configured()
    if err:
        await update.message.reply_text(err, parse_mode="HTML")
        return
    await update.message.reply_text("🔄 Force upload — mengabaikan jadwal...")

    try:
        # Uploading to YouTube is a blocking network operation
        # Run it in a background thread so the bot stays responsive
        results = await asyncio.to_thread(get_scheduler().force_upload)

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
            import html
            fname = html.escape(r.get('filename', 'Unknown'))
            if r["success"]:
                await update.message.reply_text(
                    f"✅ <b>Uploaded!</b>\n"
                    f"📹 <code>{fname}</code>\n"
                    f"🔗 {r['youtube_link']}",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            else:
                err_msg = html.escape(r.get('error', 'Unknown'))
                await update.message.reply_text(
                    f"❌ <b>Failed:</b> <code>{fname}</code>\n"
                    f"Error: {err_msg}",
                    parse_mode="HTML",
                )

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /extract command — scrape video links from a channel or playlist."""
    if not context.args:
        await update.message.reply_text(
            "❓ <b>Cara Penggunaan:</b>\n"
            "<code>/extract [Link YouTube Channel/Playlist]</code>\n\n"
            "Contoh:\n"
            "<code>/extract https://www.youtube.com/@IdeaClips2/shorts</code>\n"
            "(Maksimal 50 video terbaru akan diambil untuk mencegah spam)",
            parse_mode="HTML"
        )
        return

    url = context.args[0]
    wait_msg = await update.message.reply_text("🔍 <i>Sedang memindai channel/playlist...</i>", parse_mode="HTML")

    def _scrape_urls():
        import yt_dlp
        opts = {
            "extract_flat": True,          # Don't download, just extract info
            "playlist_items": "1-50",      # Limit to 50 items to avoid timeouts
            "quiet": True,
            "no_warnings": True,
            "extractor_args": {"youtube": ["skip=dash,hls"]}
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        # Run blocking yt-dlp extraction in background thread
        info = await asyncio.to_thread(_scrape_urls)
        
        if not info or "entries" not in info:
            await wait_msg.edit_text("❌ Gagal menemukan daftar video di link tersebut.")
            return
            
        entries = list(info["entries"])
        if not entries:
            await wait_msg.edit_text("📭 Channel/playlist kosong atau tidak bisa diakses.")
            return

        urls = []
        for entry in entries:
            # For YouTube, url is often just the ID, so we construct the full link
            base_url = entry.get("url") or entry.get("webpage_url") or entry.get("id")
            if base_url:
                if not base_url.startswith("http"):
                    base_url = f"https://www.youtube.com/watch?v={base_url}"
                urls.append(base_url)

        if not urls:
            await wait_msg.edit_text("❌ Tidak ada link valid yang bisa diekstrak.")
            return

        # Send back in chunks to avoid Telegram message length limits
        chunk_size = 20
        await wait_msg.edit_text(f"✅ Berhasil menemukan <b>{len(urls)}</b> video!\n\nSilakan copy-paste link di bawah ini ke bot:", parse_mode="HTML")
        
        for i in range(0, len(urls), chunk_size):
            chunk = urls[i:i + chunk_size]
            msg_text = "\n".join(chunk)
            # Add small delay between messages to not trigger spam blocks
            await asyncio.sleep(0.5)
            await update.message.reply_text(msg_text, disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Error in /extract: {e}")
        await wait_msg.edit_text(f"❌ Error saat mengekstrak: {str(e)[:200]}")

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

    import html
    fname_esc = html.escape(file_name)
    await message.reply_text(
        f"📥 <b>Menerima video:</b>\n"
        f"📄 <code>{fname_esc}</code>\n"
        f"📏 {size_mb:.1f} MB\n\n"
        f"⏳ Mengunduh dari Telegram...",
        parse_mode="HTML",
    )

    # Check Google config before proceeding
    err = _google_not_configured()
    if err:
        await message.reply_text(err, parse_mode="HTML")
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
        user_id = update.effective_user.id
        active_ch = _get_active_channel(user_id)
        active_platform = _get_active_platform(user_id)
        
        sheets = get_sheets()
        row = sheets.add_video(
            filename=file_name,
            drive_link=drive_result["web_view_link"],
            channel=active_ch,
            platform=active_platform
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
            platform=active_platform
        )

        # Step 5: Clean up temp file
        if os.path.exists(local_path):
            os.remove(local_path)

        # Step 6: Check if we can upload now or need to schedule
        summary = sheets.get_queue_summary(platform=active_platform)
        if summary["remaining_today"] > 0:
            status_msg = (
                f"📺 Video siap upload ke {active_platform.title()}!\n"
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

        import html
        fname_esc = html.escape(file_name)
        title_esc = html.escape(metadata['title'])
        tags_esc = html.escape(metadata['tags'])
        
        await message.reply_text(
            f"✅ <b>Pipeline selesai!</b>\n\n"
            f"📄 File: <code>{fname_esc}</code>\n"
            f"📝 Title: {title_esc}\n"
            f"🏷️ Tags: {tags_esc}\n\n"
            f"{status_msg}\n\n"
            f"💡 Kamu bisa edit metadata di Google Sheets sebelum upload.",
            parse_mode="HTML",
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
    r'tiktok\.com/|vm\.tiktok\.com/|vt\.tiktok\.com/|'
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
    matches = list(URL_PATTERN.finditer(text))
    if not matches:
        return  # Not a supported video URL

    # Check Google config
    err = _google_not_configured()
    if err:
        await message.reply_text(err, parse_mode="HTML")
        return

    for match in matches:
        url = match.group(0)

        import html
        url_esc = html.escape(url)
        await message.reply_text(
            f"🔗 <b>Link detected!</b>\n"
            f"<code>{url_esc}</code>\n\n"
            f"⏳ Downloading video via yt-dlp...",
            parse_mode="HTML",
        )

        local_path = None
        try:
            import yt_dlp

            # yt-dlp options: best quality, mp4 format, bypass Android client
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'merge_output_format': 'mp4',
                'outtmpl': str(config.TEMP_DIR / '%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'max_filesize': 500 * 1024 * 1024,  # 500 MB max
                'extractor_args': {
                    'youtube': [
                        'player_client=android,ios',
                        'player_skip=configs,webpage'
                    ],
                    'tiktok': [
                        'app_version=32.1.3',
                        'manifest_app_version=32.1.3'
                    ]
                },
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                }
            }

            # Check for cookies file to bypass YouTube's datacenter block
            cookies_paths = [
                "www.youtube.com_cookies.txt",  # Local
                "/etc/secrets/www.youtube.com_cookies.txt"  # Render Secret File
            ]
            for cp in cookies_paths:
                if os.path.exists(cp):
                    ydl_opts['cookiefile'] = cp
                    logger.info(f"Using yt-dlp cookies file: {cp}")
                    break

            # Download using asyncio.to_thread to prevent blocking main thread
            def _download_video():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(url, download=True)
                    download_path = ydl.prepare_filename(info_dict)
                    return info_dict, download_path

            info, local_path = await asyncio.to_thread(_download_video)

            if not os.path.exists(local_path):
                await message.reply_text("❌ Download gagal — file tidak ditemukan.")
                continue

            video_title = info.get('title', 'video')
            video_desc = info.get('description', '')
            video_tags = info.get('tags', [])
            duration = info.get('duration', 0)

            file_name = os.path.basename(local_path)
            size_mb = os.path.getsize(local_path) / (1024 * 1024)

            duration_str = ""
            if duration:
                mins, secs = divmod(int(duration), 60)
                duration_str = f"\n⏱️ Duration: {mins}:{secs:02d}"

            v_title_esc = html.escape(video_title)
            await message.reply_text(
                f"✅ <b>Download selesai!</b>\n"
                f"🎬 <code>{v_title_esc}</code>\n"
                f"📏 {size_mb:.1f} MB{duration_str}\n\n"
                f"📁 Uploading ke Google Drive...",
                parse_mode="HTML",
            )

            # Continue pipeline: Drive → Sheets → Groq
            drive_result = get_drive().upload(local_path)
            await message.reply_text(
                f"✅ Uploaded ke Drive!\n"
                f"🔗 {drive_result['web_view_link']}\n\n"
                f"🧠 Generating metadata via Groq AI...",
            )

            user_id = update.effective_user.id
            active_ch = _get_active_channel(user_id)
            active_platform = _get_active_platform(user_id)
            
            sheets = get_sheets()
            row = sheets.add_video(
                filename=file_name,
                drive_link=drive_result["web_view_link"],
                channel=active_ch,
                platform=active_platform
            )

            # Form rich context for Groq AI to avoid hallucination
            context_parts = [f"Original title: {video_title}"]
            extra = message.caption or ""
            if extra:
                context_parts.append(f"User caption: {extra}")
            if video_desc:
                # Limit description to 1000 chars to avoid token Bloat
                context_parts.append(f"Original description: {video_desc[:1000]}")
            if video_tags:
                tags_str = ", ".join(video_tags[:20]) # Limit to first 20 tags
                context_parts.append(f"Original tags: {tags_str}")
                
            rich_context = "\n".join(context_parts)

            # Use original rich context for Groq
            from groq_metadata import generate_metadata
            metadata = generate_metadata(
                file_name, extra_context=rich_context
            )
            sheets.update_metadata(
                row,
                metadata["title"],
                metadata["description"],
                metadata["tags"],
                platform=active_platform
            )

            # Clean up temp
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
            except PermissionError:
                pass

            # Status
            summary = sheets.get_queue_summary(platform=active_platform)
            sched = get_scheduler()
            next_time = sched.get_next_upload_time()

            if summary["remaining_today"] > 0:
                status_msg = (
                    f"📺 Video dijadwalkan upload ke <b>{active_platform.title()}</b> di <code>{next_time}</code>\n"
                    f"Atau ketik /upload untuk force upload sekarang.\n"
                    f"📊 Sisa slot hari ini: {summary['remaining_today']}"
                )
            else:
                status_msg = (
                    f"📅 Limit harian tercapai!\n"
                    f"Video dijadwalkan untuk besok."
                )

            # Step 5: Notify user via Telegram
            fname = html.escape(file_name)
            title_esc = html.escape(metadata["title"])
            tags_esc = html.escape(metadata["tags"])
            
            await message.reply_text(
                f"✅ <b>Pipeline selesai!</b>\n\n"
                f"📄 File: <code>{fname}</code>\n"
                f"📝 Title: {title_esc}\n"
                f"🏷️ Tags: {tags_esc}\n\n"
                f"{status_msg}\n\n"
                f"💡 Kamu bisa edit metadata di Google Sheets sebelum upload.",
                parse_mode="HTML",
            )

        except Exception as e:
            logger.error(f"Error processing URL: {e}", exc_info=True)
            await message.reply_text(f"❌ Error for {url_esc}: {e}", parse_mode="HTML")
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
        # Run YouTube upload in a background thread to prevent blocking the scheduler/bot
        results = await asyncio.to_thread(get_scheduler().process_queue)

        chat_id = config.TELEGRAM_CHAT_ID
        if not chat_id:
            logger.warning("TELEGRAM_CHAT_ID not set, skipping notifications.")
            return

        for r in results:
            if r["success"]:
                import html
                fname_esc = html.escape(r['filename'])
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"✅ <b>Auto-uploaded!</b>\n"
                        f"📹 <code>{fname_esc}</code>\n"
                        f"🔗 {r['youtube_link']}"
                    ),
                    parse_mode="HTML",
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


# ─── Health Check Server (for Render) ──────────────────────────────


def _start_health_server():
    """Start a simple HTTP server for Render health checks."""
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    port = int(os.environ.get("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"YT Upload Bot is running!")

        def log_message(self, format, *args):
            pass  # Suppress HTTP logs

    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health check server running on port {port}")


# ─── Main ──────────────────────────────────────────────────────────


def main():
    """Start the Telegram bot."""
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set! Check your .env file.")
        return

    # Start health check server (for Render)
    if os.environ.get("RENDER"):
        _start_health_server()

    logger.info("Starting Video Upload Pipeline Bot...")

    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(CommandHandler("channel", cmd_channel))
    app.add_handler(CommandHandler("platform", cmd_platform))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(CallbackQueryHandler(ask_callback, pattern="^save_idea$"))

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

    logger.info("Starting keep-alive web server for Render...")
    import keep_alive
    keep_alive.keep_alive()

    logger.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
