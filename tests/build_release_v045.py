"""Build a deterministic, explicit-whitelist v0.45 release archive."""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.45"
ARCHIVE_ROOT = f"ES-DE-Sync-v{VERSION}/"
PRODUCT_FILES = (
    'README.md', 'esde-sync', 'esde-sync-engine', 'esde-sync-gui.py',
    'esde-sync.desktop', 'familyroom/esde-familyroom-gui.py',
    'familyroom/esde-familyroom-launch.sh', 'hub/esde-sync-action.sh',
    'hub/esde-sync-hub-launch.sh', 'hub/esde-sync-hub.py',
    'install-transaction.py', 'install.sh', 'services/__init__.py',
    'services/android.py', 'services/dropbox.py', 'services/jobs.py',
    'update-helper.py', 'web/app.js', 'web/index.html', 'web/style.css',
)
ZIP_TIMESTAMP = (2026, 10, 23, 0, 0, 0)


def validate_source(root=ROOT):
    root = Path(root)
    if len(PRODUCT_FILES) != 20 or len(set(PRODUCT_FILES)) != 20:
        raise ValueError("제품 파일 whitelist가 20개의 고유 경로가 아닙니다.")
    gui = ast.parse((root / "esde-sync-gui.py").read_text(encoding="utf-8"))
    versions = [ast.literal_eval(node.value) for node in gui.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "APP_VERSION"
                        for target in node.targets)]
    if versions != [VERSION]:
        raise ValueError(f"GUI APP_VERSION이 {VERSION}이 아닙니다: {versions!r}")
    if not (root / "README.md").read_text(encoding="utf-8").startswith(
            "# ES-DE Sync for SteamOS v0.45\n"):
        raise ValueError("README 버전 제목이 v0.45와 일치하지 않습니다.")
    for name in PRODUCT_FILES:
        source = root / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"제품 파일이 없거나 일반 파일이 아닙니다: {name}")


def build(target=None, source_root=ROOT):
    source_root = Path(source_root)
    validate_source(source_root)
    target = Path(target) if target is not None else source_root / "releases/ES-DE-Sync-v0.45.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents replacing an existing release or evidence.
    with target.open("xb") as output:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in PRODUCT_FILES:
                source = source_root / name
                mode = stat.S_IMODE(source.stat().st_mode)
                member = zipfile.ZipInfo(ARCHIVE_ROOT + name, ZIP_TIMESTAMP)
                member.create_system = 3
                member.compress_type = zipfile.ZIP_DEFLATED
                member.external_attr = (stat.S_IFREG | mode) << 16
                member.compress_level = 9
                archive.writestr(member, source.read_bytes())
    data = target.read_bytes()
    return {"path": str(target), "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "product_files": len(PRODUCT_FILES)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help="새 ZIP 경로; 기존 파일은 덮어쓰지 않습니다.")
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2))
