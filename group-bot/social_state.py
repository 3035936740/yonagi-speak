"""Persistent, source-linked conversation state; no invented personal traits."""
import json
from pathlib import Path
import time
from console_config import atomic, read_json


class SocialState:
    def __init__(self, path, ttl=600):
        self.path, self.ttl = Path(path), ttl
        stored = read_json(path, {})
        self.data = stored if isinstance(stored,dict) else {}
        self.data.setdefault('revision', 0)
        self.data.setdefault('exchanges', [])
        self.data['exchanges'] = [item for item in self.data['exchanges']
            if isinstance(item,dict) and type(item.get('time')) in (int,float) and 'user_id' in item][-64:]
        # Interrupted work is observable, never restored as completed.
        self.data['tasks'] = {}
        self.data.setdefault('topic', {})
        self.data.setdefault('awaiting', {})
        for key in ('topic','awaiting'):
            if not isinstance(self.data[key],dict):self.data[key]={}
        self.prune()

    def prune(self, now=None):
        now = time.time() if now is None else now
        for key in ('topic', 'awaiting'):
            if now - self.data.get(key, {}).get('time', 0) > self.ttl:
                self.data[key] = {}
        self.data['exchanges'] = [item for item in self.data['exchanges']
            if 0 <= now - item['time'] < 7 * 86400][-64:]

    def save(self):
        self.prune()
        atomic(self.path, self.data)

    def observe(self, item):
        if item.get('is_bot'):
            return
        self.prune(item.get('_time'))
        self.data['revision'] += 1
        waiting = self.data['awaiting']
        if item.get('_audience') == 'others' and str(item.get('_uid')) == waiting.get('user_id'):
            self.data['awaiting'] = {}
        self.save()

    def related(self, item):
        self.prune(item.get('_time'))
        waiting = self.data['awaiting']
        return bool(waiting and str(item.get('_uid')) == waiting.get('user_id')
            and item.get('_audience') not in {'others', 'direct'}
            and not item.get('_at_ids') and not item.get('_reply_id'))

    def snapshot(self, target=None):
        self.prune()
        target = target or {}
        uid = str(target.get('_uid', ''))
        turns = [item for item in self.data['exchanges'] if item['user_id'] == uid][-2:]
        result = {'revision':self.data['revision'], 'active_topic':self.data['topic'],
            'awaiting':self.data['awaiting'], 'tasks':list(self.data['tasks'].values())[:2],
            'relationship':{'exchanges_7d':sum(item['user_id'] == uid for item in self.data['exchanges']),
                'last_exchanges':[{'user':item['user'], 'bot':item['bot']} for item in turns]}}
        if len(json.dumps(result,ensure_ascii=False))>500:
            result['relationship'].pop('last_exchanges',None)
        while result['tasks'] and len(json.dumps(result,ensure_ascii=False))>500:result['tasks'].pop()
        return result

    def plan(self, target, plan):
        stamp = time.time()
        if plan.get('topic'):
            source_time=target.get('_time',stamp)
            if source_time >= self.data['topic'].get('source_time',0):
                self.data['topic'] = {'text':plan['topic'][:60], 'time':stamp, 'source_time':source_time,
                'source_ids':target.get('_source_ids', [str(target.get('_id', ''))])[:8]}
        target['_accepted_plan'] = plan
        self.save()

    def begin(self, lane, target, stage='planning'):
        self.data['tasks'][lane] = {'lane':lane, 'message_id':str(target.get('_id', '')),
            'user_id':str(target.get('_uid', '')), 'stage':stage, 'time':time.time()}
        self.save()

    def finish(self, lane):
        self.data['tasks'].pop(lane, None)
        self.save()

    def delivered(self, reply, target):
        uid = str(target.get('_uid', ''))
        plan = target.get('_accepted_plan', {})
        text = str(reply.get('text', ''))[:120]
        self.data['exchanges'].append({'user_id':uid, 'time':reply['_time'],
            'user_message_id':str(target.get('_id', '')), 'bot_message_id':str(reply.get('_id', '')),
            'user':'[临时内容任务]' if target.get('_ephemeral_content') else str(target.get('text', ''))[:100],
            'bot':'[已发送内容答复]' if target.get('_ephemeral_content') else text,
            'expression_ids':target.get('_selected_expression_ids', [])[:3]})
        if target.get('_expects_response',plan.get('expects_response')) and uid:
            self.data['awaiting'] = {'user_id':uid, 'message_id':str(reply.get('_id', '')),
                'time':reply['_time'], 'topic':plan.get('topic', '')[:60]}
        elif self.data['awaiting'].get('user_id') == uid:
            self.data['awaiting'] = {}
        self.save()


def participation(cfg, history, incoming, state=None, now=None):
    """Configured probability is baseline intensity, direct calls bypass it."""
    now = time.time() if now is None else now
    baseline = float(cfg.get('passive_probability', .35))
    if not cfg.get('adaptive_participation_enabled', True):
        return {'probability':baseline, 'baseline':baseline, 'reason':'fixed'}
    recent = [item for item in list(history)[-30:] if 0 <= now - item.get('_time', 0) <= 120]
    own = [item for item in recent if item.get('is_bot')]
    share = len(own) / max(1, len(recent))
    last_own = max((item.get('_time', 0) for item in own), default=0)
    pressure = min(1, share / .25)
    spacing = min(1, max(.25, (now - last_own) / 45)) if own else 1
    related = bool(state and any(state.related(item) for item in incoming))
    repetition = max((item.get('_image_repetition', {}).get('repetition_confidence', 0)
        for item in incoming), default=0)
    relevance = 1.3 if related else 1.1 if repetition >= .5 else 1
    probability = min(.9, baseline * (1 - .75 * pressure) * spacing * relevance)
    return {'probability':round(probability, 4), 'baseline':baseline,
        'self_share':round(share, 3), 'related':related,
        'reason':'conversation' if related else 'recent_share_and_spacing'}
