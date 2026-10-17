"""Read-only Codex quota RPC and local, measured project token accounting."""
import json
import math
import logging
import queue
import sqlite3
import subprocess
import threading
import time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from console_config import Config
from runtime_paths import resolve_codex_executable
from platform_runtime import process_options

_cache={};_lock=threading.Lock()


def read_limits(root,timeout=15):
    cfg=Config(root).load();exe=resolve_codex_executable(cfg.get('codex_exe','auto'),root)
    proc=subprocess.Popen([exe,'app-server','--listen','stdio://'],cwd=str(root),stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',**process_options())
    events=queue.Queue();deadline=time.monotonic()+timeout
    def reader():
        try:
            for line in proc.stdout:
                try:events.put(json.loads(line))
                except ValueError:pass
        finally:events.put(None)
    thread=threading.Thread(target=reader,daemon=True);thread.start()
    def send(value):proc.stdin.write(json.dumps(value)+'\n');proc.stdin.flush()
    def request(identity,method,params=None):
        send({'id':identity,'method':method,**({'params':params} if params is not None else {})})
        while True:
            left=deadline-time.monotonic()
            if left<=0:raise TimeoutError()
            event=events.get(timeout=left)
            if event is None:raise RuntimeError('Codex app-server已退出')
            if event.get('id')!=identity:continue
            if 'error' in event:
                from debug_events import safe_log
                raise RuntimeError('Codex额度接口不可用：'+safe_log(event['error'].get('message',''))[:240])
            return event.get('result',{})
    try:
        request(1,'initialize',{'clientInfo':{'name':'qq_group_bot_usage','version':'1.0'}})
        send({'method':'initialized','params':{}})
        return normalize_limits(request(2,'account/rateLimits/read'))
    finally:
        proc.kill();proc.wait(timeout=5);thread.join(timeout=1)
        proc.stdin.close();proc.stdout.close()


def normalize_limits(result):
    buckets=result.get('rateLimitsByLimitId') or {'codex':result.get('rateLimits')}
    windows=[]
    for key,bucket in buckets.items():
        if not isinstance(bucket,dict):continue
        for field in ('primary','secondary'):
            row=bucket.get(field)
            if not isinstance(row,dict):continue
            minutes=row.get('windowDurationMins');used=row.get('usedPercent')
            if type(minutes) not in (int,float) or minutes<=0 or type(used) not in (int,float) or not math.isfinite(used):continue
            windows.append({'bucket':str(key),'name':str(bucket.get('limitName') or key),
                'minutes':minutes,'label':'5小时' if minutes==300 else '1周' if minutes==10080 else str(minutes)+'分钟',
                'used_percent':max(0,min(100,used)),'remaining_percent':max(0,min(100,100-used)),
                'resets_at':row.get('resetsAt') if type(row.get('resetsAt')) in (int,float) else None,
                'plan':bucket.get('planType')})
    return windows


def db_path(root):return Path(root)/'runtime'/'token-usage.sqlite3'


def record(root,cfg,stdout,identity,stamp=None):
    """Only count reported final turn usage; never infer from prompt length."""
    usage=None
    for line in stdout.decode('utf-8',errors='replace').splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if event.get('type')=='turn.completed' and isinstance(event.get('usage'),dict):usage=event['usage']
    if usage is None or not {'input_tokens','output_tokens'}<=usage.keys():return False
    cached=usage.get('cached_input_tokens')
    if cached is None:cached=usage.get('input_tokens_details',{}).get('cached_tokens') if isinstance(usage.get('input_tokens_details'),dict) else None
    counts=[]
    for index,value in enumerate((usage['input_tokens'],cached,usage['output_tokens'])):
        if index==1 and value is None:counts.append(None);continue
        if type(value) is not int or value<0:return False
        counts.append(value)
    if cached is not None and cached>counts[0]:return False
    path=db_path(root);path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path,timeout=5) as db:
        db.execute('CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, stamp REAL, group_id INTEGER, model TEXT, input INTEGER, cached INTEGER, output INTEGER)')
        columns={row[1] for row in db.execute('PRAGMA table_info(calls)')}
        if 'cache_reported' not in columns:
            db.execute('ALTER TABLE calls ADD COLUMN cache_reported INTEGER DEFAULT 0')
            db.execute('UPDATE calls SET cache_reported=1 WHERE cached>0')
        db.execute('INSERT OR IGNORE INTO calls(id,stamp,group_id,model,input,cached,output,cache_reported) VALUES(?,?,?,?,?,?,?,?)',
            (identity,time.time() if stamp is None else stamp,cfg.get('group_id',0),cfg.get('model',''),*counts,int(cached is not None)))
        db.execute('DELETE FROM calls WHERE stamp < ?',(time.time()-32*86400,))
    logging.getLogger('qq-group-bot').info('Model token usage; input=%s; cached=%s; output=%s; cache_reported=%s',
        counts[0],counts[1] if counts[1] is not None else 'unknown',counts[2],cached is not None)
    return True


