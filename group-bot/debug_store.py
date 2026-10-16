"""Bounded local memory CRUD and shared sticker inspection for the debugger."""
import base64
import io
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
import math
from contextlib import contextmanager
from PIL import Image
from console_config import Config, runtime, atomic
from emoji_library import EmojiLibrary
from sticker_filter import approved, EMOTIONS

TABLES={'notes':'记忆 / 表达','people':'群友资料','sources':'学习来源',
    'note_metadata':'记忆置信与反馈','supersessions':'事实修订关系'}


def group_folder(root,group):
    cfg=Config(root).load();group=int(group)
    if group not in cfg['chat_group_whitelist']:raise ValueError('该群不在白名单内')
    return runtime(root,group,cfg)


@contextmanager
def connection(root,group):
    path=group_folder(root,group)/'group-memory.sqlite3'
    if not path.is_file():raise ValueError('该群尚未建立记忆数据库')
    db=sqlite3.connect(path,timeout=5);db.row_factory=sqlite3.Row
    try:yield db
    finally:db.close()


def database_view(root,group,table='notes',query='',offset=0):
    if table not in TABLES:raise ValueError('未知数据表')
    offset=max(0,min(100000,int(offset)));query=str(query)[:200]
    path=group_folder(root,group)/'group-memory.sqlite3'
    if not path.exists():return {'tables':TABLES,'table':table,'columns':[],'rows':[],'total':0,'offset':offset,'limit':50,'revision':'missing'}
    with connection(root,group) as db:
        columns=[dict(row) for row in db.execute('PRAGMA table_info("'+table+'")')]
        names=[row['name'] for row in columns]
        if not names:return {'tables':TABLES,'table':table,'columns':[],'rows':[],'total':0,'offset':0,'limit':50,'revision':'missing-table'}
        condition=' OR '.join('CAST("'+name+'" AS TEXT) LIKE ? ESCAPE \'\\\'' for name in names)
        pattern='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        params=[pattern]*len(names)
        total=db.execute('SELECT COUNT(*) FROM "'+table+'" WHERE '+condition,params).fetchone()[0]
        rows=[dict(row) for row in db.execute('SELECT rowid AS _rowid,* FROM "'+table+'" WHERE '+condition+' ORDER BY rowid DESC LIMIT 50 OFFSET ?',params+[offset])]
    return {'tables':TABLES,'table':table,'columns':columns,'rows':rows,'total':total,'offset':offset,'limit':50,
        'revision':str(path.stat().st_mtime_ns)}


def validate_values(table,values,columns):
    if not isinstance(values,dict) or set(values)-set(columns):raise ValueError('包含未知数据字段')
    for key,value in values.items():
        if isinstance(value,(dict,list)):values[key]=json.dumps(value,ensure_ascii=False)
        elif value is not None and not isinstance(value,(str,int,float)):raise ValueError('字段必须是文本或数字')
        if len(str(values[key]))>6000:raise ValueError('单个字段上限6000字符')
        if value is not None and any(word in columns[key]['type'].upper() for word in ('INT','REAL')):
            try:number=float(value)
            except (ValueError,TypeError):raise ValueError(key+' 必须为数字')
            if not math.isfinite(number) or isinstance(value,bool):raise ValueError(key+' 必须为有限数字')
            if 'INT' in columns[key]['type'].upper() and not number.is_integer():raise ValueError(key+' 必须为整数')
            values[key]=int(number) if 'INT' in columns[key]['type'].upper() else number
    if table=='notes':
        if 'kind' in values and values['kind'] not in {'expression','fact','preference','event','episode','jargon'}:raise ValueError('未知记忆类型')
        for key in ('keywords','evidence'):
            if key in values and not isinstance(json.loads(values[key] or '[]'),list):raise ValueError(key+' 必须是JSON数组')
    for key in ('count','uses','positive','negative','last_used','stamp'):
        if key in values and float(values[key])<0:raise ValueError('计数和时间不能为负数')
    if table=='sources' and 'learned' in values and values['learned'] not in {0,1}:raise ValueError('已学习标记必须是0或1')
    if table=='note_metadata':
        if 'confidence' in values and not 0<=float(values['confidence'])<=1:raise ValueError('置信度范围0~1')
        for key in ('uses','positive','negative','last_used'):
            if key in values and float(values[key])<0:raise ValueError('统计数不能为负数')
    return values


