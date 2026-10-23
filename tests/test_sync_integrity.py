"""Isolated protocol and ROM integrity tests. Never contacts an Android device."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "esde-sync-engine"

FAKE_ADB = r'''#!/usr/bin/env python3
import hashlib, json, os, re, shlex, shutil, subprocess, sys
from pathlib import Path

args = sys.argv[1:]
root = Path(os.environ["FAKE_ADB_ROOT"])
serial = args[1] if len(args) > 1 and args[0] == "-s" else "default"
args = args[2:] if len(args) > 1 and sys.argv[1] == "-s" else args
mode = os.environ.get("FAKE_ADB_MODE", "")
log = Path(os.environ["FAKE_ADB_LOG"])

def record(action, path="", size=0):
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"serial": serial, "action": action, "path": path, "size": size}) + "\n")

def remote(path):
    return root / serial / path.lstrip("/")

def assignment(script, name="p"):
    lexer = shlex.shlex(script, posix=True)
    lexer.whitespace_split = True
    for word in lexer:
        if word.startswith(name + "="):
            return word.split("=", 1)[1]
    raise ValueError("missing path assignment")

def emit(data):
    sys.stdout.buffer.write(data)

if args and args[0] == "devices":
    print("List of devices attached")
    print(serial + "\tdevice")
    raise SystemExit(0)
disconnected = root / serial / ".disconnected"
if args and args[0] == "get-state":
    if mode == "index_disconnect" or disconnected.exists():
        print("unknown", file=sys.stderr)
        raise SystemExit(1)
    print("device")
    raise SystemExit(0)
if disconnected.exists():
    record("disconnected_attempt", " ".join(args[:1]))
    raise SystemExit(1)
if args and args[0] == "shell":
    script = " ".join(args[1:])
    if "getprop" in script:
        print("Fixture")
    elif "stat -c %s" in script:
        tokens = re.findall(r"'([^']*)'", script)
        p = remote(tokens[0]) if tokens else None
        if p and p.is_file(): print(p.stat().st_size)
        else: print("stat: not found", file=sys.stderr)
    elif "mkdir -p" in script:
        # Cleanup glob and mkdir are scoped to the isolated remote fixture.
        for token in re.findall(r"'([^']*)'", script):
            if token.endswith("esde-sync-part-"):
                prefix = remote(token)
                prefix.parent.mkdir(parents=True, exist_ok=True)
                for stale in prefix.parent.glob(prefix.name + "*"):
                    if stale.is_file(): stale.unlink()
            elif token.startswith("/"):
                remote(token).mkdir(parents=True, exist_ok=True)
        raise SystemExit(0)
    elif "rm -f" in script:
        for token in shlex.split(script):
            if token.startswith("/"):
                p = remote(token)
                record("delete", token)
                if p.is_file(): p.unlink()
        raise SystemExit(0)
    raise SystemExit(0)
if args and args[0] == "push":
    src, dst = Path(args[1]), args[2]
    target = remote(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    record("push", dst, src.stat().st_size)
    if mode in ("push_fail", "rom_push_enospc"):
        record("inject:" + mode, dst, src.stat().st_size)
        target.write_bytes(src.read_bytes()[:max(1, src.stat().st_size // 2)])
        if mode == "rom_push_enospc": print("No space left on device", file=sys.stderr)
        raise SystemExit(1)
    if mode == "push_corrupt":
        data = src.read_bytes()
        target.write_bytes(data[:-1] + bytes([data[-1] ^ 1]) if data else b"x")
    else:
        shutil.copyfile(src, target)
    raise SystemExit(0)
if args and args[0] == "exec-out":
    script = args[1] if len(args) > 1 else ""
    if "ESDE_INDEX_V2" in script and os.environ.get("FAKE_ADB_INDEX_SCRIPT"):
        Path(os.environ["FAKE_ADB_INDEX_SCRIPT"]).write_text(script, encoding="utf-8")
    if "ESDE_ROM_PENDING_V1" in script:
        p = remote(assignment(script))
        record("rom_pending", assignment(script))
        if mode == "rom_pending_bad": emit(b"ESDE_ROM_PENDING_V1 BEGIN\nP 100\nshort"); raise SystemExit(0)
        if not p.exists(): emit(b"ESDE_ROM_PENDING_V1 EMPTY\n")
        else:
            data = [b"ESDE_ROM_PENDING_V1 BEGIN\n"]
            for item in sorted(p.rglob("*")):
                if item.name.startswith(".esde-sync-") or ".esde-sync-part-" in item.name:
                    path = os.fsencode(assignment(script).rstrip("/") + "/" + str(item.relative_to(p)))
                    data.extend((f"P {len(path)}\n".encode(), path))
            emit(b"".join(data) + b"ESDE_ROM_PENDING_V1 END\n")
        raise SystemExit(0)
    if any(marker in script for marker in ("ESDE_ROM_STATE_V1", "ESDE_ROM_SPACE_V1", "ESDE_ROM_OP_V1")):
        script_dir = root / serial / ".scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        captured = script_dir / f"{len(list(script_dir.iterdir())):04d}.sh"
        captured.write_text(script, encoding="utf-8")
        syntax = subprocess.run(["sh", "-n", str(captured)], capture_output=True)
        if syntax.returncode:
            emit(b"SHELL SYNTAX ERROR\n")
            raise SystemExit(0)
        names = ("p", "dir", "txn", "backup", "part", "restore", "legacy")
        paths = {name: remote(assignment(script, name)) for name in names}
        action = assignment(script, "action") if "ESDE_ROM_OP_V1" in script else "probe"
        record("rom_" + action.lower(), assignment(script))
        if mode in ("rom_execute_shell", "rom_shell_replace_fail", "rom_shell_restore_fail"):
            remapped = script
            for name in names:
                value = assignment(script, name)
                original = "'" + value.replace("'", "'\\''") + "'"
                remapped = remapped.replace(name + "=" + original, name + "=" + shlex.quote(str(paths[name])), 1)
            if mode == "rom_shell_replace_fail" and action == "REPLACE":
                record("inject:" + mode, assignment(script), 1)
                remapped = 'mv() { printf CORRUPT > "$3"; return 1; }\n' + remapped
            elif mode == "rom_shell_restore_fail" and action == "REPLACE":
                record("fixture_corrupt_after_replace", assignment(script), 1)
                remapped = ('mv() { command mv "$@" || return $?; '
                            'printf CORRUPT > "$3"; return 0; }\n' + remapped)
            elif mode == "rom_shell_restore_fail" and action == "RESTORE":
                record("inject:" + mode, assignment(script), 1)
                remapped = 'mv() { command rm -f -- "$3"; return 1; }\n' + remapped
            run = subprocess.run(["sh", "-c", remapped], capture_output=True)
            emit(run.stdout)
            sys.stderr.buffer.write(run.stderr)
            raise SystemExit(run.returncode)
        if "ESDE_ROM_STATE_V1" in script:
            pending = assignment(script, "check_pending") == "1"
            busy = any(paths[n].exists() or paths[n].is_symlink() for n in ("txn", "backup", "part", "restore"))
            busy = busy or any(paths["legacy"].parent.glob(paths["legacy"].name + "*"))
            p = paths["p"]
            if pending and busy: emit(b"ESDE_ROM_STATE_V1 BUSY\n")
            elif p.is_symlink() or (p.exists() and not p.is_file()): emit(b"ESDE_ROM_STATE_V1 ERROR\n")
            elif p.is_file(): emit(f"ESDE_ROM_STATE_V1 FILE {p.stat().st_size}\n".encode())
            else: emit(b"ESDE_ROM_STATE_V1 MISSING\n")
            raise SystemExit(0)
        if "ESDE_ROM_SPACE_V1" in script:
            if mode == "rom_space_low": emit(b"ESDE_ROM_SPACE_V1 0 4096\n")
            elif mode == "rom_space_bad": emit(b"df: failed\n")
            else: emit(b"ESDE_ROM_SPACE_V1 100000000 4096\n")
            raise SystemExit(0)
        p, backup, part, restore, txn = (paths[x] for x in ("p", "backup", "part", "restore", "txn"))
        ok = True
        if action == "CLAIM":
            p.parent.mkdir(parents=True, exist_ok=True)
            try: txn.mkdir()
            except FileExistsError: ok = False
            if ok and any(x.exists() for x in (backup, part, restore)): ok = False
            if ok:
                (txn / "old.meta").write_text(assignment(script, "meta") + "\n")
                (txn / "target.path").write_text(assignment(script))
        elif action == "BACKUP":
            if backup.exists(): ok = False
            elif mode in ("rom_backup_fail", "rom_backup_enospc"): backup.write_bytes(p.read_bytes()[:1]); ok = False
            else:
                shutil.copyfile(p, backup)
                if mode == "rom_backup_corrupt":
                    b = backup.read_bytes(); backup.write_bytes(bytes([b[0] ^ 1]) + b[1:])
        elif action == "RESTORE_COPY":
            if restore.exists(): ok = False
            elif mode == "rom_restore_copy_fail": restore.write_bytes(b"partial"); ok = False
            else: shutil.copyfile(backup, restore)
        elif action in ("REPLACE", "RESTORE"):
            src = part if action == "REPLACE" else restore
            destructive_modes = ("rom_replace_fail", "rom_restore_copy_fail", "rom_disconnect_restore")
            if action == "REPLACE" and mode in destructive_modes:
                if mode == "rom_replace_fail": record("inject:" + mode, assignment(script), 1)
                p.unlink(missing_ok=True); ok = False
            elif action == "REPLACE" and mode == "rename_fail": ok = False
            elif action == "RESTORE" and mode == "rom_restore_fail":
                record("inject:" + mode, assignment(script), 1)
                p.unlink(missing_ok=True); ok = False
            elif src.exists():
                os.replace(src, p)
                if action == "REPLACE" and mode == "rom_restore_fail":
                    record("fixture_corrupt_after_replace", assignment(script), 1)
                    p.write_bytes(b"fixture-corruption")
                if action == "REPLACE" and mode == "rom_replace_corrupt":
                    b = p.read_bytes(); p.write_bytes(bytes([b[0] ^ 1]) + b[1:])
            else: ok = False
            if (action == "REPLACE" and mode == "rom_disconnect_replace") or (action == "RESTORE" and mode == "rom_disconnect_restore"):
                disconnected.touch()
                raise SystemExit(1)
        elif action == "CLEAN":
            if mode == "rom_clean_fail": ok = False
            else:
                for f in (backup, part, restore): f.unlink(missing_ok=True)
                for f in (txn / "old.meta", txn / "target.path"): f.unlink(missing_ok=True)
                txn.rmdir()
        if action == "REPLACE" and mode == "rename_reply_lost": emit(b"lost adb reply\n")
        else: emit(f"ESDE_ROM_OP_V1 {action} {'OK' if ok else 'ERROR'}\n".encode())
        raise SystemExit(0)
    if "ESDE_GL_V1" in script:
        p = remote(assignment(script))
        if mode == "gamelist_error": emit(b"shell transport error\n")
        elif mode == "gamelist_truncated": emit(b"ESDE_GL_V1 FILE 20\n<gameList/>")
        elif mode == "gamelist_mismatch": emit(b"ESDE_GL_V1 FILE 4\nabc\nESDE_GL_V1_END\n")
        elif not p.exists(): emit(b"ESDE_GL_V1 MISSING\n")
        elif p.stat().st_size == 0: emit(b"ESDE_GL_V1 EMPTY\n")
        else:
            data = p.read_bytes()
            emit(f"ESDE_GL_V1 FILE {len(data)}\n".encode() + data + b"\nESDE_GL_V1_END\n")
        record("gamelist", str(p), p.stat().st_size if p.exists() else -1)
        # Deliberately return success for remote errors to model adb status loss.
        raise SystemExit(0)
    if "ESDE_HASH_V1" in script:
        p = remote(assignment(script))
        record("device_hash", str(p), p.stat().st_size if p.exists() else -1)
        if mode in ("hash_error", "hash_error_stream_fail"):
            emit(b"ESDE_HASH_V1 ERROR\n")
        elif mode == "hash_unsupported":
            emit(b"ESDE_HASH_V1 UNSUPPORTED\n")
        elif mode == "hash_malformed":
            emit(b"ESDE_HASH_V1 FILE 3 xyz 3\n")
        elif not p.exists(): emit(b"ESDE_HASH_V1 ERROR\n")
        else:
            data = p.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            emit(f"ESDE_HASH_V1 FILE {len(data)} {digest} {len(data)}\n".encode())
        raise SystemExit(0)
    if "ESDE_INDEX_V2" in script:
        p = remote(assignment(script))
        record("index", str(p))
        if mode == "index_disconnect": raise SystemExit(1)
        if mode == "index_error" or (mode == "index_error_gb" and str(p).endswith("/gb")): emit(b"ESDE_INDEX_V2 ERROR\n")
        elif mode == "index_partial": emit(b"ESDE_INDEX_V2 BEGIN\nF 12 4\nshort")
        elif mode == "index_literal_tab": emit(b"ESDE_INDEX_V1 BEGIN\n/storage/emulated/0/roms/gb\\t1\nESDE_INDEX_V1 END\n")
        elif not p.exists(): emit(b"ESDE_INDEX_V2 MISSING\n")
        else:
            rows = [b"ESDE_INDEX_V2 BEGIN\n"]
            for item in sorted(p.rglob("*")):
                if item.is_file():
                    remote_name = os.fsencode(assignment(script).rstrip("/") + "/") + os.fsencode(str(item.relative_to(p)))
                    rows.extend((f"F {len(remote_name)} {item.stat().st_size}\n".encode(), remote_name))
            rows.append(b"ESDE_INDEX_V2 END\n")
            emit(b"".join(rows))
        raise SystemExit(0)
    if "ESDE_RENAME_V1" in script:
        if mode == "rename_fail":
            emit(b"ESDE_RENAME_V1 ERROR\n")
        else:
            line = next(line for line in script.splitlines() if "mv -f" in line)
            words = shlex.split(line)
            src, dst = remote(words[3]), remote(words[4])
            if not src.exists(): emit(b"ESDE_RENAME_V1 ERROR\n")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dst)
                record("rename", str(dst), dst.stat().st_size)
                emit(b"lost adb reply\n" if mode == "rename_reply_lost" else b"ESDE_RENAME_V1 OK\n")
        raise SystemExit(0)
    if script.lstrip().startswith("cat "):
        line = script.strip().splitlines()[0]
        p = remote(shlex.split(line)[1])
        record("stream", str(p), p.stat().st_size if p.exists() else -1)
        if mode == "hash_error_stream_fail": emit(b"partial")
        elif p.exists():
            with p.open("rb") as f: shutil.copyfileobj(f, sys.stdout.buffer, 1024 * 1024)
        raise SystemExit(0)
    raise SystemExit(0)
raise SystemExit(0)
'''


class Fixture:
    def __init__(self, base: Path):
        self.base = base
        self.home = base / "home"
        self.home.mkdir()
        self.remote_root = base / "remote"
        self.log = base / "adb.jsonl"
        self.fake = base / "fake-adb.py"
        self.fake.write_text(FAKE_ADB, encoding="utf-8")
        self.bin = base / "bin"
        self.bin.mkdir()
        wrapper = self.bin / "adb"
        wrapper.write_text('#!/bin/sh\nexec python3 "$FAKE_ADB_SCRIPT" "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
        self.temp = base / "tmp"
        self.temp.mkdir()

    def env(self, serial="device-A", mode=""):
        return {
            **os.environ,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.home / ".state"),
            "TMPDIR": str(self.temp),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "ESDE_SYNC_LIBRARY_ONLY": "1",
            "FAKE_ADB_SCRIPT": str(self.fake),
            "FAKE_ADB_ROOT": str(self.remote_root),
            "FAKE_ADB_LOG": str(self.log),
            "FAKE_ADB_MODE": mode,
            "FAKE_ADB_INDEX_SCRIPT": str(self.base / "captured-index-command.sh"),
        }

    def remote(self, path, serial="device-A"):
        target = self.remote_root / serial / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def bash(self, code, *args, serial="device-A", mode=""):
        return subprocess.run(
            ["bash", "-c", 'source "$ENGINE"; ' + code, "test", *map(str, args)],
            env={**self.env(serial, mode), "ENGINE": str(ENGINE)},
            capture_output=True,
            text=True,
            timeout=30,
        )


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_gamelist(fx: Fixture):
    remote_path = "/storage/emulated/0/ES-DE/gamelists/gb/gamelist.xml"
    output = fx.temp / "remote.xml"
    rp = fx.remote(remote_path)
    rp.unlink(missing_ok=True)
    result = fx.bash('read_remote_gamelist "$1" "$2" "$3"', "device-A", remote_path, output)
    check(result.returncode == 0 and "MISSING" not in result.stdout and not output.exists(), "missing gamelist must be MISSING success")
    master = fx.temp / "first-sync-master.xml"
    transformed = fx.temp / "first-sync-output.xml"
    master.write_text('<gameList><game><path>./First.gb</path><name>First</name></game></gameList>', encoding="utf-8")
    first = fx.bash('transform_gamelist "$1" "$2" "" "$3" gb "$4" ""', master, transformed, fx.temp / "first-count", fx.temp / "first-removed")
    check(first.returncode == 0 and "First.gb" in transformed.read_text(), "missing remote gamelist must allow first sync")

    good = b'<gameList><game><path>./Game.gb</path><playcount>8</playcount></game></gameList>'
    rp.write_bytes(good)
    result = fx.bash('read_remote_gamelist "$1" "$2" "$3"', "device-A", remote_path, output)
    check(result.returncode == 0 and output.read_bytes() == good, "valid FILE response was not preserved")

    rp.write_bytes(b"")
    result = fx.bash('read_remote_gamelist "$1" "$2" "$3"', "device-A", remote_path, output)
    check(result.returncode != 0 and not output.exists(), "zero byte gamelist must fail")
    rp.write_bytes(b"<gameList><broken>")
    result = fx.bash('read_remote_gamelist "$1" "$2" "$3"', "device-A", remote_path, output)
    check(result.returncode == 0, "transport should preserve bytes for XML validation")
    result = fx.bash('transform_gamelist "$1" "$2" "" "$3" gb "$4" "$5"', rp, fx.temp / "transformed.xml", fx.temp / "count", fx.temp / "removed", output)
    check(result.returncode != 0, "damaged XML must fail transformation")

    for mode in ("gamelist_error", "gamelist_truncated", "gamelist_mismatch"):
        result = fx.bash('read_remote_gamelist "$1" "$2" "$3"', "device-A", remote_path, output, mode=mode)
        check(result.returncode != 0 and not output.exists(), f"{mode} must fail closed")


def test_hash_and_atomic_rom(fx: Fixture):
    remote_path = "/storage/emulated/0/roms/gb/Game.gb"
    old, new = b"old-known-good-rom", b"new-rom-same-size!"
    check(len(old) == len(new), "fixture replacement must keep size")
    rp = fx.remote(remote_path)
    rp.write_bytes(old)
    local = fx.base / "Game.gb"
    local.write_bytes(old)
    size = str(len(old))
    same = fx.bash('remote_sha256 "$1" "$2" "$3"', "device-A", remote_path, size)
    check(same.returncode == 0 and same.stdout.strip() == hashlib.sha256(old).hexdigest(), "same-content remote hash failed")
    decision = fx.bash('rom_compare "$1" "$2" "$3" "$4"', "device-A", local, remote_path, size)
    check(decision.returncode == 0, "same-name same-size same-content ROM should be skipped")
    rp.write_bytes(new)
    other = fx.bash('remote_sha256 "$1" "$2" "$3"', "device-A", remote_path, size)
    check(other.returncode == 0 and other.stdout.strip() != hashlib.sha256(old).hexdigest(), "same-size content change not detected")
    decision = fx.bash('rom_compare "$1" "$2" "$3" "$4"', "device-A", local, remote_path, size)
    check(decision.returncode == 1, "same-name same-size changed ROM should be updated")
    decision = fx.bash('rom_compare "$1" "$2" "$3" "$4"', "device-A", local, remote_path, str(len(new) + 1))
    check(decision.returncode == 1, "different-size ROM should be updated")
    decision = fx.bash('rom_compare "$1" "$2" "$3" ""', "device-A", local, "/storage/emulated/0/roms/gb/New.gb")
    check(decision.returncode == 1, "missing ROM path should be uploaded")
    rp.write_bytes(old)
    fallback = fx.bash('remote_sha256 "$1" "$2" "$3"', "device-A", remote_path, size, mode="hash_unsupported")
    check(fallback.returncode == 0 and fallback.stdout.strip() == hashlib.sha256(old).hexdigest(), "stream fallback failed")
    failed = fx.bash('remote_sha256 "$1" "$2" "$3"', "device-A", remote_path, size, mode="hash_error_stream_fail")
    check(failed.returncode != 0, "unverifiable hash must fail")
    failed_decision = fx.bash('rom_compare "$1" "$2" "$3" "$4"', "device-A", local, remote_path, size, mode="hash_error_stream_fail")
    check(failed_decision.returncode == 2, "failed remote hash must not be treated as same or update")
    missing_local = fx.bash('rom_compare "$1" "$2" "$3" "$4"', "device-A", fx.base / "missing.rom", remote_path, size)
    check(missing_local.returncode == 2, "failed local hash must stop sync")
    malformed_fallback = fx.bash('remote_sha256 "$1" "$2" "$3"', "device-A", remote_path, size, mode="hash_malformed")
    check(malformed_fallback.returncode == 0 and malformed_fallback.stdout.strip() == hashlib.sha256(old).hexdigest(), "malformed device hash did not use checked fallback")

    local.write_bytes(new)
    new_hash = hashlib.sha256(new).hexdigest()
    result = fx.bash('push_rom_verified "$1" "$2" "$3" "$4" "$5"', "device-A", local, remote_path, len(new), new_hash)
    check(result.returncode == 0 and rp.read_bytes() == new, "verified backup + replacement failed")
    check(not list(rp.parent.glob(".esde-sync-*")), "successful transaction left artifacts")
    stale = rp.parent / ".Game.gb.esde-sync-part-interrupted"
    stale.write_bytes(b"partial from a killed sync")
    result = fx.bash('push_rom_verified "$1" "$2" "$3" "$4" "$5"', "device-A", local, remote_path, len(new), new_hash)
    check(result.returncode != 0 and stale.read_bytes() == b"partial from a killed sync", "retry must preserve and block on legacy temporary data")
    stale.unlink()  # Fixture-only manual resolution, never product auto-cleanup.
    local.write_bytes(old)
    old_hash = hashlib.sha256(old).hexdigest()
    result = fx.bash('push_rom_verified "$1" "$2" "$3" "$4" "$5"', "device-A", local, remote_path, len(old), old_hash, mode="rename_reply_lost")
    check(result.returncode == 0 and rp.read_bytes() == old, "lost rename reply must be resolved by final hash")

    # Missing, size-changed and rename/delete cases follow the existing path policy.
    local.write_bytes(new)
    other_path = "/storage/emulated/0/roms/gb/NewName.gb"
    result = fx.bash('push_rom_verified "$1" "$2" "$3" "$4" "$5"', "device-A", local, other_path, len(new), new_hash)
    check(result.returncode == 0 and fx.remote(other_path).read_bytes() == new, "new path transfer failed")
    fx.remote("/storage/emulated/0/roms/gb/OldName.gb").write_bytes(b"deleted")
    result = fx.bash('delete_remote_file "$1" "$2"', "device-A", "/storage/emulated/0/roms/gb/OldName.gb")
    check(result.returncode == 0 and not fx.remote("/storage/emulated/0/roms/gb/OldName.gb").exists(), "existing delete policy failed")

    # Device-specific state is isolated by serial.
    rp.write_bytes(new)
    fx.remote(remote_path, "device-B").write_bytes(old)
    a = fx.bash('remote_sha256 "$1" "$2" "$3"', "device-A", remote_path, len(new))
    b = fx.bash('remote_sha256 "$1" "$2" "$3"', "device-B", remote_path, len(old), serial="device-B")
    check(a.returncode == 0 and b.returncode == 0 and a.stdout != b.stdout, "device state was not isolated")


def test_rom_recovery(fx: Fixture):
    old, new = b"old-known-good-ROM", b"new-verified-ROM!!"
    cases = fx.base / "recovery-cases"
    cases.mkdir()
    results = {}

    def scenario(name, mode="", existing=True, filename="Game.gb"):
        base = cases / name; base.mkdir()
        f = Fixture(base)
        path = "/storage/emulated/0/roms/gb/" + filename
        final = f.remote(path)
        if existing: final.write_bytes(old)
        local = base / "new.rom"; local.write_bytes(new)
        code = ('push_rom_verified "$1" "$2" "$3" "$4" "$5"; rc=$?; '
                'printf "RESULT=%s STATUS=%s RETAIN=%s\\n" "$rc" "$ROM_REPLACE_STATUS" "$ROM_RECOVERY_REQUIRED"; '
                'printf "%s\\n" "$ROM_RECOVERY_REPORT"; exit "$rc"')
        run = f.bash(code, "device-A", local, path, len(new), hashlib.sha256(new).hexdigest(), mode=mode)
        key = hashlib.sha256(path.encode()).hexdigest()
        artifacts = {ext: final.parent / f".esde-sync-{key}.{ext}" for ext in ("backup", "part", "restore", "txn")}
        events = [json.loads(line) for line in f.log.read_text().splitlines()]
        return f, run, final, artifacts, events

    for mode in ("", "rom_execute_shell", "rename_reply_lost"):
        f, run, final, a, events = scenario("success-" + (mode or "normal"), mode)
        check(run.returncode == 0 and "STATUS=SUCCESS" in run.stdout and final.read_bytes() == new, f"{mode}: final commit failed")
        check(not any(x.exists() for x in a.values()), f"{mode}: successful verified commit left backup/part/lock")
        actions = [e["action"] for e in events]
        check(actions.index("rom_backup") < actions.index("push") < actions.index("rom_replace") < actions.index("rom_clean"), "backup/push/replace/cleanup ordering regressed")
        results[mode or "backup_and_commit"] = "PASS"

    for mode, status in (("rom_backup_fail", "FAILED_PRESERVED"), ("rom_backup_enospc", "FAILED_PRESERVED"), ("rom_backup_corrupt", "FAILED_PRESERVED"),
                         ("push_fail", "FAILED_PRESERVED"), ("rom_push_enospc", "FAILED_PRESERVED"), ("push_corrupt", "FAILED_PRESERVED"),
                         ("rename_fail", "FAILED_PRESERVED"), ("rom_replace_fail", "FAILED_RESTORED"),
                         ("rom_replace_corrupt", "FAILED_RESTORED"), ("rom_restore_fail", "RECOVERY_REQUIRED"),
                         ("rom_restore_copy_fail", "RECOVERY_REQUIRED"), ("rom_shell_replace_fail", "FAILED_RESTORED"),
                         ("rom_shell_restore_fail", "RECOVERY_REQUIRED"), ("rom_disconnect_replace", "UNKNOWN"),
                         ("rom_disconnect_restore", "UNKNOWN"), ("rom_clean_fail", "COMMITTED_CLEANUP_FAILED")):
        f, run, final, a, events = scenario(mode, mode)
        check(run.returncode != 0 and f"STATUS={status}" in run.stdout, f"{mode}: wrong failure status: {run.stdout} {run.stderr}")
        if mode in ("push_fail", "rom_push_enospc"):
            injected = [e for e in events if e["action"] == "inject:" + mode]
            key = hashlib.sha256(b"/storage/emulated/0/roms/gb/Game.gb").hexdigest()
            expected_part = f"/storage/emulated/0/roms/gb/.esde-sync-{key}.part"
            check(len(injected) == 1 and injected[0]["path"] == expected_part, f"{mode}: injection did not target exactly one temporary ROM push: {injected}")
            check(not any(e["action"].startswith("inject:") and e["action"] != "inject:" + mode for e in events), f"{mode}: unexpected injection recorded")
        if mode == "rom_restore_fail":
            injected = [e for e in events if e["action"] == "inject:" + mode]
            setup = [e for e in events if e["action"] == "fixture_corrupt_after_replace"]
            actions = [e["action"] for e in events]
            check(len(injected) == 1 and injected[0]["path"] == "/storage/emulated/0/roms/gb/Game.gb", "restore failure was not injected exactly once at RESTORE")
            check(len(setup) == 1 and actions.index("rom_replace") < actions.index("rom_restore_copy") < actions.index("inject:" + mode), "restore failure did not enter the real restore path first")
        if mode == "rom_replace_fail":
            injected = [e for e in events if e["action"] == "inject:" + mode]
            check(len(injected) == 1 and injected[0]["path"] == "/storage/emulated/0/roms/gb/Game.gb", "replace failure was not injected exactly once at REPLACE")
        if mode == "rom_shell_replace_fail":
            injected = [e for e in events if e["action"] == "inject:" + mode]
            check(len(injected) == 1 and [e["action"] for e in events].index("rom_replace") < [e["action"] for e in events].index("rom_restore"), "shell replace failure did not reach backup restoration")
        if mode == "rom_shell_restore_fail":
            injected = [e for e in events if e["action"] == "inject:" + mode]
            setup = [e for e in events if e["action"] == "fixture_corrupt_after_replace"]
            actions = [e["action"] for e in events]
            check(len(injected) == 1 and len(setup) == 1 and actions.index("rom_replace") < actions.index("rom_restore_copy") < actions.index("inject:" + mode), "shell restore failure did not enter restoration and inject only at RESTORE")
        if mode in ("rom_restore_fail", "rom_shell_restore_fail"):
            check(a["backup"].read_bytes() == old and a["restore"].read_bytes() == old, f"{mode}: failed restore did not preserve verified backup and restore copy")
        injection_events = [e for e in events if e["action"].startswith("inject:")]
        expected_injection_names = {"inject:" + mode} if mode in ("push_fail", "rom_push_enospc", "rom_replace_fail", "rom_restore_fail", "rom_shell_replace_fail", "rom_shell_restore_fail") else set()
        check(all(e["action"] in expected_injection_names for e in injection_events), f"{mode}: unexpected command was intercepted: {injection_events}")
        check(a["backup"].exists() and a["txn"].is_dir(), f"{mode}: recovery artifacts were removed")
        meta = (a["txn"] / "old.meta").read_text().split()
        check(meta == ["ESDE_ROM_RECOVERY_V1", str(len(old)), hashlib.sha256(old).hexdigest(), str(len(new)), hashlib.sha256(new).hexdigest()], "durable recovery metadata lacks original/new verification values")
        expected_target = os.fsencode(final) if mode.startswith("rom_shell_") else b"/storage/emulated/0/roms/gb/Game.gb"
        check((a["txn"] / "target.path").read_bytes() == expected_target, "recovery metadata lost exact target path bytes")
        if mode not in ("rom_backup_fail", "rom_backup_enospc", "rom_backup_corrupt"):
            check(a["backup"].read_bytes() == old, f"{mode}: verified original backup was consumed or corrupted")
        if status in ("FAILED_PRESERVED", "FAILED_RESTORED"):
            check(final.read_bytes() == old, f"{mode}: old final was not preserved/restored")
        if mode in ("rom_restore_fail", "rom_shell_restore_fail"):
            check(not final.exists() and a["restore"].read_bytes() == old, "destructive restore failure must retain both backup and restore copy")
        if mode == "rom_restore_copy_fail":
            check(not final.exists() and a["restore"].read_bytes() == b"partial", "partial restore copy must remain, with authoritative backup intact")
        if mode == "rom_disconnect_replace":
            check(final.read_bytes() == new, "fixture did not simulate completed replace before disconnect")
        if mode == "rom_disconnect_restore":
            check(final.read_bytes() == old, "fixture did not simulate completed restore before disconnect")
        if mode.startswith("rom_disconnect"):
            check(not any(e["action"] == "rom_clean" for e in events), "disconnect must not trigger cleanup")
        if mode in ("rom_backup_fail", "rom_backup_enospc", "rom_backup_corrupt"):
            check(not any(e["action"] == "push" for e in events), "unverified backup must block new ROM transfer")
        if mode in ("push_fail", "rom_push_enospc", "push_corrupt"):
            check(not any(e["action"] == "rom_replace" for e in events), "failed temp upload/verification must block replacement")
        check(str(a["backup"]) not in run.stdout, "report must use device paths, not fake host paths")
        check(".backup" in run.stdout and "old.meta" in run.stdout, "manual recovery locations missing from report")
        # A new run must preserve every incomplete artifact, even if final is the new ROM.
        snapshot = {p: p.read_bytes() for p in final.parent.rglob("*") if p.is_file()}
        (f.remote_root / "device-A" / ".disconnected").unlink(missing_ok=True)  # Simulated reconnect.
        again = f.bash('push_rom_verified "$1" "$2" "$3" "$4" "$5"', "device-A", f.base / "new.rom", "/storage/emulated/0/roms/gb/Game.gb", len(new), hashlib.sha256(new).hexdigest())
        check(again.returncode != 0 and all(p.exists() and p.read_bytes() == data for p, data in snapshot.items()), f"{mode}: retry overwrote/deleted unfinished recovery files")
        results[mode] = "PASS"

    for mode in ("rom_space_low", "rom_space_bad"):
        f, run, final, a, events = scenario(mode, mode)
        check(run.returncode != 0 and final.read_bytes() == old and not any(x.exists() for x in a.values()), "insufficient/unverifiable space must fail before backup/transfer")
        check(not any(e["action"] in ("rom_claim", "push", "rom_replace") for e in events), "space failure allowed a remote mutation")
        results[mode] = "PASS"

    f, run, final, a, events = scenario("new-rom", existing=False)
    check(run.returncode == 0 and final.read_bytes() == new, "new ROM transfer failed")
    check(not any(e["action"] == "rom_backup" for e in events), "new ROM should not create an old-ROM backup")
    f, run, final, a, events = scenario("new-rom-replace-fail", "rom_replace_fail", existing=False)
    check(run.returncode != 0 and not a["backup"].exists() and a["part"].read_bytes() == new, "new ROM failure must keep verified part, with no fabricated backup")
    f, run, final, a, events = scenario("new-rom-push-fail", "push_fail", existing=False)
    check(run.returncode != 0 and "STATUS=FAILED_NEW" in run.stdout and not final.exists() and not a["backup"].exists() and a["part"].exists(), "failed new transfer must be distinguished from preserved old ROM")
    nonrom = f.bash('for p in "$@"; do is_rom_file "$p" && exit 1; done; exit 0', *a.values(), a["txn"] / "old.meta", a["txn"] / "target.path")
    check(nonrom.returncode == 0, "transaction artifacts must never classify as game ROMs")
    results["new_vs_update"] = "PASS"

    # Actual generated shell scripts run on the isolated host filesystem too.
    f, run, final, a, events = scenario("shell-special-path", "rom_execute_shell", filename="한글 space ' [x]\tline\nGame.gb")
    check(run.returncode == 0 and final.read_bytes() == new, f"generated shell failed special path quoting: {run.stderr} {run.stdout}")
    results["generated_shell_special_paths"] = "PASS"
    for suffix in ("backup", "txn"):
        base = cases / ("preexisting-" + suffix); base.mkdir()
        f = Fixture(base)
        path = "/storage/emulated/0/roms/gb/Existing.gb"
        final = f.remote(path); final.write_bytes(old)
        local = base / "same.gb"; local.write_bytes(old)
        key = hashlib.sha256(path.encode()).hexdigest()
        artifact = final.parent / f".esde-sync-{key}.{suffix}"
        if suffix == "txn": artifact.mkdir()
        else: artifact.write_bytes(b"previous-backup-do-not-touch")
        run = f.bash('rom_compare "$1" "$2" "$3" "$4"', "device-A", local, path, len(old))
        check(run.returncode == 2 and artifact.exists() and final.read_bytes() == old, "identical ROM must not bypass unfinished backup/lock detection")
        if suffix == "backup": check(artifact.read_bytes() == b"previous-backup-do-not-touch", "prior backup overwritten")
        run = f.bash('rom_pending_transactions "$1" "$2"', "device-A", "/storage/emulated/0/roms/gb")
        check(run.returncode == 1 and artifact.name in run.stdout, "pending scan must list backup or empty lock directory")
        malformed = f.bash('rom_pending_transactions "$1" "$2"', "device-A", "/storage/emulated/0/roms/gb", mode="rom_pending_bad")
        check(malformed.returncode == 2, "truncated pending scan must fail, not appear empty")
        # Run the generated read-only scan with actual host sh and host paths.
        # The adb shell function intercepts every call; it never invokes real adb.
        run = f.bash('adb() { [[ "$1" == -s && "$3" == exec-out ]] || return 1; sh -c "$4"; }; rom_pending_transactions fixture "$1"', final.parent)
        check(run.returncode == 1 and artifact.name in run.stdout, f"actual generated pending scan failed: {run.stderr}")
    results["preexisting_backup_and_empty_lock"] = "PASS"
    print(json.dumps({"rom_recovery_cases": results}, indent=2))


def test_indexes_and_merge(fx: Fixture):
    root = "/storage/emulated/0/roms"
    fx.remote(root + "/gb/One.gb").write_bytes(b"1")
    out = fx.temp / "index.idx"
    result = fx.bash('build_remote_index "$1" gb "$2" "$3"; echo RC:$?', "device-A", root, out)
    check("RC:0" in result.stdout and b"gb/One.gb\0" + b"1\0" in out.read_bytes(), "remote index framing failed")
    command = fx.base / "captured-index-command.sh"
    outer_syntax = subprocess.run(["sh", "-n", str(command)], capture_output=True, text=True)
    check(outer_syntax.returncode == 0, f"captured remote command failed host sh syntax parsing: {outer_syntax.stderr}; command={command.read_text(encoding='utf-8')!r}")
    script = command.read_text(encoding="utf-8")
    check("&& {" not in script and "|| {" not in script, "generated index command must keep explicit if blocks")
    match = re.search(r"-exec sh -c '(.*?)' sh \{\} \\;", script, re.S)
    check(match is not None, f"generated find -exec sh -c payload was not found: {script!r}")
    inner = fx.base / "captured-index-inner.sh"
    inner.write_text(match.group(1), encoding="utf-8")
    inner_syntax = subprocess.run(["sh", "-n", str(inner)], capture_output=True, text=True)
    check(inner_syntax.returncode == 0, f"generated find -exec payload failed host sh syntax parsing: {inner_syntax.stderr}")
    weird_names = ("한글 space ![x].gb", "tab\tname.gb", "line\nbreak.gb")
    for name in weird_names:
        fx.remote(root + "/gb/" + name).write_bytes(name.encode("utf-8"))
    result = fx.bash('build_remote_index "$1" gb "$2" "$3"', "device-A", root, out)
    indexed = out.read_bytes()
    check(result.returncode == 0 and all(os.fsencode(name) + b"\0" in indexed for name in weird_names), "special filename bytes were not preserved")
    result = fx.bash('build_remote_index "$1" missing "$2" "$3"', "device-A", root, out)
    check(result.returncode == 0 and out.read_bytes() == b"", "missing directory should be a valid empty index")
    empty = fx.remote(root + "/gbc")
    empty.mkdir(parents=True, exist_ok=True)
    result = fx.bash('build_remote_index "$1" gbc "$2" "$3"', "device-A", root, out)
    check(result.returncode == 0 and out.read_bytes() == b"", "existing empty directory should be a valid empty index")
    for mode in ("index_error", "index_partial", "index_literal_tab"):
        result = fx.bash('build_remote_index "$1" gb "$2" "$3"', "device-A", root, out, mode=mode)
        check(result.returncode != 0 and not out.exists(), f"{mode} must reject untrusted listing")
    media_root = "/storage/emulated/0/ES-DE/downloaded_media"
    fx.remote(media_root + "/gb/한글 !.png").write_bytes(b"media")
    result = fx.bash('build_remote_index "$1" gb "$2" "$3"', "device-A", media_root, out)
    check(result.returncode == 0 and "gb/한글 !.png".encode() + b"\0" in out.read_bytes(), "media index did not use the same binary-safe protocol")
    result = fx.bash('build_remote_index "$1" gb "$2" "$3"', "device-A", media_root, out, mode="index_partial")
    check(result.returncode != 0 and not out.exists(), "partial media index must fail closed")

    master = fx.temp / "master.xml"
    android = fx.temp / "android.xml"
    merged = fx.temp / "merged.xml"
    master.write_text('<gameList><game><path>./A.gb</path><name>A</name><playcount>1</playcount><altemulator>Sameboy (Standalone)</altemulator></game></gameList><alternativeEmulator>Legion Default</alternativeEmulator>')
    android.write_text('<gameList><game><path>./A.gb</path><name>A</name><playcount>9</playcount><playtime>120</playtime></game><game><path>./AndroidOnly.gb</path><name>Android Only</name><playcount>77</playcount></game></gameList><alternativeEmulator>Android Default</alternativeEmulator>')
    result = fx.bash('transform_gamelist "$1" "$2" "$3" "$4" gb "$5" "$6"', master, merged, "", fx.temp / "count", fx.temp / "removed", android)
    check(result.returncode == 0, "existing runtime merge failed")
    data = merged.read_text()
    check("<playcount>9" in data and "<playtime>120" in data and "Android Default" in data, "runtime/default emulator merge regressed")
    check("AndroidOnly" not in data and "Sameboy (Standalone)" in data, "Android-only or game-level altemulator policy regressed")


def test_runtime_path_mapping(fx: Fixture):
    old_path = "./Poketto Hiro.gb"
    new_path = "./포켓히어로 (Pokettohiro!).gbc"
    master = fx.temp / "runtime-map-master.xml"
    android = fx.temp / "runtime-map-android.xml"
    output = fx.temp / "runtime-map-output.xml"
    count = fx.temp / "runtime-map-count"
    removed = fx.temp / "runtime-map-removed"
    map_file = fx.temp / "device-A.runtime-map.json"

    def write_map(rows, serial="device-A", schema_version=1):
        map_file.write_text(json.dumps({
            "schema_version": schema_version,
            "device_serial": serial,
            "systems": {"gbc": rows},
        }, ensure_ascii=False), encoding="utf-8")

    def transform(map_path=map_file, serial="device-A", remote=android):
        output.unlink(missing_ok=True)
        return fx.bash(
            'transform_gamelist "$1" "$2" "" "$3" gbc "$4" "$5" "$6" "$7"',
            master, output, count, removed, remote, map_path or "", serial,
        )

    def parse_output():
        return ET.fromstring("<synthetic>" + output.read_text(encoding="utf-8") + "</synthetic>")

    master.write_text(
        '<gameList><game><path>' + new_path + '</path><name>SteamOS title</name>'
        '<desc>SteamOS description</desc><playcount>99</playcount><playtime>900</playtime>'
        '<lastplayed>20261022T000000</lastplayed><genre>SteamOS genre</genre>'
        '<altemulator>Master Core (Standalone)</altemulator></game></gameList>'
        '<alternativeEmulator>SteamOS default</alternativeEmulator>', encoding="utf-8")
    android.write_text(
        '<gameList><game><path>' + old_path + '</path><name>Old Android title</name>'
        '<desc>must not migrate</desc><playcount>0</playcount><playtime></playtime>'
        '<lastplayed>20261021T124145</lastplayed><genre>wrong genre</genre>'
        '<altemulator>Wrong Core (Standalone)</altemulator></game></gameList>'
        '<alternativeEmulator>Android device default</alternativeEmulator>', encoding="utf-8")
    write_map([{"from": old_path, "to": new_path}])
    result = transform()
    check(result.returncode == 0, f"valid explicit runtime mapping failed: {result.stderr}")
    root = parse_output()
    games = {g.findtext("path"): g for g in root.iter("game")}
    game = games[new_path]
    check(old_path not in games, "old path must not be carried into SteamOS gamelist")
    check(game.findtext("name") == "SteamOS title" and game.findtext("desc") == "SteamOS description" and game.findtext("genre") == "SteamOS genre", "old Android descriptive metadata migrated")
    check([game.findtext(tag) for tag in ("playcount", "playtime", "lastplayed")] == ["0", "", "20261021T124145"], "mapped runtime values including zero/empty were not preserved")
    check(game.findtext("altemulator") == "Master Core (Standalone)" and root.findtext("alternativeEmulator") == "Android device default", "emulator merge policy changed during runtime migration")

    # Re-running after the old Android source has disappeared is a no-op and
    # leaves the already-migrated runtime values intact.
    shutil.copyfile(output, android)
    result = transform()
    check(result.returncode == 0 and parse_output().find("gameList/game/playcount").text == "0", "stale source mapping was not idempotent")

    # Missing mapping keeps normal mirror behavior: Android-only entries vanish.
    android.write_text('<gameList><game><path>./AndroidOnly.gb</path><playcount>5</playcount></game></gameList>', encoding="utf-8")
    result = transform(map_path=None)
    check(result.returncode == 0 and "AndroidOnly" not in output.read_text(encoding="utf-8"), "missing mapping file changed Android-only mirror policy")

    # A present source with no SteamOS destination is ambiguous and fails closed.
    android.write_text('<gameList><game><path>' + old_path + '</path><playcount>10</playcount></game></gameList>', encoding="utf-8")
    absent_dest = "./NoSuchDestination.gbc"
    write_map([{"from": old_path, "to": absent_dest}])
    result = transform()
    check(result.returncode != 0 and not output.exists(), "missing mapped destination must fail closed")

    # Existing destination runtime is acceptable only if it agrees exactly.
    android.write_text('<gameList><game><path>' + old_path + '</path><playcount>10</playcount></game>'
                       '<game><path>' + new_path + '</path><playcount>11</playcount></game></gameList>', encoding="utf-8")
    write_map([{"from": old_path, "to": new_path}])
    result = transform()
    check(result.returncode != 0 and not output.exists(), "conflicting target runtime must fail closed")
    android.write_text('<gameList><game><path>' + old_path + '</path><playcount>10</playcount></game>'
                       '<game><path>' + new_path + '</path><playcount>10</playcount></game></gameList>', encoding="utf-8")
    result = transform()
    check(result.returncode == 0, "identical destination runtime should be accepted")

    valid = {"schema_version": 1, "device_serial": "device-A", "systems": {"gbc": [{"from": old_path, "to": new_path}]}}
    malformed_cases = {
        "duplicate source": {"schema_version": 1, "device_serial": "device-A", "systems": {"gbc": [{"from": old_path, "to": new_path}, {"from": old_path, "to": "./Other.gbc"}]}},
        "duplicate destination": {"schema_version": 1, "device_serial": "device-A", "systems": {"gbc": [{"from": "./A.gb", "to": new_path}, {"from": "./B.gb", "to": new_path}]}},
        "chain": {"schema_version": 1, "device_serial": "device-A", "systems": {"gbc": [{"from": "./A.gb", "to": "./B.gb"}, {"from": "./B.gb", "to": "./C.gb"}]}},
        "wrong serial": {**valid, "device_serial": "device-B"},
        "wrong schema": {**valid, "schema_version": 2},
        "unknown system": {"schema_version": 1, "device_serial": "device-A", "systems": {"foo": [{"from": old_path, "to": new_path}]}},
    }
    for label, bad in malformed_cases.items():
        map_file.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        result = fx.bash('validate_runtime_path_map "$1" "$2" "gb gbc"', "device-A", map_file)
        check(result.returncode != 0, f"invalid runtime map accepted: {label}")
    map_file.write_text("{broken json", encoding="utf-8")
    result = fx.bash('validate_runtime_path_map "$1" "$2" "gb gbc"', "device-A", map_file)
    check(result.returncode != 0, "malformed JSON accepted")
    map_file.unlink()
    result = fx.bash('validate_runtime_path_map "$1" "$2" "gb gbc"', "device-B", map_file)
    check(result.returncode == 0, "missing per-device map should retain normal behavior")

    # Malformed source XML continues to fail even when mappings are configured.
    master.write_text("<gameList><broken>", encoding="utf-8")
    write_map([{"from": old_path, "to": new_path}])
    result = transform()
    check(result.returncode != 0 and not output.exists(), "malformed XML was accepted with a runtime mapping")


def test_system_index_gate(fx: Fixture):
    # Execute the scheduler only against fixture roots and a fake adb executable.
    fx.log.unlink(missing_ok=True)
    local_root = fx.base / "local-roms"
    gl_root = fx.base / "local-gamelists"
    media_root = fx.base / "local-media"
    for system in ("gb", "gbc"):
        (local_root / system).mkdir(parents=True, exist_ok=True)
        (local_root / system / f"{system}.gb").write_bytes(system.encode())
        (gl_root / system).mkdir(parents=True, exist_ok=True)
        (gl_root / system / "gamelist.xml").write_text(f'<gameList><game><path>./{system}.gb</path><name>{system}</name></game></gameList>')
        (media_root / system).mkdir(parents=True, exist_ok=True)
        (media_root / system / "image.png").write_bytes(b"image")
    remote_rom = "/storage/emulated/0/roms"
    remote_gl = "/storage/emulated/0/ES-DE/gamelists"
    remote_media = "/storage/emulated/0/ES-DE/downloaded_media"
    # Existing GB content would be deleted if a malformed/incomplete index were mistaken for empty.
    fx.remote(remote_rom + "/gb/Old.gb", "default").write_bytes(b"old")
    fx.remote(remote_media + "/gb/old.png", "default").write_bytes(b"old")
    profile_dir = fx.home / ".config/esde-sync/profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile = profile_dir / "default.conf"
    profile.write_text("\n".join([
        "SYSTEMS='gb gbc'", f"LOCAL_ROM_ROOT='{local_root}'", f"REMOTE_ROM_ROOT='{remote_rom}'",
        f"LOCAL_GAMELIST_ROOT='{gl_root}'", f"REMOTE_GAMELIST_ROOT='{remote_gl}'",
        f"LOCAL_MEDIA_ROOT='{media_root}'", f"REMOTE_MEDIA_ROOT='{remote_media}'",
        "SYNC_GAMELISTS=1", "SYNC_MEDIA=1", "MIRROR_ROMS=1", "MIRROR_MEDIA=1", "MANAGE_ESDE=0",
    ]) + "\n")
    protected_before = {p: p.read_bytes() for root in (local_root, gl_root, media_root, profile_dir) for p in root.rglob("*") if p.is_file()}
    env = fx.env(mode="index_error_gb")
    env["ESDE_SYNC_LIBRARY_ONLY"] = "0"
    env.update({"ESDE_SYNC_GUI_MODE": "1", "ESDE_SYNC_RESULT_FILE": str(fx.base / "result.txt"), "ESDE_SYNC_STATUS_FILE": str(fx.base / "status.tsv")})
    run = subprocess.run(["bash", str(ENGINE)], env=env, capture_output=True, text=True, timeout=60)
    events = [json.loads(line) for line in fx.log.read_text().splitlines()] if fx.log.exists() else []
    gb_mutations = [e for e in events if e["action"] in ("push", "rename", "delete") and "/gb/" in e["path"]]
    gbc_pushes = [e for e in events if e["action"] == "push" and "/gbc/" in e["path"]]
    check(run.returncode == 0, f"index-gate integration run should report via result file: {run.stdout} {run.stderr} {(fx.base / 'result.txt').read_text() if (fx.base / 'result.txt').exists() else 'no result'}")
    check(not gb_mutations, f"GB ROM/gamelist/media mutation occurred after ROM index failure: {gb_mutations}")
    check(fx.remote(remote_rom + "/gb/Old.gb", "default").read_bytes() == b"old", "GB mirror deletion was not blocked")
    check(fx.remote(remote_media + "/gb/old.png", "default").read_bytes() == b"old", "GB media deletion was not blocked")
    check(bool(gbc_pushes), f"healthy neighboring system should continue after isolated index failure: rc={run.returncode}; out={run.stdout}; err={run.stderr}; events={events}; result={(fx.base / 'result.txt').read_text() if (fx.base / 'result.txt').exists() else 'missing'}")
    result_text = (fx.base / "result.txt").read_text()
    check("ERROR" in result_text and "1개" in result_text, "index failure was not included in final failure count")

    # A disconnected device ends the complete run before other systems or mutations.
    fx.log.unlink(missing_ok=True)
    env = fx.env(mode="index_disconnect")
    env["ESDE_SYNC_LIBRARY_ONLY"] = "0"
    env.update({"ESDE_SYNC_GUI_MODE": "1", "ESDE_SYNC_RESULT_FILE": str(fx.base / "disconnect-result.txt"), "ESDE_SYNC_STATUS_FILE": str(fx.base / "disconnect-status.tsv")})
    subprocess.run(["bash", str(ENGINE)], env=env, capture_output=True, text=True, timeout=60)
    events = [json.loads(line) for line in fx.log.read_text().splitlines()]
    check(not [e for e in events if e["action"] in ("push", "rename", "delete")], "disconnected adb must stop all further mutations")
    check(not [e for e in events if e["action"] == "index" and e["path"].endswith("/gbc")], "disconnected adb must stop before another system")

    for pending_kind in ("backup", "txn"):
        path = remote_rom + "/gb/Old.gb"
        key = hashlib.sha256(path.encode()).hexdigest()
        artifact = fx.remote(remote_rom + f"/gb/.esde-sync-{key}.{pending_kind}", "default")
        if pending_kind == "txn": artifact.mkdir()
        else: artifact.write_bytes(b"valuable-old-backup")
        fx.log.unlink(missing_ok=True)
        env = fx.env()
        env.update({"ESDE_SYNC_LIBRARY_ONLY": "0", "ESDE_SYNC_GUI_MODE": "1", "ESDE_SYNC_RESULT_FILE": str(fx.base / "pending-result.txt"), "ESDE_SYNC_STATUS_FILE": str(fx.base / "pending-status.tsv")})
        run = subprocess.run(["bash", str(ENGINE)], env=env, capture_output=True, text=True, timeout=60)
        events = [json.loads(line) for line in fx.log.read_text().splitlines()]
        check(not [e for e in events if e["action"] in ("push", "rom_claim", "rom_replace", "delete", "gamelist")], "pending backup/empty lock must block ROM/gamelist/media for all systems")
        text = (fx.base / "pending-result.txt").read_text()
        check(artifact.name in text and "실패: 1개" in text and artifact.exists(), "pending failure count/path/preservation report is incomplete")
        if artifact.is_dir(): artifact.rmdir()
        else: artifact.unlink()

    old_final = fx.remote(remote_rom + "/gb/gb.gb", "default")
    for mode, status in (("rom_replace_fail", "FAILED_RESTORED"), ("rom_disconnect_replace", "UNKNOWN")):
        old_final.write_bytes(b"original-before-sync")
        fx.log.unlink(missing_ok=True)
        env = fx.env(mode=mode)
        env.update({"ESDE_SYNC_LIBRARY_ONLY": "0", "ESDE_SYNC_GUI_MODE": "1", "ESDE_SYNC_RESULT_FILE": str(fx.base / "recovery-result.txt"), "ESDE_SYNC_STATUS_FILE": str(fx.base / "recovery-status.tsv")})
        run = subprocess.run(["bash", str(ENGINE)], env=env, capture_output=True, text=True, timeout=60)
        text = (fx.base / "recovery-result.txt").read_text()
        events = [json.loads(line) for line in fx.log.read_text().splitlines()]
        check(status in text and "실패: 1개" in text and ".backup" in text and "old.meta" in text, "final summary must report failed/unknown recovery and device paths")
        check(not [e for e in events if e["action"] in ("gamelist", "delete") or (e["action"] == "push" and "downloaded_media" in e["path"])], "ROM recovery failure must stop later gamelist/media/deletes")
        check(not [e for e in events if e["action"] == "index" and e["path"].endswith("/gbc")], "ROM recovery failure must stop other systems")
        if status == "FAILED_RESTORED": check(old_final.read_bytes() == b"original-before-sync", "scheduler did not preserve restored original")
        # Fixture-only cleanup before the next independent injected scenario.
        for p in old_final.parent.glob(".esde-sync-*"):
            if p.is_dir(): shutil.rmtree(p)
            else: p.unlink()
        (fx.remote_root / "default" / ".disconnected").unlink(missing_ok=True)
    # Simulate a local traversal that emits a partial list, then fails. The
    # product must inspect find's status before making a mirror/delete decision.
    real_find = shutil.which("find")
    find_wrapper = fx.bin / "find"
    find_wrapper.write_text("#!/usr/bin/env python3\nimport os,subprocess,sys\n"
        "args=sys.argv[1:]\n"
        "if args and args[0] == os.environ.get('FAKE_FIND_FAIL_ROOT'):\n"
        f" p=subprocess.run([{real_find!r},*args],capture_output=True)\n"
        " rows=p.stdout.split(b'\\0'); sys.stdout.buffer.write(rows[0]+b'\\0' if rows[0] else b''); raise SystemExit(1)\n"
        f"os.execv({real_find!r},[{real_find!r},*args])\n")
    find_wrapper.chmod(0o755)
    for source_root, label in ((local_root, "ROM"), (media_root, "media")):
        fx.log.unlink(missing_ok=True)
        env = fx.env()
        env.update({"ESDE_SYNC_LIBRARY_ONLY": "0", "ESDE_SYNC_GUI_MODE": "1", "ESDE_SYNC_RESULT_FILE": str(fx.base / "find-result.txt"), "ESDE_SYNC_STATUS_FILE": str(fx.base / "find-status.tsv"), "FAKE_FIND_FAIL_ROOT": str(source_root / "gb")})
        run = subprocess.run(["bash", str(ENGINE)], env=env, capture_output=True, text=True, timeout=60)
        events = [json.loads(line) for line in fx.log.read_text().splitlines()] if fx.log.exists() else []
        text = (fx.base / "find-result.txt").read_text()
        check("실패: 1개" in text, f"{label}: partial local listing failure not counted: {text}")
        if label == "ROM":
            check(not [e for e in events if "/gb/" in e["path"] and e["action"] in ("push", "rom_claim", "rom_replace", "delete", "gamelist")], "partial local ROM list allowed system mutations")
            check(fx.remote(remote_rom + "/gb/Old.gb", "default").read_bytes() == b"old", "partial local ROM listing deleted existing remote ROM")
        else:
            check(not [e for e in events if "/downloaded_media/gb/" in e["path"] and e["action"] in ("push", "delete")], "partial local media list allowed media mirror mutations")
            check(fx.remote(remote_media + "/gb/old.png", "default").read_bytes() == b"old", "partial local media listing deleted existing remote media")

    # An absent or non-directory SteamOS ROM source is unknown, never an empty
    # source. It must block all changes for that system while a healthy peer
    # system can still be processed.
    gb_source = local_root / "gb"
    saved_gb_source = local_root / ".gb-source-saved"
    check(gb_source.is_dir() and not saved_gb_source.exists(), "GB source fixture is not ready for isolated path-state tests")
    shutil.move(gb_source, saved_gb_source)
    try:
        for state in ("missing", "not_directory"):
            if state == "not_directory":
                gb_source.write_text("not a directory")
            fx.remote(remote_rom + "/gb/Old.gb", "default").write_bytes(b"old")
            fx.log.unlink(missing_ok=True)
            result_path = fx.base / f"local-rom-{state}-result.txt"
            env = fx.env()
            env.update({"ESDE_SYNC_LIBRARY_ONLY": "0", "ESDE_SYNC_GUI_MODE": "1", "ESDE_SYNC_RESULT_FILE": str(result_path), "ESDE_SYNC_STATUS_FILE": str(fx.base / f"local-rom-{state}-status.tsv")})
            run = subprocess.run(["bash", str(ENGINE)], env=env, capture_output=True, text=True, timeout=60)
            events = [json.loads(line) for line in fx.log.read_text().splitlines()] if fx.log.exists() else []
            result_text = result_path.read_text()
            gb_writes = [e for e in events if "/gb/" in e["path"] and e["action"] in ("push", "rename", "delete")]
            check("ERROR" in result_text and "실패: 1개" in result_text, f"{state}: invalid SteamOS ROM source was not counted as one failure: {result_text}")
            check(not gb_writes, f"{state}: invalid SteamOS ROM source allowed a ROM/gamelist/media write: {gb_writes}")
            check(fx.remote(remote_rom + "/gb/Old.gb", "default").read_bytes() == b"old", f"{state}: invalid source deleted the Android ROM")
            check(any(e["action"] == "index" and e["path"].endswith("/gbc") for e in events), f"{state}: healthy neighboring system was not processed")
            check(run.returncode == 0, f"{state}: scheduler did not return its GUI result normally: {run.stderr}")
            if state == "not_directory":
                gb_source.unlink()

        # An existing, readable empty directory is an authoritative empty
        # mirror: unlike an absent source, it intentionally removes remote ROMs.
        gb_source.mkdir()
        fx.remote(remote_rom + "/gb/Old.gb", "default").write_bytes(b"old")
        fx.log.unlink(missing_ok=True)
        result_path = fx.base / "local-rom-empty-result.txt"
        env = fx.env()
        env.update({"ESDE_SYNC_LIBRARY_ONLY": "0", "ESDE_SYNC_GUI_MODE": "1", "ESDE_SYNC_RESULT_FILE": str(result_path), "ESDE_SYNC_STATUS_FILE": str(fx.base / "local-rom-empty-status.tsv")})
        run = subprocess.run(["bash", str(ENGINE)], env=env, capture_output=True, text=True, timeout=60)
        events = [json.loads(line) for line in fx.log.read_text().splitlines()] if fx.log.exists() else []
        check(run.returncode == 0, f"empty source run did not complete: {run.stderr}")
        check(not fx.remote(remote_rom + "/gb/Old.gb", "default").exists(), "valid empty SteamOS source did not mirror-delete the Android ROM")
        check(any(e["action"] == "delete" and e["path"].endswith("/gb/Old.gb") for e in events), "valid empty source did not execute the expected remote ROM deletion")
        check("실패: 0개" in result_path.read_text(), "valid empty source was incorrectly classified as a failure")
    finally:
        if gb_source.is_file():
            gb_source.unlink()
        elif gb_source.is_dir():
            gb_source.rmdir()
        shutil.move(saved_gb_source, gb_source)

    protected_after = {p: p.read_bytes() for root in (local_root, gl_root, media_root, profile_dir) for p in root.rglob("*") if p.is_file()}
    check(protected_after == protected_before, "ROM/gamelist/media originals or profiles were changed by scheduler")


def test_large_stream(fx: Fixture):
    remote_path = "/storage/emulated/0/roms/ps2/Large.chd"
    rp = fx.remote(remote_path)
    block = bytes(range(256)) * 4096
    with rp.open("wb") as f:
        for _ in range(64): f.write(block)  # 64 MiB, generated in bounded chunks.
    size = rp.stat().st_size
    digest = hashlib.sha256()
    with rp.open("rb") as f:
        while chunk := f.read(1024 * 1024): digest.update(chunk)
    start = time.monotonic()
    result = fx.bash('remote_sha256 "$1" "$2" "$3"', "device-A", remote_path, size, mode="hash_unsupported")
    elapsed = time.monotonic() - start
    check(result.returncode == 0 and result.stdout.strip() == digest.hexdigest(), "large streaming fallback failed")
    return {"size_mib": size // (1024 * 1024), "seconds": round(elapsed, 3), "max_child_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss}


def main():
    configured_root = os.environ.get("ESDE_SYNC_TEST_ROOT")
    if configured_root:
        root = Path(configured_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise RuntimeError("ESDE_SYNC_TEST_ROOT must not be a symbolic link")
        root = root.resolve()
        if root == Path("/") or root == REPO or root in REPO.parents or REPO in root.parents:
            raise RuntimeError("ESDE_SYNC_TEST_ROOT must be a dedicated directory outside the repository")
        temp_context = tempfile.TemporaryDirectory(prefix="esde-sync-v045-", dir=root)
    else:
        temp_context = tempfile.TemporaryDirectory(prefix="esde-sync-v045-")
    with temp_context as temp:
        fx = Fixture(Path(temp))
        test_gamelist(fx)
        test_hash_and_atomic_rom(fx)
        test_rom_recovery(fx)
        test_indexes_and_merge(fx)
        test_runtime_path_mapping(fx)
        test_system_index_gate(fx)
        large = test_large_stream(fx)
        print(json.dumps({
            "status": "PASS",
            "isolated_fixture": True,
            "actual_android_or_user_data_touched": False,
            "checks": [
                "gamelist MISSING/FILE/EMPTY/ERROR/truncation/length mismatch/XML corruption",
                "same-size same-content and changed-content SHA-256",
                "device hash, unsupported fallback, malformed hash, total hash failure",
                "verified original backup, destructive replace/restore failures, disconnect, pending transactions, space checks",
                "remote index failure, rename/new path/delete, per-device isolation",
                "Toybox literal-tab rejection, binary path framing, partial-list rejection, system gates and disconnect stop",
                "missing/non-directory local ROM source blocks the system; valid empty source permits mirror deletion",
                "explicit per-device runtime path mapping, conflict rejection, idempotence and metadata preservation",
                "captured generated outer and find-exec shell syntax validation",
                "runtime and alternativeEmulator merge regression",
            ],
            "large_rom": large,
        }, indent=2))


if __name__ == "__main__":
    main()
