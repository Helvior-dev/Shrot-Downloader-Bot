import asyncio
import html
import json
import os
import re
import subprocess
import uuid
import tempfile
import logging
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Any
import yt_dlp
import aiohttp
import instaloader

logger = logging.getLogger(__name__)

# Max file size for Telegram Bot API (50 MB)
MAX_FILE_SIZE = 50 * 1024 * 1024
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

@dataclass
class MediaInfo:
    filepath: Optional[str]
    title: str
    duration: Optional[int]
    uploader: Optional[str]
    width: Optional[int]
    height: Optional[int]
    media_type: str = "video"  # "video", "audio", "photo", "carousel"
    image_paths: List[str] = field(default_factory=list)

class DownloadError(Exception):
    pass

class FileTooLargeError(DownloadError):
    pass

def check_file_size_limit(filepath: str):
    """Check if video file exceeds Telegram 50 MB limit."""
    if os.path.exists(filepath):
        file_size = os.path.getsize(filepath)
        if file_size > MAX_FILE_SIZE:
            os.remove(filepath)
            raise FileTooLargeError("This video file is too large (>50MB). Large video compression feature is currently in development 🛠️")

def extract_instagram_shortcode(url: str) -> Optional[str]:
    """Extract Instagram shortcode from post/reel/tv URL."""
    match = re.search(r'/(?:p|reel|reels|tv)/([^/?#&]+)', url)
    return match.group(1) if match else None