def database_mutate(root,group,table,mode,values=None,row_id=None,expected=None):
    if table not in TABLES or mode not in {'create','update','delete'}:raise ValueError('未知数据库操作')
    folder=group_folder(root,group)
    with connection(root,group) as db:
        columns={row['name']:dict(row) for row in db.execute('PRAGMA table_info("'+table+'")')}
        values=validate_values(table,dict(values or {}),columns)
        if mode=='create' and table=='notes':values.setdefault('kind','fact')
        required='content' if table=='notes' else 'name' if table=='people' else 'text' if table=='sources' else None
        if required and (mode=='create' or required in values) and not str(values.get(required,'') or '').strip():raise ValueError('内容不能为空')
        db.execute('BEGIN IMMEDIATE')
        existing=None
        if mode!='create':
            existing=db.execute('SELECT rowid AS _rowid,* FROM "'+table+'" WHERE rowid=?',(int(row_id),)).fetchone()
            if not existing:raise ValueError('记录已删除，请刷新')
            if expected is not None and dict(existing)!=expected:raise ValueError('记录已被后台更新，请刷新后编辑')
            if mode=='update':
                for key,meta in columns.items():
                    if meta['pk'] and key in values and values[key]!=existing[key]:raise ValueError('主键不能修改，请新建记录')
        if mode!='delete' and table in {'note_metadata','supersessions'}:
            candidate={**(dict(existing) if existing else {}),**values}
            ids=[candidate.get('note_id')] if table=='note_metadata' else [candidate.get('old_id'),candidate.get('new_id')]
            linked=[db.execute('SELECT subject,kind FROM notes WHERE id=?',(identity,)).fetchone() for identity in ids]
            if any(note is None for note in linked):raise ValueError('关联的记忆记录不存在')
            if table=='supersessions':
                if ids[0]==ids[1] or dict(linked[0])!=dict(linked[1]):raise ValueError('事实修订必须关联同一群友的同类记忆')
                cursor=ids[1];visited={ids[0]}
                while cursor:
                    if cursor in visited:raise ValueError('事实修订关系不能形成循环')
                    visited.add(cursor)
                    next_row=db.execute('SELECT new_id FROM supersessions WHERE old_id=?',(cursor,)).fetchone()
                    cursor=next_row[0] if next_row else None
        backup=folder/'database-backups';backup.mkdir(exist_ok=True)
        # Use a separate read snapshot so backup does not wait on our own write lock.
        snapshot=sqlite3.connect(folder/'group-memory.sqlite3');saved=sqlite3.connect(backup/(str(time.time_ns())+'.sqlite3'))
        try:snapshot.backup(saved)
        finally:snapshot.close();saved.close()
        if mode=='create':
            for key,meta in columns.items():
                if meta['pk'] and key not in values:values[key]=uuid.uuid4().hex[:20]
            if 'stamp' in columns and 'stamp' not in values:values['stamp']=time.time()
            if table=='notes':
                values.setdefault('subject','');values.setdefault('keywords','[]');values.setdefault('evidence','[]');values.setdefault('count',1)
            if not values:raise ValueError('新记录不能为空')
            names=list(values)
            db.execute('INSERT INTO "'+table+'" ('+','.join('"'+name+'"' for name in names)+') VALUES ('+','.join('?' for _ in names)+')',[values[name] for name in names])
        elif mode=='update':
            if not values:raise ValueError('没有修改字段')
            names=list(values)
            db.execute('UPDATE "'+table+'" SET '+','.join('"'+name+'"=?' for name in names)+' WHERE rowid=?',[values[name] for name in names]+[int(row_id)])
        else:
            db.execute('DELETE FROM "'+table+'" WHERE rowid=?',(int(row_id),))
            if table=='notes':
                db.execute('DELETE FROM note_metadata WHERE note_id=?',(existing['id'],))
                db.execute('DELETE FROM supersessions WHERE old_id=? OR new_id=?',(existing['id'],existing['id']))
        if table=='sources' and mode in {'update','delete'}:
            for note in db.execute('SELECT id,evidence FROM notes').fetchall():
                try:evidence=json.loads(note['evidence'] or '[]')
                except ValueError:continue
                if any(isinstance(source,dict) and source.get('id')==existing['id'] for source in evidence):
                    db.execute('DELETE FROM notes WHERE id=?',(note['id'],))
                    db.execute('DELETE FROM note_metadata WHERE note_id=?',(note['id'],))
                    db.execute('DELETE FROM supersessions WHERE old_id=? OR new_id=?',(note['id'],note['id']))
            if mode=='update':db.execute('UPDATE sources SET learned=0 WHERE rowid=?',(int(row_id),))
        db.commit()
    return {'ok':True,'message':'数据库已保存，修改前快照已备份'}


