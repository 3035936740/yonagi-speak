import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from console_config import Config,DEFAULTS,atomic,runtime
from debug_console import Console,Supervisor
from debug_webui import DebugWebUI
from group_identity import GroupIdentities,identity_facts,resolve_names
from member_labels import MemberLabels,command_start,view
import httpx


class MemberLabelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        cfg=json.loads(Path(__file__).with_name('settings.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100,200]);atomic(self.root/'settings.json',cfg)
        self.folder=runtime(self.root,100);self.store=MemberLabels(self.folder)
        self.rule_cfg={'member_auto_bot_min_observations':3}
    def response(self,index,command='/pgr',anchored=False):
        now=time.time()+index*30
        request={'_id':'cmd'+str(index),'_uid':'11111','_time':now,'text':command,'is_bot':False}
        response={'_id':'resp'+str(index),'_uid':'22222','_time':now+2,'text':'[图片]' if command=='/pgr' else '响应指令',
            '_images':['image'] if command=='/pgr' else [],'_reply_id':request['_id'] if anchored else ''}
        return request,response
    def test_repeated_command_responses_mark_bot_and_deduplicate_same_request(self):
        request,response=self.response(0)
        self.assertFalse(self.store.observe(response,[request],self.rule_cfg))
        for n in range(4):self.store.observe({**response,'_id':'duplicate'+str(n)},[request],self.rule_cfg)
        self.assertEqual(self.store.facts()['22222']['observations'],1)
        for n in range(1,3):
            request,response=self.response(n);changed=self.store.observe(response,[request],self.rule_cfg)
        self.assertTrue(changed);self.assertEqual(self.store.facts()['22222']['label'],'bot')
        self.assertEqual(self.store.facts()['22222']['source'],'auto')
    def test_nonadjacent_text_requires_quote_or_at_anchor(self):
        request,response=self.response(0,'/a hi')
        unrelated={'_id':'chat','_uid':'33333','_time':request['_time']+1,'text':'路过'}
        self.store.observe(response,[request,unrelated],self.rule_cfg);self.assertNotIn('22222',self.store.facts())
        for n in range(3):
            request,response=self.response(n+1,'/a hi',True);self.store.observe(response,[request],self.rule_cfg)
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
            request,response=self.response(n);self.store.observe(response,[request],self.rule_cfg)
        fact=MemberLabels(self.folder).facts()['22222']
        self.assertEqual(fact['label'],'human');self.assertEqual(fact['auto_label'],'bot')
        self.assertEqual(fact['aliases'],['千柒','aaaa'])
        self.assertEqual(MemberLabels(runtime(self.root,200)).facts(),{})
        self.assertEqual(self.store.mark('22222','auto')['label'],'bot')
        self.assertEqual(self.store.mark('22222','clear')['label'],'')
        self.assertEqual(self.store.facts()['22222']['aliases'],['千柒','aaaa'])
        self.assertEqual(self.store.facts()['22222']['observations'],0)

    def test_command_prefixes_are_start_only_and_support_chinese(self):
        for text,prefix in [('/xxx hi','/'),('！查分','!'),('!draw','!'),('.xxx','.'),('y查分','y'),('y help','y'),('@11111 /pgr','/'),('／帮助','/'),('#help','#')]:
            with self.subTest(text=text):self.assertEqual(command_start(text)['prefix'],prefix)
        for text in ['hello /pgr','yes','you','...','!!!','!','/','.5','3.14','https://example.com','普通群聊']:
            with self.subTest(text=text):self.assertIsNone(command_start(text))
        self.assertEqual(command_start('查天气',['查'])['prefix'],'查')
        self.assertIsNone(command_start('/help',['!']))

    def test_default_ten_same_prefix_in_two_hundred_messages_and_restart(self):
        self.assertEqual(DEFAULTS['member_auto_bot_window_messages'],200)
        self.assertEqual(DEFAULTS['member_auto_bot_min_observations'],10)
        history=[]
        for n in range(10):
            request,response=self.response(n,'！查分' if n%2 else '!help')
            self.store.observe(request,history,{});history.append(request)
            self.store=MemberLabels(self.folder)
            self.store.observe(response,history,{});history.append(response)
            self.assertEqual(self.store.facts()['22222']['label'],'bot' if n==9 else '')
        facts=self.store.facts()
        self.assertNotIn('11111',facts)
        self.assertEqual(facts['22222']['prefix_counts'],{'!':10})
        self.assertEqual(facts['22222']['evidence'][-1]['command'],'！查分'.replace('！','!'))

    def test_mixed_prefixes_do_not_combine_and_rolling_window_expires_evidence(self):
        cfg={'member_auto_bot_window_messages':20,'member_auto_bot_min_observations':3}
        for n,command in enumerate(['/a hi','!draw','.help','y查分','/b','/c']):
            request,response=self.response(n,command,True);self.store.observe(response,[request],cfg)
        self.assertEqual(self.store.facts()['22222']['label'],'bot')
        self.assertEqual(self.store.facts()['22222']['prefix_counts']['/'],3)
        for n in range(20):
            self.store.observe({'_id':'new'+str(n),'_uid':'33333','_time':time.time()+1000+n,'text':'普通聊天'},[],cfg)
        fact=self.store.facts()['22222']
        self.assertEqual(fact['observations'],0)
        self.assertEqual(fact['label'],'bot')
        self.assertEqual(len(self.store.read()['rule_messages']),20)

    def test_below_threshold_mixed_prefixes_and_old_evidence_never_mark(self):
        cfg={'member_auto_bot_window_messages':20,'member_auto_bot_min_observations':3}
        for n,command in enumerate(['/a hi','!draw','.help','y查分','/b']):
            request,response=self.response(n,command,True);self.store.observe(response,[request],cfg)
        self.assertEqual(self.store.facts()['22222']['label'],'')
        # Fill the window, then a third slash response must not resurrect old evidence.
        for n in range(20):self.store.observe({'_id':'fill'+str(n),'_uid':'33333','_time':time.time()+500+n,'text':'聊天'},[],cfg)
        request,response=self.response(30,'/c',True);self.store.observe(response,[request],cfg)
        self.assertEqual(self.store.facts()['22222']['prefix_counts'],{'/':1})
        self.assertEqual(self.store.facts()['22222']['label'],'')

    def test_configurable_window_threshold_prefixes_and_validation(self):
        config=Config(self.root)
        with patch('console_config.validate_model',return_value=None):
            for overrides in [{'member_auto_bot_window_messages':19},{'member_auto_bot_min_observations':101},
                {'member_auto_bot_command_prefixes':[]},{'member_auto_bot_command_prefixes':[' ']},
                {'member_auto_bot_command_prefixes':'!'}]:
                with self.subTest(overrides=overrides),self.assertRaises(ValueError):config.validate({**config.load(),**overrides})
            config.save({**config.load(),'member_auto_bot_window_messages':40,'member_auto_bot_min_observations':2,'member_auto_bot_command_prefixes':['查']})
        cfg=config.load()
        for n in range(2):
            request,response=self.response(n,'查天气',True);self.store.observe(response,[request],cfg)
        self.assertEqual(self.store.facts()['22222']['label'],'bot')

    def test_wrong_target_late_response_and_command_sender_never_count(self):
        request,response=self.response(0,'!draw',True)
        for req,resp in [(dict(request,_at_ids=['33333']),response),(request,dict(response,_time=request['_time']+61)),
            (request,dict(response,text='/help')),(request,dict(response,is_bot=True))]:
            self.store=MemberLabels(self.folder)
            self.store.observe(resp,[req],{})
        self.assertEqual(self.store.facts(),{})

    def test_clear_does_not_reuse_previously_seen_evidence(self):
        now=time.time()-200;history=[]
        for n in range(3):
            request,response=self.response(n)
            request['_time']=now+n*30;response['_time']=request['_time']+2
            history.extend([request,response]);self.store.observe(response,history,self.rule_cfg)
        self.assertEqual(self.store.facts()['22222']['label'],'bot')
        self.store.mark('22222','clear')
        self.store.observe({'_id':'next','_uid':'33333','_time':time.time(),'text':'普通聊天'},history,self.rule_cfg)
        fact=self.store.facts()['22222']
        self.assertEqual(fact['label'],'');self.assertEqual(fact['observations'],0)

    def test_prefix_change_reparses_window_without_stale_matches(self):
        request,response=self.response(0,'!help',True)
        self.store.observe(response,[request],{})
        self.assertEqual(self.store.facts()['22222']['observations'],1)
        self.store.observe({'_id':'next','_uid':'33333','_time':time.time()+5,'text':'路过'},[],{'member_auto_bot_command_prefixes':['/']})
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
