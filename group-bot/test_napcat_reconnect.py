import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,AsyncMock,patch
import bot
from console_config import atomic,read_json,runtime
from debug_console import Console,Supervisor
from napcat_reconnect import reconnect


class ReconnectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        atomic(self.root/'settings.json',{**bot.settings(),'group_id':100,'chat_group_whitelist':[100,200],'enabled':True})
        self.ui=Console(self.root,Supervisor(self.root,start=False))
    def test_explicit_group_reconnect_does_not_stop_other_group(self):
        self.ui.supervisor.paused={100,200};self.ui.supervisor.retry_after={100:time.monotonic()+99}
        with patch.object(self.ui.supervisor,'stop_group') as stop,patch.object(self.ui.supervisor,'sync') as sync:
            reconnect(self.ui,'napcat');stop.assert_called_once_with(100);sync.assert_called_once()
        self.assertEqual(self.ui.supervisor.paused,{200});self.assertNotIn(100,self.ui.supervisor.retry_after)
        self.ui.execute('/help reconnect');self.assertIn('mcp',self.ui.notice)
    def test_mcp_action_checks_fresh_session_and_records_actual_result(self):
        folder=runtime(self.root,100);folder.mkdir(exist_ok=True)
        with patch('napcat_reconnect.probe_mcp',new=AsyncMock(return_value=True)) as probe:
            result=self.ui.web_action({'action':'reconnect','mode':'mcp','group':100})
            probe.assert_awaited_once_with(self.root,100);self.assertTrue(result['ok'])
        self.assertTrue(read_json(folder/'mcp-connection.json')['ok'])
        with patch('napcat_reconnect.probe_mcp',new=AsyncMock(side_effect=TimeoutError)):
            with self.assertRaises(ValueError):reconnect(self.ui,'mcp')
        self.assertFalse(read_json(folder/'mcp-connection.json')['ok'])
    def test_unexpected_zero_exit_is_retried_not_marked_as_manual_pause(self):
        atomic(self.root/'settings.json',{**bot.settings(),'group_id':100,'chat_group_whitelist':[100],'enabled':True})
        sup=Supervisor(self.root);process=Mock();process.poll.return_value=0;process.returncode=0
        sup.children[100]=process
        sup.sync();self.assertNotIn(100,sup.paused);self.assertNotIn(100,sup.children)
        self.assertGreater(sup.retry_after[100],time.monotonic());sup.sync();self.assertNotIn(100,sup.children)
    def test_existing_waiting_worker_is_not_duplicated(self):
        atomic(self.root/'settings.json',{**bot.settings(),'group_id':100,'chat_group_whitelist':[100],'enabled':True})
        folder=runtime(self.root,100);folder.mkdir(exist_ok=True)
        atomic(folder/'state.json',{'pid':123,'online':False,'waiting_for_qq':True})
        with patch('debug_console.alive',return_value=True),patch('debug_console.subprocess.Popen') as launch:
            Supervisor(self.root).sync();launch.assert_not_called()
    def test_wait_reloads_env_and_clears_stale_paused_state_after_verified_login(self):
        folder=runtime(self.root,100);folder.mkdir(exist_ok=True)
        env_file=self.root/'napcat.env';env_file.write_text('NAPCAT_HOST=http://new.test\nNAPCAT_TOKEN=local-test\n')
        cfg={**bot.settings(),'enabled':True,'group_id':100,'mcp_env_file':str(env_file)}
        atomic(folder/'state.json',{'online':False,'stopped':True})
        env={'NAPCAT_HOST':'http://old.test'}
        api=AsyncMock(side_effect=[{'user_id':169901502},{'group_id':100}])
        with patch.object(bot,'RUNTIME',folder),patch.object(bot,'settings',return_value=cfg),patch.object(bot,'api',api):
            asyncio.run(bot.wait_for_qq(None,env,cfg))
        self.assertEqual(env['NAPCAT_HOST'],'http://new.test');self.assertTrue(read_json(folder/'state.json')['online'])
        self.assertFalse(read_json(folder/'state.json')['stopped']);self.assertFalse(read_json(folder/'state.json')['waiting_for_qq'])


if __name__=='__main__':unittest.main()
