# 📥 Telegram Media Downloader Bot

A fast, lightweight, and robust Telegram bot that downloads media (videos, photos, and photo carousels) from popular social networks directly into Telegram in original quality without watermarks.

---

## ✨ Features

- ⚡ **Direct Instant Download**: Just send a link — no annoying format buttons or confirmation dialogs. The bot automatically identifies whether it's a video, single photo, or photo carousel and sends it directly.
- 🖼️ **Photo & Carousel Support**: Full HD photo downloads and multi-image galleries (carousels) with background music for TikTok and Instagram.
- 🔒 **Whitelist Access Control**: Restrict bot usage to specific Telegram usernames or user IDs via `ALLOWED_USERS`.
- 🧹 **Automatic Resource Cleanup**: Temporary files and folders are immediately purged after upload to keep server storage minimal.
- 🛡️ **Anti-Spam Concurrency Locks**: Prevents multiple concurrent downloads from the same user to avoid rate-limiting.
- ☁️ **Cloud Ready (Render / Railway / VPS)**: Built-in aiohttp HTTP server and self-pinger task to prevent free instances from sleeping.
- 🇬🇧 **100% English UI**: Clean, localized messages with live progress percentages.

---

## 🌐 Supported Platforms

| Platform | Supported Media | Features |
| :--- | :--- | :--- |
| **TikTok** | Videos & Photo Slideshows | Original audio merged, watermark-free |
| **Instagram** | Reels, Videos, Photos & Carousels | Full HD quality, fast download |
| **Pinterest** | Photos & Videos | Direct media extraction |
| **Twitter / X** | Videos & Photos | Highest available bitrate |

> **Note**: YouTube downloading is intentionally disabled to ensure 100% stable, cookie-free hosting on cloud datacenters (like Render).

---

## 📋 Requirements

- Python 3.10+
- **FFmpeg** (installed and added to system PATH)

Built with `aiogram 3`, `yt-dlp`, `aiohttp`, and `Instaloader`.

---

## 🚀 Setup & Running

### 1. Local Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your_username/your_repository_name.git
   cd your_repository_name
   ```

2. **Configure environment variables:**
   Create a `.env` file based on `.env.example`:
   ```env
   # Telegram Bot Token from @BotFather
   BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ

   # Optional: Allowed Telegram Usernames or IDs (comma-separated). Leave empty to allow everyone.
   ALLOWED_USERS=your_username, friend_username, 123456789

   # Optional: Telegram sticker ID to show while downloading
   LOADING_STICKER_ID=
   ```

3. **Create and activate a virtual environment (`.venv`):**
   * **Windows (PowerShell):**
     ```powershell
     python -m venv .venv
     Set-ExecutionPolicy -Scope CurrentUser RemoteSigned  # Run once if scripts are disabled
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
   ```

---

### 2. Cloud Deployment (Render.com)

1. Push your code to your GitHub repository.
2. In the [Render Dashboard](https://dashboard.render.com), click **New +** -> **Web Service**.
3. Connect your repository.
4. Configure service settings:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
5. In the **Environment Variables** section, add:
   - `BOT_TOKEN` = `your_bot_token_from_botfather`
   - `ALLOWED_USERS` = `your_username, other_user`
6. Click **Deploy Web Service**.

---

## 🤖 Bot Commands

- `/start` — Welcome message and instructions.
- `/help` — List of supported social media platforms.

---

## 📄 License

MIT License. Free to use, modify, and distribute.
