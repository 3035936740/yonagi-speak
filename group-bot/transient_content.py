"""Small, process-only follow-up summaries with active expiry; no disk persistence."""
from collections import OrderedDict
import re
import threading
import time
import uuid
from dialogue import feedback_about_reply


class TransientContent:
    def __init__(self, ttl=120, capacity=8, summary_limit=600, clock=time.monotonic):
        self.ttl, self.capacity, self.summary_limit = ttl, capacity, summary_limit
        self.clock = clock
        self.entries = OrderedDict()
        self.lock = threading.RLock()

    def _remove(self, key):
        with self.lock:
            entry = self.entries.pop(key, None)
            if entry:
                entry['timer'].cancel()

    def clear(self):
        with self.lock:
            for key in list(self.entries):
                self._remove(key)

    def remember(self, group_id, target, reply):
        text = str(reply.get('text', '')).strip()
        if not text or text.startswith('[已发送'):
            return
        key = uuid.uuid4().hex
        with self.lock:
            ids = {str(value) for value in (target.get('_id'), target.get('_reply_id'),
                target.get('_content_source_id'), reply.get('_id')) if value is not None}
            fact=dict(target.get('_source_evidence',{}))
            source_url=str(target.get('_source_url',''))[:500]
            # A split reply is one answer: follow-up feedback needs all parts,
            # rather than only the final sentence of a two-message summary.
            for old_key, old in list(reversed(self.entries.items())):
                if (target.get('_id') and old.get('request') == str(target['_id'])
                        and old['owner'] == str(target.get('_uid')) and old['group'] == str(group_id)
                        and self.clock() - old['created'] < self.ttl):
                    text = old['summary'] + '\n' + text
                    ids.update(old['ids'])
                    if not fact:fact=old.get('source_fact',{})
                    if not source_url:source_url=old.get('source_url','')
                    self._remove(old_key)
                    break
            # Keep only a short answer, bounded facts and the already-read public
            # page pointer for the same owner. No pixels or full pages persist.
            entry = {'group': str(group_id), 'owner': str(target.get('_uid')),
                'request': str(target.get('_id')), 'ids': ids,
                'summary': text[:self.summary_limit], 'created': self.clock(),
                'source_fact':{key:str(fact.get(key,''))[:maximum] for key,maximum in [('title',80),('body',520)]} if fact else {}}
            entry['source_url']=source_url
            timer = threading.Timer(self.ttl, self._remove, args=(key,))
            timer.daemon = True
            entry['timer'] = timer
            self.entries[key] = entry
            while len(self.entries) > self.capacity:
                self._remove(next(iter(self.entries)))
            timer.start()

    def recall(self, group_id, target):
        quote = target.get('_reply_id')
        words = re.sub(r'@\d+|\[[^\]]*\]', '', target.get('text', '')).strip()
        followup = bool(target.get('_feedback_on_answer') or feedback_about_reply(words)
            or target.get('_continuation_bot_id')
            or re.search(r'刚才|上面(?:那|的)|这(?:个|张|篇|图)|那(?:个|张|篇|图)|看到.{0,4}[啥什]|看到了啥|^(?:所以|它|继续|再展开|详细说)', words))
        with self.lock:
            for key in list(self.entries):
                if self.clock() - self.entries[key]['created'] >= self.ttl:
                    self._remove(key)
            if not quote and not followup and target.get('_audience') not in {'direct','possibly_direct'}:
                return {}
            for entry in reversed(self.entries.values()):
                if entry['group'] != str(group_id) or entry['owner'] != str(target.get('_uid')):
                    continue
                if quote and str(quote) not in entry['ids']:
                    continue
                return {'summary': entry['summary'], 'seconds_ago': int(self.clock() - entry['created']),
                    'source_fact':dict(entry.get('source_fact',{})),
                    'source_url':entry.get('source_url',''),
                    'scope': '仅上一轮临时短答，可能不完整或有识别错误；未知细节不要推测'}
        return {}


TEMP_CONTENT = TransientContent()
