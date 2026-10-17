"""Explicit operator sends, delivered once by the owning group worker."""
from contextlib import contextmanager
import json
from platform_runtime import msvcrt
from pathlib import Path
import re
import time
import uuid

from console_config import Config,pid_alive,read_json,runtime
from storage_io import atomic_write_text
from manual_images import decode_images,save_images,plan,segments,archive_images,display_text,remove_upload_copies,content_plan
from manual_targets import validate as validate_targets,resolve as resolve_targets


class ManualOutbox:
    def __init__(self,root,group):
        self.root=Path(root);self.group=int(group)
        if self.group not in Config(root).load()['chat_group_whitelist']:raise ValueError('该群不在白名单内')
        self.folder=runtime(root,self.group)/'manual-outbox'

    def write(self,row):
        self.folder.mkdir(parents=True,exist_ok=True)
        atomic_write_text(self.folder/(row['id']+'.json'),json.dumps(row,ensure_ascii=False,indent=2))

    def get(self,identity):
        if not re.fullmatch(r'[a-f0-9]{32}',str(identity)):raise ValueError('无效发送请求ID')
        row=read_json(self.folder/(identity+'.json'))
        if not row:raise ValueError('发送请求未找到')
        return row

    def mark_ready(self,pid):
        self.folder.parent.mkdir(parents=True,exist_ok=True)
        atomic_write_text(self.folder.parent/'manual-worker.json',json.dumps({'pid':pid,'version':3}))

    @contextmanager
    def lock(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        with (self.folder/'queue.lock').open('a+b') as stream:
            deadline=time.monotonic()+3
            while True:
                try:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if time.monotonic()>deadline:raise ValueError('发送队列正在更新，请重试')
                    time.sleep(.02)
            try:yield
            finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)

    def enqueue(self,text,identity=None,images=None,reply_id=None,at_ids=None):
        reply_id,at_ids=validate_targets(reply_id,at_ids)
        images=decode_images(images)
        if not isinstance(text,str) or len(text)>2000 or not text.strip() and not images and not at_ids:raise ValueError('手动消息需有文字、图片或@，文字最多2000字符')
        plan(text,images)
        content_plan({'text':text,'images':images,'at_ids':at_ids})
        identity=str(identity or uuid.uuid4().hex).replace('-','')
        if not re.fullmatch(r'[a-f0-9]{32}',identity):raise ValueError('无效发送请求ID')
        cfg=Config(self.root,self.group).load();state=read_json(runtime(self.root,self.group)/'state.json')
        with self.lock():
            if (self.folder/(identity+'.json')).exists():
                old=self.get(identity)
                signature=lambda rows:[(row['slot'],row['digest']) for row in rows]
                if old['text']!=text or signature(old.get('images',[]))!=signature(images) or old.get('reply_id','')!=reply_id or old.get('at_ids',[])!=at_ids:raise ValueError('同一发送请求不能替换内容')
                return old
            if not cfg['enabled'] or not state.get('online') or state.get('stopped') or not pid_alive(state.get('pid')):
                raise ValueError('群后台尚未运行或QQ未连接，请先启动该群后台')
            if read_json(self.folder.parent/'manual-worker.json').get('pid')!=state.get('pid'):
                raise ValueError('当前群后台尚未加载手动发送功能，请 /exit 后手动启动 Debug')
            if images and read_json(self.folder.parent/'manual-worker.json').get('version',1)<2:raise ValueError('当前群后台尚未加载图片发送，请 /stop 后手动重启')
            if (reply_id or at_ids) and read_json(self.folder.parent/'manual-worker.json').get('version',1)<3:raise ValueError('当前群后台尚未加载手动回复和@，请 /stop 后手动重启')
            pending=sum(read_json(path).get('status') in {'queued','sending','record_pending'} for path in self.folder.glob('*.json'))
            if pending>=10:raise ValueError('该群待发送手动消息已达10条，请等待处理')
            row={'id':identity,'group_id':self.group,'text':text,'created':time.time(),'status':'queued'}
            if reply_id or at_ids:row.update(resolve_targets(self.root,self.group,reply_id,at_ids))
            if images:row['images']=save_images(self.folder,identity,images)
            self.write(row);return row

    async def drain(self,api,client,env,record,cfg):
        if not self.folder.exists():return
        count=0
        for path in sorted(self.folder.glob('*.json'),key=lambda item:item.stat().st_mtime):
            row=read_json(path)
            if row.get('status') not in {'queued','sending','delivered','record_pending'}:continue
            if count>=5:return
            count+=1
            if not re.fullmatch(r'[a-f0-9]{32}',str(row.get('id',''))) or row.get('group_id')!=self.group:continue
            if row['status']=='sending':
                row.update(status='uncertain',error='上次发送结果未知；请先核对QQ，不自动重发');self.write(row);continue
            if row['status']=='queued':
                if time.time()-row['created']>300:
                    row.update(status='expired',error='排队超过5分钟，未发送');self.write(row);continue
                if not cfg['enabled'] or (self.folder.parent/'STOP').exists():return
                try:message=segments(self.folder,row)
                except (OSError,ValueError,KeyError,TypeError):
                    row.update(status='failed',error='本地附件不可用，未发送；请重新上传图片');self.write(row);continue
                row.update(status='sending');self.write(row)
                try:
                    response=await api(client,env,'send_group_msg',{'group_id':self.group,'message':message})
                    identity=str(response.get('message_id') or '')
                    if not re.fullmatch(r'-?\d+',identity):raise ValueError('QQ未返回消息ID')
                except Exception as error:
                    row.update(status='uncertain',error='发送失败或结果未知，请核对QQ：'+type(error).__name__)
                    self.write(row);continue
                row.update(status='delivered',message_id=identity,sent_at=time.time());self.write(row)
            try:
                if row.get('images'):
                    row['image_hashes']=archive_images(self.folder,row)
                row['display_text']=display_text(row)
                record(row)
            except Exception as error:
                row.update(status='record_pending',error='QQ已发送，本地记录待补写：'+type(error).__name__)
            else:
                row.update(status='sent');row.pop('error',None)
            self.write(row)
            if row['status']=='sent' and row.get('image_hashes'):
                try:remove_upload_copies(self.folder,row)
                except OSError:pass

    def prune(self):
        if not self.folder.exists():return
        for path in self.folder.glob('*.json'):
            if time.time()-path.stat().st_mtime>30*86400 and read_json(path).get('status') in {'sent','expired'}:
                try:remove_upload_copies(self.folder,read_json(path))
                except (OSError,KeyError):pass
                path.unlink(missing_ok=True)
