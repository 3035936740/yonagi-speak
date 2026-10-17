import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bot
from prompt_cache import stable_schema,check_constraints,transport_schema


def spec(identity):
    return {'type':'object','properties':{'reply_to':{'type':'string','const':identity},'media_ids':{'type':'array','maxItems':2,'items':{'type':'string','enum':[identity+'-sticker']}},
        'action':{'type':'string','enum':['chat']},'member_updates':{'type':'array','items':{'type':'object','properties':{'qq_id':{'type':'string','enum':[identity]}}}}}}


class PromptCacheTests(unittest.TestCase):
    def test_schema_stable_across_message_and_candidate_ids_keeps_policy(self):
        first=spec('a');original=deepcopy(first)
        self.assertEqual(stable_schema(first),stable_schema(spec('b')));self.assertEqual(first,original)
        self.assertEqual(stable_schema(first)['properties']['action']['enum'],['chat'])
        self.assertEqual(stable_schema(first)['properties']['media_ids']['maxItems'],2)
        check_constraints(first,{'reply_to':'a','media_ids':['a-sticker'],'action':'chat'})
        for bad in [{'reply_to':'b'},{'media_ids':['invented']},{'action':'draw'},{'member_updates':[{'qq_id':'wrong'}]}]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):check_constraints(first,bad)

    def test_identical_schema_gets_same_path_across_temp_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);one=root/'one.json';two=root/'two.json';one.write_text(json.dumps(spec('a')));two.write_text(json.dumps(spec('b')))
            a,_=transport_schema(one,root);b,_=transport_schema(two,root)
            self.assertEqual(a,b);self.assertEqual(len(list((root/'prompt-cache-schemas').glob('*.json'))),1)

    def test_actual_model_transport_stabilizes_schema_and_rejects_invalid_id(self):
        async def run(root):
            schema=root/'reply.json';schema.write_text(json.dumps(spec('a')));paths=[];answer={'reply_to':'a','media_ids':['a-sticker'],'action':'chat'}
            class Process:
                returncode=0
                async def communicate(self,content):
                    output.write_text(json.dumps(answer));return b'',b''
            async def launch(*command,**kwargs):
                nonlocal output
                output=Path(command[command.index('-o')+1]);path=Path(command[command.index('--output-schema')+1]);paths.append(path)
                self.assertNotIn('const',json.loads(path.read_text())['properties']['reply_to']);return Process()
            output=None
            with patch.object(bot,'RUNTIME',root),patch.object(bot,'resolve_codex_executable',return_value='mock'),patch.object(bot.asyncio,'create_subprocess_exec',launch):
                self.assertEqual(await bot.generate_json(bot.settings(),'target a',schema),answer)
                answer={'reply_to':'wrong'}
                with self.assertRaises(ValueError):await bot.generate_json(bot.settings(),'target a',schema)
            self.assertEqual(paths[0],paths[1])
        with tempfile.TemporaryDirectory() as folder:asyncio.run(run(Path(folder)))


if __name__=='__main__':unittest.main()
