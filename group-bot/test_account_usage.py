import json
from pathlib import Path
import tempfile
import time
import sqlite3
import unittest
from unittest.mock import patch
import account_usage as usage


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_usage_dedup_group_windows_and_cache_not_double_counted(self):
        now=time.time()
        raw=json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':20}}).encode()
        usage.record(self.root,{'group_id':100,'model':'test'},raw,'one',now-60)
        usage.record(self.root,{'group_id':100,'model':'test'},raw,'one',now-60)
        usage.record(self.root,{'group_id':200},raw,'two',now-6*3600)
        usage.record(self.root,{'group_id':100},raw,'old',now-8*86400)
        data=usage.snapshot(self.root,100);self.assertEqual(data['rolling_5h']['total'],120)
        self.assertEqual(data['rolling_week']['cached'],80);self.assertEqual(data['rolling_week']['calls'],1)
        self.assertEqual(usage.snapshot(self.root)['rolling_week']['total'],240)
        self.assertFalse(usage.record(self.root,{},b'{"type":"turn.failed"}','missing'))
        self.assertFalse(usage.record(self.root,{},b'{"type":"turn.completed","usage":{"input_tokens":true}}','bad'))
    def test_real_window_selection_cache_stale_error_and_unknown_not_zero(self):
        now=time.time();result={'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':25,'windowDurationMins':300,'resetsAt':now+600},'secondary':{'usedPercent':60,'windowDurationMins':10080,'resetsAt':now+3600}},'other':{'primary':{'usedPercent':5,'windowDurationMins':1440}}}}
        windows=usage.normalize_limits(result);self.assertEqual([row['label'] for row in windows],['5小时','1周','1440分钟'])
        self.assertEqual(windows[0]['remaining_percent'],75)
        with patch.object(usage,'read_limits',return_value=windows) as read:
            data=usage.snapshot(self.root,refresh=True);usage.snapshot(self.root,refresh=True);self.assertEqual(read.call_count,1)
        self.assertEqual(data['windows'][0]['project_tokens_in_window']['total'],0)
        self.assertIsNone(data['windows'][2]['project_tokens_in_window'])
        usage._cache[str(self.root.resolve())]['attempted']=0
        with patch.object(usage,'read_limits',side_effect=TimeoutError):data=usage.snapshot(self.root,refresh=True)
        self.assertTrue(data['stale']);self.assertTrue(data['error']);self.assertEqual(len(data['windows']),3)
        self.assertIn('剩余token总数未知',usage.format_text(data))
    def test_empty_limits_and_unknown_account_windows_are_not_invented(self):
        self.assertEqual(usage.normalize_limits({}),[])
        self.assertEqual(usage.snapshot(self.root)['windows'],[])
        self.assertIn('尚无额度',usage.format_text(usage.snapshot(self.root)))
        result=usage.snapshot(self.root);result['windows']=[{'name':'pro','minutes':10080,'label':'1周','used_percent':10,'remaining_percent':90,'resets_at':None}]
        self.assertIn('5 hour token -',usage.format_text(result));self.assertNotIn('1 week token -',usage.format_text(result))

    def test_cache_missing_zero_nested_and_partial_coverage_are_distinct(self):
        def add(identity,extra):
            usage.record(self.root,{},json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':10,**extra}}).encode(),identity)
        add('unknown',{});data=usage.snapshot(self.root)['rolling_5h']
        self.assertIsNone(data['cached']);self.assertIsNone(data['cache_hit_percent']);self.assertEqual(data['cache_unknown_calls'],1)
        add('zero',{'cached_input_tokens':0});add('nested',{'input_tokens_details':{'cached_tokens':80}})
        data=usage.snapshot(self.root)['rolling_5h'];self.assertEqual(data['cached'],80);self.assertEqual(data['uncached'],120)
        self.assertEqual(data['cache_hit_percent'],40);self.assertEqual(data['cache_reported_calls'],2);self.assertEqual(data['cache_hit_calls'],1)
        self.assertEqual(data['input'],300);self.assertEqual(data['total'],330)

    def test_legacy_zero_is_unknown_positive_is_known_and_migration_preserves_rows(self):
        path=usage.db_path(self.root);path.parent.mkdir()
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE calls(id TEXT PRIMARY KEY,stamp REAL,group_id INTEGER,model TEXT,input INTEGER,cached INTEGER,output INTEGER)')
            db.executemany('INSERT INTO calls VALUES(?,?,?,?,?,?,?)',[(name,time.time(),100,'test',100,cached,10) for name,cached in [('zero',0),('positive',50)]])
        before=usage.snapshot(self.root)['rolling_5h'];self.assertEqual(before['cache_unknown_calls'],1);self.assertEqual(before['cached'],50)
        usage.record(self.root,{},b'{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":10}}','new')
        after=usage.snapshot(self.root)['rolling_5h'];self.assertEqual(after['calls'],3);self.assertEqual(after['cache_unknown_calls'],1)
        self.assertEqual(after['cache_reported_calls'],2);self.assertEqual(after['cache_hit_percent'],25)


if __name__=='__main__':unittest.main()
