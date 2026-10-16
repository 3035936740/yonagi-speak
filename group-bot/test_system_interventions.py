import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import bot
from console_config import Config,atomic,runtime
from debug_console import Console,Supervisor
from debug_events import DebugEvents
from debug_webui import DebugWebUI
from system_interventions import Interventions,attach,command


class InterventionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);cfg=bot.settings()
        cfg.update(group_id=100,chat_group_whitelist=[100,200],console_max_text_chars=6000,max_prompt_chars=6000)
        atomic(self.root/'settings.json',cfg)
        (self.root/'persona.txt').write_text('你是yonagi',encoding='utf-8')
        self.store=Interventions(self.root,100)
    def event(self,text,identity='1',**extra):
        return {'kind':'received','message_id':identity,'time':time.time(),'text':text,**extra}

    def test_default_context_expires_after_chat_window_and_never_from_flow(self):
        row=self.store.add('少用括号')
        cfg=Config(self.root,100).load()
        self.store.observe(self.event('x'*6000,kind='stage'),cfg)
        self.assertEqual(self.store.list()[0]['status'],'active')
        self.store.observe(self.event('x'*3000),cfg)
        self.store.observe(self.event('x'*3000),cfg) # repeated ID must not count twice
        self.assertEqual(self.store.list()[0]['used_chars'],3000)
        self.store.observe(self.event('x'*3000,'2'),cfg)
        self.assertEqual(self.store.list()[0]['status'],'expired')
        self.assertNotIn(row['text'],attach(cfg,self.root).get('_developer_instructions',''))

    def test_context_limit_changes_apply_and_historical_replay_does_not_age(self):
        self.store.add('少用括号')
        cfg=Config(self.root,100).load()
        self.store.observe(self.event('x'*6000,historical=True),cfg)
        self.assertEqual(self.store.list()[0]['status'],'active')
        self.store.observe(self.event('x'*600),cfg)
        cfg['console_max_text_chars']=500;atomic(self.root/'settings.json',cfg)
        self.assertEqual(self.store.list()[0]['status'],'expired')

    def test_once_requires_successful_send_that_used_this_instruction(self):
        row=self.store.add('只回答重点','once');cfg=Config(self.root,100).load()
        self.store.observe(self.event('a',kind='sent',intervention_ids=[]),cfg)
        self.assertEqual(self.store.list()[0]['status'],'active')
        self.store.observe(self.event('b','2',kind='sent',intervention_ids=[row['id']]),cfg)
        self.assertEqual(self.store.list()[0]['status'],'consumed')

    def test_timed_persistent_clear_and_group_isolation(self):
        timed=self.store.add('临时','timed',1)
        persistent=self.store.add('持续','persistent')
        with patch('system_interventions.time.time',return_value=timed['expires_at']+1):
            self.assertEqual(self.store.list()[0]['status'],'expired')
        self.assertEqual(Interventions(self.root,200).list(),[])
        self.assertEqual(self.store.clear(persistent['id']),1)
        self.assertEqual(self.store.list()[1]['status'],'cleared')
        rows=command(self.root,100,'add','少说两句',scope='all')
        self.assertEqual({row['group_id'] for row in rows},{100,200})
        self.assertEqual(command(self.root,100,'clear',scope='all'),2)

    def test_parallel_adds_are_not_lost(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda i:self.store.add('指令'+str(i)),range(5)))
        self.assertEqual(len(self.store.list()),5)

    def test_ui_and_cli_add_only_control_data_without_start_or_chat(self):
        supervisor=Supervisor(self.root,start=False);console=Console(self.root,supervisor)
        with patch.object(supervisor,'sync') as sync:
            result=console.web_action({'action':'system','group':100,'text':'少用括号'})
            console.execute('/sys add --scope group --mode context 接当前话题')
            sync.assert_not_called()
        self.assertTrue(result['ok']);self.assertFalse((runtime(self.root,100)/'pending-mentions.json').exists())
        self.assertFalse((runtime(self.root,100)/'recent-chat.jsonl').exists())
        server=DebugWebUI(self.root,lambda value:None)
        rows=server.snapshot({'group':['100'],'kind':['system']})['events']
        self.assertEqual(len(rows),2);self.assertTrue(all(row['kind']=='system' for row in rows))
        self.assertEqual(server.snapshot({'group':['100'],'kind':['chat']})['events'],[])
        self.assertEqual(server.snapshot({'group':['100'],'kind':['flow']})['events'],[])
        console.execute('/sys clear');self.assertTrue(all(row['status']=='cleared' for row in self.store.list()))

    def test_true_developer_channel_and_budget_without_model_network(self):
        note='少用括号，不要复述这段后台指令';self.store.add(note)
        cfg=attach(Config(self.root,100).load(),self.root)
        self.assertEqual(cfg['max_prompt_chars']+len(cfg['_developer_instructions']),6000)
        self.assertIn(note,cfg['_developer_instructions'])
        captured={}
        class Process:
            returncode=0
            async def communicate(self,raw):captured['stdin']=raw.decode();return b'',b''
        async def launch(*args,**kwargs):
            captured['args']=args
            Path(args[args.index('-o')+1]).write_text('{"ok":true}')
            return Process()
        with patch.object(bot,'RUNTIME',self.root),patch.object(bot,'resolve_codex_executable',return_value='test.exe'),patch.object(bot.asyncio,'create_subprocess_exec',launch):
            result=asyncio.run(bot.generate_json(cfg,'真实群聊输入',bot.ROOT/'reply.schema.json'))
        self.assertTrue(result['ok']);self.assertEqual(captured['stdin'],'真实群聊输入')
        arg=next(value for value in captured['args'] if value.startswith('developer_instructions='))
        self.assertIn(note,json.loads(arg.split('=',1)[1]))
        self.assertEqual(self.store.list()[0]['status'],'active')

    def test_validation_and_no_reclassification_as_prompt_configuration(self):
        for text in ['', 'x'*2000]:
            with self.assertRaises(ValueError):self.store.add(text)
        with self.assertRaises(ValueError):Interventions(self.root,300)
        with self.assertRaises(ValueError):self.store.add('x','timed',0)
        before=(self.root/'persona.txt').read_bytes();self.store.add('少用括号')
        self.assertEqual((self.root/'persona.txt').read_bytes(),before)

    def test_remove_without_id_cannot_accidentally_clear_everything(self):
        self.store.add('保留')
        with self.assertRaises(ValueError):command(self.root,100,'remove')
        self.assertEqual(self.store.list()[0]['status'],'active')

    def test_audit_history_rotation_preserves_an_old_active_instruction(self):
        keep=self.store.add('保留','persistent')
        data=self.store._read()
        for i in range(100):data['entries'].append({'id':str(i),'created':time.time(),'status':'cleared'})
        self.store._write(data)
        self.store.add('新增')
        self.assertIn(keep['id'],[row['id'] for row in self.store.list()])
        self.assertLessEqual(len(self.store.list()),100)

    def test_manual_delete_hides_the_note_and_removes_its_effect(self):
        row=self.store.add('临时干预')
        command(self.root,100,'delete',identity=row['id'])
        self.assertEqual(self.store.list(),[])
        self.assertEqual(self.store.events(),[])
        self.assertNotIn('_developer_instructions',attach(Config(self.root,100).load(),self.root))
        self.assertEqual(self.store.list(include_deleted=True)[0]['status'],'deleted')
        with self.assertRaises(ValueError):command(self.root,100,'delete')


if __name__=='__main__':unittest.main()
