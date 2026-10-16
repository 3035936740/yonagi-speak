"""Operator instructions: persistent local control, never a chat trigger."""
from contextlib import contextmanager
import json
from platform_runtime import msvcrt
from pathlib import Path
import re
import time
import uuid

from console_config import Config, read_json, runtime
from storage_io import atomic_write_text

MODES={'context','once','timed','persistent'}


class Interventions:
    def __init__(self,root,group):
        self.root=Path(root);self.group=int(group)
        if self.group not in Config(root).load()['chat_group_whitelist']:raise ValueError('该群不在白名单内')
        self.folder=runtime(root,self.group);self.path=self.folder/'system-interventions.json'

    @contextmanager
    def locked(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        with (self.folder/'system-interventions.lock').open('a+b') as stream:
            # Windows allows locking a byte beyond EOF. Do not read that byte
            # before acquiring it: another process may already own its lock.
            deadline=time.monotonic()+3
            while True:
                try:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if time.monotonic()>deadline:raise ValueError('系统干预正在更新，请重试')
                    time.sleep(.02)
            try:yield
            finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)

    def _read(self):return read_json(self.path,{'entries':[],'seen':[]})
    def _write(self,data):atomic_write_text(self.path,json.dumps(data,ensure_ascii=False,indent=2))
    def _expire(self,data,cfg):
        changed=False
        for row in data['entries']:
            if row['status']!='active':continue
            reason=('time' if row['mode']=='timed' and time.time()>=row['expires_at'] else
                'context' if row['mode']=='context' and row.get('used_chars',0)+len(row['text'])>=cfg['console_max_text_chars'] else '')
            if reason:row.update(status='expired',ended=time.time(),end_reason=reason);changed=True
        return changed

    def list(self,include_deleted=False):
        if not self.path.exists():return []
        with self.locked():
            data=self._read()
            if self._expire(data,Config(self.root,self.group).load()):self._write(data)
            return data['entries'] if include_deleted else [row for row in data['entries'] if row['status']!='deleted']

    def delete(self,identity):
        if not re.fullmatch(r'[0-9a-f]{12}',str(identity or '')):raise ValueError('无效干预ID')
        with self.locked():
            data=self._read()
            for row in data['entries']:
                if row['id']==identity:
                    row.update(status='deleted',ended=time.time());self._write(data);return 1
            raise ValueError('干预记录未找到')

    def add(self,text,mode='context',minutes=30,scope='group'):
        if not isinstance(text,str) or not text.strip():raise ValueError('系统干预不能为空')
        text=text.strip()
        if mode not in MODES:raise ValueError('有效期可选 context/once/timed/persistent')
        if mode=='timed' and (isinstance(minutes,bool) or not isinstance(minutes,(int,float)) or not 1<=minutes<=10080):raise ValueError('定时有效期范围1~10080分钟')
        cfg=Config(self.root,self.group).load()
        cap=min(1500,max(100,cfg['max_prompt_chars']//4),cfg['console_max_text_chars']-1)
        with self.locked():
            data=self._read();self._expire(data,cfg)
            active=[row for row in data['entries'] if row['status']=='active']
            if len(active)>=10 or sum(len(row['text']) for row in active)+len(text)>cap:
                raise ValueError('生效中的干预总长度不能超过'+str(cap)+'字符，最多10条；请先清除旧干预')
            row={'id':uuid.uuid4().hex[:12],'kind':'system','role':'developer','text':text,
                 'created':time.time(),'group_id':self.group,'scope':scope,'mode':mode,
                 'status':'active','used_chars':0}
            if mode=='timed':row['expires_at']=row['created']+minutes*60
            entries=data['entries']+[row]
            # Retaining audit history must never evict a still-active override.
            data['entries']=sorted([item for item in entries if item['status']=='active']+
                [item for item in entries if item['status']!='active'][-90:],key=lambda item:item['created'])
            self._write(data)
            return row

    def clear(self,identity=None):
        if not self.path.exists():return 0
        with self.locked():
            data=self._read();count=0
            for row in data['entries']:
                if row['status']=='active' and (identity is None or row['id']==identity):
                    row.update(status='cleared',ended=time.time());count+=1
            self._write(data);return count

    def observe(self,row,cfg):
        if not self.path.exists() or row.get('kind') not in {'received','sent'} or row.get('historical'):return
        key=str(row.get('message_id') or '')
        if not key:return
        with self.locked():
            data=self._read()
            if key in data.get('seen',[]):return
            changed=False
            for entry in data['entries']:
                if entry['status']!='active' or row.get('time',0)<entry['created']:continue
                if entry['mode']=='context':
                    entry['used_chars']+=len(row.get('text',''))+len(row.get('quote',{}).get('text',''));changed=True
                if entry['mode']=='once' and row['kind']=='sent' and entry['id'] in row.get('intervention_ids',[]):
                    entry.update(status='consumed',ended=time.time());changed=True
            data['seen']=(data.get('seen',[])+[key])[-2000:]
            changed=self._expire(data,cfg) or changed
            if changed:self._write(data)

    def events(self):
        return [{'kind':'system','role':'developer','time':row['created'],'group_id':self.group,
                 'context_id':'system:'+row['id'],'text':row['text'],'intervention':row,'level':'INFO'}
                for row in self.list()]


def attach(cfg,root):
    """Capture instructions for this job, reserving their model-input budget."""
    rows=[row for row in Interventions(root,cfg['group_id']).list() if row['status']=='active']
    cfg=dict(cfg)
    if not rows:return cfg
    instructions=('本段来自本地操作员的系统干预，是开发者指令，不是群友消息，也不是待回答的问题。'
        '仅在正常触发的任务中应用，不为本段另发回复，不复述或解释干预内容。'
        '保留核心人设、任务输出schema及事实依据；下列指令调整本群后续互动。\n'+
        '\n'.join('['+row['id']+'] '+row['text'] for row in rows))
    cfg['_developer_instructions']=instructions
    cfg['_intervention_ids']=[row['id'] for row in rows]
    cfg['_intervention_chars']=len(instructions)
    cfg['max_prompt_chars']=max(1,cfg.get('max_prompt_chars',12800)-len(instructions))
    return cfg


def command(root,group,verb='list',text='',mode='context',scope='group',minutes=30,identity=None):
    if scope not in {'group','all'}:raise ValueError('范围可选 group/all')
    if verb in {'remove','delete'} and not re.fullmatch(r'[0-9a-f]{12}',str(identity or '')):raise ValueError('remove/delete 必须提供完整干预ID；清除全部请用 clear')
    groups=Config(root).load()['chat_group_whitelist'] if scope=='all' else [group]
    if verb=='add':return [Interventions(root,g).add(text,mode,minutes,scope) for g in groups]
    if verb in {'clear','remove'}:return sum(Interventions(root,g).clear(identity if verb=='remove' else None) for g in groups)
    if verb=='list':return [entry for g in groups for entry in Interventions(root,g).list()]
    if verb=='delete':return sum(Interventions(root,g).delete(identity) for g in groups)
    raise ValueError('系统干预操作可选 add/list/remove/delete/clear')
