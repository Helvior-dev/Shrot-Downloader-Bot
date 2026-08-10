# Telegram Media Downloader Bot

A Telegram bot that downloads media (videos, photos, carousels, and audio) from various social networks directly into Telegram. No ads, watermarks, or popups.

## Features

- **Downloads Video & Audio**: Fetches high-quality MP4 videos and extracts MP3 audio tracks.
- **Photo & Carousel Support**: Supports downloading single photos and multi-image galleries (carousels) from Instagram, TikTok, and Pinterest.
- **Access Control**: Can be restricted to specific Telegram usernames or user IDs.
- **Resource Management**: Automatically cleans up temporary files after sending them to the user.
- **Concurrency Locks**: Prevents multiple parallel downloads of the same link to avoid spam and save bandwidth.
- **Render Ready**: Includes a background ping mechanism to keep the bot awake on free hosting tiers like Render.

## Supported Platforms

| Platform | Video | Photo / Carousel | Audio (MP3) |
| :--- | :---: | :---: | :---: |
| TikTok | Yes | Yes | Yes |
| Instagram | Yes | Yes | Yes |
| YouTube Shorts | Yes | No | Yes |
| Pinterest | Yes | Yes | No |
| Twitter / X | Yes | Yes | Yes |

## Requirements

- Python 3.10+
- FFmpeg (must be installed on the system)

Built with `aiogram 3`, `yt-dlp`, `aiohttp`, and `Instaloader`.

## Setup

### Local Installation

1. Clone the repository:
```bash
git clone https://github.com/your_username/your_repository_name.git
cd your_repository_name
```

2. Create a `.env` file based on the example:
```env
BOT_TOKEN=your_telegram_bot_token_from_botfather
# Optional: comma-separated list of allowed user IDs or usernames
ALLOWED_USERS=123456789, your_username
# Optional: Telegram sticker ID to show while downloading
LOADING_STICKER_ID=CAACAgIAAxkBAAMHanndNbk4bRRly3ame8EjBE1cxOQAAr0cAAJHONBKJuWKeGhBF-09BA
```

3. Set up the virtual environment and install dependencies:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows use: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

4. Run the bot:
```bash
python bot.py
```

### Deployment (Render Free Tier)

1. Push your code to GitHub.
2. In [Render](https://dashboard.render.com), create a new **Web Service**.
3. Connect your repository.
4. Add the required environment variables (`BOT_TOKEN`, `ALLOWED_USERS`, `LOADING_STICKER_ID`).
5. Deploy. The built-in pinger will prevent the service from sleeping.
