"""Profile reference switches and opt-in writes, without live model or QQ calls."""
import asyncio
import itertools
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import bot
from console_config import Config,DEFAULTS,atomic,runtime
from group_identity import GroupIdentities,identity_facts,resolve_names
from member_labels import MemberLabels
from member_model import FIELDS,apply_updates,update_schema
from role_analysis import analyze


class MemberModelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.target={'_id':'message1','_uid':'22222','_self_id':'99999','text':'叫我昵称B就好','sender':'原昵称','_time':time.time()}
        self.folder=self.root/'runtime';self.store=MemberLabels(self.folder)
    def update(self,field,value,**kwargs):
        return {'qq_id':'22222','field':field,'value':value,'confidence':.95,'evidence_message_ids':['message1'],**kwargs}
    def apply(self,updates,**cfg):
        return apply_updates(cfg,self.target,[],updates,self.folder)
    def annotate(self):
        identities=GroupIdentities(self.folder/'group-identities.json','99999')
        identities.observe({'user_id':'22222','nickname':'原昵称'})
        identities.annotate(self.target,[],{})
    def test_reference_and_write_defaults_inherit_per_group(self):
        for key in ('type','aliases','note'):
            self.assertTrue(DEFAULTS['member_reference_'+key+'_enabled'])
            self.assertFalse(DEFAULTS['member_model_'+key+'_enabled'])
        cfg={**bot.settings(),'group_id':100,'chat_group_whitelist':[100,200]};atomic(self.root/'settings.json',cfg)
        with patch('console_config.validate_model'):
            Config(self.root).set('member_reference_note_enabled','off')
            Config(self.root,100).set('member_reference_note_enabled','on')
            Config(self.root,100).set('member_model_note_enabled','on')
        self.assertTrue(Config(self.root,100).load()['member_reference_note_enabled'])
        self.assertFalse(Config(self.root,200).load()['member_reference_note_enabled'])
        self.assertTrue(Config(self.root,100).load()['member_model_note_enabled'])
        self.assertFalse(Config(self.root,200).load()['member_model_note_enabled'])
    def test_each_reference_switch_filters_actual_prompt_and_name_resolution(self):
        self.store.mark('22222','AI','资料说明唯一标记',['昵称B'])
        self.target['text']='昵称B是谁';self.annotate()
        for types,aliases,note in itertools.product((False,True),repeat=3):
            cfg={**bot.settings(),'member_reference_type_enabled':types,'member_reference_aliases_enabled':aliases,'member_reference_note_enabled':note}
            facts=identity_facts(cfg,self.target)['sender']
            self.assertEqual('account_label' in facts,types)
            self.assertEqual('extra_names' in facts,aliases)
            self.assertEqual('account_note' in facts,note)
            prompt=bot.build_prompt(cfg,[],'接话',self.target)
            self.assertEqual('资料说明唯一标记' in prompt,note)
            self.assertEqual(bool(resolve_names(cfg,self.target)),aliases)
        self.assertEqual(self.store.facts()['22222']['note'],'资料说明唯一标记')
    def test_write_permissions_are_independent_of_reference_permissions(self):
        updates=[self.update('account_type','AI'),self.update('extra_names','昵称B, aaaa'),self.update('account_note','喜欢节奏游戏')]
        self.assertEqual(self.apply(updates),[])
        self.assertFalse(self.store.path.exists())
        changed=self.apply(updates,member_model_note_enabled=True,member_reference_note_enabled=False)
        self.assertEqual([item['field'] for item in changed],['account_note'])
        fact=self.store.facts()['22222'];self.assertEqual(fact['note'],'喜欢节奏游戏');self.assertEqual(fact['note_source'],'model')
        self.assertEqual(fact['label'],'');self.assertEqual(fact['aliases'],[])
        self.apply(updates,**{key:True for key in FIELDS.values()})
        fact=self.store.facts()['22222'];self.assertEqual(fact['source'],'model');self.assertEqual(fact['label'],'AI')
        self.assertEqual(fact['aliases'],['昵称B','aaaa']);self.assertEqual(len(fact['model_history']),3)
    def test_manual_edits_and_manual_clearing_take_priority(self):
        permissions={key:True for key in FIELDS.values()}
        self.apply([self.update('account_type','AI'),self.update('account_note','模型记录'),self.update('extra_names','模型昵称')],**permissions)
        self.store.mark('22222','human','人工说明',['人工昵称'])
        self.apply([self.update('account_type','bot'),self.update('account_note','覆盖'),self.update('extra_names','覆盖昵称')],**permissions)
        fact=self.store.facts()['22222'];self.assertEqual((fact['label'],fact['note'],fact['aliases']),('human','人工说明',['人工昵称']))
        self.store.mark('22222','human','',[])
        self.apply([self.update('account_note','重新写入'),self.update('extra_names','重新写入')],**permissions)
        fact=self.store.facts()['22222'];self.assertEqual(fact['note'],'');self.assertEqual(fact['aliases'],[])
    def test_invalid_cross_account_evidence_and_unknown_accounts_are_rejected(self):
        for update in [self.update('account_type','AI',qq_id='33333'),self.update('account_type','AI',qq_id='99999'),
                self.update('account_type','AI',evidence_message_ids=['invented']),self.update('account_type','AI',confidence=.8),
                self.update('account_type','AI',confidence=True),self.update('account_type','not-a-label')]:
            self.assertEqual(self.apply([update],member_model_type_enabled=True),[])
        self.assertFalse(self.store.path.exists())
    def test_schema_exposes_only_enabled_fields_and_grounded_ids(self):
        self.assertIsNone(update_schema({},self.target,[]))
        spec=update_schema({'member_model_note_enabled':True},self.target,[])['items']['properties']
        self.assertEqual(spec['field']['enum'],['account_note'])
        self.assertEqual(spec['qq_id']['enum'],['22222'])
        self.assertEqual(spec['evidence_message_ids']['items']['enum'],['message1'])
    def test_context_can_update_silently_and_does_not_forward_update_values_to_reply(self):
        async def run():
            async def model(cfg,prompt,schema,images=None):
                if schema.name.startswith('emotion'):return {'mood':'neutral','reply_tone':'casual','confidence':.9,'note':''}
                spec=json.loads(schema.read_text(encoding='utf-8'))['properties']
                self.assertIn('member_updates',spec);self.assertIn('member_updates',prompt)
                return {'addressed_to_bot':False,'intent':'unrelated','needs_web_search':False,'search_query':'',
                    'confidence':.9,'reference_message_id':'message1','note':'',
                    'member_updates':[self.update('account_note','上下文识别到的账号说明')]}
            cfg={**bot.settings(),'multi_role_enabled':True,'member_model_note_enabled':True,'_reserve_extra_call':lambda:True}
            result=await analyze(cfg,[],self.target,[],model,self.root)
            self.assertTrue(self.target['_member_profile_analyzed'])
            self.assertNotIn('member_updates',result['context'])
            self.assertEqual(MemberLabels(self.root).facts()['22222']['note'],'上下文识别到的账号说明')
        asyncio.run(run())
    def test_single_reply_model_can_learn_without_forcing_a_chat_response(self):
        async def run():
            async def model(cfg,prompt,schema,images=None):
                spec=json.loads(schema.read_text(encoding='utf-8'))['properties']
                self.assertEqual(spec['member_updates']['items']['properties']['field']['enum'],['account_note'])
                self.assertIn('member_updates',prompt);self.assertLessEqual(len(prompt),6000)
                return {'reply_to':'message1','should_reply':False,'conversation':'open','action':'chat',
                    'text':'','text_parts':[],'reply_mode':'silent','media_kind':'none','media_id':'','media_ids':[],
                    'member_updates':[self.update('account_note','主回复模型识别的说明')]}
            with patch.object(bot,'RUNTIME',self.root),patch.object(bot,'generate_json',model):
                result=await bot.generate({**bot.settings(),'multi_role_enabled':False,'member_model_note_enabled':True},[],'自然触发',self.target)
            self.assertFalse(result)
            self.assertEqual(MemberLabels(self.root).facts()['22222']['note'],'主回复模型识别的说明')
        asyncio.run(run())


if __name__=='__main__':unittest.main()