def library(root):return EmojiLibrary(Path(root)/'shared-emoji-library',Path(root)/'qq-faces.json',shared=True)


def sticker_view(root,query='',offset=0):
    lib=library(root);rows=[]
    for key,item in lib.items.items():
        market=item.get('kind')=='market_face';name=str(item.get('emoji_name') or item.get('label') or item.get('data',{}).get('summary') or '未命名').strip('[]')
        title='['+str(item.get('package_name') or '未获取表情包名')+'_'+name+']' if market else str(item.get('description') or item.get('label') or key)
        rows.append({'id':key,'kind':item.get('kind'),'title':title,'label':item.get('label',''),
            'description':item.get('description',''),'emotion':item.get('emotion',''),'use_when':item.get('use_when',''),
            'package_name':item.get('package_name',''),'emoji_name':item.get('emoji_name',name),
            'state':'approved' if market or approved(item) else 'unreviewed','uses':item.get('uses',0),
            'content_hash':item.get('content_hash',''),'time':item.get('time',0)})
    for key,item in lib.rejected.items():
        rows.append({'id':key,'kind':'rejected','title':'未收录 · '+item.get('asset_type','未知'),
            'state':'rejected','asset_type':item.get('asset_type',''),'review_seen_count':item.get('review_seen_count',0),'time':item.get('time',0)})
    incoming=lib.root/'incoming'
    for path in incoming.glob('*.json'):
        try:meta=json.loads(path.read_text(encoding='utf-8'))
        except (ValueError,OSError):continue
        rows.append({'id':path.stem,'kind':'pending','title':str(meta.get('label') or '待审核图片')[:80],'state':'pending','time':meta.get('time',0)})
    rows=sorted(rows,key=lambda item:item.get('time',0),reverse=True)
    filtered=[row for row in rows if str(query).lower() in json.dumps(row,ensure_ascii=False).lower()]
    offset=max(0,int(offset));return {'rows':filtered[offset:offset+24],'total':len(filtered),'offset':offset,'limit':24,'emotions':EMOTIONS}


def sticker_image(root,key):
    if not re.fullmatch(r'[smu][a-f0-9]{16}',str(key)):raise ValueError('无效表情ID')
    lib=library(root)
    if str(key).startswith('u'):
        incoming=lib.root/'incoming';manifest=incoming/(key+'.json')
        if not manifest.is_file():raise ValueError('图片已处理，请刷新')
        data=json.loads(manifest.read_text(encoding='utf-8'));path=(incoming/data['path']).resolve()
        if path.parent!=incoming.resolve():raise ValueError('图片文件不可用')
    else:
        item=lib.items.get(key)
        if not item or not approved(item):raise ValueError('图片尚未审核通过或已删除')
        from emoji_assets import managed_asset_path
        path=managed_asset_path(item,'path',lib.root)
    if not path.is_file() or path.stat().st_size>2*1024*1024:raise ValueError('图片文件不可用')
    raw=path.read_bytes()
    with Image.open(io.BytesIO(raw)) as image:mime=Image.MIME.get(image.format,'application/octet-stream');image.verify()
    return raw,mime


