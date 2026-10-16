"""Independent rolling chat and execution windows; never let logs evict chat."""
from debug_references import text_chars,message_metadata
from recent_chat_journal import RecentChatJournal


def lane(row):return 'chat' if row.get('kind') in {'sent','received'} else 'flow'


def bounded(records,cfg):
    totals={'chat':0,'flow':0};counts={'chat':0,'flow':0};closed=set();kept=[]
    limit=cfg['console_max_text_chars'];capacity=cfg['console_max_records']
    for original in reversed(list(records)):
        if original.get('kind')=='sent' and not original.get('message_id') and original.get('action')=='forward_group_single_msg':
            original={**original,'kind':'stage','level':'INFO','text':'原生表情已发送，等待QQ消息ID；'+str(original.get('text',''))}
            original.pop('image_count',None)
            original.pop('image_hashes',None)
        name=lane(original)
        if name in closed:continue
        row=original;size=text_chars(row)
        if counts[name]==0 and size>limit:
            # An oversized newest event must not empty its entire window.
            row={**row,'text':str(row.get('text',''))[:limit],'text_truncated':True}
            if row.get('quote'):
                row['quote']={**row['quote'],'text':str(row['quote'].get('text',''))[:max(0,limit-len(row['text']))]}
            size=text_chars(row)
        if counts[name]>=capacity or totals[name]+size>limit:
            closed.add(name);continue
        kept.append(row);totals[name]+=size;counts[name]+=1
    return list(reversed(kept))


def recover_chat(records,folder,cfg,hidden=(),group_id=None):
    """Read the existing recent journal to bridge chat evicted by older versions."""
    seen={str(row.get('message_id')) for row in records if lane(row)=='chat'}
    journal=RecentChatJournal(folder/'recent-chat.jsonl',cfg.get('recent_chat_journal_max_messages',500),
        cfg.get('recent_chat_journal_hours',24)).load()
    result=list(records)
    for item in journal:
        key=str(item.get('_id',''))
        if not key or key in seen or key in hidden:continue
        result.append({'kind':'sent' if item.get('is_bot') else 'received','time':item['_time'],
            'message_id':key,'sender':item.get('sender',''),'text':item.get('text',''),
            'group_id':group_id if group_id is not None else cfg['group_id'],'historical':True,'manual':bool(item.get('_manual')),
            'image_hashes':item.get('_image_hashes',[]),**message_metadata(item,journal)})
        seen.add(key)
    return bounded(sorted(result,key=lambda row:row.get('time',0)),cfg)