async def download_file_aiohttp(url: str, filepath: str, headers: dict) -> None:
    timeout = aiohttp.ClientTimeout(total=120, sock_read=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            with open(filepath, 'wb') as f:
                async for chunk in resp.content.iter_chunked(8192):
                    f.write(chunk)

def _has_audio_stream(filepath: str) -> bool:
    """Check if a video file contains an audio stream using ffprobe."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', filepath],
            capture_output=True, text=True, timeout=10
        )
        return 'audio' in result.stdout
    except Exception:
        return True  # Assume audio exists if ffprobe fails

async def _merge_audio_into_video(video_path: str, audio_url: str, output_dir: str, headers: dict) -> str:
    """Download separate audio track and merge it into a video file using ffmpeg."""
    audio_path = os.path.join(output_dir, f"{uuid.uuid4()}_audio.mp3")
    merged_path = os.path.join(output_dir, f"{uuid.uuid4()}_merged.mp4")
    
    try:
        await download_file_aiohttp(audio_url, audio_path, headers)
        
        loop = asyncio.get_running_loop()
        cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-i', audio_path,
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-shortest',
            merged_path
        ]
        await loop.run_in_executor(None, lambda: subprocess.run(cmd, capture_output=True, timeout=60))
        
        if os.path.exists(merged_path) and os.path.getsize(merged_path) > 0:
            os.remove(video_path)
            os.remove(audio_path)
            logger.info(f"Successfully merged audio into video: {os.path.getsize(merged_path) / 1024 / 1024:.2f} MB")
            return merged_path
    except Exception as e:
        logger.warning(f"Audio merge failed: {e}")
        for f in [audio_path, merged_path]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
    
    return video_path  # Return original if merge fails

async def _instagram_fallback_download(url: str, output_dir: str) -> MediaInfo:
    """Fallback downloader for Instagram photo posts/carousels via Instaloader."""
    shortcode = extract_instagram_shortcode(url)
    if not shortcode:
        raise DownloadError("Could not parse Instagram shortcode.")

    def run_instaloader():
        L = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            quiet=True
        )
        return instaloader.Post.from_shortcode(L.context, shortcode)

    loop = asyncio.get_running_loop()
    try:
        post = await loop.run_in_executor(None, run_instaloader)
    except Exception as e:
        raise DownloadError(f"Instagram download error: {str(e)}")

    caption = post.caption or "Instagram Post"
    uploader = post.owner_username

    items = []
    if post.typename == "GraphSidecar":
        for node in post.get_sidecar_nodes():
            if node.is_video:
                items.append((node.video_url, "video"))
            else:
                items.append((node.display_url, "photo"))
    elif post.is_video:
        items.append((post.video_url, "video"))
    else:
        items.append((post.url, "photo"))

    if not items:
        raise DownloadError("No media found in Instagram post.")

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    downloaded_paths = []

    for i, (media_url, mtype) in enumerate(items):
        ext = ".mp4" if mtype == "video" else ".jpg"
        file_id = f"{uuid.uuid4()}_{i}{ext}"
        filepath = os.path.join(output_dir, file_id)

        await download_file_aiohttp(media_url, filepath, headers)
        downloaded_paths.append((filepath, mtype))

    if len(downloaded_paths) == 1:
        fp, mtype = downloaded_paths[0]
        if mtype == "video":
            check_file_size_limit(fp)
        return MediaInfo(
            filepath=fp,
            title=caption[:100],
            duration=None,
            uploader=uploader,
            width=None,
            height=None,
            media_type=mtype
        )
    else:
        img_paths = [fp for fp, _ in downloaded_paths]
        return MediaInfo(
            filepath=None,
            title=caption[:100],
            duration=None,
            uploader=uploader,
            width=None,
            height=None,
            media_type="carousel",
            image_paths=img_paths
        )

async def _pinterest_fallback_download(url: str, output_dir: str) -> MediaInfo:
    """Fallback downloader for Pinterest pins (photos & videos)."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    
    timeout = aiohttp.ClientTimeout(sock_read=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                content = await resp.text()
    except Exception as e:
        raise DownloadError(f"Pinterest error: {str(e)}")

    og_video = None
    m_video = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']og:video[^"\']*["\']', content)
    if not m_video:
        m_video = re.search(r'<meta[^>]+(?:name|property)=["\']og:video[^"\']*["\'][^>]+content=["\']([^"\']+)["\']', content)
    if m_video:
        og_video = m_video.group(1)

    og_image = None
    m_image = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']og:image["\']', content)
    if not m_image:
        m_image = re.search(r'<meta[^>]+(?:name|property)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', content)
    if m_image:
        og_image = m_image.group(1)

    title_match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']og:title["\']', content)
    if not title_match:
        title_match = re.search(r'<title>([^<]+)</title>', content)
    title = title_match.group(1) if title_match else "Pinterest Pin"

    if og_video:
        target_url = og_video
        ext = ".mp4"
        media_type = "video"
    elif og_image:
        target_url = og_image
        ext = os.path.splitext(target_url)[1].lower() or ".jpg"
        media_type = "photo"
    else:
        video_urls = re.findall(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', content)
        image_urls = re.findall(r'https?://i\.pinimg\.com/(?:originals|736x|564x|474x)/[a-zA-Z0-9/_.\-]+\.(?:jpg|jpeg|png|webp)', content)
        if video_urls:
            target_url = video_urls[0]
            ext = ".mp4"
            media_type = "video"
        elif image_urls:
            target_url = image_urls[0]
            ext = os.path.splitext(target_url)[1].lower() or ".jpg"
            media_type = "photo"
        else:
            raise DownloadError("No media found on Pinterest pin.")

    file_id = f"{uuid.uuid4()}{ext}"
    filepath = os.path.join(output_dir, file_id)

    try:
        await download_file_aiohttp(target_url, filepath, headers)
    except Exception as e:
        if og_image and target_url != og_image:
            await download_file_aiohttp(og_image, filepath, headers)
        else:
            raise DownloadError(f"Failed to download media file: {e}")

    if media_type == "video":
        check_file_size_limit(filepath)

    return MediaInfo(
        filepath=filepath,
        title=title[:100],
        duration=None,
        uploader="Pinterest",
        width=None,
        height=None,
        media_type=media_type
    )

async def _tiktok_fallback_download(url: str, output_dir: str, format_type: str = "video") -> MediaInfo:
    """Fallback downloader for TikTok posts (photos, videos, audio) via TikWM API with auto-retry."""
    clean_url = html.unescape(url).strip()
    api_url = f"https://tikwm.com/api/?url={urllib.parse.quote(clean_url)}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    data = None
    last_msg = ""
    timeout = aiohttp.ClientTimeout(total=60, sock_read=45)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt in range(3):
            try:
                async with session.get(api_url, headers=headers) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                if data and data.get("code") == 0 and data.get("data"):
                    break
                last_msg = data.get("msg", "API limit reached") if data else "No response"
                if attempt < 2:
                    await asyncio.sleep(1.3)
            except Exception as e:
                last_msg = str(e)
                if attempt < 2:
                    await asyncio.sleep(1.3)

    if not data or data.get("code") != 0 or not data.get("data"):
        raise DownloadError(f"TikTok fallback error: {last_msg}")

    p_data = data["data"]
    title = p_data.get("title", "TikTok Post")
    uploader = p_data.get("author", {}).get("nickname") or p_data.get("author", {}).get("unique_id") or "TikTok User"
    images = p_data.get("images", [])

    if format_type == "audio":
        audio_url = p_data.get("music") or p_data.get("play")
        if not audio_url:
            raise DownloadError("No audio track found in TikTok post.")
        file_id = f"{uuid.uuid4()}.mp3"
        filepath = os.path.join(output_dir, file_id)
        await download_file_aiohttp(audio_url, filepath, headers)
        return MediaInfo(filepath=filepath, title=title[:100], duration=p_data.get("duration"), uploader=uploader, width=None, height=None, media_type="audio")

    # If post contains photo carousel / images
    if images:
        downloaded_paths = []
        for i, img_url in enumerate(images):
            file_id = f"{uuid.uuid4()}_{i}.jpg"
            filepath = os.path.join(output_dir, file_id)
            await download_file_aiohttp(img_url, filepath, headers)
            downloaded_paths.append(filepath)

        if len(downloaded_paths) == 1:
            return MediaInfo(filepath=downloaded_paths[0], title=title[:100], duration=None, uploader=uploader, width=None, height=None, media_type="photo")
        else:
            return MediaInfo(filepath=None, title=title[:100], duration=None, uploader=uploader, width=None, height=None, media_type="carousel", image_paths=downloaded_paths)

    # Video post download
    video_url = p_data.get("play") or p_data.get("wmplay")
    if not video_url:
        raise DownloadError("No video URL found in TikTok post.")

    file_id = f"{uuid.uuid4()}.mp4"
    filepath = os.path.join(output_dir, file_id)
    await download_file_aiohttp(video_url, filepath, headers)

    # Verify audio stream exists; if not, merge music track
    if not _has_audio_stream(filepath):
        music_url = p_data.get("music")
        if music_url:
            logger.info("Video has no audio stream. Merging music track...")
            filepath = await _merge_audio_into_video(filepath, music_url, output_dir, headers)
        else:
            logger.warning("Video has no audio and no music URL available.")

    check_file_size_limit(filepath)

    return MediaInfo(filepath=filepath, title=title[:100], duration=p_data.get("duration"), uploader=uploader, width=None, height=None, media_type="video")

def _yt_dlp_download(url: str, output_dir: str, format_type: str, progress_hook: Optional[Callable[[dict], None]] = None) -> MediaInfo:
    file_id = str(uuid.uuid4())
    output_template = os.path.join(output_dir, f"{file_id}.%(ext)s")

    if format_type == "audio":
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'geo_bypass': True,
            'nocheckcertificate': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
    else:
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'geo_bypass': True,
            'nocheckcertificate': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
        
    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except Exception as e:
            raise DownloadError(f"Download error: {str(e)}")

        if not info:
            raise DownloadError("Could not retrieve media info.")

        # Check for photo carousels / multi-item entries
        entries = info.get('entries')
        if entries:
            image_paths = []
            video_paths = []
            for entry in entries:
                if entry and 'requested_downloads' in entry:
                    for req in entry['requested_downloads']:
                        fp = req.get('filepath')
                        if fp and os.path.exists(fp):
                            ext = os.path.splitext(fp)[1].lower()
                            if ext in IMAGE_EXTENSIONS:
                                image_paths.append(fp)
                            else:
                                video_paths.append(fp)

            if not image_paths and not video_paths:
                for f in os.listdir(output_dir):
                    fp = os.path.join(output_dir, f)
                    ext = os.path.splitext(f)[1].lower()
                    if ext in IMAGE_EXTENSIONS:
                        image_paths.append(fp)
                    else:
                        video_paths.append(fp)

            if len(image_paths) > 1:
                return MediaInfo(
                    filepath=None,
                    title=info.get('title', 'Photo Carousel'),
                    duration=None,
                    uploader=info.get('uploader'),
                    width=None,
                    height=None,
                    media_type="carousel",
                    image_paths=image_paths
                )
            elif len(image_paths) == 1:
                return MediaInfo(
                    filepath=image_paths[0],
                    title=info.get('title', 'Photo'),
                    duration=None,
                    uploader=info.get('uploader'),
                    width=None,
                    height=None,
                    media_type="photo"
                )

        # Single item download search
        downloaded_file = None
        if 'requested_downloads' in info and info['requested_downloads']:
            downloaded_file = info['requested_downloads'][0].get('filepath')
        
        if not downloaded_file or not os.path.exists(downloaded_file):
            for f in os.listdir(output_dir):
                if f.startswith(file_id):
                    downloaded_file = os.path.join(output_dir, f)
                    break

        if not downloaded_file or not os.path.exists(downloaded_file):
            all_files = [os.path.join(output_dir, f) for f in os.listdir(output_dir)]
            if all_files:
                downloaded_file = all_files[0]

        if not downloaded_file or not os.path.exists(downloaded_file):
            raise DownloadError("Downloaded file was not found on disk.")

        ext = os.path.splitext(downloaded_file)[1].lower()
        if format_type == "audio":
            actual_media_type = "audio"
        elif ext in IMAGE_EXTENSIONS:
            actual_media_type = "photo"
        else:
            actual_media_type = "video"
            check_file_size_limit(downloaded_file)

        return MediaInfo(
            filepath=downloaded_file,
            title=info.get('title', 'No Title'),
            duration=info.get('duration'),
            uploader=info.get('uploader') or info.get('uploader_id'),
            width=info.get('width'),
            height=info.get('height'),
            media_type=actual_media_type
        )

async def download_media(url: str, format_type: str = "video", progress_callback: Optional[Callable[[str], None]] = None) -> MediaInfo:
    """Async wrapper for media downloading (TikTok, Instagram, Pinterest, Twitter/X)."""
    temp_dir = tempfile.mkdtemp(prefix="tg_bot_vids_")
    
    url = html.unescape(url).strip()
    loop = asyncio.get_running_loop()
    
    # Safe wrapper for yt-dlp progress hook to call async callback in the loop
    def yt_dlp_progress_hook(d: dict):
        if not progress_callback:
            return
        
        if d['status'] == 'downloading':
            percent_str = d.get('_percent_str', '').strip()
            if percent_str:
                # Remove ANSI escape codes that yt-dlp sometimes adds
                percent_str = re.sub(r'\x1b\[[0-9;]*m', '', percent_str)
                asyncio.run_coroutine_threadsafe(progress_callback(percent_str), loop)
    
    try:
        # Route TikTok through dedicated API downloader first for complete audio+video streams
        if "tiktok.com" in url.lower() or "vm.tiktok.com" in url.lower() or "vt.tiktok.com" in url.lower():
            try:
                return await _tiktok_fallback_download(url, temp_dir, format_type)
            except Exception as e:
                logger.info(f"TikTok API download failed ({e}), trying yt-dlp...")
                
        elif "instagram.com" in url.lower():
            try:
                return await _instagram_fallback_download(url, temp_dir)
            except Exception as e:
                logger.info(f"Instagram Instaloader failed ({e}), trying yt-dlp...")
                
        elif "pinterest.com" in url.lower() or "pin.it" in url.lower():
            try:
                return await _pinterest_fallback_download(url, temp_dir)
            except Exception as e:
                logger.info(f"Pinterest fallback failed ({e}), trying yt-dlp...")

        # Run yt-dlp in executor for Twitter/X and other platforms
        return await loop.run_in_executor(None, _yt_dlp_download, url, temp_dir, format_type, yt_dlp_progress_hook)
        
    except Exception:
        # Cleanup on failure
        if os.path.exists(temp_dir):
            for f in os.listdir(temp_dir):
                try:
                    os.remove(os.path.join(temp_dir, f))
                except OSError:
                    pass
            try:
                os.rmdir(temp_dir)
            except OSError:
                pass
        raise

def cleanup_media(media_info: MediaInfo):
    """Remove local temp files and folder."""
    try:
        paths = []
        if media_info.filepath:
            paths.append(media_info.filepath)
        paths.extend(media_info.image_paths)

        parent_dir = None
        for p in paths:
            if os.path.exists(p):
                parent_dir = os.path.dirname(p)
                os.remove(p)

        if parent_dir and os.path.exists(parent_dir) and "tg_bot_vids_" in parent_dir:
            for f in os.listdir(parent_dir):
                try:
                    os.remove(os.path.join(parent_dir, f))
                except OSError:
                    pass
            os.rmdir(parent_dir)
    except Exception as e:
        logger.warning(f"Error during cleanup: {e}")
