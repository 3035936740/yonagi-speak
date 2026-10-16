"""Offline execution of multiple text/sticker parts and grounded QQ actions."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch,AsyncMock

from PIL import Image
import bot
from console_config import Config,atomic
from emoji_library import ReplyText,selected_media
from message_delivery import delivery_plan,deliver_burst
from reply_behavior import delivery_preferences,preferences,reply_chunks,reply_segments

GROUP=985569438


class ReplyCompositionTests(unittest.TestCase):
    def choices(self,folder):
        result=[]
        for index in range(3):
            path=Path(folder)/f'{index}.png'
            Image.new('RGB',(8,8),(index,10,20)).save(path)
            result.append({'id':f's{index}','kind':'sticker','path':str(path),'preview':str(path),
                'content_hash':str(index),'time':time.time(),'label':f'猫猫{index}','emotion':'开心','use_when':'开心接话',
                'asset_type':'sticker','filter_version':1,'sticker_confidence':.95})
        return result

    def target(self):
        return {'_id':'101','_uid':'11','_self_id':'99','sender':'甲','text':'一起出去玩',
            '_time':time.time(),'at_bot':True,'_audience':'direct'}

    def test_four_texts_then_three_stickers_become_seven_real_messages(self):
        async def run(folder):
            choices=self.choices(folder);target=self.target()
            cfg={**bot.settings(),'at_reply_probability':1,'quote_reply_probability':1}
            target.update(_media_choices=choices,_reply_style=preferences(cfg,True,target))
            parts=['走哇','我先去占位置','你们带点吃的','等下门口见']
            async def model(cfg,prompt,schema,images=None):
                props=json.loads(schema.read_text(encoding='utf-8'))['properties']
                self.assertEqual(props['text_parts']['maxItems'],6)
                self.assertEqual(props['media_ids']['maxItems'],3)
                self.assertEqual(set(props['media_ids']['items']['enum']),{'s0','s1','s2'})
                return {'reply_to':'101','conversation':'direct','should_reply':True,'action':'chat',
                    'text':'','text_parts':parts,'media_ids':['s0','s1','s2'],'media_kind':'none','media_id':'',
                    'at_message_ids':[],'quote_reply':False,'reply_mode':'text','answer_complete':True}
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'generate_json',model):
                reply=await bot.generate(cfg,[],'被@',target)
            self.assertEqual(reply.chunks,parts)
            self.assertEqual(reply.media_ids,['s0','s1','s2'])
            plan=delivery_plan(bot.outgoing_segments(reply,target,[]),GROUP)
            self.assertEqual(len(plan),7)
            self.assertEqual([s['type'] for s in plan[0]['params']['message']],['reply','at','text','text'])
            self.assertEqual([p.get('_media_ids') for p in plan[4:]],[['s0'],['s1'],['s2']])
            sent=[]
            async def api(client,env,action,params):
                sent.append(params['message']);return {'message_id':len(sent)}
            checkpoints=[]
            await deliver_burst(api,None,None,plan,lambda:True,
                lambda result,part:checkpoints.append(json.loads(json.dumps(plan))),delay=(0,0))
            self.assertEqual(len(sent),7);self.assertFalse(plan)
            self.assertFalse(any(s['type'] in {'at','reply'} for message in sent[1:] for s in message))
            self.assertFalse(any(any(k.startswith('_') for k in s) for message in sent for s in message))
            self.assertEqual([s[0]['type'] for s in sent[4:]],['image']*3)
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_multi_media_deduplicates_and_rejects_unseen_or_unapproved_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            choices=self.choices(folder)
            alias={**choices[0],'id':'alias'}
            rejected={**choices[2],'id':'rejected','asset_type':'photo'}
            result={'media_ids':['s0','s0','alias','invented','rejected','s1','s2']}
            segments=selected_media(result,choices+[alias,rejected],limit=2,track=True)
            self.assertEqual([s['_media_id'] for s in segments if s.get('_media_id')],['s0','s1'])
            target={'_media_choices':choices,'_prompt_media_ids':['s1']}
            media=bot.reply_media({'media_ids':['s0','s1','s2']},target,{'max_stickers_per_reply':3})
            self.assertEqual([s['_media_id'] for s in media if s.get('_media_id')],['s1'])

    def test_probability_draw_is_the_execution_gate_and_never_invents_a_person(self):
        for rate,expected in ((0,False),(1,True)):
            target=self.target();target['_delivery_options']=bot.available_delivery_options(target,[])
            with patch('reply_behavior.random.random',return_value=.3) as draw:
                target['_delivery_preferences']=delivery_preferences({'at_reply_probability':rate,'quote_reply_probability':rate})
                quote,people=bot.delivery_metadata({'quote_reply':False,'at_message_ids':[]},target)
                self.assertEqual(draw.call_count,2)
            self.assertEqual(quote,expected);self.assertEqual(people,['11'] if expected else [])
        target['_delivery_options']=[]
        self.assertEqual(bot.delivery_metadata({},target),(False,[]))
        target['_delivery_options']=[{'message_id':'101','user_id':'99'}]
        self.assertEqual(bot.delivery_metadata({'at_message_ids':['made-up']},target)[1],[])

    def test_native_only_sequence_never_creates_a_stray_at_message(self):
        target=self.target();target['_delivery_preferences']={'allow_at':True,'allow_quote':True}
        media=[{'type':'_native_market_face','data':{'message_id':'1'}},
            {'type':'_message_break','data':{}},
            {'type':'_native_market_face','data':{'message_id':'2'}}]
        reply=ReplyText('',media,quote_reply=True,at_uids=['11'])
        plan=delivery_plan(bot.outgoing_segments(reply,target,[]),GROUP)
        self.assertEqual([p['action'] for p in plan],['forward_group_single_msg']*2)

    def test_semantic_parts_and_total_text_survive_configured_cap(self):
        parts=['第一句','第二句','第三句','第四句','第五句']
        text=bot.plain_reply({'should_reply':True,'text':'','text_parts':parts},120)
        self.assertEqual(text,'\n'.join(parts))
        self.assertEqual(reply_chunks({'text_parts':parts},text,120,True,True),parts)
        smaller=reply_chunks({'text_parts':parts},text,120,True,True,maximum=3)
        self.assertEqual(smaller,['第一句','第二句','第三句，第四句，第五句'])
        self.assertEqual(reply_chunks({'text_parts':parts},text,120,False),['，'.join(parts)])
        with patch('reply_behavior.random.random',return_value=.99):
            style=preferences({'burst_reply_probability':.1},False)
            self.assertTrue(style['allow_burst']);self.assertFalse(style['prefer_burst'])
        self.assertFalse(preferences({'burst_reply_probability':0},False)['allow_burst'])

    def test_restart_resumes_only_unsent_stickers_and_stop_retains_remainder(self):
        async def run():
            media=[{'type':'image','data':{'file':'base64://test'},'_media_id':f's{i}'} if i%2==0
                else {'type':'_message_break','data':{}} for i in range(5)]
            plan=delivery_plan(reply_segments(ReplyText('一\n二\n三\n四',media,chunks=['一','二','三','四'])),GROUP)
            calls=[];active=[True]
            async def api(client,env,action,params):
                calls.append(params);return {'message_id':len(calls)}
            def record(result,part):
                if len(calls)==5:active[0]=False
            await deliver_burst(api,None,None,plan,lambda:active[0],record,delay=(0,0))
            self.assertEqual(len(calls),5);self.assertEqual(len(plan),2)
            resumed=json.loads(json.dumps(plan));active[0]=True
            await deliver_burst(api,None,None,resumed,lambda:active[0],record,delay=(0,0))
            self.assertEqual(len(calls),7);self.assertFalse(resumed)
            self.assertEqual([p['_media_ids'] for p in plan],[['s2'],['s4']])
        asyncio.run(run())

    def test_composition_limits_persist_and_inherit_without_resetting_other_settings(self):
        with tempfile.TemporaryDirectory() as folder,patch('console_config.validate_model'):
            root=Path(folder);atomic(root/'settings.json',{**bot.settings(),'group_id':100,'chat_group_whitelist':[100,200]})
            store=Config(root);store.set('max_reply_parts','8');store.set('max_stickers_per_reply','4')
            group=Config(root,200)
            self.assertEqual(group.load()['max_reply_parts'],8)
            group.set('max_stickers_per_reply','2')
            self.assertEqual(Config(root,200).load()['max_stickers_per_reply'],2)
            group.reset('max_stickers_per_reply')
            self.assertEqual(Config(root,200).load()['max_stickers_per_reply'],4)
            for key,value in [('max_reply_parts','11'),('max_stickers_per_reply','6')]:
                with self.assertRaises(ValueError):store.set(key,value)

    def test_real_poll_loop_records_each_part_and_each_sticker_usage(self):
        async def run(folder):
            root=Path(folder);runtime=root/'runtime';runtime.mkdir()
            cfg={**bot.settings(),'enabled':True,'group_id':GROUP,'chat_group_whitelist':[GROUP],
                'shared_sticker_library':False,'memory_enabled':False,'style_learning_enabled':False,
                'background_image_reading_enabled':False,'proactive_topics_enabled':False,
                'passive_probability':0,'mention_cooldown_seconds':0,'poll_seconds':0,
                'expected_self_id':99,'mcp_env_file':str(root/'missing.env')}
            atomic(root/'settings.json',cfg)
            choices=self.choices(root)
            library=bot.EmojiLibrary(runtime/'emoji-library',root/'missing-faces.json')
            library.items={item['id']:item for item in choices};library.save()
            calls=[];polls=[0];sleep=asyncio.sleep
            async def tick(seconds):await sleep(0);await sleep(0)
            async def api(client,env,action,params=None):
                if action=='get_login_info':return {'user_id':99}
                if action in {'get_group_info','get_group_member_list'}:return {}
                if action=='get_group_msg_history':
                    if params.get('count')==100:return {'messages':[]}
                    polls[0]+=1
                    if polls[0]>12:
                        (runtime/'STOP').touch();raise AssertionError('delivery stalled')
                    return {'messages':[{'message_id':101,'time':time.time(),'user_id':11,
                        'sender':{'nickname':'甲'},'message':[{'type':'at','data':{'qq':'99'}},
                            {'type':'text','data':{'text':'一起出去玩'}}]}]}
                if action=='send_group_msg':
                    calls.append(params['message'])
                    if len(calls)==7:(runtime/'STOP').touch()
                    return {'message_id':1000+len(calls)}
                raise AssertionError(action)
            async def generate(client,cfg,history,trigger,target,**kwargs):
                target['_media_choices']=choices
                media=selected_media({'media_ids':['s0','s1','s2']},choices,track=True)
                return ReplyText('走哇\n我去占位\n记得带吃的\n门口见',media,
                    chunks=['走哇','我去占位','记得带吃的','门口见'],quote_reply=True,at_uids=['11'])
            with patch.object(bot,'ROOT',root),patch.object(bot,'RUNTIME',runtime), \
                    patch.object(bot,'settings',return_value=cfg),patch.object(bot,'EmojiLibrary',return_value=library), \
                    patch.object(bot,'api',api),patch.object(bot,'generate_for_message',generate), \
                    patch.object(bot,'maintain_images',AsyncMock()),patch.object(asyncio,'sleep',tick):
                await bot.run()
            self.assertEqual(len(calls),7,(runtime/'debug-events.jsonl').read_text(encoding='utf-8')[-5000:])
            state=json.loads((runtime/'state.json').read_text(encoding='utf-8'))
            self.assertEqual(len(state['reply_times']),7)
            self.assertEqual([library.items[f's{i}']['uses'] for i in range(3)],[1,1,1])
            records=[json.loads(line) for line in (runtime/'recent-chat.jsonl').read_text(encoding='utf-8').splitlines()]
            sent=[row for row in records if row.get('is_bot')]
            self.assertEqual(len(sent),7)
            self.assertEqual(sent[0]['_at_ids'],['11']);self.assertEqual(sent[0]['_reply_id'],'101')
            self.assertEqual([row['_sticker_ids'] for row in sent[4:]],[['s0'],['s1'],['s2']])
            self.assertTrue(all(row['text'].startswith('[表情包：') for row in sent[4:]))
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))


if __name__=='__main__':unittest.main()
