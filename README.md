# 🎬 Telegram → YouTube Auto Upload Pipeline

Pipeline otomatis: terima video dari Telegram → simpan di Google Drive → generate metadata via Groq AI → upload ke YouTube dengan scheduling (max 6/hari).

## Architecture

```
Telegram Bot → Google Drive → Google Sheets → YouTube
                                    ↕
                              Groq AI (metadata)
```

## Features

- 📱 **Telegram Bot** — kirim video dari HP, langsung masuk pipeline
- 📁 **Google Drive** — backup otomatis semua video
- 📊 **Google Sheets** — queue management, edit metadata sebelum upload
- 🧠 **Groq AI** — auto-generate judul, deskripsi, tags (SEO-friendly)
- 📺 **YouTube Upload** — otomatis dengan scheduling (max 6/hari)
- ⏰ **Scheduler** — proses antrian setiap 30 menit
- 📅 **Auto-schedule** — overflow otomatis ke hari berikutnya

## Bot Commands

| Command | Fungsi |
|---------|--------|
| `/start` | Welcome message & help |
| `/status` | Lihat status antrian (pending/uploaded/failed) |
| `/queue` | Lihat jadwal upload hari ini |
| `/upload` | Trigger upload manual ke YouTube |

## Setup Guide

### 1. Prerequisites

- Python 3.10+
- Akun Google (Gmail)
- Akun Groq (gratis)

### 2. Buat Telegram Bot

1. Chat [@BotFather](https://t.me/BotFather) di Telegram
2. Kirim `/newbot` dan ikuti instruksi
3. Simpan **Bot Token** yang diberikan

### 3. Setup Google Cloud

1. Buka [Google Cloud Console](https://console.cloud.google.com/)
2. Buat project baru
3. Enable APIs:
   - **Google Drive API**
   - **Google Sheets API**
   - **YouTube Data API v3**

#### Service Account (untuk Drive & Sheets)

4. Buka **APIs & Services → Credentials**
5. **Create Credentials → Service Account**
6. Download JSON key → simpan ke `credentials/service_account.json`

#### OAuth2 (untuk YouTube)

7. **Create Credentials → OAuth 2.0 Client ID**
8. Application type: **Desktop App**
9. Download JSON → simpan ke `credentials/client_secrets.json`
10. Buka **OAuth consent screen** → tambahkan email kamu sebagai test user

### 4. Setup Google Sheets

1. Buat Google Sheet baru
2. Copy Sheet ID dari URL:
   ```
   https://docs.google.com/spreadsheets/d/[SHEET_ID]/edit
   ```
3. **Share** sheet ke email service account (ada di JSON file)
4. Beri akses **Editor**

### 5. Setup Google Drive

1. Buat folder di Google Drive untuk video
2. Copy Folder ID dari URL:
   ```
   https://drive.google.com/drive/folders/[FOLDER_ID]
   ```
3. **Share** folder ke email service account
4. Beri akses **Editor**

### 6. Daftar Groq

1. Buka [console.groq.com](https://console.groq.com)
2. Daftar gratis
3. Buat API Key

### 7. Install & Run

```bash
# Clone / masuk ke folder project
cd giant-cluster

# Copy .env template
cp .env.example .env

# Edit .env dengan credentials kamu
notepad .env

# Install dependencies
pip install -r requirements.txt

# Jalankan bot
python bot.py
```

Saat pertama kali jalan, browser akan terbuka untuk **YouTube OAuth2 login**.
Login dengan akun Google yang punya channel YouTube.

### 8. Test

1. Buka Telegram → chat ke bot kamu
2. Kirim `/start`
3. Kirim video file
4. Cek Google Sheets — video harus muncul dengan metadata
5. Kirim `/upload` — video akan diupload ke YouTube

## Environment Variables

| Variable | Deskripsi |
|----------|-----------|
| `TELEGRAM_BOT_TOKEN` | Token dari @BotFather |
| `GROQ_API_KEY` | API key dari console.groq.com |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path ke service account JSON |
| `GOOGLE_DRIVE_FOLDER_ID` | ID folder Google Drive |
| `GOOGLE_SHEET_ID` | ID Google Sheet |
| `YOUTUBE_CLIENT_SECRETS_FILE` | Path ke OAuth2 client secrets JSON |
| `YOUTUBE_CATEGORY` | YouTube category ID (default: 22) |
| `YOUTUBE_PRIVACY` | Privacy status: public/private/unlisted |
| `MAX_UPLOADS_PER_DAY` | Max upload per hari (default: 6) |
| `SCHEDULER_INTERVAL_MINUTES` | Interval scheduler dalam menit (default: 30) |

## Free Tier Limits

| Service | Limit |
|---------|-------|
| YouTube Data API v3 | 10,000 units/hari (~6 upload) |
| Google Apps Script | N/A (we use Python instead) |
| Google Drive | 15 GB storage |
| Telegram Bot API | File max 20 MB upload, 50 MB download |
| Groq API | 30 req/min, 14,400 req/day |
| Google Sheets | 10 juta sel |

## Troubleshooting

| Error | Solusi |
|-------|--------|
| `TELEGRAM_BOT_TOKEN not set` | Isi token di `.env` file |
| `File too large` | Telegram limit 50MB download, kirim file yang lebih kecil |
| `Quota exceeded` | YouTube limit tercapai, tunggu besok |
| `OAuth2 error` | Hapus `credentials/youtube_token.json` dan login ulang |
| `Drive permission denied` | Pastikan folder di-share ke service account email |
