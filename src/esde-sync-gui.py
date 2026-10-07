#!/usr/bin/env python3
import json
import base64
import os
import pathlib
import socket
import subprocess
import urllib.request
import tempfile
import hashlib
import fcntl
import shlex
import zipfile
import shutil
import threading
import time
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

HOME = pathlib.Path.home()
APP_DIR = pathlib.Path(os.environ.get("ESDE_SYNC_APP_HOME", HOME / ".local/share/esde-sync"))
STATE_DIR = pathlib.Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "esde-sync"
PROFILE_DIR = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "esde-sync" / "profiles"
ENGINE = APP_DIR / "esde-sync-engine"
STATUS = STATE_DIR / "gui-status.tsv"
RESULT = STATE_DIR / "gui-result.txt"
WEB = APP_DIR / "web"

process = None
lock = threading.Lock()

ESDE_PACKAGE = "org.es_de.frontend"
DEFAULT_TEMPLATE_SERIAL = "MC94516AQF040306243"
LOCAL_ROM_ROOT = HOME / "Emulation" / "roms"

APP_VERSION = "0.37"
UPDATE_META_URL = "https://api.github.com/repos/dhlim89/esde-sync/contents/version.json?ref=main"


INSTANCE_LOCK = STATE_DIR / "instance.lock"
PORT_FILE = STATE_DIR / "gui-port"

def read_status():
    """Read engine progress TSV safely and return a JSON-serializable object."""
    default = {"percent": 0, "text": "대기 중"}
    try:
        if not STATUS.exists():
            return default
        raw = STATUS.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return default

        # Current engine format: percent<TAB>text
        parts = raw.split("\t", 1)
        if len(parts) == 2:
            try:
                percent = max(0, min(100, int(parts[0].strip())))
            except Exception:
                percent = 0
            return {"percent": percent, "text": parts[1].strip() or "대기 중"}

        # Backward-safe fallback if only text exists.
        return {"percent": 0, "text": raw}
    except Exception:
        return default

def read_result():
    """Read the last sync result without ever breaking /api/state."""
    try:
        if not RESULT.exists():
            return ""
        return RESULT.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

