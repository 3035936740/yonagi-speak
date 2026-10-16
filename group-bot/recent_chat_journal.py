"""Crash-safe, bounded recent chat context stored as JSON Lines."""
import json
import os
from pathlib import Path
import time


FIELDS = ('sender', 'text', 'at_bot', 'is_bot', '_time', '_uid', '_id',
    '_at_ids', '_reply_id', '_audience', '_reply_to_uid', '_media_sent', '_group_id', '_nickname', '_card', '_image_hashes')


def compact(item):
    if not isinstance(item, dict) or item.get('_id') in (None, ''):
        return None
    saved = {key: item[key] for key in FIELDS if key in item}
    hashes = item.get('_image_hashes', []) or list(item.get('_image_keys', {}).values())
    if hashes:
        import re
        saved['_image_hashes'] = [value for value in hashes[:2]
            if isinstance(value,str) and re.fullmatch(r'[a-f0-9]{64}', value)]
    saved['_id'] = str(saved['_id'])[:80]
    saved['_uid'] = str(saved.get('_uid', ''))[:40]
    saved['sender'] = str(saved.get('sender', ''))[:80]
    for key in ('_nickname','_card'):
        if key in saved:saved[key]=str(saved[key] or '')[:80]
    saved['text'] = ('[临时内容已清空]' if item.get('_ephemeral_content')
        else str(saved.get('text', ''))[:1000])
    saved['_time'] = float(saved.get('_time', 0) or 0)
    if '_reply_id' in saved and saved['_reply_id'] is not None:
        saved['_reply_id'] = str(saved['_reply_id'])[:80]
    if '_at_ids' in saved:
        saved['_at_ids'] = [str(value)[:40] for value in saved['_at_ids'][:8]]
    return saved


class RecentChatJournal:
    def __init__(self, path, max_messages=500, max_age_hours=24):
        self.path = Path(path)
        self.max_messages = max(24, int(max_messages))
        self.max_age = max(1, float(max_age_hours)) * 3600
        self.records = []
        self.ids = set()

    def _bounded(self, items):
        cutoff = time.time() - self.max_age
        unique = {}
        for item in items:
            saved = compact(item)
            if saved and saved['_time'] >= cutoff:
                unique[saved['_id']] = saved
        return sorted(unique.values(), key=lambda value: value['_time'])[-self.max_messages:]

    def load(self):
        rows = []
        try:
            with self.path.open('r', encoding='utf-8') as stream:
                for line in stream:
                    try:
                        rows.append(json.loads(line))
                    except (TypeError, ValueError):
                        # A crash can leave only the last line incomplete.
                        continue
        except FileNotFoundError:
            pass
        self.records = self._bounded(rows)
        self.ids = {item['_id'] for item in self.records}
        return list(self.records)

    def append(self, item):
        saved = compact(item)
        if not saved or saved['_id'] in self.ids:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(saved, ensure_ascii=False, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.records.append(saved)
        self.ids.add(saved['_id'])
        if len(self.records) > self.max_messages:
            self.checkpoint(self.records)
        return True

    def checkpoint(self, items=None):
        records = self._bounded(self.records if items is None else items)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + '.tmp')
        with temp.open('w', encoding='utf-8', newline='\n') as stream:
            for item in records:
                stream.write(json.dumps(item, ensure_ascii=False, separators=(',', ':')) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(self.path)
        self.records = records
        self.ids = {item['_id'] for item in records}

