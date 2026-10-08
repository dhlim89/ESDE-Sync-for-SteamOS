#!/usr/bin/env python3
"""Detached installer; only starts after the API response has been flushed."""
import json
import fcntl
import os
import pathlib
import subprocess
import sys
import time


def run_update(lock_fd):
    installer = pathlib.Path(sys.argv[1])
    state = pathlib.Path(sys.argv[2])
    status = state / "update-status.json"
    try:
        with (state / "update.log").open("a", encoding="utf-8", buffering=1) as log:
            try:
                if sys.stdin.buffer.readline() != b"apply\n":
                    raise RuntimeError("API 응답 이후 설치 시작 신호를 받지 못했습니다.")
                log.write(f"UPDATE START {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                install_env = os.environ.copy()
                install_env["ESDE_SYNC_UPDATE_LOCK_FD"] = str(lock_fd)
                subprocess.run(["bash", str(installer)], cwd=str(pathlib.Path.home()),
                               stdout=log, stderr=log, check=True,
                               env=install_env, pass_fds=(lock_fd,))
                launcher = pathlib.Path.home() / ".local/bin/esde-sync"
                subprocess.run([str(launcher), "--update-relaunch"],
                               cwd=str(pathlib.Path.home()),
                               stdout=log, stderr=log, check=True, timeout=45)
                outcome = {"ok": True, "reason": "새 버전 창을 열었습니다."}
            except Exception as e:
                outcome = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
                log.write(f"UPDATE ERROR: {outcome['reason']}\n")
            temporary = status.with_suffix(".tmp")
            temporary.write_text(json.dumps(outcome, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, status)
            log.write(f"UPDATE END: {outcome['reason']}\n")
    except Exception as e:
        print(f"UPDATE HELPER ERROR: {e}", file=sys.stderr)
        return 1
    return 0 if outcome["ok"] else 1


def main():
    # This is the backend's already-locked open-file description. It remains
    # locked even if that backend is killed, until every inherited copy closes.
    lock_fd = int(sys.argv[3])
    try:
        with os.fdopen(lock_fd, "a+") as held_lock:
            state_lock = pathlib.Path(sys.argv[2]) / "update.lock"
            actual = os.fstat(held_lock.fileno())
            expected = state_lock.stat()
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise RuntimeError("업데이트 lock fd가 state/update.lock과 일치하지 않습니다.")
            os.set_inheritable(lock_fd, False)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return run_update(lock_fd)
    except Exception as e:
        state = pathlib.Path(sys.argv[2])
        try:
            with (state / "update.log").open("a", encoding="utf-8") as log:
                log.write(f"UPDATE LOCK ERROR: {type(e).__name__}: {e}\n")
        except Exception:
            print(f"UPDATE LOCK ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
