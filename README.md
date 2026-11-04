# Telegram Media Downloader Bot

Send the bot a TikTok, Instagram, Pinterest or X link. It downloads the media and sends it to Telegram without asking you to select a format. Availability and quality depend on the source platform.

## What it does

- Sends videos, photos and mixed photo/video carousels. Carousels are split into groups of up to 10 items.
- Restricts access to the Telegram IDs or usernames in `ALLOWED_USERS`. The bot will not start if the list is empty.
- Allows one download at a time per user. Request files are removed after delivery or failure; stale folders created by this version are cleaned after 24 hours.
- Starts an HTTP health endpoint when `PORT` is set. It also pings itself, but this does not guarantee that a free hosting instance stays awake.
- Uses English messages. Download percentages are available for yt-dlp downloads.
- Shows short download errors to users; diagnostic details are kept in server logs with URLs and bot tokens redacted.

## Supported platforms

| Platform | Media | Notes |
| :--- | :--- | :--- |
| **TikTok** | Videos & Photo Slideshows | Short links, fallback download sources |
| **Instagram** | Reels, Videos, Photos & Carousels | Photo/video albums |
| **Pinterest** | Photos & Videos | Media extraction |
| **Twitter / X** | Videos & Photos | yt-dlp extraction, including `t.co` links |

Only links to these platforms are accepted. YouTube is not supported.

Uploads are limited to 50,000,000 bytes for videos/audio and 10,000,000 bytes for photos. Each request is limited to 200,000,000 bytes of temporary data and 20 carousel items. Oversized downloads are rejected rather than compressed.

## Requirements

- Python 3.10+
- **FFmpeg** (installed and added to system PATH)

Python dependencies are listed in `requirements.txt`.

## Setup and running

### Local setup

1. **Clone the repository:**
   ```bash
    git clone https://github.com/Helvior-dev/Shrot-Downloader-Bot.git
    cd Shrot-Downloader-Bot
   ```

2. **Configure environment variables:**
   Create a `.env` file based on `.env.example`:
   ```env
   # Telegram bot token from @BotFather
   BOT_TOKEN=your_bot_token

   # Required: Telegram user IDs (comma-separated)
   ALLOWED_USERS=123456789,987654321

   # Optional: sticker shown while downloading
   LOADING_STICKER_ID=

   # Optional: HTTP/HTTPS proxy
   PROXY_URL=
   ```

3. **Create and activate a virtual environment (`.venv`):**
   * **Windows (PowerShell):**
     ```powershell
      python -m venv .venv
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

### Render deployment

1. Push your code to your GitHub repository.
2. In the [Render Dashboard](https://dashboard.render.com), click **New +** -> **Web Service**.
3. Connect your repository.
4. Configure service settings:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
5. In **Environment Variables**, add:
    - `BOT_TOKEN` = `your_bot_token_from_botfather`
    - `ALLOWED_USERS` = `123456789,987654321` (required; use your actual Telegram user IDs)
6. Click **Deploy Web Service**.

## Bot commands

- `/start`: instructions for sending a link.
- `/help`: supported platforms and file limits.
