"""Installer plans and platform boundaries; never install software or contact QQ."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
import zipfile
import platform_runtime

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/manage.py'
if not SCRIPT.exists():SCRIPT=Path(__file__).resolve().parents[1]/'scripts/manage.py'
spec=importlib.util.spec_from_file_location('portable_manage',SCRIPT)
manage=importlib.util.module_from_spec(spec);spec.loader.exec_module(manage)


class PortableLaunchTests(unittest.TestCase):
    def fixture(self,folder):
        root=Path(folder);bot=root/'group-bot';bot.mkdir()
        (bot/'debug_console.py').touch()
        (bot/'settings.example.json').write_text('{"enabled":false}',encoding='utf-8')
        (bot/'persona-default.txt').write_text('yonagi',encoding='utf-8')
        (bot/'napcat.env.example').write_text('NAPCAT_HOST=http://127.0.0.1:3000\n',encoding='utf-8')
        return root,bot

    def test_initialize_is_relative_idempotent_and_preserves_persona_and_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root,bot=self.fixture(folder)
            with patch.object(manage,'ROOT',root):
                manage.initialize()
                self.assertEqual(json.loads((bot/'settings.json').read_text())['mcp_env_file'],'napcat.env')
                (bot/'persona.txt').write_text('custom persona',encoding='utf-8')
                (bot/'settings.json').write_text('{"keep":123}',encoding='utf-8')
                manage.initialize()
            self.assertEqual((bot/'persona.txt').read_text(),'custom persona')
            self.assertEqual((bot/'settings.json').read_text(),'{"keep":123}')

    def test_existing_launch_config_blocks_reinstallation(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(manage.subprocess,'run') as run:
            root=Path(folder);(root/'launch.local.json').write_text('{"existing":true}')
            with patch.object(manage,'ROOT',root):
                with self.assertRaises(ValueError):manage.install_docker_napcat()
            self.assertEqual((root/'launch.local.json').read_text(),'{"existing":true}')
            self.assertEqual(run.call_count,1)

    def test_docker_install_keeps_volumes_and_exposes_only_loopback_ports(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(manage.subprocess,'run') as run:
            root=Path(folder)
            with patch.object(manage,'ROOT',root):manage.install_docker_napcat()
            cfg=json.loads((root/'runtime/napcat-docker/compose.json').read_text())['services']['napcat']
            self.assertTrue(all(p.startswith('127.0.0.1:') for p in cfg['ports']))
            self.assertIn('127.0.0.1:3000:3000',cfg['ports']);self.assertEqual(len(cfg['volumes']),3)
            self.assertGreater(len(cfg['environment']['WEBUI_TOKEN']),20)
            launch=json.loads((root/'launch.local.json').read_text())['napcat']
            self.assertFalse(Path(launch['compose_file']).is_absolute())
            self.assertIn('pull',run.call_args.args[0])

    def test_dry_run_has_no_writes_downloads_or_process_launches(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(manage.subprocess,'run') as run,patch.object(manage,'download') as download:
            root=Path(folder)
            with patch.object(manage,'ROOT',root):manage.install_napcat('docker',True)
            run.assert_not_called();download.assert_not_called();self.assertEqual(list(root.iterdir()),[])

    def test_archive_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=Path(folder)/'bad.zip'
            with zipfile.ZipFile(archive,'w') as stream:stream.writestr('../escape','bad')
            with self.assertRaises(ValueError):manage.safe_extract(archive,Path(folder)/'target')
            self.assertFalse((Path(folder)/'escape').exists())

    def test_posix_process_options_and_job_cleanup(self):
        with patch.object(platform_runtime.os,'name','posix'),patch.object(platform_runtime.os,'killpg',create=True) as kill,patch.object(platform_runtime.signal,'SIGKILL',9,create=True):
            self.assertEqual(platform_runtime.process_options(),{'start_new_session':True})
            job=platform_runtime.PosixJob();job.add(Mock(pid=123));job.close();job.close()
            kill.assert_called_once_with(123,9)


if __name__=='__main__':unittest.main()
