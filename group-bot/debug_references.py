"""QQ quote identifiers and bounded previews for the local debugger."""
import json
from pathlib import Path


def message_metadata(item, history=()):
    data={'user_id':str(item.get('_uid','')), 'at_ids':list(item.get('_at_ids',[]))[:8]}
    count=item.get('_image_count',len(item.get('_images',[])))
    count=min(2,count) if isinstance(count,int) and not isinstance(count,bool) else 0
    if count:data['image_count']=count
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
    from image_archive import ImageArchive, SummaryOverrides, replace_summaries, message_image_keys
    from sticker_filter import approved
    from emoji_assets import asset_path
    cache=ImageMemory(Path(runtime)/'image-memory.json');cache.configure(cfg)
    archive=ImageArchive(runtime);overrides=SummaryOverrides(runtime)
    def read(name):
        try:return json.loads((Path(shared_root)/name).read_text(encoding='utf-8'))
        except (ValueError,OSError):return {}
    hashes,index,rejected=read('hashes.json'),read('index.json'),read('rejected.json')
    result=[]
    for original in records:
        row=dict(original);keys=cache.data['messages'].get(str(row.get('message_id','')),[]) or row.get('image_hashes',[])
        if not keys:keys=message_image_keys(row.get('group_id',cfg['group_id']),row.get('message_id',''),row.get('image_count',0))
        if keys:
            pictures=[]
            summaries=[]
            for key in keys[:2]:
                digest=archive.digest(key,cache.data['sources'].get(key,{}).get('digest'))
                sticker_id=hashes.get(digest);sticker=index.get(sticker_id,{})
                if not sticker and len(keys)==1:
                    native=next((item for item in index.values() if item.get('kind')=='market_face'
                        and str(item.get('source_message_id',''))==str(row.get('message_id'))),None)
                    if native:sticker=native;sticker_id=native['id']
                accepted=bool(sticker.get('kind')=='market_face' or (approved(sticker) and asset_path(sticker,'path',shared_root).is_file()))
                refusal=rejected.get(sticker_id,{})
                override=overrides.get(key,digest);summary=(override or {}).get('summary') or cache.lookup(key=key)
                summaries.append(summary)
                pictures.append({'key':key,'hash':digest or key,**cache.frequency(key=key),
                    'summary_available':bool(summary),'summary':summary,
                    'summary_revision':(override or {}).get('revision',''),'summary_origin':'operator' if override else 'model',
                    'original_cached':bool(digest and any((archive.root/(digest+'.'+ext)).is_file() for ext in ('gif','png','jpeg','webp'))),
                    'sticker_state':'approved' if accepted else 'rejected' if refusal else 'not_collected',
                    'asset_type':refusal.get('asset_type') or sticker.get('asset_type',''),
                    'sticker_id':sticker_id if accepted else '',
                    'emotion':sticker.get('emotion','') if accepted else '',
                    'use_when':sticker.get('use_when','') if accepted else ''})
            row['image_info']=pictures
            row['text']=replace_summaries(row.get('text',''),summaries)
        result.append(row)
    return result
