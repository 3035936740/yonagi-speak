import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock,patch
import bot
from console_config import atomic,runtime
from debug_console import Console,Supervisor
from debug_webui import DebugWebUI
from debug_events import DebugEvents
from manual_images import segments,display_text
from recent_chat_journal import RecentChatJournal
import test_manual_outbox as manual_tests
from test_manual_images import attachment,picture


class ManualTargetTests(unittest.TestCase):
    def setUp(self):
        manual_tests.ManualSendTests.setUp(self)
        atomic(self.root/'runtime/group-identities.json',{'members':[{'qq_id':'123456','card':'群友A'},{'qq_id':'234567','nickname':'群友B'}]})
        self.journal.append({'_id':'111','_uid':'123456','sender':'群友A','text':'原消息','_time':time.time()})

    def test_native_reply_mentions_and_images_are_one_send_in_order(self):
        row=self.box.enqueue('前[@123456]中[图片1]后','a'*32,[attachment(picture())],'111',['123456','234567'])
        sent=segments(self.box.folder,row)
        self.assertEqual([part['type'] for part in sent],['reply','at','text','text','at','text','image','text'])
        self.assertEqual(sent[0]['data'],{'id':'111'});self.assertEqual(sent[1]['data'],{'qq':'234567'})
        self.assertEqual(display_text(row),'@群友B 前@群友A中[图片]后')
        api=AsyncMock(return_value={'message_id':800});record=unittest.mock.Mock()
        asyncio.run(self.box.drain(api,None,{},record,self.cfg));asyncio.run(self.box.drain(api,None,{},record,self.cfg))
        self.assertEqual(api.await_count,1);self.assertEqual(record.call_count,1)
        self.assertEqual(record.call_args.args[0]['quote']['text'],'原消息')
        self.assertEqual(self.box.enqueue('前[@123456]中[图片1]后','a'*32,[attachment(picture())],'111',['123456','234567'])['status'],'sent')
        with self.assertRaises(ValueError):self.box.enqueue('前[@123456]中[图片1]后','a'*32,[attachment(picture())],'111',['123456'])

    def test_invalid_deleted_and_cross_group_targets_and_old_workers_are_rejected(self):
        for ids in [['all'],[True],['123'],['999999'],['123456']*9]:
            with self.subTest(ids=ids),self.assertRaises(ValueError):self.box.enqueue('test',at_ids=ids)
        for reply in ['../../x','999',True]:
            with self.subTest(reply=reply),self.assertRaises(ValueError):self.box.enqueue('test',reply_id=reply)
        other=runtime(self.root,200);other.mkdir(parents=True,exist_ok=True)
        (other/'debug-events.jsonl').write_text(json.dumps({'group_id':200,'kind':'received','message_id':'222','user_id':'999999'}))
        with self.assertRaises(ValueError):self.box.enqueue('test',reply_id='222')
        atomic(self.root/'runtime/deleted-messages.json',['111'])
        with self.assertRaises(ValueError):self.box.enqueue('test',reply_id='111')
        atomic(self.root/'runtime/manual-worker.json',{'pid':333,'version':2})
        with self.assertRaisesRegex(ValueError,'尚未加载手动回复'):self.box.enqueue('test',at_ids=['123456'])
        self.assertEqual(self.box.enqueue('旧后台纯文字')['status'],'queued')

    def test_at_only_is_native_and_unselected_text_tokens_stay_literal(self):
        row=self.box.enqueue('',at_ids=['123456','123456'])
        self.assertEqual(row['at_ids'],['123456']);self.assertEqual(segments(self.box.folder,row)[0],{'type':'at','data':{'qq':'123456'}})
        self.assertEqual(segments(self.box.folder,self.box.enqueue('[@123456]')),[{'type':'text','data':{'text':'[@123456]'}}])
        with self.assertRaises(ValueError):self.box.enqueue('[@123456][@123456]',at_ids=['123456'])

    def test_real_worker_keeps_quote_and_mentions_in_journal_memory_and_events(self):
        request=self.box.enqueue('[@123456]回应',reply_id='111',at_ids=['123456'])
        cfg={**self.cfg,'poll_seconds':.01,'mcp_env_file':str(self.root/'empty.env'),'shared_sticker_library':False,'proactive_topics_enabled':False}
        (self.root/'empty.env').write_text('NAPCAT_HOST=http://invalid.test\nNAPCAT_TOKEN=test\n')
        sent=[]
        async def api(client,env,action,params=None):
            if action=='send_group_msg':sent.append(params);(self.root/'runtime/STOP').touch();return {'message_id':801}
            if action=='get_group_msg_history':return {'messages':[]}
            if action=='get_group_member_list':return []
            return {}
        with patch.object(bot,'ROOT',self.root),patch.object(bot,'RUNTIME',self.root/'runtime'),patch.object(bot,'settings',return_value=cfg),\
            patch.object(bot.debug_events,'CURRENT',DebugEvents(self.root/'runtime',100)),\
            patch.object(bot,'api',api),patch.object(bot,'wait_for_qq',new=AsyncMock(return_value=({'user_id':169901502,'nickname':'yonagi'},{'group_name':'测试群'}))),\
            patch.object(bot,'generate_json',new=AsyncMock()) as model:
            asyncio.run(asyncio.wait_for(bot._run(),3));model.assert_not_called()
        self.assertEqual(len(sent),1);self.assertEqual([part['type'] for part in sent[0]['message']],['reply','at','text'])
        item=next(row for row in RecentChatJournal(self.journal.path).load() if row['_id']=='801')
        self.assertEqual(item['_reply_id'],'111');self.assertEqual(item['_at_ids'],['123456'])
        self.assertEqual(item['_quoted_message']['text'],'原消息');self.assertEqual(item['text'],'@群友A回应')
        text=self.memory.db.execute("SELECT text FROM manual_messages WHERE id='801'").fetchone()[0]
        self.assertIn('回复 群友A：原消息',text);self.assertIn('@群友A回应',text)
        event=next(json.loads(line) for line in (self.root/'runtime/debug-events.jsonl').read_text(encoding='utf-8').splitlines() if json.loads(line).get('message_id')=='801')
        self.assertEqual(event['reply_id'],'111');self.assertEqual(event['at_ids'],['123456']);self.assertEqual(event['quote']['text'],'原消息')
        self.assertEqual(self.box.get(request['id'])['status'],'sent')

    def test_web_action_and_worker_capability(self):
        console=Console(self.root,Supervisor(self.root,start=False));server=DebugWebUI(self.root,console.web_action)
        result=console.web_action({'action':'manual_send','group':100,'text':'回应','reply_id':'111','at_ids':['123456']})
        self.assertEqual(result['request']['reply_id'],'111');self.assertEqual(result['request']['at_ids'],['123456'])
        snapshot=server.snapshot({'group':['100']});self.assertIn('manual_targets',snapshot['features']);self.assertTrue(snapshot['status']['manual_targets_ready'])
        atomic(self.root/'runtime/manual-worker.json',{'pid':333,'version':2})
        self.assertFalse(server.snapshot({'group':['100']})['status']['manual_targets_ready'])


if __name__=='__main__':unittest.main()
