from pathlib import Path
from shutil import which
import os, re, shutil, subprocess, tempfile, time
SRC_ROMS = Path.home()/"Emulation/roms"
SRC_GAMELISTS = Path.home()/"ES-DE/gamelists"
SRC_MEDIA = Path.home()/"Emulation/tools/downloaded_media"
DEST_ROOT = Path.home()/"Dropbox/ES-DE Sync"
DEST_ROMS = DEST_ROOT/"roms"
DEST_GAMELISTS = DEST_ROOT/"gamelists"
DEST_MEDIA = DEST_ROOT/"downloaded_media"
CONFIG = Path.home()/".config/esde-familyroom-updater/systems.conf"
STATE = Path.home()/".local/state/esde-familyroom-updater"
LOG = STATE/"update.log"
ALT_MAP = {s:{"Sameboy (Standalone)":"My OldBoy! (Standalone)"} for s in ("gb", "gbc")}
ALT_RE = re.compile(r"(<altemulator>)(.*?)(</altemulator>)", re.S)

def log(s):
    with LOG.open("a",encoding="utf-8") as f: f.write(time.strftime("[%F %T] ")+s+"\n")

def systems():
    return sorted([p.name for p in SRC_ROMS.iterdir() if p.is_dir()],key=str.lower) if SRC_ROMS.is_dir() else []

def saved():
    if not CONFIG.exists(): return set()
    return {x.strip() for x in CONFIG.read_text(encoding="utf-8").splitlines() if x.strip() and not x.lstrip().startswith("#")}

def save(items):
    CONFIG.write_text("".join(f"{x}\n" for x in sorted(items,key=str.lower)),encoding="utf-8")

def supported_exts(sys):
    p=SRC_ROMS/sys/"systeminfo.txt"
    if not p.is_file(): return set()
    t=p.read_text(encoding="utf-8",errors="ignore")
    marker="Supported file extensions:"
    i=t.find(marker)
    if i<0: return set()
    block=t[i+len(marker):].split("\n\n",1)[0]
    return {x.lower() for x in re.findall(r"(?<!\w)\.[A-Za-z0-9][A-Za-z0-9+_-]*",block)}

def has_rom(sys):
    exts=supported_exts(sys)
    if not exts: return False
    for _,dirs,files in os.walk(SRC_ROMS/sys):
        dirs[:]=[d for d in dirs if not d.startswith(".")]
        for n in files:
            if not n.startswith(".") and Path(n).suffix.lower() in exts: return True
    return False

def safe(path):
    r=DEST_ROOT.resolve(); p=Path(path).resolve()
    return p!=r and str(p).startswith(str(r)+os.sep)

def remove(path,label):
    p=Path(path)
    if not p.exists() and not p.is_symlink(): return "skip"
    if not safe(p): log(f"REFUSE DELETE {label}: {p}"); return "fail"
    try:
        p.unlink() if p.is_symlink() or p.is_file() else shutil.rmtree(p)
        log(f"DELETE {label}: {p}"); return "deleted"
    except Exception as e:
        log(f"FAIL DELETE {label}: {e}"); return "fail"

def rsync_mirror(src,dst,label):
    src=Path(src); dst=Path(dst)
    if not src.is_dir(): return remove(dst,label+" source-missing")
    dst.mkdir(parents=True,exist_ok=True)
    p=subprocess.run(["rsync","-a","--delete","--delete-delay","--itemize-changes",str(src)+"/",str(dst)+"/"],text=True,capture_output=True)
    with LOG.open("a",encoding="utf-8") as f:
        if p.stdout:f.write(p.stdout)
        if p.stderr:f.write(p.stderr)
    log(("OK " if p.returncode==0 else "FAIL ")+label)
    return "ok" if p.returncode==0 else "fail"

def transform_file(path,sys):
    mp=ALT_MAP.get(sys,{})
    if not mp:return 0
    t=path.read_text(encoding="utf-8",errors="surrogateescape")
    c=0
    def repl(m):
        nonlocal c
        new=mp.get(m.group(2))
        if new is None:return m.group(0)
        c+=1; return m.group(1)+new+m.group(3)
    out=ALT_RE.sub(repl,t)
    if c:path.write_text(out,encoding="utf-8",errors="surrogateescape")
    return c

