"""Project-relative temporary ownership and bounded cleanup of AI output files."""
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import time
import uuid
import math
from datetime import datetime,timedelta,timezone
from debug_events import emit

log=logging.getLogger(__name__)
CHINA=timezone(timedelta(hours=8))


def created_time(path):
    """An immutable creation stamp wins over a later preview/read/mtime change."""
    try:
        data=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        stamp=data.get('created')
        if data.get('artifact')==path.name and isinstance(stamp,(int,float)) and not isinstance(stamp,bool) and math.isfinite(stamp) and stamp>0:
            return stamp
    except (OSError,ValueError,TypeError):pass
    return path.stat().st_mtime


def next_daily_time(now,clock):
    hour,minute=map(int,clock.split(':'))
    today=datetime.fromtimestamp(now,CHINA).replace(hour=hour,minute=minute,second=0,microsecond=0)
    return (today if today.timestamp()>now else today+timedelta(days=1)).timestamp()


def next_image_expiry(runtime,ttl_seconds,now):
    folder=temp_folder(runtime)
    if not folder.exists():return now+60
    times=[]
    for path in folder.iterdir():
        if path.is_file() and not path.is_symlink() and re.fullmatch(r'[a-f0-9]{32}\.(?:png|jpe?g|webp|gif)',path.name):
            expiry=created_time(path)+ttl_seconds
            times.append(expiry if expiry>now else now+60)
    return min(times) if times else now+60


def generated_root():
    return (Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))).expanduser()/'generated_images').resolve()


def thread_folder(root,thread_id):
    if not isinstance(thread_id,str) or not re.fullmatch(r'[a-fA-F0-9-]{36}',thread_id):
        raise ValueError('Image job has no valid thread ID')
    identity=str(uuid.UUID(thread_id))
    folder=(Path(root)/identity).resolve()
    if folder.parent!=Path(root).resolve():raise ValueError('Image output directory escaped its root')
    return folder


def temp_folder(runtime):
    base=Path(runtime).resolve()
    folder=(base/'temp/ai-images').resolve()
    if not folder.is_relative_to(base):raise ValueError('AI image temp directory escaped runtime')
    return folder


def store_image(runtime,source,thread_id,job_id,root=None):
    root=Path(root or generated_root()).resolve();source=Path(source).resolve(strict=True)
    if source.parent!=thread_folder(root,thread_id):raise ValueError('Image belongs to a different generation thread')
    folder=temp_folder(runtime);folder.mkdir(parents=True,exist_ok=True)
    identity=uuid.UUID(job_id).hex
    suffix=source.suffix.lower()
    if suffix not in {'.png','.jpg','.jpeg','.webp','.gif'}:raise ValueError('Unsupported generated image file')
    path=folder/(identity+suffix);part=path.with_suffix(suffix+'.part')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        shutil.copyfile(source,part)
        if hashlib.sha256(part.read_bytes()).hexdigest()!=digest:raise OSError('Generated image copy changed')
        part.replace(path)
        # Paths in metadata are relative to their respective dynamically located
        # roots, so moving the bot folder does not invalidate its temp image.
        metadata={'artifact':path.name,'source_relative_path':source.relative_to(root).as_posix(),
            'thread_id':thread_id,'sha256':digest,'created':time.time()}
        record=path.with_suffix('.json');pending=record.with_suffix('.json.part')
        pending.write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8');pending.replace(record)
    finally:part.unlink(missing_ok=True)
    return path


def remove_image(runtime,path,root=None):
    folder=temp_folder(runtime);path=Path(path)
    path=(Path(runtime)/path).resolve() if not path.is_absolute() else path.resolve()
    if path.parent!=folder or not re.fullmatch(r'[a-f0-9]{32}\.(?:png|jpe?g|webp|gif)',path.name):
        raise ValueError('Refusing to delete a file outside AI image temp storage')
    record=path.with_suffix('.json');root=Path(root or generated_root()).resolve()
    retry_external=False
    try:
        data=json.loads(record.read_text(encoding='utf-8'))
        source=(root/data['source_relative_path']).resolve()
        if (source.parent==thread_folder(root,data['thread_id']) and data.get('artifact')==path.name
                and source.is_file() and not source.is_symlink()
                and hashlib.sha256(source.read_bytes()).hexdigest()==data.get('sha256')):
            source.unlink()
    except FileNotFoundError:pass
    except OSError:
        retry_external=True
        log.warning('AI image external temp removal deferred; file may be in use')
    except (ValueError,KeyError,TypeError):
        # A missing external cache must not prevent deletion of the bot's copy.
        log.warning('AI image external temp cleanup skipped; metadata or source unavailable')
    existed=path.exists();path.unlink(missing_ok=True)
    if not retry_external:record.unlink(missing_ok=True)
    return existed


