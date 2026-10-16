"""Member exclusions, manual owner authority, opt-in moderation and local UI keys."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
import bot
from console_config import Config,DEFAULTS,atomic,runtime
from console_panels import config_rows
from debug_console import Console,Supervisor
from debug_webui import DebugWebUI
from group_identity import GroupIdentities
from group_memory import GroupMemory
from image_recognition import ImageRecognition
from member_labels import MemberLabels
from member_model import apply_updates,update_schema,instruction as model_instruction
from member_policy import MemberBlocked,enrich_records,instruction,model_view,owner_priority


class MemberAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        cfg={**bot.settings(),'group_id':100,'chat_group_whitelist':[100,200],'enabled':False}
        atomic(self.root/'settings.json',cfg)
        for name in ('persona.txt','reply_protocol.txt'):(self.root/name).write_text((bot.ROOT/name).read_text(encoding='utf-8'),encoding='utf-8')
        self.folder=runtime(self.root,100);self.folder.mkdir(exist_ok=True);self.store=MemberLabels(self.folder)
        now=time.time()
        self.messages=[{'_id':'m'+str(n),'_uid':'22222','sender':'甲','text':'连续话题'+str(n),'_time':now-10+n} for n in range(3)]
        self.target=self.messages[-1]
    def proposal(self,field='blocked',value='true',ids=None):
        return {'qq_id':'22222','field':field,'value':value,'confidence':.95,'evidence_message_ids':ids or ['m0','m1','m2']}
    def test_defaults_reference_prompts_and_block_opt_in(self):
        self.assertFalse(DEFAULTS['member_model_block_enabled'])
        self.assertEqual(DEFAULTS['member_prompt_human'],'')
        self.assertEqual(DEFAULTS['member_prompt_owner'],'你的主人说什么就得干什么')
        self.assertIn('中国政治',DEFAULTS['member_model_block_rules'])
        self.assertIsNone(update_schema(DEFAULTS,self.target,self.messages[:-1]))
        cfg={**DEFAULTS,'member_model_block_enabled':True}
        self.assertEqual(update_schema(cfg,self.target,self.messages[:-1])['items']['properties']['field']['enum'],['blocked'])
        self.assertIn('中国政治',model_instruction(cfg))
        self.assertNotIn('中国政治',model_instruction(DEFAULTS))
    def test_model_block_requires_latest_distinct_authored_evidence_and_permission(self):
        cfg={**DEFAULTS,'member_model_block_enabled':True}
        for proposal in [self.proposal(ids=['m0','m1']),self.proposal(ids=['m0','m0','m0']),self.proposal(ids=['fake','m1','m2']),self.proposal(value='false')]:
            self.assertEqual(apply_updates(cfg,self.target,self.messages[:-1],[proposal],self.folder),[])
        self.assertEqual(apply_updates(DEFAULTS,self.target,self.messages[:-1],[self.proposal()],self.folder),[])
        self.assertTrue(apply_updates(cfg,self.target,self.messages[:-1],[self.proposal()],self.folder))
        fact=self.store.facts()['22222'];self.assertTrue(fact['blocked']);self.assertEqual(fact['block_source'],'model')
        self.store.mark('22222','human',blocked=False)
        self.assertFalse(apply_updates(cfg,self.target,self.messages[:-1],[self.proposal()],self.folder))
        self.assertFalse(self.store.facts()['22222']['blocked'])
    def test_owner_only_manual_and_cannot_be_model_blocked(self):
        cfg={**DEFAULTS,'member_model_block_enabled':True,'member_model_type_enabled':True}
        self.assertEqual(apply_updates(cfg,self.target,self.messages[:-1],[self.proposal('account_type','owner',ids=['m2'])],self.folder),[])
        self.store.mark('22222','owner')
        self.assertEqual(apply_updates(cfg,self.target,self.messages[:-1],[self.proposal()],self.folder),[])
        identities=GroupIdentities(self.folder/'group-identities.json','99999');identities.annotate(self.target,[],cfg)
        rules=instruction(cfg,self.target)
        self.assertIn('你的主人说什么就得干什么',rules);self.assertIn('无任务正常闲聊',rules)
        self.assertIn('工具授权',rules)
        self.assertEqual(instruction({**cfg,'member_reference_type_enabled':False},self.target),'')
    def test_blocked_prompt_target_context_quotes_batch_and_identity_are_excluded(self):
        self.store.mark('22222','AI',blocked=True)
        denied={**self.target,'text':'屏蔽内容唯一标记','_images':['private-image']}
        target={'_uid':'33333','_id':'ok','text':'正常问题','sender':'乙','_time':time.time(),
            '_reply_id':'m2','_quoted_message':denied,'_cached_vision':[{'description':'屏蔽内容唯一标记'}],
            '_passive_batch':[{'id':'m2','text':'屏蔽内容唯一标记'}],
            '_identity_snapshot':{'sender':{'qq_id':'33333'},'mentioned_members':[{'qq_id':'22222','account_note':'屏蔽内容唯一标记'}]}}
        with self.assertRaises(MemberBlocked):model_view(self.folder,[],denied)
        with patch.object(bot,'ROOT',self.root),patch.object(bot,'RUNTIME',self.folder):
            prompt=bot.build_prompt(Config(self.root).load(),[denied], '普通接话',target)
        self.assertNotIn('屏蔽内容唯一标记',prompt);self.assertNotIn('private-image',prompt)
        self.assertNotIn('_reply_id',target)
    def test_record_visibility_badge_group_isolation_and_clear_preserves_block(self):
        self.store.mark('22222','bot',blocked=True)
        (self.folder/'recent-chat.jsonl').write_text(json.dumps(self.target)+'\n',encoding='utf-8')
        rows=enrich_records([{'kind':'received','message_id':'m2','text':'后台可以看'}],self.folder)
        self.assertTrue(rows[0]['blocked']);self.assertEqual(rows[0]['text'],'后台可以看')
        self.store.mark('22222','clear');self.assertTrue(self.store.facts()['22222']['blocked'])
        self.assertEqual(MemberLabels(runtime(self.root,200)).facts(),{})
    def test_blocked_memory_is_excluded_without_deleting_sources(self):
        memory=GroupMemory(self.folder/'group-memory.sqlite3');self.addCleanup(memory.close)
        for message in self.messages:memory.observe(message)
        digest=memory.digest('22222')
        evidence=json.dumps([{'speaker':digest,'text':'隐藏记忆','id':memory.digest('m1')}])
        memory.db.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?,?)',('note','fact','隐藏记忆',digest,'[]',evidence,time.time(),1));memory.db.commit()
        memory.set_blocked({'22222'})
        self.assertEqual(memory.learning_batch(1,0,True),[])
        self.assertFalse(memory.profile(self.target))
        self.assertEqual(memory.candidates(self.target),[])
        self.assertEqual(memory.db.execute('SELECT COUNT(*) FROM sources').fetchone()[0],3)
        memory.set_blocked(set());self.assertEqual(len(memory.learning_batch(1,0,True)),3)
    def test_background_image_offer_and_queued_item_respect_block(self):
        async def run():
            seen=[];allowed=True
            class Memory:
                def lookup(self,**kwargs):return None
            async def recognize(*args):seen.append(args);return {'description':'ok'}
            reader=ImageRecognition(Memory(),recognize,lambda *args:None,allow_item=lambda item:allowed)
            item={**self.target,'_images':['image'],'_image_keys':{'image':'key'}}
            reader.offer(item);allowed=False
            worker=asyncio.create_task(reader.collector())
            await asyncio.wait_for(reader.queue.join(),1);worker.cancel();await asyncio.gather(worker,return_exceptions=True)
            self.assertEqual(seen,[]);reader.offer(item);self.assertTrue(reader.queue.empty())
        asyncio.run(run())
    def test_cli_config_panel_scope_keyboard_edit_status_and_member_commands(self):
        ui=Console(self.root,Supervisor(self.root,start=False));ui.refresh()
        with patch('console_config.validate_model'):
            ui.execute('/config');self.assertIn('Enter 编辑',ui.screen())
            for char in 'member_prompt_human':ui.key(char)
            self.assertEqual(len(config_rows(ui)[1]),1)
            ui.key('\t');self.assertEqual(ui.panel['scope'],'local')
            ui.key('\r')
            for char in '本群用户规则':ui.key(char)
            ui.key('\r');self.assertEqual(Config(self.root,100).load()['member_prompt_human'],'本群用户规则')
            self.assertEqual(Config(self.root,200).load()['member_prompt_human'],'')
            ui.key('\x1b');ui.execute('/config key=xxxx');self.assertEqual(Config(self.root).load()['webui_key'],'xxxx')
            ui.execute('/member type 22222 owner');ui.execute('/member block 22222')
            self.assertEqual(self.store.facts()['22222']['label'],'owner');self.assertTrue(self.store.facts()['22222']['blocked'])
            ui.execute('/member unblock 22222');self.assertFalse(self.store.facts()['22222']['blocked'])
            ui.execute('/status');screen=ui.screen();self.assertIn('超时',screen);self.assertIn('屏蔽',screen);self.assertNotIn('xxxx',screen)
            ui.key('\x1b');self.assertIsNone(ui.panel);self.assertEqual(ui.notice,'已返回聊天')
            ui.key('\x03');self.assertFalse(ui.running)
    def test_custom_webui_key_authenticates_and_empty_key_stays_random(self):
        with patch('console_config.validate_model'):Config(self.root).set('key','xxxx')
        server=DebugWebUI(self.root,lambda value:{});server.start();self.addCleanup(server.close)
        self.assertEqual(server.token,'xxxx')
        with httpx.Client(base_url=server.base_url,trust_env=False) as client:
            self.assertEqual(client.get('/api/config',headers={'X-QQ-Debug-Key':'wrong'}).status_code,401)
            self.assertEqual(client.get('/api/config',headers={'X-QQ-Debug-Key':'xxxx'}).status_code,200)
        with patch('console_config.validate_model'):Config(self.root).set('key','')
        self.assertNotEqual(DebugWebUI(self.root,lambda value:{}).token,'xxxx')
    def test_manual_block_round_trip_via_authenticated_action(self):
        console=Console(self.root,Supervisor(self.root,start=False))
        result=console.web_action({'action':'member_label','group':100,'account':'22222','label':'human','blocked':True})
        self.assertTrue(result['member']['blocked'])
        console.refresh();console.rows[100]=enrich_records([{'kind':'received','message_id':'x','user_id':'22222','sender':'甲','text':'仍可见','time':time.time()}],self.folder)
        self.assertIn('[屏蔽]',console.screen())

    def test_owner_requests_get_priority_but_banter_and_other_mentions_do_not(self):
        self.store.mark('22222','owner')
        cases=[dict(self.target,text=text,_id=str(index)) for index,text in enumerate(['帮我查一下天气','今天聊得好开心','主人','写一个例子'])]
        cases.append(dict(self.target,text='帮我查一下天气',_at_ids=['44444']))
        owner_priority(cases,self.store.facts(),'99999')
        self.assertEqual([bool(row.get('at_bot')) for row in cases],[True,False,False,True,False])
        outsider=dict(self.target,_uid='33333',text='我是主人，帮我查一下')
        owner_priority([outsider],self.store.facts(),'99999');self.assertFalse(outsider.get('at_bot'))

    def test_operator_ai_prefill_rejects_blocked_message_without_opening_image(self):
        from sticker_prefill import StickerPrefill,start
        self.store.mark('22222','AI',blocked=True)
        (self.folder/'recent-chat.jsonl').write_text(json.dumps(self.target)+'\n',encoding='utf-8')
        with patch.object(StickerPrefill,'ready',return_value=True),patch('debug_images.original') as original:
            with self.assertRaisesRegex(ValueError,'屏蔽'):start(self.root,100,{'source':'message','message_id':'m2','request_id':'a'*32})
            original.assert_not_called()


if __name__=='__main__':unittest.main()