def gamelist_mirror(sys):
    src=SRC_GAMELISTS/sys; dst=DEST_GAMELISTS/sys
    if not src.is_dir(): return remove(dst,f"GAMELIST:{sys} source-missing"),0
    try:
        with tempfile.TemporaryDirectory(prefix=f"esde-{sys}-",dir=STATE) as td:
            tmp=Path(td)/sys
            shutil.copytree(src,tmp)
            count=sum(transform_file(p,sys) for p in tmp.rglob("*.xml"))
            if count: log(f"ALTEMU {sys}: {count} replacement(s)")
            return rsync_mirror(tmp,dst,f"GAMELIST:{sys}[Android]"),count
    except Exception as e:
        log(f"FAIL GAMELIST {sys}: {e}"); return "fail",0

def purge_unselected(sel):
    sel=set(sel); d=f=0
    for name,b in [("ROMS",DEST_ROMS),("GAMELISTS",DEST_GAMELISTS),("MEDIA",DEST_MEDIA)]:
        b.mkdir(parents=True,exist_ok=True)
        for child in list(b.iterdir()):
            if child.name in sel: continue
            r=remove(child,f"{name}:{child.name} unselected")
            d+=r=="deleted"; f+=r=="fail"
    return d,f

def state():
    available = systems()
    selected = saved()
    return {"target":str(DEST_ROOT), "config":str(CONFIG), "log_path":str(LOG),
            "configured":CONFIG.exists(), "selected_systems":sorted(selected),
            "systems":[{"name":s, "has_rom":has_rom(s), "selected":s in selected} for s in available]}


def mirror_plan(selected):
    """Describe the existing mirror policy without writing or executing it."""
    valid = set(systems())
    if not isinstance(selected, list) or any(not isinstance(s, str) or s not in valid for s in selected):
        raise ValueError("선택한 시스템을 Legion Go S에서 찾을 수 없습니다.")
    selected = list(dict.fromkeys(selected))
    purge = [str(p) for base in (DEST_ROMS, DEST_GAMELISTS, DEST_MEDIA)
             if base.is_dir() for p in sorted(base.iterdir()) if p.name not in selected]
    return {"systems":selected, "purge":purge,
            "mirrors":[{"system":s,"kind":kind,"source":str(src/s),"target":str(dst/s),
                        "action":"mirror" if (src/s).is_dir() else "remove"}
                       for s in selected for kind,src,dst in
                       (("rom",SRC_ROMS,DEST_ROMS),("gamelist",SRC_GAMELISTS,DEST_GAMELISTS),("media",SRC_MEDIA,DEST_MEDIA))]}


def run(selected, progress):
    plan = mirror_plan(selected)
    if not which("rsync"):
        raise RuntimeError("rsync를 찾을 수 없습니다.")
    STATE.mkdir(parents=True, exist_ok=True)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    save(plan["systems"])
    for base in (DEST_ROMS, DEST_GAMELISTS, DEST_MEDIA):
        base.mkdir(parents=True, exist_ok=True)
    ok = dele = skip = fail = conv = 0
    maximum = 1 + 3 * len(plan["systems"])
    progress(0, maximum, "선택 해제 시스템 정리")
    d, x = purge_unselected(plan["systems"])
    dele += d; fail += x
    done = 1
    progress(done, maximum, "시스템 처리 준비")
    for s in plan["systems"]:
        for kind in ("ROM", "gamelist", "media"):
            progress(done, maximum, f"{s}: {kind} 처리 중")
            if kind == "ROM": result = rsync_mirror(SRC_ROMS/s, DEST_ROMS/s, f"ROM:{s}")
            elif kind == "gamelist":
                result, count = gamelist_mirror(s); conv += count
            else: result = rsync_mirror(SRC_MEDIA/s, DEST_MEDIA/s, f"MEDIA:{s}")
            ok += result == "ok"; dele += result == "deleted"
            skip += result == "skip"; fail += result == "fail"
            done += 1
            progress(done, maximum, f"{s}: {kind} 처리 완료")
    detail = f"선택 {len(plan['systems'])}개 · 성공 {ok} · 삭제 {dele} · 건너뜀 {skip} · 실패 {fail} · 대체 에뮬레이터 변환 {conv}"
    log(f"RESULT ok={ok} del={dele} skip={skip} fail={fail} alt={conv}")
    return {"ok":fail == 0,"summary":"Dropbox 공유 라이브러리 완료" if not fail else "Dropbox 공유 라이브러리 오류",
            "detail":detail,"error":"" if not fail else f"실패 {fail}개", "counts":{"ok":ok,"del":dele,"skip":skip,"fail":fail,"alt":conv}}
