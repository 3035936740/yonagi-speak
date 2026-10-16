import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import bot
from failure_response import respond,ReplyFailure
from peer_reply import image_description_requested,visual_summary_leakage

FACTS={'description':'白发猫耳、黄色眼睛的角色，右上角写着妈妈','visible_text':'妈妈','confidence':.9}


class VisualSummaryBoundaryTests(unittest.TestCase):
    def target(self,**extra):
        return {'_id':'t','_uid':'123','sender':'测试群友','_time':time.time(),
            'text':'[图片摘要：白发猫耳、黄色眼睛的角色，右上角写着妈妈]',
            '_cached_vision':[FACTS],'_passive_turn':True,'_audience':'open','at_bot':False,**extra}

    def test_internal_caption_does_not_become_a_user_read_request(self):
        self.assertFalse(image_description_requested(self.target()))
        self.assertFalse(image_description_requested(self.target(text='妈妈')))
        for text in ('这是什么东西','图里写的啥','这张图是什么意思','帮我描述一下'):
            self.assertTrue(image_description_requested(self.target(text=text)))

    def test_reported_ocr_and_appearance_are_blocked_except_requested_description(self):
        for text in ('白发猫耳，金眼睛，右上角写着“妈妈”',
            '右上角写着“妈妈”，是个白发猫耳金眼的角色'):
            self.assertTrue(visual_summary_leakage(text,self.target()))
            self.assertFalse(visual_summary_leakage(text,self.target(text='这是什么图')))
        self.assertFalse(visual_summary_leakage('妈咪！',self.target()))
        self.assertFalse(visual_summary_leakage('这只猫好可爱',self.target()))

    def test_passive_failure_is_silent_without_another_model_call(self):
        async def run():
            async def forbidden(*args):raise AssertionError('Passive failure must not generate a caption')
            cfg={**bot.settings(),'_reserve_extra_call':lambda:(_ for _ in ()).throw(AssertionError('no reserve'))}
            reply=await respond(cfg,[],self.target(),'text',ValueError('budget'),forbidden,bot.ROOT)
            self.assertFalse(reply);self.assertEqual(reply.route,'silent')
        asyncio.run(run())

    def test_direct_banter_failure_cannot_expose_visual_facts(self):
        async def run():
            async def model(cfg,prompt,schema,images=None):
                self.assertIn('"recognized_images": []',prompt)
                return {'text':'白发猫耳，金眼睛，右上角写着妈妈'}
            target=self.target(text='妈妈',at_bot=True,_audience='direct',_passive_turn=False)
            reply=await respond({**bot.settings(),'_reserve_extra_call':lambda:True},[],target,
                'text',ReplyFailure('generic'),model,bot.ROOT)
            self.assertNotIn('猫耳',str(reply));self.assertNotIn('右上角',str(reply))
        asyncio.run(run())

    def test_sticker_selection_has_a_small_independent_budget(self):
        choices=[{'id':str(n),'kind':'sticker','visual_index':n+1,'label':'猫耳反应图'*100,
            'emotion':'赞同','use_when':'接认亲梗'*100} for n in range(11)]
        target=self.target(_accepted_plan={'action':'sticker'},_media_choices=choices,
            _reply_style={'allow_no_words_reaction':True},_conversation_state={'large':'无关状态'*3000},
            _identity_resolution=[{'large':'无关身份'*3000}],_expressions=['无关旧话术'*1000]*8)
        history=[{'sender':'群友','text':'以前的话题'*1000} for _ in range(10)]
        prompt=bot.build_sticker_prompt(bot.settings(),history,target)
        self.assertLessEqual(len(prompt),6000)
        self.assertGreater(len(bot.prompt_media_ids(prompt)),0)
        self.assertNotIn('无关身份',prompt);self.assertIn('内部设定是女生',prompt)
        self.assertIn('text为空',prompt)

    def test_normal_reply_leak_uses_one_rewrite_instead_of_sending_caption(self):
        async def run(folder):
            calls=[]
            async def model(cfg,prompt,schema,images=None):
                calls.append(prompt)
                return {'reply_to':'t','should_reply':True,'conversation':'open','action':'chat',
                    'text':'白发猫耳，金眼睛，右上角写着妈妈' if len(calls)==1 else '妈咪又增加了',
                    'text_parts':[],'reply_mode':'text','media_kind':'none','media_id':'','answer_complete':True}
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                reply=await bot.generate({**bot.settings(),'_reserve_extra_call':lambda:True},[],
                    '群聊接梗',self.target(text='又来了哈哈 [图片摘要：白发猫耳，金眼睛，右上角写着妈妈]'))
            self.assertEqual(len(calls),2);self.assertEqual(str(reply),'妈咪又增加了')
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))


if __name__=='__main__':unittest.main()
