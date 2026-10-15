"""Read the user's configured credential without logging or embedding its value."""
import os
from pathlib import Path


def configured_token():
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if token:
        return token.strip(), 'environment'
    path = Path(__file__).resolve().parents[2] / '.secrets/github_token'
    if path.exists():
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError('Local credential file must be a regular file with mode 600')
        token = path.read_text().strip()
        if token:
            return token, 'protected local configuration'
    return None, 'anonymous'
