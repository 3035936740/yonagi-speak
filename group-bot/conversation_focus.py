"""Small restart-safe record of whom the bot was answering."""
import json
from pathlib import Path
import time


class ConversationFocus:
    def __init__(self, path):
        self.path = Path(path)
        try:
            self.entries = json.loads(self.path.read_text(encoding='utf-8'))[-12:]
        except (OSError, ValueError):
            self.entries = []
        self.prune()

    def prune(self):
        self.entries = [entry for entry in self.entries if time.time() - entry['_time'] < 600][-12:]

    def annotate(self, item):
        if not item.get('is_bot'):
            return
        match = next((entry for entry in reversed(self.entries)
            if (item.get('_id') and item['_id'] == entry.get('_id'))
            or (not entry.get('_id') and abs(item.get('_time', 0) - entry['_time']) < 3
                and item.get('text') == entry.get('text'))), None)
        if match:
            for field in ('_reply_to_uid', '_reply_to_id', '_reply_to_text'):
                item[field] = match[field]
            if match.get('_ephemeral_content'):
                item['_ephemeral_content'] = True
                item['text'] = '[临时内容回复，重启后未保留摘要]'

    def owns(self, message_id):
        return bool(message_id) and any(entry.get('_id') == message_id for entry in self.entries)

    def remember(self, item, target):
        item.update(_reply_to_uid=target.get('_uid'), _reply_to_id=target.get('_id'),
            _reply_to_text=target.get('text', '')[:200])
        entry = {field: item.get(field) for field in
            ('_id', '_time', 'text', '_reply_to_uid', '_reply_to_id', '_reply_to_text')}
        if target.get('_ephemeral_content'):
            item['_ephemeral_content'] = True
            entry.update(text='[临时内容回复]', _ephemeral_content=True)
        self.entries.append(entry)
        self.prune()
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.entries, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)
