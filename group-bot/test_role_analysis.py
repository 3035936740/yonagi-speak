import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import bot
from dialogue import audience
from role_analysis import analyze, checked, evidence

EMOTION={'mood':'confused','reply_tone':'simple','confidence':.9,'note':'需要换一种说法'}
CONTEXT={'addressed_to_bot':True,'intent':'feedback','needs_web_search':False,'search_query':'',
    'confidence':.9,'reference_message_id':'previous','note':'在回应刚才的回答'}
VISION={'description':'人物在挥手','visible_text':'hello','confidence':.8,'uncertainty':''}


class RoleAnalysisTests(unittest.TestCase):
    def test_vision_precedes_parallel_context_emotion_and_only_vision_gets_image(self):
        async def run():
            started=[]; reservations=[]
            barrier=asyncio.Event()
            async def generate(cfg,prompt,schema,images=None):
                role=schema.name.split('.')[0]
                started.append((role,images,cfg['model_timeout_seconds']))
                if role!='vision':
                    self.assertEqual(started[0][0],'vision')
                    self.assertIn('人物在挥手',prompt)
                    self.assertIn('"recognition":"read"',prompt)
                    if len(started)==3: barrier.set()
                    await asyncio.wait_for(barrier.wait(),1)
                return {'emotion':EMOTION,'context':CONTEXT,'vision':VISION}[role]
            with tempfile.TemporaryDirectory() as root:
                cfg={**bot.settings(),'multi_role_enabled':True,
                    '_reserve_extra_call':lambda:reservations.append(1) or True}
                target={'_id':'target','_uid':'a','_time':time.time(),'text':'看不懂','at_bot':False}
                previous={'_id':'previous','_time':target['_time']-10,'_uid':'bot','is_bot':True,'text':'专业回答'}
                original=Path(root)/'original.jpg'
                result=await analyze(cfg,[previous],target,[original],generate,root)
                self.assertEqual(set(result),{'emotion','context','vision'})
                self.assertEqual(len(reservations),3)
                self.assertEqual(next(images for role,images,_ in started if role=='vision'),[original])
                self.assertTrue(all(images is None for role,images,_ in started if role!='vision'))
                timeouts={role:timeout for role,_,timeout in started}
                self.assertEqual(timeouts['context'],16)
                self.assertEqual(timeouts['emotion'],16)
                self.assertEqual(timeouts['vision'],16)
                self.assertFalse(list(Path(root).glob('roles-*')))
        asyncio.run(run())

    def test_quoted_bot_reply_keeps_the_question_that_produced_it(self):
        now=time.time()
        question={'_id':'question','_uid':'a','_time':now-1500,'sender':'coffee',
            'text':'phigros最近更新的谱面你玩了吗','at_bot':True}
        clarification={'_id':'clarify','_uid':'a','_time':now-1490,'sender':'coffee','text':'你看看'}
        answer={'_id':'answer','_uid':'bot','_time':now-1480,'sender':'yonagi','is_bot':True,
            'text':'没看到更新，你发链接'}
        target={'_id':'target','_uid':'a','_time':now,'sender':'coffee','text':'建议你再看看',
            'at_bot':True,'_reply_id':'answer','_quoted_message':answer}
        fillers=[{'_id':f'f{index}','_uid':'x','_time':now-100+index,
            'sender':'别人','text':f'别的话{index}'} for index in range(30)]
        context=[question,clarification,answer,*fillers]
        data=evidence(context,target)
        self.assertIn('phigros最近更新',json.dumps(data,ensure_ascii=False))
        self.assertEqual(data['quoted_message']['id'],'answer')
        prompt=bot.build_prompt(bot.settings(),context,'被@',target)
        self.assertIn('phigros最近更新',prompt)

    def test_budget_and_failed_role_fall_back_without_losing_other_result(self):
        async def run():
            calls=[]; tokens=iter([True,True,False])
            async def generate(cfg,prompt,schema,images=None):
                role=schema.name.split('.')[0];calls.append(role)
                if role=='emotion': raise TimeoutError()
                return {'context':CONTEXT,'vision':VISION}[role]
            with tempfile.TemporaryDirectory() as root:
                cfg={**bot.settings(),'multi_role_enabled':True,'_reserve_extra_call':lambda:next(tokens)}
                target={'_id':'target','_time':time.time(),'text':'看不懂'}
                previous={'_id':'previous','_time':target['_time']-1,'is_bot':True}
                result=await analyze(cfg,[previous],target,[Path(root)/'image'],generate,root)
                self.assertEqual(calls,['vision','context'])
                self.assertEqual(result,{'context':CONTEXT,'vision':VISION})
        asyncio.run(run())

    def test_invalid_context_reference_is_rejected(self):
        self.assertIsNone(checked('context',{**CONTEXT,'reference_message_id':'another-group'},{'previous'}))
        self.assertIsNone(checked('emotion',{**EMOTION,'confidence':3},{'previous'}))

    def test_optional_timeout_honors_config_and_records_safe_detail(self):
        async def run(folder):
            budgets={}
            async def model(cfg,prompt,schema,images=None):
                role=schema.name.split('.')[0];budgets[role]=cfg['model_timeout_seconds']
                if role=='emotion':raise RuntimeError('Codex generation timed out; token=private-secret')
                return CONTEXT
            cfg={**bot.settings(),'multi_role_enabled':True,'role_timeout_seconds':24,
                'model_timeout_seconds':20,'_reserve_extra_call':lambda:True}
            with self.assertLogs('qq-group-bot',level='WARNING') as captured:
                result=await analyze(cfg,[],{'_id':'previous','text':'你好'},[],model,folder)
            self.assertEqual(budgets,{'context':20,'emotion':20})
            self.assertIn('context',result);self.assertNotIn('emotion',result)
            detail=' '.join(captured.output)
            self.assertIn('reason=timeout',detail);self.assertIn('timeout_seconds=20',detail)
            self.assertNotIn('private-secret',detail)
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_reply_target_is_bound_without_forcing_ambiguous_messages_to_reply(self):
        async def run():
            schemas=[];paths=[]
            async def generate(cfg,prompt,schema,images=None):
                schemas.append(json.loads(schema.read_text(encoding='utf-8')))
                paths.append(schema)
                return {'reply_to':'current','conversation':'others','should_reply':False,
                    'text':'','media_kind':'none','media_id':''}
            with patch.object(bot,'generate_json',generate):
                result=await bot.generate(bot.settings(),[],'判断上下文',
                    {'_id':'current','text':'不是在问你','at_bot':False,'_audience':'possibly_direct'})
            self.assertFalse(result)
            self.assertEqual(schemas[0]['properties']['reply_to']['const'],'current')
            self.assertNotIn('const',schemas[0]['properties']['should_reply'])
            self.assertFalse(paths[0].exists())
        asyncio.run(run())

    def test_role_outputs_reach_reply_and_gallery_does_not_reach_vision_role(self):
        async def run():
            calls=[]
            async def download(client,url,path,cfg): path.write_bytes(b'test-image')
            def sheet(choices,path): path.write_bytes(b'test-gallery');return True
            async def generate(cfg,prompt,schema,images=None):
                role=schema.name.split('.')[0];calls.append((role,prompt,list(images) if images else None))
                if role in {'emotion','context','vision'}:
                    return {'emotion':EMOTION,'context':CONTEXT,'vision':VISION}[role]
                self.assertIn('"role_analysis"',prompt)
                self.assertIn('confused',prompt)
                self.assertIn('人物在挥手',prompt)
                return {'reply_to':'target','conversation':'direct','should_reply':True,
                    'text':'简单说就是绕后打','media_kind':'none','media_id':''}
            with tempfile.TemporaryDirectory() as root,patch.object(bot,'RUNTIME',Path(root)), \
                    patch.object(bot,'download_image',download),patch.object(bot.EmojiLibrary,'contact_sheet',sheet), \
                    patch.object(bot,'generate_json',generate):
                cfg={**bot.settings(),'multi_role_enabled':True,'_reserve_extra_call':lambda:True}
                target={'_id':'target','_uid':'a','_time':time.time(),'text':'看不懂',
                    'at_bot':False,'_audience':'possibly_direct','_images':['https://gchat.qpic.cn/example']}
                previous={'_id':'previous','_time':target['_time']-1,'is_bot':True,'text':'专业回答'}
                reply=await bot.generate_for_message(None,cfg,[previous],'接续对话',target)
                self.assertEqual(reply.route,'direct')
                self.assertEqual(len(calls),4)
                self.assertEqual(len(next(images for role,_,images in calls if role=='vision')),1)
                self.assertEqual(len(calls[-1][2]),2) # main reply sees the original plus the separate gallery
                self.assertNotIn('_role_analysis',target)
        asyncio.run(run())

    def test_high_confidence_context_rejection_skips_reply_but_not_explicit_at(self):
        async def run(at):
            calls=[]
            async def generate(cfg,prompt,schema,images=None):
                role=schema.name.split('.')[0];calls.append(role)
                if role=='emotion': return EMOTION
                if role=='context': return {**CONTEXT,'addressed_to_bot':False,'intent':'unrelated'}
                return {'reply_to':'target','conversation':'direct','should_reply':True,
                    'text':'来了','media_kind':'none','media_id':''}
            cfg={**bot.settings(),'multi_role_enabled':True,'_reserve_extra_call':lambda:True}
            target={'_id':'target','_time':time.time(),'text':'问问别人','at_bot':at,'_audience':'possibly_direct'}
            previous={'_id':'previous','_time':target['_time']-1,'is_bot':True}
            with patch.object(bot,'generate_json',generate),patch.object(bot.EmojiLibrary,'contact_sheet',return_value=False):
                result=await bot.generate_for_message(None,cfg,[previous],'接续',target)
            self.assertEqual(bool(result),at)
            self.assertEqual(len(calls),3 if at else 2)
        asyncio.run(run(False));asyncio.run(run(True))

    def test_recent_unmarked_statement_is_a_context_candidate_but_ack_is_not(self):
        now=time.time()
        reply={'_id':'previous','_time':now-20,'is_bot':True,'_reply_to_uid':'a','text':'刚才的答案'}
        self.assertEqual(audience({'_id':'new','_uid':'a','_time':now,'text':'原来还能这样'},[reply],'bot'),'possibly_direct')
        self.assertEqual(audience({'_id':'new','_uid':'a','_time':now,'text':'谢谢'},[reply],'bot'),'open')

    def test_cancelled_parallel_jobs_release_temporary_role_schemas(self):
        async def run():
            started=asyncio.Event(); count=0; cancelled=[]
            async def generate(cfg,prompt,schema,images=None):
                nonlocal count
                count+=1
                if count==2: started.set()
                try: await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.append(schema.name)
                    raise
            with tempfile.TemporaryDirectory() as root:
                cfg={**bot.settings(),'multi_role_enabled':True,'_reserve_extra_call':lambda:True}
                task=asyncio.create_task(analyze(cfg,[],{'_id':'target','_time':time.time()},[],generate,root))
                await asyncio.wait_for(started.wait(),1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertEqual(len(cancelled),2)
                self.assertFalse(list(Path(root).glob('roles-*')))
        asyncio.run(run())


if __name__=='__main__':unittest.main()
