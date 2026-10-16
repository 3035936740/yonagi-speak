import json
from pathlib import Path
import tempfile
import time
import unittest
from console_config import atomic,Config,runtime
from debug_events import DebugEvents
from debug_history import bounded
from debug_references import text_chars
from debug_webui import DebugWebUI
from recent_chat_journal import RecentChatJournal


class DebugWindowTests(unittest.TestCase):
    def setup_root(self,root):
        cfg=json.loads(Path(__file__).with_name('settings.example.json').read_text(encoding='utf-8'))
        cfg.update(group_id=100,chat_group_whitelist=[100,200],console_max_text_chars=6000);atomic(root/'settings.json',cfg)
        return Config(root).load()

    def test_log_flood_never_reduces_fifty_chat_messages_to_two(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.setup_root(root);events=DebugEvents(root/'runtime',100)
            for n in range(50):events.emit('received',message_id=str(n),text='a'*100)
            for n in range(35):events.emit('stage',text='log'*100)
            chat=[r for r in events.rows if r['kind']=='received']
            self.assertEqual(len(chat),50)
            for n in range(50,61):events.emit('received',message_id=str(n),text='b'*100)
            chat=[r for r in events.rows if r['kind']=='received']
            self.assertEqual([r['message_id'] for r in chat],[str(n) for n in range(1,61)])
            restarted=DebugEvents(root/'runtime',100)
            self.assertEqual(list(events.rows),list(restarted.rows))
            self.assertLessEqual(sum(text_chars(r) for r in events.rows if r['kind']=='stage'),6000)

    def test_oversized_event_keeps_latest_content_and_never_clears_other_lane(self):
        cfg={'console_max_text_chars':500,'console_max_records':100}
        rows=[{'kind':'received','text':'keep'}, {'kind':'stage','text':'x'*900}]
        kept=bounded(rows,cfg)
        self.assertEqual(len(kept),2)
        self.assertEqual(kept[-1]['text'],'x'*500)
        self.assertTrue(kept[-1]['text_truncated'])

    def test_native_delivery_receipt_is_not_a_second_chat_message(self):
        rows=[{'kind':'sent','message_id':'','action':'forward_group_single_msg','image_count':1,
                'text':'[表情包：[这是你吗]]'},
            {'kind':'sent','message_id':'1843586123','image_count':1,'text':'[商城表情：这是你吗]'}]
        kept=bounded(rows,{'console_max_text_chars':6000,'console_max_records':100})
        self.assertEqual([row['message_id'] for row in kept if row['kind']=='sent'],['1843586123'])
        self.assertEqual(kept[0]['kind'],'stage');self.assertNotIn('image_count',kept[0])

    def test_snapshot_recovers_journal_without_writes_or_resurrecting_deleted_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cfg=self.setup_root(root);target=runtime(root,200,cfg)
            journal=RecentChatJournal(target/'recent-chat.jsonl')
            stamp=time.time()
            for n in range(50):journal.append({'_id':str(n),'_uid':'12345','_time':stamp+n/100,
                'sender':'群友','text':'a'*80,'_image_count':1 if n==49 else 0})
            atomic(target/'deleted-messages.json',['3'])
            path=target/'debug-events.jsonl';path.write_text(json.dumps({'kind':'received','message_id':'49',
                'group_id':200,'time':stamp+.49,'sender':'群友','text':'a'*80})+'\n',encoding='utf-8')
            before={p:p.read_bytes() for p in (path,journal.path)}
            server=DebugWebUI(root,lambda value:None)
            snapshot=server.snapshot({'group':['200'],'kind':['chat']})
            self.assertEqual(len(snapshot['events']),49)
            self.assertNotIn('3',[r['message_id'] for r in snapshot['events']])
            self.assertEqual(snapshot['status']['chat_text_chars'],49*80)
            self.assertTrue(all(r['group_id']==200 for r in snapshot['events']))
            self.assertEqual({p:p.read_bytes() for p in before},before)


if __name__=='__main__':unittest.main()
