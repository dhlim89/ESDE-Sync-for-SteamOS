import base64
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='esde-v037-')
        self.root = Path(self.tmp.name)
        self.app = self.root / 'app'
        self.env = dict(os.environ, ESDE_SYNC_APP_HOME=str(self.app),
                        ESDE_SYNC_BIN_DIR=str(self.root / 'bin'),
                        ESDE_SYNC_DESKTOP_DIR=str(self.root / 'desktop'),
                        XDG_STATE_HOME=str(self.root / 'state'),
                        XDG_CONFIG_HOME=str(self.root / 'config'),
                        ESDE_SYNC_HEADLESS='1')
        self.port_file = self.root / 'state/esde-sync/gui-port'
        self.profile = self.root / 'config/esde-sync/profiles/device.conf'
        self.profile.parent.mkdir(parents=True)
        self.profile.write_bytes(b'SYSTEMS="gb gbc"\nMIRROR_ROMS=1\n# preserve me\n')
        self.original = self.profile.read_bytes()
        self.processes = []
        self.install()
        self.meta = {'version': '0.38', 'url': '', 'sha256': ''}
        owner = self

        class Fixture(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/contents':
                    owner.headers_seen = dict(self.headers)
                    body = json.dumps({'encoding': 'base64', 'content':
                        base64.encodebytes(json.dumps(owner.meta).encode()).decode()}).encode()
                elif self.path == '/archive':
                    body = owner.archive
                elif self.path == '/malformed':
                    body = b'{"encoding":"base64","content":"!invalid!"}'
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.fixture = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        self.fixture.daemon_threads = True
        self.fixture_url = 'http://127.0.0.1:' + str(self.fixture.server_port)
        self.thread = threading.Thread(target=self.fixture.serve_forever, daemon=True)
        self.thread.start()
        self.archive = b'not installed: checksum must reject this'

    def tearDown(self):
        for p in self.processes:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
        # Updater relaunch is a separate helper descendant; stop only our app.
        subprocess.run(['pkill', '-f', str(self.app / 'esde-sync-gui.py')], check=False)
        self.fixture.shutdown()
        self.fixture.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def install(self):
        subprocess.run(['bash', str(ROOT / 'src/install.sh')], env=self.env,
                       check=True, stdout=subprocess.DEVNULL)

    def start(self, fixture=False, endpoint='/contents'):
        if fixture:
            # Separate process; real handler and real Contents parser, local metadata URL only.
            harness = self.root / 'harness.py'
            harness.write_text('import importlib.util\n'
                f's=importlib.util.spec_from_file_location("gui", {str(self.app / "esde-sync-gui.py")!r})\n'
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n'
                f'm.UPDATE_META_URL={self.fixture_url + endpoint!r}\n'
                'm.main()\n')
            cmd = ['python3', str(harness)]
        else:
            cmd = [str(self.root / 'bin/esde-sync')]
        p = subprocess.Popen(cmd, env=self.env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self.processes.append(p)
        return p

    def request(self, path='/api/state', post=False):
        port = self.port_file.read_text().strip()
        req = urllib.request.Request('http://127.0.0.1:' + port + path,
                                     data=b'' if post else None)
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertIn('application/json', r.headers['Content-Type'])
            return json.load(r)

    def ready(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                return self.request()
            except (OSError, ValueError):
                time.sleep(.05)
        self.fail('Backend did not become ready')

    def test_contents_state_and_failure_json_logs(self):
        self.start(fixture=True)
        d = self.ready()['update']
        self.assertTrue(d['ok'])
        self.assertEqual((d['current'], d['latest'], d['available']), ('0.37', '0.38', True))
        self.assertEqual(self.headers_seen['Accept'], 'application/vnd.github+json')
        self.assertEqual(self.headers_seen['User-Agent'], 'ES-DE-Sync/0.37')
        self.meta['version'] = '0.36'
        self.assertFalse(self.request()['update']['available'])
        self.meta.update(version='0.38', url=self.fixture_url + '/missing')
        error = self.request('/api/update', post=True)
        self.assertFalse(error['ok'])
        self.assertIn('404', error['reason'])
        log = self.root / 'state/esde-sync/update.log'
        self.assertIn('UPDATE ERROR', log.read_text())
        self.meta.update(url=self.fixture_url + '/archive', sha256='0' * 64)
        error = self.request('/api/update', post=True)
        self.assertFalse(error['ok'])
        self.assertIn('SHA-256', error['reason'])
        self.assertIn('SHA-256', log.read_text())
        self.assertEqual(self.profile.read_bytes(), self.original)
        self.assertFalse((self.root / 'state/esde-sync/gui-result.txt').exists())

    def test_malformed_contents_returns_json(self):
        self.start(fixture=True, endpoint='/malformed')
        self.assertFalse(self.ready()['update']['ok'])
        error = self.request('/api/update', post=True)
        self.assertFalse(error['ok'])
        self.assertIn('업데이트 정보를', error['reason'])

    def test_single_instance_and_installer_restart(self):
        installed = self.app / 'esde-sync-gui.py'
        installed.write_text(installed.read_text().replace('APP_VERSION = "0.37"', 'APP_VERSION = "0.36"'))
        first = self.start()
        self.assertEqual(self.ready()['update']['current'], '0.36')
        port = self.port_file.read_text()
        second = self.start()
        self.assertEqual(second.wait(timeout=10), 0)
        self.assertIsNone(first.poll())
        self.assertEqual(self.port_file.read_text(), port)
        self.install()
        first.wait(timeout=10)
        self.assertFalse(self.port_file.exists())
        self.assertEqual(self.profile.read_bytes(), self.original)
        self.start()
        self.assertEqual(self.ready()['update']['current'], '0.37')
        self.assertEqual(self.profile.read_bytes(), self.original)

    def test_live_github_contents_and_state(self):
        url = 'https://api.github.com/repos/dhlim89/esde-sync/contents/version.json?ref=main'
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                                   'User-Agent': 'ES-DE-Sync/0.37'})
        with urllib.request.urlopen(req, timeout=15) as response:
            contents = json.load(response)
        metadata = json.loads(base64.b64decode(contents['content']))
        self.assertTrue(metadata['version'])
        self.start()
        info = self.ready()['update']
        self.assertTrue(info['ok'], info['error'])
        self.assertEqual(info['latest'], metadata['version'])
        self.assertEqual(info['current'], '0.37')
        expected = tuple(map(int, metadata['version'].lstrip('v').split('.'))) > (0, 37)
        self.assertEqual(info['available'], expected)
    def test_successful_update_helper_installs_and_relaunches(self):
        archive_path = self.root / 'release.zip'
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in (ROOT / 'src').rglob('*'):
                if p.is_file() and '__pycache__' not in p.parts:
                    z.write(p, 'esde-sync-v0.37/' + str(p.relative_to(ROOT / 'src')))
        self.archive = archive_path.read_bytes()
        self.meta.update(url=self.fixture_url + '/archive',
                         sha256=hashlib.sha256(self.archive).hexdigest())
        first = self.start(fixture=True)
        self.ready()
        self.assertTrue(self.request('/api/update', post=True)['ok'])
        first.wait(timeout=10)
        deadline = time.monotonic() + 15
        log = self.root / 'state/esde-sync/update.log'
        while time.monotonic() < deadline:
            if log.exists() and 'relaunch' in log.read_text():
                break
            time.sleep(.1)
        self.assertIn('install exit=0', log.read_text())
        self.assertIn('relaunch', log.read_text())
        self.assertEqual(self.ready()['update']['current'], '0.37')
        self.assertEqual(self.profile.read_bytes(), self.original)


if __name__ == '__main__':
    py_compile.compile(str(ROOT / 'src/esde-sync-gui.py'), doraise=True)
    unittest.main(verbosity=2)