def sticker_mutate(root,mode,key='',values=None):
    lib=library(root)
    if mode=='create':
        values=values or {};encoded=values.get('image_base64','')
        if not isinstance(encoded,str) or len(encoded)>2800000:raise ValueError('图片上限2MB')
        try:raw=base64.b64decode(encoded,validate=True)
        except ValueError:raise ValueError('无效图片编码')
        if len(raw)>2*1024*1024:raise ValueError('图片上限2MB')
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {'GIF','PNG','JPEG','WEBP'} or image.width*image.height>20000000:raise ValueError('不支持的图片')
            extension=image.format.lower();image.verify()
        import hashlib
        digest=hashlib.sha256(raw).hexdigest()
        with lib.transaction():
            old=lib.hashes.get(digest)
            if old and old in lib.items:return {'ok':True,'message':'相同图片已入库，未重复保存'}
            if old:
                lib.hashes.pop(digest,None);lib.rejected.pop(old,None);lib.save()
        incoming=lib.root/'incoming';incoming.mkdir(exist_ok=True);identity='u'+digest[:16]
        path=incoming/(identity+'.'+extension)
        if not path.exists():path.write_bytes(raw)
        atomic(incoming/(identity+'.json'),{'path':path.name,'label':str(values.get('label',''))[:80],'time':time.time(),'digest':digest})
        return {'ok':True,'message':'已加入待审核队列，审核通过后才能发送'}
    if mode not in {'update','delete'}:raise ValueError('未知表情库操作')
    if mode=='delete' and re.fullmatch(r'u[a-f0-9]{16}',str(key)):
        incoming=lib.root/'incoming';manifest=incoming/(key+'.json')
        if not manifest.exists():raise ValueError('待审核图片已处理，请刷新')
        data=json.loads(manifest.read_text(encoding='utf-8'));digest=data['digest']
        with lib.transaction():
            canonical='s'+digest[:16];lib.hashes[digest]=canonical;lib.reject(canonical,'manual');lib.save()
        path=(incoming/data['path']).resolve()
        if path.parent==incoming.resolve():path.unlink(missing_ok=True)
        manifest.unlink();return {'ok':True,'message':'已取消收录'}
    with lib.transaction():
        item=lib.items.get(key)
        if not item:raise ValueError('表情已删除或尚未入库')
        backup=lib.root/'admin-backups';backup.mkdir(exist_ok=True)
        atomic(backup/(str(time.time_ns())+'.json'),{'index':lib.items,'hashes':lib.hashes,'aliases':lib.aliases,'rejected':lib.rejected})
        if mode=='delete':
            import shutil
            saved=backup/(key+'-'+str(time.time_ns()));saved.mkdir()
            for field in ('path','preview'):
                from emoji_assets import asset_path
                original=asset_path(item,field,lib.root)
                if item.get(field) and original.is_relative_to(lib.root.resolve()) and original.is_file():shutil.copy2(original,saved/(field+original.suffix))
            lib.reject(key,'manual');lib.save();return {'ok':True,'message':'已移出共享表情库，原图已备份，所有群生效'}
        limits={'label':80,'description':35,'emotion':20,'use_when':30,'package_name':80,'emoji_name':80}
        if not isinstance(values,dict) or set(values)-set(limits):raise ValueError('只能编辑表情名称、情绪和使用说明')
        for name,value in values.items():
            if not isinstance(value,str) or len(value)>limits[name]:raise ValueError('字段长度或类型不正确')
            if name=='emotion' and value not in EMOTIONS:raise ValueError('未知情绪分类')
            item[name]=value
        item['metadata_edited']=time.time()
        lib.save()
    return {'ok':True,'message':'表情资料已保存，所有群共享'}
