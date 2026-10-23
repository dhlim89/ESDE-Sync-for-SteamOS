"""Offline v0.45 archive, installer, upgrade and rollback validation."""
from pathlib import Path
import ast
import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import zipfile
from unittest.mock import patch

from build_release_v045 import PRODUCT_FILES, ROOT, VERSION, build, validate_source

V044_ARCHIVE = ROOT / "releases/ES-DE-Sync-v0.44.zip"
V044_SHA256 = "3038ce366071fbc76f5f7d84eef52fef422beb4c2639f4564473fcf5750f1302"


def digest(path):
    path = Path(path)
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mode": stat.S_IMODE(path.stat().st_mode),
            "mtime_ns": path.stat().st_mtime_ns}


def extract_checked(archive, destination, version, expected_root=ROOT):
    destination.mkdir(parents=True)
    prefix = f"ES-DE-Sync-v{version}/"
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        assert len(package.namelist()) == len(set(package.namelist())) == len(PRODUCT_FILES)
        assert set(package.namelist()) == {prefix + name for name in PRODUCT_FILES}
        for info in package.infolist():
            assert info.filename.startswith(prefix)
            assert stat.S_IFMT(info.external_attr >> 16) == stat.S_IFREG
            target = (destination / info.filename).resolve()
            assert target.is_relative_to(destination.resolve())
        package.extractall(destination)
        root = destination / prefix.rstrip("/")
        for info in package.infolist():
            name = info.filename.removeprefix(prefix)
            target = root / name
            target.chmod((info.external_attr >> 16) & 0o777)
            if expected_root is not None:
                expected = expected_root / name
                assert hashlib.sha256(target.read_bytes()).hexdigest() == hashlib.sha256(
                    expected.read_bytes()).hexdigest()
                assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(expected.stat().st_mode)
    return root


def version_from_gui(path, expected=VERSION):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    values = [ast.literal_eval(node.value) for node in tree.body
              if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "APP_VERSION"
                      for target in node.targets)]
    assert values == [expected], values
    return values[0]


def update_logic_test(source):
    tree = ast.parse((source / "esde-sync-gui.py").read_text(encoding="utf-8"))
    gui_text = (source / "esde-sync-gui.py").read_text(encoding="utf-8")
    assert "DEFAULT_TEMPLATE_SERIAL" not in gui_text
    funcs = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    ns = {"ast": ast}
    subset = ast.Module(body=[funcs["_version_tuple"]], type_ignores=[])
    exec(compile(subset, "esde-sync-gui.py", "exec"), ns)
    compare = ns["_version_tuple"]
    assert compare("0.44") < compare("0.45")
    assert not compare("0.44") > compare("0.45")
    assert compare("0.46") > compare("0.45")

    # The updater writes into result["available"], so inspect the actual
    # comparison expression rather than assuming a local variable assignment.
    available_comparisons = [node for node in ast.walk(funcs["_fetch_update_info"])
                             if isinstance(node, ast.Compare)
                             and isinstance(node.ops[0], ast.Gt)]
    assert len(available_comparisons) == 1

    def fetch_for(latest):
        contents = {"encoding": "base64", "content": base64.b64encode(
            json.dumps({"version": latest, "url": "fixture", "sha256": "a" * 64}).encode()).decode()}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps(contents).encode()

        fake_urllib = types.SimpleNamespace(request=types.SimpleNamespace(
            Request=lambda *args, **kwargs: object(), urlopen=lambda *args, **kwargs: Response()))
        scope = {"TEST_MODE": False, "APP_VERSION": VERSION, "UPDATE_META_URL": "fixture",
                 "urllib": fake_urllib, "json": json, "base64": base64,
                 "log_update": lambda _message: None, "_version_tuple": compare}
        code = ast.Module(body=[funcs["_fetch_update_info"]], type_ignores=[])
        exec(compile(code, "esde-sync-gui.py", "exec"), scope)
        return scope["_fetch_update_info"]()

    assert fetch_for("0.44")["available"] is False
    assert fetch_for("0.46")["available"] is True


