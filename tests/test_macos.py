import json
import os
from pathlib import Path
import plistlib
import runpy
import tempfile
import unittest

prepare = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'deploy/prepare-macos.py'))['prepare']

class MacPreparationTests(unittest.TestCase):
    def test_disabled_definition_with_exact_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'Application Support' / 'queue'
            executable = Path(directory) / 'CLI Path' / 'codex-queue'
            path = prepare(executable, folder)
            data = plistlib.loads(path.read_bytes())
            self.assertTrue(data['Disabled'])
            self.assertFalse(data['RunAtLoad'])
            self.assertFalse(data['KeepAlive'])
            self.assertEqual(data['StartInterval'], 60)
            self.assertEqual(data['ProgramArguments'][0], str(executable.resolve()))
            self.assertEqual(data['ProgramArguments'][-3:], ['worker', '--once', '--execute'])
            self.assertFalse(json.loads((folder/'config.json').read_text())['execution_enabled'])
            self.assertEqual(path.parent.name, 'launchd')
            if os.name != "nt": self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_disabled_config_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = folder / 'config.json'
            original = '{"execution_enabled": false, "codex": "/custom/path"}'
            config.write_text(original)
            prepare('/queue', folder)
            self.assertEqual(config.read_text(), original)

    def test_enabled_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'config.json').write_text('{"execution_enabled": true}')
            with self.assertRaises(ValueError):
                prepare('/queue', folder)
            self.assertFalse((folder/'launchd/local.codex-queue.plist').exists())
