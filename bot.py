import asyncio
import logging
import os
import time
import shutil
import tempfile
import glob
from dotenv import load_dotenv

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

# Load environment variables
load_dotenv()

from handlers import commands, downloader
from downloader import OWNER_MARKER

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

async def clean_temp_directories_task():
    """Clean stale downloads without deleting another active instance's files at startup."""
    while True:
        try:
            temp_dir = tempfile.gettempdir()
            pattern = os.path.join(temp_dir, "tg_bot_vids_*")
            orphaned = glob.glob(pattern)
            for folder in orphaned:
                try:
                    if (os.path.isdir(folder) and not os.path.islink(folder)
                            and os.path.isfile(os.path.join(folder, OWNER_MARKER))
                            and os.path.getmtime(folder) < time.time() - 86400):
                        shutil.rmtree(folder)
                        logger.info(f"Cleaned up orphaned temp directory: {folder}")
                except Exception as e:
                    logger.warning(f"Failed to remove {folder}: {e}")
        except Exception as e:
            logger.error(f"Error in temp cleanup task: {e}")
        await asyncio.sleep(1800)

async def self_pinger_task() -> None:
    """Periodically ping the HTTP endpoint; hosting may still suspend free instances."""
    await asyncio.sleep(10)
    port = os.getenv("PORT", "10000")
    render_url = os.getenv("RENDER_EXTERNAL_URL", f"http://127.0.0.1:{port}")
    
    logger.info(f"Self-pinger task started targeting: {render_url}")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await asyncio.sleep(600)
                async with session.get(render_url) as resp:
                    logger.info(f"Self-ping status: {resp.status}")
            except Exception as e:
                logger.warning(f"Self-ping attempt failed: {e}")

async def start_dummy_web_server() -> None:
    """Start lightweight HTTP server for Render health checks if PORT env is set."""
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", lambda req: web.Response(text="Bot is running!"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health check HTTP server listening on port {port}")

async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in the environment or .env file.")

    if not os.getenv("ALLOWED_USERS", "").strip():
        raise RuntimeError("ALLOWED_USERS is empty. Configure Telegram user IDs before starting the bot.")

    if os.getenv("PORT"):
        await start_dummy_web_server()
        asyncio.create_task(self_pinger_task())

    # Start background cleanup tasks
    asyncio.create_task(clean_temp_directories_task())

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    # Include routers
    dp.include_router(commands.router)
    dp.include_router(downloader.router)
    
    # Set bot commands in Telegram menu
    await bot.set_my_commands([
        BotCommand(command="start", description="Start the bot"),
        BotCommand(command="help", description="Supported platforms"),
    ])

    print("Bot is running and ready for messages.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