def acquire_single_instance():
    """
    Keep exactly one ES-DE Sync backend.
    Returns (lock_file, existing_port). existing_port is set when another
    instance is already running.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = INSTANCE_LOCK.open("a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file, None
    except BlockingIOError:
        try:
            port = int(PORT_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            port = None
        return None, port


def foreground_package(serial):
    try:
        out = subprocess.check_output(
            ["adb","-s",serial,"shell","dumpsys","window"],
            text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            if "mCurrentFocus=" in line or "mFocusedApp=" in line:
                m = __import__("re").search(r"u0\s+([A-Za-z0-9._]+)/(?:[A-Za-z0-9._$]+)", line)
                if m:
                    return m.group(1)
                m = __import__("re").search(r"([A-Za-z0-9._]+)/[A-Za-z0-9._$]+", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return ""

def is_safe_to_sync():
    dev = adb_info()
    if not dev["connected"]:
        return False, "Android 기기가 연결되어 있지 않습니다."
    pkg = foreground_package(dev["serial"])
    if not pkg:
        return False, "현재 실행 중인 앱을 확인할 수 없습니다. 기기 화면을 켠 뒤 다시 시도해 주세요."
    # ES-DE itself, system UI, or launcher/home surfaces are allowed.
    allowed = {
        ESDE_PACKAGE,
        "com.android.systemui",
        "com.google.android.apps.nexuslauncher",
        "com.android.launcher3",
    }
    if pkg in allowed:
        return True, ""
    return False, f"게임 또는 다른 앱이 실행 중입니다: {pkg}\n게임을 종료한 뒤 다시 동기화해 주세요."

def adb_info():
    try:
        out = subprocess.check_output(["adb", "devices"], text=True, stderr=subprocess.DEVNULL)
        serials = [line.split()[0] for line in out.splitlines()[1:] if line.strip().endswith("\tdevice")]
        if len(serials) != 1:
            return {"connected": False, "name": "Android 기기 없음", "serial": ""}
        serial = serials[0]
        model = subprocess.check_output(["adb","-s",serial,"shell","getprop","ro.product.model"], text=True).strip()
        maker = subprocess.check_output(["adb","-s",serial,"shell","getprop","ro.product.manufacturer"], text=True).strip()
        return {"connected": True, "name": f"{maker} {model}".strip(), "serial": serial}
    except Exception:
        return {"connected": False, "name": "Android 기기 없음", "serial": ""}

def profile_info(serial):
    p = PROFILE_DIR / f"{serial}.conf"
    systems = ""
    exists = p.exists()
    if exists:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("SYSTEMS="):
                systems = line.split("=",1)[1].strip().strip('"').replace("\\ ", " ")
                break
    return {
        "exists": exists,
        "systems": systems or "—",
        "path": str(p),
    }


def _version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except Exception:
        return (0,)

def update_info():
    result = {
        "ok": False,
        "current": APP_VERSION,
        "latest": APP_VERSION,
        "available": False,
        "notes": "",
        "url": "",
        "sha256": "",
        "error": "",
    }
    try:
        req = urllib.request.Request(
            UPDATE_META_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"ES-DE-Sync/{APP_VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            contents = json.loads(r.read().decode("utf-8"))
        if contents.get("encoding") != "base64" or not isinstance(contents.get("content"), str):
            raise ValueError("GitHub Contents API 응답에 base64 content가 없습니다.")
        encoded = "".join(contents["content"].split())
        meta = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("업데이트 메타데이터는 JSON 객체여야 합니다.")

        latest = str(meta.get("version", "")).strip().lstrip("v")
        result["latest"] = latest or APP_VERSION
        result["notes"] = str(meta.get("notes", "") or "")
        result["url"] = str(meta.get("url", "") or "")
        result["sha256"] = str(meta.get("sha256", "") or "")
        result["available"] = bool(latest) and _version_tuple(latest) > _version_tuple(APP_VERSION)
        result["ok"] = True
    except Exception as e:
        result["error"] = str(e)
    return result

def _perform_update(url, expected_sha256=""):
    log_path = STATE_DIR / "update.log"
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="esde-sync-update-"))
        archive = tmpdir / "update.zip"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"ES-DE-Sync/{APP_VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=60) as r, archive.open("wb") as f:
            shutil.copyfileobj(r, f)

        if expected_sha256:
            h = hashlib.sha256()
            with archive.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            actual = h.hexdigest().lower()
            if actual != expected_sha256.lower():
                raise RuntimeError(
                    f"업데이트 파일 SHA-256 검증 실패: expected={expected_sha256} actual={actual}"
                )

        extract_dir = tmpdir / "extract"
        extract_dir.mkdir()
        with zipfile.ZipFile(archive, "r") as z:
            z.extractall(extract_dir)

        installers = list(extract_dir.rglob("install.sh"))
        if not installers:
            raise RuntimeError("업데이트 패키지에서 install.sh를 찾을 수 없습니다.")

        install_sh = installers[0]
        os.chmod(install_sh, 0o755)

        launcher = pathlib.Path(os.environ.get("ESDE_SYNC_BIN_DIR", HOME / ".local/bin")) / "esde-sync"
        helper = tmpdir / "apply-update.sh"
        helper.write_text(f"""#!/usr/bin/env bash
set -u
LOG={shlex.quote(str(log_path))}
echo "=== update start $(date -Is) ===" >> "$LOG"
sleep 1
cd {shlex.quote(str(install_sh.parent))} || {{ echo "cd failed" >> "$LOG"; exit 1; }}
chmod +x install.sh || {{ echo "chmod failed" >> "$LOG"; exit 1; }}
./install.sh >> "$LOG" 2>&1
RC=$?
echo "install exit=$RC" >> "$LOG"
if [ "$RC" -ne 0 ]; then
  exit "$RC"
