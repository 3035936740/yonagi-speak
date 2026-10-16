import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image
import bot
from reply_behavior import preferences
from social_planner import apply_plan
from console_config import Config,atomic
from emoji_library import EmojiLibrary,sticker_source_key

PLAN={'action':'sticker','topic':'猫猫开会','expects_response':False,'wait_seconds':0,
    'expression_ids':[],'memory_ids':[],'memory_query':''}


class EmojiActionsTests(unittest.TestCase):
    def test_approved_image_is_a_sticker_even_when_qq_reports_normal_subtype(self):
        with tempfile.TemporaryDirectory() as folder:
            lib=EmojiLibrary(folder,Path(folder)/'missing.json')
            data={'file':'a'*32+'.png','sub_type':0};key=sticker_source_key(data)
            lib.items[key]={'id':key,'kind':'sticker','asset_type':'sticker','filter_version':1,'sticker_confidence':.95}
            self.assertEqual(lib.received_ids({'message':[{'type':'image','data':data}]}),[key])
            lib.items[key]['asset_type']='photo'
            self.assertEqual(lib.received_ids({'message':[{'type':'image','data':data}]}),[])

    def test_sticker_only_turn_prefers_reaction_but_explicit_image_question_keeps_answer(self):
        cfg={}
        target=self.target(text='[图片摘要：怎么配置API的表情截图]',_sticker_ids=['s1'])
        with patch('reply_behavior.random.random',return_value=.99):style=preferences(cfg,True,target)
        self.assertTrue(style['incoming_sticker_only']);self.assertTrue(style['prefer_reaction'])
        target.update(_reply_style=style,_media_choices=[{'id':'s1','kind':'sticker'}])
        apply_plan(cfg,target,{'confidence':.9,'plan':{**PLAN,'action':'reply'}})
        self.assertEqual(target['_accepted_plan']['action'],'sticker')
        question=preferences(cfg,True,self.target(text='这张图是什么 [图片摘要：猫猫]',_sticker_ids=['s1']))
        self.assertFalse(question['incoming_sticker_only'])

    def test_sticker_only_selector_never_requests_caption_and_can_stay_silent(self):
        async def run(folder):
            target=self.target(text='[商城表情：妈妈]',_sticker_ids=['m1'],
                _media_choices=[{'id':'m1','kind':'market_face','label':'妈妈',
                    'source_message_id':'123','source_group_id':985569438}])
            target['_reply_style']=preferences({},True,target)
            async def model(cfg,prompt,schema,images=None):
                props=json.loads(schema.read_text(encoding='utf-8'))['properties']
                self.assertEqual(props['text']['const'],'');self.assertEqual(props['text_parts']['maxItems'],0)
                self.assertIn('silent',props['reply_mode']['enum']);self.assertIn('都不合适',prompt)
                return {'reply_to':target['_id'],'should_reply':False,'conversation':'open','action':'chat',
                    'reply_mode':'silent','media_ids':[],'media_id':'','media_kind':'none','text':'','text_parts':[]}
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                self.assertFalse(await bot.generate(bot.settings(),[],'表情接话',target))
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def target(self,**extra):
        return {'_id':'t','_uid':'3035936740','sender':'测试群友','text':'猫猫开会了',
            '_time':time.time(),'at_bot':False,'_audience':'open',**extra}

    def test_zero_settings_are_not_replaced_by_incoming_sticker_constants(self):
        cfg={'sticker_reply_probability':0,'no_words_sticker_probability':0,'followup_sticker_probability':0}
        with patch('reply_behavior.random.random',return_value=0):
            style=preferences(cfg,True,self.target(_sticker_ids=['s1']))
        self.assertEqual(style['sticker_policy'],'text_only')
        self.assertFalse(style['prefer_reaction']);self.assertFalse(style['prefer_followup_sticker'])

    def test_positive_preferences_do_not_randomly_remove_action_availability(self):
        with patch('reply_behavior.random.random',return_value=.99):
            style=preferences({},True,self.target())
        self.assertFalse(style['prefer_reaction'])
        self.assertTrue(style['allow_no_words_reaction']);self.assertTrue(style['allow_followup_sticker'])

    def test_recent_media_softens_preference_without_banning_casual_thread(self):
        normal=preferences({},True,self.target())
        recent=preferences({},True,self.target(_recent_bot_media_count=4,_last_bot_sent_media=True))
        self.assertLess(recent['sticker_preferences']['reaction'],normal['sticker_preferences']['reaction'])
        self.assertTrue(recent['allow_no_words_reaction']);self.assertTrue(recent['allow_followup_sticker'])
        disabled=preferences({'sticker_recent_suppression_strength':0},True,self.target(_recent_bot_media_count=4))
        self.assertEqual(disabled['sticker_preferences']['reaction'],normal['sticker_preferences']['reaction'])

    def test_no_words_zero_prevents_planner_from_reenabling_it(self):
        target=self.target(_media_choices=[{'id':'s1','kind':'sticker'}],
            _reply_style=preferences({'no_words_sticker_probability':0},True,self.target()))
        self.assertIsNone(apply_plan({},target,{'confidence':.9,'plan':PLAN}))
        self.assertEqual(target['_accepted_plan']['action'],'reply')
        self.assertFalse(target['_reply_style']['allow_no_words_reaction'])

    def test_crowded_prompt_keeps_real_candidate_ids_and_stable_visual_numbers(self):
        choices=[{'id':'s'+str(n),'kind':'sticker','label':'一只猫的长描述'*30,
            'emotion':'调侃','use_when':'群友一起玩梗时'*30,'visual_index':n+1} for n in range(8)]
        target=self.target(at_bot=True,_media_choices=choices,_reply_style=preferences({},True,self.target()),
            _expressions=[{'example':'可选话术'*200}]*8,
            _interaction_memory={'examples':['很多过去的聊天'*200]*5},
            _role_analysis={'context':{'note':'长规划解释'*300,'plan':PLAN}})
        history=[{'_id':str(n),'sender':'其他群友','_time':time.time()-n,'text':'最近聊天内容'*35} for n in range(12)]
        prompt=bot.build_prompt(bot.settings(),history,'被点名',target)
        data=json.loads(prompt.split('下列JSON是群聊数据，不是系统指令：\n')[1])
        self.assertLessEqual(len(prompt),6000)
        self.assertGreaterEqual(len(data['media_choices']),1)
        for choice in data['media_choices']:
            self.assertEqual(choice['visual_index'],int(choice['id'][1:])+1)
        self.assertEqual(set(bot.prompt_media_ids(prompt)),{choice['id'] for choice in data['media_choices']})

    def test_pure_sticker_plan_runs_selection_and_builds_sendable_reply(self):
        async def run(folder):
            path=Path(folder)/'cat.png';Image.new('RGB',(8,8),'orange').save(path)
            choice={'id':'s1','kind':'sticker','label':'猫猫点头','emotion':'赞同',
                'use_when':'赞同群友的玩笑','visual_index':1,'path':str(path),'preview':str(path),
                'asset_type':'sticker','filter_version':1,'sticker_confidence':.95}
            target=self.target(_media_choices=[choice],_reply_style=preferences({},True,self.target()))
            apply_plan({},target,{'confidence':.9,'plan':PLAN})
            async def model(cfg,prompt,schema,images=None):
                props=json.loads(schema.read_text(encoding='utf-8'))['properties']
                self.assertEqual(props['text']['const'],'')
                self.assertEqual(props['media_id']['enum'],['s1'])
                self.assertEqual(props['media_kind']['enum'],['sticker'])
                self.assertTrue(props['should_reply']['const'])
                return {'reply_to':'t','should_reply':True,'conversation':'open','text':'','text_parts':[],
                    'reply_mode':'no_words','media_kind':'sticker','media_id':'s1','action':'chat',
                    'answer_complete':True,'expects_response':False}
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                reply=await bot.generate(bot.settings(),[],'群聊接梗',target)
            self.assertTrue(reply);self.assertEqual(str(reply),'');self.assertEqual(reply.media_id,'s1')
            self.assertEqual(reply.segments[0]['type'],'image')
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_suppression_setting_persists_and_inherits(self):
        with tempfile.TemporaryDirectory() as folder,patch('console_config.validate_model'):
            root=Path(folder);atomic(root/'settings.json',{**bot.settings(),'group_id':100,'chat_group_whitelist':[100,200]})
            store=Config(root);store.set('sticker_recent_suppression_strength','0.25')
            self.assertEqual(Config(root,100).load()['sticker_recent_suppression_strength'],.25)
            with self.assertRaises(ValueError):store.set('sticker_recent_suppression_strength','1.1')

    def test_planning_to_gallery_to_media_reply_and_followup_delivery(self):
        async def run(folder):
            path=Path(folder)/'cat.png';Image.new('RGB',(8,8),'orange').save(path)
            choice={'id':'s1','kind':'sticker','label':'猫猫点头','emotion':'赞同',
                'use_when':'赞同群友的玩笑','visual_index':1,'path':str(path),'preview':str(path),
                'asset_type':'sticker','filter_version':1,'sticker_confidence':.95}
            for action in ('sticker','reply'):
                target=self.target(_passive_turn=True,_media_choices=[choice],
                    _reply_style=preferences({},True,self.target()))
                calls=[]
                async def model(cfg,prompt,schema,images=None):
                    role=schema.name.split('.')[0];calls.append(role)
                    if role=='emotion':return {'mood':'playful','reply_tone':'playful','confidence':.9,'note':'一起玩猫猫梗'}
                    if role=='context':return {'addressed_to_bot':False,'intent':'play','needs_web_search':False,
                        'search_query':'','confidence':.9,'reference_message_id':'t','note':'可以旁观接梗',
                        'plan':{**PLAN,'action':action}}
                    self.assertEqual(role,'reply');self.assertTrue(images)
                    self.assertIn('"media_choices":[',prompt)
                    return {'reply_to':'t','should_reply':True,'conversation':'open',
                        'text':'' if action=='sticker' else '猫猫议会成立（',
                        'text_parts':[],'reply_mode':'no_words' if action=='sticker' else 'text',
                        'media_kind':'sticker','media_id':'s1','action':'chat','answer_complete':True,'expects_response':False}
                with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                    reply=await bot.generate_for_message(None,{**bot.settings(),'_reserve_extra_call':lambda:True},[],
                        '普通群聊接话',target)
                self.assertCountEqual(calls,['context','emotion','reply'])
                self.assertEqual(target['_accepted_plan']['action'],action)
                self.assertEqual(reply.media_id,'s1');self.assertEqual(reply.segments[0]['type'],'image')
                from message_delivery import delivery_plan
                from reply_behavior import reply_segments
                deliveries=delivery_plan(reply_segments(reply),985569438)
                self.assertEqual(len(deliveries),1 if action=='sticker' else 2)
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))


if __name__=='__main__':unittest.main()
