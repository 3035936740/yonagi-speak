"""Own-user Windows Task Scheduler adapter for per-group image cleanup."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from console_config import Config,atomic,read_json,runtime
from generated_image_store import ImageCleanup

CLEANUP_KEYS={'generated_image_cleanup_mode','generated_image_cleanup_interval_minutes',
    'generated_image_cleanup_daily_time','generated_image_windows_schedule','generated_image_ttl_hours',
    'generated_image_cleanup_interval_hours','chat_group_whitelist'}


def task_plan(root):
    root=Path(root).resolve();global_cfg=Config(root).load()
    prefix='QQMCP-AI-Temp-'+hashlib.sha256(str(root).casefold().encode()).hexdigest()[:12]
    tasks=[]
    for group in global_cfg['chat_group_whitelist']:
        cfg=Config(root,group).load()
        if cfg['generated_image_cleanup_mode']!='windows':continue
        tasks.append({'name':prefix+'-'+str(group),'group':group,'schedule':cfg['generated_image_windows_schedule'],
            'interval':cfg['generated_image_cleanup_interval_minutes'],'time':cfg['generated_image_cleanup_daily_time'],'enabled':True})
    python=Path(sys.executable).with_name('pythonw.exe')
    return {'prefix':prefix,'tasks':tasks,'root':str(root),'python':str(python),
        'script':str(root/'windows_image_cleanup.py'),'task_path':'\\QQMCP\\'}


def sync_tasks(root,force=False):
    root=Path(root).resolve();plan=task_plan(root);state_path=root/'image-cleanup-tasks.json'
    previous=read_json(state_path)
    if os.name!='nt':
        if plan['tasks']:raise ValueError('Windows任务计划程序仅可用于Windows；请选择分钟间隔、每日定时或每张图片到期')
        return {'tasks':[]}
    if not force and not plan['tasks'] and not previous.get('tasks'):return {'tasks':[]}
    if plan['tasks'] and not Path(plan['python']).is_file():raise ValueError('当前Python环境缺少pythonw.exe，不能隐藏运行Windows清理任务')
    with tempfile.TemporaryDirectory(prefix='qq-image-cleanup-') as folder:
        path=Path(folder)/'plan.json';path.write_text(json.dumps(plan,ensure_ascii=False),encoding='utf-8')
        try:
            completed=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                '-File',str(root/'schedule_image_cleanup.ps1'),'-PlanPath',str(path)],
                capture_output=True,encoding='utf-8',errors='replace',timeout=45,creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError,subprocess.TimeoutExpired) as exc:raise ValueError('Windows任务计划程序应用失败：'+type(exc).__name__)
        if completed.returncode:raise ValueError('Windows任务计划程序应用失败：'+completed.stderr.strip()[-500:])
    state={'updated':time.time(),'tasks':[item['name'] for item in plan['tasks']],
        'schedule':[{key:value for key,value in item.items() if key!='enabled'} for item in plan['tasks']],
        'task_path':plan['task_path'],'root':str(root)}
    atomic(state_path,state)
    return state


def run_task(root,group):
    root=Path(root).resolve();cfg=Config(root,group).load()
    # Old OS tasks may race a config change; they never clear a now-normal group.
    if cfg['generated_image_cleanup_mode']!='windows':return 0
    folder=runtime(root,group,Config(root).load());folder.mkdir(exist_ok=True)
    logging.basicConfig(filename=folder/'image-cleanup-task.log',level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',encoding='utf-8')
    removed=ImageCleanup(folder).tick(cfg)
    logging.info('Windows scheduled cleanup finished; group=%s; removed=%s',group,removed)
    return removed


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true');parser.add_argument('--group',type=int)
    parser.add_argument('--sync',action='store_true');args=parser.parse_args();root=Path(__file__).parent
    if args.sync:sync_tasks(root)
    elif args.run and args.group:run_task(root,args.group)
    else:parser.error('choose --sync or --run --group <QQ group ID>')


if __name__=='__main__':main()
