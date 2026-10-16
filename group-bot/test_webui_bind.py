from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import httpx
from console_config import atomic,Config
from debug_webui import DebugWebUI


class BindTests(unittest.TestCase):
    def setup_root(self,root,host='127.0.0.1',port=0):
        cfg=json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100,200],webui_host=host,webui_port=port)
        atomic(root/'settings.json',cfg)

    def test_config_persists_valid_addresses_ports_and_rejects_group_override(self):
        with tempfile.TemporaryDirectory() as folder,patch('console_config.validate_model'):
            root=Path(folder);self.setup_root(root);store=Config(root)
            for host in ('127.0.0.1','0.0.0.0','::','::1','192.168.1.2'):
                store.set('webui_host',host);self.assertEqual(Config(root).load()['webui_host'],host)
            store.set('webui_port','8123');self.assertEqual(Config(root).load()['webui_port'],8123)
            for key,value in [('webui_host','http://example.com'),('webui_host','evil.example'),
                    ('webui_port','65536'),('webui_port','-1')]:
                with self.assertRaises(ValueError):store.set(key,value)
            with self.assertRaises(ValueError):Config(root,200).set('webui_port','1234')

    def test_fixed_port_and_origin_auth_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            self.setup_root(root,port=port);server=DebugWebUI(root,lambda value:{'ok':True})
            try:
                server.start();self.assertEqual(server.server.server_port,port)
                with httpx.Client(base_url=server.base_url,trust_env=False) as client:
                    self.assertEqual(client.get('/').status_code,200)
                    self.assertEqual(client.get('/api/system').status_code,401)
                    headers={'X-QQ-Debug-Key':server.token,'Origin':server.base_url.rstrip('/')}
                    self.assertEqual(client.post('/api/action',json={},headers=headers).status_code,200)
                    self.assertEqual(client.post('/api/action',json={},headers={**headers,'Origin':'http://evil.example'}).status_code,403)
                    self.assertEqual(client.get('/',headers={'Host':f'evil.example:{port}'}).status_code,403)
            finally:server.close()

    def test_wildcard_accepts_literal_lan_host_but_keeps_auth_and_origin_checks(self):
        requested=[]
        class LocalTestServer(ThreadingHTTPServer):
            def __init__(self,address,handler):
                requested.append(address);super().__init__(('127.0.0.1',0),handler)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.setup_root(root,'0.0.0.0');server=DebugWebUI(root,lambda value:{'ok':True})
            try:
                with patch('debug_webui.ThreadingHTTPServer',LocalTestServer):server.start()
                self.assertEqual(requested,[('0.0.0.0',0)])
                host='192.168.1.2:'+str(server.server.server_port)
                with httpx.Client(base_url=server.base_url,trust_env=False) as client:
                    self.assertEqual(client.get('/api/system',headers={'Host':host}).status_code,401)
                    headers={'Host':host,'X-QQ-Debug-Key':server.token,'Origin':'http://'+host}
                    self.assertEqual(client.post('/api/action',json={},headers=headers).status_code,200)
                    self.assertEqual(client.post('/api/action',json={},headers={**headers,'Origin':'http://evil.example'}).status_code,403)
            finally:server.close()

    def test_occupied_port_is_reported_without_switching_to_random_port(self):
        with tempfile.TemporaryDirectory() as folder,socket.socket() as sock:
            sock.bind(('127.0.0.1',0));sock.listen()
            root=Path(folder);self.setup_root(root,port=sock.getsockname()[1])
            server=DebugWebUI(root,lambda value:{})
            with self.assertRaisesRegex(ValueError,'WebUI'):server.start()
            self.assertIsNone(server.server)

    def test_ipv6_loopback_url_and_auth(self):
        if not socket.has_ipv6:self.skipTest('IPv6 unavailable')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.setup_root(root,'::1');server=DebugWebUI(root,lambda value:{})
            try:
                try:server.start()
                except ValueError as exc:self.skipTest(str(exc))
                self.assertTrue(server.base_url.startswith('http://[::1]:'))
                with httpx.Client(base_url=server.base_url,trust_env=False) as client:
                    self.assertEqual(client.get('/').status_code,200)
                    self.assertEqual(client.get('/api/system').status_code,401)
            finally:server.close()


if __name__=='__main__':unittest.main()
