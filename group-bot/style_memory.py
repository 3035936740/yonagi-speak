"""Bounded, anonymous group-language observations; never executable instructions."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import time


def safe_text(text):
    text = re.sub(r'@\d+|\[[^\]]*\]', '', str(text)).strip()
    if not 2 <= len(text) <= 70:
        return ''
    blocked = r'https?://|\d{5,}|(?:密钥|密码|token|api.key|忽略.*指令|系统提示|执行命令|powershell|住址|身份证|手机号|我家|我叫|学校|工作单位|操你|草你|尿出来|做爱|未成年|开盒)'
    if re.search(blocked, text, re.I) or '\n' in text:
        return ''
    return text


def grams(text):
    text = re.sub(r'\W', '', text.lower())
    return {text[i:i + 2] for i in range(len(text) - 1)}


def banter_categories(text):
    categories = []
    if re.search(r'炒鱿鱼|废物|笨蛋|萨比|没问你|滚|好凶', text):
        categories.append('可能在熟人互损或假装生气')
    if re.search(r'妈妈|主人|乖宝宝|宝宝|叫妈', text):
        categories.append('可能在玩角色称呼和认亲梗')
    if re.search(r'涩|贴贴|亲亲|教培|磕', text):
        categories.append('可能是轻度擦边或亲昵玩笑')
    return categories


class StyleMemory:
    def __init__(self, path):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        self.salt = data.get('salt') or __import__('secrets').token_hex(16)
        self.samples = data.get('samples', [])[-400:]
        self.seen = data.get('seen', [])[-1000:]
        self.previous = None
        self.pairs = data.get('pairs', [])[-100:]
        self.voice = data.get('voice', {})
        self.prune()

    def update_voice(self, strength=.35):
        """Slow group-wide adaptation; one prolific speaker cannot dominate."""
        if len(self.samples) < 12 or time.time()-self.voice.get('updated_at',0)<600:
            return
        speakers=Counter(item['speaker'] for item in self.samples)
        if len(speakers)<2:return
        weighted=[]
        for item in self.samples:
            age=max(0,time.time()-item['time'])/3600
            weight=(.5 ** (age/72)) / max(1,speakers[item['speaker']] ** .5)
            weighted.append((weight,item['text']))
        total=sum(weight for weight,_ in weighted)
        if not total:return
        mean=sum(weight*len(text) for weight,text in weighted)/total
        brackets=sum(weight for weight,text in weighted if re.search(r'[（(](?:[）)])?\s*$',text))/total
        period=sum(weight for weight,text in weighted if text.endswith('。'))/total
        alpha=.2
        old=self.voice
        def blend(key,value):return round((1-alpha)*old.get(key,value)+alpha*value,3)
        self.voice={'mean_length':blend('mean_length',mean), 'bracket_fraction':blend('bracket_fraction',brackets),
            'period_fraction':blend('period_fraction',period),'updated_at':time.time(),
            'sample_count':len(self.samples),'speaker_count':len(speakers)}

    def voice_profile(self, strength=.35):
        self.update_voice(strength)
        return {key:value for key,value in self.voice.items() if key!='updated_at'} | {'strength':strength} if self.voice else {}

    def digest(self, value):
        return hashlib.sha256((self.salt + str(value)).encode()).hexdigest()[:20]

    def prune(self):
        cutoff = time.time() - 7 * 86400
        self.samples = [s for s in self.samples if s['time'] >= cutoff][-400:]
        self.pairs = [p for p in self.pairs if p['time'] >= cutoff][-100:]

    def observe(self, item, recent_bot=()):
        from interaction import social_act
        key = self.digest(item.get('_id', ''))
        if not item.get('_id') or key in self.seen or item.get('is_bot'):
            return
        self.seen = (self.seen + [key])[-1000:]
        # Friends echoing the bot are interaction evidence, not an independent
        # source of group style. Avoid teaching our own verbal tics back to us.
        from peer_reply import normalize
        if normalize(item.get('text','')) and any(normalize(item['text'])==normalize(old.get('text','')) for old in recent_bot):
            self.previous=None
            return
        text = safe_text(item.get('text', ''))
        stamp = item.get('_time', time.time())
        if not text or stamp < time.time() - 7 * 86400:
            self.previous = None
            return
        sample = {'text': text, 'speaker': self.digest(item.get('_uid', '')), 'source_id': item['_id'],
                  'time': stamp}
        previous = self.previous
        if (previous and previous['speaker'] != sample['speaker']
            and 0 <= stamp - previous['time'] <= 45
            and (not item.get('_reply_id') or item['_reply_id'] == previous.get('source_id'))):
            self.pairs.append({'before': previous['text'], 'reply': text, 'time': stamp,
                'act': social_act(previous['text'])})
        self.samples.append(sample)
        self.previous = sample
        self.prune()

    def save(self):
        self.prune()
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps({'salt': self.salt, 'samples': self.samples,
            'pairs': self.pairs, 'seen': self.seen, 'voice':self.voice}, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)

    def retrieve(self, target, limit=900):
        from interaction import social_act
        self.prune()
        if len(self.samples) < 12:
            return {}
        lengths = sorted(len(s['text']) for s in self.samples)
        counts = Counter(s['text'] for s in self.samples)
        phrases = [text for text, count in counts.most_common(10)
            if count >= 3 and len({s['speaker'] for s in self.samples if s['text'] == text}) >= 2]
        query = grams(target)
        situations = set(banter_categories(target))
        scored = []
        for pair in self.pairs:
            words = grams(pair['before'])
            overlap = len(query & words) / max(1, len(query | words))
            if situations & set(banter_categories(pair['before'])):
                overlap = max(overlap, 0.25)
            if social_act(target) != 'statement' and (pair.get('act') or social_act(pair['before'])) == social_act(target):
                overlap = max(overlap, .3)
            if overlap >= 0.2:
                scored.append((overlap, pair))
        examples = []
        for _, pair in sorted(scored, key=lambda p: p[0], reverse=True):
            candidate = {'before': pair['before'], 'reply': pair['reply']}
            if candidate not in examples:
                examples.append(candidate)
            if len(examples) == 3:
                break
        result = {'observed_messages': len(self.samples),
            'possible_banter_situations': sorted(situations),
            'median_length': lengths[len(lengths) // 2],
            'no_final_period_fraction': round(sum(not s['text'].endswith('。') for s in self.samples) / len(self.samples), 2),
            'shared_phrases': phrases[:5], 'possible_reply_examples': examples}
        suffixes = Counter()
        suffix_examples = {}
        for sample in self.samples:
            match = re.search(r'(（\s*）|\(\s*\)|（|\()\s*$', sample['text'])
            if match:
                suffix = match.group(1).replace(' ', '')
                suffixes[suffix] += 1
                suffix_examples.setdefault(suffix, sample['text'])
        # Learn punctuation across different sentences; exact phrase counts miss it.
        result['ending_habits'] = [{'form': suffix, 'uses': count,
            'fraction': round(count / len(self.samples), 3), 'example': suffix_examples[suffix]}
            for suffix, count in suffixes.most_common(4) if count >= 2]
        if re.search(r'和我教培', target):
            result['local_jargon_hint'] = '本群语境下可能是谐音挑逗梗，不是补课请求；可以理解玩笑但不要展开露骨互动。实际教培行业话题不适用这个推测。'
        expression_counts = Counter(s['text'] for s in self.samples if 2 <= len(s['text']) <= 16)
        result['short_expression_samples'] = [text for text, count in expression_counts.most_common(30)
            if count >= 2 and len({s['speaker'] for s in self.samples if s['text'] == text}) >= 2][:4]
        while len(json.dumps(result, ensure_ascii=False)) > limit:
            if result['possible_reply_examples']:
                result['possible_reply_examples'].pop()
            elif result['shared_phrases']:
                result['shared_phrases'].pop()
            elif result['short_expression_samples']:
                result['short_expression_samples'].pop()
            elif result['ending_habits']:
                result['ending_habits'].pop()
            else:
                return {}
        return result
