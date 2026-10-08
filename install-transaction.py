#!/usr/bin/env python3
"""Stage every application asset, then replace or roll back all user launchers."""
import ast
import fcntl
import hashlib
import os
import pathlib
import select
import shutil
import signal
import subprocess
import sys
import tempfile


def manifest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def stop_backend(script):
    victims = []
    try:
        for entry in pathlib.Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                args = (entry / "cmdline").read_bytes().split(b"\0")
                if len(args) > 1 and pathlib.Path(os.fsdecode(args[0])).name.startswith("python") and args[1] == os.fsencode(str(script)):
                    fd = os.pidfd_open(int(entry.name))
                    if (entry / "cmdline").read_bytes().split(b"\0")[:2] == args[:2]:
                        victims.append(fd)
                    else:
                        os.close(fd)
            except (FileNotFoundError, ProcessLookupError):
                continue
        for fd in victims:
            try:
                signal.pidfd_send_signal(fd, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for fd in victims:
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            if not poller.poll(10000):
                raise RuntimeError("기존 backend가 종료되지 않아 설치를 중단합니다.")
    finally:
        for fd in victims:
            os.close(fd)


def reserve_file(parent, prefix):
    fd, name = tempfile.mkstemp(dir=parent, prefix=prefix)
    os.close(fd)
    return pathlib.Path(name)


def install(source):
    home = pathlib.Path.home()
    app = home / ".local/share/esde-sync"
    binary = home / ".local/bin/esde-sync"
    desktop = home / ".local/share/applications/esde-sync.desktop"
    state = pathlib.Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")) / "esde-sync"
    state.mkdir(parents=True, exist_ok=True)
    # Manual installs acquire the same update lock. A helper passes its already
    # locked fd; never unlock that shared description from this subprocess.
    inherited = os.environ.get("ESDE_SYNC_UPDATE_LOCK_FD")
    if inherited is None:
        held_lock = (state / "update.lock").open("a+")
    else:
        held_lock = os.fdopen(os.dup(int(inherited)), "a+")
        actual = os.fstat(held_lock.fileno())
        expected = (state / "update.lock").stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            held_lock.close()
            raise RuntimeError("설치 lock fd가 update.lock과 일치하지 않습니다.")
        os.set_inheritable(int(inherited), False)
    with held_lock:
        try:
            fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("업데이트가 이미 진행 중입니다.")
        return transact(source, app, binary, desktop, state)


def transact(source, app, binary, desktop, state):
    if app.is_symlink() or (app.exists() and not app.is_dir()):
        raise RuntimeError("설치 경로는 실제 디렉터리여야 합니다.")
    for parent in (app.parent, binary.parent, desktop.parent):
        parent.mkdir(parents=True, exist_ok=True)
    root = pathlib.Path(tempfile.mkdtemp(dir=app.parent, prefix=".esde-sync-stage-"))
    staged_app = root / "app"
    staged_app.mkdir()
    staged_bin = reserve_file(binary.parent, ".esde-sync-stage-")
    staged_desktop = reserve_file(desktop.parent, ".esde-sync-stage-")
    backup_bin = reserve_file(binary.parent, ".esde-sync-backup-")
    backup_desktop = reserve_file(desktop.parent, ".esde-sync-backup-")
    backup_bin.unlink()
    backup_desktop.unlink()
    family = app.parent / "esde-familyroom-updater"
    if family.is_symlink() or (family.exists() and not family.is_dir()):
        shutil.rmtree(root)
        for temporary in (staged_bin, staged_desktop):
            temporary.unlink(missing_ok=True)
        raise RuntimeError("Dropbox updater 설치 경로는 실제 디렉터리여야 합니다.")
    family_names = ("esde-familyroom-launch.sh", "esde-familyroom-gui.py")
    staged_family = root / "familyroom"
    family_desktop = desktop.with_name("esde-familyroom-update.desktop")
    family_created = False
    hub = app.parent / "esde-sync-hub"
    hub_desktop = desktop.with_name("esde-sync-hub.desktop")
    staged_hub = root / "hub"
    backup_hub_desktop = root / "previous-hub-desktop"
    if hub.is_symlink() or (hub.exists() and not hub.is_dir()):
        shutil.rmtree(root)
        for temporary in (staged_bin, staged_desktop):
            temporary.unlink(missing_ok=True)
        raise RuntimeError("허브 설치 경로는 실제 디렉터리여야 합니다.")
    records = []
    cleanup = True
    try:
        # Complete staging before stopping a backend or replacing existing files.
        for name in ("esde-sync-engine", "esde-sync-gui.py", "update-helper.py"):
            shutil.copy2(source / name, staged_app / name)
            (staged_app / name).chmod(0o755)
        shutil.copytree(source / "web", staged_app / "web")
        shutil.copytree(source / "services", staged_app / "services", ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for asset in (staged_app / 'services').rglob('*.py'):
            compile(asset.read_text(encoding='utf-8'), str(asset), 'exec')
        shutil.copy2(source / "esde-sync", staged_bin)
        staged_bin.chmod(0o755)
        shutil.copytree(source / "hub", staged_hub)
        for asset in staged_hub.iterdir():
            asset.chmod(0o755)
            if asset.suffix == ".py":
                compile(asset.read_text(), str(asset), "exec")
            elif asset.suffix == ".sh":
                subprocess.run(["bash", "-n", str(asset)], check=True)
        hub_expected = manifest(staged_hub)
        staged_family.mkdir()
        for name in family_names:
            asset = staged_family / name
            shutil.copy2(source / "familyroom" / name, asset)
            asset.chmod(0o755)
            if asset.suffix == ".py":
                compile(asset.read_text(), str(asset), "exec")
            else:
                subprocess.run(["bash", "-n", str(asset)], check=True)
        family_expected = manifest(staged_family)
        if family_expected != {name: hashlib.sha256((source / "familyroom" / name).read_bytes()).hexdigest()
                               for name in family_names}:
            raise RuntimeError("Dropbox updater staging 파일 해시 검증 실패")
        staged_desktop.write_text((source / "esde-sync.desktop").read_text().replace(
            "@HOME@", str(binary.parent.parent.parent)), encoding="utf-8")
        staged_desktop.chmod(0o755)
        gui_tree = ast.parse((staged_app / "esde-sync-gui.py").read_text(encoding="utf-8"))
        compile(gui_tree, str(staged_app / "esde-sync-gui.py"), "exec")
        versions = [ast.literal_eval(n.value) for n in gui_tree.body
                    if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "APP_VERSION" for t in n.targets)]
        if len(versions) != 1 or not isinstance(versions[0], str):
            raise ValueError("APP_VERSION 검증 실패")
        version = versions[0]
        for path in (staged_app / "update-helper.py", staged_bin):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        for name in ("index.html", "app.js", "style.css"):
            if not (staged_app / "web" / name).read_bytes():
                raise ValueError(f"정적 파일 누락 또는 비어 있음: {name}")
        subprocess.run(["bash", "-n", str(source / "install.sh")], check=True)
        subprocess.run(["bash", "-n", str(staged_app / "esde-sync-engine")], check=True)
        node = shutil.which("node")
        if node:
            subprocess.run([node, "--check", str(staged_app / "web/app.js")], check=True)
        expected = manifest(staged_app)
        if expected != {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in expected}:
            raise RuntimeError("staging 파일 해시 검증 실패")
        bin_hash = hashlib.sha256(staged_bin.read_bytes()).hexdigest()
        desktop_hash = hashlib.sha256(staged_desktop.read_bytes()).hexdigest()

        stop_backend(app / "esde-sync-gui.py")
        stop_backend(family / "esde-familyroom-gui.py")
        stop_backend(hub / "esde-sync-hub.py")
        (state / "gui-port").unlink(missing_ok=True)

        def replace(staged, live, backup):
            existed = os.path.lexists(live)
            record = {"staged": staged, "live": live, "backup": backup,
                      "existed": existed, "installed": False}
            records.append(record)
            if existed:
                os.replace(live, backup)
            os.replace(staged, live)
            record["installed"] = True

        # Leave the old tree before renaming/removing it, including nested cwd.
        try:
            current = pathlib.Path.cwd().resolve()
        except OSError:
            os.chdir(pathlib.Path.home())
        else:
            if current.is_relative_to(app.resolve()):
                os.chdir(pathlib.Path.home())

        replace(staged_app, app, root / "previous-app")
        replace(staged_bin, binary, backup_bin)
        replace(staged_hub, hub, root / "previous-hub")
        # Swap only distributed code; preserve unknown DB/log/cache/user assets.
        if not family.exists():
            family.mkdir()
            family_created = True
        for name in family_names:
            replace(staged_family / name, family / name, root / ("previous-" + name))
        replace(staged_desktop, desktop, backup_desktop)
        # Both retired menu entries participate in the same rollback transaction.
        for legacy, backup in ((hub_desktop, backup_hub_desktop),
                               (family_desktop, root / "previous-family-desktop")):
            existed = os.path.lexists(legacy)
            records.append({"staged": root / ("removed-" + legacy.name), "live": legacy,
                            "backup": backup, "existed": existed, "installed": False})
            if existed:
                os.replace(legacy, backup)
        if any(hashlib.sha256((family / name).read_bytes()).hexdigest() != family_expected[name]
               for name in family_names):
            raise RuntimeError("Dropbox updater 설치 파일 해시 검증 실패")
        if manifest(hub) != hub_expected:
            raise RuntimeError("허브 파일 해시 검증 실패")
        if manifest(app) != expected:
            raise RuntimeError("새 설치본 파일 해시 검증 실패")
        if hashlib.sha256(binary.read_bytes()).hexdigest() != bin_hash or hashlib.sha256(desktop.read_bytes()).hexdigest() != desktop_hash:
            raise RuntimeError("launcher 파일 해시 검증 실패")
        print(f"ES-DE Sync v{version} 설치 완료", flush=True)
    except BaseException:
        errors = []
        for record in reversed(records):
            try:
                # Also handle an interrupt immediately after a successful rename.
                if record["installed"] or (not os.path.lexists(record["staged"]) and os.path.lexists(record["live"])):
                    os.replace(record["live"], record["staged"])
                if os.path.lexists(record["backup"]):
                    os.replace(record["backup"], record["live"])
                elif record["existed"] and not os.path.lexists(record["live"]):
                    raise RuntimeError("기존 설치본 backup을 찾을 수 없습니다.")
            except BaseException as error:
                errors.append(f"{record['live']}: {error}; backup={record['backup']}")
        if family_created and family.exists():
            try:
                family.rmdir()
            except OSError as error:
                errors.append(f"{family}: {error}")
        if errors:
            cleanup = False  # Keep all recovery material if rollback itself fails.
            print("ROLLBACK ERROR: " + "; ".join(errors), file=sys.stderr, flush=True)
        else:
            print("INSTALL FAILED: 이전 설치본과 launcher를 유지/복원했습니다.", file=sys.stderr, flush=True)
        raise
    finally:
        if cleanup:
            shutil.rmtree(root, ignore_errors=True)
            for path in (staged_bin, staged_desktop, backup_bin, backup_desktop):
                path.unlink(missing_ok=True)


def main():
    def interrupted(signum, frame):
        raise RuntimeError(f"설치 중단 신호: {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        install(pathlib.Path(sys.argv[1]).resolve())
    except BaseException as error:
        print(f"INSTALL ERROR: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
