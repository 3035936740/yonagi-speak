"""Shared stdlib launcher. Paths are relative to this checkout, never the cwd."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def bot_root():
    for path in (ROOT/'group-bot',ROOT/'work/group-bot'):
        if (path/'debug_console.py').is_file():return path
    raise ValueError('group-bot/debug_console.py is missing')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def write_new(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        stream.write(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def initialize():
    bot=bot_root();settings=bot/'settings.json'
    if not settings.exists():
        cfg=read(bot/'settings.example.json');cfg['mcp_env_file']='napcat.env';write_new(settings,cfg)
    for name,source in [('persona.txt','persona-default.txt'),('napcat.env','napcat.env.example')]:
        dest=bot/name
        if not dest.exists():
            with dest.open('xb') as stream:stream.write((bot/source).read_bytes())
    return bot


def download(url,path):
    if not url.startswith('https://'):raise ValueError('Only HTTPS downloads are accepted')
    request=urllib.request.Request(url,headers={'User-Agent':'QQ-Group-Bot-Setup'})
    with urllib.request.urlopen(request,timeout=120) as response,path.open('wb') as stream:
        shutil.copyfileobj(response,stream)


def launch_settings():
    return read(ROOT/'launch.local.json')


def save_launch(value):
    # Installer only initializes this file. Existing installations are never replaced.
    if (ROOT/'launch.local.json').exists():raise ValueError('launch.local.json already exists; keeping existing installation')
    write_new(ROOT/'launch.local.json',value)


def local_path(value):
    path=Path(os.path.expandvars(os.path.expanduser(str(value))))
    return (ROOT/path).resolve() if not path.is_absolute() else path.resolve()


def napcat_command(cfg):
    mode=cfg.get('mode')
    if mode=='docker':
        return ['docker','compose','-f',str(local_path(cfg['compose_file'])),'up','-d']
    if mode=='windows_hook' and os.name=='nt':
        folder=local_path(cfg['napcat_dir']);qq=local_path(cfg['qq_exe'])
        for path in (folder/'NapCatWinBootMain.exe',folder/'NapCatWinBootHook.dll',folder/'napcat.mjs',qq):
            if not path.is_file():raise ValueError('NapCat/QQ file missing: '+str(path))
        return [str(folder/'NapCatWinBootMain.exe'),str(qq),str(folder/'NapCatWinBootHook.dll')]
    if mode=='windows_batch' and os.name=='nt':
        batch=local_path(cfg['launcher'])
        if not batch.is_file():raise ValueError('NapCat launcher missing: '+str(batch))
        # Windows filenames cannot contain quotes; cmd /s requires this outer pair.
        return '"'+os.environ.get('COMSPEC','cmd.exe')+'" /d /s /c ""'+str(batch)+'""'
    raise ValueError('Run Install-NapCat first, or configure launch.local.json for this platform')


def napcat_ready():
    """Avoid starting a duplicate instance; do not require login before launch."""
    try:
        with urllib.request.urlopen('http://127.0.0.1:6099',timeout=2) as response:
            return response.status==200
    except (OSError,urllib.error.URLError):return False


def start_napcat(quiet=False,dry_run=False):
    if not dry_run and napcat_ready():
        print('NapCat WebUI is already running: http://127.0.0.1:6099/webui');return
    cfg=launch_settings().get('napcat',{})
    command=napcat_command(cfg)
    if dry_run:print(json.dumps({'command':command},ensure_ascii=False));return
    if cfg['mode']=='docker':subprocess.run(command,cwd=ROOT,check=True)
    else:
        folder=(local_path(cfg['napcat_dir']) if cfg['mode']=='windows_hook'
            else local_path(cfg['launcher']).parent)
        env=dict(os.environ)
        if cfg['mode']=='windows_hook':
            env.update(NAPCAT_PATCH_PACKAGE=str(folder/'qqnt.json'),NAPCAT_LOAD_PATH=str(folder/'loadNapCat.js'),
                NAPCAT_INJECT_PATH=str(folder/'NapCatWinBootHook.dll'),NAPCAT_MAIN_PATH=str(folder/'napcat.mjs'),
                NAPCAT_WORKDIR=str(folder))
        logs=ROOT/'runtime';logs.mkdir(exist_ok=True)
        with (logs/'napcat-launch.out.log').open('ab') as out,(logs/'napcat-launch.err.log').open('ab') as err:
            process=subprocess.Popen(command,cwd=folder,env=env,stdin=subprocess.DEVNULL,
                stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
        (logs/'napcat-launch.pid').write_text(str(process.pid),encoding='ascii')
    print('NapCat started: http://127.0.0.1:6099/webui')
    if not quiet:print('Scan QQ login in WebUI. See runtime/napcat-launch.*.log or docker compose logs.')


def install_docker_napcat(dry_run=False):
    folder=ROOT/'runtime/napcat-docker';compose=folder/'compose.json'
    if dry_run:print(json.dumps({'backend':'docker','compose':str(compose)}));return
    subprocess.run(['docker','compose','version'],check=True)
    if (ROOT/'launch.local.json').exists():raise ValueError('Existing launch.local.json kept; no reinstall performed')
    # This bot needs OneBot HTTP, rather than the upstream example's WS-only port.
    folder.mkdir(parents=True,exist_ok=True)
    config=folder/'data/config';config.mkdir(parents=True,exist_ok=True)
    document={'services':{'napcat':{'image':'mlikiowa/napcat-docker:latest','restart':'unless-stopped',
        'environment':{'MODE':'ws','WEBUI_TOKEN':secrets.token_urlsafe(24)},
        'ports':['127.0.0.1:6099:6099','127.0.0.1:3000:3000','127.0.0.1:3001:3001'],
        'volumes':['./data/qq:/app/.config/QQ','./data/config:/app/napcat/config','./data/plugins:/app/napcat/plugins']}}}
    if not compose.exists():write_new(compose,document)
    subprocess.run(['docker','compose','-f',str(compose),'pull'],cwd=folder,check=True)
    save_launch({'napcat':{'mode':'docker','compose_file':str(compose.relative_to(ROOT)).replace('\\','/')}})
    print('Installed. Start-QQ-NapCat starts the container. WebUI token is in '+str(compose))
    print('After QQ login, enable an HTTP server on 0.0.0.0:3000 inside NapCat WebUI and set its token in group-bot/napcat.env.')


def safe_extract(archive,dest):
    dest=dest.resolve()
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            path=(dest/item.filename.replace('\\','/')).resolve()
            if not path.is_relative_to(dest) or (item.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Unsafe archive entry')
        source.extractall(dest)


def install_napcat(backend,dry_run=False):
    if backend=='docker' or os.name!='nt':return install_docker_napcat(dry_run)
    if platform.machine().lower() not in {'amd64','x86_64'}:raise ValueError('Windows OneKey installer requires x64; use Docker instead')
    if (ROOT/'launch.local.json').exists():raise ValueError('Existing launch.local.json kept; no reinstall performed')
    folder=ROOT/'runtime/napcat-onekey'
    url='https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.Windows.OneKey.zip'
    if dry_run:print(json.dumps({'backend':'windows_onekey','url':url,'folder':str(folder)}));return
    folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='napcat-download-',dir=ROOT/'runtime') as temp:
        archive=Path(temp)/'NapCat.zip';download(url,archive)
        print('Downloaded archive SHA256: '+hashlib.sha256(archive.read_bytes()).hexdigest())
        # Extract only to a new folder; never overwrite an existing QQ/config tree.
        target=folder/secrets.token_hex(4);target.mkdir();safe_extract(archive,target)
    installers=list(target.rglob('NapCatInstaller.exe'))
    if len(installers)!=1:raise ValueError('Official package has no unique NapCatInstaller.exe: '+str(target))
    subprocess.run([str(installers[0])],cwd=installers[0].parent,check=True)
    launchers=list(target.rglob('napcat.bat'))
    if len(launchers)!=1:raise ValueError('Choose the installed napcat.bat and configure launch.local.json; package left at '+str(target))
    save_launch({'napcat':{'mode':'windows_batch','launcher':str(launchers[0].relative_to(ROOT)).replace('\\','/')}})
    print('Installed. Run Start-QQ-NapCat, log in QQ and enable OneBot HTTP.')


def start_bot(args,extra):
    bot=bot_root()
    if args.dry_run:
        print(json.dumps({'bot':str(bot),'webui':not args.cli,'auto_napcat':not args.skip_napcat,'arguments':extra}));return
    initialize()
    if args.model or args.effort or args.fast:
        cfg=read(bot/'settings.json')
        subprocess.run([sys.executable,'-X','utf8',str(bot/'configure_launch.py'),
            '--model',args.model or cfg['model'],'--effort',args.effort or cfg['reasoning_effort'],
            '--fast',args.fast or ('on' if cfg.get('fast_mode') else 'off')],cwd=bot,check=True)
    if args.cooldown:
        cfg=read(bot/'settings.json')
        for group in cfg['chat_group_whitelist']:
            path=bot/('runtime' if group==cfg['group_id'] else 'runtime-'+str(group))/'NO_COOLDOWN'
            if args.cooldown=='off':path.parent.mkdir(parents=True,exist_ok=True);path.touch()
            else:path.unlink(missing_ok=True)
    if not args.skip_napcat:start_napcat(quiet=True)
    command=[sys.executable,'-X','utf8',str(bot/'debug_console.py'),'--root',str(bot)]
    if not args.cli:command.append('--webui')
    command+=extra
    raise SystemExit(subprocess.call(command,cwd=bot))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('entry',choices=['bot','napcat','install-napcat','init'])
    parser.add_argument('--dry-run',action='store_true');parser.add_argument('--skip-napcat',action='store_true')
    parser.add_argument('--cli',action='store_true');parser.add_argument('--quiet',action='store_true')
    parser.add_argument('--model');parser.add_argument('--effort');parser.add_argument('--fast',choices=['on','off'])
    parser.add_argument('--cooldown',choices=['on','off']);parser.add_argument('--backend',choices=['auto','docker'],default='auto')
    args,extra=parser.parse_known_args()
    if extra and args.entry!='bot':parser.error('Unknown arguments: '+' '.join(extra))
    if args.entry=='bot':start_bot(args,extra)
    elif args.entry=='napcat':start_napcat(args.quiet,args.dry_run)
    elif args.entry=='install-napcat':install_napcat(args.backend,args.dry_run)
    elif not args.dry_run:initialize()


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:
        print('Setup/launch failed: '+str(exc),file=sys.stderr);raise SystemExit(1)
