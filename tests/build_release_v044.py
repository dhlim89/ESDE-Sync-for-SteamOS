"""Build a new 20-file release without touching historical artifacts."""
from pathlib import Path
import argparse
import hashlib
import json
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_FILES = (
    'README.md', 'esde-sync', 'esde-sync-engine', 'esde-sync-gui.py',
    'esde-sync.desktop', 'familyroom/esde-familyroom-gui.py',
    'familyroom/esde-familyroom-launch.sh', 'hub/esde-sync-action.sh',
    'hub/esde-sync-hub-launch.sh', 'hub/esde-sync-hub.py',
    'install-transaction.py', 'install.sh', 'services/__init__.py',
    'services/android.py', 'services/dropbox.py', 'services/jobs.py',
    'update-helper.py', 'web/app.js', 'web/index.html', 'web/style.css',
)

def build(target=None):
    target = Path(target) if target is not None else ROOT / 'releases/ES-DE-Sync-v0.44.zip'
    target.parent.mkdir(exist_ok=True)
    # Exclusive creation prevents replacement of an existing release.
    with target.open('xb') as output:
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in PRODUCT_FILES:
                source = ROOT / name
                assert source.is_file() and not source.is_symlink(), name
                member = zipfile.ZipInfo('ES-DE-Sync-v0.44/' + name, (2026, 10, 8, 0, 0, 0))
                member.create_system = 3
                member.external_attr = (stat.S_IFREG | stat.S_IMODE(source.stat().st_mode)) << 16
                archive.writestr(member, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return {'path': str(target), 'size': target.stat().st_size,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Output ZIP path; existing files are never overwritten.')
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))
