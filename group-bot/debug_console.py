"""Terminal supervisor with live group transcripts and prefix commands."""
import argparse
from collections import deque
import ctypes
import json
from platform_runtime import msvcrt
from platform_runtime import process_options, enable_terminal, PosixJob
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unicodedata
import queue
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
import webbrowser
from console_config import Config, PROTECTED, ALIASES, read_json, runtime, unique, delete_local, atomic, deleted_ids
from debug_events import clean
from debug_references import text_chars
from debug_history import recover_chat,lane
from debug_store import database_mutate, sticker_mutate
from runtime_paths import prepare_runtime_paths
from system_interventions import command as intervention_command,Interventions
from manual_outbox import ManualOutbox
from debug_images import mutate as image_mutate
from sticker_prefill import StickerPrefill, start as start_sticker_prefill

from console_help import COMMANDS,help_text


def display_width(text):return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def wrap(text,width):
    lines=[];line='';used=0
    for char in clean(text):
        size=display_width(char)
        if char=='\n' or used+size>width:
            lines.append(line);line='';used=0
            if char=='\n':continue
        line+=char;used+=size
    lines.append(line);return lines


class WindowsJob:
    """Kill bot descendants if the console window is closed abruptly."""
    def __init__(self):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_=[('per_process',ctypes.c_int64),('per_job',ctypes.c_int64),('flags',w.DWORD),
                ('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),('process_limit',w.DWORD),
                ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(ctypes.Structure):_fields_=[(key,ctypes.c_uint64) for key in ['read','write','other','read_bytes','write_bytes','other_bytes']]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.restype=w.HANDLE
        self.api.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]
        self.api.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        self.api.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.api.CloseHandle.argtypes=[w.HANDLE]
        self.handle=self.api.CreateJobObjectW(None,None)
        info=Extended();info.basic.flags=0x2000
        if not self.handle or not self.api.SetInformationJobObject(self.handle,9,ctypes.byref(info),ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(),'Cannot create process cleanup job')
    def add(self,process):
        if not self.api.AssignProcessToJobObject(self.handle,int(process._handle)):
            raise OSError(ctypes.get_last_error(),'Cannot attach bot to cleanup job')
    def close(self):
        if self.handle:self.api.CloseHandle(self.handle);self.handle=None


class Supervisor:
    def __init__(self,root,start=True):
        self.root=Path(root);self.config=Config(root);self.children={};self.streams={};self.exited={}
        self.start=start;self.jobs={}
        self.paused=set()
        self.retry_after={}
        self.closed=False
    def sync(self):
        if not self.start or self.closed:return
        cfg=self.config.load();groups=[int(group) for group in cfg['chat_group_whitelist']]
        for group in list(self.children):
            if group not in groups:self.stop_group(group)
        for group in groups:
            if not Config(self.root,group).load()['enabled']:
                if group in self.children:self.stop_group(group)
                continue
            if group in self.children:
                if self.children[group].poll() is not None:
                    self.exited[group]=self.children[group].returncode
                    intentional=(runtime(self.root,group)/'STOP').exists()
                    self.stop_group(group)
                    if intentional:self.paused.add(group)
                    else:self.retry_after[group]=time.monotonic()+5
                continue
            if group in self.paused:continue
            if time.monotonic()<self.retry_after.get(group,0):continue
            folder=runtime(self.root,group,cfg);new=not folder.exists();folder.mkdir(exist_ok=True)
            if new and (runtime(self.root,cfg['group_id'],cfg)/'NO_COOLDOWN').exists():(folder/'NO_COOLDOWN').touch()
            (folder/'STOP').unlink(missing_ok=True)
            state=read_json(folder/'state.json')
            if alive(state.get('pid')):continue
            stream=(folder/'console-worker.log').open('ab',buffering=0)
            args=[sys.executable,'-X','utf8',str(self.root/'bot.py')]
            if group!=cfg['group_id']:args+=['--group-id',str(group)]
            process=subprocess.Popen(args,cwd=self.root,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,
                **process_options(),env={**os.environ,'PYTHONIOENCODING':'utf-8'})
            job=WindowsJob() if os.name=='nt' else PosixJob()
            try:job.add(process)
            except Exception:process.terminate();stream.close();job.close();raise
            self.jobs[group]=job
            self.children[group]=process;self.streams[group]=stream
            self.retry_after.pop(group,None)
            from console_config import atomic
            if read_json(folder/'state.json').get('pid')!=process.pid:
                atomic(folder/'state.json',{**state,'pid':process.pid,'online':False,'stopped':False,'waiting_for_qq':True,'error':''})
    def stop_group(self,group):
        folder=runtime(self.root,group);folder.mkdir(exist_ok=True);(folder/'STOP').touch()
        process=self.children.pop(group,None)
        if process:
            try:process.wait(timeout=25)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
        else:
            deadline=time.time()+25
            while alive(read_json(folder/'state.json').get('pid')):
                if time.time()>deadline:raise ValueError('该群后台未停止，未修改记录')
                time.sleep(.2)
        stream=self.streams.pop(group,None)
        if stream:stream.close()
        job=self.jobs.pop(group,None)
        if job:job.close()
        self.exited.pop(group,None)
    def close(self):
        self.closed=True
        cfg=self.config.load()
        for group in cfg['chat_group_whitelist']:
            folder=runtime(self.root,group);folder.mkdir(exist_ok=True);(folder/'STOP').touch()
        for group in set(cfg['chat_group_whitelist'])|set(self.children):self.stop_group(group)
        for job in self.jobs.values():job.close()
        self.jobs.clear()


def alive(pid):
    if not pid:return False
    if os.name!='nt':
        from console_config import pid_alive
        return pid_alive(pid)
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.OpenProcess.restype=ctypes.c_void_p
    api.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_bool,ctypes.c_uint32]
    api.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=api.OpenProcess(0x1000,False,int(pid))
    if handle:api.CloseHandle(handle);return True
    return False


