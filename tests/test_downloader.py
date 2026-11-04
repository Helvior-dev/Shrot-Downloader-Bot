"""Offline checks for access control, download limits and Telegram albums."""

import asyncio
import importlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import InputMediaPhoto, InputMediaVideo

import downloader
from handlers.commands import command_help_handler, command_start_handler
from handlers.downloader import process_video_link, send_carousel
from utils.auth import is_user_allowed, is_user_admin


TEST_TEMP_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "opencode"


class FakeResponse:
    def __init__(self, chunks, content_length=None, status=200, headers=None, body="", json_data=None):
        self.content_length = content_length
        self.chunks = chunks
        self.content = self
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.json_data = json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def raise_for_status(self):
        pass

    async def text(self):
        return self.body

    async def json(self):
        return self.json_data

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class FakeSession:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.calls = 0
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def get(self, url, **_kwargs):
        self.calls += 1
        self.urls.append(url)
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


class AccessTests(unittest.TestCase):
    def test_whitelist_fails_closed_and_accepts_ids(self):
        user = SimpleNamespace(id=123, username="friend")
        with patch.dict(os.environ, {"ALLOWED_USERS": ""}):
            self.assertFalse(is_user_allowed(user))
            self.assertFalse(is_user_allowed(None))
        with patch.dict(os.environ, {"ALLOWED_USERS": "456, 123"}):
            self.assertTrue(is_user_allowed(user))
        with patch.dict(os.environ, {"ALLOWED_USERS": "@friend"}):
            self.assertTrue(is_user_allowed(user))
        with patch.dict(os.environ, {"ADMIN_USERS": ""}):
            self.assertFalse(is_user_admin(user))

    def test_only_supported_hosts_are_accepted(self):
        for url in ("https://vm.tiktok.com/abc", "https://www.instagram.com/p/abc",
                    "https://pin.it/abc", "https://www.pinterest.co.uk/pin/1",
                    "https://x.com/user/status/1", "https://t.co/abc"):
            with self.subTest(url=url):
                self.assertTrue(downloader.is_supported_url(url))
        for url in ("https://127.0.0.1/", "http://localhost/", "https://x.com.evil.org/",
                    "https://evil.org/?next=tiktok.com", "https://user:pass@x.com/status/1",
                    "ftp://x.com/file", "https://x.com:8080/status/1"):
            with self.subTest(url=url):
                self.assertFalse(downloader.is_supported_url(url))

    def test_extractor_media_urls_cannot_target_local_hosts(self):
        for url in ("http://127.0.0.1:80/", "http://169.254.169.254/latest/meta-data",
                    "http://[::1]/", "http://metadata.google.internal/", "file:///etc/passwd"):
            with self.subTest(url=url):
                self.assertFalse(downloader.is_public_media_url(url))
        self.assertTrue(downloader.is_public_media_url("https://cdn.example.org/video.mp4"))


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinterest_api_detects_story_video_with_false_video_flag(self):
        pin_id = "607282331046601700"
        payload = {'resource_response': {'status': 'success', 'data': {
            'id': pin_id, 'is_video': False, 'videos': None,
            'story_pin_data': {'pages': [{'blocks': [{'video': {'video_list': {
                'V_HLSV3_MOBILE': {'url': 'https://v1.pinimg.com/story.m3u8'}
            }}}]}]}
        }}}
        with patch.object(downloader.aiohttp, 'ClientSession',
                          return_value=FakeSession(FakeResponse([], json_data=payload))):
            self.assertTrue(await downloader._pinterest_pin_is_video(pin_id))

    async def test_pinterest_api_rejects_unverified_metadata(self):
        with patch.object(downloader.aiohttp, 'ClientSession',
                          return_value=FakeSession(FakeResponse([], json_data={}))):
            with self.assertRaises(downloader.DownloadError):
                await downloader._pinterest_pin_is_video('123')

    async def test_pinterest_preview_is_rejected_when_api_reports_video(self):
        page = '<meta property="og:image" content="https://i.pinimg.com/preview.jpg">'
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder, \
             patch.object(downloader.aiohttp, 'ClientSession',
                          return_value=FakeSession(FakeResponse([], body=page))), \
             patch.object(downloader, '_pinterest_pin_is_video', new_callable=AsyncMock,
                          return_value=True) as check, \
             patch.object(downloader, 'download_file_aiohttp', new_callable=AsyncMock) as download:
            with self.assertRaises(downloader.DownloadError):
                await downloader._pinterest_fallback_download('https://es.pinterest.com/pin/607282331046601700/', folder)
        check.assert_awaited_once_with('607282331046601700')
        download.assert_not_awaited()

    async def test_pinterest_video_uses_yt_dlp_when_page_only_has_preview(self):
        page = '<meta property="og:image" content="https://i.pinimg.com/preview.jpg">'
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder, \
             patch.object(downloader.tempfile, 'gettempdir', return_value=folder), \
             patch.object(downloader.aiohttp, 'ClientSession',
                          return_value=FakeSession(FakeResponse([], body=page))), \
             patch.object(downloader, '_pinterest_pin_is_video', new_callable=AsyncMock,
                          return_value=True), \
             patch.object(downloader, 'download_file_aiohttp', new_callable=AsyncMock) as image_download, \
             patch.object(downloader, '_yt_dlp_download') as yt_download:
            def download_video(_url, output_dir, _format, _hook):
                path = Path(output_dir) / 'video.mp4'
                path.write_bytes(b'video')
                return downloader.MediaInfo(str(path), 'title', None, None, None, None, media_type='video')

            yt_download.side_effect = download_video
            media = await downloader.download_media('https://es.pinterest.com/pin/607282331046601700/')
            self.assertEqual(media.media_type, 'video')
            downloader.cleanup_media(media)
        image_download.assert_not_awaited()
        yt_download.assert_called_once()

    async def test_pinterest_refuses_yt_dlp_thumbnail_for_video_pin(self):
        page = '<meta property="og:image" content="https://i.pinimg.com/preview.jpg">'
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder, \
             patch.object(downloader.tempfile, 'gettempdir', return_value=folder), \
             patch.object(downloader.aiohttp, 'ClientSession',
                          return_value=FakeSession(FakeResponse([], body=page))), \
             patch.object(downloader, '_pinterest_pin_is_video', new_callable=AsyncMock, return_value=True), \
             patch.object(downloader, 'download_file_aiohttp', new_callable=AsyncMock) as image_download, \
             patch.object(downloader, '_yt_dlp_download') as yt_download:
            def download_preview(_url, output_dir, _format, _hook):
                path = Path(output_dir) / 'preview.jpg'
                path.write_bytes(b'photo')
                return downloader.MediaInfo(str(path), 'title', None, None, None, None, media_type='photo')

            yt_download.side_effect = download_preview
            with self.assertRaisesRegex(downloader.DownloadError, 'refusing to send its preview'):
                await downloader.download_media('https://es.pinterest.com/pin/607282331046601700/')
        image_download.assert_not_awaited()
        self.assertEqual(list(Path(folder).iterdir()) if Path(folder).exists() else [], [])

    async def test_pinterest_refuses_photo_when_metadata_is_unavailable(self):
        page = '<meta property="og:image" content="https://i.pinimg.com/preview.jpg">'
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder, \
             patch.object(downloader.tempfile, 'gettempdir', return_value=folder), \
             patch.object(downloader.aiohttp, 'ClientSession',
                          return_value=FakeSession(FakeResponse([], body=page))), \
             patch.object(downloader, '_pinterest_pin_is_video', new_callable=AsyncMock,
                          side_effect=downloader.DownloadError('API unavailable')), \
             patch.object(downloader, 'download_file_aiohttp', new_callable=AsyncMock) as image_download, \
             patch.object(downloader, '_yt_dlp_download') as yt_download:
            def download_preview(_url, output_dir, _format, _hook):
                path = Path(output_dir) / 'preview.jpg'
                path.write_bytes(b'photo')
                return downloader.MediaInfo(str(path), 'title', None, None, None, None, media_type='photo')

            yt_download.side_effect = download_preview
            with self.assertRaisesRegex(downloader.DownloadError, 'pin type could not be verified'):
                await downloader.download_media('https://es.pinterest.com/pin/607282331046601700/')
        image_download.assert_not_awaited()

    async def test_pinterest_story_video_uses_mp4_instead_of_preview(self):
        pin_id = "607282331046601700"
        page = (f'<meta property="og:image" content="https://i.pinimg.com/preview.jpg">'
                f'<script>{{"id":"{pin_id}","storyPinData":{{"pages":[{{"blocks":['
                '{"__typename":"StoryPinVideoBlock","videoDataV2":{'
                '"videoList720P":{"v720P":{"url":"https://v1.pinimg.com/video_720w.mp4"}}'
                '}}]}}]}}</script>')
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder, \
             patch.object(downloader.aiohttp, "ClientSession", return_value=FakeSession(FakeResponse([], body=page))), \
             patch.object(downloader, "download_file_aiohttp", new_callable=AsyncMock) as download:
            result = await downloader._pinterest_fallback_download(
                f"https://es.pinterest.com/pin/{pin_id}/", folder)
        self.assertEqual(result.media_type, "video")
        self.assertEqual(download.await_args.args[0], "https://v1.pinimg.com/video_720w.mp4")

    async def test_pinterest_failed_video_does_not_send_thumbnail(self):
        pin_id = "607282331046601700"
        page = (f'<meta property="og:image" content="https://i.pinimg.com/preview.jpg">'
                f'<script>{{"entityId":"{pin_id}","storyPinData":{{"pages":[{{"blocks":['
                '{"__typename":"StoryPinVideoBlock","videoDataV2":{'
                '"videoList720P":{"v720P":{"url":"https://v1.pinimg.com/video.mp4"}}'
                '}}]}}]}}</script>')
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder, \
             patch.object(downloader.aiohttp, "ClientSession", return_value=FakeSession(FakeResponse([], body=page))), \
             patch.object(downloader, "download_file_aiohttp", new_callable=AsyncMock,
                          side_effect=OSError("video unavailable")) as download:
            with self.assertRaises(downloader.DownloadError):
                await downloader._pinterest_fallback_download(f"https://es.pinterest.com/pin/{pin_id}/", folder)
        download.assert_awaited_once()

    async def test_pinterest_story_without_mp4_never_sends_preview(self):
        pin_id = "607282331046601700"
        page = (f'<meta property="og:image" content="https://i.pinimg.com/preview.jpg">'
                f'<script>{{"id":"{pin_id}","storyPinData":{{"pages":[{{"blocks":['
                '{"__typename":"StoryPinVideoBlock"}]}}]}}</script>')
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder, \
             patch.object(downloader.aiohttp, "ClientSession", return_value=FakeSession(FakeResponse([], body=page))), \
             patch.object(downloader, "download_file_aiohttp", new_callable=AsyncMock) as download:
            with self.assertRaises(downloader.DownloadError):
                await downloader._pinterest_fallback_download(f"https://es.pinterest.com/pin/{pin_id}/", folder)
        download.assert_not_awaited()

    async def test_pinterest_video_pin_without_story_data_never_sends_preview(self):
        page = ('<meta property="og:image" content="https://i.pinimg.com/preview.jpg">'
                '<script>{"id":"123","is_video":true,"videos":{"video_list":{}}}</script>')
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder, \
             patch.object(downloader.aiohttp, "ClientSession", return_value=FakeSession(FakeResponse([], body=page))), \
             patch.object(downloader, "download_file_aiohttp", new_callable=AsyncMock) as download:
            with self.assertRaises(downloader.DownloadError):
                await downloader._pinterest_fallback_download("https://www.pinterest.com/pin/123/", folder)
        download.assert_not_awaited()

    async def test_pinterest_photo_still_uses_image(self):
        page = '<meta property="og:image" content="https://i.pinimg.com/photo.jpg">'
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder, \
             patch.object(downloader.aiohttp, "ClientSession", return_value=FakeSession(FakeResponse([], body=page))), \
             patch.object(downloader, '_pinterest_pin_is_video', new_callable=AsyncMock, return_value=False), \
             patch.object(downloader, "download_file_aiohttp", new_callable=AsyncMock) as download:
            result = await downloader._pinterest_fallback_download("https://www.pinterest.com/pin/123/", folder)
        self.assertEqual(result.media_type, "photo")
        self.assertEqual(download.await_args.args[0], "https://i.pinimg.com/photo.jpg")

    async def test_start_and_help_messages_use_plain_text(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=7, username="friend", first_name="Alex"),
                                  answer=AsyncMock())
        with patch("handlers.commands.is_user_allowed", return_value=True):
            await command_start_handler(message)
            start_text = message.answer.call_args.args[0]
            await command_help_handler(message)
            help_text = message.answer.call_args.args[0]
        self.assertIn("Send a TikTok, Instagram, Pinterest, or X link", start_text)
        self.assertIn("Video/audio limit: 50 MB; photo limit: 10 MB", help_text)

    async def test_handler_rejects_unsupported_link_before_download(self):
        message = SimpleNamespace(text="https://127.0.0.1/private", from_user=SimpleNamespace(id=7),
                                  answer=AsyncMock())
        with patch("handlers.downloader.is_user_allowed", return_value=True), \
             patch("handlers.downloader.download_media", new_callable=AsyncMock) as download:
            await process_video_link(message, AsyncMock())
        message.answer.assert_awaited_once()
        download.assert_not_awaited()

    async def test_startup_refuses_empty_whitelist(self):
        with patch.dict(os.environ, {"BOT_TOKEN": "test-token", "ALLOWED_USERS": ""}), \
             patch("dotenv.load_dotenv", return_value=False):
            bot_module = importlib.import_module("bot")
            with patch.object(bot_module, "BOT_TOKEN", "test-token"):
                with self.assertRaisesRegex(RuntimeError, "ALLOWED_USERS is empty"):
                    await bot_module.main()

    async def test_stale_cleanup_skips_other_and_recent_directories(self):
        bot_module = importlib.import_module("bot")
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as parent:
            stale = Path(parent) / "tg_bot_vids_stale"
            recent = Path(parent) / "tg_bot_vids_recent"
            other = Path(parent) / "tg_bot_vids_other"
            for folder in (stale, recent, other):
                folder.mkdir()
            for folder in (stale, recent):
                (folder / downloader.OWNER_MARKER).touch()
            age = time.time() - 90000
            os.utime(stale, (age, age))
            os.utime(other, (age, age))
            with patch.object(bot_module.tempfile, "gettempdir", return_value=parent), \
                 patch.object(bot_module.shutil, "rmtree") as remove, \
                 patch.object(bot_module.asyncio, "sleep", new_callable=AsyncMock,
                              side_effect=asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await bot_module.clean_temp_directories_task()
            remove.assert_called_once_with(str(stale))

    async def test_aiohttp_rejects_oversized_content_length_and_stream(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder:
            path = str(Path(folder) / "photo.jpg")
            for response in (FakeResponse([], content_length=9), FakeResponse([b"12345", b"6789"])):
                with self.subTest(response=response), patch.object(downloader, "MAX_PHOTO_SIZE", 8), \
                     patch.object(downloader.aiohttp, "ClientSession", return_value=FakeSession(response)), \
                     patch.object(downloader, "PROXY_URL", None):
                    with self.assertRaises(downloader.FileTooLargeError):
                        await downloader.download_file_aiohttp("https://example.com/image", path, {})
                    self.assertLessEqual(Path(path).stat().st_size if Path(path).exists() else 0, 8)

    async def test_aiohttp_respects_total_request_budget(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder:
            (Path(folder) / "first.jpg").write_bytes(b"1" * 15)
            path = str(Path(folder) / "second.jpg")
            response = FakeResponse([b"123456"], content_length=6)
            with patch.object(downloader, "MAX_REQUEST_SIZE", 20), \
                 patch.object(downloader.aiohttp, "ClientSession", return_value=FakeSession(response)), \
                 patch.object(downloader, "PROXY_URL", None):
                with self.assertRaises(downloader.FileTooLargeError):
                    await downloader.download_file_aiohttp("https://example.com/image", path, {})
            self.assertFalse(Path(path).exists())

    async def test_aiohttp_rejects_redirect_to_local_address_before_request(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder:
            response = FakeResponse([], status=302, headers={"Location": "http://127.0.0.1/private"})
            session = FakeSession(response)
            with patch.object(downloader.aiohttp, "ClientSession", return_value=session), \
                 patch.object(downloader, "PROXY_URL", None):
                with self.assertRaises(downloader.DownloadError):
                    await downloader.download_file_aiohttp("https://cdn.example.org/video.mp4",
                                                           str(Path(folder) / "video.mp4"), {})
            self.assertFalse((Path(folder) / "video.mp4").exists())
            self.assertEqual(session.calls, 1)

    async def test_aiohttp_follows_public_redirect_and_saves_file(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder:
            session = FakeSession([
                FakeResponse([], status=302, headers={"Location": "/media.mp4"}),
                FakeResponse([b"video"], content_length=5),
            ])
            path = str(Path(folder) / "video.mp4")
            with patch.object(downloader.aiohttp, "ClientSession", return_value=session), \
                 patch.object(downloader, "PROXY_URL", None):
                await downloader.download_file_aiohttp("https://cdn.example.org/redirect", path, {})
            self.assertEqual(Path(path).read_bytes(), b"video")
            self.assertEqual(session.urls, ["https://cdn.example.org/redirect", "https://cdn.example.org/media.mp4"])

    async def test_short_link_rejects_redirect_to_unsupported_host(self):
        response = FakeResponse([], status=302, headers={"Location": "http://127.0.0.1/private"})
        session = FakeSession(response)
        with patch.object(downloader.aiohttp, "ClientSession", return_value=session), \
             patch.object(downloader, "PROXY_URL", None):
            with self.assertRaises(downloader.DownloadError):
                await downloader._expand_short_url("https://vm.tiktok.com/abc")
        self.assertEqual(session.calls, 1)

    async def test_success_cleanup_removes_only_own_request(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as parent:
            other = Path(parent) / "tg_bot_vids_unrelated"
            other.mkdir()
            (other / "keep.txt").write_text("keep")

            def fake_download(_url, output_dir, _format, _hook):
                path = Path(output_dir) / "video.mp4"
                path.write_bytes(b"video")
                return downloader.MediaInfo(str(path), "title", None, None, None, None)

            with patch.object(downloader.tempfile, "gettempdir", return_value=parent), \
                 patch.object(downloader, "_yt_dlp_download", side_effect=fake_download):
                media = await downloader.download_media("https://x.com/user/status/1")
                self.assertTrue(Path(media.temp_dir).exists())
                downloader.cleanup_media(media)
            self.assertFalse(Path(media.temp_dir).exists())
            self.assertTrue((other / "keep.txt").exists())

    async def test_oversized_fallback_cleans_temp_and_does_not_retry(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as parent:
            with patch.object(downloader.tempfile, "gettempdir", return_value=parent), \
                 patch.object(downloader, "_tiktok_fallback_download", new_callable=AsyncMock,
                              side_effect=downloader.FileTooLargeError("File exceeds the Telegram upload limit.")), \
                 patch.object(downloader, "_yt_dlp_download") as fallback:
                with self.assertRaises(downloader.FileTooLargeError):
                    await downloader.download_media("https://www.tiktok.com/@user/video/1")
            fallback.assert_not_called()
            self.assertEqual(list(Path(parent).iterdir()), [])

    async def test_cancelled_download_cleans_temp(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as parent:
            with patch.object(downloader.tempfile, "gettempdir", return_value=parent), \
                 patch.object(downloader, "_tiktok_fallback_download", new_callable=AsyncMock,
                              side_effect=asyncio.CancelledError):
                with self.assertRaises(asyncio.CancelledError):
                    await downloader.download_media("https://www.tiktok.com/@user/video/1")
            self.assertEqual(list(Path(parent).iterdir()), [])

    async def test_yt_dlp_result_is_checked_before_upload(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as parent:
            def fake_download(_url, output_dir, _format, _hook):
                path = Path(output_dir) / "video.mp4"
                path.write_bytes(b"video1234")
                return downloader.MediaInfo(str(path), "title", None, None, None, None)

            with patch.object(downloader.tempfile, "gettempdir", return_value=parent), \
                 patch.object(downloader, "MAX_FILE_SIZE", 8), \
                 patch.object(downloader, "_yt_dlp_download", side_effect=fake_download):
                with self.assertRaises(downloader.FileTooLargeError):
                    await downloader.download_media("https://x.com/user/status/1")
            self.assertEqual(list(Path(parent).iterdir()), [])

    async def test_yt_dlp_progress_stops_oversized_transfer(self):
        class FakeYoutubeDL:
            def __init__(self, opts):
                self.hook = opts["progress_hooks"][0]

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def extract_info(self, *_args, **_kwargs):
                self.hook({"status": "downloading", "downloaded_bytes": 9, "filename": "video.mp4"})

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as parent:
            with patch.object(downloader.tempfile, "gettempdir", return_value=parent), \
                 patch.object(downloader, "MAX_FILE_SIZE", 8), \
                 patch.object(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL):
                with self.assertRaises(downloader.FileTooLargeError):
                    await downloader.download_media("https://x.com/user/status/1")
            self.assertEqual(list(Path(parent).iterdir()), [])

    async def test_mixed_carousel_is_split_and_sent_completely(self):
        message = SimpleNamespace(answer_media_group=AsyncMock(), answer_video=AsyncMock(), answer_photo=AsyncMock())
        paths = [f"{i}.jpg" for i in range(10)] + ["last.mp4"]
        media = downloader.MediaInfo(None, "album", None, None, None, None, media_type="carousel", media_paths=paths)
        await send_carousel(message, media)
        group = message.answer_media_group.call_args.kwargs["media"]
        self.assertEqual(len(group), 10)
        self.assertIsInstance(group[0], InputMediaPhoto)
        self.assertIsNone(group[0].caption)
        message.answer_video.assert_awaited_once()
        self.assertEqual(message.answer_video.call_args.kwargs["video"].path, "last.mp4")

        message.answer_media_group.reset_mock()
        message.answer_video.reset_mock()
        media.media_paths = ["first.jpg", "second.mp4"]
        await send_carousel(message, media)
        group = message.answer_media_group.call_args.kwargs["media"]
        self.assertIsInstance(group[1], InputMediaVideo)
        self.assertIsNone(group[1].caption)

    async def test_successful_upload_sends_only_media(self):
        for media_type, extension, method in (("video", "mp4", "answer_video"),
                                              ("photo", "jpg", "answer_photo"),
                                              ("audio", "mp3", "answer_audio")):
            with self.subTest(media_type=media_type):
                progress = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
                message = SimpleNamespace(
                    text="https://x.com/user/status/1", from_user=SimpleNamespace(id=71),
                    chat=SimpleNamespace(id=71), answer=AsyncMock(return_value=progress),
                    answer_video=AsyncMock(), answer_photo=AsyncMock(), answer_audio=AsyncMock(),
                )
                media = downloader.MediaInfo(f"media.{extension}", "Title", 8.5, "TikTok User", None, None,
                                             media_type=media_type)
                with patch.dict(os.environ, {"LOADING_STICKER_ID": ""}), \
                     patch("handlers.downloader.is_user_allowed", return_value=True), \
                     patch("handlers.downloader.download_media", new_callable=AsyncMock, return_value=media), \
                     patch("handlers.downloader.cleanup_media"):
                    await process_video_link(message, SimpleNamespace(send_chat_action=AsyncMock()))
                self.assertEqual(message.answer.await_count, 1)
                kwargs = getattr(message, method).call_args.kwargs
                self.assertNotIn("caption", kwargs)
                self.assertNotIn("performer", kwargs)
                self.assertNotIn("title", kwargs)
                self.assertNotIn("duration", kwargs)
                progress.delete.assert_awaited_once()


class YtDlpTests(unittest.TestCase):
    def test_yt_dlp_enforces_file_limit_and_validates_tls(self):
        options = {}

        class FakeYoutubeDL:
            def __init__(self, opts):
                options.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def extract_info(self, *_args, **_kwargs):
                return None

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder, \
             patch.object(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            with self.assertRaises(downloader.DownloadError):
                downloader._yt_dlp_download("https://x.com/user/status/1", folder, "video")
        self.assertEqual(options["max_filesize"], downloader.MAX_FILE_SIZE)
        self.assertNotIn("nocheckcertificate", options)

    def test_yt_dlp_does_not_send_ownership_marker_as_video(self):
        class FakeYoutubeDL:
            def __init__(self, _opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def extract_info(self, *_args, **_kwargs):
                return {"title": "Unavailable"}

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_DIR) as folder, \
             patch.object(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            (Path(folder) / downloader.OWNER_MARKER).touch()
            with self.assertRaises(downloader.DownloadError):
                downloader._yt_dlp_download("https://x.com/user/status/1", folder, "video")


if __name__ == "__main__":
    unittest.main()
