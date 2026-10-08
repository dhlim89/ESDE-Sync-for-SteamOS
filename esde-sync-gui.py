#!/usr/bin/env python3
import json
import re
import base64
import sys
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
import urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from services import dropbox
from services.android import AndroidService
from services.jobs import JobManager, BusyError

HOME = pathlib.Path.home()
TEST_MODE = os.environ.get('ESDE_SYNC_TEST_MODE') == '1'
TEST_ROOT = pathlib.Path(os.environ['ESDE_SYNC_TEST_ROOT']).resolve() if TEST_MODE else None
if TEST_MODE and (not TEST_ROOT.is_relative_to(pathlib.Path('/tmp')) or TEST_ROOT == pathlib.Path('/tmp')):
    raise RuntimeError('test root는 /tmp 하위의 전용 디렉터리여야 합니다.')
APP_DIR = pathlib.Path(__file__).resolve().parent if TEST_MODE else HOME / ".local" / "share" / "esde-sync"
STATE_DIR = pathlib.Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "esde-sync"
if TEST_MODE: STATE_DIR = TEST_ROOT / 'state/esde-sync'
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

APP_VERSION = "0.44"
UPDATE_META_URL = "https://api.github.com/repos/dhlim89/ESDE-Sync-for-SteamOS/contents/version.json?ref=main"


INSTANCE_LOCK = STATE_DIR / "instance.lock"
PORT_FILE = STATE_DIR / "gui-port"
UPDATE_STATUS = STATE_DIR / "update-status.json"
UPDATE_LOCK = STATE_DIR / "update.lock"
update_lock_file = None
update_pending = False
update_helper = None


def log_update(message):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / "update.log").open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception as e:
        print(f"UPDATE LOG ERROR: {e}; {message}", file=sys.stderr)


def engine_running():
    if process and process.poll() is None:
        return True
    # Include engines launched outside this GUI; never terminate them.
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
            if os.fsencode(str(ENGINE)) in args[:3]:
                return True
        except (FileNotFoundError, ProcessLookupError):
            continue
    return False


def acquire_update_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handle = UPDATE_LOCK.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BlockingIOError:
        handle.close()
        return None
    except Exception:
        handle.close()
        raise


def updating():
    # Caller holds the thread lock; the flock also covers other backends/helpers.
    if update_pending:
        return True
    if update_helper is not None:
        update_helper.poll()  # Reap a finished helper without trusting PID state.
    handle = acquire_update_lock()
    if handle is None:
        return True
    handle.close()
    return False