def seed_user_data(home):
    rows = {
        ".config/esde-sync/profiles/DEVICE-A.conf": b'SYSTEMS="gb gbc"\nCUSTOM=keep\n',
        ".config/esde-sync/profiles/DEVICE-A.runtime-map.json":
            b'{"schema_version":1,"device_serial":"DEVICE-A","systems":{}}\n',
        ".config/esde-familyroom-updater/systems.conf": b"gbc\ngb\n",
        ".local/state/esde-sync/esde-sync.log": b"previous sync log\n",
        ".local/state/esde-sync/gui-result.txt": b"previous result\n",
        ".local/state/esde-familyroom-updater/update.log": b"previous dropbox log\n",
        ".local/share/esde-familyroom-updater/custom.db": b"user-owned dropbox state",
        "Dropbox/ES-DE Sync/roms/gb/fixture.gb": b"user-owned library fixture",
        "Emulation/roms/gb/fixture.gb": b"user-owned ROM fixture",
        "ES-DE/gamelists/gb/gamelist.xml": b"<gameList/>",
    }
    for rel, data in rows.items():
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)
    return rows


def protected_manifest(home):
    roots = (".config/esde-sync/profiles", ".config/esde-familyroom-updater",
             ".local/state/esde-sync", ".local/state/esde-familyroom-updater",
             ".local/share/esde-familyroom-updater", "Dropbox", "Emulation", "ES-DE")
    result = {}
    for rel in roots:
        base = home / rel
        paths = [base] if base.is_file() else base.rglob("*") if base.is_dir() else ()
        for path in paths:
            if path.is_file() and path.name not in (
                    "update.lock", "gui-port", "esde-familyroom-gui.py",
                    "esde-familyroom-launch.sh"):
                result[str(path.relative_to(home))] = digest(path)
    return result


def install(source, home, log_path):
    env = os.environ.copy()
    env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_STATE_HOME": str(home / ".local/state"), "PYTHONDONTWRITEBYTECODE": "1"})
    for key in ("ESDE_SYNC_TEST_MODE", "ESDE_SYNC_TEST_ROOT", "ESDE_SYNC_UPDATE_LOCK_FD"):
        env.pop(key, None)
    result = subprocess.run(["bash", str(source / "install.sh")], cwd=home,
                            env=env, capture_output=True, text=True, timeout=45)
    log_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, (result.stdout, result.stderr)


def verify_install(home, source):
    app = home / ".local/share/esde-sync"
    assert version_from_gui(app / "esde-sync-gui.py") == VERSION
    expected_app = {name for name in PRODUCT_FILES
                    if name.startswith(("web/", "services/"))
                    or name in ("esde-sync-gui.py", "esde-sync-engine", "update-helper.py")}
    assert {str(path.relative_to(app)) for path in app.rglob("*") if path.is_file()} == expected_app
    for name in expected_app:
        assert hashlib.sha256((app / name).read_bytes()).hexdigest() == hashlib.sha256(
            (source / name).read_bytes()).hexdigest()
    for name in ("esde-sync-gui.py", "esde-sync-engine", "update-helper.py"):
        assert (app / name).stat().st_mode & 0o111
    launcher = home / ".local/bin/esde-sync"
    assert launcher.stat().st_mode & 0o111
    assert launcher.read_bytes() == (source / "esde-sync").read_bytes()
    menus = list((home / ".local/share/applications").glob("*.desktop"))
    assert len(menus) == 1 and menus[0].name == "esde-sync.desktop"
    expected_menu = (source / "esde-sync.desktop").read_text().replace("@HOME@", str(home))
    assert menus[0].read_text() == expected_menu
    for folder, names in (("esde-sync-hub", ("esde-sync-hub.py", "esde-sync-hub-launch.sh", "esde-sync-action.sh")),
                          ("esde-familyroom-updater", ("esde-familyroom-gui.py", "esde-familyroom-launch.sh"))):
        for name in names:
            assert (home / ".local/share" / folder / name).stat().st_mode & 0o111


def rollback_test(source_v044, source_v045, home, temp):
    seed_user_data(home)
    install(source_v044, home, temp / "install-v044-rollback-seed.log")
    old_data = protected_manifest(home)
    old_app = home / ".local/share/esde-sync"
    old_code = {str(path.relative_to(home)): digest(path)
                for root in (old_app, home / ".local/share/esde-sync-hub",
                             home / ".local/share/esde-familyroom-updater",
                             home / ".local/share/applications", home / ".local/bin/esde-sync")
                for path in ([root] if root.is_file() else root.rglob("*") if root.is_dir() else ())
                if path.is_file()}
    spec = __import__("importlib.util", fromlist=["spec_from_file_location"]).spec_from_file_location(
        "isolated_v045_installer", source_v045 / "install-transaction.py")
    installer = __import__("importlib.util", fromlist=["module_from_spec"]).module_from_spec(spec)
    spec.loader.exec_module(installer)
    real_manifest = installer.manifest

    def fail_after_commit(root):
        if Path(root) == old_app:
            raise RuntimeError("isolated test failure after commit")
        return real_manifest(root)

    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
           "XDG_STATE_HOME": str(home / ".local/state")}
    with patch.dict(os.environ, env), patch.object(installer, "manifest", side_effect=fail_after_commit):
        try:
            installer.install(source_v045)
        except RuntimeError as exc:
            assert "isolated test failure" in str(exc)
        else:
            raise AssertionError("rollback failure injection did not fire")
    assert version_from_gui(old_app / "esde-sync-gui.py", "0.44") == "0.44"
    for rel, before in old_code.items():
        assert digest(home / rel) == before, rel
    assert protected_manifest(home) == old_data
    assert not list((home / ".local/share").glob(".esde-sync-stage-*"))
    assert not list((home / ".local/bin").glob(".esde-sync-backup-*"))


