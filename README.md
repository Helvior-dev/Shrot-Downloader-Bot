# Telegram Media Downloader Bot

A fast, lightweight Telegram bot that automatically downloads media (videos, photos, and carousels) from popular social networks directly into Telegram without ads, watermarks, or unnecessary format selection prompts.

## Features

- **Direct Instant Download**: Just send a link — the bot automatically detects whether it's a video, photo, or photo carousel and downloads it immediately.
- **YouTube Anti-Bot Bypass**: Uses optimized mobile extractor clients (`ios`, `android`, `mweb`) to bypass "Sign in to confirm you're not a bot" checks without requiring cookies.
- **Photo & Carousel Support**: Full HD photo and multi-image gallery support for Instagram, TikTok, and Pinterest.
- **Access Control**: Can be restricted to specific Telegram usernames or user IDs via `ALLOWED_USERS`.
- **Resource Management**: Automatically cleans up temporary files after sending them to the user.
- **Concurrency Locks**: Prevents multiple parallel downloads from the same user to avoid spam and save bandwidth.
- **Render Ready**: Includes a background health check and self-pinger to prevent sleep on cloud platforms like Render.

## Supported Platforms

| Platform | Supported Media |
| :--- | :--- |
| **TikTok** | Videos & Photo Carousels |
| **Instagram** | Reels, Videos, Single Photos & Carousels |
| **YouTube** | Shorts & Standard Videos |
| **Pinterest** | Photos & Videos |
| **Twitter / X** | Videos & Photos |

## Requirements

- Python 3.10+
- FFmpeg (must be installed and added to system PATH)

Built with `aiogram 3`, `yt-dlp`, `aiohttp`, and `Instaloader`.

## Setup & Running

### 1. Local Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your_username/your_repository_name.git
   cd your_repository_name
   ```

2. **Configure environment variables:**
   Create a `.env` file from `.env.example`:
   ```env
   BOT_TOKEN=your_telegram_bot_token_from_botfather
   # Optional: comma-separated list of allowed user IDs or usernames
   ALLOWED_USERS=123456789, your_username
   # Optional: Telegram sticker ID to show while downloading
   LOADING_STICKER_ID=
   ```

3. **Create and activate virtual environment (`.venv`):**
   * **Windows (PowerShell):**
     ```powershell
     python -m venv .venv
     Set-ExecutionPolicy -Scope CurrentUser RemoteSigned  # Required once on fresh Windows
     .\.venv\Scripts\Activate.ps1
     ```
   * **Linux / macOS:**
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```

4. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

5. **Start the bot:**
   ```bash
   python bot.py
   # Or directly on Windows without activation:
   # .\.venv\Scripts\python.exe bot.py
   ```

---

### 2. Deployment on Render

1. Push your code to GitHub.
2. In [Render](https://dashboard.render.com), create a new **Web Service**.
3. Connect your repository.
4. Set the **Build Command**:
   ```bash
   pip install -r requirements.txt
   ```
5. Set the **Start Command**:
   ```bash
   python bot.py
   ```
6. Add your **Environment Variables** in Render Dashboard (`BOT_TOKEN`, `ALLOWED_USERS`, etc.).
7. Deploy!
