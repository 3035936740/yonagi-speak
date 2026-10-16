"""Authenticated local avatar proxy, with fixed QQ hosts and bounded caching."""
import hashlib
import io
import json
from pathlib import Path
import threading
import time

import httpx
from PIL import Image
from console_config import Config,read_json,runtime
from group_identity import qq_id

_lock=threading.Lock()
_failures={}


def enrich_users(records,folder,cfg):
    journal={}
    path=Path(folder)/'recent-chat.jsonl'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:row=json.loads(line);journal[str(row.get('_id',''))]=row
            except ValueError:pass
    # Names are deliberately never used to guess account IDs.
    result=[]
    for original in records:
        row=dict(original);user=qq_id(row.get('user_id')) or qq_id(journal.get(str(row.get('message_id','')),{}).get('_uid'))
        if user:row['user_id']=user
        result.append(row)
    return result


def avatar(root,group,kind,account):
    root=Path(root);cfg=Config(root).load();group=int(group);account=qq_id(account)
    if group not in cfg['chat_group_whitelist'] or not account or kind not in {'group','user'}:raise ValueError('头像参数无效')
    if kind=='group' and account!=str(group):raise ValueError('群头像不在当前授权群内')
    if kind=='user':
        folder=runtime(root,group,cfg)
        allowed={qq_id(row.get('qq_id')) for row in read_json(folder/'group-identities.json').get('members',[])}
        for file in ['recent-chat.jsonl','debug-events.jsonl']:
            path=folder/file
            if path.exists():
                for line in path.read_text(encoding='utf-8').splitlines():
                    try:row=json.loads(line);allowed.add(qq_id(row.get('_uid') or row.get('user_id')))
                    except ValueError:pass
        if account not in allowed:raise ValueError('该账号不在本群可见记录中')
    key=kind+'-'+account;failure_key=(str(root.resolve()),key)
    folder=root/'runtime/avatar-cache';path=folder/(key+'.bin')
    with _lock:
        if path.exists() and time.time()-path.stat().st_mtime<21600:return path.read_bytes(),'image/png'
        if time.time()-_failures.get(failure_key,0)<60:raise ValueError('头像暂不可用')
    if not Config(root,group).load().get('avatar_remote_enabled',False):raise ValueError('头像联网未启用，使用默认头像')
    url=(f'https://p.qlogo.cn/gh/{account}/{account}/100' if kind=='group' else
         f'https://q1.qlogo.cn/g?b=qq&nk={account}&s=100')
    try:
        with httpx.Client(timeout=4,trust_env=False,follow_redirects=False) as client:
            with client.stream('GET',url) as response:
                response.raise_for_status()
                if not response.headers.get('content-type','').startswith('image/'):raise ValueError('头像不是图片')
                data=bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data)>1024*1024:raise ValueError('头像文件过大')
        with Image.open(io.BytesIO(data)) as image:
            if image.width*image.height>4000000:raise ValueError('头像尺寸过大')
            image.thumbnail((128,128));output=io.BytesIO();image.convert('RGBA').save(output,format='PNG')
        raw=output.getvalue()
        folder.mkdir(parents=True,exist_ok=True)
        # Cache writes are private and atomic; extensions never come from a URL.
        temp=folder/(key+'-'+hashlib.sha256(raw).hexdigest()[:12]+'.tmp')
        with _lock:temp.write_bytes(raw);temp.replace(path)
        return raw,'image/png'
    except (httpx.HTTPError,OSError,ValueError,Image.DecompressionBombError) as exc:
        with _lock:_failures[failure_key]=time.time()
        raise ValueError('头像暂不可用，使用默认头像') from exc
