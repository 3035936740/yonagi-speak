import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
from console_config import Config, atomic
from config_metadata import metadata
from debug_console import Console, Supervisor
from debug_images import image_bytes
from debug_references import enrich_images, message_metadata
from debug_webui import DebugWebUI
from image_archive import message_image_keys


class ChatDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        cfg=json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100,200],enabled=False)
        atomic(self.root/'settings.json',cfg)
        self.folder=self.root/'runtime';self.folder.mkdir()
        (self.root/'persona.txt').write_text('yonagi',encoding='utf-8')
        self.validation=patch('console_config.validate_model');self.validation.start()
    def tearDown(self):self.validation.stop();self.temp.cleanup()

    def test_default_global_group_inheritance_and_restart_persistence(self):
        self.assertEqual(Config(self.root,100).load()['chat_image_display'],'image')
        Config(self.root).set('chat_image_display','image')
        self.assertEqual(Config(self.root,100).load()['chat_image_display'],'image')
        Config(self.root,100).set('chat_image_display','text')
        self.assertEqual(Config(self.root,100).load()['chat_image_display'],'text')
        self.assertEqual(Config(self.root,200).load()['chat_image_display'],'image')
        self.assertEqual(json.loads((self.root/'settings.json').read_text(encoding='utf-8'))['chat_image_display'],'image')
        Config(self.root,100).reset('chat_image_display')
        self.assertEqual(Config(self.root,100).load()['chat_image_display'],'image')

    def test_invalid_display_mode_is_not_saved(self):
        for value in ['picture','',True,42,{}]:
            cfg=Config(self.root).load();cfg['chat_image_display']=value
            with self.subTest(value=value),self.assertRaises(ValueError):Config(self.root).save(cfg)
        self.assertEqual(Config(self.root).load()['chat_image_display'],'image')
        self.assertEqual(set(metadata(Config(self.root).load())['chat_image_display']['options']),{'text','image'})

    def test_snapshot_exposes_effective_mode_and_leaves_chat_memory_text_unchanged(self):
        row={'group_id':100,'kind':'received','message_id':'123','text':'hello [图片摘要：猫猫] world','image_count':1}
        path=self.folder/'debug-events.jsonl';path.write_text(json.dumps(row,ensure_ascii=False)+'\n',encoding='utf-8')
        before=path.read_bytes();console=Console(self.root,Supervisor(self.root,start=False));server=DebugWebUI(self.root,console.web_action)
        Config(self.root).set('chat_image_display','image');Config(self.root,100).set('chat_image_display','text')
        first=server.snapshot({'group':['100']});second=server.snapshot({'group':['200']})
        self.assertEqual(first['status']['chat_image_display'],'text');self.assertEqual(second['status']['chat_image_display'],'image')
        self.assertEqual(first['events'][0]['text'],row['text']);self.assertEqual(path.read_bytes(),before)
        self.assertTrue(first['events'][0]['image_info'])
        self.assertFalse((self.folder/'image-memory.json').exists())

    def test_new_sent_image_is_previewable_from_its_verified_qq_message_slot(self):
        row={'group_id':100,'kind':'sent','message_id':'559','text':'[已发送图片或表情]','image_count':1}
        (self.folder/'debug-events.jsonl').write_text(json.dumps(row)+'\n',encoding='utf-8')
        info=enrich_images([row],self.folder,self.root/'shared-emoji-library',Config(self.root).load())[0]['image_info'][0]
        self.assertEqual(info['key'],message_image_keys(100,'559',1)[0])
        output=io.BytesIO();Image.new('RGB',(10,10),'red').save(output,format='GIF');raw=output.getvalue()
        def respond(request):
            if request.method=='POST':return httpx.Response(200,json={'retcode':0,'data':{'group_id':100,'message':[{'type':'image','data':{'url':'https://gchat.qpic.cn/sent'}}]}})
            return httpx.Response(200,content=raw)
        client=httpx.Client(transport=httpx.MockTransport(respond),trust_env=False)
        with patch('debug_images.dotenv_values',return_value={'NAPCAT_HOST':'http://127.0.0.1:3000'}),patch('debug_images.httpx.Client',return_value=client):
            received,mime=image_bytes(self.root,100,'559',info['key'])
        self.assertEqual(received,raw);self.assertEqual(mime,'image/gif')
        with self.assertRaises(ValueError):image_bytes(self.root,200,'559',info['key'])

    def test_qq_builtin_face_and_quoted_picture_do_not_become_wrong_image_attachments(self):
        row={'kind':'received','message_id':'123','text':'😎','quote':{'found':True,'text':'[图片摘要：引用的图片]'}}
        data=enrich_images([row],self.folder,self.root/'shared-emoji-library',Config(self.root).load())[0]
        self.assertNotIn('image_info',data)
        self.assertNotIn('image_count',message_metadata({'_uid':'1','text':'😎'}))
        self.assertEqual(message_metadata({'_uid':'1','_image_count':1})['image_count'],1)


if __name__=='__main__':unittest.main()
