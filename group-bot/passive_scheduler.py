"""Bounded group-turn scheduling inspired by MaiBot's message gates.

Independent adapter: keep fresh messages until a turn is considered, use an
inverse-frequency threshold with idle compensation, or an optional score gate.
No MaiBot fitted weights or source implementation are copied.
"""
import json
import math
from pathlib import Path
import re
import time
from console_config import atomic,read_json
from debug_events import emit
from interaction import select_hook
from dialogue import passive_candidate
from social_state import participation


def meaningful(item):
    text=re.sub(r'@\d+|\[[^\]]*\]|\s+','',item.get('text',''))
    return bool(text) or bool(item.get('_images') or item.get('_sticker_ids') or item.get('_image_hashes'))


def join_candidate(item,history,self_id):
    if item.get('is_bot') or not meaningful(item):return False
    if item.get('_images') or item.get('_sticker_ids') or item.get('_image_hashes'):return True
    # Keep human-directed conversation as context. A substantive hook can be
    # considered for an observer reaction, never relabelled as an @ to the bot.
    if item.get('_audience')=='others':
        return len(re.sub(r'@\d+|\[[^\]]*\]|\s+','',item.get('text','')))>=2
    return passive_candidate(item,history,self_id)


def score_batch(history,batch,now):
    recent=[item for item in history if 0<=now-item.get('_time',0)<=300]
    share=sum(bool(item.get('is_bot')) for item in recent)/max(1,len(recent))
    other=sum(item.get('_audience')=='others' for item in batch)/max(1,len(batch))
    plain=[re.sub(r'@\d+|\[[^\]]*\]','',item.get('text','')).strip() for item in batch]
    question=any(re.search(r'[?？]|怎么|为什么|有人|谁知道',text) for text in plain)
    open_text=any(text and item.get('_audience')!='others' for text,item in zip(plain,batch))
    reaction=any(item.get('_sticker_ids') or item.get('_image_repetition',{}).get('repetition_confidence',0)>=.5 for item in batch)
    newest=max((item.get('_time',now) for item in batch),default=now)
    idle=max(0,now-newest)
    score=.22+min(.32,.08*len(batch))+.14*question+.12*open_text+.14*reaction
    score-=.18*other+.1*(not any(plain))+.5*share
    score+=min(.15,idle/600)
    return round(min(.95,max(.01,score)),4)


