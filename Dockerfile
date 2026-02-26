FROM python:3.13-slim

# Install ffmpeg for yt-dlp
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create temp/credentials directories
RUN mkdir -p temp credentials

# Restore credentials from env vars, then start bot
CMD python setup_credentials.py && python bot.py