fi
sleep 1
echo "relaunch" >> "$LOG"
exec {shlex.quote(str(launcher))} >> "$LOG" 2>&1
""", encoding="utf-8")
        os.chmod(helper, 0o755)

        subprocess.Popen(
            ["bash", str(helper)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True, "업데이트를 설치하고 ES-DE Sync를 다시 시작합니다."
    except Exception as e:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"UPDATE ERROR: {type(e).__name__}: {e}\n")
        except Exception:
            pass
        return False, f"{type(e).__name__}: {e}"


def available_systems():
    if not LOCAL_ROM_ROOT.is_dir():
        return []
    systems = []
    for p in sorted(LOCAL_ROM_ROOT.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_dir():
            continue
        try:
            # Show systems that actually contain at least one file somewhere inside.
            if any(x.is_file() for x in p.rglob("*")):
                systems.append(p.name)
        except Exception:
            continue
    return systems

def register_profile_from_template(serial, systems):
    if not serial:
        return False, "등록할 기기 시리얼을 확인할 수 없습니다."
    if not systems:
        return False, "동기화할 시스템을 하나 이상 선택해 주세요."

    valid = set(available_systems())
    chosen = [s for s in systems if s in valid]
    if not chosen:
        return False, "선택한 시스템을 Legion Go S에서 찾을 수 없습니다."

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    target = PROFILE_DIR / f"{serial}.conf"
    if target.exists():
        return True, "이미 등록된 기기입니다."

    template = PROFILE_DIR / f"{DEFAULT_TEMPLATE_SERIAL}.conf"
    if not template.exists():
        return False, f"Android 기본 프로필을 찾을 수 없습니다: {template}"

    text = template.read_text(encoding="utf-8", errors="replace")
    systems_line = 'SYSTEMS="' + " ".join(chosen) + '"'

    if re.search(r"(?m)^SYSTEMS=.*$", text):
        text = re.sub(r"(?m)^SYSTEMS=.*$", systems_line, text, count=1)
    else:
        text = systems_line + "\n" + text

    target.write_text(text, encoding="utf-8")
    return True, f"기기 프로필을 등록했습니다: {target.name}\n동기화 시스템: {' '.join(chosen)}"


def update_profile_systems(serial, systems):
    if not serial:
        return False, "기기 시리얼을 확인할 수 없습니다."
    if not systems:
        return False, "동기화할 시스템을 하나 이상 선택해 주세요."

    valid = set(available_systems())
    chosen = [s for s in systems if s in valid]
    if not chosen:
        return False, "선택한 시스템을 Legion Go S에서 찾을 수 없습니다."

    target = PROFILE_DIR / f"{serial}.conf"
    if not target.exists():
        return False, "기기 프로필을 찾을 수 없습니다."

    text = target.read_text(encoding="utf-8", errors="replace")
    systems_line = 'SYSTEMS="' + " ".join(chosen) + '"'

    if re.search(r"(?m)^SYSTEMS=.*$", text):
        text = re.sub(r"(?m)^SYSTEMS=.*$", systems_line, text, count=1)
    else:
        text = systems_line + "\n" + text

    target.write_text(text, encoding="utf-8")
    return True, f"동기화 시스템을 변경했습니다: {' '.join(chosen)}"

def start_sync():
    global process
    with lock:
        if process and process.poll() is None:
            return False, "이미 동기화가 진행 중입니다."
        dev = adb_info()
        if not dev["connected"]:
            return False, "Android 기기가 연결되어 있지 않습니다."
        if not profile_info(dev["serial"])["exists"]:
            return False, "이 기기는 아직 등록되지 않았습니다. 먼저 기기 등록을 해 주세요."
        ok, reason = is_safe_to_sync()
        if not ok:
            return False, reason
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        for f in (STATUS, RESULT):
            try: f.unlink()
            except FileNotFoundError: pass
        env = os.environ.copy()
        env["ESDE_SYNC_GUI_MODE"] = "1"
        env["ESDE_SYNC_STATUS_FILE"] = str(STATUS)
        env["ESDE_SYNC_RESULT_FILE"] = str(RESULT)
        process = subprocess.Popen([str(ENGINE)], env=env)
        return True, ""

class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = urllib.parse.urlparse(path).path
        rel = path.lstrip("/") or "index.html"
        return str(WEB / rel)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/state":
            dev = adb_info()
            profile = profile_info(dev["serial"]) if dev["connected"] else {"exists":False,"systems":"—","path":""}
            running = bool(process and process.poll() is None)
            body = {
                "device": dev,
                "profile": profile,
                "available_systems": available_systems(),
                "update": update_info(),
                "running": running,
                "status": read_status(),
                "result": read_result(),
            }
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(data)
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/start":
            ok, reason = start_sync()
            data = json.dumps({"ok":ok, "reason":reason}, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8")
            self.end_headers(); self.wfile.write(data)
            return

        if self.path in ("/api/register", "/api/update-systems"):
            length = int(self.headers.get("Content-Length", "0") or "0")
            payload = {}
            if length:
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except Exception:
                    payload = {}

            systems = payload.get("systems") or []
            dev = adb_info()
            if not dev["connected"]:
                ok, reason = False, "Android 기기가 연결되어 있지 않습니다."
            elif self.path == "/api/register":
                ok, reason = register_profile_from_template(dev["serial"], systems)
            else:
                ok, reason = update_profile_systems(dev["serial"], systems)

            data = json.dumps({"ok":ok, "reason":reason}, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8")
            self.end_headers(); self.wfile.write(data)
            return

        if self.path == "/api/update":
            try:
                info = update_info()
                if not info.get("ok"):
                    ok, reason = False, "업데이트 정보를 확인할 수 없습니다: " + info.get("error", "")
                elif not info.get("available"):
                    ok, reason = False, "현재 최신 버전입니다."
                elif not info.get("url"):
                    ok, reason = False, "업데이트 다운로드 주소가 없습니다."
                else:
                    ok, reason = _perform_update(info["url"], info.get("sha256", ""))
            except Exception as e:
                ok, reason = False, f"{type(e).__name__}: {e}"
                try:
                    STATE_DIR.mkdir(parents=True, exist_ok=True)
                    with (STATE_DIR / "update.log").open("a", encoding="utf-8") as f:
                        f.write(f"API UPDATE ERROR: {reason}\n")
                except Exception:
                    pass

            data = json.dumps({"ok":ok, "reason":reason}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.end_headers()
            try:
                self.wfile.write(data)
                self.wfile.flush()
            except Exception:
                pass

            if ok:
                threading.Thread(
                    target=lambda: (time.sleep(1.2), os._exit(0)),
                    daemon=True
                ).start()
            return

        self.send_error(404)

def find_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def launch_browser(url):
    if os.environ.get("ESDE_SYNC_HEADLESS") == "1":
        return
    origin_brave = str(HOME / "Applications" / "Brave-Origin" / "brave")
    candidates = [
        [origin_brave,f"--app={url}","--new-window"],
        ["flatpak","run","com.brave.Browser",f"--app={url}","--new-window"],
        ["brave",f"--app={url}","--new-window"],
        ["brave-browser",f"--app={url}","--new-window"],
        ["google-chrome",f"--app={url}","--new-window"],
        ["chromium",f"--app={url}","--new-window"],
        ["xdg-open",url],
    ]
    for cmd in candidates:
        try:
            if cmd[0] == "flatpak":
                check = subprocess.run(["flatpak","info","com.brave.Browser"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if check.returncode != 0: continue
            else:
                from shutil import which
                if os.path.isabs(cmd[0]):
                    if not os.path.isfile(cmd[0]) or not os.access(cmd[0], os.X_OK):
                        continue
                elif which(cmd[0]) is None:
                    continue
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            continue
    raise RuntimeError("브라우저를 실행할 수 없습니다.")

def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    instance_lock, existing_port = acquire_single_instance()
    if instance_lock is None:
        # Re-open the already running instance instead of creating another backend.
        if existing_port:
            launch_browser(f"http://127.0.0.1:{existing_port}/")
        return

    port = find_port()
    PORT_FILE.write_text(str(port), encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    launch_browser(f"http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    finally:
        try:
            PORT_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            fcntl.flock(instance_lock.fileno(), fcntl.LOCK_UN)
            instance_lock.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
