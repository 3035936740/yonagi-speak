import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import bot
from peer_reply import quality_issue
from social_tact import feedback_scene


class SocialTactTests(unittest.TestCase):
    def target(self,**changes):
        return dict({'_id':'t','_uid':'u','_time':time.time(),'sender':'群友','text':'情商非常低，但是挺智能的',
            'at_bot':True,'_self_id':'99999'},**changes)

    def test_mixed_feedback_is_not_just_praise(self):
        target=self.target()
        scene=feedback_scene({},target,[])
        self.assertTrue(scene['mixed_praise']);self.assertTrue(scene['criticism'])
        target['_social_feedback']=scene
        self.assertEqual(quality_issue('情商低归低，智能还是有的（',target),'defensive_self_praise')
        self.assertEqual(quality_issue('我至少挺聪明的',target),'defensive_self_praise')
        for reply in ('这句确实没接好','光听后半句去了，前半句也收到了','那我先补补接话这门课'):
            self.assertEqual(quality_issue(reply,target),'')

    def test_same_speaker_continuation_and_role_addressing(self):
        target=self.target(text='但是挺智能的',at_bot=False,_role_analysis={'context':{'addressed_to_bot':True}})
        previous={'_id':'p','_uid':'u','_time':target['_time']-5,'text':'情商非常低'}
        scene=feedback_scene({},target,[previous])
        self.assertTrue(scene['continuation']);self.assertTrue(scene['mixed_praise'])
        self.assertEqual(feedback_scene({},target,[{**previous,'_uid':'other'}]),{})
        self.assertEqual(feedback_scene({},target,[{**previous,'_time':target['_time']-180}]),{})

    def test_praise_negation_and_third_party_do_not_trigger_guard(self):
        for target in (self.target(text='你挺智能的'),self.target(text='你情商不低，挺聪明的'),
            self.target(at_bot=False,_at_ids=['12345']),
            self.target(at_bot=False,text='小明情商非常低，但是挺智能的'),
            self.target(text='你觉得小明情商低吗'),
            self.target(text='小明情商低，但是挺智能的',_role_analysis={'context':{'feedback_target':'other','addressed_to_bot':True}}),
            self.target(at_bot=False,_quoted_message={'text':'别人说的话','is_bot':False})):
            self.assertEqual(feedback_scene({},target,[]),{})

    def test_disabled_preserves_baseline(self):
        target=self.target()
        self.assertEqual(feedback_scene({'high_eq_enabled':False},target,[]),{})
        target.update(_high_eq_enabled=False,_social_feedback={'about_self':True,'criticism':True})
        self.assertEqual(quality_issue('智能还是有的',target),'')
        prompt=bot.build_prompt({**bot.settings(),'high_eq_enabled':False},[],'被@',target)
        self.assertNotIn('高情商接话：',prompt)

    def test_bad_candidate_gets_one_existing_budgeted_repair(self):
        async def run(folder):
            calls=[];reserved=[];target=self.target()
            async def model(cfg,prompt,schema,images=None):
                calls.append(prompt)
                return {'reply_to':'t','conversation':'direct','should_reply':True,'action':'chat',
                    'text':'情商低归低，智能还是有的（' if len(calls)==1 else '这回确实没接好，前半句也听进去了',
                    'text_parts':[],'media_kind':'none','media_id':'','answer_complete':True}
            def reserve():reserved.append(True);return True
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                reply=await bot.generate({**bot.settings(),'_reserve_extra_call':reserve},[],'被@',target)
            self.assertIn('前半句也听进去了',str(reply));self.assertEqual(len(calls),2)
            self.assertEqual(len(reserved),1);self.assertIn('defensive_self_praise',calls[-1])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_good_reply_does_not_add_a_model_call(self):
        async def run(folder):
            calls=[]
            async def model(cfg,prompt,schema,images=None):
                calls.append(prompt)
                return {'reply_to':'t','conversation':'direct','should_reply':True,'action':'chat',
                    'text':'这句确实没接好','text_parts':[],'media_kind':'none','media_id':'','answer_complete':True}
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                reply=await bot.generate(bot.settings(),[],'被@',self.target())
            self.assertEqual(str(reply),'这句确实没接好');self.assertEqual(len(calls),1)
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))


if __name__=='__main__':unittest.main()