class Console:
    def __init__(self,root,supervisor):
        self.root=Path(root);self.config=Config(root);self.supervisor=supervisor
        self.group=self.config.load()['group_id'];self.buffer='';self.finding=False;self.view='all'
        self.search='';self.scroll=0;self.notice='输入 /help 查看命令';self.running=True;self.rows={};self.names={}
        self.command_history=[];self.history_index=0
        self._last_frame=None;self.panel=None
        self.web_server=None;self.web_requests=queue.Queue()
    def open_webui(self):
        from debug_webui import DebugWebUI
        if self.web_server is None:self.web_server=DebugWebUI(self.root,self.submit_web_action)
        url=self.web_server.start()
        opened=webbrowser.open_new_tab(url)
        self.notice='WebUI 链接：'+url+'\n'+('浏览器已打开，CLI继续运行' if opened else '请复制上面的链接在浏览器打开')
        self.draw()
        return url
    def submit_web_action(self,value):
        future=Future();self.web_requests.put((future,value))
        try:return future.result(timeout=40)
        except FutureTimeoutError:
            future.cancel();raise TimeoutError('WebUI action timed out')
    def drain_web_actions(self):
        for _ in range(4):
            try:future,value=self.web_requests.get_nowait()
            except queue.Empty:return
            if not future.set_running_or_notify_cancel():continue
            try:future.set_result(self.web_action(value))
            except Exception as exc:future.set_exception(exc)
    def web_action(self,value):
        original=self.group;original_config=self.config;group=int(value.get('group',self.group))
        if group not in self.groups():raise ValueError('该群不在白名单内')
        self.group=group
        try:
            action=value.get('action')
            scope=value.get('scope','global')
            if scope not in {'global','group'}:raise ValueError('未知配置作用域')
            if action in {'config','persona','reply_protocol','config_inherit'} and scope=='group':self.config=Config(self.root,group)
            if action=='config':
                key=self.config.key(str(value.get('key','')));item=value.get('value')
                if key in PROTECTED:raise ValueError('运行路径请停机后修改原文件')
                text=item if isinstance(item,str) else json.dumps(item,ensure_ascii=False)
                self.configure('set '+key+' '+text)
            elif action=='database':
                return {**database_mutate(self.root,group,str(value.get('table','')),str(value.get('mode','')),
                    value.get('values'),value.get('row_id'),value.get('expected')),'selected':group}
            elif action=='member_label':
                from member_labels import store
                result=store(self.root,group).mark(str(value.get('account','')),str(value.get('label','')),value.get('note',''),value.get('aliases'),value.get('blocked'))
                return {'ok':True,'member':result,'selected':group,'message':'群友标记已保存，下次回复生效'}
            elif action=='sticker':
                return {**sticker_mutate(self.root,str(value.get('mode','')),str(value.get('id','')),value.get('values')),'selected':group}
            elif action=='image':
                return {**image_mutate(self.root,group,str(value.get('mode','')),
                    str(value.get('message_id','')),str(value.get('key','')),value.get('values')),'selected':group}
            elif action=='sticker_prefill':
                job=(StickerPrefill(self.root,group).cancel(str(value.get('id',''))) if value.get('mode')=='cancel'
                    else start_sticker_prefill(self.root,group,value))
                return {'ok':True,'job':job,'selected':group,'message':'AI识别只填入表单，不自动保存'}
            elif action=='config_inherit':
                self.config.reset(str(value.get('key','')));self.notice='已恢复继承全局设置'
            elif action=='system':
                result=intervention_command(self.root,group,str(value.get('mode','add')),text=value.get('text',''),
                    mode=value.get('lifetime','context'),scope=value.get('target_scope','group'),
                    minutes=value.get('minutes',30),identity=value.get('id'))
                self.notice='系统干预已保存，不触发立即回复' if value.get('mode','add')=='add' else '系统干预已更新'
                return {'ok':True,'message':self.notice,'result':result,'selected':group}
            elif action=='manual_send':
                request=ManualOutbox(self.root,group).enqueue(value.get('text',''),value.get('request_id'),value.get('images'),value.get('reply_id'),value.get('at_ids'))
                self.notice='手动消息已排队，发送成功后加入上下文和手动消息记忆'
                return {'ok':True,'message':self.notice,'request':request,'selected':group}
            elif action in {'persona','reply_protocol'}:
                text=value.get('text')
                if not isinstance(text,str):raise ValueError('提示词必须是文本')
                self.config.set(action,text);self.notice='提示词已写入 '+action+'.txt，原版本已备份'
            elif action=='whitelist':
                mode=unique(str(value.get('mode','')),['add','remove']);gid=str(value.get('group_id',''))
                self.execute('/whitelist '+mode+' '+gid)
            elif action=='delete':self.execute('/delete '+str(value.get('message_id','')))
            elif action=='restart':self.execute('/restart')
            elif action=='reconnect':
                from napcat_reconnect import reconnect
                reconnect(self,str(value.get('mode','napcat')))
            elif action=='pause':
                self.supervisor.paused.add(group);self.supervisor.stop_group(group);self.notice='当前群已暂停，记录已保存'
            elif action=='command':
                command=str(value.get('command',''));parts=command.lstrip('/').split(maxsplit=1)
                if not parts:raise ValueError('请输入命令')
                name=unique(parts[0],COMMANDS)
                if name=='webui':raise ValueError('此命令请在CLI运行')
                if name=='config' and len(parts)>1 and parts[1].split()[0] in {'edit','ed','e'}:
                    raise ValueError('请用网页的配置或提示词编辑页')
                self.execute(command)
            else:raise ValueError('未知操作')
            return {'ok':True,'message':self.notice,'selected':self.group,'shutdown':not self.running}
        finally:
            self.config=original_config
            groups=self.groups();self.group=original if original in groups else groups[0]
    def groups(self):return self.config.load()['chat_group_whitelist']
    def refresh(self):
        cfg=self.config.load()
        if self.group not in self.groups():self.group=self.groups()[0]
        for group in self.groups():
            folder=runtime(self.root,group,cfg);meta=read_json(folder/'debug-group.json')
            self.names[group]=meta.get('name',str(group));hidden=deleted_ids(folder)
            rows=[];seen=set()
            path=folder/'debug-events.jsonl'
            if path.exists():
                # The writer bounds this file by text characters and records.
                for line in path.read_text(encoding='utf-8').splitlines():
                    try:row=json.loads(line)
                    except ValueError:continue
                    if str(row.get('message_id','')) in hidden:continue
                    key=(row.get('kind'),str(row.get('message_id','')))
                    if row.get('kind') in {'sent','received'} and key[1]:
                        if key in seen:continue
                        seen.add(key)
                    rows.append(row)
            rows=recover_chat(rows,folder,Config(self.root,group).load(),hidden,group)
            from member_policy import enrich_records
            rows=enrich_records(rows,folder)
            rows+=Interventions(self.root,group).events()
            rows.sort(key=lambda row:row.get('time',0))
            self.rows[group]=rows
    def switch(self,step):
        groups=self.groups();self.group=groups[(groups.index(self.group)+step)%len(groups)];self.scroll=0
        self.notice='已切换到 '+self.names.get(self.group,str(self.group))
    def matches(self,text):return [group for group in self.groups() if text.lower() in (str(group)+' '+self.names.get(group,'')).lower()]
    def complete(self):
        if self.finding:
            hits=self.matches(self.buffer)
            if len(hits)==1:self.buffer=str(hits[0])
            self.notice='匹配：'+str(hits);return
        text=self.buffer.lstrip('/');parts=text.split();trailing=text.endswith(' ')
        choices=COMMANDS;prefix=parts[-1] if parts and not trailing else ''
        if len(parts)>1 or trailing:
            try:command=unique(parts[0],COMMANDS)
            except ValueError:return
            if command=='config':
                choices=['show','set','get','edit','prompt','persona','reply_protocol','cooldown','local','global','inherit']+list(self.config.load())+list(ALIASES)
                scoped_parts=parts[2:] if len(parts)>1 and parts[1] in {'local','global'} else parts[1:]
                if len(scoped_parts)>=2 and scoped_parts[0] in {'set','get','inherit'}:choices=list(self.config.load())+list(ALIASES)+['persona','reply_protocol']
                if len(parts)>=3 and parts[1] in {'set','get'}:choices=list(self.config.load())+list(ALIASES)+['persona','reply_protocol']
            elif command=='whitelist':choices=['add','remove','show']
            elif command=='view':choices=['all','chat','flow','system']
            elif command=='member':choices=['list','show','type','block','unblock']
            elif command=='help':choices=COMMANDS+['all']
            elif command=='system':choices=['add','list','remove','delete','clear','--scope','group','all','--mode','context','once','timed','persistent','--minutes']
            elif command=='group':choices=[str(group) for group in self.groups()]
            else:choices=[]
        hits=sorted(set(choice for choice in choices if choice.lower().startswith(prefix.lower())))
        if not hits:return
        common=os.path.commonprefix(hits)
        if len(hits)==1:common+=' '
        if trailing:self.buffer+=common
        else:self.buffer=self.buffer[:-len(prefix)]+common if prefix else self.buffer+common
        self.notice='补全：'+'  '.join(hits)[:500]
    def execute(self,text):
        parts=text.lstrip('/').split(maxsplit=1)
        if not parts:return
        command=unique(parts[0],COMMANDS);rest=parts[1] if len(parts)>1 else ''
        if command=='reconnect':
            from napcat_reconnect import reconnect
            reconnect(self,rest.strip() or 'napcat');return
        if command=='usage':
            from account_usage import snapshot,format_text
            options=rest.split()
            if set(options)-{'refresh','all'}:raise ValueError('/usage [refresh] [all]')
            self.notice=format_text(snapshot(self.root,None if 'all' in options else self.group,True));self.panel={'kind':'help','scroll':0};return
        if command in {'quit','exit','stop'}:
            if rest:raise ValueError('/exit、/quit 或 /stop 不需要参数')
            self.running=False;self.notice='正在保存记录并退出；所有授权群后台及 WebUI 将停止';return
        if command=='webui':self.open_webui();return
        if command=='system':
            self.system_command(rest);return
        if command=='say':
            request=ManualOutbox(self.root,self.group).enqueue(rest)
            self.notice='已排队发送到当前群；请求ID：'+request['id'];return
        if command=='restart':
            self.notice='正在保存并重启当前群';self.draw();self.supervisor.stop_group(self.group)
            self.supervisor.paused.discard(self.group)
            self.supervisor.retry_after.pop(self.group,None)
            self.supervisor.sync();self.notice='已重启当前群';return
        if command=='help':
            self.notice=help_text(rest);self.panel={'kind':'help','scroll':0};return
        if command=='member':
            from console_panels import member_command
            member_command(self,rest);return
        if command=='status':
            from console_panels import status
            self.notice=status(self);self.panel={'kind':'status'};return
        if command=='groups':self.notice=' | '.join(f'{self.names.get(g,g)} ({g})' for g in self.groups());return
        if command=='group':
            hits=self.matches(rest)
            if len(hits)!=1:raise ValueError('群匹配不唯一：'+str(hits))
            self.group=hits[0];self.scroll=0;return
        if command=='search':self.search=rest;self.scroll=0;self.notice='当前群记录筛选：'+rest;return
        if command=='view':self.panel=None;self.view=unique(rest,['all','chat','flow','system']);self.scroll=0;self.notice='视图：'+self.view;return
        if command=='delete':
            if not any(str(row.get('message_id',''))==rest for row in self.rows.get(self.group,[])):
                raise ValueError('当前群保留记录中没有该消息ID')
            self.notice='正在停止当前群以删除本地记录，QQ消息不会撤回';self.draw()
            self.supervisor.stop_group(self.group)
            delete_local(self.root,self.group,rest);self.supervisor.sync();self.refresh()
            self.notice='已删除本地消息 '+rest+' 及可追踪上下文；本群图片摘要缓存已清空';return
        if command=='whitelist':
            words=rest.split();action=unique(words[0] if words else 'show',['show','add','remove'])
            cfg=self.config.load();groups=cfg['chat_group_whitelist']
            if action=='show':self.notice=str(groups);return
            if len(words)!=2 or not words[1].isdigit():raise ValueError('/whitelist add|remove 群号')
            group=int(words[1])
            if action=='add':cfg['chat_group_whitelist']=list(dict.fromkeys(groups+[group]))
            else:cfg['chat_group_whitelist']=[value for value in groups if value!=group]
            self.config.save(cfg);self.supervisor.sync();self.notice='白名单已更新，对应群后台已同步';return
        if command=='config':self.configure(rest)
    def system_command(self,rest):
        words=rest.split();verb='list' if not words else 'add'
        if words and any(value.startswith(words[0]) for value in ['add','list','remove','delete','clear']):
            verb=unique(words.pop(0),['add','list','remove','delete','clear'])
        options={'scope':'group','mode':'context','minutes':30}
        while words and words[0].startswith('--'):
            name=words.pop(0)[2:]
            if name not in options or not words:raise ValueError('参数格式：--scope group|all --mode context|once|timed|persistent --minutes 30')
            options[name]=float(words.pop(0)) if name=='minutes' else words.pop(0)
        result=intervention_command(self.root,self.group,verb,text=' '.join(words),identity=words[0] if words and verb in {'remove','delete'} else None,**options)
        self.notice=('系统干预已保存，不触发立即回复。' if verb=='add' else '系统干预：')+json.dumps(result,ensure_ascii=False)
        self.refresh()
    def configure(self,rest):
        if not rest.strip():
            self.panel={'kind':'config','scope':'local' if self.config.group_id is not None else 'global','index':0,'query':'','message':''}
            self.notice='配置面板：方向键选择，Enter编辑，Tab切换作用域';return
        if '=' in rest.split(maxsplit=1)[0]:
            name,value=rest.split('=',1)
            self.config.set(name.strip(),value)
            self.supervisor.sync();self.notice='已保存 '+name.strip()+'；WebUI key/地址/端口需重启后台生效';return
        words=rest.split(maxsplit=1);key=words[0] if words else 'show';value=words[1] if len(words)>1 else ''
        if key in {'local','global'}:
            original=self.config;self.config=Config(self.root,self.group if key=='local' else None)
            try:return self.configure(value)
            finally:self.config=original
        if key=='inherit':
            self.config.reset(value);self.notice='已恢复继承全局设置';return
        action=unique(key,['show','get','set','edit','prompt','cooldown','local','global','inherit']+list(self.config.load())+list(ALIASES))
        if action in {'local','global'}:return self.configure(action+' '+value)
        if action=='inherit':self.config.reset(value);self.notice='已恢复继承全局设置';return
        if action=='show':
            cfg=self.config.load();values={k:v for k,v in cfg.items() if k not in PROTECTED and (not value or value in k)}
            if 'webui_key' in values:values['webui_key']='已设置（隐藏）' if values['webui_key'] else '随机生成'
            self.notice=json.dumps(values,ensure_ascii=False,indent=2);return
        if action=='get':
            field=self.config.key(value)
            self.notice=self.config.prompt_path(field).read_text(encoding='utf-8') if field in {'persona','reply_protocol'} else str(self.config.load().get(field));return
        if action=='set':
            pair=value.split(maxsplit=1)
            if len(pair)==1 and pair[0] in {'webui_key','key','member_prompt_human','member_prompt_owner','member_prompt_AI','member_prompt_bot'}:pair.append('')
            if len(pair)!=2:raise ValueError('/config set 字段 值')
            key,value=pair
        elif action=='edit':
            field='persona' if not value or value in {'prompt','persona'} else unique(value,['settings','reply_protocol'])
            path=self.root/('settings.json' if field=='settings' else field+'.txt')
            temp=self.root/('console-edit-'+str(time.time_ns())+'.txt')
            temp.write_text(path.read_text(encoding='utf-8'),encoding='utf-8')
            self.notice='在记事本编辑并保存，关闭记事本后会校验和应用';self.draw()
            try:
                if os.name=='nt':subprocess.Popen(['notepad.exe',str(temp)])
                else:
                    import shlex
                    subprocess.run(shlex.split(os.environ.get('EDITOR','vi'))+[str(temp)],check=True)
                self.notice='在记事本保存后回到此窗口按 Enter 应用，Esc 取消';self.draw()
                while True:
                    char=msvcrt.getwch()
                    if char=='\x1b':self.notice='已取消编辑';return
                    if char=='\r':break
                text=temp.read_text(encoding='utf-8-sig')
                if field in {'persona','reply_protocol'}:self.config.set(field,text)
                else:self.config.save(json.loads(text))
            finally:temp.unlink(missing_ok=True)
            self.supervisor.sync();self.notice='编辑已应用，原配置已备份';return
        else:key=action
        if key=='cooldown':
            if value not in {'on','off'}:raise ValueError('/config cooldown on|off')
            for group in ([self.group] if self.config.group_id is not None else self.groups()):
                folder=runtime(self.root,group);folder.mkdir(parents=True,exist_ok=True)
                path=folder/'NO_COOLDOWN'
                if value=='off':path.touch()
                else:path.unlink(missing_ok=True)
        else:self.config.set(key,value)
        if self.config.key(key)=='enabled' and self.config.load()['enabled']:self.supervisor.paused.clear()
        if self.config.key(key)=='shared_sticker_library':
            for group in self.groups():self.supervisor.stop_group(group)
        self.supervisor.sync();self.notice='已保存，所有授权群共用；下一轮生效（已经开始的任务沿用原快照）'
    def key(self,char,code=None):
        if char=='\x03':self.running=False;return
        if self.panel:
            from console_panels import key
            key(self,char,code);return
        if code is not None:
            if code in {'K','M'} and not self.finding:self.switch(-1 if code=='K' else 1)
            elif code=='I':self.scroll+=12
            elif code=='Q':self.scroll=max(0,self.scroll-12)
            elif code in {'H','P'} and self.command_history:
                self.history_index=max(0,min(len(self.command_history),self.history_index+(-1 if code=='H' else 1)))
                self.buffer=self.command_history[self.history_index] if self.history_index<len(self.command_history) else ''
            return
        if char=='\x03':self.running=False
        elif char=='\x06':self.finding=True;self.buffer='';self.notice='输入群名/群号，Enter选择；Esc取消'
        elif char=='\x1b':self.finding=False;self.buffer='';self.search='';self.scroll=0
        elif char=='\b':self.buffer=self.buffer[:-1]
        elif char=='\t':self.complete()
        elif char=='\r':
            text=self.buffer;self.buffer=''
            try:
                if self.finding:
                    matches=self.matches(text)
                    if len(matches)!=1:raise ValueError('匹配结果：'+str(matches)+'，请继续输入')
                    self.group=matches[0];self.finding=False;self.scroll=0
                    self.notice='已切换到 '+self.names.get(self.group,str(self.group))
                else:
                    self.command_history.append(text);self.history_index=len(self.command_history);self.execute(text)
            except (ValueError,KeyError,OSError) as exc:
                if self.finding:self.buffer=text
                self.notice='错误：'+str(exc)
        elif char.isprintable():self.buffer+=char
    def screen(self,width=100,height=30):
        if self.panel:
            from console_panels import render
            return render(self,width,height)
        cfg=self.config.load();state=read_json(runtime(self.root,self.group)/'state.json')
        header=f"QQ 调试台 | {self.names.get(self.group,self.group)} ({self.group}) | {'在线' if state.get('online') else '等待连接/已停止'} | {cfg['model']} {cfg['reasoning_effort']} fast={cfg['fast_mode']}"
        if self.group in self.supervisor.exited:header+=' | 后台退出码='+str(self.supervisor.exited[self.group])+'，用 /restart 重试'
        body=[]
        labels={'received':'← 群友','sent':'→ bot','stage':'流程','decision':'判断','trigger':'触发','group':'群'}
        for row in self.rows.get(self.group,[]):
            kind=row.get('kind','stage')
            if self.view=='chat' and kind not in {'sent','received'}:continue
            if self.view=='flow' and kind in {'sent','received','system'}:continue
            if self.view=='system' and kind!='system':continue
            text=clean(row.get('text',''))
            if self.search.lower() not in (text+' '+str(row.get('message_id',''))).lower():continue
            prefix=time.strftime('%H:%M:%S',time.localtime(row.get('time',0)))+' '+labels.get(kind,kind)
            if row.get('historical'):prefix+=' [历史]'
            if row.get('blocked'):prefix+=' [屏蔽]'
            if row.get('sender'):prefix+=' '+clean(row['sender'])
            if row.get('role'):prefix+=' '+row['role']
            if row.get('message_id'):prefix+=' #'+str(row['message_id'])
            body+=wrap(prefix+'  '+text,width-1)
        if len(self.notice)>500:
            body=wrap(self.notice,width-1)
            notice=['命令输出（PgUp/PgDn 翻页，/view all 返回聊天）']
        else:notice=wrap(self.notice,width-1)[-min(8,max(2,height//4)):]
        available=max(1,height-len(notice)-5);self.scroll=min(self.scroll,max(0,len(body)-available))
        end=len(body)-self.scroll;visible=body[max(0,end-available):end]
        lines=wrap(header,width-1)[:1]
        if self.web_server and self.web_server.server:lines+=['WebUI: '+self.web_server.base_url+'  (/webui 再次打开浏览器)']
        lines+=['─'*(width-1)]+visible
        lines+=['']*max(0,height-len(lines)-len(notice)-3)
        if self.finding:notice+=wrap('匹配：'+' | '.join(f'{self.names.get(g,g)}({g})' for g in self.matches(self.buffer)),width-1)[:1]
        footer=wrap('←→ 切群 | Ctrl+F 搜群 | Tab 补全 | PgUp/PgDn 翻页 | /help | Ctrl+C 退出',width-1)[0]
        input_line=wrap(('搜群> ' if self.finding else 'qq> ')+self.buffer,width-1)[-1]
        lines+=notice+[footer,input_line]
        return '\n'.join(lines[-height:])
    def draw(self):
        size=shutil.get_terminal_size((100,30));frame=self.screen(size.columns,size.lines)
        if frame==self._last_frame:return
        self._last_frame=frame
        sys.stdout.write('\x1b[H\x1b[2J'+frame);sys.stdout.flush()
    def run(self,auto_webui=False):
        restore_terminal=enable_terminal()
        sys.stdout.write('\x1b[?1049h');sys.stdout.flush()
        try:
            if auto_webui:self.open_webui()
            refresh_at=0
            while self.running:
                if (self.root/'CONSOLE_STOP').exists():self.running=False;break
                self.drain_web_actions()
                if not self.running:break
                if time.time()>=refresh_at:
                    self.supervisor.sync();self.refresh();refresh_at=time.time()+1
                if msvcrt.kbhit():
                    char=msvcrt.getwch()
                    if char in {'\x00','\xe0'}:self.key('',msvcrt.getwch())
                    else:self.key(char)
                self.draw();time.sleep(.08)
        finally:
            restore_terminal()
            while True:
                try:future,_=self.web_requests.get_nowait()
                except queue.Empty:break
                if not future.done():future.set_exception(ValueError('CLI已关闭'))
            if self.web_server:self.web_server.close()
            self.notice='正在保存记录并停止机器人…';self.draw();self.supervisor.close()
            sys.stdout.write('\x1b[?1049l\n机器人已停止，记录已保存在各群 runtime 中\n');sys.stdout.flush()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path(__file__).parent)
    parser.add_argument('--check',action='store_true');parser.add_argument('--attach',action='store_true')
    parser.add_argument('--webui',action='store_true');args=parser.parse_args()
    supervisor=Supervisor(args.root,start=not (args.check or args.attach));console=Console(args.root,supervisor)
    if args.check:
        console.refresh();console.execute('/status');print(console.screen());return
    lock=(args.root/'console.lock').open('a+b');lock.seek(0)
    if not lock.read(1):lock.write(b'0');lock.flush()
    lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:raise SystemExit('调试控制台已在运行，请切回原窗口')
    (args.root/'CONSOLE_STOP').unlink(missing_ok=True)
    try:
        if not args.attach:
            try:
                prepared=prepare_runtime_paths(args.root)
                console.notice='启动路径已检查；Codex CLI：'+prepared['resolved_cli']+(
                    '\n旧路径已备份并迁移为自动发现/项目相对路径' if prepared.get('migrated') else '')
            except (OSError,ValueError) as exc:raise SystemExit('启动路径检查失败：'+str(exc))
        console.run(auto_webui=args.webui)
    finally:lock.close();supervisor.close()


if __name__=='__main__':main()
