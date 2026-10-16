"""Resolve project files from the installation root and discover native Codex CLI."""
import logging
import os
from pathlib import Path
import shutil
import time

log=logging.getLogger('qq-group-bot')
_last_cli=None


def project_path(value,root):
    path=Path(os.path.expandvars(os.path.expanduser(str(value))))
    return (path if path.is_absolute() else Path(root)/path).resolve()


def resolve_codex_executable(configured='auto',root=None,*,desktop_bin=None,which=None,environ=None):
    """Honor a valid explicit binary; recover removed version paths each call.

    Only native binaries are launched with exec argv. A .cmd/.ps1 shim would
    change quoting/shell semantics for prompts and is never used as a fallback.
    No copy of the subscription binary and no per-model-call version probes.
    """
    root=Path(root or Path(__file__).parent);environ=os.environ if environ is None else environ
    which=shutil.which if which is None else which
    configured=str(configured or 'auto').strip();candidates=[]
    def native(value):
        if not value:return None
        path=Path(value)
        if path.suffix.lower() in {'.cmd','.bat','.ps1'}:return None
        return path.resolve() if path.is_file() else None
    if configured.lower()!='auto':
        explicit=native(project_path(configured,root))
        if explicit:return _chosen(explicit,'configured')
        if '/' not in configured and '\\' not in configured:
            explicit=native(which(configured))
            if explicit:return _chosen(explicit,'PATH')
    override=environ.get('QQ_BOT_CODEX_EXE')
    explicit=native(project_path(override,root)) if override else None
    if explicit:return _chosen(explicit,'QQ_BOT_CODEX_EXE')
    local=Path(environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))
    folder=Path(desktop_bin) if desktop_bin is not None else local/'OpenAI/Codex/bin'
    direct=native(folder/'codex.exe')
    if direct:return _chosen(direct,'desktop')
    try:
        # App upgrades replace hash-named directories. Search only this known
        # binary location, never arbitrary files or a working directory tree.
        for child in folder.iterdir():
            if child.is_dir():
                path=native(child/'codex.exe')
                if path:candidates.append((path.stat().st_mtime_ns,str(path),path))
    except FileNotFoundError:pass
    except OSError as exc:log.info('Codex discovery directory unavailable; error_type=%s',type(exc).__name__)
    if candidates:return _chosen(max(candidates)[2],'desktop')
    for name in ('codex.exe','codex'):
        explicit=native(which(name))
        if explicit:return _chosen(explicit,'PATH')
    # Official npm distributions put a native binary behind their JS shim.
    npm=Path(environ.get('APPDATA',Path.home()/'AppData/Roaming'))/'npm/node_modules/@openai'
    for package in ('codex','codex-win32-x64','codex-win32-arm64',
            'codex/node_modules/@openai/codex-win32-x64','codex/node_modules/@openai/codex-win32-arm64'):
        for architecture in ('x86_64-pc-windows-msvc','aarch64-pc-windows-msvc'):
            explicit=native(npm/package/'vendor'/architecture/'codex/codex.exe')
            if explicit:return _chosen(explicit,'npm-native')
    raise FileNotFoundError('未找到可用的 Codex CLI；codex_exe='+configured+
        '；已检查桌面安装目录 '+str(folder)+' 和 PATH。请安装/修复 Codex，或指定有效的本地程序路径。')


def _chosen(path,source):
    global _last_cli
    value=str(path)
    if value!=_last_cli:
        _last_cli=value;log.info('Codex executable resolved; source=%s; path=%s',source,value)
    return value


def prepare_runtime_paths(root,dry_run=False):
    """Startup-only migration of known legacy paths, after worker shutdown.

    The WebUI's protected-field validation remains unchanged. This performs
    the offline original-file migration that its error message requires.
    """
    from console_config import atomic,read_json,pid_alive
    root=Path(root).resolve();path=root/'settings.json';cfg=read_json(path)
    if not cfg:raise ValueError('无法读取启动配置：'+str(path))
    selected=resolve_codex_executable(cfg.get('codex_exe','auto'),root)
    updates={};configured=str(cfg.get('codex_exe','auto'))
    normalized=configured.replace('\\','/').casefold()
    if not configured or configured in {'codex','codex.exe'} or '/openai/codex/bin/' in normalized:
        updates['codex_exe']='auto'
    expected=(root.parent/'qq-mcp/.env').resolve()
    original_env=cfg.get('mcp_env_file','')
    original_path=project_path(original_env,root) if original_env else None
    if original_path==expected or (expected.is_file() and original_env and not original_path.is_file()
            and str(original_env).replace('\\','/').endswith('/qq-mcp/.env')):
        updates['mcp_env_file']='../qq-mcp/.env'
    updates={key:value for key,value in updates.items() if cfg.get(key)!=value}
    active=[]
    for state_path in root.glob('runtime*/state.json'):
        state=read_json(state_path)
        if state.get('pid') and pid_alive(state['pid']):active.append(state['pid'])
    result={'resolved_cli':selected,'updates':updates,'active_worker_pids':active,'migration_pending':bool(updates)}
    if not updates or dry_run:return result
    if active:raise ValueError('启动路径迁移前请先在旧Debug输入 /exit；仍有后台进程：'+', '.join(map(str,active)))
    latest=read_json(path)
    if any(latest.get(key)!=cfg.get(key) for key in updates):
        raise ValueError('启动配置刚被其他进程修改，请重新启动Debug后再试')
    backup=root/'config-backups';backup.mkdir(exist_ok=True)
    atomic(backup/('runtime-paths-'+str(time.time_ns())+'.json'),latest)
    latest.update(updates);atomic(path,latest)
    result['migration_pending']=False;result['migrated']=True
    return result
