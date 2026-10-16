"""Per-group account labels with operator precedence and auditable bot hints."""
import json
from pathlib import Path
import re
import time

from console_config import Config,read_json,runtime
from group_identity import qq_id
from image_archive import locked
from storage_io import atomic_write_text

LABELS={'AI','bot','human'}


class MemberLabels:
    def __init__(self,folder):
        self.folder=Path(folder);self.path=self.folder/'member-labels.json'
    def read(self):return read_json(self.path,{'members':{}})
    def save(self,data):atomic_write_text(self.path,json.dumps(data,ensure_ascii=False,indent=2))
    def mark(self,account,label,note='',aliases=None):
        account=qq_id(account)
        if not account or label not in LABELS|{'auto','clear'}:raise ValueError('请选择有效QQ号及AI / bot / 真人 / 自动判断')
        if not isinstance(note,str) or len(note)>200:raise ValueError('标记说明最多200字')
        if aliases is not None:
            if not isinstance(aliases,list) or len(aliases)>10 or any(not isinstance(name,str) or not name.strip() or len(name)>40 for name in aliases):
                raise ValueError('额外昵称最多10个，每个1～40字')
            aliases=list(dict.fromkeys(name.strip() for name in aliases))
        with locked(self.folder/'member-labels.lock'):
            data=self.read()
            if label=='clear':
                previous=data['members'].pop(account,{})
                kept=aliases if aliases is not None else previous.get('aliases',[])
                row={'aliases':kept,'updated_at':time.time()} if kept else {}
                if row:data['members'][account]=row
                self.save(data);return self.effective(row)
            row=data['members'].setdefault(account,{})
            row.update(manual_label='' if label=='auto' else label,note=note,updated_at=time.time())
            if aliases is not None:row['aliases']=aliases
            self.save(data)
        return self.effective(row)
    @staticmethod
    def effective(row):
        manual=row.get('manual_label','');automatic=row.get('auto_label','')
        return {'label':manual or automatic or '', 'source':'manual' if manual else 'auto' if automatic else 'unmarked',
            'manual_label':manual,'auto_label':automatic,'note':row.get('note',''),
            'aliases':row.get('aliases',[]),
            'observations':len(row.get('evidence',[])),'evidence':row.get('evidence',[])[-6:]}
    def facts(self):
        return {account:self.effective(row) for account,row in self.read().get('members',{}).items()}
    def observe(self,item,history,cfg):
        if not cfg.get('member_auto_bot_marking_enabled',True) or item.get('is_bot'):return False
        account=qq_id(item.get('_uid'));message_id=str(item.get('_id',''))
        if not account or not message_id:return False
        timestamp=item.get('_time',time.time())
        commands=[entry for entry in list(history)[-30:] if not entry.get('is_bot')
            and str(entry.get('_uid'))!=account and 0<=timestamp-entry.get('_time',0)<=20
            and re.match(r'^\s*(?:@\d+\s*)?/(?:pgr|a)(?=\s|$)',str(entry.get('text','')),re.I)]
        anchored=[entry for entry in commands if str(item.get('_reply_id',''))==str(entry.get('_id',''))
            or str(entry.get('_uid')) in set(map(str,item.get('_at_ids',[])))]
        # An unquoted /pgr image needs one unambiguous recent request. /a text
        # needs a real reply/@ anchor, not just an unrelated line nearby.
        candidates=anchored or ([commands[0]] if len(commands)==1 and item.get('_images')
            and re.match(r'^\s*(?:@\d+\s*)?/pgr(?=\s|$)',str(commands[0].get('text','')),re.I) else [])
        if len(candidates)!=1:return False
        command=candidates[0];command_id=str(command.get('_id',''))
        if not command_id or not (item.get('_images') or item.get('text','').strip()):return False
        with locked(self.folder/'member-labels.lock'):
            data=self.read();row=data['members'].setdefault(account,{})
            evidence=[entry for entry in row.get('evidence',[]) if timestamp-entry.get('time',0)<30*86400]
            if any(entry['command_id']==command_id for entry in evidence):return False
            evidence.append({'command_id':command_id,'response_id':message_id,'time':timestamp,
                'command':'/pgr' if '/pgr' in command['text'].lower() else '/a',
                'anchor':'reply_or_at' if anchored else 'unique_recent_image'})
            row['evidence']=evidence[-24:];before=row.get('auto_label','')
            if len(evidence)>=cfg.get('member_auto_bot_min_observations',3):row['auto_label']='bot'
            row['updated_at']=time.time();self.save(data)
            return row.get('auto_label','')!=before


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
