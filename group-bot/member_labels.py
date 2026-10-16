"""Per-group account labels with operator precedence and auditable bot hints."""
import json
from pathlib import Path
import re
import time
import unicodedata

from console_config import Config,read_json,runtime
from group_identity import qq_id
from image_archive import locked
from storage_io import atomic_write_text

LABELS={'AI','bot','human'}
COMMAND_PREFIXES=['/','!','！','.','。','#','y']


def command_start(text,prefixes=COMMAND_PREFIXES):
    """Match an explicit command prefix only at the start, after optional @ IDs."""
    text=unicodedata.normalize('NFKC',str(text)).strip()
    text=re.sub(r'^(?:@\d+\s*)+','',text).lstrip()
    for prefix in sorted({unicodedata.normalize('NFKC',p).casefold() for p in prefixes},key=len,reverse=True):
        if not text.casefold().startswith(prefix):continue
        rest=text[len(prefix):]
        if not rest.strip() or not rest.lstrip()[0].isalnum():continue
        # A short Latin prefix may introduce Chinese or a separated argument,
        # but must not turn ordinary English words such as "yes" into commands.
        if prefix.isascii() and prefix.isalpha() and rest[0].isascii() and rest[0].isalpha():continue
        if prefix=='.' and rest.lstrip()[0].isdigit():continue
        token=re.match(r'\S+',text)
        return {'prefix':prefix,'command':token.group(0)[:80]}
    return None


def rule_message(item,prefixes):
    return {'id':str(item.get('_id','')),'uid':str(item.get('_uid','')),
        'time':item.get('_time',0),'self':bool(item.get('is_bot')),
        'start':str(item.get('text',''))[:160],'command':command_start(item.get('text',''),prefixes),
        'content':bool(item.get('_images') or str(item.get('text','')).strip()),
        'image':bool(item.get('_images')),'reply':str(item.get('_reply_id','')),
        'ats':list(map(str,item.get('_at_ids',[])))}


def response_evidence(messages):
    evidence=[];used=set()
    for index,response in enumerate(messages):
        account=response['uid']
        if response['self'] or response['command'] or not response['content']:continue
        commands=[row for row in messages[:index] if row['command'] and not row['self']
            and row['uid']!=account and 0<=response['time']-row['time']<=60
            and (account,row['id']) not in used
            and (not row['ats'] or account in row['ats'])]
        quoted=[row for row in commands if response['reply']==row['id']]
        addressed=[row for row in commands if row['uid'] in response['ats']]
        anchored=quoted or addressed
        # Without a quote/@, text must immediately follow the request. Images
        # can take longer, but still require one unambiguous recent command.
        candidates=anchored or (commands if len(commands)==1 and not response['reply'] and not response['ats']
            and (response['image'] or index>0 and messages[index-1]['id']==commands[0]['id']) else [])
        if len(candidates)!=1:continue
        command=candidates[0]
        pair=(account,command['id'])
        if pair in used:continue
        used.add(pair)
        evidence.append({'account':account,'command_id':command['id'],'response_id':response['id'],'time':response['time'],
            **command['command'],'anchor':'reply_or_at' if anchored else 'unique_recent_image' if response['image'] else 'adjacent_response'})
    return evidence


