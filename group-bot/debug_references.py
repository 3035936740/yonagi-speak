"""QQ quote identifiers and bounded previews for the local debugger."""
import json
from pathlib import Path


def message_metadata(item, history=()):
    data={'user_id':str(item.get('_uid','')), 'at_ids':list(item.get('_at_ids',[]))[:8]}
    reply_id=item.get('_reply_id')
    if not reply_id:return data
    data['reply_id']=str(reply_id)
    source=item.get('_quoted_message') or next((row for row in history
        if str(row.get('_id',''))==str(reply_id)),None)
    if source:
        data['quote']={'message_id':str(reply_id),'sender':str(source.get('sender',''))[:80],
            'text':str(source.get('text',''))[:180], 'found':True}
    return data


def text_chars(row):
    return len(row.get('text',''))+len(row.get('quote',{}).get('text',''))


def enrich_quotes(records, runtime, hidden=()):
    # Older event files omitted reply_id even though the bot journal kept it.
    sources={}
    path=Path(runtime)/'recent-chat.jsonl'
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            try:item=json.loads(line)
            except ValueError:continue
            if str(item.get('_id','')) not in hidden:
                sources[str(item.get('_id',''))]=item
    except OSError:pass
    for row in records:
        if row.get('kind') in {'sent','received'} and row.get('message_id'):
            key=str(row['message_id'])
            sources[key]={**sources.get(key,{}),'sender':row.get('sender',''),
                'text':row.get('text',''), '_reply_id':row.get('reply_id') or sources.get(key,{}).get('_reply_id')}
    result=[]
    for original in records:
        row=dict(original)
        reply_id=row.get('reply_id') or row.get('quote',{}).get('message_id') or sources.get(str(row.get('message_id','')),{}).get('_reply_id')
        if reply_id or '[引用回复]' in row.get('text',''):
            reply_id=str(reply_id or '')
            source=sources.get(reply_id) if reply_id not in hidden else None
            saved=row.get('quote') if reply_id not in hidden else None
            row['reply_id']=reply_id
            row['quote']=({'message_id':reply_id,'found':True,'sender':str(source.get('sender',''))[:80],
                'text':str(source.get('text',''))[:180]} if source else
                dict(saved) if saved and saved.get('found') else {'message_id':reply_id,'found':False})
        result.append(row)
    return result


def enrich_images(records, runtime, shared_root, cfg):
    # Display recognition and sticker admission independently. Reading this
    # metadata neither renews the 2000-message TTL nor admits anything to QQ.
    from image_memory import ImageMemory
    from sticker_filter import approved
    from emoji_assets import asset_path
    cache=ImageMemory(Path(runtime)/'image-memory.json');cache.configure(cfg)
    def read(name):
        try:return json.loads((Path(shared_root)/name).read_text(encoding='utf-8'))
        except (ValueError,OSError):return {}
    hashes,index,rejected=read('hashes.json'),read('index.json'),read('rejected.json')
    result=[]
    for original in records:
        row=dict(original);keys=cache.data['messages'].get(str(row.get('message_id','')),[]) or row.get('image_hashes',[])
        if keys:
            pictures=[]
            for key in keys[:2]:
                digest=cache.data['sources'].get(key,{}).get('digest')
                sticker_id=hashes.get(digest);sticker=index.get(sticker_id,{})
                accepted=bool(approved(sticker) and asset_path(sticker,'path',shared_root).is_file())
                refusal=rejected.get(sticker_id,{})
                pictures.append({'hash':digest or key,**cache.frequency(key=key),
                    'summary_available':bool(cache.lookup(key=key)),
                    'sticker_state':'approved' if accepted else 'rejected' if refusal else 'not_collected',
                    'asset_type':refusal.get('asset_type') or sticker.get('asset_type',''),
                    'sticker_id':sticker_id if accepted else '',
                    'emotion':sticker.get('emotion','') if accepted else '',
                    'use_when':sticker.get('use_when','') if accepted else ''})
            row['image_info']=pictures
        result.append(row)
    return result
