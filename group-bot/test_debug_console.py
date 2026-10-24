import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from console_config import Config, atomic, runtime, delete_local, deleted_ids
from debug_console import Console, Supervisor, wrap, display_width
from debug_events import DebugEvents, safe_log
from group_memory import GroupMemory
import dialogue
import emoji_library
from message_delivery import delivery_plan


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        base=json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))
        base.update(group_id=100,chat_group_whitelist=[100,200])
        atomic(self.root/'settings.json',base);(self.root/'persona.txt').write_text('你叫yonagi',encoding='utf-8')
        self.catalog=patch('console_config.validate_model');self.catalog.start()
        self.sup=Supervisor(self.root,start=False);self.ui=Console(self.root,self.sup)
        for group,name in [(100,'烧饼聊天群'),(200,'音游交流群')]:
            folder=runtime(self.root,group);folder.mkdir()
            atomic(folder/'debug-group.json',{'name':name})
        self.ui.refresh()
    def tearDown(self):self.catalog.stop();self.temp.cleanup()
    def test_arrows_search_group_name_and_number_and_tab(self):
        self.ui.key('', 'M');self.assertEqual(self.ui.group,200)
        self.ui.key('', 'K');self.assertEqual(self.ui.group,100)
        self.ui.key('\x06')
        for char in '音游':self.ui.key(char)
        self.ui.key('\t');self.assertEqual(self.ui.buffer,'200')
        self.ui.key('\r');self.assertEqual(self.ui.group,200)
        self.ui.execute('/group 100');self.assertEqual(self.ui.group,100)
    def test_command_prefixes_and_ambiguous_prefix_do_not_guess(self):
        self.ui.execute('/sta');self.assertIn('12800字符',self.ui.notice)
        self.ui.buffer='/sta';self.ui.complete();self.assertEqual(self.ui.buffer,'/status ')
        self.ui.buffer='/config set console_max_t';self.ui.complete()
        self.assertEqual(self.ui.buffer,'/config set console_max_text_chars ')
        with self.assertRaises(ValueError):self.ui.execute('/gr')
    def test_help_opens_at_top_scrolls_and_documents_all_executable_commands(self):
        from console_help import COMMANDS,help_text
        full=help_text()
        for command in COMMANDS:self.assertIn('/'+command,full)
        self.ui.execute('/help');first=self.ui.screen(100,24)
        self.assertIn('/help config',first);self.assertIn('/config',first)
        self.assertEqual(self.ui.panel['scroll'],0)
        self.ui.key('','Q');second=self.ui.screen(100,24)
        self.assertGreater(self.ui.panel['scroll'],0);self.assertNotEqual(first,second)
        self.ui.key('','I');self.assertEqual(self.ui.panel['scroll'],0)
        self.ui.key('\x1b');self.assertIsNone(self.ui.panel);self.assertEqual(self.ui.notice,'已返回聊天')
    def test_detailed_help_works_from_other_panels_and_completes_topics(self):
        for topic,expected in [('config','member_prompt_owner'),('member','/member block QQ号'),('system','--mode context|once|timed|persistent')]:
            self.ui.execute('/status');self.ui.execute('/help '+topic)
            self.assertEqual(self.ui.panel['kind'],'help');self.assertIn(expected,self.ui.notice)
        self.ui.buffer='/help mem';self.ui.complete();self.assertEqual(self.ui.buffer,'/help member ')
        with self.assertRaises(ValueError):self.ui.execute('/help nonexistent')
    def test_config_probability_name_prompt_and_capacity_validation_backup(self):
        self.ui.execute('/co rate 0.42');self.assertEqual(Config(self.root).load()['passive_probability'],.42)
        self.ui.execute('/config name alice');self.assertEqual(Config(self.root).load()['bot_name'],'alice')
        self.ui.execute('/config prompt 新提示词\\n第二行')
        self.assertEqual((self.root/'persona.txt').read_text(encoding='utf-8'),'新提示词\n第二行')
        self.ui.execute('/config text_limit 8000');self.assertEqual(Config(self.root).load()['console_max_text_chars'],8000)
        with self.assertRaises(ValueError):self.ui.execute('/config rate 5')
        self.assertEqual(Config(self.root).load()['passive_probability'],.42)
        self.assertTrue(list((self.root/'config-backups').glob('*.json')))
    def test_white_list_add_remove_and_preserve_at_least_one(self):
        self.ui.execute('/wh a 300');self.assertIn(300,self.ui.groups())
        self.ui.execute('/wh r 100');self.assertNotIn(100,self.ui.groups())
        self.ui.refresh();self.assertIn(self.ui.group,[200,300])
        self.ui.execute('/wh r 200')
        with self.assertRaises(ValueError):self.ui.execute('/wh r 300')
    def test_model_validation_failure_does_not_modify_file(self):
        previous=(self.root/'settings.json').read_bytes()
        with patch('console_config.validate_model',side_effect=ValueError('invalid model')):
            with self.assertRaises(ValueError):self.ui.execute('/config model invalid')
        self.assertEqual(previous,(self.root/'settings.json').read_bytes())
    def test_dynamic_name_and_new_group_delivery(self):
        config=Config(self.root);config.set('name','alice')
        with patch('dialogue.Config',return_value=config):
            self.assertTrue(dialogue.bot_name_mentioned('alice说话'))
            self.assertFalse(dialogue.bot_name_mentioned('yonagi说话'))
        with patch('emoji_library.read_json',return_value={'chat_group_whitelist':[100,200]}):
            plan=delivery_plan([{'type':'text','data':{'text':'hello'}}],200)
            self.assertEqual(plan[0]['params']['group_id'],200)
            with self.assertRaises(ValueError):delivery_plan([],300)
    def test_record_file_is_bounded_by_characters_and_tracks_actual_stages(self):
        Config(self.root).set('text_limit','500')
        events=DebugEvents(runtime(self.root,100),100)
        for index in range(10):events.emit('received',text='猫'*100,message_id=str(index),sender='群友')
        events.emit('decision',text='情绪=开心',role='emotion');events.close()
        rows=[json.loads(line) for line in (runtime(self.root,100)/'debug-events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertLessEqual(sum(len(row['text']) for row in rows if row['kind'] in {'received','sent'}),500)
        self.assertLessEqual(sum(len(row['text']) for row in rows if row['kind'] not in {'received','sent'}),500)
        self.ui.refresh();screen=self.ui.screen()
        self.assertIn('emotion',screen);self.assertIn('群友',screen)
        self.ui.execute('/view flow');self.assertNotIn('← 群友',self.ui.screen())
    def test_console_sanitizes_terminal_controls_and_log_tokens(self):
        self.assertNotIn('secret',safe_log('Bearer secret https://gchat.qpic.cn/a?token=secret'))
        self.assertNotIn('\x1b',''.join(wrap('\x1b[2J你好',4)))
        self.assertTrue(all(display_width(line)<=4 for line in wrap('你好hello',4)))
    def test_delete_cleans_local_records_context_evidence_and_pending_only_selected_group(self):
        folder=runtime(self.root,100);other=runtime(self.root,200)
        rows=[{'_id':'1','text':'原始消息'},{'_id':'2','text':'保留消息'}]
        for target in (folder,other):
            (target/'recent-chat.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
        atomic(folder/'pending-mentions.json',[{'_id':'3','_source_ids':['1','3']},{'_id':'2'}])
        atomic(folder/'style-memory.json',{'samples':[{'source_id':'1','text':'原始消息'},{'source_id':'2','text':'保留消息'}],'pairs':[{}]})
        memory=GroupMemory(folder/'group-memory.sqlite3');memory.observe({'_id':'1','_uid':'user','text':'喜欢音游','_time':time.time()})
        digest=memory.digest('1');memory.close()
        delete_local(self.root,100,'1')
        self.assertEqual(deleted_ids(folder),{'1'})
        self.assertNotIn('原始消息',(folder/'recent-chat.jsonl').read_text(encoding='utf-8'))
        self.assertIn('原始消息',(other/'recent-chat.jsonl').read_text(encoding='utf-8'))
        self.assertEqual(json.loads((folder/'pending-mentions.json').read_text(encoding='utf-8')),[{'_id':'2'}])
        memory=GroupMemory(folder/'group-memory.sqlite3')
        self.assertIsNone(memory.db.execute('SELECT id FROM sources WHERE id=?',(digest,)).fetchone());memory.close()
    def test_config_output_can_be_scrolled(self):
        self.ui.execute('/config show');self.ui.scroll=100000
        screen=self.ui.screen(100,30)
        self.assertIn('bot_name',screen)
        self.assertIn('PgUp',screen)


class SupervisorTests(unittest.TestCase):
    def test_real_windows_job_stop_and_whitelist_removal_do_not_stop_other_group(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            atomic(root/'settings.json',{'group_id':100,'chat_group_whitelist':[100,200],'enabled':True})
            (root/'bot.py').write_text('''import json,os,sys,time
from pathlib import Path
root=Path(__file__).parent
group=int(sys.argv[sys.argv.index('--group-id')+1]) if '--group-id' in sys.argv else 100
runtime=root/('runtime' if group==100 else 'runtime-'+str(group))
(runtime/'state.json').write_text(json.dumps({'pid':os.getpid(),'online':True}))
while not (runtime/'STOP').exists():time.sleep(.02)
(runtime/'state.json').write_text(json.dumps({'pid':os.getpid(),'online':False}))
''',encoding='utf-8')
            supervisor=Supervisor(root)
            try:
                supervisor.sync();deadline=time.time()+4
                while not all((runtime(root,g)/'state.json').exists() for g in [100,200]):
                    if time.time()>deadline:self.fail('stub workers did not start')
                    time.sleep(.02)
                first,second=supervisor.children[100],supervisor.children[200]
                atomic(root/'settings.json',{'group_id':100,'chat_group_whitelist':[100],'enabled':True})
                supervisor.sync()
                self.assertIsNotNone(second.poll());self.assertIsNone(first.poll())
                (runtime(root,100)/'STOP').touch();first.wait(timeout=4)
                supervisor.sync();self.assertIn(100,supervisor.paused)
                supervisor.sync();self.assertNotIn(100,supervisor.children)
                supervisor.paused.discard(100);supervisor.sync()
                resumed=supervisor.children[100]
                supervisor.close();self.assertIsNotNone(resumed.poll());self.assertFalse(supervisor.jobs)
            finally:supervisor.close()
