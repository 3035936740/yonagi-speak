"""Offline NapCat responses: expired market sources, durable retries and failed jobs."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import bot
from emoji_library import EmojiLibrary, selected_media
from emoji_library import ReplyText
from message_delivery import (delivery_plan, deliver_one, deliver_burst,
    attach_market_fallbacks, DeliveryAborted)
from napcat_errors import NapCatAPIError

GROUP = 985569438
PACKET = {'emoji_package_id':245727, 'emoji_id':'298a5f8237a4a8c6653aa6677ffd7741',
    'key':'8274bb20908d4937', 'summary':'[妈妈来了]'}


def rejected(action, message='无法找到消息740465444'):
    return NapCatAPIError(action, {'status':'failed', 'retcode':200, 'message':message})


def old_plan():
    return [{'action':'forward_group_single_msg', 'params':{'group_id':GROUP, 'message_id':'740465444'}}]


class NapCatDeliveryTests(unittest.TestCase):
    def test_native_send_without_id_uses_only_real_echo_in_chat_and_journal(self):
        async def run(folder):
            root=Path(folder);runtime=root/'runtime';runtime.mkdir()
            cfg={**bot.settings(),'enabled':True,'group_id':GROUP,'chat_group_whitelist':[GROUP],
                'shared_sticker_library':False,'memory_enabled':False,'style_learning_enabled':False,
                'background_image_reading_enabled':False,'proactive_topics_enabled':False,
                'passive_probability':0,'mention_cooldown_seconds':0,'poll_seconds':0,
                'mcp_env_file':str(root/'missing.env')}
            from console_config import atomic
            import hashlib
            atomic(root/'settings.json',cfg)
            identity='m'+hashlib.sha256(PACKET['emoji_id'].encode()).hexdigest()[:16]
            choice={'id':identity,'kind':'market_face','source_message_id':'33','source_group_id':GROUP,
                'data':PACKET,'label':'[妈妈来了]','time':time.time()}
            library=EmojiLibrary(runtime/'emoji-library',root/'missing-faces.json');library.items={identity:choice};library.save()
            calls=[];polls=[0];sleep=asyncio.sleep
            human={'message_id':101,'time':time.time(),'user_id':3035936740,'sender':{'nickname':'群友'},
                'message':[{'type':'at','data':{'qq':'169901502'}},{'type':'text','data':{'text':'发个表情'}}]}
            async def tick(seconds):await sleep(0);await sleep(0)
            async def api(client,env,action,params=None):
                if action=='get_login_info':return {'user_id':169901502}
                if action in {'get_group_info','get_group_member_list'}:return {}
                if action=='get_group_msg_history':
                    if params.get('count')==100:return {'messages':[]}
                    polls[0]+=1
                    if calls:
                        (runtime/'STOP').touch()
                        return {'messages':[human,{'message_id':777,'time':time.time(),'user_id':169901502,
                            'sender':{'nickname':'yonagi'},'message':[{'type':'mface','data':PACKET}]}]}
                    if polls[0]>12:(runtime/'STOP').touch()
                    return {'messages':[human]}
                if action=='forward_group_single_msg':calls.append(action);return None
                raise AssertionError(action)
            async def generate(client,cfg,history,trigger,target,**kwargs):
                target['_media_choices']=[choice]
                return ReplyText('',selected_media({'media_ids':[identity]},[choice],track=True))
            with patch.object(bot,'ROOT',root),patch.object(bot,'RUNTIME',runtime),patch.object(bot,'settings',return_value=cfg), \
                    patch.object(bot,'api',api),patch.object(bot,'generate_for_message',generate), \
                    patch.object(bot,'maintain_images',AsyncMock()),patch.object(asyncio,'sleep',tick):
                await bot.run()
            self.assertEqual(calls,['forward_group_single_msg'])
            rows=[json.loads(line) for line in (runtime/'debug-events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([r['message_id'] for r in rows if r['kind']=='sent'],['777'])
            journal=[json.loads(line) for line in (runtime/'recent-chat.jsonl').read_text(encoding='utf-8').splitlines()]
            own=[r for r in journal if r.get('is_bot')]
            self.assertEqual(len(own),1);self.assertEqual(own[0]['_id'],'777')
            self.assertEqual(own[0]['_sticker_ids'],[identity]);self.assertEqual(own[0]['_image_count'],1)
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))

    def test_real_api_wrapper_preserves_business_detail_and_redacts_credentials(self):
        async def run():
            response = {'status':'failed', 'retcode':200,
                'message':'无法找到消息740465444; token=private-test-secret'}
            transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
            async with httpx.AsyncClient(transport=transport) as client:
                with self.assertRaises(NapCatAPIError) as caught:
                    await bot.api(client, {'NAPCAT_HOST':'http://offline.invalid'}, 'forward_group_single_msg')
            error = caught.exception
            self.assertEqual(error.retcode, 200)
            self.assertIn('无法找到消息740465444', str(error))
            self.assertNotIn('private-test-secret', str(error))
            self.assertTrue(error.missing_forward_source)
        asyncio.run(run())

    def test_expired_forward_falls_back_and_restart_never_replays_delivered_text(self):
        async def run():
            plan = delivery_plan([{'type':'text', 'data':{'text':'来了'}}], GROUP) + old_plan()
            attach_market_fallbacks(plan, [{'kind':'market_face', 'source_group_id':GROUP,
                'source_message_id':'740465444', 'data':PACKET}])
            calls, completed = [], []
            async def api(client, env, action, params):
                calls.append(action)
                if action == 'forward_group_single_msg':raise rejected(action)
                if params['message'][0]['type'] == 'mface':
                    self.assertEqual(params['message'], [{'type':'mface', 'data':PACKET}])
                    if len(calls) == 3:raise rejected(action, '系统繁忙，请稍后重试')
                return {'message_id':len(calls)}
            with self.assertRaises(NapCatAPIError):
                await deliver_burst(api, None, None, plan, lambda:True,
                    lambda result, part:completed.append(part), delay=(0,0))
            self.assertEqual(len(plan), 1)
            plan = json.loads(json.dumps(plan))  # Durable pending file / new process.
            self.assertEqual(plan[0]['_attempts'], 2)
            await deliver_burst(api, None, None, plan, lambda:True,
                lambda result, part:completed.append(part), delay=(0,0))
            self.assertFalse(plan)
            self.assertEqual(calls, ['send_group_msg','forward_group_single_msg','send_group_msg','send_group_msg'])
            self.assertEqual(len(completed), 2)
            self.assertEqual(completed[-1]['_unavailable_source_id'], '740465444')
        asyncio.run(run())

    def test_uncertain_forward_timeout_never_falls_back_or_resends(self):
        async def run():
            plan = old_plan(); plan[0]['_market_face'] = PACKET
            api = AsyncMock(side_effect=httpx.ReadTimeout('request may already be accepted'))
            with self.assertRaises(DeliveryAborted):await deliver_one(api, None, None, plan)
            self.assertEqual(api.await_count, 1)
            self.assertEqual(plan[0]['action'], 'forward_group_single_msg')
        asyncio.run(run())

    def test_permanent_or_unknown_business_error_does_not_try_native_fallback(self):
        async def run(message):
            plan = old_plan(); plan[0]['_market_face'] = PACKET
            api = AsyncMock(side_effect=rejected('forward_group_single_msg', message))
            with self.assertRaises(DeliveryAborted):await deliver_one(api, None, None, plan)
            self.assertEqual(api.await_count, 1)
        for message in ('权限不足', '转发消息失败，未知错误', '操作超时'):
            asyncio.run(run(message))

    def test_retry_limit_survives_process_restart(self):
        async def run():
            api = AsyncMock(side_effect=httpx.ConnectError('temporarily offline'))
            plan = old_plan()
            for _ in range(2):
                with self.assertRaises(httpx.ConnectError):await deliver_one(api, None, None, plan)
                plan = json.loads(json.dumps(plan))
            with self.assertRaises(DeliveryAborted):await deliver_one(api, None, None, plan)
            with self.assertRaises(DeliveryAborted):await deliver_one(api, None, None, json.loads(json.dumps(plan)))
            self.assertEqual(api.await_count, 3)
        asyncio.run(run())

    def test_incomplete_packet_or_unauthorized_source_cannot_become_fallback(self):
        for group, packet in ((123, PACKET), (GROUP, {**PACKET, 'key':''})):
            plan = old_plan()
            attach_market_fallbacks(plan, [{'kind':'market_face', 'source_group_id':group,
                'source_message_id':'740465444', 'data':packet}])
            self.assertNotIn('_market_face', plan[0])

    def test_failed_source_is_remembered_until_a_new_source_is_observed(self):
        with tempfile.TemporaryDirectory() as folder:
            library = EmojiLibrary(folder, Path(folder)/'missing', shared=True)
            def observe(source):
                library.observe({'group_id':GROUP, 'message_id':source,
                    'message':[{'type':'mface','data':PACKET}]})
            observe('740465444'); library.mark_source_unavailable('740465444')
            library = EmojiLibrary(folder, Path(folder)/'missing', shared=True)
            def media():
                choices = library.choices('妈妈')
                return selected_media({'media_kind':'market_face','media_id':choices[0]['id']}, choices)
            self.assertEqual(media(), [{'type':'mface','data':PACKET}])
            observe('740465444')  # History replay must not forget the failed mapping.
            self.assertEqual(media()[0]['type'], 'mface')
            observe('991')
            self.assertEqual(media()[0]['type'], '_native_market_face')

    def test_poll_loop_retires_failed_outbox_and_accepts_next_mention(self):
        async def run(folder):
            polls, failures, generations, sent = 0, 0, 0, []
            cfg = {**bot.settings(), 'passive_probability':0, 'memory_enabled':False,
                'style_learning_enabled':False, 'background_image_reading_enabled':False,
                'proactive_topics_enabled':False, 'mention_cooldown_seconds':0}
            root = Path(folder)
            pending = {'_id':'old', '_time':time.time(), '_uid':'3035936740', 'sender':'群友',
                'text':'来个表情', 'at_bot':True, '_audience':'direct', '_remaining_delivery':old_plan()}
            (root/'pending-mentions.json').write_text(json.dumps([pending]), encoding='utf-8')
            sleep = asyncio.sleep
            async def tick(seconds):await sleep(0);await sleep(0)
            async def api(client, env, action, params=None):
                nonlocal polls, failures
                if action == 'get_login_info':return {'user_id':169901502}
                if action == 'get_group_info':return {}
                if action == 'get_group_msg_history':
                    if params.get('count') == 100:return {'messages':[]}
                    polls += 1
                    if polls > 10:(root/'STOP').touch()
                    return {'messages': [] if polls < 3 else [{'message_id':'new', 'time':time.time(),
                        'user_id':3035936740, 'sender':{'nickname':'群友'}, 'message':[
                            {'type':'at','data':{'qq':'169901502'}}, {'type':'text','data':{'text':'还在吗'}}]}]}
                if action == 'forward_group_single_msg':
                    failures += 1
                    raise rejected(action)
                if action == 'send_group_msg':
                    sent.append(params['message']);(root/'STOP').touch();return {'message_id':999}
                raise AssertionError(action)
            async def generate(*args, **kwargs):
                nonlocal generations
                generations += 1
                return '在'
            with patch.object(bot,'RUNTIME',root), patch.object(bot,'settings',return_value=cfg), \
                    patch.object(bot,'api',api), patch.object(bot,'generate_for_message',generate), \
                    patch.object(bot,'maintain_images',AsyncMock()), patch.object(asyncio,'sleep',tick), \
                    patch.object(bot.debug_events,'emit') as emit:
                await bot.run()
            self.assertEqual(failures, 1)
            self.assertEqual(generations, 1)
            self.assertEqual(len(sent), 1)
            self.assertEqual(json.loads((root/'pending-mentions.json').read_text(encoding='utf-8')), [])
            failures = [call.kwargs for call in emit.call_args_list if call.kwargs.get('role') == 'reply_failure']
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]['context_id'], 'old')
            self.assertIn('无法找到消息740465444', failures[0]['text'])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))


if __name__ == '__main__':unittest.main()
