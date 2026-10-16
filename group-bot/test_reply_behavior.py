import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image
import bot
from dialogue import passive_candidate
from emoji_library import EmojiLibrary, ReplyText
from message_delivery import delivery_plan, deliver_burst
from reply_behavior import allowed_media, preferences, reply_chunks, reply_segments


class ReplyBehaviorTests(unittest.TestCase):
    def test_sticker_policy_depends_on_scene_and_recent_bot_media(self):
        cfg = {'sticker_reply_probability': 1, 'followup_sticker_probability': 1,
            'no_words_sticker_probability': 1, 'friendly_tease_probability': 0}
        with patch('reply_behavior.random.random', return_value=0):
            playful = preferences(cfg, True, {'text': '妈妈', '_sticker_ids': ['s1']})
            self.assertEqual(playful['sticker_policy'], 'reaction_or_followup')
            self.assertTrue(playful['allow_no_words_reaction'])
            self.assertTrue(playful['allow_followup_sticker'])
            technical = preferences(cfg, True, {'text': '帮我详细总结一下这个机制'})
            self.assertEqual(technical['sticker_policy'], 'text_only')
            self.assertFalse(technical['allow_followup_sticker'])
            repeated = preferences(cfg, True, {'text': '妈妈', '_sticker_ids': ['s1'],
                '_recent_bot_media_count': 2})
            self.assertEqual(repeated['sticker_policy'], 'reaction_or_followup')
            self.assertLess(repeated['sticker_preferences']['reaction'],playful['sticker_preferences']['reaction'])

    def test_context_gate_rejects_model_sticker_in_text_only_lane(self):
        media = [{'type': 'image', 'data': {'file': 'base64://x'}}]
        self.assertEqual(allowed_media(media, '技术回答', {'sticker_policy': 'text_only'}), [])
        self.assertEqual(allowed_media(media, '绑不住了', {'allow_followup_sticker': True}), media)
        self.assertEqual(allowed_media(media, '', {'allow_no_words_reaction': True}), media)

    def test_semantic_burst_keeps_bracket_and_separates_sticker(self):
        chunks = reply_chunks({'text_parts': ['这也太大了（', '硬盘：我先走了']}, 'unused', 120, True, True)
        reply = ReplyText('\n'.join(chunks), [{'type': 'image', 'data': {'file': 'base64://abc'}}], chunks=chunks)
        plan = delivery_plan([{'type': 'reply', 'data': {'id': '1'}}] + reply_segments(reply), 985569438)
        self.assertEqual(len(plan), 3)
        self.assertEqual(plan[0]['params']['message'][1]['data']['text'], '这也太大了（')
        self.assertEqual(plan[1]['params']['message'][0]['data']['text'], '硬盘：我先走了')
        self.assertEqual(plan[2]['params']['message'][0]['type'], 'image')
        self.assertFalse(any(s['type'] == 'reply' for s in plan[1]['params']['message']))

    def test_quote_and_at_are_optional_grounded_delivery_actions(self):
        now=time.time()
        target={'_id':'101','_uid':'11','_self_id':'169901502','_time':now,'sender':'甲'}
        history=[{'_id':'202','_uid':'22','_time':now-8,'sender':'乙','text':'叫我干嘛'}]
        target['_delivery_options']=bot.available_delivery_options(target,history)
        quote,at_uids=bot.delivery_metadata(
            {'quote_reply':True,'at_message_ids':['202','invented']},target)
        reply=ReplyText('你也来看看（',quote_reply=quote,at_uids=at_uids)
        segments=bot.outgoing_segments(reply,target,history)
        self.assertEqual([s['type'] for s in segments[:4]],['reply','at','text','text'])
        self.assertEqual(segments[0]['data']['id'],'101')
        self.assertEqual(segments[1]['data']['qq'],'22')
        direct=bot.outgoing_segments(ReplyText('不用引用',quote_reply=False),target,history)
        self.assertEqual(direct[0]['type'],'text')
        with self.assertRaises(ValueError):
            delivery_plan([{'type':'at','data':{'qq':'invented'}},{'type':'text','data':{'text':'x'}}],985569438)

    def test_reply_schema_only_allows_recent_delivery_targets(self):
        async def scenario():
            now=time.time()
            target={'_id':'101','_uid':'11','_self_id':'169901502','_time':now,'sender':'甲',
                'text':'叫乙来看看','at_bot':True,'_audience':'direct','_reply_style':{},'_media_choices':[]}
            context=[{'_id':'202','_uid':'22','_time':now-5,'sender':'乙','text':'我在'}]
            async def generate(cfg,prompt,schema,images=None):
                spec=json.loads(schema.read_text(encoding='utf-8'))
                self.assertEqual(set(spec['properties']['at_message_ids']['items']['enum']),{'101','202'})
                self.assertIn('delivery_options',prompt)
                return {'reply_to':'101','should_reply':True,'conversation':'direct','text':'乙你也看看（',
                    'text_parts':[],'reply_mode':'text','media_kind':'none','media_id':'','action':'chat',
                    'image_request':'','media_observations':[],'quote_reply':False,'at_message_ids':['202']}
            with patch.object(bot,'generate_json',generate):
                reply=await bot.generate({**bot.settings(),'at_reply_probability':1,'quote_reply_probability':1},context,'被@',target)
            self.assertEqual(reply.at_uids,['22'])
            # A 100% quote setting now executes the attachment rather than
            # merely giving the model permission to opt out a second time.
            self.assertTrue(reply.quote_reply)
        asyncio.run(scenario())

    def test_short_text_urls_and_emoticons_are_not_chopped(self):
        for text in ['绷（', '去 https://example.com/a,b，然后重启一下就可以了', '这个颜文字（这个，那个）不能被切开']:
            self.assertEqual(reply_chunks({}, text, 120, True), [text])
        self.assertEqual(reply_chunks({'text_parts': ['行（', '那我先试试']}, '', 120, False), ['行（，那我先试试'])

    def test_burst_budget_and_retry_keep_exact_remaining_parts(self):
        async def scenario():
            reply = ReplyText('一\n二\n三', chunks=['一', '二', '三'])
            plan = delivery_plan(reply_segments(reply), 985569438)
            sent, checkpoints, budget = [], [], [1]
            async def api(client, env, action, params):
                sent.append(params['message'][0]['data']['text'])
                return {'message_id': len(sent)}
            def record(result, part):
                budget[0] -= 1
                checkpoints.append(json.dumps(plan))
            await deliver_burst(api, None, None, plan, lambda: budget[0] > 0, record, delay=(0, 0))
            self.assertEqual(sent, ['一'])
            self.assertEqual(len(plan), 2)
            # Restart from the checkpoint; only the unsent messages survive.
            resumed = json.loads(checkpoints[-1])
            budget[0] = 10
            failed = [False]
            async def flaky(client, env, action, params):
                if not failed[0]:
                    failed[0] = True
                    raise OSError('temporary network error')
                return await api(client, env, action, params)
            with self.assertRaises(OSError):
                await deliver_burst(flaky, None, None, resumed, lambda: True, record, delay=(0, 0))
            self.assertEqual(len(resumed), 2)
            await deliver_burst(flaky, None, None, resumed, lambda: True, record, delay=(0, 0))
            self.assertEqual(sent, ['一', '二', '三'])
            self.assertFalse(resumed)
        asyncio.run(scenario())

    def test_stop_between_parts_prevents_the_next_send(self):
        async def scenario():
            plan = delivery_plan(reply_segments(ReplyText('a\nb', chunks=['a', 'b'])), 985569438)
            active = [True]
            async def api(*args):
                return {}
            def record(*args):
                active[0] = False
            await deliver_burst(api, None, None, plan, lambda: active[0], record, delay=(0, 0))
            self.assertEqual(len(plan), 1)
        asyncio.run(scenario())

    def test_old_stickers_rotate_and_actual_received_sticker_is_available(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = EmojiLibrary(root, root / 'missing.json')
            for i in range(20):
                path = root / f'{i}.png'
                Image.new('RGB', (8, 8), (i, 0, 0)).save(path)
                library.items[str(i)] = {'id': str(i), 'kind': 'sticker', 'label': '表情', 'path': str(path),
                    'preview': str(path), 'time': time.time() - 20 + i, 'asset_type': 'sticker', 'filter_version': 1, 'sticker_confidence': 0.95}
            seen = set()
            for _ in range(12):
                choices = library.choices('绷不住', received=['0'])
                stickers = [s for s in choices if s['kind'] == 'sticker']
                self.assertEqual(len(stickers), 8)
                self.assertTrue(stickers[0]['recently_received'])
                seen.update(s['id'] for s in stickers)
            self.assertGreater(len(seen), 8)
            library.mark_used('0')
            restored = EmojiLibrary(root, root / 'missing.json')
            self.assertEqual(restored.items['0']['uses'], 1)
            self.assertTrue(library.contact_sheet(choices, root / 'sheet.jpg'))
            with Image.open(root / 'sheet.jpg') as sheet:
                self.assertEqual(sheet.size, (384, 848))

    def test_no_words_can_react_but_does_not_override_other_conversation(self):
        async def scenario():
            target = {'_id': '1', '_time': time.time(), 'text': '绷', 'sender': 'a', '_audience': 'open',
                '_reply_style': {'allow_no_words_reaction': True, 'allow_burst': True},
                '_media_choices': [{'id': 'm1', 'kind': 'market_face', 'source_message_id': '123',
                    'source_group_id': 985569438, 'label': '妈妈来了'}]}
            response = {'reply_to': '1', 'should_reply': False, 'conversation': 'open', 'text': '',
                'text_parts': [], 'reply_mode': 'no_words', 'media_kind': 'market_face', 'media_id': 'm1'}
            async def generate(*args):
                return response
            with patch.object(bot, 'generate_json', generate):
                reply = await bot.generate(bot.settings(), [], '闲聊', target)
                self.assertTrue(reply)
                self.assertEqual(str(reply), '')
                self.assertEqual(reply.segments[0]['type'], '_native_market_face')
                response['conversation'] = 'others'
                self.assertFalse(await bot.generate(bot.settings(), [], '闲聊', target))
                response['conversation'] = 'open'
                target['_reply_style']['allow_no_words_reaction'] = False
                self.assertFalse(await bot.generate(bot.settings(), [], '闲聊', target))
                response['reply_mode'] = 'silent'
                target['_reply_style']['allow_no_words_reaction'] = True
                self.assertFalse(await bot.generate(bot.settings(), [], '闲聊', target))
        asyncio.run(scenario())

    def test_only_collected_sticker_can_trigger_passive_picture_reaction(self):
        item = {'_id': '1', '_time': time.time(), 'text': '[图片，未读取内容]', '_audience': 'open'}
        self.assertFalse(passive_candidate(item, [], '169901502'))
        self.assertTrue(passive_candidate({**item, '_sticker_ids': ['s1']}, [], '169901502'))
        self.assertFalse(passive_candidate({**item, '_sticker_ids': ['s1'], '_at_ids': ['123']}, [], '169901502'))

    def test_emotion_classification_is_bounded_grounded_and_persistent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = EmojiLibrary(root, root / 'missing.json')
            library.items = {'s1': {'id': 's1', 'kind': 'sticker', 'time': time.time(), 'asset_type': 'sticker', 'filter_version': 1, 'sticker_confidence': 0.95}}
            data = {'id': 's1', 'emotion': '无语', 'description': '猫猫翻白眼', 'use_when': '无奈或不想接话时'}
            choices = [library.items['s1']]
            self.assertEqual(library.learn_emotions([{**data, 'id': 'invented'}], choices), 0)
            self.assertEqual(library.learn_emotions([{**data, 'emotion': 'invalid'}], choices), 0)
            self.assertEqual(library.learn_emotions([data], []), 0)
            self.assertEqual(library.learn_emotions([data], choices), 1)
            self.assertEqual(library.learn_emotions([{**data, 'emotion': '开心'}], choices), 0)
            restored = EmojiLibrary(root, root / 'missing.json')
            self.assertEqual(restored.items['s1']['emotion'], '无语')
            self.assertEqual(restored.items['s1']['use_when'], '无奈或不想接话时')

    def test_classification_reaches_owner_after_vision_target_copy(self):
        async def scenario():
            target = {'_id': '1', 'text': '你好', '_media_choices': []}
            async def generate(cfg, context, trigger, adjusted, images):
                adjusted['_media_observations'] = [{'id': 's1', 'emotion': '开心'}]
                return ''
            with patch.object(bot, 'generate', generate):
                self.assertEqual(await bot.generate_for_message(None, bot.settings(), [], '闲聊', target), '')
            self.assertEqual(target['_media_observations'][0]['id'], 's1')
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
