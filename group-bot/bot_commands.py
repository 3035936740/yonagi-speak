"""Message-level bot invocations, independent of the caller's account type."""
import re
import unicodedata
from console_config import read_json
from member_labels import MemberLabels,COMMAND_PREFIXES,command_start
from image_archive import locked
from storage_io import atomic_write_text
import json
from pathlib import Path


def explicit_self(item,self_id):
    return bool(item.get('at_bot') or str(self_id) in set(map(str,item.get('_at_ids',[]))))


def invocation(item,cfg,routes=None,self_id=''):
    if item.get('is_bot') or explicit_self(item,self_id):return None
    if item.get('_bot_command'):return item['_bot_command']
    text=unicodedata.normalize('NFKC',str(item.get('text',''))).strip()
    prefixes=cfg.get('member_auto_bot_command_prefixes',COMMAND_PREFIXES)
    found=command_start(text,prefixes)
    if found:return {'prefix':found['prefix'],'source':'configured_prefix'}
    stripped=re.sub(r'^(?:@\d+\s*)+','',text).lstrip()
    for prefix,entry in (routes or {}).items():
        if len(entry.get('commands',[]))>=2 and re.match(r'^'+re.escape(prefix)+r'\s+\S',stripped,re.I):
            return {'prefix':prefix,'source':'known_bot_responses','bot_ids':entry.get('bot_ids',[])}
    return None


def annotate(folder,history,batch,cfg,self_id):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    known={uid for uid,row in MemberLabels(folder).facts().items() if row['label']=='bot'}
    path=folder/'bot-command-routes.json';window=cfg.get('member_auto_bot_window_messages',200)
    rows={str(item.get('_id')):item for item in list(history)[-window:]+list(batch) if item.get('_id')}
    ordered=sorted(rows.values(),key=lambda row:row.get('_time',0))[-window:]
    with locked(path.with_suffix('.lock')):
        routes=read_json(path,{})
        for index,response in enumerate(ordered):
            uid=str(response.get('_uid',''))
            if uid not in known:continue
            candidates=[row for row in ordered[:index] if str(row.get('_uid',''))!=uid and not row.get('is_bot')
                and 0<=response.get('_time',0)-row.get('_time',0)<=60
                and (str(response.get('_reply_id',''))==str(row['_id']) or str(row.get('_uid','')) in set(map(str,response.get('_at_ids',[]))))]
            exact=[row for row in candidates if str(response.get('_reply_id',''))==str(row['_id'])]
            if exact:candidates=exact
            if not candidates and index and not response.get('_reply_id') and not response.get('_at_ids'):
                previous=ordered[index-1]
                if not previous.get('is_bot') and str(previous.get('_uid',''))!=uid and 0<=response.get('_time',0)-previous.get('_time',0)<=30:
                    candidates=[previous]
            if not candidates:continue
            request=candidates[-1]
            if explicit_self(request,self_id):continue
            text=re.sub(r'^(?:@\d+\s*)+','',unicodedata.normalize('NFKC',str(request.get('text',''))).strip())
            match=re.match(r'^([a-zA-Z]{1,12})\s+([a-zA-Z][\w-]{0,30})(?:\s|$)',text)
            if not match:continue
            prefix=match.group(1).casefold()
            bases=cfg.get('member_auto_bot_command_prefixes',COMMAND_PREFIXES)
            if not any(prefix.startswith(str(base).casefold()) for base in bases if str(base).isascii() and str(base).isalpha()):continue
            entry=routes.setdefault(prefix,{'commands':[],'bot_ids':[]})
            identity=str(request['_id'])
            if identity not in entry['commands']:entry['commands']=(entry['commands']+[identity])[-window:]
            entry['bot_ids']=list(dict.fromkeys(entry['bot_ids']+[uid]))[-10:]
            request['_bot_command']={'prefix':prefix,'source':'known_bot_response','bot_ids':[uid]}
        routes={prefix:entry for prefix,entry in routes.items() if set(entry.get('bot_ids',[]))&known}
        atomic_write_text(path,json.dumps(routes,ensure_ascii=False))
    for item in list(history)+list(batch):
        found=invocation(item,cfg,routes,self_id)
        if found:item['_bot_command']=found
    return routes
