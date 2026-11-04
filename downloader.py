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
import shutil
import ipaddress
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Any
import yt_dlp
import aiohttp
import instaloader

logger = logging.getLogger(__name__)

# Use decimal MB to stay below Telegram's 50 MB / 10 MB upload limits.
MAX_FILE_SIZE = 50_000_000
MAX_PHOTO_SIZE = 10_000_000
MAX_REQUEST_SIZE = 200_000_000
MAX_CAROUSEL_ITEMS = 20
OWNER_MARKER = ".shrot_downloader_bot"
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
PROXY_URL = os.getenv("PROXY_URL") or os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
SUPPORTED_DOMAINS = (
    "tiktok.com", "instagram.com", "pinterest.com", "pinterest.co.uk",
    "pinterest.de", "pinterest.fr", "pinterest.it", "pin.it", "x.com", "twitter.com", "t.co",
)

def is_supported_url(url: str) -> bool:
    """Accept only HTTP(S) links to the supported social networks."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        return (
            parsed.scheme.lower() in ("http", "https")
            and not parsed.username and not parsed.password
            and parsed.port in (None, 80, 443)
            and any(host == domain or host.endswith("." + domain) for domain in SUPPORTED_DOMAINS)
        )
    except ValueError:
        return False

def is_public_media_url(url: str) -> bool:
    """Reject local addresses in media URLs supplied by extractors or redirects."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ''
        if (parsed.scheme.lower() not in ('http', 'https') or not host
                or parsed.username or parsed.password or parsed.port not in (None, 80, 443)
                or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal'))):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False

@dataclass
class MediaInfo:
    filepath: Optional[str]
    title: str
    duration: Optional[int]
    uploader: Optional[str]
    width: Optional[int]
    height: Optional[int]
    media_type: str = "video"  # "video", "audio", "photo", "carousel"
    media_paths: List[str] = field(default_factory=list)
    temp_dir: Optional[str] = None

class DownloadError(Exception):
    pass

class FileTooLargeError(DownloadError):
    pass

def check_file_size_limit(filepath: str):
    """Check a downloaded file against the Telegram upload limit for its type."""
    if os.path.exists(filepath):
        file_size = os.path.getsize(filepath)
        limit = MAX_PHOTO_SIZE if os.path.splitext(filepath)[1].lower() in IMAGE_EXTENSIONS else MAX_FILE_SIZE
        if file_size > limit:
            raise FileTooLargeError(f"File exceeds the Telegram upload limit ({limit // 1_000_000} MB).")

def _check_request_size(output_dir: str):
    size = sum(entry.stat().st_size for entry in os.scandir(output_dir) if entry.is_file())
    if size > MAX_REQUEST_SIZE:
        raise FileTooLargeError("Download exceeds the 200 MB per-request disk limit.")
    return size

def _downloaded_files(output_dir: str) -> List[str]:
    return [os.path.join(output_dir, name) for name in os.listdir(output_dir)
            if name != OWNER_MARKER and not name.endswith('.part')
            and os.path.isfile(os.path.join(output_dir, name))]

def extract_instagram_shortcode(url: str) -> Optional[str]:
    """Extract Instagram shortcode from post/reel/tv URL."""
    match = re.search(r'/(?:p|reel|reels|tv|share)/([^/?#&]+)', url)
    return match.group(1) if match else None

async def download_file_aiohttp(url: str, filepath: str, headers: dict, proxy: Optional[str] = None) -> None:
    """Download binary file with aiohttp using optional proxy."""
    target_proxy = proxy or PROXY_URL
    output_dir = os.path.dirname(filepath)
    used_bytes = _check_request_size(output_dir)
    extension = os.path.splitext(filepath)[1].lower()
    file_limit = MAX_PHOTO_SIZE if extension in IMAGE_EXTENSIONS else MAX_FILE_SIZE
    remaining = min(file_limit, MAX_REQUEST_SIZE - used_bytes)
    if remaining <= 0:
        raise FileTooLargeError("Download exceeds the per-request disk limit.")
    timeout = aiohttp.ClientTimeout(total=120, sock_read=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for _ in range(6):
            if not is_public_media_url(url):
                raise DownloadError("Media URL points to a local or unsupported address.")
            async with session.get(url, headers=headers, proxy=target_proxy, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    url = urllib.parse.urljoin(url, resp.headers.get('Location', ''))
                    continue
                resp.raise_for_status()
                if resp.content_length is not None and resp.content_length > remaining:
                    raise FileTooLargeError("File exceeds the Telegram upload or per-request disk limit.")
                with open(filepath, 'wb') as f:
                    downloaded = 0
                    async for chunk in resp.content.iter_chunked(8192):
                        downloaded += len(chunk)
                        if downloaded > remaining:
                            raise FileTooLargeError("File exceeds the Telegram upload or per-request disk limit.")
                        f.write(chunk)
                return
        raise DownloadError("Too many media URL redirects.")

def _has_audio_stream(filepath: str) -> bool:
    """Check if a video file contains an audio stream using ffprobe."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', filepath],
            capture_output=True, text=True, timeout=10
        )
        return 'audio' in result.stdout
    except Exception:
        return True  # Assume audio exists if ffprobe fails or is missing

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
            check_file_size_limit(merged_path)
            _check_request_size(output_dir)
            os.remove(video_path)
            os.remove(audio_path)
            logger.info(f"Successfully merged audio into video: {os.path.getsize(merged_path) / 1024 / 1024:.2f} MB")
            return merged_path
    except FileTooLargeError:
        raise
    except Exception as e:
        logger.warning(f"Audio merge failed: {e}")
        for f in [audio_path, merged_path]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
    
    return video_path  # Return original if merge fails

async def _expand_short_url(url: str) -> str:
    """Follow HTTP redirects for shortened or mobile share links (e.g. vm.tiktok.com, vt.tiktok.com, /t/, pin.it)."""
    clean_url = html.unescape(url).strip()
    if not any(domain in clean_url.lower() for domain in ["vm.tiktok.com", "vt.tiktok.com", "tiktok.com/t/", "pin.it", "t.co", "bit.ly"]):
        return clean_url
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    timeout = aiohttp.ClientTimeout(total=8)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            current_url = clean_url
            for _ in range(6):
                if not is_supported_url(current_url):
                    raise DownloadError("Short link redirects to an unsupported site.")
                async with session.get(current_url, allow_redirects=False, proxy=PROXY_URL) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        current_url = urllib.parse.urljoin(current_url, resp.headers.get('Location', ''))
                        continue
                    resp.raise_for_status()
                    return current_url
            raise DownloadError("Too many short-link redirects.")
    except DownloadError:
        raise
    except Exception as e:
        logger.debug(f"Failed to expand short URL {clean_url}: {e}")
    return clean_url

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
    if len(items) > MAX_CAROUSEL_ITEMS:
        raise FileTooLargeError("Carousel has more than 20 items.")

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
            media_paths=img_paths
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
            async with session.get(url, headers=headers, proxy=PROXY_URL, allow_redirects=False) as resp:
                resp.raise_for_status()
                if resp.status != 200:
                    raise DownloadError("Pinterest returned a redirect or an unexpected response.")
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
    except FileTooLargeError:
        raise
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

async def _tiktok_tikwm_download(url: str, output_dir: str, format_type: str = "video") -> Optional[MediaInfo]:
    """Download TikTok content via multi-endpoint TikWM API."""
    clean_url = html.unescape(url).strip()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Referer': 'https://www.tikwm.com/',
        'Origin': 'https://www.tikwm.com',
    }

    endpoints = [
        ("POST", "https://www.tikwm.com/api/", {"url": clean_url, "hd": "1"}),
        ("POST", "https://tikwm.com/api/", {"url": clean_url, "hd": "1"}),
        ("GET", f"https://tikwm.com/api/?url={urllib.parse.quote(clean_url)}&hd=1", None),
    ]

    data = None
    timeout = aiohttp.ClientTimeout(total=25, sock_read=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for method, endpoint_url, payload in endpoints:
            try:
                if method == "POST":
                    async with session.post(endpoint_url, data=payload, headers=headers, proxy=PROXY_URL) as resp:
                        if resp.status == 200:
                            res_json = await resp.json()
                            if res_json and res_json.get("code") == 0 and res_json.get("data"):
                                data = res_json
                                break
                else:
                    async with session.get(endpoint_url, headers=headers, proxy=PROXY_URL) as resp:
                        if resp.status == 200:
                            res_json = await resp.json()
                            if res_json and res_json.get("code") == 0 and res_json.get("data"):
                                data = res_json
                                break
            except Exception:
                continue

    if not data or data.get("code") != 0 or not data.get("data"):
        return None

    p_data = data["data"]
    title = p_data.get("title", "TikTok Post")
    uploader = p_data.get("author", {}).get("nickname") or p_data.get("author", {}).get("unique_id") or "TikTok User"
    images = p_data.get("images", [])
    if len(images) > MAX_CAROUSEL_ITEMS:
        raise FileTooLargeError("Carousel has more than 20 items.")

    download_headers = {
        'User-Agent': headers['User-Agent'],
        'Referer': 'https://www.tiktok.com/'
    }

    if format_type == "audio":
        audio_url = p_data.get("music") or p_data.get("play")
        if not audio_url:
            return None
        file_id = f"{uuid.uuid4()}.mp3"
        filepath = os.path.join(output_dir, file_id)
        await download_file_aiohttp(audio_url, filepath, download_headers)
        return MediaInfo(filepath=filepath, title=title[:100], duration=p_data.get("duration"), uploader=uploader, width=None, height=None, media_type="audio")

    # If post contains photo carousel / images
    if images:
        downloaded_paths = []
        for i, img_url in enumerate(images):
            file_id = f"{uuid.uuid4()}_{i}.jpg"
            filepath = os.path.join(output_dir, file_id)
            await download_file_aiohttp(img_url, filepath, download_headers)
            downloaded_paths.append(filepath)

        if len(downloaded_paths) == 1:
            return MediaInfo(filepath=downloaded_paths[0], title=title[:100], duration=None, uploader=uploader, width=None, height=None, media_type="photo")
        else:
            return MediaInfo(filepath=None, title=title[:100], duration=None, uploader=uploader, width=None, height=None, media_type="carousel", media_paths=downloaded_paths)

    # Video post download (prefer HD video if available)
    video_url = p_data.get("hdplay") or p_data.get("play") or p_data.get("wmplay")
    if not video_url:
        return None

    file_id = f"{uuid.uuid4()}.mp4"
    filepath = os.path.join(output_dir, file_id)
    await download_file_aiohttp(video_url, filepath, download_headers)

    # Verify audio stream exists; if silent/missing, merge music track
    if not _has_audio_stream(filepath):
        music_url = p_data.get("music")
        if music_url:
            logger.info("Video has no audio stream. Merging music track...")
            filepath = await _merge_audio_into_video(filepath, music_url, output_dir, download_headers)

    check_file_size_limit(filepath)
    return MediaInfo(filepath=filepath, title=title[:100], duration=p_data.get("duration"), uploader=uploader, width=None, height=None, media_type="video")

async def _tiktok_ssstik_download(url: str, output_dir: str, format_type: str = "video") -> Optional[MediaInfo]:
    """Secondary fallback downloader for TikTok via SSSTik."""
    clean_url = html.unescape(url).strip()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
    }
    timeout = aiohttp.ClientTimeout(total=25, sock_read=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get("https://ssstik.io/en", headers=headers, proxy=PROXY_URL) as r:
                if r.status != 200:
                    return None
                html_text = await r.text()
                tt_match = re.search(r'data-hx-vals=["\']\{[^}]*tt:\s*[\'"]([^\'"]+)[\'"]', html_text) or re.search(r'"tt":"([^"]+)"', html_text)
                tt_val = tt_match.group(1) if tt_match else "0"

                post_headers = headers.copy()
                post_headers['HX-Request'] = 'true'
                post_headers['HX-Trigger'] = '_search'
                post_headers['HX-Target'] = 'target'
                post_headers['HX-Current-URL'] = 'https://ssstik.io/en'
                post_headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'

                async with s.post("https://ssstik.io/abc?url=dl", data={"id": clean_url, "locale": "en", "tt": tt_val}, headers=post_headers, proxy=PROXY_URL) as r_post:
                    if r_post.status != 200:
                        return None
                    post_html = await r_post.text()

                title_match = re.search(r'<h2>([^<]+)</h2>', post_html) or re.search(r'<p class="maintext">([^<]+)</p>', post_html)
                title = title_match.group(1).strip() if title_match else "TikTok Post"

                if format_type == "audio":
                    audio_links = re.findall(r'href="(https?://[^"]+/m/[^"]+)"', post_html) or re.findall(r'href="([^"]+)"[^>]*class="[^"]*(?:download_link|pure-button)[^"]*music[^"]*"', post_html)
                    if not audio_links:
                        return None
                    file_id = f"{uuid.uuid4()}.mp3"
                    filepath = os.path.join(output_dir, file_id)
                    await download_file_aiohttp(audio_links[0], filepath, headers)
                    return MediaInfo(filepath=filepath, title=title[:100], duration=None, uploader="TikTok User", width=None, height=None, media_type="audio")

                # Check if video download links exist
                video_links = [l for l in re.findall(r'href="(https?://[^"]+)"[^>]*class="[^"]*(?:download_link|pure-button)[^"]*"', post_html) if "/m/" not in l and "#" not in l]
                if video_links:
                    file_id = f"{uuid.uuid4()}.mp4"
                    filepath = os.path.join(output_dir, file_id)
                    await download_file_aiohttp(video_links[0], filepath, headers)
                    check_file_size_limit(filepath)
                    return MediaInfo(filepath=filepath, title=title[:100], duration=None, uploader="TikTok User", width=None, height=None, media_type="video")

                # Check photo slideshows
                img_links = re.findall(r'href="(https?://[^"]+)"[^>]*class="[^"]*download_link[^"]*"[^>]*download', post_html) or re.findall(r'<img[^>]+src="(https?://[^"]+)"[^>]*class="slide', post_html)
                if img_links:
                    if len(img_links) > MAX_CAROUSEL_ITEMS:
                        raise FileTooLargeError("Carousel has more than 20 items.")
                    downloaded_paths = []
                    for i, img_url in enumerate(img_links):
                        file_id = f"{uuid.uuid4()}_{i}.jpg"
                        filepath = os.path.join(output_dir, file_id)
                        await download_file_aiohttp(img_url, filepath, headers)
                        downloaded_paths.append(filepath)
                    if len(downloaded_paths) == 1:
                        return MediaInfo(filepath=downloaded_paths[0], title=title[:100], duration=None, uploader="TikTok User", width=None, height=None, media_type="photo")
                    return MediaInfo(filepath=None, title=title[:100], duration=None, uploader="TikTok User", width=None, height=None, media_type="carousel", media_paths=downloaded_paths)

    except FileTooLargeError:
        raise
    except Exception as e:
        logger.debug(f"SSSTik fallback error: {e}")
        return None
    return None

async def _tiktok_fallback_download(url: str, output_dir: str, format_type: str = "video") -> MediaInfo:
    """Multi-tiered TikTok downloader: expands URLs and cascades through TikWM and SSSTik."""
    expanded_url = await _expand_short_url(url)
    
    # Tier 1: TikWM API
    try:
        info = await _tiktok_tikwm_download(expanded_url, output_dir, format_type)
        if info:
            return info
    except FileTooLargeError:
        raise
    except Exception as e:
        logger.debug(f"TikWM Tier 1 error: {e}")

    # Tier 2: SSSTik Scraper Gateway
    try:
        info = await _tiktok_ssstik_download(expanded_url, output_dir, format_type)
        if info:
            return info
    except FileTooLargeError:
        raise
    except Exception as e:
        logger.debug(f"SSSTik Tier 2 error: {e}")

    raise DownloadError("All specialized TikTok resolvers failed to retrieve media.")

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
            'max_filesize': MAX_FILE_SIZE,
            'geo_bypass': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
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
            'max_filesize': MAX_FILE_SIZE,
            'geo_bypass': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        }
        
    if PROXY_URL:
        ydl_opts['proxy'] = PROXY_URL
    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except FileTooLargeError:
            raise
        except Exception as e:
            if "Telegram upload limit" in str(e) or "per-request disk limit" in str(e):
                raise FileTooLargeError(str(e)) from e
            raise DownloadError(f"Download error: {str(e)}")

        if not info:
            raise DownloadError("Could not retrieve media info.")

        # Check for photo carousels / multi-item entries
        entries = info.get('entries')
        if entries:
            item_paths = []
            for entry in entries:
                if entry and 'requested_downloads' in entry:
                    for req in entry['requested_downloads']:
                        fp = req.get('filepath')
                        if fp and os.path.exists(fp):
                            item_paths.append(fp)

            if not item_paths:
                item_paths = _downloaded_files(output_dir)

            if len(item_paths) > MAX_CAROUSEL_ITEMS:
                raise FileTooLargeError("Carousel has more than 20 items.")
            if len(item_paths) > 1:
                return MediaInfo(
                    filepath=None,
                    title=info.get('title', 'Photo Carousel'),
                    duration=None,
                    uploader=info.get('uploader'),
                    width=None,
                    height=None,
                    media_type="carousel",
                    media_paths=item_paths
                )
            elif len(item_paths) == 1 and os.path.splitext(item_paths[0])[1].lower() in IMAGE_EXTENSIONS:
                return MediaInfo(
                    filepath=item_paths[0],
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
            for fp in _downloaded_files(output_dir):
                if os.path.basename(fp).startswith(file_id):
                    downloaded_file = fp
                    break

        if not downloaded_file or not os.path.exists(downloaded_file):
            all_files = _downloaded_files(output_dir)
            if all_files:
                downloaded_file = all_files[0]

        if not downloaded_file or not os.path.exists(downloaded_file):
            if (info.get('filesize') or info.get('filesize_approx') or 0) > MAX_FILE_SIZE:
                raise FileTooLargeError("File exceeds the Telegram upload limit (50 MB).")
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
    """Async unified entrypoint for media downloading (TikTok, Instagram, Pinterest, Twitter/X)."""
    if not is_supported_url(url):
        raise DownloadError("Unsupported URL. Send a TikTok, Instagram, Pinterest, or X link.")
    temp_dir = tempfile.mkdtemp(prefix="tg_bot_vids_")
    
    url = html.unescape(url).strip()
    loop = asyncio.get_running_loop()
    
    # Safe wrapper for yt-dlp progress hook to call async callback in the loop
    def yt_dlp_progress_hook(d: dict):
        if d.get('status') == 'downloading':
            filename = d.get('filename', '').removesuffix('.part')
            limit = MAX_PHOTO_SIZE if os.path.splitext(filename)[1].lower() in IMAGE_EXTENSIONS else MAX_FILE_SIZE
            if d.get('downloaded_bytes', 0) > limit:
                raise FileTooLargeError("File exceeds the Telegram upload limit.")
            _check_request_size(temp_dir)
        if progress_callback and d.get('status') == 'downloading':
            percent_str = d.get('_percent_str', '').strip()
            if percent_str:
                percent_str = re.sub(r'\x1b\[[0-9;]*m', '', percent_str)
                asyncio.run_coroutine_threadsafe(progress_callback(percent_str), loop)
    
    try:
        with open(os.path.join(temp_dir, OWNER_MARKER), 'w', encoding='utf-8'):
            pass
        if urllib.parse.urlsplit(url).hostname == 't.co':
            url = await _expand_short_url(url)
        # Route TikTok through dedicated multi-tiered resolvers first
        host = urllib.parse.urlsplit(url).hostname or ''
        if host == 'tiktok.com' or host.endswith('.tiktok.com'):
            try:
                media_info = await _tiktok_fallback_download(url, temp_dir, format_type)
            except FileTooLargeError:
                raise
            except Exception as e:
                logger.info(f"TikTok Fallback resolvers failed ({e}), attempting yt-dlp as final tier...")
                media_info = None
        elif host == 'instagram.com' or host.endswith('.instagram.com'):
            try:
                media_info = await _instagram_fallback_download(url, temp_dir)
            except FileTooLargeError:
                raise
            except Exception as e:
                logger.info(f"Instagram Instaloader failed ({e}), trying yt-dlp...")
                media_info = None
        elif host == 'pin.it' or host.endswith('.pin.it') or any(
            host == domain or host.endswith('.' + domain) for domain in SUPPORTED_DOMAINS if domain.startswith('pinterest.')
        ):
            try:
                expanded_url = await _expand_short_url(url) if host == 'pin.it' or host.endswith('.pin.it') else url
                media_info = await _pinterest_fallback_download(expanded_url, temp_dir)
            except FileTooLargeError:
                raise
            except Exception as e:
                logger.info(f"Pinterest fallback failed ({e}), trying yt-dlp...")
                media_info = None
        else:
            media_info = None

        if media_info is None:
            # Run yt-dlp for X and as a fallback for supported sites.
            media_info = await loop.run_in_executor(None, _yt_dlp_download, url, temp_dir, format_type, yt_dlp_progress_hook)

        paths = [media_info.filepath] if media_info.filepath else media_info.media_paths
        if not paths or len(paths) > MAX_CAROUSEL_ITEMS:
            raise DownloadError("No supported media found or carousel is too large.")
        for path in paths:
            check_file_size_limit(path)
        _check_request_size(temp_dir)
        media_info.temp_dir = temp_dir
        return media_info
        
    except (Exception, asyncio.CancelledError):
        try:
            shutil.rmtree(temp_dir)
        except OSError as e:
            logger.warning(f"Failed to remove temporary download directory: {e}")
        raise

def cleanup_media(media_info: MediaInfo):
    """Remove only the temporary directory created for this request."""
    try:
        directory = media_info.temp_dir
        if directory and os.path.dirname(directory) == tempfile.gettempdir() and os.path.basename(directory).startswith('tg_bot_vids_'):
            shutil.rmtree(directory)
    except Exception as e:
        logger.warning(f"Error during cleanup: {e}")
