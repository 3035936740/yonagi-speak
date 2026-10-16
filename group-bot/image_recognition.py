"""Bounded background image queue with shared foreground recognition jobs."""
import asyncio
import logging
from image_memory import source_key
from image_sources import failure_details


class ImageRecognition:
    def __init__(self, memory, recognize, publish, can_start=lambda: True):
        self.memory, self.recognize, self.publish = memory, recognize, publish
        self.can_start = can_start
        self.queue = asyncio.Queue(maxsize=64)
        self.pending = {}
        self.lock = asyncio.Lock()

    def offer(self, item):
        if not item.get('_images'):
            return
        known = [self.memory.lookup(key=item.get('_image_keys',{}).get(url) or source_key({'url':url}))
            for url in item['_images'][:2]]
        if known and all(known):
            self.publish(item, known, 'read')
            return
        if self.queue.full():
            self.publish(item, [], 'queue_full')
            return
        self.queue.put_nowait(item)
        self.publish(item, [], 'pending')

    async def read(self, url, key):
        known = self.memory.lookup(key=key)
        if known:
            return known
        if key not in self.pending:
            async def run():
                async with self.lock:
                    return self.memory.lookup(key=key) or await self.recognize(url, key)
            self.pending[key] = asyncio.create_task(run())
        task = self.pending[key]
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self.pending.get(key) is task:
                self.pending.pop(key, None)

    async def collector(self):
        while True:
            item = await self.queue.get()
            try:
                while not self.can_start():
                    await asyncio.sleep(.5)
                summaries = []
                for url in item.get('_images', [])[:2]:
                    key = item.get('_image_keys', {}).get(url) or source_key({'url':url})
                    try:
                        summary = await self.read(url, key)
                        if summary:
                            summaries.append(summary)
                    except Exception as error:
                        logging.getLogger('qq-group-bot').warning('Background image recognition unavailable; %s',
                            failure_details(error, url))
                self.publish(item, summaries, 'read' if summaries else 'unavailable')
            finally:
                self.queue.task_done()

    async def close(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