def cleanup_images(runtime,ttl_seconds=7200,now=None,root=None):
    folder=temp_folder(runtime)
    if not folder.exists():return 0
    cutoff=(time.time() if now is None else now)-ttl_seconds;removed=0
    for path in list(folder.iterdir()):
        if not path.is_file() or path.is_symlink():continue
        if re.fullmatch(r'[a-f0-9]{32}\.(?:png|jpe?g|webp|gif)',path.name):
            if created_time(path)>cutoff:continue
            try:removed+=int(remove_image(runtime,path,root))
            except OSError:log.warning('AI image temp cleanup failed; file still in use')
        elif re.fullmatch(r'[a-f0-9]{32}\.json',path.name):
            if path.stat().st_mtime>cutoff:continue
            try:
                data=json.loads(path.read_text(encoding='utf-8'));artifact=folder/data['artifact']
                if artifact.exists():continue
                remove_image(runtime,artifact,root)
            except (OSError,ValueError,KeyError,TypeError):log.warning('AI image orphan metadata cleanup skipped')
        elif re.fullmatch(r'[a-f0-9]{32}\.(?:png|jpe?g|webp|gif|json)\.part',path.name):
            if path.stat().st_mtime>cutoff:continue
            path.unlink(missing_ok=True)
    return removed


class ImageCleanup:
    def __init__(self,runtime):
        self.runtime=Path(runtime);self.next_due=0;self.interval=None;self.last_checked=None;self.signature=None;self.mode=None
    def tick(self,cfg,now=None):
        now=time.time() if now is None else now
        mode=cfg.get('generated_image_cleanup_mode','interval')
        interval=cfg.get('generated_image_cleanup_interval_minutes',cfg.get('generated_image_cleanup_interval_hours',2)*60)*60
        ttl=cfg.get('generated_image_ttl_hours',2)*3600;clock=cfg.get('generated_image_cleanup_daily_time','03:00')
        signature=(mode,interval,ttl,clock,cfg.get('generated_image_windows_schedule','daily'))
        changed=self.signature!=signature
        first=self.last_checked is None
        self.mode=mode;self.interval=interval;self.signature=signature
        if not first and not changed:
            if mode=='windows':return 0
            if mode=='expiry':self.next_due=min(self.next_due,next_image_expiry(self.runtime,ttl,now))
            if now<self.next_due:return 0
        self.last_checked=now
        try:removed=cleanup_images(self.runtime,ttl,now)
        except OSError:
            log.warning('AI image temporary directory cleanup deferred; directory unavailable')
            removed=0
        self.next_due=(now+interval if mode=='interval' else next_daily_time(now,clock) if mode=='daily'
            else next_image_expiry(self.runtime,ttl,now) if mode=='expiry' else None)
        self.runtime.mkdir(parents=True,exist_ok=True)
        record=self.runtime/'image-cleanup-state.json';pending=record.with_suffix('.json.part')
        state={'mode':mode,'last_checked':now,'next_due':self.next_due,'removed':removed,
            'reason':'startup' if first else 'config_changed' if changed else 'schedule','timezone':'Asia/Shanghai'}
        try:pending.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8');pending.replace(record)
        except OSError:log.warning('AI image cleanup state could not be saved')
        log.info('AI image temp cleanup; mode=%s; removed=%s; next_due=%s',mode,removed,self.next_due)
        return removed


async def maintain_images(runtime,settings):
    """Cleanup remains active while QQ reconnect/login waits are in progress."""
    maintenance=ImageCleanup(runtime)
    initial=settings()
    if initial.get('generated_image_cleanup_mode')=='windows':
        from windows_image_cleanup import sync_tasks
        try:await asyncio.to_thread(sync_tasks,Path(runtime).resolve().parent)
        except ValueError as exc:emit('stage',text='Windows image cleanup registration failed: '+str(exc)[:500],level='WARNING')
    while True:
        previous=maintenance.last_checked
        removed=await asyncio.to_thread(maintenance.tick,settings())
        if maintenance.last_checked!=previous:
            emit('stage',text='AI image temp cleanup; mode='+str(maintenance.mode)+'; removed='+str(removed)+'; next_due='+str(maintenance.next_due),
                image_cleanup_removed=removed,image_cleanup_mode=maintenance.mode,image_cleanup_next_due=maintenance.next_due)
        delay=60 if maintenance.next_due is None else min(60,max(1,maintenance.next_due-time.time()))
        await asyncio.sleep(delay)
