"""Onboarding never installs corrupt data, invokes a model, or duplicates servers."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import setup_demo
import run_demo


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'config').mkdir()
        self.archive = self.root / 'data.zip'
        self.archive.write_bytes(b'verified synthetic data archive')
        self.release = {'repo': 'owner/private', 'tag': 'data-v1', 'asset': 'data.zip',
                        'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                        'archive_bytes': self.archive.stat().st_size}
        (self.root / 'config/data-release.json').write_text(json.dumps(self.release))
        python = setup_demo.environment_python(self.root)
        python.parent.mkdir(parents=True)
        python.write_text('synthetic python placeholder')

    def test_setup_checks_pinned_digest_before_installing(self):
        self.archive.write_bytes(b'corrupted')
        with patch('setup_demo.subprocess.run') as command, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                setup_demo.setup(self.root, archive=self.archive, skip_dependencies=True)
        command.assert_not_called()

    def test_setup_uses_verified_installer_without_aws_or_model_commands(self):
        with patch('setup_demo.subprocess.run') as command, contextlib.redirect_stdout(io.StringIO()):
            setup_demo.setup(self.root, archive=self.archive, skip_dependencies=True)
        args = command.call_args.args[0]
        self.assertEqual(args[1:5], ['-m', 'oracle.data_bundle', 'install', str(self.archive.resolve())])
        self.assertIn(self.release['sha256'], args)
        self.assertNotIn('aws', args)
        self.assertNotIn('ollama', args)

    def test_public_release_download_is_pinned_without_login_or_commands(self):
        with patch('setup_demo.urllib.request.urlopen', return_value=io.BytesIO(self.archive.read_bytes())) as fetch, \
             patch('setup_demo.subprocess.run', side_effect=AssertionError('commands forbidden')):
            path = setup_demo.download_data(self.root, self.release)
        self.assertEqual(path.name, 'data.zip')
        self.assertEqual(path.read_bytes(), self.archive.read_bytes())
        fetch.assert_called_once_with('https://github.com/owner/private/releases/download/data-v1/data.zip', timeout=60)

    def test_corrupt_or_oversized_download_is_never_cached(self):
        for content in (b'corrupted', self.archive.read_bytes() + b'extra'):
            with self.subTest(content_length=len(content)), \
                 patch('setup_demo.urllib.request.urlopen', return_value=io.BytesIO(content)):
                with self.assertRaises(RuntimeError):
                    setup_demo.download_data(self.root, self.release)
            self.assertEqual(list((self.root / 'artifacts/downloads').iterdir()), [])

    def test_existing_cached_archive_needs_no_login_or_download(self):
        cache = self.root / 'artifacts/downloads'
        cache.mkdir(parents=True)
        (cache / 'data.zip').write_bytes(self.archive.read_bytes())
        with patch('setup_demo.subprocess.run', side_effect=AssertionError('network forbidden')):
            setup_demo.download_data(self.root, self.release)

    def test_launcher_requires_completed_setup(self):
        with patch('run_demo.subprocess.Popen') as process:
            with self.assertRaisesRegex(RuntimeError, 'make setup'):
                run_demo.run(self.root)
        process.assert_not_called()

    def test_launcher_reuses_local_server_without_loading_models(self):
        (self.root / 'oracle-data-manifest.json').write_text('{}')
        with patch('run_demo.ready', return_value=True), patch('run_demo.open_chrome') as browser, \
             patch('run_demo.subprocess.Popen') as process, contextlib.redirect_stdout(io.StringIO()):
            run_demo.run(self.root)
        process.assert_not_called()
        browser.assert_called_once_with('http://127.0.0.1:8765')

    def test_launcher_starts_only_the_local_server_and_cleans_up_its_child(self):
        (self.root / 'oracle-data-manifest.json').write_text('{}')
        child = Mock()
        child.poll.side_effect = [None, None]
        with patch('run_demo.ready', side_effect=[False, True]), patch('run_demo.open_chrome') as browser, \
             patch('run_demo.subprocess.Popen', return_value=child) as process, contextlib.redirect_stdout(io.StringIO()):
            run_demo.run(self.root)
        args = process.call_args.args[0]
        self.assertEqual(args[1:], ['-m', 'oracle', 'serve', '--port', '8765'])
        child.terminate.assert_called_once()
        browser.assert_called_once()

    def test_launcher_rejects_stale_server_and_off_host_redirects(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({'model_settings': {}, 'scenarios': [], 'instance': {'project_root': 'other'}}).encode()
        with patch('run_demo.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(RuntimeError, 'Another checkout'):
                run_demo.ready('http://127.0.0.1:8765', {'project_root': 'this'})
        with self.assertRaisesRegex(RuntimeError, 'redirects'):
            run_demo.NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://example.com')
