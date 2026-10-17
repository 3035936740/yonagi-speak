import asyncio
import base64
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image

import bot
from console_config import Config, atomic, prompt_path
from debug_events import DebugEvents
from debug_references import enrich_images
from debug_store import database_view, database_mutate, sticker_view, sticker_mutate, sticker_image, library
from debug_webui import DebugWebUI
from emoji_library import EmojiLibrary, sticker_source_key
from group_memory import GroupMemory
from image_memory import ImageMemory
from reply_behavior import reply_chunks


FACTS={'description':'挥手小猫贴纸','visible_text':'','confidence':.95,'uncertainty':''}

class AdminAndScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        cfg=bot.settings();cfg.update(group_id=100,chat_group_whitelist=[100,200]);atomic(self.root/'settings.json',cfg)
        (self.root/'persona.txt').write_text('全局yonagi人格',encoding='utf-8')
        (self.root/'reply_protocol.txt').write_text('全局系统规则',encoding='utf-8')
        (self.root/'runtime').mkdir();memory=GroupMemory(self.root/'runtime/group-memory.sqlite3');memory.close()
        self.validate=patch('console_config.validate_model');self.validate.start()
    def tearDown(self):self.validate.stop();self.temp.cleanup()

    def test_database_blank_ids_generate_unique_random_ids_and_keep_english_types(self):
        for values in [{},{'id':''},{'id':'  \t '},{'id':None}]:
            database_mutate(self.root,100,'notes','create',{**values,'kind':'fact','content':'人工记录'})
        rows=database_view(self.root,100,'notes')['rows']
        self.assertEqual(len(rows),4);self.assertEqual(len({row['id'] for row in rows}),4)
        for row in rows:
            self.assertRegex(row['id'],r'^[a-f0-9]{20}$');self.assertEqual(row['kind'],'fact')
        database_mutate(self.root,100,'notes','create',{'id':'manual-id','kind':'preference','content':'自定义编号'})
        row=database_view(self.root,100,'notes')['rows'][0]
        self.assertEqual(row['id'],'manual-id');self.assertEqual(row['kind'],'preference')
        with self.assertRaises(ValueError):database_mutate(self.root,100,'notes','create',{'kind':'事实','content':'不能存中文枚举'})

    def test_global_defaults_inherit_and_explicit_equal_override_stays_after_global_change(self):
        global_cfg=Config(self.root);local=Config(self.root,100);other=Config(self.root,200)
        self.assertEqual(local.load()['passive_probability'],global_cfg.load()['passive_probability'])
        local.set('rate','0.35');global_cfg.set('rate','0.7')
        self.assertEqual(local.load()['passive_probability'],.35)
        self.assertEqual(other.load()['passive_probability'],.7)
        self.assertEqual(Config(self.root,100).overrides(),{'passive_probability':.35})
        local.reset('rate');self.assertEqual(local.load()['passive_probability'],.7)
        self.assertEqual(local.overrides(),{})

    def test_group_prompt_inherits_and_blank_reset_preserves_global_and_other_group(self):
        local=Config(self.root,100)
        self.assertEqual(local.prompt_path('persona'),self.root/'persona.txt')
        local.set('prompt','局部群人格');self.assertEqual(local.prompt_path('persona').read_text(encoding='utf-8'),'局部群人格')
        self.assertEqual(Config(self.root,200).prompt_path('persona').read_text(encoding='utf-8'),'全局yonagi人格')
        self.assertEqual(prompt_path(self.root,{'group_id':100},'persona'),local.prompt_path('persona'))
        local.reset('prompt');self.assertEqual(local.prompt_path('persona'),self.root/'persona.txt')
        self.assertTrue(list((local.scope_root/'config-backups').glob('*.txt')))

    def test_group_config_cannot_change_whitelist_or_paths(self):
        local=Config(self.root,100)
        for key,value in [('whitelist','[200]'),('codex_exe','other.exe')]:
            with self.assertRaises(ValueError):local.set(key,value)
        with self.assertRaises(ValueError):Config(self.root,300).load()

    def test_image_cleanup_hours_persist_fractional_values_and_group_inheritance(self):
        global_cfg=Config(self.root);local=Config(self.root,100)
        global_cfg.set('generated_image_ttl_hours','2')
        global_cfg.set('generated_image_ttl_hours','1.5')
        self.assertEqual(local.load()['generated_image_ttl_hours'],1.5)
        local.set('generated_image_cleanup_interval_hours','0.5')
        self.assertEqual(Config(self.root,100).load()['generated_image_cleanup_interval_hours'],.5)
        self.assertEqual(Config(self.root,200).load()['generated_image_cleanup_interval_hours'],2)
        with self.assertRaises(ValueError):local.set('generated_image_ttl_hours','0')

    def test_cleanup_modes_time_validation_and_legacy_hours_conversion(self):
        cfg=Config(self.root);cfg.set('generated_image_cleanup_interval_hours','0.5')
        self.assertEqual(cfg.load()['generated_image_cleanup_interval_minutes'],30)
        cfg.set('generated_image_cleanup_mode','daily');cfg.set('generated_image_cleanup_daily_time','23:45')
        self.assertEqual(Config(self.root,200).load()['generated_image_cleanup_daily_time'],'23:45')
        for key,value in [('generated_image_cleanup_mode','unknown'),('generated_image_cleanup_daily_time','25:90'),('generated_image_cleanup_interval_minutes','0')]:
            with self.assertRaises(ValueError):cfg.set(key,value)

    def test_windows_plan_covers_only_authorized_groups_selecting_windows(self):
        from windows_image_cleanup import task_plan
        with patch('windows_image_cleanup.sync_tasks',return_value={}):
            Config(self.root).set('generated_image_cleanup_mode','windows')
            Config(self.root,200).set('generated_image_cleanup_mode','daily')
        plan=task_plan(self.root)
        self.assertEqual([row['group'] for row in plan['tasks']],[100])
        self.assertTrue(plan['tasks'][0]['name'].startswith('QQMCP-AI-Temp-'))
        self.assertEqual(plan['python'].split('\\')[-1],'pythonw.exe')

    def test_windows_registration_failure_rolls_back_saved_and_inherited_configuration(self):
        before=(self.root/'settings.json').read_bytes()
        with patch('windows_image_cleanup.sync_tasks',side_effect=[ValueError('denied'),{}]):
            with self.assertRaisesRegex(ValueError,'配置已恢复'):Config(self.root).set('generated_image_cleanup_mode','windows')
        self.assertEqual((self.root/'settings.json').read_bytes(),before)
        local=Config(self.root,100)
        with patch('windows_image_cleanup.sync_tasks',return_value={}):local.set('generated_image_cleanup_mode','windows')
        before=(local.scope_root/'settings.json').read_bytes()
        with patch('windows_image_cleanup.sync_tasks',side_effect=[ValueError('denied'),{}]):
            with self.assertRaisesRegex(ValueError,'配置已恢复'):local.reset('generated_image_cleanup_mode')
        self.assertEqual((local.scope_root/'settings.json').read_bytes(),before)

    def test_group_bot_loads_effective_probabilities_and_name(self):
        Config(self.root,200).set('name','夜凪');Config(self.root,200).set('rate','0.8')
        with patch.object(bot,'ROOT',self.root),patch.object(bot,'INSTANCE_GROUP_ID',200):
            cfg=bot.settings();self.assertEqual(cfg['bot_name'],'夜凪');self.assertEqual(cfg['group_id'],200);self.assertEqual(cfg['passive_probability'],.8)

    def test_api_exposes_inheritance_values_without_prefilling_local_fields(self):
        ui=DebugWebUI(self.root,lambda _:None)
        data=ui.public_config({'scope':['group'],'group':['100']})
        self.assertEqual(data['overrides'],{});self.assertFalse(data['prompt_overrides']['persona'])
        self.assertEqual(data['global_prompts']['persona'],'全局yonagi人格')
        self.assertNotIn('codex_exe',data['global_config'])

    def test_group_event_budget_uses_override(self):
        Config(self.root,100).set('text_limit','500')
        events=DebugEvents(self.root/'runtime',100)
        for n in range(10):events.emit('received',text='图'*100,message_id=str(n))
        self.assertLessEqual(sum(len(row['text']) for row in events.rows),500)

    def test_database_create_read_update_delete_backups_and_conflict(self):
        database_mutate(self.root,100,'notes','create',{'content':'偏爱轻音乐','keywords':['轻音乐']})
        data=database_view(self.root,100,'notes','轻音乐');self.assertEqual(data['total'],1)
        row=data['rows'][0];self.assertEqual(row['kind'],'fact')
        database_mutate(self.root,100,'notes','update',{'content':'偏爱氛围音乐'},row['_rowid'],row)
        with self.assertRaises(ValueError):database_mutate(self.root,100,'notes','update',{'content':'旧页面覆盖'},row['_rowid'],row)
        current=database_view(self.root,100)['rows'][0]
        database_mutate(self.root,100,'notes','delete',row_id=current['_rowid'],expected=current)
        self.assertEqual(database_view(self.root,100)['total'],0)
        self.assertGreaterEqual(len(list((self.root/'runtime/database-backups').glob('*.sqlite3'))),3)

    def test_database_blocks_unknown_tables_fields_foreign_group_invalid_values(self):
        for table in ['meta','notes; DROP TABLE notes','sqlite_master']:
            with self.assertRaises(ValueError):database_view(self.root,100,table)
        for values in [{'content':''},{'content':'x','count':'oops'},{'content':'x','unexpected':'z'}]:
            with self.assertRaises(ValueError):database_mutate(self.root,100,'notes','create',values)
        with self.assertRaises(ValueError):database_view(self.root,300)

    def test_picture_frequency_deduplicates_messages_aliases_and_survives_restart(self):
        path=self.root/'runtime/image-memory.json';cache=ImageMemory(path,10)
        cache.observe('1',['a','b','a']);cache.bind('a','bytes');cache.bind('b','bytes');cache.store('a','bytes',FACTS)
        self.assertEqual(cache.frequency(digest='bytes')['seen_count'],1)
        cache.observe('1',['a']);self.assertEqual(cache.frequency(key='a')['seen_count'],1)
        for n in range(2,5):cache.observe(str(n),['a'])
        self.assertEqual(ImageMemory(path,10).frequency(key='a')['seen_count'],4)
        for n in range(10):cache.observe('text'+str(n),[])
        self.assertEqual(cache.frequency(key='a')['seen_count'],0)

    def test_repeat_renews_ttl_and_upgrade_recovers_old_count(self):
        path=self.root/'runtime/image-memory.json';cache=ImageMemory(path,10)
        cache.observe('1',['a']);cache.store('a','digest',FACTS)
        for n in range(8):cache.observe('gap'+str(n),[])
        cache.observe('again',['a']);self.assertEqual(cache.frequency(key='a')['remaining_messages'],10)
        cache.data['sources']['a'].pop('occurrences');cache.save()
        self.assertEqual(ImageMemory(path,10).frequency(key='a')['seen_count'],2)

    def test_high_repeat_reconsiders_photo_but_never_screenshots(self):
        lib=library(self.root);data={'file':'same-photo'};key=sticker_source_key(data)
        lib.reject(key,'photo');lib.offer_candidate(data,{'seen_count':5});self.assertEqual(lib.queue.qsize(),1)
        lib=library(self.root);lib.reject(key,'screenshot');lib.offer_candidate(data,{'seen_count':100});self.assertEqual(lib.queue.qsize(),0)

    def test_upload_preview_then_review_admission_edit_delete(self):
        async def run():
            stream=io.BytesIO();Image.new('RGB',(12,12),'red').save(stream,format='PNG');raw=stream.getvalue()
            sticker_mutate(self.root,'create',values={'label':'挥手','image_base64':base64.b64encode(raw).decode()})
            pending=sticker_view(self.root)['rows'][0];self.assertEqual(pending['state'],'pending')
            self.assertEqual(sticker_image(self.root,pending['id'])[0],raw)
            async def classify(path,key):return {'id':key,'asset_type':'sticker','confidence':.95,'emotion':'打招呼','description':'挥手贴纸','use_when':'打招呼'}
            lib=EmojiLibrary(self.root/'shared-emoji-library',self.root/'qq-faces.json',classifier=classify,shared=True)
            lib.offer_uploads();worker=asyncio.create_task(lib.collector(None))
            try:await asyncio.wait_for(lib.queue.join(),2)
            finally:worker.cancel();await asyncio.gather(worker,return_exceptions=True)
            row=sticker_view(self.root)['rows'][0];self.assertEqual(row['state'],'approved')
            self.assertEqual(sticker_image(self.root,row['id'])[0],raw)
            sticker_mutate(self.root,'update',row['id'],{'emotion':'开心','use_when':'开心时'})
            self.assertEqual(sticker_view(self.root)['rows'][0]['emotion'],'开心')
            sticker_mutate(self.root,'delete',row['id'])
            with self.assertRaises(ValueError):sticker_image(self.root,row['id'])
        asyncio.run(run())

    def test_market_names_format_and_edits_survive_rediscovery(self):
        lib=library(self.root);packet={'group_id':100,'message_id':123,'message':[{'type':'mface','data':{
            'emoji_package_id':10,'emoji_id':'abcdef','key':'qq-rendering-key','summary':'[挥手]','package_name':'小猫'}}]}
        with patch('emoji_library.authorized_groups',return_value={100}):lib.observe(packet)
        row=sticker_view(self.root)['rows'][0];self.assertEqual(row['title'],'[小猫_挥手]')
        sticker_mutate(self.root,'update',row['id'],{'package_name':'猫咪','emoji_name':'你好'})
        packet['message'][0]['data'].pop('package_name')
        with patch('emoji_library.authorized_groups',return_value={100}):library(self.root).observe(packet)
        self.assertEqual(sticker_view(self.root)['rows'][0]['title'],'[猫咪_你好]')

    def test_long_single_part_is_split_without_losing_the_substantive_tail(self):
        text='这次读到了具体仓库信息，'+('Orzmic和工具箱都是实际项目，'*4)+'最后结论是项目跨度很大（'
        parts=reply_chunks({'text_parts':[text]},text,300,True)
        self.assertGreater(len(parts),1);self.assertIn('最后结论是项目跨度很大',parts[-1])
        def normalize(value):return __import__('re').sub(r'[，。；\n]','',value)
        self.assertEqual(normalize(''.join(parts)),normalize(text))

    def test_repeated_photo_requires_model_approval_and_persistent_usage(self):
        from sticker_filter import accepts,approved
        entry={'id':'s1','asset_type':'sticker','confidence':.95,'reason':'这是实拍动物反应图，长期用于表达不满'}
        self.assertFalse(accepts(entry,'s1'))
        self.assertTrue(accepts(entry,'s1',{'seen_count':5}))
        item={'kind':'sticker','asset_type':'sticker','filter_version':1,'sticker_confidence':.95,
            'admission_reason':entry['reason'],'usage_evidence':{'seen_count':5},'repeat_review_min_count':5}
        self.assertTrue(approved(item))
        self.assertFalse(accepts({**entry,'asset_type':'photo'},'s1',{'seen_count':100}))
        self.assertFalse(accepts({**entry,'reason':'这是截图，不是独立表情'},'s1',{'seen_count':100}))

    def test_database_relations_reject_orphans_and_cycles(self):
        for identity in ('old','new'):
            database_mutate(self.root,100,'notes','create',{'id':identity,'content':'事实','subject':'friend'})
        with self.assertRaises(ValueError):database_mutate(self.root,100,'note_metadata','create',{'note_id':'missing'})
        database_mutate(self.root,100,'supersessions','create',{'old_id':'old','new_id':'new'})
        with self.assertRaises(ValueError):database_mutate(self.root,100,'supersessions','create',{'old_id':'new','new_id':'old'})
        self.assertEqual(database_view(self.root,100,'supersessions')['total'],1)

    def test_local_enabled_survives_global_disabled_in_supervisor(self):
        from debug_console import Supervisor
        Config(self.root).set('enabled','off');Config(self.root,200).set('enabled','on')
        supervisor=Supervisor(self.root)
        child=__import__('unittest.mock',fromlist=['Mock']).Mock();child.poll.return_value=None
        supervisor.children={100:child,200:child}
        with patch.object(supervisor,'stop_group',side_effect=lambda group:supervisor.children.pop(group)) as stop:
            supervisor.sync();stop.assert_called_once_with(100)
        self.assertIn(200,supervisor.children)

    def test_uploaded_candidate_claimed_once_across_group_instances(self):
        stream=io.BytesIO();Image.new('RGB',(12,12),'red').save(stream,format='PNG')
        sticker_mutate(self.root,'create',values={'image_base64':base64.b64encode(stream.getvalue()).decode()})
        first,second=library(self.root),library(self.root)
        first.offer_uploads();second.offer_uploads()
        self.assertEqual(first.queue.qsize(),1);self.assertEqual(second.queue.qsize(),0)

    def test_untrusted_message_cannot_read_or_remove_local_upload_paths(self):
        async def run():
            path=self.root/'keep.png';path.write_bytes(b'keep');manifest=self.root/'keep.json';manifest.write_text('{}')
            lib=library(self.root);lib.offer_candidate({'file':'forged','_local_path':str(path),'_upload_manifest':str(manifest)})
            worker=asyncio.create_task(lib.collector(None))
            try:await asyncio.wait_for(lib.queue.join(),2)
            finally:worker.cancel();await asyncio.gather(worker,return_exceptions=True)
            self.assertEqual(path.read_bytes(),b'keep');self.assertTrue(manifest.exists());self.assertFalse(lib.items)
        asyncio.run(run())

    def test_pending_checkpoint_excludes_ephemeral_source_and_role_facts(self):
        from bot import checkpoint_messages
        message={'_id':'queued','_text':'看看这个','_source_evidence':{'body':'ephemeral'},
            '_source_url':'https://example.com','_role_analysis':{'context':{}},'_cached_vision':{'description':'ephemeral'}}
        checkpoint_messages(self.root/'runtime/pending-messages.json',[message])
        data=json.loads((self.root/'runtime/pending-messages.json').read_text(encoding='utf-8'))
        self.assertNotIn('_source_evidence',json.dumps(data));self.assertNotIn('_role_analysis',json.dumps(data))


if __name__=='__main__':unittest.main()
