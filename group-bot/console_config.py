"""Validated, atomic local controls shared by the console and bot."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
import ctypes
from configure_launch import validate as validate_model
from storage_io import atomic_write_text, read_text

DEFAULTS = {'bot_name':'yonagi', 'bot_aliases':['Ethereal Paradise','EP'],
    'console_max_records':6000, 'console_max_text_chars':6000, 'image_summary_window_messages':2000,
    'image_repeat_window_messages':10, 'image_repeat_min_count':3,
    'image_repeat_pair_confidence':.5, 'image_repeat_full_streak':3, 'role_models':{},
    'proactive_topic_probability':.15, 'background_image_reading_enabled':True,
    'at_reply_probability':.5, 'quote_reply_probability':.5,
    'social_planner_enabled':True, 'semantic_selection_enabled':True,
    'adaptive_participation_enabled':True, 'style_adaptation_strength':.35,
    'conversation_state_ttl_seconds':600,'semantic_candidate_chars':900,
    'sticker_repeat_review_min_count':5,'sticker_repeat_review_step':3,
    'sticker_upload_retry_seconds':300,'sticker_recent_suppression_strength':.5,'peer_reply_enabled':True,
    'reply_failure_log_only':True,
    'generated_image_ttl_hours':2.0,'generated_image_cleanup_interval_hours':2.0,
    'generated_image_cleanup_mode':'interval','generated_image_cleanup_interval_minutes':120.0,
    'generated_image_cleanup_daily_time':'03:00','generated_image_windows_schedule':'daily',
    'passive_trigger_mode':'frequency','passive_window_seconds':180,
    'passive_round_seconds':40,'passive_idle_compensation_enabled':True,'passive_retry_backoff_seconds':15}
ALIASES = {'name':'bot_name','aliases':'bot_aliases','rate':'passive_probability',
    'effort':'reasoning_effort','fast':'fast_mode','records':'console_max_records',
    'whitelist':'chat_group_whitelist','prompt':'persona','protocol':'reply_protocol','text_limit':'console_max_text_chars'}
PROTECTED = {'group_id','mcp_env_file','codex_exe'}
GLOBAL_ONLY = PROTECTED | {'chat_group_whitelist'}


def pid_alive(pid):
    if not isinstance(pid,int) or pid<=0:return False
    kernel=ctypes.windll.kernel32
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return False
    try:
        code=ctypes.c_ulong()
        kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
        return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code)) and code.value==259)
    finally:
        kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.CloseHandle(handle)


def read_json(path, default=None):
    try: return json.loads(read_text(path, encoding='utf-8-sig'))
    except (OSError,ValueError): return {} if default is None else default


def atomic(path, data):
    atomic_write_text(path, json.dumps(data,ensure_ascii=False,indent=2))


def runtime(root, group, cfg=None):
    cfg=cfg or read_json(Path(root)/'settings.json')
    return Path(root)/('runtime' if int(group)==int(cfg['group_id']) else 'runtime-'+str(group))


def unique(word, choices):
    if word in choices:return word
    hits=[key for key in choices if key.lower().startswith(word.lower())]
    if len(hits)==1:return hits[0]
    raise ValueError('命令/字段不明确：'+(', '.join(hits) if hits else word))


class Config:
    def __init__(self, root, group_id=None):
        self.root=Path(root);self.path=self.root/'settings.json';self.group_id=int(group_id) if group_id is not None else None
        self.scope_root=self.root if self.group_id is None else self.root/'group-config'/str(self.group_id)
    def overrides(self):return read_json(self.scope_root/'settings.json') if self.group_id is not None else {}
    def load(self):
        saved=read_json(self.path)
        base={**DEFAULTS,**saved}
        if 'generated_image_cleanup_interval_minutes' not in saved and 'generated_image_cleanup_interval_hours' in saved:
            base['generated_image_cleanup_interval_minutes']=saved['generated_image_cleanup_interval_hours']*60
        if self.group_id is not None:
            if self.group_id not in base['chat_group_whitelist']:raise ValueError('该群不在白名单内')
            overrides=self.overrides()
            base.update({key:value for key,value in overrides.items() if key in base and key not in GLOBAL_ONLY})
            if 'generated_image_cleanup_interval_minutes' not in overrides and 'generated_image_cleanup_interval_hours' in overrides:
                base['generated_image_cleanup_interval_minutes']=overrides['generated_image_cleanup_interval_hours']*60
        return base
    def prompt_path(self, name):
        if name not in {'persona','reply_protocol'}:raise ValueError('未知提示词')
        local=self.scope_root/(name+'.txt')
        return local if local.exists() else self.root/(name+'.txt')
    def reset(self,key):
        if self.group_id is None:raise ValueError('全局设置没有上层可继承')
        key=self.key(key);self.scope_root.mkdir(parents=True,exist_ok=True)
        if key in {'persona','reply_protocol'}:
            path=self.scope_root/(key+'.txt')
            if path.exists():
                backup=self.scope_root/'config-backups';backup.mkdir(exist_ok=True)
                (backup/(key+'-'+str(time.time_ns())+'.txt')).write_bytes(path.read_bytes());path.unlink()
            return
        path=self.scope_root/'settings.json';original=path.read_bytes() if path.exists() else None
        data=self.overrides();data.pop(key,None);self.save_overrides(data)
        self._sync_cleanup_plan({key},original)
    def save_overrides(self,data):
        self.scope_root.mkdir(parents=True,exist_ok=True)
        backup=self.scope_root/'config-backups';backup.mkdir(exist_ok=True)
        atomic(backup/(str(time.time_ns())+'.json'),self.overrides())
        atomic(self.scope_root/'settings.json',data)
    def _sync_cleanup_plan(self,changed,original):
        from windows_image_cleanup import CLEANUP_KEYS,sync_tasks
        if not changed&CLEANUP_KEYS:return
        try:sync_tasks(self.root)
        except ValueError as exc:
            destination=self.scope_root/'settings.json'
            if original is None:destination.unlink(missing_ok=True)
            else:
                restored=destination.with_name('settings.rollback-'+uuid.uuid4().hex+'.tmp')
                restored.write_bytes(original);restored.replace(destination)
            try:sync_tasks(self.root,force=True)
            except ValueError:pass
            raise ValueError('清理计划保存失败，配置已恢复原值：'+str(exc))
    def key(self, text):
        matched=unique(text,list(self.load())+list(ALIASES)+['persona','reply_protocol','cooldown'])
        return ALIASES.get(matched,matched)
    def validate(self,cfg):
        cfg={**DEFAULTS,**cfg}
        current=self.load()
        if set(cfg)-set(current):raise ValueError('未知配置字段：'+str(set(cfg)-set(current)))
        for key,value in cfg.items():
            if key in PROTECTED and value!=current[key]:raise ValueError('运行路径/主群编号请停机后修改原配置文件')
            if isinstance(current[key],bool):
                if not isinstance(value,bool):raise ValueError(key+' 必须为 on/off')
            elif isinstance(current[key],(int,float)):
                if isinstance(value,bool) or not isinstance(value,(int,float)):raise ValueError(key+' 必须为数字')
                if isinstance(current[key],int) and not isinstance(value,int) and key not in {'generated_image_ttl_hours','generated_image_cleanup_interval_hours','generated_image_cleanup_interval_minutes'} and not any(word in key for word in ('probability','confidence','strength')):raise ValueError(key+' 必须为整数')
                if ('probability' in key or 'confidence' in key) and not 0<=value<=1:raise ValueError(key+' 范围为 0~1')
                if value<0:raise ValueError(key+' 不能为负数')
                if ('max_' in key or key in {'poll_seconds','context_messages','image_repeat_min_count','image_repeat_full_streak','image_repeat_window_messages','image_summary_window_messages'}) and value<=0:raise ValueError(key+' 必须大于0')
            elif isinstance(current[key],str) and (not isinstance(value,str) or len(value)>1000):raise ValueError(key+' 必须为短文本')
        if not 24<=cfg['recent_chat_journal_max_messages']<=100000:raise ValueError('近期记录条数范围24~100000')
        if not 100<=cfg['console_max_records']<=100000:raise ValueError('控制台记录条数范围100~100000')
        if not 500<=cfg['console_max_text_chars']<=1000000:raise ValueError('控制台文本字符上限范围500~1000000')
        if not 500<=cfg['max_prompt_chars']<=100000:raise ValueError('模型输入字符上限范围500~100000')
        if not 0<=cfg['style_adaptation_strength']<=1:raise ValueError('风格适应强度范围0~1')
        if not 0<=cfg['sticker_recent_suppression_strength']<=1:raise ValueError('近期发图抑制强度范围0~1')
        if not 60<=cfg['conversation_state_ttl_seconds']<=86400:raise ValueError('聊天状态有效期范围60~86400秒')
        if not 200<=cfg['semantic_candidate_chars']<=2000:raise ValueError('语义候选字符预算范围200~2000')
        if not 2<=cfg['image_repeat_window_messages']<=100:raise ValueError('复读窗口范围2~100')
        if not 2<=cfg['image_repeat_min_count']<=cfg['image_repeat_window_messages']:raise ValueError('复读次数必须在2~窗口条数之间')
        if not 3<=cfg['image_repeat_full_streak']<=cfg['image_repeat_window_messages']:raise ValueError('完整复读连续次数必须在3~窗口条数之间')
        if not 3<=cfg['sticker_repeat_review_min_count']<=cfg['image_summary_window_messages']:raise ValueError('重复图片重新审核次数范围3~图片有效期窗口')
        if not 1<=cfg['sticker_repeat_review_step']<=cfg['image_summary_window_messages']:raise ValueError('重新审核计数增量范围1~图片有效期窗口')
        if not 30<=cfg['sticker_upload_retry_seconds']<=86400:raise ValueError('图片审核失败重试间隔范围30~86400秒')
        for key in ('generated_image_ttl_hours','generated_image_cleanup_interval_hours'):
            if not .1<=cfg[key]<=168:raise ValueError('生图临时文件保留/清理时间范围0.1~168小时')
        if cfg['generated_image_cleanup_mode'] not in {'interval','daily','expiry','windows'}:raise ValueError('未知生图清理模式')
        if cfg['generated_image_windows_schedule'] not in {'interval','daily'}:raise ValueError('Windows任务可选分钟间隔或每日定时')
        if cfg['passive_trigger_mode'] not in {'frequency','dynamic'}:raise ValueError('水群触发方式为frequency或dynamic')
        if not 30<=cfg['passive_window_seconds']<=180:raise ValueError('水群待处理消息有效期范围30~180秒')
        if not 5<=cfg['passive_round_seconds']<=120:raise ValueError('水群统计轮次范围5~120秒')
        if not 0<=cfg['passive_retry_backoff_seconds']<=300:raise ValueError('水群规划间隔范围0~300秒')
        if not 1<=cfg['passive_min_messages']<=100:raise ValueError('水群消息阈值基准范围1~100')
        if not 1<=cfg['generated_image_cleanup_interval_minutes']<=10080:raise ValueError('生图清理间隔范围1~10080分钟')
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',cfg['generated_image_cleanup_daily_time']):raise ValueError('每日清理时间格式为HH:MM，例如03:00')
        groups=cfg['chat_group_whitelist']
        if not isinstance(groups,list) or not groups or len(groups)>30 or any(type(x)!=int or x<=0 for x in groups):raise ValueError('白名单必须是1~30个正整数群号')
        if len(set(groups))!=len(groups):raise ValueError('白名单群号不能重复')
        if not isinstance(cfg['bot_aliases'],list) or any(not isinstance(x,str) or not x.strip() or len(x)>80 for x in cfg['bot_aliases']):raise ValueError('别名必须是短文本数组')
        if not cfg['bot_name'].strip() or len(cfg['bot_name'])>80:raise ValueError('名字为1~80字符')
        catalog_path=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'models_cache.json'
        catalog=read_json(catalog_path)
        validate_model(catalog,cfg['model'],cfg['reasoning_effort'],cfg['fast_mode'])
        if not isinstance(cfg['role_models'],dict):raise ValueError('role_models 必须是JSON对象')
        if cfg.get('role_models'):
            for role,model in cfg['role_models'].items():
                if role not in {'context','emotion','vision'} or not isinstance(model,dict):raise ValueError('无效分工模型配置')
                validate_model(catalog,model.get('model',cfg['model']),model.get('reasoning_effort',cfg['reasoning_effort']),cfg['fast_mode'])
        return cfg
    def save(self,cfg,explicit_keys=()):
        old_cfg=self.load()
        destination=self.scope_root/'settings.json'
        original=destination.read_bytes() if destination.exists() else None
        if 'generated_image_cleanup_interval_hours' in explicit_keys and 'generated_image_cleanup_interval_minutes' not in explicit_keys:
            cfg={**cfg,'generated_image_cleanup_interval_minutes':cfg['generated_image_cleanup_interval_hours']*60}
        cfg=self.validate(cfg)
        if self.group_id is not None:
            global_cfg=Config(self.root).load();old=self.overrides()
            data={key:cfg[key] for key in cfg if key not in GLOBAL_ONLY and (key in old or key in explicit_keys or cfg[key]!=global_cfg[key])}
            self.save_overrides(data)
        else:
            backup=self.root/'config-backups';backup.mkdir(exist_ok=True)
            atomic(backup/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.json'),self.load())
            atomic(self.path,cfg)
        changed={key for key in cfg if cfg.get(key)!=old_cfg.get(key)}|set(explicit_keys)
        self._sync_cleanup_plan(changed,original)
    def set(self,key,text):
        key=self.key(key)
        if self.group_id is not None and key in GLOBAL_ONLY:raise ValueError('该字段只能修改全局配置')
        if self.group_id is not None and not text.strip():self.reset(key);return
        if key in {'persona','reply_protocol'}:
            if not text.strip() or len(text)>30000:raise ValueError('提示词不能为空，上限30000字符')
            self.scope_root.mkdir(parents=True,exist_ok=True)
            path=self.scope_root/(key+'.txt');backup=self.scope_root/'config-backups';backup.mkdir(exist_ok=True)
            if path.exists():(backup/(key+'-'+str(time.time_ns())+'.txt')).write_text(path.read_text(encoding='utf-8'),encoding='utf-8')
            temp=path.with_suffix('.tmp');temp.write_text(text.replace('\\n','\n'),encoding='utf-8');temp.replace(path);return
        if self.group_id is not None and key in GLOBAL_ONLY:raise ValueError('该字段只能修改全局配置')
        cfg=self.load();old=cfg[key]
        if isinstance(old,bool):
            if text.lower() not in {'on','off','true','false','1','0'}:raise ValueError('布尔配置请用 on/off')
            value=text.lower() in {'on','true','1'}
        elif isinstance(old,str):value=text
        else:
            try:value=json.loads(text)
            except ValueError:raise ValueError('数字/数组/对象请使用JSON格式')
        cfg[key]=value;self.save(cfg,explicit_keys=[key])


def prompt_path(root,cfg,name):
    group=cfg.get('group_id')
    local=Path(root)/'group-config'/str(group)/(name+'.txt')
    return local if group and local.exists() else Path(root)/(name+'.txt')


def deleted_ids(path):return set(read_json(Path(path)/'deleted-messages.json',[]))


def delete_local(root,group,message_id):
    """Called only after this group's bot has stopped; no QQ recall API."""
    if not re.fullmatch(r'-?\d{1,30}',message_id):raise ValueError('消息ID必须是整数')
    folder=runtime(root,group); ids=deleted_ids(folder);ids.add(message_id)
    atomic(folder/'deleted-messages.json',sorted(ids))
    journal=folder/'recent-chat.jsonl'
    if journal.exists():
        kept=[]
        for line in journal.read_text(encoding='utf-8').splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if str(row.get('_id'))!=message_id:kept.append(row)
        temp=journal.with_suffix('.tmp');temp.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in kept),encoding='utf-8');temp.replace(journal)
    for name in ['conversation-focus.json','interaction-memory.json','style-memory.json','pending-mentions.json','social-state.json']:
        path=folder/name;data=read_json(path, [] if name.startswith('conversation') else {})
        def matches(record):
            return isinstance(record,dict) and (any(str(record.get(key,''))==message_id
                for key in ('id','_id','_reply_to_id','source_id','user_message_id','bot_message_id'))
                or message_id in record.get('_source_ids',[]) or message_id in record.get('source_ids',[]))
        def remove(value):
            if isinstance(value,list):return [remove(x) for x in value if not matches(x)]
            if isinstance(value,dict):return {key:remove(item) for key,item in value.items()}
            return value
        if name=='style-memory.json':data['pairs']=[];data['voice']={}
        if name=='interaction-memory.json':data['turns']=[]
        if name=='conversation-focus.json':data=[]
        if name=='social-state.json':data.update(topic={},awaiting={},tasks={})
        if path.exists():atomic(path,remove(data))
    database=folder/'group-memory.sqlite3'
    if database.exists():
        with sqlite3.connect(database) as db:
            salt=db.execute("SELECT value FROM meta WHERE key='salt'").fetchone()
            if salt:
                digest=hashlib.sha256((salt[0]+message_id).encode()).hexdigest()[:20]
                db.execute('DELETE FROM sources WHERE id=?',(digest,))
                for key,evidence in db.execute('SELECT id,evidence FROM notes').fetchall():
                    if any(item.get('id')==digest for item in json.loads(evidence or '[]') if isinstance(item,dict)):
                        db.execute('DELETE FROM notes WHERE id=?',(key,))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='note_metadata'").fetchone():
                    db.execute('DELETE FROM note_metadata WHERE note_id NOT IN (SELECT id FROM notes)')
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='supersessions'").fetchone():
                    db.execute('DELETE FROM supersessions WHERE old_id NOT IN (SELECT id FROM notes) OR new_id NOT IN (SELECT id FROM notes)')
    cache=folder/'image-memory.json'
    if cache.exists():
        data=read_json(cache);data['entries']={};atomic(cache,data)
    # Physically remove message events from all retained debug log segments.
    for path in folder.glob('debug-events.jsonl*'):
        rows=[]
        for line in path.read_text(encoding='utf-8').splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if str(row.get('message_id',''))!=message_id and message_id not in row.get('source_ids',[]):rows.append(line)
        temp=path.with_name(path.name+'.tmp');temp.write_text('\n'.join(rows)+'\n',encoding='utf-8');temp.replace(path)
