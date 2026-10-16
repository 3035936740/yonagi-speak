from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
from console_config import atomic, runtime, Config
from debug_console import Console, Supervisor
from debug_events import DebugEvents
from debug_webui import DebugWebUI
from config_metadata import FIELDS


class WebUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        cfg=json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100,200]);atomic(self.root/'settings.json',cfg)
        (self.root/'persona.txt').write_text('你是yonagi，随口聊两句',encoding='utf-8')
        for group,name in [(100,'烧饼群'),(200,'音游群')]:
            events=DebugEvents(runtime(self.root,group),group);events.metadata(name)
            events.emit('received',text='<script>alert(1)</script> 测试',message_id='123',sender='群友')
            events.emit('sent',text='来了(',message_id='124',sender='yonagi');events.close()
        self.console=Console(self.root,Supervisor(self.root,start=False));self.console.refresh()
        self.draw=patch.object(self.console,'draw');self.draw.start()
        self.server=DebugWebUI(self.root,self.console.web_action);self.server.start()
        self.client=httpx.Client(base_url=self.server.base_url,trust_env=False)
        self.headers={'X-QQ-Debug-Key':self.server.token}
        self.catalog=patch('console_config.validate_model');self.catalog.start()
    def tearDown(self):self.draw.stop();self.catalog.stop();self.client.close();self.server.close();self.temp.cleanup()
    def test_assets_cli_style_and_api_requires_auth(self):
        response=self.client.get('/')
        self.assertEqual(response.status_code,200);self.assertIn('id="command-form"',response.text)
        self.assertIn('只看系统提示词',response.text);self.assertIn('id="intervention-form"',response.text)
        self.assertIn('frame-ancestors',response.headers['Content-Security-Policy'])
        self.assertEqual(self.client.get('/app.js').status_code,200)
        self.assertEqual(self.client.get('/api/config').status_code,401)
        self.assertEqual(self.client.get('/api/snapshot').status_code,401)
        self.assertEqual(self.client.get('/api/config',headers={'X-QQ-Debug-Key':'wrong'}).status_code,401)
    def test_snapshot_search_groups_and_local_records(self):
        result=self.client.get('/api/snapshot?group=200&kind=chat&q=来了',headers=self.headers).json()
        self.assertEqual(result['selected'],200)
        self.assertEqual(result['groups'][1]['name'],'音游群')
        self.assertEqual(len(result['events']),1);self.assertEqual(result['events'][0]['kind'],'sent')
        self.assertEqual(self.client.get('/api/snapshot?group=300',headers=self.headers).status_code,400)
    def test_config_excludes_paths_and_preserves_actual_persona(self):
        result=self.client.get('/api/config',headers=self.headers).json()
        self.assertNotIn('mcp_env_file',result['config']);self.assertNotIn('codex_exe',result['config'])
        self.assertIn('yonagi',result['persona']);self.assertEqual(result['config']['console_max_text_chars'],12800)

    def test_disabled_group_is_identified_without_claiming_qq_is_offline(self):
        cfg=Config(self.root).load();cfg['enabled']=False;atomic(self.root/'settings.json',cfg)
        # A previously online worker record may remain after disabling a group.
        atomic(runtime(self.root,100)/'state.json',{'online':True,'pid':123})
        result=self.server.snapshot({'group':['100']})
        self.assertIs(result['status']['enabled'],False)
        self.assertIs(result['groups'][0]['enabled'],False)
        self.assertIs(result['status']['online'],True)
        self.assertEqual(self.server.system_status()['online_groups'],0)
        local=self.root/'group-config/200/settings.json';local.parent.mkdir(parents=True)
        atomic(local,{'enabled':True})
        result=self.server.snapshot({'group':['200']})
        self.assertIs(result['status']['enabled'],True)
        self.assertIs(result['groups'][1]['enabled'],True)

    def test_context_id_and_role_search(self):
        events=DebugEvents(runtime(self.root,100),100)
        events.emit('decision',role='vision',context_id='ctx-image-42',text='识图完成')
        events.close()
        for query in ['ctx-image-42','vision']:
            result=self.client.get('/api/snapshot?group=100&q='+query,headers=self.headers).json()
            self.assertEqual(len(result['events']),1)
            self.assertEqual(result['events'][0]['context_id'],'ctx-image-42')

    def test_all_public_config_has_chinese_name_and_help(self):
        result=self.client.get('/api/config',headers=self.headers).json()
        self.assertEqual(set(result['config'])-set(FIELDS),set())
        for key in result['config']:
            meta=result['fields'][key]
            self.assertTrue(any('\u4e00'<=char<='\u9fff' for char in meta['label']),key)
            self.assertGreater(len(meta['help']),15,key)

    def test_theme_asset_available_without_debug_token(self):
        response=self.client.get('/theme.js')
        self.assertEqual(response.status_code,200)
        self.assertIn("let theme='light'",response.text)
        self.assertIn('qq-debug-theme',response.text)
    def test_mutation_needs_token_and_same_origin(self):
        action={'action':'config','key':'rate','value':.4,'group':100}
        self.assertEqual(self.client.post('/api/action',json=action).status_code,401)
        self.assertEqual(self.client.post('/api/action',json=action,headers={**self.headers,'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/',headers={'Host':'evil.example'}).status_code,403)
        self.assertEqual(Config(self.root).load()['passive_probability'],.35)
    def test_config_changes_validated_atomic_and_cli_selection_preserved(self):
        response=self.client.post('/api/action',json={'action':'config','key':'rate','value':.4,'group':200},headers=self.headers)
        self.assertEqual(response.status_code,200);self.assertEqual(self.console.group,100)
        self.assertEqual(Config(self.root).load()['passive_probability'],.4)
        invalid=self.client.post('/api/action',json={'action':'config','key':'rate','value':5,'group':200},headers=self.headers)
        self.assertEqual(invalid.status_code,400);self.assertEqual(Config(self.root).load()['passive_probability'],.4)
        blocked=self.client.post('/api/action',json={'action':'config','key':'codex_exe','value':'bad','group':100},headers=self.headers)
        self.assertEqual(blocked.status_code,400)
    def test_persona_whitelist_and_local_delete(self):
        response=self.client.post('/api/action',json={'action':'persona','text':'新的完整设定\n下一行','group':100},headers=self.headers)
        self.assertEqual(response.status_code,200);self.assertIn('下一行',(self.root/'persona.txt').read_text(encoding='utf-8'))
        self.assertEqual(self.client.post('/api/action',json={'action':'whitelist','mode':'add','group_id':'300','group':100},headers=self.headers).status_code,200)
        self.assertIn(300,Config(self.root).load()['chat_group_whitelist'])
        response=self.client.post('/api/action',json={'action':'delete','message_id':'123','group':100},headers=self.headers)
        self.assertEqual(response.status_code,200)
        rows=self.client.get('/api/snapshot?group=100',headers=self.headers).json()['events']
        self.assertFalse(any(row.get('message_id')=='123' for row in rows))
    def test_zero_probability_can_be_restored_to_a_fraction(self):
        for probability in [0,.35,1,.4]:
            response=self.client.post('/api/action',json={'action':'config','key':'rate','value':probability,'group':100},headers=self.headers)
            self.assertEqual(response.status_code,200)
            self.assertEqual(Config(self.root).load()['passive_probability'],probability)
    def test_command_prefix_works_and_native_editor_is_not_invoked(self):
        response=self.client.post('/api/action',json={'action':'command','command':'/sta','group':100},headers=self.headers)
        self.assertEqual(response.status_code,200);self.assertIn('12800字符',response.json()['message'])
        blocked=self.client.post('/api/action',json={'action':'command','command':'/co e settings','group':100},headers=self.headers)
        self.assertEqual(blocked.status_code,400)

    def test_web_exit_returns_shutdown_and_stops_console_loop(self):
        response=self.client.post('/api/action',json={'action':'command','command':'/ex','group':100},headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['shutdown']);self.assertFalse(self.console.running)
        self.assertIn('保存记录',response.json()['message'])

    def test_exit_alias_completion_and_arguments(self):
        self.console.buffer='/ex';self.console.complete();self.assertEqual(self.console.buffer,'/exit ')
        with self.assertRaises(ValueError):self.console.execute('/exit extra')
        self.assertTrue(self.console.running)
        self.console.execute('/exit');self.assertFalse(self.console.running)
    def test_webui_command_prints_link_opens_browser_and_reuses_listener(self):
        with patch('debug_console.webbrowser.open_new_tab',return_value=True) as browser,patch.object(self.console,'draw'):
            try:
                self.console.execute('/webui');url=self.console.web_server.url
                self.assertIn(url,self.console.notice);browser.assert_called_with(url)
                self.console.execute('/webui');self.assertEqual(self.console.web_server.url,url)
                self.assertEqual(browser.call_count,2)
                self.assertIn(self.console.web_server.base_url,self.console.screen())
            finally:self.console.web_server.close()
    def test_actions_are_queued_on_supervisor_thread(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.console.submit_web_action,{'action':'config','key':'name','value':'alice','group':100})
            deadline=time.time()+1
            while self.console.web_requests.empty():
                if time.time()>deadline:self.fail('request was not queued')
                time.sleep(.005)
            self.assertEqual(Config(self.root).load()['bot_name'],'yonagi')
            self.console.drain_web_actions();self.assertTrue(future.result(timeout=1)['ok'])
            self.assertEqual(Config(self.root).load()['bot_name'],'alice')
    def test_assets_never_insert_untrusted_chat_as_html(self):
        script=(Path(__file__).with_name('webui')/'app.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML',script);self.assertIn('textContent',script)
        self.assertEqual(self.client.get('/../settings.json',headers=self.headers).status_code,404)
