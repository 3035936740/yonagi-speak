"""Local authenticated debug UI; mutations run on the CLI supervisor thread."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit, parse_qs
from console_config import Config, PROTECTED, read_json, runtime, deleted_ids
from config_metadata import metadata,HIDDEN_FIELDS
from debug_references import enrich_quotes, enrich_images, text_chars
from debug_store import database_view, sticker_view, sticker_image
from debug_avatars import avatar,enrich_users
from system_interventions import Interventions
from manual_outbox import ManualOutbox

ASSETS={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/theme.js':'theme.js'}


class DebugWebUI:
    def __init__(self,root,dispatch):
        self.root=Path(root);self.config=Config(root);self.dispatch=dispatch
        self.token=secrets.token_urlsafe(24);self.server=None;self.thread=None
    def start(self):
        if self.server:return self.url
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,status,value,mime='application/json; charset=utf-8'):
                body=(json.dumps(value,ensure_ascii=False).encode('utf-8') if isinstance(value,(dict,list)) else value)
                self.send_response(status)
                self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; frame-ancestors 'none'; form-action 'self'")
                self.end_headers();self.wfile.write(body)
            def allowed_host(self):
                return self.headers.get('Host') in {f'127.0.0.1:{owner.server.server_port}',f'localhost:{owner.server.server_port}'}
            def authorized(self):return hmac.compare_digest(self.headers.get('X-QQ-Debug-Key',''),owner.token)
            def do_GET(self):
                if not self.allowed_host():return self.reply(403,{'error':'Invalid local host'})
                parsed=urlsplit(self.path)
                if parsed.path in ASSETS:
                    path=Path(__file__).with_name('webui')/ASSETS[parsed.path]
                    mime='text/html; charset=utf-8' if parsed.path=='/' else 'text/css; charset=utf-8' if parsed.path=='/style.css' else 'text/javascript; charset=utf-8'
                    return self.reply(200,path.read_bytes(),mime)
                if not self.authorized():return self.reply(401,{'error':'请在CLI输入 /webui 打开授权页面'})
                try:
                    if parsed.path=='/api/snapshot':return self.reply(200,owner.snapshot(parse_qs(parsed.query)))
                    if parsed.path=='/api/config':return self.reply(200,owner.public_config(parse_qs(parsed.query)))
                    query=parse_qs(parsed.query)
                    if parsed.path=='/api/database':return self.reply(200,database_view(owner.root,int(query.get('group',[owner.config.load()['group_id']])[0]),query.get('table',['notes'])[0],query.get('q',[''])[0],query.get('offset',[0])[0]))
                    if parsed.path=='/api/stickers':return self.reply(200,sticker_view(owner.root,query.get('q',[''])[0],query.get('offset',[0])[0]))
                    if parsed.path=='/api/sticker-image':
                        raw,mime=sticker_image(owner.root,query.get('id',[''])[0]);return self.reply(200,raw,mime)
                    if parsed.path=='/api/system':return self.reply(200,owner.system_status())
                    if parsed.path=='/api/avatar':
                        raw,mime=avatar(owner.root,int(query.get('group',['0'])[0]),query.get('kind',[''])[0],query.get('id',[''])[0])
                        return self.reply(200,raw,mime)
                    if parsed.path=='/api/interventions':
                        group=int(query.get('group',[owner.config.load()['group_id']])[0])
                        return self.reply(200,{'entries':Interventions(owner.root,group).list()})
                    if parsed.path=='/api/manual-send':
                        group=int(query.get('group',['0'])[0]);identity=query.get('id',[''])[0]
                        return self.reply(200,ManualOutbox(owner.root,group).get(identity))
                    return self.reply(404,{'error':'Not found'})
                except (ValueError,KeyError,OSError) as exc:return self.reply(400,{'error':str(exc)})
            def do_POST(self):
                def reject(status,message):
                    # Consume a bounded body before closing on Windows, so a
                    # rejected POST reliably delivers its status instead of RST.
                    self.connection.settimeout(3)
                    try:
                        size=int(self.headers.get('Content-Length','0'))
                        if 0<size<=262144:self.rfile.read(size)
                    except (ValueError,OSError):pass
                    return self.reply(status,{'error':message})
                origin=self.headers.get('Origin')
                if not self.allowed_host() or (origin and origin not in {f'http://127.0.0.1:{owner.server.server_port}',f'http://localhost:{owner.server.server_port}'}):
                    return reject(403,'Invalid local origin')
                if not self.authorized():return reject(401,'授权会话失效，请在CLI输入 /webui')
                if urlsplit(self.path).path!='/api/action':return self.reply(404,{'error':'Not found'})
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=3*1024*1024:return self.reply(413,{'error':'请求内容过大'})
                    self.connection.settimeout(15)
                    value=json.loads(self.rfile.read(length))
                    if not isinstance(value,dict):raise ValueError('请求必须是JSON对象')
                    result=owner.dispatch(value)
                    return self.reply(200,result)
                except TimeoutError:return self.reply(504,{'error':'操作仍在处理中，请查看群状态后再操作'})
                except (ValueError,KeyError,OSError,TypeError) as exc:return self.reply(400,{'error':str(exc)})
                except Exception as exc:return self.reply(500,{'error':'操作失败：'+type(exc).__name__})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,name='qq-debug-webui',daemon=True);self.thread.start()
        return self.url
    @property
    def base_url(self):return f'http://127.0.0.1:{self.server.server_port}/'
    @property
    def url(self):return self.base_url+'#key='+self.token
    def close(self):
        if self.server:
            self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
            self.server=None
    def snapshot(self,query):
        cfg=self.config.load();groups=[]
        for group in cfg['chat_group_whitelist']:
            folder=runtime(self.root,group,cfg);meta=read_json(folder/'debug-group.json');state=read_json(folder/'state.json')
            groups.append({'id':group,'name':meta.get('name',str(group)),'online':bool(state.get('online')),
                'enabled':bool(Config(self.root,group).load()['enabled']),
                'stopped':bool(state.get('stopped')),'pid':state.get('pid'),
                'error':state.get('error',''),'waiting_for_qq':bool(state.get('waiting_for_qq')),
                'last_poll':state.get('last_poll')})
        selected=int(query.get('group',[groups[0]['id']])[0])
        if selected not in cfg['chat_group_whitelist']:raise ValueError('该群不在白名单内')
        cfg=Config(self.root,selected).load()
        folder=runtime(self.root,selected,cfg);state=read_json(folder/'state.json');cache=read_json(folder/'image-memory.json')
        social=read_json(folder/'social-state.json')
        water=read_json(folder/'passive-participation.json')
        records=[];seen=set();hidden=deleted_ids(folder)
        path=folder/'debug-events.jsonl'
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                try:row=json.loads(line)
                except ValueError:continue
                if str(row.get('message_id','')) in hidden:continue
                key=(row.get('kind'),str(row.get('message_id','')))
                if key[0] in {'sent','received'} and key[1]:
                    if key in seen:continue
                    seen.add(key)
                records.append(row)
        records=records[-cfg['console_max_records']:]
        stored_text_chars=sum(text_chars(row) for row in records)
        records=enrich_quotes(records,folder,hidden)
        records=enrich_users(records,folder,cfg)
        records=enrich_images(records,folder,self.root/'shared-emoji-library',cfg)
        records+=Interventions(self.root,selected).events()
        records.sort(key=lambda row:row.get('time',0))
        kind=query.get('kind',['all'])[0];term=query.get('q',[''])[0].lower()
        filtered=[row for row in records if (kind=='all' or (kind=='chat' and row['kind'] in {'sent','received'})
            or (kind=='flow' and row['kind'] not in {'sent','received','system'})
            or (kind=='system' and row['kind']=='system'))
            and term in (' '.join(str(row.get(key,'')) for key in ('text','message_id','reply_id','sender','context_id','trace_id','request_id','role','kind','level'))+' '+json.dumps(row.get('quote',{}),ensure_ascii=False)).lower()]
        return {'ui_revision':2,'features':['avatars','system_interventions','manual_send','intervention_delete'],'groups':groups,'selected':selected,'events':filtered,'status':{
            'enabled':bool(cfg['enabled']),
            'manual_send_ready':bool(state.get('pid')) and read_json(folder/'manual-worker.json').get('pid')==state.get('pid'),
            'intervention_count':sum(row['status']=='active' for row in Interventions(self.root,selected).list()),
            'online':bool(state.get('online')),'pid':state.get('pid'),'last_poll':state.get('last_poll'),
            'error':state.get('error',''),'waiting_for_qq':bool(state.get('waiting_for_qq')),
            'records':len(records),'shown_records':len(filtered),'text_chars':stored_text_chars,
            'text_limit':cfg['console_max_text_chars'],'prompt_limit':cfg['max_prompt_chars'],
            'model_calls_today':state.get('model_calls_today',0),'image_summaries':len(cache.get('entries',{})),
            'observed_messages':cache.get('sequence',0),'memory_stats':state.get('memory_stats',{}),
            'image_cleanup':{**read_json(folder/'image-cleanup-state.json'),
                'mode':cfg['generated_image_cleanup_mode'],'interval_minutes':cfg['generated_image_cleanup_interval_minutes'],
                'ttl_hours':cfg['generated_image_ttl_hours'],'daily_time':cfg['generated_image_cleanup_daily_time'],
                'windows_schedule':cfg['generated_image_windows_schedule'],
                'windows_tasks':read_json(self.root/'image-cleanup-tasks.json').get('tasks',[])},
            'conversation_state':{'topic':social.get('topic',{}),'awaiting':social.get('awaiting',{}),
                'tasks':list(social.get('tasks',{}).values()),'exchanges':len(social.get('exchanges',[]))},
            'participation':{**state.get('participation',{}),**water.get('latest',{})},
            'participation_outcomes':water.get('outcomes',{}),
            'model':cfg['model'],'effort':cfg['reasoning_effort'],'fast':cfg['fast_mode'],
            'name':cfg['bot_name'],'rate':cfg['passive_probability'],
            'no_cooldown':(folder/'NO_COOLDOWN').exists()}}
    def public_config(self,query=None):
        query=query or {};group=int(query.get('group',[self.config.load()['group_id']])[0])
        scope=query.get('scope',['global'])[0]
        if scope not in {'global','group'}:raise ValueError('未知配置作用域')
        scoped=Config(self.root,group if scope=='group' else None)
        cfg=scoped.load();catalog=read_json(Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'models_cache.json')
        models=[{'name':item['slug'],'efforts':[row['effort'] for row in item.get('supported_reasoning_levels',[])]}
            for item in catalog.get('models',[]) if item.get('slug')]
        public={key:value for key,value in cfg.items() if key not in PROTECTED|HIDDEN_FIELDS}
        return {'config':public,'fields':metadata(public),
            'ui_revision':2,
            'scope':scope,'group':group,'overrides':scoped.overrides(),
            'global_config':{key:value for key,value in self.config.load().items() if key not in PROTECTED|HIDDEN_FIELDS},
            'prompt_overrides':{name:(scoped.scope_root/(name+'.txt')).exists() if scope=='group' else True for name in ('persona','reply_protocol')},
            'global_only':['chat_group_whitelist'],
            'global_prompts':{name:(self.root/(name+'.txt')).read_text(encoding='utf-8') if (self.root/(name+'.txt')).exists() else '' for name in ('persona','reply_protocol')},
            'persona':scoped.prompt_path('persona').read_text(encoding='utf-8'),'models':models,
            'reply_protocol':scoped.prompt_path('reply_protocol').read_text(encoding='utf-8') if scoped.prompt_path('reply_protocol').exists() else '',
            'help':('当前群覆盖保存到 group-config/'+str(group)+'/；空白/恢复继承采用全局值，其他群不受影响' if scope=='group' else '全局默认保存到 settings.json；未单独配置的群继承它')+'；修改前备份，重启保留'}
    def system_status(self):
        cfg=self.config.load();groups=[]
        for group in cfg['chat_group_whitelist']:
            folder=runtime(self.root,group,cfg);state=read_json(folder/'state.json');effective=Config(self.root,group).load()
            groups.append({'id':group,'name':read_json(folder/'debug-group.json').get('name',str(group)),
                'enabled':bool(effective['enabled']),'waiting_for_qq':bool(state.get('waiting_for_qq')),
                'online':bool(state.get('online')),'stopped':bool(state.get('stopped')),'pid':state.get('pid'),
                'last_poll':state.get('last_poll'),'error':state.get('error',''),'model':effective['model'],
                'effort':effective['reasoning_effort'],'fast':effective['fast_mode'],'name_persona':effective['bot_name'],
                'model_calls_today':state.get('model_calls_today',0),'memory_stats':state.get('memory_stats',{}),
                'override_count':len(Config(self.root,group).overrides())})
        return {'groups':groups,'today_model_calls':sum(row['model_calls_today'] for row in groups),
            'online_groups':sum(row['online'] and row['enabled'] and not row['stopped'] for row in groups),'total_groups':len(groups),
            'global_model':cfg['model'],'global_name':cfg['bot_name']}
