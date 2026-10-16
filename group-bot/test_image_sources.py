import asyncio
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

import bot
from emoji_library import EmojiLibrary
from image_memory import ImageMemory
from image_recognition import ImageRecognition
from image_sources import ImageDownloadError, failure_details, validate_source
from sticker_filter import approved


MARKET_URL = 'https://gxh.vip.qq.com/club/item/parcel/item/d1/d10f548c0c67d401b6bb38e6531e11cb/raw300.gif'
FACTS = {'description': '猫耳角色，旁边写着妈妈', 'visible_text': '妈妈',
         'confidence': .95, 'uncertainty': ''}


def gif_bytes():
    data = io.BytesIO()
    Image.new('RGB', (20, 30), 'red').save(data, format='GIF', save_all=True,
        append_images=[Image.new('RGB', (20, 30), 'blue')], duration=100, loop=0)
    return data.getvalue()


class ImageSourceTests(unittest.TestCase):
    def test_market_gif_download_decodes_for_vision_and_hashes_original(self):
        async def run():
            raw = gif_bytes()
            requests = []
            def respond(request):
                requests.append(str(request.url))
                return httpx.Response(200, content=raw)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'vision.jpg'
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    digest = await bot.download_image(client, MARKET_URL, path, {})
                self.assertEqual(requests, [MARKET_URL])
                self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
                with Image.open(path) as image:
                    self.assertEqual((image.format, image.size), ('JPEG', (20, 30)))
        asyncio.run(run())

    def test_source_guards_reject_lookalikes_local_files_credentials_and_unexpected_market_paths(self):
        for url in ['https://gxh.vip.qq.com.evil.test/club/item/parcel/item/a.gif',
                    'https://gxh.vip.qq.com@127.0.0.1/club/item/parcel/item/a.gif',
                    'https://user:secret@gxh.vip.qq.com/club/item/parcel/item/a.gif',
                    'http://gxh.vip.qq.com/club/item/parcel/item/a.gif',
                    'https://gxh.vip.qq.com:9999/club/item/parcel/item/a.gif',
                    'https://gxh.vip.qq.com:invalid/club/item/parcel/item/a.gif',
                    'https://gxh.vip.qq.com/anything-else', 'file:///C:/secret.png',
                    'https://127.0.0.1/image.png', None]:
            with self.subTest(url=url), self.assertRaisesRegex(ImageDownloadError, 'image_source_not_allowed'):
                validate_source(url)
        for url in ['https://gchat.qpic.cn/a', 'https://multimedia.nt.qq.com.cn/download', MARKET_URL]:
            validate_source(url)
        with self.assertRaises(ImageDownloadError):
            validate_source('https://q1.qlogo.cn/g')
        validate_source('https://q1.qlogo.cn/g', allow_avatar=True)

    def test_redirect_and_size_limits_remain_enforced(self):
        async def run():
            for response, cfg, reason in [
                    (httpx.Response(302, headers={'location': 'http://127.0.0.1/private'}), {}, 'image_redirect_not_allowed'),
                    (httpx.Response(200, content=gif_bytes()), {'max_image_bytes': 5}, 'image_download_too_large')]:
                calls = []
                def respond(request):
                    calls.append(request)
                    return response
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / 'image.jpg'
                    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                        with self.assertRaisesRegex(ImageDownloadError, reason):
                            await bot.download_image(client, MARKET_URL, path, cfg)
                    self.assertEqual(len(calls), 1)
                    self.assertFalse(path.exists())
        asyncio.run(run())

    def test_diagnostics_report_reason_without_url_query_or_exception_secrets(self):
        url = 'https://gchat.qpic.cn/private-image?token=do-not-log&key=hidden'
        request = httpx.Request('GET', url)
        response = httpx.Response(403, request=request)
        error = httpx.HTTPStatusError('secret-in-exception', request=request, response=response)
        self.assertEqual(failure_details(error, url), 'reason=image_http_403; source=gchat.qpic.cn')
        self.assertEqual(failure_details(ValueError('secret-in-exception'), url),
            'reason=ValueError; source=gchat.qpic.cn')

    def test_real_market_download_reaches_shared_vision_cache_and_reply_only_once(self):
        async def run():
            requests, vision_calls, replies = [], [], []
            with tempfile.TemporaryDirectory() as folder:
                memory = ImageMemory(Path(folder) / 'cache.json')
                memory.observe('first', ['market-key'])
                def respond(request):
                    requests.append(str(request.url))
                    return httpx.Response(200, content=gif_bytes())
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    async def recognize(url, key):
                        path = Path(folder) / 'shared.jpg'
                        digest = await bot.download_image(client, url, path, {})
                        vision_calls.append(path.read_bytes())
                        memory.store(key, digest, FACTS)
                        return FACTS
                    reader = ImageRecognition(memory, recognize, lambda *args: None)
                    async def generate(cfg, context, trigger, target, images):
                        self.assertFalse(images)
                        replies.append(target['_cached_vision'])
                        return ''
                    with patch.object(bot, 'RUNTIME', Path(folder)), patch.object(bot, 'generate', generate), \
                            patch.object(bot, 'analyze_roles', AsyncMock(return_value={})), \
                            patch.object(bot.EmojiLibrary, 'contact_sheet', return_value=False):
                        cfg = {**bot.settings(), '_image_memory': memory, '_image_reader': reader}
                        for identity in ('first', 'second'):
                            await bot.generate_for_message(client, cfg, [], '被@', {
                                '_id': identity, 'text': '看这个', 'at_bot': True,
                                '_images': [MARKET_URL], '_image_keys': {MARKET_URL: 'market-key'}})
                    await reader.close()
                self.assertEqual(requests, [MARKET_URL])
                self.assertEqual(len(vision_calls), 1)
                self.assertEqual(len(replies), 2)
                self.assertTrue(all(row[0]['description'] == FACTS['description'] for row in replies))
        asyncio.run(run())

    def test_deterministic_shared_download_failure_is_logged_once_without_duplicate_download(self):
        async def run():
            reader = type('Reader', (), {'read': AsyncMock(side_effect=ImageDownloadError('image_source_not_allowed'))})()
            with tempfile.TemporaryDirectory() as folder, patch.object(bot, 'RUNTIME', Path(folder)), \
                    patch.object(bot, 'download_image', AsyncMock()) as download, \
                    patch.object(bot, 'generate', AsyncMock(return_value='')), \
                    patch.object(bot, 'analyze_roles', AsyncMock(return_value={})), \
                    patch.object(bot.EmojiLibrary, 'contact_sheet', return_value=False), \
                    self.assertLogs('qq-group-bot', level='WARNING') as messages:
                await bot.generate_for_message(None, {**bot.settings(), '_image_reader': reader}, [], '被@',
                    {'_id': 'blocked', 'text': '看看', 'at_bot': True,
                     '_images': ['https://invalid.example/a?token=private']})
                download.assert_not_awaited()
            self.assertEqual(len(messages.output), 1)
            self.assertIn('reason=image_source_not_allowed; source=invalid.example', messages.output[0])
            self.assertNotIn('private', messages.output[0])
        asyncio.run(run())

    def test_sticker_collector_uses_same_market_source_support_and_keeps_animation(self):
        async def run():
            raw = gif_bytes()
            async def classifier(path, key):
                self.assertEqual(path.read_bytes(), raw)
                return {'id': key, 'asset_type': 'sticker', 'confidence': .99,
                    'emotion': '开心', 'description': '猫猫挥手', 'use_when': '打招呼时'}
            with tempfile.TemporaryDirectory() as folder:
                library = EmojiLibrary(folder, Path(folder) / 'faces.json', classifier)
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw))) as client:
                    worker = asyncio.create_task(library.collector(client))
                    try:
                        library.observe({'message': [{'type': 'image', 'data':
                            {'sub_type': 1, 'file': 'market.gif', 'url': MARKET_URL}}]})
                        await asyncio.wait_for(library.queue.join(), 2)
                        item = next(iter(library.items.values()))
                        self.assertTrue(approved(item))
                        from emoji_assets import asset_path
                        with Image.open(asset_path(item, 'path', library.root)) as image:
                            self.assertEqual(image.n_frames, 2)
                    finally:
                        worker.cancel()
                        await asyncio.gather(worker, return_exceptions=True)
        asyncio.run(run())

    def test_background_failures_publish_unavailable_and_safe_diagnostics(self):
        async def run():
            events = []
            with tempfile.TemporaryDirectory() as folder:
                reader = ImageRecognition(ImageMemory(Path(folder) / 'memory.json'),
                    AsyncMock(side_effect=ImageDownloadError('image_download_too_large')),
                    lambda item, summaries, status: events.append(status))
                with self.assertLogs('qq-group-bot', level='WARNING') as messages:
                    worker = asyncio.create_task(reader.collector())
                    try:
                        reader.offer({'_id': 'too-large', '_images': [MARKET_URL + '?token=private']})
                        await asyncio.wait_for(reader.queue.join(), 2)
                    finally:
                        worker.cancel()
                        await asyncio.gather(worker, return_exceptions=True)
                        await reader.close()
                self.assertEqual(events, ['pending', 'unavailable'])
                self.assertIn('reason=image_download_too_large; source=gxh.vip.qq.com', messages.output[0])
                self.assertNotIn('private', messages.output[0])
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
