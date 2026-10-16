"""Bounded local debugging events: real I/O and stages, never hidden reasoning."""
import json
import logging
from collections import deque
from pathlib import Path
import re
import time
from contextvars import ContextVar
from contextlib import contextmanager
from console_config import Config
from debug_references import text_chars
from debug_history import bounded

CURRENT = None
TRACE = ContextVar('qq_debug_trace',default={})


@contextmanager
def trace(message_id,source_ids=()):
    token=TRACE.set({'context_id':str(message_id),'request_id':str(message_id),
        'source_ids':[str(value) for value in source_ids if value is not None][:8]})
    try:yield
    finally:TRACE.reset(token)


def clean(text):
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(text))
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)[:4000]


def safe_log(text):
    text = re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?[参数隐藏]', clean(text))
    return re.sub(r'(?i)(authorization|bearer|token|password|api_key|secret)([\s:=]+)[^\s,;]+', r'\1\2[隐藏]', text)


class StageHandler(logging.Handler):
    def __init__(self, events):
        super().__init__(); self.events = events

    def emit(self, record):
        try:self.events.emit('stage', text=safe_log(record.getMessage()), level=record.levelname)
        except OSError:pass


class DebugEvents:
    def __init__(self, runtime, group_id):
        self.runtime, self.group_id = Path(runtime), int(group_id)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.path=self.runtime/'debug-events.jsonl'
        self.rows=deque()
        try:
            for line in self.path.read_text(encoding='utf-8').splitlines():
                try:self.rows.append(json.loads(line))
                except ValueError:pass
        except OSError:pass
        self.handler = StageHandler(self)

    def config(self):
        base=Config(self.runtime.parent).load()
        return Config(self.runtime.parent,self.group_id).load() if self.group_id in base.get('chat_group_whitelist',[]) else base

    def emit(self, kind, **data):
        row = {'time': time.time(), 'group_id': self.group_id, 'kind': kind, **TRACE.get(), **data}
        if 'text' in row: row['text'] = clean(row['text'])
        cfg=self.config()
        limit=cfg['console_max_text_chars']
        if 'text' in row:row['text']=row['text'][:limit]
        self.rows.append(row)
        self._save(cfg)
        if kind in {'received','sent'} and self.group_id in cfg.get('chat_group_whitelist',[]):
            from system_interventions import Interventions
            Interventions(self.runtime.parent,self.group_id).observe(row,cfg)

    def update_message(self, message_id, text, **data):
        updated = False
        for row in self.rows:
            if row.get('kind') in {'received', 'sent'} and str(row.get('message_id')) == str(message_id):
                row.update(text=clean(text), **data)
                updated = True
        if updated:
            self._save(self.config())
        return updated

    def _save(self, cfg):
        self.rows=deque(bounded(self.rows,cfg))
        temp=self.path.with_suffix('.tmp')
        try:
            temp.write_text(''.join(json.dumps(item,ensure_ascii=False,separators=(',',':'))+'\n'
                for item in self.rows),encoding='utf-8');temp.replace(self.path)
        except OSError:
            temp.unlink(missing_ok=True)

    def metadata(self, name):
        data = {'group_id': self.group_id, 'name': clean(name)[:100]}
        path = self.runtime/'debug-group.json'; temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8'); temp.replace(path)
        self.emit('group', text=data['name'])

    def close(self):
        pass


def emit(kind, **data):
    if CURRENT is not None:
        try:CURRENT.emit(kind, **data)
        except OSError:pass


def update_message(message_id, text, **data):
    if CURRENT is not None:
        try:return CURRENT.update_message(message_id, text, **data)
        except OSError:pass
    return False
