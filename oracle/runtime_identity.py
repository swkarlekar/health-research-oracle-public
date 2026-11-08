"""Identify a running checkout without contacting a model or cloud service."""
import hashlib
from pathlib import Path


def runtime_identity(root):
    root = Path(root).resolve()
    paths = [*sorted((root / 'oracle').glob('*.py')),
             *sorted((root / 'oracle/web_assets').glob('*')),
             root / 'config/data-release.json', root / 'config/bedrock-models.json',
             root / 'config/aws-eval-prices.json']
    digest = hashlib.sha256()
    for path in paths:
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return {'project_root': str(root), 'code_and_release_sha256': digest.hexdigest()}
