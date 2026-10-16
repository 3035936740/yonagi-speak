import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from PIL import Image
from console_config import atomic
from debug_avatars import avatar,enrich_users


class AvatarTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        atomic(self.root/'settings.json',{'group_id':12345,'chat_group_whitelist':[12345],'avatar_remote_enabled':True})
        (self.root/'runtime').mkdir()
        atomic(self.root/'runtime/group-identities.json',{'members':[{'qq_id':'67890'}]})

    def test_fixed_remote_hosts_cache_and_valid_image_decode(self):
        raw=io.BytesIO();Image.new('RGB',(100,100),'blue').save(raw,format='JPEG')
        calls=[]
        def handle(request):calls.append(str(request.url));return httpx.Response(200,content=raw.getvalue(),headers={'content-type':'image/jpeg'})
        real=httpx.Client
        with patch('debug_avatars.httpx.Client',side_effect=lambda **kwargs:real(transport=httpx.MockTransport(handle),**kwargs)):
            first,mime=avatar(self.root,12345,'user','67890')
            self.assertEqual(first[:8],b'\x89PNG\r\n\x1a\n');self.assertEqual(mime,'image/png')
            self.assertEqual(avatar(self.root,12345,'user','67890')[0],first)
            avatar(self.root,12345,'group','12345')
        self.assertEqual(calls,['https://q1.qlogo.cn/g?b=qq&nk=67890&s=100','https://p.qlogo.cn/gh/12345/12345/100'])

    def test_no_arbitrary_host_or_nonmember_access(self):
        with patch('debug_avatars.httpx.Client') as client:
            for group,kind,identity in [(12345,'user','http://127.0.0.1'),(12345,'user','11111'),(12345,'group','67890'),(67890,'group','67890')]:
                with self.assertRaises(ValueError):avatar(self.root,group,kind,identity)
            client.assert_not_called()

    def test_network_disabled_never_calls_remote_host(self):
        atomic(self.root/'settings.json',{'group_id':12345,'chat_group_whitelist':[12345],'avatar_remote_enabled':False})
        with patch('debug_avatars.httpx.Client') as client:
            with self.assertRaisesRegex(ValueError,'联网未启用'):avatar(self.root,12345,'group','12345')
            client.assert_not_called()

    def test_older_log_ids_recovered_from_journal_never_by_name(self):
        (self.root/'runtime/recent-chat.jsonl').write_text(json.dumps({'_id':'1','_uid':'67890','sender':'同名'})+'\n',encoding='utf-8')
        rows=enrich_users([{'message_id':'1','sender':'同名'},{'message_id':'2','sender':'同名'}],self.root/'runtime',{})
        self.assertEqual(rows[0]['user_id'],'67890');self.assertNotIn('user_id',rows[1])

    def test_failed_or_nonimage_response_uses_fallback_contract(self):
        real=httpx.Client
        def handle(request):return httpx.Response(200,text='not an image',headers={'content-type':'text/html'})
        with patch('debug_avatars.httpx.Client',side_effect=lambda **kwargs:real(transport=httpx.MockTransport(handle),**kwargs)):
            with self.assertRaisesRegex(ValueError,'默认头像'):avatar(self.root,12345,'group','12345')


if __name__=='__main__':unittest.main()
