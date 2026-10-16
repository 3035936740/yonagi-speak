"""Per-group factual vision cache, renewed by actual image occurrences."""
import hashlib
import json
from pathlib import Path
import re
from role_analysis import checked


def source_key(data):
    file = str(data.get('file', '')).strip('{}').lower()
    identity = file if re.fullmatch(r'[a-f0-9]{32,64}(?:\.[a-z0-9]+)?', file) else str(data.get('file', '')) + '|' + str(data.get('url', ''))
    return hashlib.sha256(identity.encode()).hexdigest()


class ImageMemory:
    def __init__(self, path, window=2000):
        self.path, self.window = Path(path), window
        try:
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.data = {}
        for key, value in {'sequence': 0, 'seen': [], 'sources': {}, 'entries': {}, 'recent': [], 'messages':{}}.items():
            self.data.setdefault(key, value)
        self.seen = set(self.data['seen'])
        # Recover counts from the existing message→hash map on upgrade. Do not
        # guess how often old cache entries were used when no evidence survives.
        start=self.data['sequence']-len(self.data['seen'])+1
        recovered={}
        for offset,identity in enumerate(self.data['seen']):
            for key in self.data['messages'].get(identity,[]):
                recovered.setdefault(key,[]).append(start+offset)
        for key,source in self.data['sources'].items():
            source.setdefault('occurrences',recovered.get(key,[]))
        self.repeat_window=10;self.repeat_min_count=3;self.pair_confidence=.5;self.full_streak=3
        self.prune()

    def configure(self, cfg):
        self.window=int(cfg.get('image_summary_window_messages',2000))
        self.repeat_window=int(cfg.get('image_repeat_window_messages',10))
        self.repeat_min_count=int(cfg.get('image_repeat_min_count',3))
        self.pair_confidence=float(cfg.get('image_repeat_pair_confidence',.5))
        self.full_streak=int(cfg.get('image_repeat_full_streak',3))
        self.data['recent']=self.data['recent'][-self.repeat_window:]
        self.prune()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)

    def prune(self):
        seq = self.data['sequence']
        for category in ('entries', 'sources'):
            self.data[category] = {key: value for key, value in self.data[category].items()
                if seq - value['last_seen'] < self.window}
        for source in self.data['sources'].values():
            source['occurrences']=[n for n in source.get('occurrences',[]) if seq-n<self.window][-self.window:]

    def observe(self, message_id, keys):
        message_id = str(message_id or '')
        if not message_id or message_id in self.seen:
            return self.repetition(keys)
        self.data['sequence'] += 1
        seq = self.data['sequence']
        self.data['seen'].append(message_id)
        self.seen.add(message_id)
        while len(self.data['seen']) > self.window + 100:
            self.seen.discard(self.data['seen'].pop(0))
        keys = list(dict.fromkeys(keys))[:20]
        if keys:self.data['messages'][message_id] = keys[:2]
        self.data['messages'] = {identity:values for identity,values in self.data['messages'].items() if identity in self.seen}
        for key in keys:
            source = self.data['sources'].setdefault(key, {})
            source['last_seen'] = seq
            source.setdefault('occurrences',[]).append(seq)
            digest = source.get('digest')
            if digest in self.data['entries']:
                self.data['entries'][digest]['last_seen'] = seq
        self.data['recent'] = (self.data['recent'] + [keys])[-self.repeat_window:]
        self.prune()
        self.save()
        return self.repetition(keys)

    def repetition(self, keys):
        def canonical(key):
            return self.data['sources'].get(key, {}).get('digest', key)
        recent = [{canonical(source) for source in message} for message in self.data['recent']]
        candidates = []
        for key in {canonical(key) for key in keys}:
            positions = [index for index, message in enumerate(recent) if key in message]
            count = len(positions)
            trailing = 0
            for message in reversed(recent):
                if key not in message: break
                trailing += 1
            gaps = [right - left for left, right in zip(positions, positions[1:])]
            adjacency = sum(gap == 1 for gap in gaps) / len(gaps) if gaps else 0
            density = count / (positions[-1] - positions[0] + 1) if positions else 0
            confidence = (1.0 if trailing >= self.full_streak else
                max(self.pair_confidence if trailing >= 2 else 0, .3 + .45 * density + .15 * adjacency) if count >= self.repeat_min_count else
                self.pair_confidence if trailing >= 2 else 0.0)
            candidates.append({'same_image_messages': count, 'consecutive_same_image': trailing >= 2,
                'trailing_same_image_messages': trailing,
                'average_gap_messages': round(sum(gaps) / len(gaps), 2) if gaps else 0,
                'adjacent_pair_ratio': round(adjacency, 2),
                'repetition_confidence': round(confidence, 2)})
        best = max(candidates, key=lambda item: (item['repetition_confidence'], item['same_image_messages']),
            default={'same_image_messages': 0, 'consecutive_same_image': False,
                'trailing_same_image_messages': 0, 'average_gap_messages': 0,
                'adjacent_pair_ratio': 0, 'repetition_confidence': 0.0})
        confidence = best['repetition_confidence']
        return {**best, 'window_messages': len(recent), 'is_repetition': confidence > 0,
            'repetition_confidence': confidence,
            'meaning': ('连续复读' if confidence == 1 else
                '疑似复读，也可能误发或只是断续发同一表情；连续性越高复读倾向越强')
                + '；结合插话和语境决定是否接梗，不证明它是表情包'}

    def bind(self, key, digest):
        source = self.data['sources'].setdefault(key, {'last_seen': self.data['sequence']})
        source['digest'] = digest
        # A second URL with equal bytes renews the same content entry too.
        if digest in self.data['entries']:
            self.data['entries'][digest]['last_seen'] = max(
                self.data['entries'][digest]['last_seen'], source['last_seen'])
        self.save()

    def frequency(self, key=None, digest=None):
        digest=digest or self.data['sources'].get(key,{}).get('digest')
        sources=[(name,value) for name,value in self.data['sources'].items()
            if (digest and value.get('digest')==digest) or (not digest and name==key)]
        positions={n for _,value in sources for n in value.get('occurrences',[]) if self.data['sequence']-n<self.window}
        last=max((value['last_seen'] for _,value in sources),default=0)
        recent=self.repetition([name for name,_ in sources])
        return {'seen_count':len(positions),'repeat_count':max(0,len(positions)-1),
            'window_messages':self.window,'remaining_messages':max(0,self.window-(self.data['sequence']-last)) if sources else 0,
            'recent_count':recent['same_image_messages'],'consecutive_count':recent['trailing_same_image_messages'],
            'repetition_confidence':recent['repetition_confidence']}

    def lookup(self, key=None, digest=None):
        self.prune()
        digest = digest or self.data['sources'].get(key, {}).get('digest')
        entry = self.data['entries'].get(digest)
        if entry and checked('vision', entry.get('summary'), set()):
            return dict(entry['summary'])
        return None

    def for_message(self, message_id, keys=()):
        keys = list(keys) or self.data['messages'].get(str(message_id), [])
        if not keys and str(message_id) in self.seen:
            # Old caches stored occurrence order but not its message→hash map.
            # Recover only the exact sequence, never guess from a recent image.
            index=self.data['seen'].index(str(message_id))
            sequence=self.data['sequence']-len(self.data['seen'])+index+1
            keys=[key for key,value in self.data['sources'].items() if value['last_seen']==sequence][:2]
        return [summary for key in keys if (summary:=self.lookup(key=key))]

    def store(self, key, digest, summary):
        if not checked('vision', summary, set()) or summary['confidence'] < .6:
            return False
        self.bind(key, digest)
        self.data['entries'][digest] = {'last_seen': self.data['sources'][key]['last_seen'],
            'summary': dict(summary)}
        self.save()
        return True