def run(archive):
    validate_source()
    assert V044_ARCHIVE.is_file()
    assert hashlib.sha256(V044_ARCHIVE.read_bytes()).hexdigest() == V044_SHA256
    update_logic_test(ROOT)
    with tempfile.TemporaryDirectory(prefix="esde-v045-release-test-") as temporary:
        temp = Path(temporary)
        built_a, built_b = temp / "rebuild-a.zip", temp / "rebuild-b.zip"
        build(built_a)
        build(built_b)
        assert built_a.read_bytes() == built_b.read_bytes()
        try:
            build(built_a)
        except FileExistsError:
            pass
        else:
            raise AssertionError("builder overwrote an existing archive")
        assert Path(archive).read_bytes() == built_a.read_bytes(), "supplied archive differs from current source"

        source_v045 = extract_checked(Path(archive), temp / "v045-extracted", VERSION)
        profile_test = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tests/test_gui_profiles.py")],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert profile_test.returncode == 0, (profile_test.stdout, profile_test.stderr)
        with zipfile.ZipFile(archive) as package:
            names = package.namelist()
            assert all(not any(part in name.lower() for part in
                               ("tests/", "fixtures/", "runtime-map", "profile", "roms/", "downloaded_media/"))
                       for name in names)
        source_v044 = extract_checked(V044_ARCHIVE, temp / "v044-extracted", "0.44", expected_root=None)

        clean = temp / "fresh-empty-home"
        clean.mkdir()
        install(source_v045, clean, temp / "install-fresh-empty.log")
        verify_install(clean, source_v045)
        assert not (clean / ".config/esde-sync/profiles").exists()
        assert not (clean / "Dropbox").exists() and not (clean / "Emulation").exists()

        home = temp / "fresh-preserved-home"
        home.mkdir()
        seed_user_data(home)
        protected = protected_manifest(home)
        install(source_v045, home, temp / "install-fresh-preserved.log")
        verify_install(home, source_v045)
        after_protected = protected_manifest(home)
        if after_protected != protected:
            changed = {key: (protected.get(key), after_protected.get(key))
                       for key in protected.keys() | after_protected.keys()
                       if protected.get(key) != after_protected.get(key)}
            raise AssertionError(f"fresh install changed protected user data: {changed!r}")

        upgrade = temp / "v044-upgrade-home"
        upgrade.mkdir()
        seed_user_data(upgrade)
        install(source_v044, upgrade, temp / "install-v044-seed.log")
        assert version_from_gui(upgrade / ".local/share/esde-sync/esde-sync-gui.py", "0.44") == "0.44"
        protected = protected_manifest(upgrade)
        install(source_v045, upgrade, temp / "install-v044-to-v045.log")
        verify_install(upgrade, source_v045)
        assert protected_manifest(upgrade) == protected

        rollback_home = temp / "rollback-home"
        rollback_home.mkdir()
        rollback_test(source_v044, source_v045, rollback_home, temp)

        print(json.dumps({"status": "PASS", "actual_install_or_user_data_touched": False,
                          "product_files": len(PRODUCT_FILES),
                          "tests": {"deterministic_rebuild_and_no_overwrite": "PASS",
                                    "zip_whitelist_hashes_permissions_crc": "PASS",
                                    "fresh_install": "PASS",
                                    "fresh_install_user_data_preserved": "PASS",
                                    "v044_to_v045_upgrade": "PASS",
                                    "profile_runtime_map_dropbox_state_logs_preserved": "PASS",
                                    "manufacturer_model_profile_template_selection": "PASS",
                                    "stable_v044_not_offered_as_update": "PASS",
                                    "rollback_restores_v044_and_user_data": "PASS"}}, indent=2))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    run(args.archive)
