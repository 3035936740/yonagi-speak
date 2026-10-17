import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import AsyncMock,patch
import httpx
from PIL import Image
import bot
from console_config import atomic,Config
from debug_console import Console,Supervisor
from debug_images import original
from debug_webui import DebugWebUI
from image_archive import ImageArchive
from image_memory import ImageMemory
from manual_images import MAX_IMAGE_BYTES,segments
from recent_chat_journal import RecentChatJournal
import test_manual_outbox as manual_tests


def picture(color='red',gif=False):
    output=io.BytesIO();image=Image.new('RGB',(24,32),color)
    if gif:image.save(output,format='GIF',save_all=True,append_images=[Image.new('RGB',(24,32),'blue')],duration=120,loop=0)
    else:image.save(output,format='PNG')
    return output.getvalue()


def attachment(raw,slot=1):return {'slot':slot,'data':base64.b64encode(raw).decode('ascii')}


class ManualImageTests(unittest.TestCase):
    setUp=manual_tests.ManualSendTests.setUp
    def record(self,row):
        item={'sender':'yonagi','text':row.get('display_text',row['text']),'is_bot':True,'_manual':True,
            '_uid':'169901502','_id':row['message_id'],'_time':row['sent_at'],
            '_image_count':len(row.get('image_hashes',[])),'_image_hashes':row.get('image_hashes',[])}
        self.memory.record_manual(item,row['id']);self.journal.append(item)
        if not any(old['_id']==item['_id'] for old in self.history):self.history.append(item)

    def test_ordered_mixed_segments_are_one_send_and_originals_persist(self):
        first=picture();second=picture('green',True)
        row=self.box.enqueue('前[图片2]中[图片1]后','a'*32,[attachment(first),attachment(second,2)])
        api=AsyncMock(return_value={'message_id':600})
        asyncio.run(self.box.drain(api,None,{},self.record,self.cfg));asyncio.run(self.box.drain(api,None,{},self.record,self.cfg))
        self.assertEqual(api.await_count,1)
        sent=api.call_args.args[3]['message'];self.assertEqual([part['type'] for part in sent],['text','image','text','image','text'])
        self.assertEqual(sent[0]['data']['text'],'前');self.assertEqual(sent[-1]['data']['text'],'后')
        self.assertEqual(base64.b64decode(sent[1]['data']['file'].split('base64://')[1]),second)
        self.assertEqual(base64.b64decode(sent[3]['data']['file'].split('base64://')[1]),first)
        result=self.box.get(row['id']);self.assertEqual(result['status'],'sent');self.assertEqual(result['display_text'],'前[图片]中[图片]后')
        self.assertEqual(len(self.history),1);self.assertEqual(self.memory.stats()['manual_messages'],1)
        cached=original(self.root,100,'600',result['image_hashes'][0]);self.assertEqual(cached.read_bytes(),second)
        with Image.open(cached) as image:self.assertEqual(image.n_frames,2)
        self.assertEqual(self.journal.records[0]['_image_count'],2)
        self.assertFalse(list((self.box.folder/'assets'/row['id']).glob('*')))
        self.assertEqual(self.box.enqueue('前[图片2]中[图片1]后','a'*32,[attachment(first),attachment(second,2)])['status'],'sent')
        with self.assertRaises(ValueError):self.box.enqueue('前[图片2]中[图片1]后','a'*32,[attachment(picture('black')),attachment(second,2)])

    def test_image_only_plain_marker_and_missing_marker_are_supported(self):
        for text in ('','[图片]','文字'):
            row=self.box.enqueue(text,images=[attachment(picture())]);message=segments(self.box.folder,row)
            self.assertEqual(message[-1]['type'],'image')
            if text=='文字':self.assertEqual(message[0]['data']['text'],'文字')
        with self.assertRaises(ValueError):self.box.enqueue('[图片1][图片1]',images=[attachment(picture())])

    def test_invalid_images_counts_sizes_and_old_worker_are_rejected(self):
        invalid=[attachment(b'not-an-image'),{'slot':1,'data':'%%%'}, {'slot':True,'data':attachment(picture())['data']},
            {'slot':1,'data':base64.b64encode(b'x'*(MAX_IMAGE_BYTES+1)).decode()}]
        for entry in invalid:
            with self.subTest(entry=str(entry)[:50]),self.assertRaises(ValueError):self.box.enqueue('',images=[entry])
        for images in [[attachment(picture())]*2,[attachment(picture(),n) for n in range(1,4)]]:
            with self.assertRaises(ValueError):self.box.enqueue('',images=images)
        atomic(self.box.folder.parent/'manual-worker.json',{'pid':333,'version':1})
        with self.assertRaisesRegex(ValueError,'尚未加载图片'):self.box.enqueue('',images=[attachment(picture())])
        self.assertEqual(self.box.enqueue('旧版仍可发文字')['status'],'queued')

    def test_missing_or_changed_attachment_never_calls_qq(self):
        row=self.box.enqueue('文字',images=[attachment(picture())]);path=self.box.folder/'assets'/row['id']/row['images'][0]['file']
        path.write_bytes(picture('green'));api=AsyncMock()
        asyncio.run(self.box.drain(api,None,{},self.record,self.cfg));api.assert_not_called()
        self.assertEqual(self.box.get(row['id'])['status'],'failed')
        self.assertEqual(self.memory.stats()['manual_messages'],0)

    def test_record_retry_and_unknown_send_do_not_duplicate_images(self):
        row=self.box.enqueue('图',images=[attachment(picture())]);api=AsyncMock(return_value={'message_id':601})
        asyncio.run(self.box.drain(api,None,{},lambda row:(_ for _ in ()).throw(OSError()),self.cfg))
        self.assertEqual(self.box.get(row['id'])['status'],'record_pending')
        asyncio.run(self.box.drain(api,None,{},self.record,self.cfg));self.assertEqual(api.await_count,1)
        second=self.box.enqueue('',images=[attachment(picture())]);failed=AsyncMock(side_effect=TimeoutError())
        asyncio.run(self.box.drain(failed,None,{},self.record,self.cfg));asyncio.run(self.box.drain(failed,None,{},self.record,self.cfg))
        self.assertEqual(failed.await_count,1);self.assertEqual(self.box.get(second['id'])['status'],'uncertain')

    def test_authenticated_http_accepts_images_and_scopes_them_to_group(self):
        console=Console(self.root,Supervisor(self.root,start=False));server=DebugWebUI(self.root,console.web_action)
        server.start();self.addCleanup(server.close)
        with httpx.Client(base_url=server.base_url,trust_env=False) as client:
            value={'action':'manual_send','group':100,'text':'网页[图片1]','request_id':'b'*32,'images':[attachment(picture())]}
            self.assertEqual(client.post('/api/action',json=value).status_code,401)
            headers={'X-QQ-Debug-Key':server.token,'Origin':server.base_url.rstrip('/')}
            response=client.post('/api/action',json=value,headers=headers);self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['request']['status'],'queued')
            self.assertEqual(len(response.json()['request']['images']),1)
            self.assertEqual(client.post('/api/action',json={**value,'group':300},headers=headers).status_code,400)
            snapshot=client.get('/api/snapshot?group=100',headers=headers).json()
            self.assertTrue(snapshot['status']['manual_images_ready']);self.assertIn('manual_images',snapshot['features'])

    def test_large_image_http_body_and_total_attachment_limit(self):
        output=io.BytesIO();Image.frombytes('RGB',(1200,1200),os.urandom(1200*1200*3)).save(output,format='PNG')
        raw=output.getvalue();self.assertGreater(len(raw),3*1024*1024);self.assertLess(len(raw),MAX_IMAGE_BYTES)
        with self.assertRaisesRegex(ValueError,'合计上限8MB'):
            self.box.enqueue('',images=[attachment(raw),attachment(raw,2)])
        console=Console(self.root,Supervisor(self.root,start=False));server=DebugWebUI(self.root,console.web_action)
        server.start();self.addCleanup(server.close)
        with httpx.Client(base_url=server.base_url,trust_env=False,timeout=10) as client:
            headers={'X-QQ-Debug-Key':server.token,'Origin':server.base_url.rstrip('/')}
            value={'action':'manual_send','group':100,'text':'大图[图片1]','images':[attachment(raw)]}
            response=client.post('/api/action',json=value,headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            value['action']='command';value['command']='/status'
            self.assertEqual(client.post('/api/action',json=value,headers=headers).status_code,413)

    def test_real_worker_records_image_memory_and_archive_without_model_calls(self):
        raw=picture('red',True);row=self.box.enqueue('你好[图片1]',images=[attachment(raw)])
        cfg={**self.cfg,'poll_seconds':.01,'mcp_env_file':str(self.root/'empty.env'),'shared_sticker_library':False,'proactive_topics_enabled':False}
        (self.root/'empty.env').write_text('NAPCAT_HOST=http://invalid.test\nNAPCAT_TOKEN=test\n')
        sent=[]
        async def api(client,env,action,params=None):
            if action=='send_group_msg':sent.append(params);(self.root/'runtime/STOP').touch();return {'message_id':602}
            if action=='get_group_msg_history':return {'messages':[]}
            if action=='get_group_member_list':return []
            return {}
        with patch.object(bot,'ROOT',self.root),patch.object(bot,'RUNTIME',self.root/'runtime'),patch.object(bot,'settings',return_value=cfg),\
            patch.object(bot,'api',api),patch.object(bot,'wait_for_qq',new=AsyncMock(return_value=({'user_id':169901502,'nickname':'yonagi'},{'group_name':'测试群'}))),\
            patch.object(bot,'generate_json',new=AsyncMock()) as model:
            asyncio.run(asyncio.wait_for(bot._run(),3));model.assert_not_called()
        self.assertEqual(len(sent),1);self.assertEqual([part['type'] for part in sent[0]['message']],['text','image'])
        item=RecentChatJournal(self.journal.path).load()[0];self.assertEqual(item['text'],'你好[图片]')
        cache=ImageMemory(self.root/'runtime/image-memory.json');key=item['_image_hashes'][0]
        self.assertIn(key,cache.data['sources'])
        archive=ImageArchive(self.root/'runtime');archive.prune(set(cache.data['sources']))
        self.assertEqual(archive.get(archive.digest(key)).read_bytes(),raw)
        self.assertEqual(self.memory.db.execute("SELECT text FROM manual_messages WHERE id='602'").fetchone()[0],'你好[图片]')
        self.assertEqual(self.box.get(row['id'])['status'],'sent')


if __name__=='__main__':unittest.main()