def totals(root,since,group=None,now=None):
    result={'input':0,'cached':None,'output':0,'total':0,'calls':0,'cache_reported_calls':0,'cache_unknown_calls':0,
        'cache_hit_calls':0,'cache_known_input':0,'cache_hit_percent':None,'uncached':None}
    path=db_path(root)
    if not path.exists():return result
    with sqlite3.connect('file:'+path.resolve().as_posix()+'?mode=ro',uri=True,timeout=5) as db:
        columns={row[1] for row in db.execute('PRAGMA table_info(calls)')}
        known='cache_reported=1' if 'cache_reported' in columns else 'cached>0'
        row=db.execute('SELECT COALESCE(SUM(input),0),COALESCE(SUM(CASE WHEN '+known+' THEN cached ELSE 0 END),0),COALESCE(SUM(output),0),COUNT(*),'
            'SUM(CASE WHEN '+known+' THEN 1 ELSE 0 END),SUM(CASE WHEN '+known+' THEN input ELSE 0 END),SUM(CASE WHEN '+known+' AND cached>0 THEN 1 ELSE 0 END) FROM calls WHERE stamp>=? AND stamp<=?'+(' AND group_id=?' if group is not None else ''),
            [since,time.time() if now is None else now]+([group] if group is not None else [])).fetchone()
    result.update(zip(('input','cached','output','calls','cache_reported_calls','cache_known_input','cache_hit_calls'),row))
    for key in ('cache_reported_calls','cache_known_input','cache_hit_calls'):result[key]=result[key] or 0
    result['total']=result['input']+result['output'];result['cache_unknown_calls']=result['calls']-result['cache_reported_calls']
    if not result['cache_reported_calls']:result['cached']=None
    else:
        result['uncached']=result['cache_known_input']-result['cached']
        result['cache_hit_percent']=round(100*result['cached']/result['cache_known_input'],1) if result['cache_known_input'] else 0
    return result


def snapshot(root,group=None,refresh=False):
    key=str(Path(root).resolve());now=time.time()
    with _lock:
        cached=_cache.get(key,{})
        if refresh and now-cached.get('attempted',0)>=60:
            cached={**cached,'attempted':now}
            try:cached.update(windows=read_limits(root),updated=time.time(),error='')
            except Exception as error:cached['error']='额度查询失败：'+type(error).__name__+'；请检查Codex登录或网络，未返回的额度不会按0处理'
            _cache[key]=cached
    windows=[]
    for row in cached.get('windows',[]):
        end=row.get('resets_at');start=end-row['minutes']*60 if end else None
        windows.append({**row,'project_tokens_in_window':totals(root,start,group,now) if start is not None and start<=now<end else None})
    return {'windows':windows,'updated':cached.get('updated'),'error':cached.get('error',''),
        'stale':bool(cached.get('error')) or now-cached.get('updated',0)>300,
        'scope':'当前群' if group is not None else '全部群',
        'rolling_5h':totals(root,now-5*3600,group,now),'rolling_week':totals(root,now-7*86400,group,now),
        'note':'账号额度跨Codex客户端共享；token仅统计本项目启用后CLI实际返回的用量。输入含缓存，合计=输入+输出，缓存不重复相加。未返回usage的失败/超时及独立生图调用不计入；剩余token总数未知。'}


def format_text(data):
    lines=['额度与token用量  /usage','账号额度（所有客户端共享）']
    if not any(row['minutes']==300 for row in data['windows']):lines.append('5 hour token -')
    if not any(row['minutes']==10080 for row in data['windows']):lines.append('1 week token -')
    for row in data['windows']:
        reset=datetime.fromtimestamp(row['resets_at'],timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S') if row.get('resets_at') else '未知'
        lines.append(row['name']+' · '+row['label']+'：已用 '+str(row['used_percent'])+'%，剩余 '+str(row['remaining_percent'])+'%；北京时间重置 '+reset)
        if row.get('project_tokens_in_window') is not None:lines.append('  本项目在此额度窗口已用 '+str(row['project_tokens_in_window']['total'])+' token')
    if not data['windows']:lines.append('尚无额度数据，使用 /usage refresh 查询')
    if data['error']:lines.append(data['error'])
    if data['updated']:lines.append('额度查询时间 '+datetime.fromtimestamp(data['updated'],timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S')+('（旧缓存）' if data['stale'] else ''))
    for key,label in [('rolling_5h','最近5小时'),('rolling_week','最近1周')]:
        row=data[key];cache='未报告' if row['cached'] is None else str(row['cached'])
        lines.append(data['scope']+' '+label+'：'+str(row['total'])+' token；输入 '+str(row['input'])+'（缓存 '+cache+'），输出 '+str(row['output'])+'；'+str(row['calls'])+' 次已报告调用')
        lines.append('  缓存命中 '+('—' if row['cache_hit_percent'] is None else str(row['cache_hit_percent'])+'%')+'；未缓存输入 '+('—' if row['uncached'] is None else str(row['uncached']))+'；缓存数据覆盖 '+str(row['cache_reported_calls'])+'/'+str(row['calls'])+' 次（部分覆盖时比例仅基于已报告输入）')
    return '\n'.join(lines+[data['note']])
