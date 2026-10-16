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
from unittest.mock import AsyncMock, Mock, patch

import httpx
from PIL import Image
import bot
from console_config import atomic, Config
from debug_console import Console, Supervisor
from debug_webui import DebugWebUI
from image_archive import ImageArchive
from image_memory import ImageMemory
from sticker_prefill import StickerPrefill, start

VALUES={'label':'妈妈猫','description':'白发猫耳角色撒娇，旁边写着妈妈','emotion':'撒娇','use_when':'撒娇或接妈妈梗时'}


def raw_image():
    output=io.BytesIO();first=Image.new('RGB',(24,32),'red')
    first.save(output,format='GIF',save_all=True,append_images=[Image.new('RGB',(24,32),'blue')],duration=100,loop=0)
    return output.getvalue()


class StickerPrefillTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        cfg=json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100,200],enabled=True)
        atomic(self.root/'settings.json',cfg)
        self.folder=self.root/'runtime';self.folder.mkdir()
        atomic(self.folder/'state.json',{'online':True,'pid':os.getpid()})
        self.queue=StickerPrefill(self.root,100);self.queue.mark_ready(os.getpid());self.raw=raw_image()
        self.cfg=Config(self.root,100).load()
    def tearDown(self):self.temp.cleanup()

    def test_success_only_returns_four_form_fields_without_admitting_sending_or_changing_summary(self):
        row=self.queue.enqueue(self.raw,'a'*32);reserve=Mock(return_value=True)
        async def model(cfg,prompt,spec,images):
            self.assertEqual(set(json.loads(spec.read_text(encoding='utf-8'))['required']),set(VALUES))
            self.assertNotIn('_developer_instructions',cfg)
            self.assertEqual(cfg['model'],'vision-model')
            with Image.open(images[0]) as image:self.assertEqual(image.width,1024)
            self.assertIn('不发送消息',prompt);return VALUES
        cfg={**self.cfg,'role_models':{'vision':{'model':'vision-model','reasoning_effort':'low'}},'_developer_instructions':'should not reach this form'}
        asyncio.run(self.queue.process_next(cfg,model,reserve))
        result=self.queue.get(row['id']);self.assertEqual(result['status'],'done');self.assertEqual(result['values'],VALUES)
        reserve.assert_called_once();self.assertFalse(list(self.queue.folder.glob('*.img')))
        self.assertFalse((self.root/'shared-emoji-library').exists())
        self.assertFalse((self.folder/'image-memory.json').exists());self.assertFalse((self.folder/'recent-chat.jsonl').exists())
        self.assertFalse((self.folder/'image-summary-edits.json').exists())

    def test_same_request_is_idempotent_and_second_pending_job_is_rejected(self):
        one=self.queue.enqueue(self.raw,'a'*32)
        self.assertEqual(self.queue.enqueue(self.raw,'a'*32),one)
        with self.assertRaisesRegex(ValueError,'已有'):self.queue.enqueue(self.raw,'b'*32)
        model=AsyncMock(return_value=VALUES)
        asyncio.run(self.queue.process_next(self.cfg,model,lambda:True))
        asyncio.run(self.queue.process_next(self.cfg,model,lambda:True))
        self.assertEqual(model.await_count,1)

    def test_cancel_before_start_does_not_call_model(self):
        row=self.queue.enqueue(self.raw);self.queue.cancel(row['id']);model=AsyncMock()
        asyncio.run(self.queue.process_next(self.cfg,model,lambda:True))
        model.assert_not_called();self.assertEqual(self.queue.get(row['id'])['status'],'cancelled')
        self.assertFalse(list(self.queue.folder.glob('*.img')))

    def test_cancel_during_model_discards_late_result(self):
        async def run():
            row=self.queue.enqueue(self.raw);entered=asyncio.Event();finish=asyncio.Event()
            async def model(*args):entered.set();await finish.wait();return VALUES
            task=asyncio.create_task(self.queue.process_next(self.cfg,model,lambda:True))
            await entered.wait();self.queue.cancel(row['id']);finish.set();await task
            result=self.queue.get(row['id']);self.assertEqual(result['status'],'cancelled');self.assertNotIn('values',result)
        asyncio.run(run())

    def test_quota_invalid_output_and_expired_jobs_never_approve(self):
        for mode in ['quota','invalid','expired']:
            with self.subTest(mode=mode):
                row=self.queue.enqueue(self.raw)
                if mode=='expired':row['created']=time.time()-181;self.queue.write(row)
                model=AsyncMock(return_value={**VALUES,'description':'x'*36})
                asyncio.run(self.queue.process_next(self.cfg,model,lambda:mode!='quota'))
                self.assertEqual(self.queue.get(row['id'])['status'],'failed')
                self.assertEqual(model.await_count,1 if mode=='invalid' else 0)
                self.assertFalse(list(self.queue.folder.glob('*.img')))
        self.assertFalse((self.root/'shared-emoji-library').exists())

    def test_restart_fails_interrupted_job_without_retrying_model(self):
        row=self.queue.enqueue(self.raw);row['status']='running';self.queue.write(row)
        self.queue.mark_ready(os.getpid())
        self.assertEqual(self.queue.get(row['id'])['status'],'failed');self.assertFalse(self.queue.pending())

    def test_offline_old_worker_bad_upload_or_cross_group_rejected(self):
        (self.folder/'sticker-prefill-worker.json').unlink()
        with self.assertRaises(ValueError):self.queue.enqueue(self.raw)
        self.queue.mark_ready(os.getpid());atomic(self.folder/'state.json',{'online':False,'pid':os.getpid()})
        with self.assertRaises(ValueError):self.queue.enqueue(self.raw)
        with self.assertRaises(ValueError):StickerPrefill(self.root,999)
        atomic(self.folder/'state.json',{'online':True,'pid':os.getpid()})
        with self.assertRaises(ValueError):start(self.root,100,{'request_id':'a'*32,'source':'upload','image_base64':'invalid!'})

    def test_chat_source_is_message_bound_and_does_not_admit_or_write_manual_summary(self):
        key=hashlib.sha256(b'source').hexdigest();digest=ImageArchive(self.folder).put(self.raw,key)
        memory=ImageMemory(self.folder/'image-memory.json');memory.observe('123',[key]);memory.bind(key,digest)
        result=start(self.root,100,{'request_id':'b'*32,'source':'message','message_id':'123','key':key})
        self.assertEqual(result['status'],'queued')
        self.assertFalse((self.root/'shared-emoji-library').exists());self.assertFalse((self.folder/'image-summary-edits.json').exists())

    def test_authorized_http_job_status_is_separate_from_save_action(self):
        console=Console(self.root,Supervisor(self.root,start=False));server=DebugWebUI(self.root,console.web_action);server.start()
        try:
            with httpx.Client(base_url=server.base_url,trust_env=False) as client:
                body={'action':'sticker_prefill','group':100,'source':'upload','request_id':'c'*32,'image_base64':base64.b64encode(self.raw).decode()}
                self.assertEqual(client.post('/api/action',json=body).status_code,401)
                headers={'X-QQ-Debug-Key':server.token}
                response=client.post('/api/action',json=body,headers=headers)
                self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['job']['status'],'queued')
                model=AsyncMock(return_value=VALUES);asyncio.run(self.queue.process_next(self.cfg,model,lambda:True))
                response=client.get('/api/sticker-prefill?group=100&id='+'c'*32,headers=headers)
                self.assertEqual(response.json()['values'],VALUES)
                self.assertEqual(client.get('/api/sticker-prefill?group=200&id='+'c'*32,headers=headers).status_code,400)
                self.assertFalse((self.root/'shared-emoji-library').exists())
        finally:server.close()

    def test_real_bot_loop_uses_existing_model_quota_but_never_sends_or_admits(self):
        cfg={**self.cfg,'poll_seconds':.01,'mcp_env_file':str(self.root/'empty.env'),
             'shared_sticker_library':False,'proactive_topics_enabled':False}
        (self.root/'empty.env').write_text('NAPCAT_HOST=http://invalid.test\n')
        calls=[];submitted=[]
        async def api(client,env,action,params=None):
            calls.append(action)
            if action=='get_group_msg_history':
                if not submitted and (self.folder/'manual-worker.json').exists():
                    submitted.append(StickerPrefill(self.root,100).enqueue(self.raw,'d'*32))
                return {'messages':[]}
            if action=='get_group_member_list':return []
            return {}
        async def model(*args):
            (self.folder/'STOP').touch();return VALUES
        with patch.object(bot,'ROOT',self.root),patch.object(bot,'RUNTIME',self.folder),patch.object(bot,'settings',return_value=cfg),\
             patch.object(bot,'api',api),patch.object(bot,'generate_json',new=AsyncMock(side_effect=model)) as generated,\
             patch.object(bot,'wait_for_qq',new=AsyncMock(return_value=({'user_id':169901502,'nickname':'yonagi'},{'group_name':'测试群'}))):
            asyncio.run(asyncio.wait_for(bot._run(),3))
        self.assertEqual(generated.await_count,1)
        self.assertNotIn('send_group_msg',calls)
        self.assertEqual(self.queue.get('d'*32)['status'],'done')
        self.assertEqual(json.loads((self.folder/'state.json').read_text(encoding='utf-8'))['model_calls_today'],1)
        self.assertFalse((self.root/'shared-emoji-library').exists())


if __name__=='__main__':unittest.main()
