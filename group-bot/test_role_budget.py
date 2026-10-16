import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
import bot
from console_config import DEFAULTS
from role_analysis import analyze
from role_budget import fit_role_prompt,RoleBudgetError


class RoleBudgetTests(unittest.TestCase):
    def test_defaults_are_12800_for_input_and_both_record_lanes(self):
        self.assertEqual(DEFAULTS['max_prompt_chars'],12800)
        self.assertEqual(DEFAULTS['console_max_text_chars'],12800)
        self.assertEqual(bot.settings()['max_prompt_chars'],12800)

    def test_oversized_context_and_emotion_are_fitted_before_transport(self):
        async def run(limit):
            target={'_id':'target','_uid':'22222','sender':'甲','text':'CURRENT_QUESTION','_time':time.time(),
                '_reply_id':'quote','_quoted_message':{'_id':'quote','text':'QUOTE_REQUIRED','_uid':'33333','sender':'乙'},
                '_peer_scene':{'optional':'x'*50000},'_conversation_state':{'tasks':['x'*2000]*5},
                '_person_profile':{'optional':'x'*5000},
                '_expression_candidates':[{'id':'e','example':'x'*10000}],
                '_memory_candidates':[{'id':'m','content':'x'*10000}],
                '_passive_batch':[{'id':str(n),'text':'x'*500} for n in range(8)],
                '_identity_snapshot':{'sender':{'qq_id':'22222','account_label':'owner','label_source':'manual'}},
                '_transient_summary':{'optional':'x'*5000}}
            before=deepcopy(target);calls=[]
            async def generate(cfg,prompt,schema,images=None):
                role=schema.name.split('.')[0];calls.append(role)
                self.assertLessEqual(len(prompt),cfg['max_prompt_chars'])
                self.assertIn('CURRENT_QUESTION',prompt);self.assertIn('QUOTE_REQUIRED',prompt)
                self.assertIn('你的主人说什么就得干什么',prompt)
                json.JSONDecoder().raw_decode(prompt[prompt.index('{"target":'):])
                if role=='emotion':return {'mood':'neutral','reply_tone':'casual','confidence':.9,'note':''}
                return {'addressed_to_bot':True,'intent':'question','needs_web_search':False,'search_query':'',
                    'reference_message_id':'target','confidence':.9,'note':''}
            with tempfile.TemporaryDirectory() as folder:
                cfg={**bot.settings(),'max_prompt_chars':limit,'multi_role_enabled':True,'_reserve_extra_call':lambda:True}
                if limit==4800:cfg.update(_developer_instructions='干预'*600,_intervention_chars=1200)
                result=await analyze(cfg,[],target,[],generate,folder)
                self.assertEqual(set(result),{'context','emotion'});self.assertCountEqual(calls,['context','emotion'])
                self.assertEqual(target['_peer_scene'],before['_peer_scene'])
        for limit in (6000,4800,12800):
            with self.subTest(limit=limit):asyncio.run(run(limit))

    def test_small_valid_budget_keeps_whole_json_and_impossible_budget_is_explicit(self):
        data={'target':{'id':'now','text':'当前问题'},'recent_chat':[{'id':'old','text':'x'*2000}],
            'peer_scene':{'optional':'x'*10000}}
        prompt=fit_role_prompt('long built in guidance '*100,data,500,'判断上下文；只输出schema JSON。\n')
        self.assertLessEqual(len(prompt),500);self.assertIn('当前问题',prompt)
        self.assertEqual(json.loads(prompt[prompt.index('{'):])['target']['id'],'now')
        with self.assertRaises(RoleBudgetError):fit_role_prompt('required',{'target':{'text':'不能删除的当前问题'*100}},50,'required')


if __name__=='__main__':unittest.main()
