# yt-dlp Telegram Bot

A simple, high-performance Telegram bot to download videos from YouTube, TikTok, Twitter, Reddit, and more using [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Features

- 🎥 **Video Download**: Supports thousands of sites via yt-dlp, check here [Supported sites](https://ytdl-org.github.io/youtube-dl/supportedsites.html).
- 🎵 **Audio Extraction**: Convert videos to MP3.
- ⚙️ **Quality Selection**: Choose video resolution (1080p, 720p, etc.).
- 🚀 **High Performance**: Concurrent downloads and uploads.
- ~~⚡ **Aria2c Support**: Optimized for speed and stability.~~ (Disabled, since default downloader seems to be performiing better)

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/forsyth47/yt-dlp-telegram.git
   cd yt-dlp-telegram
   ```

2. **Install dependencies**
   ```bash
   python3 -m venv .venv
   source ./.venv/bin/activate
   pip install -r requirements.txt
   # Completely Optional: Install aria2 for experimental downloads (not recommened, unless for devs)
   # brew install aria2  # macOS
   # sudo apt install aria2  # Linux
   ```

3. **Configuration**
   Edit `config.py` with your Telegram API credentials:
   ```python
   token = "YOUR_BOT_TOKEN"
   api_id = 123456
   api_hash = "your_api_hash"
   ```

4. **Run the bot**
   ```bash
   python main.py
   ```

## Credits

Inspired by and based on the idea from [ssebastianoo/yt-dlp-telegram](https://github.com/ssebastianoo/yt-dlp-telegram).
