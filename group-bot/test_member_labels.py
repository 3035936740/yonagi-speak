import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from console_config import atomic,runtime
from debug_console import Console,Supervisor
from debug_webui import DebugWebUI
from group_identity import GroupIdentities,identity_facts,resolve_names
from member_labels import MemberLabels,view
import httpx


class MemberLabelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        cfg=json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100,200]);atomic(self.root/'settings.json',cfg)
        self.folder=runtime(self.root,100);self.store=MemberLabels(self.folder)
    def response(self,index,command='/pgr',anchored=False):
        now=time.time()+index*30
        request={'_id':'cmd'+str(index),'_uid':'11111','_time':now,'text':command,'is_bot':False}
        response={'_id':'resp'+str(index),'_uid':'22222','_time':now+2,'text':'[图片]' if command=='/pgr' else '响应指令',
            '_images':['image'] if command=='/pgr' else [],'_reply_id':request['_id'] if anchored else ''}
        return request,response
    def test_repeated_command_responses_mark_bot_and_deduplicate_same_request(self):
        request,response=self.response(0)
        self.assertFalse(self.store.observe(response,[request],{}))
        for n in range(4):self.store.observe({**response,'_id':'duplicate'+str(n)},[request],{})
        self.assertEqual(self.store.facts()['22222']['observations'],1)
        for n in range(1,3):
            request,response=self.response(n);changed=self.store.observe(response,[request],{})
        self.assertTrue(changed);self.assertEqual(self.store.facts()['22222']['label'],'bot')
        self.assertEqual(self.store.facts()['22222']['source'],'auto')
    def test_a_text_requires_quote_or_at_anchor(self):
        request,response=self.response(0,'/a hi')
        self.store.observe(response,[request],{});self.assertEqual(self.store.facts(),{})
        for n in range(3):
            request,response=self.response(n,'/a hi',True);self.store.observe(response,[request],{})
        self.assertEqual(self.store.facts()['22222']['label'],'bot')
    def test_command_sender_and_ambiguous_images_are_not_marked(self):
        request,response=self.response(0)
        self.store.observe(request,[],{})
        self.store.observe({**response,'_uid':'11111'},[request],{})
        self.store.observe(response,[request,{**request,'_id':'other','_uid':'33333'}],{})
        self.assertEqual(self.store.facts(),{})
    def test_manual_human_wins_over_auto_bot_and_is_group_scoped(self):
        self.store.mark('22222','human','人工核实',['千柒','aaaa'])
        for n in range(3):
            request,response=self.response(n);self.store.observe(response,[request],{})
        fact=MemberLabels(self.folder).facts()['22222']
        self.assertEqual(fact['label'],'human');self.assertEqual(fact['auto_label'],'bot')
        self.assertEqual(fact['aliases'],['千柒','aaaa'])
        self.assertEqual(MemberLabels(runtime(self.root,200)).facts(),{})
        self.assertEqual(self.store.mark('22222','auto')['label'],'bot')
        self.assertEqual(self.store.mark('22222','clear')['label'],'')
        self.assertEqual(self.store.facts()['22222']['aliases'],['千柒','aaaa'])
        self.assertEqual(self.store.facts()['22222']['observations'],0)
    def test_extra_names_bind_identity_and_do_not_replace_qq_nickname(self):
        self.store.mark('22222','AI',aliases=['千柒','aaaa'])
        identities=GroupIdentities(self.folder/'group-identities.json','99999')
        identities.observe({'user_id':'22222','nickname':'原昵称'})
        target={'_uid':'33333','text':'aaaa是谁','_time':time.time()}
        identities.annotate(target,[],{})
        facts=identity_facts({},target);person=facts['mentioned_members'][0]
        self.assertEqual(person['qq_id'],'22222');self.assertEqual(person['nickname'],'原昵称')
        self.assertEqual(person['account_label'],'AI');self.assertEqual(person['extra_names'],['千柒','aaaa'])
        resolved=resolve_names({},target)
        self.assertEqual(resolved['resolved_qq_id'],'22222')
        self.assertEqual(resolved['references'][0]['kind'],'extra_name')
    def test_invalid_labels_aliases_and_outside_group_rejected(self):
        for label in ['unknown','机器人']:
            with self.assertRaises(ValueError):self.store.mark('22222',label)
        with self.assertRaises(ValueError):self.store.mark('22222','AI',aliases=['x'*41])
        with self.assertRaises(ValueError):self.store.mark('bad','human')
        with self.assertRaises(ValueError):view(self.root,300)
    def test_authenticated_ui_action_persists_labels_without_sending_messages(self):
        console=Console(self.root,Supervisor(self.root,start=False))
        server=DebugWebUI(self.root,console.web_action);server.start();self.addCleanup(server.close)
        with httpx.Client(base_url=server.base_url,trust_env=False) as client:
            self.assertEqual(client.get('/api/members?group=100').status_code,401)
            headers={'X-QQ-Debug-Key':server.token,'Origin':server.base_url.rstrip('/')}
            result=client.post('/api/action',headers=headers,json={'action':'member_label','group':100,
                'account':'22222','label':'AI','aliases':['千柒','aaaa']})
            self.assertEqual(result.status_code,200,result.text)
            rows=client.get('/api/members?group=100&q=aaaa',headers=headers).json()['members']
            self.assertEqual(rows[0]['label'],'AI');self.assertEqual(rows[0]['aliases'],['千柒','aaaa'])
            self.assertFalse((self.folder/'manual-outbox').exists())


if __name__=='__main__':unittest.main()
