import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bot
from console_config import DEFAULTS,Config,atomic
from model_timeouts import combined_timeout,request_timeout,vision_config
from role_analysis import analyze


class TimeoutTests(unittest.TestCase):
    def test_independent_defaults_and_all_mixed_combinations(self):
        self.assertEqual([DEFAULTS[key] for key in ('model_timeout_seconds','vision_timeout_seconds','image_timeout_seconds')],[30,60,150])
        for stages,expected in [({'chat':True},30),({'vision':True},60),({'image':True},150),
                ({'chat':True,'vision':True},90),({'vision':True,'image':True},210),
                ({'chat':True,'vision':True,'image':True},240)]:
            self.assertEqual(combined_timeout({},**stages),expected)
        self.assertEqual(request_timeout({},['a.jpg','b.jpg']),90)  # not once per image
        self.assertEqual(request_timeout(vision_config({}),['a.jpg']),60)
    def test_vision_role_is_not_capped_by_chat_or_planner_limit(self):
        async def run(folder):
            budgets={}
            async def model(cfg,prompt,schema,images=None):
                budgets[schema.name.split('.')[0]]=(cfg['model_timeout_seconds'],request_timeout(cfg,images))
                if schema.name.startswith('vision'):
                    return {'description':'猫猫表情','visible_text':'','confidence':.9,'uncertainty':''}
                return {}
            cfg={**bot.settings(),'model_timeout_seconds':30,'vision_timeout_seconds':60,
                'role_timeout_seconds':16,'multi_role_enabled':True,'_reserve_extra_call':lambda:True}
            await analyze(cfg,[],{'_id':'t','text':'看看'},[Path(folder)/'image.jpg'],model,folder)
            self.assertEqual(budgets['vision'],(60,60))
            self.assertEqual(budgets['context'],(16,16));self.assertEqual(budgets['emotion'],(16,16))
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))
    def test_model_subprocess_actually_receives_combined_timeout(self):
        async def run(folder):
            budgets=[]
            class Process:
                returncode=0
                async def communicate(self,content):
                    output.write_text('{"ok":true}',encoding='utf-8');return b'',b''
            output=None
            async def launch(*command,**kwargs):
                nonlocal output
                output=Path(command[command.index('-o')+1]);return Process()
            original=asyncio.wait_for
            async def wait(awaitable,timeout):
                budgets.append(timeout);return await original(awaitable,timeout)
            with patch.object(bot,'RUNTIME',Path(folder)),patch.object(bot,'resolve_codex_executable',return_value='mock'),patch.object(bot.asyncio,'create_subprocess_exec',launch),patch.object(bot.asyncio,'wait_for',wait):
                for images in (None,[Path(folder)/'image.jpg']):
                    await bot.generate_json({**bot.settings(),'model_timeout_seconds':30,'vision_timeout_seconds':60},'test',bot.ROOT/'reply.schema.json',images)
            self.assertEqual(budgets,[30,90])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(folder))
    def test_config_persists_inherits_and_validates_each_timeout(self):
        with tempfile.TemporaryDirectory() as folder,patch('console_config.validate_model'):
            root=Path(folder);cfg={**bot.settings(),'group_id':100,'chat_group_whitelist':[100,200]};atomic(root/'settings.json',cfg)
            Config(root).set('vision_timeout_seconds','75')
            self.assertEqual(Config(root,100).load()['vision_timeout_seconds'],75)
            Config(root,100).set('vision_timeout_seconds','90')
            self.assertEqual(Config(root,100).load()['vision_timeout_seconds'],90)
            self.assertEqual(Config(root,200).load()['vision_timeout_seconds'],75)
            for value in ('0','-1','7201'):
                with self.assertRaises(ValueError):Config(root).set('model_timeout_seconds',value)


if __name__=='__main__':unittest.main()