class PassiveScheduler:
    def __init__(self,path,self_id):
        self.path=Path(path);self.self_id=str(self_id);self.pending={}
        stored=read_json(self.path);self.data=stored if isinstance(stored,dict) else {}
        self.data.setdefault('rounds',[]);self.data.setdefault('replies',[]);self.data.setdefault('forced',[])
        self.data.setdefault('forced_ids',[]);self.data.setdefault('scored_ids',[])
        self.data.setdefault('outcomes',{});self.last_key=None;self.latest={}
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);self.data['latest']=self.latest
        atomic(self.path,self.data)
    def observe(self,items,now=None):
        now=time.time() if now is None else now
        for item in items:
            if (not item.get('is_bot') and not item.get('at_bot')
                    and item.get('_audience') not in {'direct','possibly_direct'} and meaningful(item)
                    and item.get('_id') and 0<=now-item.get('_time',0)<=180):
                identity=str(item['_id'])
                if identity not in self.data['scored_ids'] or identity in self.pending:
                    self.pending[identity]=item # share foreground/background image facts
        while len(self.pending)>120:self.pending.pop(next(iter(self.pending)))
    def _record(self,result,ids):
        self.latest=result
        key=(result['reason'],tuple(ids),result.get('threshold'),result.get('should_trigger'))
        if key!=self.last_key:
            self.last_key=key;self.save()
            emit('decision',role='participation',text=json.dumps(result,ensure_ascii=False),source_ids=ids[-12:])
        return result
    def check(self,cfg,history,now=None,last_send=0,blocked=None):
        now=time.time() if now is None else now;window=cfg.get('passive_window_seconds',180)
        self.pending={key:item for key,item in self.pending.items() if 0<=now-item.get('_time',0)<=window}
        batch=list(self.pending.values());ids=list(self.pending)
        for key in ('rounds','replies','forced'):
            self.data[key]=[entry for entry in self.data[key] if entry[0]>=now-3600][-512:]
        effective=participation(cfg,history,batch,state=cfg.get('_social_state'),now=now)['probability']
        mode=cfg.get('passive_trigger_mode','frequency')
        threshold=max(1,math.ceil((cfg.get('passive_min_messages',3)/3)/max(effective,.0001)))
        result={'mode':mode,'frequency':round(effective,4),'configured_frequency':cfg['passive_probability'],
            'pending_count':len(batch),'threshold':threshold,'should_trigger':False,'reason':'waiting_messages'}
        if not batch:return self._record({**result,'reason':'no_new_messages'},ids)
        if effective<=0:return self._record({**result,'reason':'frequency_disabled'},ids)
        if blocked:return self._record({**result,'reason':blocked},ids)
        elapsed=now-max(last_send,self.data.get('last_attempt',0))
        if elapsed<cfg['passive_cooldown_seconds']:
            return self._record({**result,'reason':'cooldown','cooldown_remaining':round(cfg['passive_cooldown_seconds']-elapsed,1)},ids)
        if now-self.data.get('last_attempt',0)<cfg.get('passive_retry_backoff_seconds',15):
            return self._record({**result,'reason':'planner_backoff'},ids)
        score=score_batch(history,batch,now)
        rounds=self.data['rounds'];round_seconds=cfg.get('passive_round_seconds',40)
        fresh_ids=[identity for identity in ids if identity not in self.data['scored_ids']]
        if fresh_ids:
            self.data['scored_ids']=(self.data['scored_ids']+fresh_ids)[-2000:]
            if not rounds or now-rounds[-1][0]>=round_seconds:rounds.append([now,score])
            else:rounds[-1][1]=score
        newest=max(item.get('_time',now) for item in batch);idle=max(0,now-newest)
        times=sorted(set(item.get('_time',0) for item in list(history)[-60:] if not item.get('is_bot') and 0<=now-item.get('_time',0)<=1800))
        gaps=[b-a for a,b in zip(times,times[1:]) if 0<b-a<=300]
        average=min(60,max(5,sum(gaps)/len(gaps))) if gaps else 20
        idle_needed=max(5,(threshold-len(batch))*average)
        compensated=bool(cfg.get('passive_idle_compensation_enabled',True) and idle>=idle_needed)
        result.update(score=score,idle_seconds=round(idle,1),idle_needed_seconds=round(idle_needed,1))
        if mode=='dynamic':
            expected=sum(entry[1] for entry in rounds);forced=len(self.data['forced'])
            target=effective*(expected+forced);actual=len(self.data['replies'])
            keep=max(0,min(1,(target-forced)/max(.01,expected)))
            keep*=min(1,max(0,1+(target-actual)/max(1,target)))
            if len(rounds)>=20 and keep>0:
                used=0;score_threshold=1
                for value in sorted((entry[1] for entry in rounds),reverse=True):
                    used+=value
                    if used>=keep*expected:score_threshold=value;break
            else:score_threshold=1 if keep<=0 else .1+.7*(1-keep)
            ready=bool(effective>0 and score>=score_threshold)
            result.update(score_threshold=round(score_threshold,4),expected_replies=round(expected+forced,2),
                target_replies=round(target,2),actual_reply_turns=actual,scored_rounds=len(rounds))
        else:ready=len(batch)>=threshold or compensated
        if not ready:return self._record(result,ids)
        candidates=[item for item in batch if join_candidate(item,history,self.self_id)]
        if not candidates:
            self.consume(ids,now);return self._record({**result,'reason':'no_usable_hook'},ids)
        open_candidates=[item for item in candidates if item.get('_audience')!='others']
        target=select_hook(open_candidates or candidates,history)
        target=dict(target);target['_passive_turn']=True;target['_passive_batch_ids']=ids
        target['_passive_batch']=[{'id':str(item['_id']),'sender':str(item.get('sender',''))[:40],
            'text':str(item.get('text',''))[:120],'audience':item.get('_audience','open')} for item in batch[-8:]]
        result.update(should_trigger=True,reason='dynamic_score' if mode=='dynamic' else 'message_threshold' if len(batch)>=threshold else 'idle_compensation',
            target_id=str(target['_id']),candidate_count=len(candidates))
        self._record(result,ids)
        return {**result,'target':target}
    def consume(self,ids,now):
        for identity in ids:self.pending.pop(str(identity),None)
        self.data['last_attempt']=now;self.save()
    def forced(self,target,now):
        if target.get('_passive_turn') or target.get('_proactive_topic'):return
        identity=str(target.get('_id',''))
        if identity and identity not in self.data['forced_ids']:
            self.data['forced_ids']=(self.data['forced_ids']+[identity])[-2000:]
            self.data['forced'].append([now]);self.save()
    def delivered(self,target,now):
        if target.get('_participation_counted'):return
        target['_participation_counted']=True
        self.data['replies'].append([now,bool(target.get('_passive_turn'))])
        self.outcome('sent' if target.get('_passive_turn') else 'direct_sent',target)
    def outcome(self,reason,target):
        counts=self.data['outcomes'];counts[reason]=counts.get(reason,0)+1;self.save()
        if target.get('_passive_turn'):
            emit('decision',role='participation_result',text=json.dumps({'result':reason,
                'message_id':str(target.get('_id','')),'plan':target.get('_accepted_plan',{}).get('action','unavailable')},ensure_ascii=False),
                context_id=str(target.get('_id','')),source_ids=target.get('_passive_batch_ids',[])[-12:])
