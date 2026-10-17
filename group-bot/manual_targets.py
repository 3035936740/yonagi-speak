"""Resolve explicit operator reply/mention targets only within the selected group."""
import json
import re
from console_config import read_json,runtime,deleted_ids


def validate(reply_id=None,at_ids=None):
    reply='' if reply_id is None else str(reply_id)
    if reply and not re.fullmatch(r'-?\d{1,20}',reply):raise ValueError('无效引用消息ID')
    if at_ids is None:at_ids=[]
    if not isinstance(at_ids,list) or len(at_ids)>8:raise ValueError('最多@8位群友')
    if any(type(uid) not in (str,int) or not re.fullmatch(r'[1-9]\d{4,14}',str(uid)) for uid in at_ids):
        raise ValueError('请选择有效群友，不支持@全体成员')
    return reply,list(dict.fromkeys(str(uid) for uid in at_ids))


def resolve(root,group,reply,at_ids):
    folder=runtime(root,group);sources={};names={};hidden=deleted_ids(folder)
    for member in read_json(folder/'group-identities.json').get('members',[]):
        uid=str(member.get('qq_id',''))
        names[uid]=str(member.get('card') or member.get('nickname') or uid)[:80]
    for filename in ('recent-chat.jsonl','debug-events.jsonl'):
        try:lines=(folder/filename).read_text(encoding='utf-8').splitlines()
        except OSError:continue
        for line in lines:
            try:row=json.loads(line)
            except ValueError:continue
            if not isinstance(row,dict):continue
            if filename=='debug-events.jsonl':
                if row.get('kind') not in {'received','sent'} or row.get('group_id',group)!=group:continue
                identity=str(row.get('message_id',''));uid=str(row.get('user_id',''))
            else:
                if row.get('_group_id',group)!=group:continue
                identity=str(row.get('_id',''));uid=str(row.get('_uid',''))
            if identity in hidden:continue
            if row.get('sender'):names[uid]=str(row['sender'])[:80]
            sources[identity]={'message_id':identity,'user_id':uid,'sender':str(row.get('sender',''))[:80],
                'text':str(row.get('text',''))[:180],'found':True}
    if reply and reply not in sources:raise ValueError('当前群找不到这条原消息，请重新选择回复目标')
    if any(uid not in names for uid in at_ids):raise ValueError('@目标不在当前群本地资料中，请刷新后重新选择')
    return {'reply_id':reply,'at_ids':at_ids,'at_names':{uid:names[uid] for uid in at_ids},
        **({'quote':sources[reply]} if reply else {})}