class MemberLabels:
    def __init__(self,folder):
        self.folder=Path(folder);self.path=self.folder/'member-labels.json'
    def read(self):return read_json(self.path,{'members':{}})
    def save(self,data):atomic_write_text(self.path,json.dumps(data,ensure_ascii=False,indent=2))
    def mark(self,account,label,note='',aliases=None):
        account=qq_id(account)
        if not account or label not in LABELS|{'auto','clear'}:raise ValueError('请选择有效QQ号及AI / bot / 真人 / 自动判断')
        if not isinstance(note,str) or len(note)>200:raise ValueError('账号说明最多200字')
        if aliases is not None:
            if not isinstance(aliases,list) or len(aliases)>10 or any(not isinstance(name,str) or not name.strip() or len(name)>40 for name in aliases):
                raise ValueError('额外昵称最多10个，每个1～40字')
            aliases=list(dict.fromkeys(name.strip() for name in aliases))
        with locked(self.folder/'member-labels.lock'):
            data=self.read()
            if label=='clear':
                previous=data['members'].pop(account,{})
                kept=aliases if aliases is not None else self.effective(previous)['aliases']
                row={'aliases':kept,'rule_after':time.time(),'updated_at':time.time()}
                if row:data['members'][account]=row
                self.save(data);return self.effective(row)
            row=data['members'].setdefault(account,{})
            old=self.effective(row)
            row.update(manual_label='' if label=='auto' else label,updated_at=time.time())
            if note!=old['note']:
                row.update(note=note,manual_note_set=True);row.pop('model_note',None)
            if aliases is not None and aliases!=old['aliases']:
                row.update(aliases=aliases,manual_aliases_set=True);row.pop('model_aliases',None)
            self.save(data)
        return self.effective(row)
    @staticmethod
    def effective(row):
        manual=row.get('manual_label','');automatic=row.get('auto_label','');model=row.get('model_label','')
        manual_note=bool(row.get('manual_note_set') or row.get('note'))
        manual_aliases=bool(row.get('manual_aliases_set') or row.get('aliases'))
        return {'label':manual or model or automatic or '', 'source':'manual' if manual else 'model' if model else 'auto' if automatic else 'unmarked',
            'manual_label':manual,'auto_label':automatic,'note':row.get('note','') if manual_note else row.get('model_note',''),
            'note_source':'manual' if manual_note else 'model' if row.get('model_note') else 'unmarked',
            'aliases':row.get('aliases',[]) if manual_aliases else row.get('model_aliases',[]),
            'aliases_source':'manual' if manual_aliases else 'model' if row.get('model_aliases') else 'unmarked',
            'model_history':row.get('model_history',[])[-6:],
            'prefix_counts':row.get('prefix_counts',{}),
            'observations':len(row.get('evidence',[])),'evidence':row.get('evidence',[])[-6:]}
    def facts(self):
        return {account:self.effective(row) for account,row in self.read().get('members',{}).items()}
    def observe(self,item,history,cfg):
        if not cfg.get('member_auto_bot_marking_enabled',True):return False
        account=qq_id(item.get('_uid'));message_id=str(item.get('_id',''))
        if not account or not message_id:return False
        window=cfg.get('member_auto_bot_window_messages',200)
        prefixes=cfg.get('member_auto_bot_command_prefixes',COMMAND_PREFIXES)
        with locked(self.folder/'member-labels.lock'):
            data=self.read()
            messages=data.get('rule_messages')
            if messages is None:messages=[rule_message(row,prefixes) for row in list(history)[-window:] if row.get('_id')]
            else:
                known={row['id'] for row in messages};last_time=max((row['time'] for row in messages),default=0)
                messages.extend(rule_message(row,prefixes) for row in list(history)[-window:]
                    if row.get('_id') and str(row['_id']) not in known and row.get('_time',0)>=last_time)
            if message_id not in {row['id'] for row in messages}:messages.append(rule_message(item,prefixes))
            messages=sorted(messages,key=lambda row:row['time'])[-window:];data['rule_messages']=messages
            for message in messages:message['command']=command_start(message.get('start',''),prefixes)
            changed=False
            grouped={}
            for entry in response_evidence(messages):
                uid=entry.pop('account');grouped.setdefault(uid,[]).append(entry)
            # Refresh the rolling evidence for previously seen responders too.
            accounts=set(data['members'])|set(grouped)
            for uid in accounts:
                evidence=[entry for entry in grouped.get(uid,[]) if entry['time']>data['members'].get(uid,{}).get('rule_after',0)]
                if not evidence and uid not in data['members']:continue
                row=data['members'].setdefault(uid,{})
                row['evidence']=evidence
                counts={}
                for entry in evidence:counts[entry['prefix']]=counts.get(entry['prefix'],0)+1
                row['prefix_counts']=counts
                if max(counts.values(),default=0)>=cfg.get('member_auto_bot_min_observations',10) and row.get('auto_label')!='bot':
                    row.update(auto_label='bot',updated_at=time.time());changed=changed or uid==account
            self.save(data)
            return changed


def store(root,group):
    cfg=Config(root).load();group=int(group)
    if group not in cfg['chat_group_whitelist']:raise ValueError('该群不在白名单内')
    return MemberLabels(runtime(root,group,cfg))


def view(root,group,query=''):
    labels=store(root,group);facts=labels.facts()
    roster=read_json(labels.folder/'group-identities.json').get('members',[])
    members={str(row['qq_id']):row for row in roster if qq_id(row.get('qq_id'))}
    for account in facts:members.setdefault(account,{'qq_id':account})
    rows=[]
    for account,member in members.items():
        row={**member,**facts.get(account,labels.effective({}))}
        if query.casefold() in json.dumps(row,ensure_ascii=False).casefold():rows.append(row)
    rows.sort(key=lambda row:(not bool(row['label']),row.get('card') or row.get('nickname') or row['qq_id']))
    return {'members':rows[:200],'matched':len(rows),'total':len(members),'limit':200}