def read_status():
    """Decode the engine's value<TAB>max<TAB>label progress record."""
    default = {"value": 0, "max": 0, "percent": 0, "label": "대기 중"}
    try:
        raw = STATUS.read_text(encoding="utf-8", errors="replace").rstrip("\r\n")
        if not raw.strip():
            return default
        fields = raw.split("\t", 2)
        if len(fields) != 3:
            return default
        value, maximum = int(fields[0]), int(fields[1])
        percent = max(0, min(100, value * 100 // maximum)) if maximum > 0 else 0
        return {"value": value, "max": maximum, "percent": percent,
                "label": fields[2].strip() or "대기 중"}
    except (OSError, ValueError):
        return default


def read_result():
    """Decode OK/ERROR plus the following detail text, or no result yet."""
    try:
        raw = RESULT.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not raw.strip():
        return None
    kind, _, text = raw.partition("\n")
    kind = kind.strip()
    if kind not in ("OK", "ERROR"):
        # Keep malformed records visible rather than reporting false success.
        return {"kind": "ERROR", "text": raw}
    return {"kind": kind, "text": text}


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


FOREGROUND_FIELDS = ("mResumedActivity", "ResumedActivity", "topResumedActivity",
                     "mFocusedActivity", "mCurrentFocus", "mFocusedApp")


def extract_foreground_package(output):
    for field in FOREGROUND_FIELDS:
        for line in output.splitlines():
            if not re.search(r"\b" + field + r"\b", line):
                continue
            match = re.search(r"([A-Za-z0-9._]+)/[A-Za-z0-9._$]+", line)
            if match:
                return match.group(1)
    return ""


def foreground_package(serial):
    for args in (("activity", "activities"), ("window",), ("activity", "top")):
        try:
            output = subprocess.check_output(
                ["adb", "-s", serial, "shell", "dumpsys", *args],
                text=True, stderr=subprocess.DEVNULL, timeout=10)
            package = extract_foreground_package(output)
            if not package and args == ("activity", "top"):
                # Android versions that expose only a top ACTIVITY header.
                match = re.search(r"(?m)^\s*ACTIVITY\s+([A-Za-z0-9._]+)/[A-Za-z0-9._$]+", output)
                if match:
                    package = match.group(1)
            if package:
                return package
        except (subprocess.SubprocessError, OSError):
            continue
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
        return {"connected": True, "name": f"{maker} {model}".strip(), "serial": serial,
                "manufacturer": maker, "model": model}
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

metadata_lock = threading.Lock()
metadata_cache = None
metadata_checked = 0.0


def update_info(force=False):
    global metadata_cache, metadata_checked
    # /api/state polls every 500 ms; avoid exhausting GitHub's API rate limit.
    with metadata_lock:
        if not force and metadata_cache is not None and time.monotonic() - metadata_checked < 120:
            return dict(metadata_cache)
        metadata_cache = _fetch_update_info()
        metadata_checked = time.monotonic()
        return dict(metadata_cache)


def _fetch_update_info():
    if TEST_MODE:
        return {'ok':True,'current':APP_VERSION,'latest':APP_VERSION,'available':False,
                'notes':'읽기 전용 lifecycle 검증: 온라인 업데이트 확인·설치 비활성',
                'url':'','sha256':'','error':'','test_mode':True}
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
            headers={"User-Agent": f"ES-DE-Sync/{APP_VERSION}",
                     "Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            contents = json.loads(r.read().decode("utf-8"))
        if contents.get("encoding") != "base64":
            raise ValueError("GitHub Contents API의 encoding이 base64가 아닙니다.")
        encoded = "".join(contents["content"].split())
        meta = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
        if not isinstance(meta, dict) or not meta.get("version"):
            raise ValueError("version.json의 version이 없습니다.")

        latest = str(meta.get("version", "")).strip().lstrip("v")
        result["latest"] = latest or APP_VERSION
        result["notes"] = str(meta.get("notes", "") or "")
        result["url"] = str(meta.get("url", "") or "")
        result["sha256"] = str(meta.get("sha256", "") or "")
        result["available"] = bool(latest) and _version_tuple(latest) > _version_tuple(APP_VERSION)
        result["ok"] = True
    except Exception as e:
        result["error"] = str(e)
        log_update(f"METADATA ERROR: {type(e).__name__}: {e}")
    return result

def _perform_update(url, expected_sha256=""):
    global update_helper
    tmpdir = None
    try:
        # Fail before installation if the mandatory log cannot be written.
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / "update.log").open("a", encoding="utf-8") as f:
            f.write("UPDATE PREPARE\n")
        tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="esde-sync-update-"))
        archive = tmpdir / "update.zip"
        req = urllib.request.Request(url, headers={"User-Agent": f"ES-DE-Sync/{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=60) as r, archive.open("wb") as f:
            shutil.copyfileobj(r, f)
        if not expected_sha256:
            raise ValueError("업데이트 파일 SHA-256이 없습니다.")
        digest = hashlib.sha256()
        with archive.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual.lower() != expected_sha256.lower():
            raise ValueError(f"업데이트 파일 SHA-256 검증 실패: {actual}")
        extract_dir = tmpdir / "extract"
        extract_dir.mkdir()
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                target = (extract_dir / member.filename).resolve()
                if not target.is_relative_to(extract_dir.resolve()):
                    raise ValueError("업데이트 ZIP에 허용되지 않은 경로가 있습니다.")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("업데이트 ZIP에 심볼릭 링크가 있습니다.")
            z.extractall(extract_dir)
        installers = list(extract_dir.rglob("install.sh"))
        if len(installers) != 1:
            raise ValueError("업데이트 패키지에 install.sh가 정확히 하나 있어야 합니다.")
        helper = tmpdir / "update-helper.py"
        shutil.copyfile(APP_DIR / "update-helper.py", helper)
        UPDATE_STATUS.unlink(missing_ok=True)
        update_helper = subprocess.Popen(
            [sys.executable, str(helper), str(installers[0]), str(STATE_DIR),
             str(update_lock_file.fileno())],
            pass_fds=(update_lock_file.fileno(),),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, cwd=str(HOME),
        )
        return True, "업데이트 후 새 창이 자동으로 열립니다. 이 창은 새로고침하지 마세요."
    except Exception as e:
        log_update(f"UPDATE ERROR: {type(e).__name__}: {e}")
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)
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

def register_profile_from_template(serial, systems, manufacturer, model):
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
    for key, value in (("DEVICE_SERIAL", serial),
                       ("DEVICE_MANUFACTURER", manufacturer), ("DEVICE_MODEL", model)):
        line = key + "=" + shlex.quote(value)
        pattern = r"(?m)^" + key + r"=.*$"
        if re.search(pattern, text):
            text = re.sub(pattern, lambda match: line, text)
        else:
            text = line + "\n" + text
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

android = AndroidService(sys.modules[__name__])

def external_blocker():
    if engine_running(): return "별도 Android engine이 실행 중입니다. 완료 후 다시 시도해 주세요."
    legacy = HOME / ".local/share/esde-familyroom-updater/esde-familyroom-gui.py"
    for entry in pathlib.Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        try:
            args = (entry/'cmdline').read_bytes().split(b'\0')
            if os.fsencode(str(legacy)) in args[:3]:
                return "기존 Dropbox 프로그램이 실행 중입니다. 종료 후 다시 시도해 주세요."
        except OSError: pass
    return ''

jobs = JobManager(STATE_DIR, external_blocker)
active_page = 'android'
window_seen = 0.0
window_session = ''
window_reserved = 0.0
cache_lock = threading.Lock()
android_cache = {'device':{'connected':False,'name':'기기 확인 중','serial':''},
                 'profile':{'exists':False,'systems':'—','path':''},'available_systems':[],
                 'running':False,'status':read_status(),'result':read_result()}
dropbox_cache = {'target':str(dropbox.DEST_ROOT),'systems':[], 'selected_systems':[], 'configured':False}
update_cache = {'current':APP_VERSION,'latest':APP_VERSION,'available':False,'ok':False,'checking':True}


def refresh_services():
    global android_cache, dropbox_cache
    a = android.state()
    d = dropbox.state()
    with cache_lock:
        android_cache = a
        dropbox_cache = d


def refresh_update(force=False):
    global update_cache
    info = update_info(force=force)
    with cache_lock: update_cache = info
    return info


def monitors():
    def services_loop():
        while True:
            try: refresh_services()
            except Exception: pass
            time.sleep(3)
    def updates_loop():
        while True:
            try: refresh_update()
            except Exception: pass
            time.sleep(120)
    for target in (services_loop,updates_loop):
        threading.Thread(target=target,daemon=True).start()


def app_state():
    with cache_lock:
        a = dict(android_cache); d = dict(dropbox_cache); u = dict(update_cache)
    snapshot = jobs.snapshot()
    a['status'] = read_status(); a['result'] = read_result()
    a['job'] = snapshot['latest'].get('android')
    a['recent_job'] = snapshot['recent'].get('android')
    a['running'] = bool(snapshot['active_job'] and snapshot['active_job']['kind']=='android')
    if a['running']:
        a['status'] = {**a['job']['progress'], 'label':a['job']['label']}
    elif a['job']:
        a['result'] = {'kind':'OK' if a['job']['state']=='success' else 'ERROR',
                       'text':a['job']['detail'] or a['job']['error']}
    d['job'] = snapshot['latest'].get('dropbox')
    d['recent_job'] = snapshot['recent'].get('dropbox')
    u['job'] = snapshot['latest'].get('update')
    u['active'] = bool(snapshot['active_job'] and snapshot['active_job']['kind']=='update')
    return {'version':APP_VERSION,'test_mode':TEST_MODE,'active_page':active_page,'active_job':snapshot['active_job'],
            'android':a,'dropbox':d,'update':u,
            # v0.41 API readers retain their fields.
            **a,'running':bool(snapshot['active_job'])}


def start_sync():
    job_id = jobs.reserve('android', STATE_DIR/'esde-sync.log')
    try: serial = android.validate_start()
    except Exception as error:
        jobs.finish(job_id,{'ok':False,'error':str(error),'detail':str(error)})
        raise
    jobs.launch(job_id,lambda progress:android.run(serial,progress))
    return job_id


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        rel = urllib.parse.unquote(urllib.parse.urlparse(path).path).lstrip('/') or 'index.html'
        target = (WEB/rel).resolve()
        return str(target) if target.is_relative_to(WEB.resolve()) else str(WEB/'.not-found')

    def log_message(self, fmt, *args): pass

    def send_json(self, body, status=200):
        data=json.dumps(body,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(data); self.wfile.flush()

    def do_GET(self):
        path=urllib.parse.urlparse(self.path).path
        if path=='/api/health':
            self.send_json({'ok':True,'version':APP_VERSION,'instance_id':INSTANCE_ID,'test_mode':TEST_MODE,'app_dir':str(APP_DIR),'state_dir':str(STATE_DIR)}); return
        if path=='/api/state': self.send_json(app_state()); return
        if path=='/api/android/state': self.send_json(app_state()['android']); return
        if path=='/api/dropbox/state': self.send_json(app_state()['dropbox']); return
        if path in ('/api/update/status','/api/update-status'):
            try:
                with lock: active=updating()
                status=json.loads(UPDATE_STATUS.read_text()) if UPDATE_STATUS.exists() else {}
                self.send_json({'active':active,**status})
            except Exception as error: self.send_json({'ok':False,'reason':str(error)},500)
            return
        if path.startswith('/api/jobs/'):
            job=jobs.get(path.removeprefix('/api/jobs/'))
            self.send_json(job if job else {'ok':False,'reason':'작업을 찾을 수 없습니다.'},200 if job else 404); return
        super().do_GET()

    def payload(self):
        length=int(self.headers.get('Content-Length','0'))
        if length<0 or length>65536: raise ValueError('요청 크기가 허용 범위를 벗어났습니다.')
        data=json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
        if not isinstance(data,dict): raise ValueError('JSON 객체가 필요합니다.')
        return data

    def do_POST(self):
        global active_page, window_seen, window_session, window_reserved
        # Same-origin requests only; loopback APIs must not accept cross-site writes.
        origin=self.headers.get('Origin')
        if origin and origin!=f'http://{self.headers.get("Host")}':
            self.send_json({'ok':False,'reason':'허용되지 않은 Origin입니다.'},403); return
        try:
            data=self.payload()
            if TEST_MODE and self.path not in ('/api/app/activate','/api/app/page','/api/app/session','/api/app/session/close','/api/app/launch-failed','/api/update/check'):
                self.send_json({'ok':False,'reason':'읽기 전용 lifecycle test mode에서는 실제 작업과 설정 변경이 금지됩니다.'},403); return
            if self.path in ('/api/app/activate','/api/app/session','/api/app/page','/api/app/session/close','/api/app/launch-failed'):
                with lock:
                    page=data.get('page',active_page)
                    if page not in ('android','dropbox'): raise ValueError('알 수 없는 화면입니다.')
                    if self.path=='/api/app/launch-failed':
                        if not window_session: window_reserved=0.0
                        self.send_json({'ok':True}); return
                    if self.path=='/api/app/session/close':
                        if data.get('session') == window_session:
                            window_session=''; window_seen=0.0; window_reserved=0.0
                        self.send_json({'ok':True}); return
                    if self.path=='/api/app/session':
                        session=data.get('session','')
                        if not isinstance(session,str) or not session: raise ValueError('session이 필요합니다.')
                        if window_session and session!=window_session and time.monotonic()-window_seen<90:
                            self.send_json({'ok':False,'reason':'기존 앱 창을 사용해 주세요.'},409); return
                        window_session=session; window_seen=time.monotonic(); window_reserved=0
                    elif self.path=='/api/app/activate':
                        alive=(bool(window_session) and time.monotonic()-window_seen<90) or (window_reserved>0 and time.monotonic()-window_reserved<15)
                        if not alive: window_reserved=time.monotonic()
                        active_page=page
                        self.send_json({'ok':True,'window_alive':alive,'active_page':page}); return
                    else: active_page=page
                    self.send_json({'ok':True,'active_page':active_page}); return
            if self.path in ('/api/android/start','/api/start'):
                job_id=start_sync(); self.send_json({'ok':True,'job_id':job_id},202); return
            if self.path in ('/api/android/register','/api/register','/api/android/systems','/api/update-systems'):
                selected=data.get('systems',[])
                if not isinstance(selected,list) or any(not isinstance(s,str) for s in selected): raise ValueError('systems 목록이 필요합니다.')
                with jobs.configuration():
                    dev=adb_info()
                    if not dev['connected']: raise ValueError('Android 기기가 연결되어 있지 않습니다.')
                    if self.path in ('/api/android/register','/api/register'):
                        ok,reason=register_profile_from_template(dev['serial'],selected,dev['manufacturer'],dev['model'])
                    else: ok,reason=update_profile_systems(dev['serial'],selected)
                self.send_json({'ok':ok,'reason':reason},200 if ok else 400); return
            if self.path=='/api/dropbox/start':
                job_id=jobs.reserve('dropbox',dropbox.LOG)
                try: plan=dropbox.mirror_plan(data.get('systems',[]))
                except Exception as error:
                    jobs.finish(job_id,{'ok':False,'error':str(error)}); raise
                jobs.launch(job_id,lambda progress:dropbox.run(plan['systems'],progress))
                self.send_json({'ok':True,'job_id':job_id},202); return
            if self.path=='/api/update/check':
                self.send_json({'ok':True,'update':refresh_update(force=True)}); return
            if self.path in ('/api/update/install','/api/update'):
                self.install_update(); return
            self.send_json({'ok':False,'reason':'API를 찾을 수 없습니다.'},404)
        except BusyError as error: self.send_json({'ok':False,'reason':str(error),'active_job':jobs.snapshot()['active_job']},409)
        except (ValueError,KeyError) as error: self.send_json({'ok':False,'reason':str(error)},400)
        except Exception as error: self.send_json({'ok':False,'reason':f'{type(error).__name__}: {error}'},500)

    def install_update(self):
        global update_lock_file, update_pending
        job_id=jobs.reserve('update',STATE_DIR/'update.log')
        handed=False
        try:
            with lock:
                update_pending=True
                update_lock_file=os.fdopen(os.dup(jobs.update_handle(job_id).fileno()),'a+')
            info=refresh_update(force=True)
            if not info.get('ok'): raise ValueError('업데이트 정보를 확인할 수 없습니다: '+info.get('error',''))
            if not info.get('available'): raise ValueError('현재 최신 버전입니다.')
            if not info.get('url'): raise ValueError('업데이트 다운로드 주소가 없습니다.')
            jobs.progress(job_id,0,1,'업데이트 다운로드 및 검증')
            ok,reason=_perform_update(info['url'],info.get('sha256',''))
            if not ok: raise RuntimeError(reason)
            self.send_json({'ok':True,'reason':reason,'job_id':job_id},202)
            update_helper.stdin.write(b'apply\n'); update_helper.stdin.close()
            handed=True
            helper=update_helper
            def wait_helper(progress):
                progress(0,1,'앱 설치 및 재시작 중')
                code=helper.wait()
                outcome=json.loads(UPDATE_STATUS.read_text()) if UPDATE_STATUS.exists() else {}
                ok=code==0 and outcome.get('ok') is True
                return {'ok':ok,'summary':'앱 업데이트 완료' if ok else '앱 업데이트 오류',
                        'detail':outcome.get('reason','업데이트 결과가 없습니다.'),'error':'' if ok else outcome.get('reason','업데이트 실패')}
            jobs.launch(job_id,wait_helper)
        except Exception as error:
            if update_helper and update_helper.stdin and not update_helper.stdin.closed:
                update_helper.stdin.close()
            jobs.finish(job_id,{'ok':False,'summary':'앱 업데이트 오류','detail':str(error),'error':str(error)})
            if handed: log_update('UPDATE HANDOFF ERROR: '+str(error))
            else: raise
        finally:
            with lock:
                if update_lock_file: update_lock_file.close()
                update_lock_file=None; update_pending=False


INSTANCE_ID = __import__('uuid').uuid4().hex

def main():
    # Direct launches can also inherit a directory removed by an update.
    try:
        os.getcwd()
    except OSError:
        os.chdir(HOME)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    instance_lock, existing_port = acquire_single_instance()
    if instance_lock is None:
        return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    PORT_FILE.write_text(str(port), encoding="utf-8")
    monitors()
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
