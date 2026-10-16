import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import bot
from dialogue import audience, quote_needed
from interaction import social_act, group_mentions, select_hook, quality_issue, InteractionMemory
from reply_behavior import preferences
from group_memory import GroupMemory


class InteractionTests(unittest.TestCase):
    def item(self, key, text, uid='a', seconds=0, direct=False):
        return {'_id': key, '_uid': uid, '_time': time.time() + seconds, 'text': text,
            'sender': uid, 'at_bot': direct, '_audience': 'direct' if direct else 'open', '_images': []}

    def test_calls_and_goofing_are_not_information_questions(self):
        self.assertEqual(social_act('@169901502 主人'), 'role_play')
        self.assertEqual(social_act('妈妈\n妈妈'), 'role_play')
        self.assertEqual(social_act('喵喵喵'), 'play')
        self.assertEqual(social_act('我拔你网线就知错了'), 'banter')
        self.assertEqual(social_act('ffmpeg 怎么安装'), 'question')
        self.assertEqual(social_act('妈妈给我画一个头像'), 'request')

    def test_short_text_picture_followups_form_one_turn(self):
        first = self.item('1', '@169901502 画你头像的表情包', direct=True)
        picture = self.item('2', '[图片，未读取内容]', seconds=2)
        picture['_images'] = ['https://example.test/ref']
        tail = self.item('3', 'LINE风格，无语的（', seconds=3)
        turns = group_mentions([first, picture, tail], bot.wants_image)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]['_id'], '1')
        self.assertEqual(turns[0]['_source_ids'], ['1', '2', '3'])
        self.assertIn('LINE风格', turns[0]['text'])
        self.assertEqual(turns[0]['_images'], picture['_images'])
        self.assertEqual(first['text'], '@169901502 画你头像的表情包')
        self.assertTrue(quote_needed(turns[0], [], first['_time'] + 46))

    def test_independent_tasks_or_intervening_people_are_not_merged(self):
        first = self.item('1', '@169901502 画张图', direct=True)
        other = self.item('2', '代码怎么写', seconds=2, direct=True)
        self.assertEqual(len(group_mentions([first, other], bot.wants_image)), 2)
        human = self.item('3', '我下机了', uid='b', seconds=1)
        tail = self.item('4', '可以', seconds=2)
        self.assertNotIn('可以', group_mentions([first, human, tail], bot.wants_image)[0]['text'])

    def test_passive_hook_uses_topic_instead_of_last_acknowledgement(self):
        topic = self.item('1', '你tibo叔叔又发重置了')
        ack = self.item('2', '草', uid='b', seconds=1)
        self.assertIs(select_hook([topic, ack], [topic, ack]), topic)
        self.assertEqual(audience(topic, [], 'bot'), 'open')

    def test_statement_followup_is_considered_without_stealing_another_thread(self):
        mine = {'_id': 'b1', '_uid': 'bot', '_time': time.time() - 10, 'text': '在呢',
            'is_bot': True, '_reply_to_uid': 'a', '_reply_to_text': '下机了吗'}
        followup = self.item('1', '我拔你网线就知错了')
        self.assertEqual(audience(followup, [mine], 'bot'), 'possibly_direct')
        switched = self.item('2', '@other 你下机了吗', seconds=-5)
        switched['_audience'], switched['_at_ids'] = 'others', ['other']
        self.assertEqual(audience(followup, [mine, switched], 'bot'), 'others')

    def test_reply_delivery_preferences_do_not_conflict(self):
        cfg = {**bot.settings(),'max_prompt_chars':6000}
        for _ in range(150):
            mode = preferences(cfg, True, self.item('1', '妈妈'))
            self.assertFalse(mode['prefer_reaction'] and mode['prefer_followup_sticker'])
            technical = preferences(cfg, True, self.item('2', 'ffmpeg怎么安装'))
            self.assertFalse(technical['prefer_reaction'])

    def test_light_teasing_requires_a_prior_exchange(self):
        familiar={**self.item('2','原版内部怎么分类的',direct=True),
            '_interaction_memory':{'recent_interaction_count':1,'earlier_exchanges':[]}}
        stranger={**familiar,'_interaction_memory':{}}
        with patch('reply_behavior.random.random',return_value=0):
            self.assertEqual(preferences({**bot.settings(),'max_prompt_chars':6000},False,familiar)['peer_voice'],'light_tease')
            self.assertEqual(preferences({**bot.settings(),'max_prompt_chars':6000},False,stranger)['peer_voice'],'casual_peer')

    def test_interactions_survive_restart_and_remain_attributed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'memory.json'
            memory = InteractionMemory(path)
            target = self.item('u1', '画一张我头像的LINE贴纸')
            memory.remember({'_id': 'b1', '_time': time.time(), 'text': '[已发送图片]', '_media_sent': True}, target)
            restored = InteractionMemory(path)
            result = restored.retrieve(self.item('u2', '还记得上次那个图吗'))
            self.assertEqual(result['earlier_exchanges'][0]['bot_said'], '发了一张图片或表情')
            self.assertEqual(result['recent_interaction_count'],1)
            self.assertFalse(restored.retrieve(self.item('u3', '还记得上次那个图吗', uid='b'))['earlier_exchanges'])

    def test_only_feedback_about_our_reply_affects_our_style(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = InteractionMemory(Path(folder) / 'memory.json')
            memory.remember({'_id': 'b1', '_time': time.time() - 3, 'text': '嗯？怎么突然叫主人'}, self.item('u1', '主人'))
            critique = {**self.item('u2', '你还是太客服了', direct=True), '_reply_id': 'other-message'}
            memory.observe(critique)
            self.assertFalse(memory.feedback)
            critique['_reply_id'] = 'b1'
            memory.observe(critique)
            self.assertIn('avoid_service_and_commentary', memory.retrieve(self.item('u3', '主人'))['feedback_hints'])

    def test_quality_checks_do_not_turn_legitimate_questions_into_silence(self):
        call = {**self.item('1', '主人', direct=True), '_social_act': 'role_play'}
        self.assertTrue(quality_issue('怎么突然叫上主人了（', call))
        self.assertTrue(quality_issue('有什么需要我帮忙的', call))
        self.assertTrue(quality_issue('咋啦', call, ['咋了', '嗯？']))
        technical = self.item('2', 'ffmpeg报错怎么处理', direct=True)
        self.assertFalse(quality_issue('请提供更多报错信息', technical))
        self.assertFalse(quality_issue('来了（', call))
        invitation = self.item('3', '和我教培', direct=True)
        self.assertTrue(quality_issue('教培什么呀（', invitation))
        topic = self.item('4', '你tibo叔叔又发重置了')
        self.assertTrue(quality_issue('又重置了啊', topic))

    def test_live_context_has_room_even_with_large_memory_payload(self):
        cfg = {**bot.settings(),'max_prompt_chars':6000}
        now = time.time()
        target = {**self.item('target', '刚才你说啥', direct=True), '_memories': ['历史资料' * 200],
            '_learned_style': {'samples': ['风格' * 250]}, '_expressions': ['表达' * 200]}
        recent = [self.item(str(i), '当前讨论第' + str(i) + '句', uid='human', seconds=-20 + i) for i in range(10)]
        prompt = bot.build_prompt(cfg, recent, '测试', target)
        self.assertLessEqual(len(prompt), 6000)
        self.assertGreaterEqual(sum('当前讨论第' + str(i) + '句' in prompt for i in range(10)), 8)

    def test_bad_draft_can_be_repaired_once_with_a_budget_reservation(self):
        async def scenario():
            calls, reserved = [], []
            target = self.item('1', '主人', direct=True)
            base = {'reply_to': '1', 'conversation': 'direct', 'should_reply': True, 'action': 'chat',
                'text_parts': [], 'media_kind': 'none', 'media_id': ''}
            async def generate(cfg, prompt, schema, images=None):
                calls.append(prompt)
                return {**base, 'text': '怎么突然叫主人了' if len(calls) == 1 else '来了（'}
            def reserve():
                reserved.append(True)
                return True
            with patch.object(bot, 'generate_json', generate):
                reply = await bot.generate({**{**bot.settings(),'max_prompt_chars':6000}, '_reserve_extra_call': reserve}, [], '被@', target)
            self.assertEqual(str(reply), '来了（')
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(reserved), 1)
            self.assertTrue(all(len(prompt) <= 6000 for prompt in calls))
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
