"""One-time local setup. Downloads pinned data, never a model or AWS resource."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import venv

ROOT = Path(__file__).resolve().parents[1]


def environment_python(root):
    return root / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def download_data(root, release):
    if not re.fullmatch(r'[0-9a-f]{64}', release['sha256']) or Path(release['asset']).name != release['asset']:
        raise RuntimeError('The pinned data-release configuration is invalid')
    directory = root / 'artifacts/downloads'
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / release['asset']
    if not archive.exists():
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', release['repo']) or not re.fullmatch(r'[\w.-]+', release['tag']):
            raise RuntimeError('The pinned release location is invalid')
        url = f"https://github.com/{release['repo']}/releases/download/{release['tag']}/{release['asset']}"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, suffix='.partial', delete=False) as target:
                temporary = Path(target.name)
                received = 0
                with urllib.request.urlopen(url, timeout=60) as response:
                    while block := response.read(1024 * 1024):
                        received += len(block)
                        if received > release['archive_bytes']:
                            raise RuntimeError('Release download exceeds its pinned size')
                        target.write(block)
            if received != release['archive_bytes'] or digest(temporary) != release['sha256']:
                raise RuntimeError('Release download failed its size or checksum verification')
            temporary.replace(archive)
        except urllib.error.URLError as exc:
            raise RuntimeError('Could not download the public data release. Check your connection and retry make setup.') from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    if digest(archive) != release['sha256']:
        raise RuntimeError('Cached data download failed its checksum. Remove ' + str(archive) + ' and retry; no corpus files were installed.')
    return archive


def setup(root=ROOT, *, archive=None, skip_dependencies=False):
    if sys.version_info < (3, 12):
        raise RuntimeError('Python 3.12 or newer is required. Install it from python.org, then retry.')
    root = Path(root).resolve()
    release = json.loads((root / 'config/data-release.json').read_text())
    python = environment_python(root)
    if not python.is_file():
        print('Creating the project Python environment…', flush=True)
        venv.EnvBuilder(with_pip=True).create(root / '.venv')
    if not skip_dependencies:
        print('Installing the small demo runtime (no LLM weights)…', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '-r', str(root / 'requirements-demo.txt')], check=True, cwd=root)
    print('Loading and verifying the versioned corpus data…', flush=True)
    archive = Path(archive).resolve() if archive else download_data(root, release)
    if digest(archive) != release['sha256']:
        raise RuntimeError('The data archive does not match the pinned release checksum')
    subprocess.run([str(python), '-m', 'oracle.data_bundle', 'install', str(archive),
                    '--root', str(root), '--sha256', release['sha256']], check=True, cwd=root)
    print('Setup complete. Run make run, then open Settings to select your AWS SSO profile and authorize a spending allowance.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-archive', type=Path, help='Use an already downloaded release asset')
    parser.add_argument('--skip-dependencies', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        setup(archive=args.data_archive, skip_dependencies=args.skip_dependencies)
    except (RuntimeError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        print('Setup stopped: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
