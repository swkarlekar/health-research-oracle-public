"""Start the local page and open Chrome. No model or AWS call on launch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

from setup_demo import ROOT, environment_python
sys.path.insert(0, str(ROOT))
from oracle.runtime_identity import runtime_identity


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, target):
        raise RuntimeError('Local readiness redirects are refused')


def ready(url, expected=None):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(url + '/api/config', timeout=1) as stream:
            value = json.loads(stream.read(128001))
        if isinstance(value, dict) and 'model_settings' in value and 'scenarios' in value:
            if expected is not None and value.get('instance') != expected:
                raise RuntimeError('Another checkout or older demo is using this port. Stop its server, then retry make run; or choose --port 8766.')
            return True
        return False
    except (OSError, ValueError, urllib.error.URLError):
        return False


def open_chrome(url):
    if sys.platform == 'darwin':
        result = subprocess.run(['/usr/bin/open', '-a', 'Google Chrome', url], capture_output=True)
        if result.returncode == 0:
            return
    else:
        choices = [shutil.which('google-chrome'), shutil.which('chrome'), shutil.which('chromium')]
        if sys.platform == 'win32':
            import os
            choices += [str(Path(os.environ.get(name, '')) / 'Google/Chrome/Application/chrome.exe')
                        for name in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')]
        for choice in choices:
            if choice and Path(choice).is_file():
                subprocess.Popen([choice, url])
                return
    print('Chrome could not be opened automatically. Open this address in Chrome: ' + url)


def run(root=ROOT, *, port=8765, browser=True):
    root = Path(root).resolve()
    python = environment_python(root)
    if not python.is_file() or not (root / 'oracle-data-manifest.json').is_file():
        raise RuntimeError('Run make setup first to install the runtime and verified release data')
    url = f'http://127.0.0.1:{port}'
    expected = runtime_identity(root)
    if ready(url, expected):
        print('Using the running demo at ' + url)
        if browser:
            open_chrome(url)
        return
    child = subprocess.Popen([str(python), '-m', 'oracle', 'serve', '--port', str(port)], cwd=root)
    try:
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError('The demo server could not start. Check the terminal output above.')
            if ready(url, expected):
                print('Demo ready at ' + url + ' · Stop with Ctrl+C', flush=True)
                if browser:
                    open_chrome(url)
                child.wait()
                return
            time.sleep(.2)
        raise RuntimeError('The local page did not become ready; no model or AWS fallback was attempted')
    except KeyboardInterrupt:
        pass
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        if not 1 <= args.port <= 65535:
            raise RuntimeError('Port must be between 1 and 65535')
        run(port=args.port, browser=not args.no_browser)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print('Launch stopped: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
