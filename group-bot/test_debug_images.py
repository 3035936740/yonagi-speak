import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
import bot
from console_config import Config, atomic, runtime
from debug_console import Console, Supervisor
from debug_images import image_bytes, mutate, original
from debug_references import enrich_images
from debug_store import library, sticker_mutate, sticker_image
from debug_webui import DebugWebUI
from image_archive import ImageArchive, SummaryOverrides
from image_memory import ImageMemory, source_key
from sticker_filter import approved


FACTS={'description':'一张普通图片','visible_text':'','confidence':.8,'uncertainty':''}
EDIT={**FACTS,'description':'猫猫说妈妈','visible_text':'妈妈','confidence':1}


def picture(color='red', animated=False):
    output=io.BytesIO();first=Image.new('RGB',(24,32),color)
    if animated:first.save(output,format='GIF',save_all=True,append_images=[Image.new('RGB',(24,32),'blue')],duration=100,loop=0)
    else:first.save(output,format='PNG')
    return output.getvalue()


class DebugImageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        cfg=json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100,200],enabled=False)
        atomic(self.root/'settings.json',cfg)
        self.folder=runtime(self.root,100);self.folder.mkdir()
        self.key=hashlib.sha256(b'first-source').hexdigest();self.other=hashlib.sha256(b'second-source').hexdigest()
        self.raw=picture(animated=True)
        self.archive=ImageArchive(self.folder);self.digest=self.archive.put(self.raw,self.key)
        self.memory=ImageMemory(self.folder/'image-memory.json')
        self.memory.observe('123',[self.key]);self.memory.store(self.key,self.digest,FACTS)
        self.row={'kind':'received','message_id':'123','text':'[图片摘要：一张普通图片]','image_hashes':[self.key]}
        (self.folder/'debug-events.jsonl').write_text(json.dumps(self.row,ensure_ascii=False)+'\n',encoding='utf-8')
    def tearDown(self):self.temp.cleanup()

    def test_original_returns_identical_animation_with_authenticated_message_binding(self):
        raw,mime=image_bytes(self.root,100,'123',self.key)
        self.assertEqual(raw,self.raw);self.assertEqual(mime,'image/gif')
        with Image.open(io.BytesIO(raw)) as image:self.assertEqual(image.n_frames,2)
        for group,mid,key in [(999,'123',self.key),(200,'123',self.key),(100,'999',self.key),(100,'123',self.other),(100,'123','../secret')]:
            with self.subTest(group=group,mid=mid,key=key),self.assertRaises(ValueError):image_bytes(self.root,group,mid,key)

    def test_summary_edit_survives_live_auto_write_and_restart_without_renewing_cache_ttl(self):
        seen=self.memory.data['entries'][self.digest]['last_seen'];sequence=self.memory.data['sequence']
        result=mutate(self.root,100,'summary','123',self.key,{'summary':EDIT,'expected_revision':''})
        self.assertTrue(result['ok'])
        self.memory.store(self.key,self.digest,FACTS) # existing live instance writes its old snapshot
        self.assertEqual(self.memory.lookup(key=self.key),EDIT)
        self.assertEqual(ImageMemory(self.folder/'image-memory.json').lookup(key=self.key),EDIT)
        self.assertEqual(self.memory.data['sequence'],sequence)
        self.assertEqual(self.memory.data['entries'][self.digest]['last_seen'],seen)
        records=enrich_images([self.row],self.folder,self.root/'shared-emoji-library',Config(self.root,100).load())
        self.assertIn('猫猫说妈妈',records[0]['text'])
        self.assertEqual(records[0]['image_info'][0]['summary_origin'],'operator')
        self.assertEqual(records[0]['image_info'][0]['summary_revision'],result['revision'])
        self.assertEqual(len(list((self.folder/'image-edit-backups').glob('*.json'))),1)

    def test_correction_updates_live_context_preserving_image_order_and_nonimage_text(self):
        raw=picture('green');digest=self.archive.put(raw,self.other)
        self.memory.observe('124',[self.key,self.other]);self.memory.store(self.other,digest,FACTS)
        SummaryOverrides(self.folder).set(self.other,digest,EDIT)
        message={'_id':'124','_image_hashes':[self.key,self.other],'text':'看看 [图片摘要：第一张] 然后 [图片摘要：第二张] 尾部'}
        changed=self.memory.correct_messages([message])
        self.assertEqual(changed,[message]);self.assertEqual(message['text'],'看看 [图片摘要：一张普通图片] 然后 [图片摘要：猫猫说妈妈] 尾部')
        self.assertFalse(self.memory.correct_messages([message]))

    def test_summary_edits_reject_stale_revision_invalid_fields_and_removed_messages(self):
        mutate(self.root,100,'summary','123',self.key,{'summary':EDIT,'expected_revision':''})
        with self.assertRaisesRegex(ValueError,'其他页面'):mutate(self.root,100,'summary','123',self.key,{'summary':FACTS,'expected_revision':''})
        with self.assertRaises(ValueError):mutate(self.root,100,'summary','123',self.key,{'summary':{**EDIT,'description':'x'*121}})
        atomic(self.folder/'deleted-messages.json',['123'])
        with self.assertRaisesRegex(ValueError,'本地删除'):image_bytes(self.root,100,'123',self.key)

    def test_manual_admission_overrides_rejection_and_preserves_original_after_archive_prune(self):
        lib=library(self.root);identity='s'+self.digest[:16]
        with lib.transaction():lib.hashes[self.digest]=identity;lib.reject(identity,'uncertain');lib.save()
        result=mutate(self.root,100,'collect','123',self.key,{'label':'妈妈猫','description':'猫猫说妈妈','emotion':'撒娇','use_when':'撒娇时'})
        loaded=library(self.root);item=loaded.items[result['id']]
        self.assertTrue(approved(item));self.assertTrue(item['manual_admission']);self.assertNotIn(identity,loaded.rejected)
        self.assertFalse(Path(item['path']).is_absolute())
        path=self.archive.get(self.digest);os.utime(path,(time.time()-600,time.time()-600));self.archive.prune(set())
        self.assertFalse(path.exists())
        raw,mime=sticker_image(self.root,identity);self.assertEqual(raw,self.raw)
        with Image.open(io.BytesIO(raw)) as image:self.assertEqual(image.n_frames,2)

    def test_upload_supports_explicit_manual_admission_without_classifier(self):
        result=sticker_mutate(self.root,'create',values={'image_base64':base64.b64encode(self.raw).decode(),
            'admission':'manual','label':'手动表情','emotion':'开心','description':'猫猫','use_when':'打招呼'})
        item=library(self.root).items[result['id']]
        self.assertTrue(approved(item));self.assertFalse(list((self.root/'shared-emoji-library/incoming').glob('*.json')))
        self.assertEqual(sticker_image(self.root,result['id'])[0],self.raw)

    def test_lazy_original_fetch_uses_exact_message_component_and_never_another_group(self):
        self.archive.get(self.digest).unlink()
        for returned_group,expected_ok in [(200,False),(100,True)]:
            calls=[]
            def respond(request):
                calls.append(str(request.url))
                if request.method=='POST':return httpx.Response(200,json={'retcode':0,'data':{'group_id':returned_group,
                    'message':[{'type':'image','data':{'url':'https://gchat.qpic.cn/correct'}}]}})
                return httpx.Response(200,content=self.raw)
            client=httpx.Client(transport=httpx.MockTransport(respond),trust_env=False)
            with patch('debug_images.dotenv_values',return_value={'NAPCAT_HOST':'http://127.0.0.1:3000'}),patch('debug_images.httpx.Client',return_value=client):
                if expected_ok:self.assertEqual(original(self.root,100,'123',self.key).read_bytes(),self.raw)
                else:
                    with self.assertRaisesRegex(ValueError,'不属于'):original(self.root,100,'123',self.key)
            self.assertEqual(len(calls),2 if expected_ok else 1)

    def test_mismatched_refetched_original_does_not_overwrite_expected_hash(self):
        self.archive.get(self.digest).unlink()
        def respond(request):
            if request.method=='POST':return httpx.Response(200,json={'retcode':0,'data':{'group_id':100,'message':[{'type':'image','data':{'url':'https://gchat.qpic.cn/wrong'}}]}})
            return httpx.Response(200,content=picture('green'))
        client=httpx.Client(transport=httpx.MockTransport(respond),trust_env=False)
        with patch('debug_images.dotenv_values',return_value={'NAPCAT_HOST':'http://127.0.0.1:3000'}),patch('debug_images.httpx.Client',return_value=client):
            with self.assertRaisesRegex(ValueError,'哈希不一致'):original(self.root,100,'123',self.key)
        self.assertEqual(self.archive.digest(self.key),self.digest)

    def test_missing_first_image_url_does_not_shift_the_requested_second_image(self):
        data={'file':'second.png','url':'https://gchat.qpic.cn/second'};key=source_key(data)
        self.memory.observe('125',[key]);calls=[]
        def respond(request):
            calls.append(str(request.url))
            if request.method=='POST':return httpx.Response(200,json={'retcode':0,'data':{'group_id':100,'message':[
                {'type':'image','data':{'file':'missing.png','url':''}},{'type':'image','data':data}]}})
            return httpx.Response(200,content=self.raw)
        client=httpx.Client(transport=httpx.MockTransport(respond),trust_env=False)
        with patch('debug_images.dotenv_values',return_value={'NAPCAT_HOST':'http://127.0.0.1:3000'}),patch('debug_images.httpx.Client',return_value=client):
            self.assertEqual(original(self.root,100,'125',key).read_bytes(),self.raw)
        self.assertEqual(calls[-1],data['url'])

    def test_download_pipeline_archives_original_bytes_before_vision_conversion(self):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=self.raw))) as client:
                key=hashlib.sha256(b'new-source').hexdigest()
                path=self.folder/'vision.jpg'
                await bot.download_image(client,'https://gchat.qpic.cn/raw',path,{'_image_archive':self.archive,'_image_archive_key':key})
                self.assertEqual(self.archive.get(self.archive.digest(key)).read_bytes(),self.raw)
                with Image.open(path) as image:self.assertEqual(image.format,'JPEG')
        asyncio.run(run())

    def test_authenticated_http_original_and_summary_actions_and_stop_alias(self):
        console=Console(self.root,Supervisor(self.root,start=False))
        server=DebugWebUI(self.root,console.web_action);server.start()
        try:
            with httpx.Client(base_url=server.base_url,trust_env=False) as client:
                query={'group':100,'message_id':'123','key':self.key}
                self.assertEqual(client.get('/api/message-image',params=query).status_code,401)
                headers={'X-QQ-Debug-Key':server.token}
                response=client.get('/api/message-image',params=query,headers=headers)
                self.assertEqual(response.status_code,200);self.assertEqual(response.content,self.raw)
                response=client.post('/api/action',headers=headers,json={'action':'image','mode':'summary',**query,'values':{'summary':EDIT}})
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn('image_management',client.get('/api/snapshot?group=100',headers=headers).json()['features'])
                result=console.web_action({'action':'command','group':100,'command':'/stop'})
                self.assertTrue(result['shutdown']);self.assertFalse(console.running)
        finally:server.close()


if __name__=='__main__':unittest.main()
