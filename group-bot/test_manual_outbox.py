import asyncio
from collections import deque
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch

import bot
from console_config import Config,atomic,delete_local,runtime
from debug_console import Console,Supervisor
from debug_webui import DebugWebUI
from group_memory import GroupMemory
from manual_outbox import ManualOutbox
from recent_chat_journal import RecentChatJournal


class ManualSendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        cfg=bot.settings();cfg.update(group_id=100,chat_group_whitelist=[100,200],enabled=True)
        atomic(self.root/'settings.json',cfg);(self.root/'persona.txt').write_text('yonagi',encoding='utf-8')
        self.box=ManualOutbox(self.root,100);self.box.mark_ready(333)
        atomic(self.root/'runtime/state.json',{'online':True,'pid':333})
        self.alive=patch('manual_outbox.pid_alive',return_value=True);self.alive.start();self.addCleanup(self.alive.stop)
        self.cfg=Config(self.root,100).load()
        self.memory=GroupMemory(self.root/'runtime/group-memory.sqlite3');self.addCleanup(self.memory.close)
        self.journal=RecentChatJournal(self.root/'runtime/recent-chat.jsonl');self.journal.load();self.history=deque()

    def record(self,row):
        item={'sender':'yonagi','text':row['text'],'is_bot':True,'_manual':True,'_manual_request_id':row['id'],
              '_uid':'169901502','_id':row['message_id'],'_time':row['sent_at']}
        self.memory.record_manual(item,row['id']);self.journal.append(item)
        if not any(old['_id']==item['_id'] for old in self.history):self.history.append(item)

    def test_success_is_literal_bot_speech_in_context_and_memory_once(self):
        text='这是我手动写的，不需要模型改写（'
        row=self.box.enqueue(text,'a'*32);api=AsyncMock(return_value={'message_id':555})
        with patch.object(bot,'generate_json') as model:
            asyncio.run(self.box.drain(api,None,{},self.record,self.cfg))
            asyncio.run(self.box.drain(api,None,{},self.record,self.cfg))
            model.assert_not_called()
        self.assertEqual(api.await_count,1);self.assertEqual(api.call_args.args[2],'send_group_msg')
        self.assertEqual(api.call_args.args[3],{'group_id':100,'message':[{'type':'text','data':{'text':text}}]})
        self.assertEqual(self.box.get(row['id'])['status'],'sent')
        self.assertEqual(self.journal.records[0]['text'],text);self.assertTrue(self.journal.records[0]['is_bot'])
        self.assertEqual(self.memory.stats()['manual_messages'],1);self.assertEqual(self.memory.stats()['sources'],0)
        self.assertFalse(self.journal.append(self.history[0])) # QQ echo cannot add another copy
        self.assertEqual(self.box.enqueue(text,'a'*32)['status'],'sent')
        self.assertEqual(len(self.history),1)

    def test_record_failure_retries_only_local_record_never_the_qq_send(self):
        row=self.box.enqueue('测试');api=AsyncMock(return_value={'message_id':556})
        def fail(_):raise PermissionError('locked journal')
        asyncio.run(self.box.drain(api,None,{},fail,self.cfg))
        self.assertEqual(self.box.get(row['id'])['status'],'record_pending')
        asyncio.run(self.box.drain(api,None,{},self.record,self.cfg))
        self.assertEqual(api.await_count,1);self.assertEqual(self.box.get(row['id'])['status'],'sent')

    def test_timeout_marks_uncertain_and_does_not_retry_or_make_up_memory(self):
        row=self.box.enqueue('超时');api=AsyncMock(side_effect=TimeoutError())
        asyncio.run(self.box.drain(api,None,{},self.record,self.cfg))
        asyncio.run(self.box.drain(api,None,{},self.record,self.cfg))
        self.assertEqual(api.await_count,1);self.assertEqual(self.box.get(row['id'])['status'],'uncertain')
        self.assertEqual(self.memory.stats()['manual_messages'],0);self.assertEqual(len(self.history),0)

    def test_stale_sending_or_expired_queue_is_not_sent_after_restart(self):
        sending=self.box.enqueue('未知');sending['status']='sending';self.box.write(sending)
        old=self.box.enqueue('过期');old['created']=time.time()-400;self.box.write(old)
        api=AsyncMock();asyncio.run(self.box.drain(api,None,{},self.record,self.cfg));api.assert_not_called()
        self.assertEqual(self.box.get(sending['id'])['status'],'uncertain');self.assertEqual(self.box.get(old['id'])['status'],'expired')

    def test_disabled_or_old_worker_cannot_silently_accept_undeliverable_messages(self):
        for text in ['','x'*2001]:
            with self.assertRaises(ValueError):self.box.enqueue(text)
        with self.assertRaises(ValueError):ManualOutbox(self.root,300)
        (self.root/'runtime/manual-worker.json').unlink()
        with self.assertRaisesRegex(ValueError,'尚未加载'):self.box.enqueue('旧进程')
        self.box.mark_ready(333);atomic(self.root/'runtime/state.json',{'online':False,'pid':333})
        with self.assertRaises(ValueError):self.box.enqueue('离线')

    def test_web_and_cli_only_enqueue_and_acknowledge_actual_delivery_later(self):
        console=Console(self.root,Supervisor(self.root,start=False))
        result=console.web_action({'action':'manual_send','group':100,'text':'网页手动消息','request_id':'b'*32})
        console.execute('/say CLI手动消息')
        self.assertEqual(result['request']['status'],'queued');self.assertEqual(self.memory.stats()['manual_messages'],0)
        server=DebugWebUI(self.root,console.web_action)
        self.assertIn('manual_send',server.snapshot({'group':['100']})['features'])
        self.assertTrue(server.snapshot({'group':['100']})['status']['manual_send_ready'])
        self.assertEqual(self.journal.records,[])

    def test_deleting_local_manual_message_clears_its_context_and_memory(self):
        row=self.box.enqueue('可删除');api=AsyncMock(return_value={'message_id':557})
        asyncio.run(self.box.drain(api,None,{},self.record,self.cfg))
        delete_local(self.root,100,'557')
        self.assertEqual(self.memory.stats()['manual_messages'],0)
        self.assertNotIn('557',[item['_id'] for item in RecentChatJournal(self.journal.path).load()])

    def test_manual_message_preserves_up_to_2000_characters_in_saved_context(self):
        row=self.box.enqueue('字'*1800);api=AsyncMock(return_value={'message_id':558})
        asyncio.run(self.box.drain(api,None,{},self.record,self.cfg))
        self.assertEqual(len(RecentChatJournal(self.journal.path).load()[0]['text']),1800)

    def test_real_worker_loop_records_manual_send_without_a_model_call(self):
        # Exercise the actual loop/callback, while replacing only external I/O.
        cfg={**self.cfg,'poll_seconds':.01,'mcp_env_file':str(self.root/'empty.env'),
             'shared_sticker_library':False,'proactive_topics_enabled':False}
        (self.root/'empty.env').write_text('NAPCAT_HOST=http://invalid.test\nNAPCAT_TOKEN=test\n')
        row={'id':'c'*32,'group_id':100,'text':'实际工作循环的手动消息','created':time.time(),'status':'queued'}
        self.box.write(row);calls=[]
        async def api(client,env,action,params=None):
            calls.append(action)
            if action=='send_group_msg':
                (self.root/'runtime/STOP').touch();return {'message_id':559}
            if action=='get_group_msg_history':return {'messages':[]}
            if action=='get_group_member_list':return []
            return {}
        with patch.object(bot,'ROOT',self.root),patch.object(bot,'RUNTIME',self.root/'runtime'),\
             patch.object(bot,'settings',return_value=cfg),patch.object(bot,'api',api),\
             patch.object(bot,'wait_for_qq',new=AsyncMock(return_value=({'user_id':169901502,'nickname':'yonagi'},{'group_name':'测试群'}))),\
             patch.object(bot,'generate_json',new=AsyncMock()) as model:
            asyncio.run(asyncio.wait_for(bot._run(),timeout=3))
            model.assert_not_called()
        self.assertEqual(calls.count('send_group_msg'),1)
        self.assertEqual(self.box.get('c'*32)['status'],'sent')
        self.assertEqual(RecentChatJournal(self.journal.path).load()[0]['_id'],'559')
        self.assertEqual(self.memory.db.execute("SELECT text FROM manual_messages WHERE id='559'").fetchone()[0],row['text'])


if __name__=='__main__':unittest.main()
