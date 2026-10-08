"""Offline release validation using disposable HOME fixtures and public ZIPs."""
from pathlib import Path
import sys,os,json,zipfile,hashlib,stat,tempfile,subprocess,importlib.util,ast
from unittest.mock import patch
from build_release_v044 import PRODUCT_FILES, build

def run(ROOT):
 WORKSPACE=Path(__file__).resolve().parents[1]
 (ROOT/'results').mkdir()
 T=ROOT/'fixtures';T.mkdir();SOURCE=T/'extracted/ES-DE-Sync-v0.44'
 # Both archives and all installations are private fixtures under /tmp.
 files=list(PRODUCT_FILES)
 EXPECTED={name:{'sha256':hashlib.sha256((WORKSPACE/name).read_bytes()).hexdigest(),'mode':oct(stat.S_IMODE((WORKSPACE/name).stat().st_mode))} for name in files}
 ARCHIVE=WORKSPACE/'releases/ES-DE-Sync-v0.44.zip'
 V043=T/'stable/ES-DE-Sync-v0.43'
 baseline=WORKSPACE/'releases/ES-DE-Sync-v0.43.zip'
 assert hashlib.sha256(baseline.read_bytes()).hexdigest()=='04fea5f853232a12e10358de548eeeddacfa640e07032f93871496edfaea58c0'
 with zipfile.ZipFile(baseline) as archive:
  assert archive.testzip() is None
  assert set(archive.namelist())=={'ES-DE-Sync-v0.43/'+name for name in files}
  for info in archive.infolist():
   assert (T/'stable'/info.filename).resolve().is_relative_to(T/'stable')
   assert stat.S_IFMT(info.external_attr>>16)==stat.S_IFREG
  archive.extractall(T/'stable')
  for info in archive.infolist():(T/'stable'/info.filename).chmod((info.external_attr>>16)&0o777)
 results={}
 assert len(files)==len(set(files))==20
 assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()=='3038ce366071fbc76f5f7d84eef52fef422beb4c2639f4564473fcf5750f1302'
 assert ARCHIVE.stat().st_size==47864
 built=ROOT/'built-v044.zip'
 summary=build(built)
 assert built.read_bytes()==ARCHIVE.read_bytes(), 'Temporary build differs from validated release'
 original=built.read_bytes()
 try:build(built)
 except FileExistsError:pass
 else:raise AssertionError('Existing ZIP was overwritten')
 assert built.read_bytes()==original
 results['temporary_build_exact_release_sha256_and_no_overwrite']='PASS'
 python_files=[name for name in files if name.endswith('.py') or name=='esde-sync']
 python_files+=['tests/build_release_v044.py','tests/test_release_v044.py']
 for name in python_files:compile((WORKSPACE/name).read_text(encoding='utf-8'),name,'exec')
 for name in files:
  if name.endswith('.sh') or name=='esde-sync-engine':subprocess.run(['bash','-n',str(WORKSPACE/name)],check=True)
 results['python_and_shell_syntax']='PASS'
 
 with zipfile.ZipFile(ARCHIVE) as z:
  assert z.testzip() is None
  assert len(set(z.namelist()))==len(z.namelist())==len(EXPECTED)
  assert set(z.namelist())=={'ES-DE-Sync-v0.44/'+name for name in EXPECTED}
  for info in z.infolist():
   target=(T/'extracted'/info.filename).resolve();assert target.is_relative_to((T/'extracted').resolve())
   assert stat.S_IFMT(info.external_attr>>16)==stat.S_IFREG
  z.extractall(T/'extracted')
  for info in z.infolist():
   name=info.filename.removeprefix('ES-DE-Sync-v0.44/');p=SOURCE/name;p.chmod((info.external_attr>>16)&0o777)
   assert hashlib.sha256(p.read_bytes()).hexdigest()==EXPECTED[name]['sha256']
   assert stat.S_IMODE(p.stat().st_mode)==int(EXPECTED[name]['mode'],8)
 assert len(list(SOURCE.rglob('*.desktop')))==1
 results['zip_crc_whitelist_extract_hash_permissions']='PASS'
 def env(home):
  e={**os.environ,'HOME':str(home),'XDG_CONFIG_HOME':str(home/'.config'),'XDG_STATE_HOME':str(home/'.local/state'),'PYTHONDONTWRITEBYTECODE':'1'}
  for name in ('ESDE_SYNC_TEST_MODE','ESDE_SYNC_TEST_ROOT','ESDE_SYNC_UPDATE_LOCK_FD'):e.pop(name,None)
  return e
 def write(home,name,body,mode=0o600):
  p=home/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(body);p.chmod(mode)
 def seed(home):
  home.mkdir(parents=True)
  data={'.config/esde-sync/profiles/MINI-FIXTURE.conf':b'SYSTEMS="gb gbc"\nALTEMU_MAP_GB="Sameboy (Standalone)=>My OldBoy! (Standalone)"\nCUSTOM=keep\n',
  '.config/esde-sync/profiles/AIR-FIXTURE.conf':b'SYSTEMS="gb gbc"\nALTEMU_MAP_GBC="Sameboy (Standalone)=>My OldBoy! (Standalone)"\n',
  '.config/esde-familyroom-updater/systems.conf':b'# saved selection\ngbc\ngb\n',
  '.local/state/esde-sync/gui-status.tsv':b'0\t1\tfixture\n',
  '.local/state/esde-sync/gui-result.txt':b'OK\nretained result\n',
  '.local/state/esde-sync/esde-sync.log':b'retained engine log\n',
  '.local/state/esde-sync/custom.runtime':b'keep app runtime',
  '.local/state/esde-familyroom-updater/update.log':b'retained dropbox log\n',
  '.local/state/esde-familyroom-updater/custom.db':b'keep dropbox runtime',
  '.local/share/esde-familyroom-updater/user.db':b'keep unknown family user asset',
  'Dropbox/ES-DE Sync/roms/gb/fixture.gb':b'DROPBOX-ROM',
  'Dropbox/ES-DE Sync/downloaded_media/gb/covers/fixture.png':b'DROPBOX-MEDIA',
  'Dropbox/ES-DE Sync/gamelists/gb/gamelist.xml':b'<gameList/>',
  'Emulation/roms/gb/fixture.gb':b'LEGION-ROM',
  'Emulation/tools/downloaded_media/gb/covers/fixture.png':b'LEGION-MEDIA',
  'ES-DE/gamelists/gb/gamelist.xml':b'<gameList/>'}
  for name,body in data.items():write(home,name,body)
  return data
 protected_prefixes=['.config/esde-sync/profiles','.config/esde-familyroom-updater/systems.conf','.local/state/esde-sync','.local/state/esde-familyroom-updater','Dropbox','Emulation','ES-DE/gamelists']
 def digest(p):return {'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'mode':stat.S_IMODE(p.stat().st_mode),'mtime_ns':p.stat().st_mtime_ns}
 def protected(home):
  rows={}
  for name in protected_prefixes:
   base=home/name
   for p in ([base] if base.is_file() else base.rglob('*') if base.is_dir() else []):
    if p.is_file() and p.name not in ('update.lock','gui-port'):rows[str(p.relative_to(home))]=digest(p)
  p=home/'.local/share/esde-familyroom-updater/user.db';rows[str(p.relative_to(home))]=digest(p)
  return rows
 def preserve(home,old):
  for name,row in old.items():assert (home/name).exists() and digest(home/name)==row,name
  assert {k:v for k,v in protected(home).items() if k not in old}=={},'unexpected protected data changes'
 def version(home):
  text=(home/'.local/share/esde-sync/esde-sync-gui.py').read_text()
  return next(ast.literal_eval(n.value) for n in ast.parse(text).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='APP_VERSION' for t in n.targets))
 def install(source,home,label):
  run=subprocess.run(['bash',str(source/'install.sh')],env=env(home),cwd=str(home),capture_output=True,text=True,timeout=30)
  (ROOT/'results'/('package-'+label+'.log')).write_text(run.stdout+run.stderr)
  assert run.returncode==0,(label,run.stdout,run.stderr)
 def validate(home):
  assert version(home)=='0.44'
  assert (home/'.local/bin/esde-sync').read_bytes()==(SOURCE/'esde-sync').read_bytes()
  app=home/'.local/share/esde-sync'
  expected=[n for n in EXPECTED if n.startswith(('web/','services/')) or n in ('esde-sync-gui.py','esde-sync-engine','update-helper.py')]
  assert {str(p.relative_to(app)) for p in app.rglob('*') if p.is_file()}==set(expected)
  for name in expected:assert hashlib.sha256((app/name).read_bytes()).hexdigest()==EXPECTED[name]['sha256']
  for name in ('esde-sync-gui.py','esde-sync-engine','update-helper.py'):assert (app/name).stat().st_mode&0o111
  assert (home/'.local/bin/esde-sync').stat().st_mode&0o111
  menu=home/'.local/share/applications';entries=list(menu.glob('*.desktop'));assert len(entries)==1 and entries[0].name=='esde-sync.desktop'
  assert entries[0].read_text()==(SOURCE/'esde-sync.desktop').read_text().replace('@HOME@',str(home))
  assert 'Categories=Utility;\n' in entries[0].read_text()
  for folder,names in [('esde-sync-hub',['esde-sync-hub.py','esde-sync-hub-launch.sh','esde-sync-action.sh']),('esde-familyroom-updater',['esde-familyroom-gui.py','esde-familyroom-launch.sh'])]:
   for name in names:
    p=home/'.local/share'/folder/name;assert p.stat().st_mode&0o111
    assert all(token not in p.read_text() for token in ('ThreadingHTTPServer','serve_forever','--app=','Popen'))
 # Pristine HOME installation must create code/menu only, without user libraries/config.
 clean=T/'empty/home';clean.mkdir(parents=True);install(SOURCE,clean,'empty-home');validate(clean)
 assert not (clean/'.config/esde-sync/profiles').exists() and not (clean/'.config/esde-familyroom-updater/systems.conf').exists()
 assert all(not (clean/name).exists() for name in ('Dropbox','Emulation','ES-DE'))
 results['pristine_home_install_no_user_data_created']='PASS'
 # Fresh install from the actual extracted archive.
 home=T/'fresh/home';seed(home);old=protected(home);install(SOURCE,home,'fresh');validate(home);preserve(home,old)
 results['fresh_install_from_zip']='PASS';results['fresh_install_user_data_preserved']='PASS'
 # v0.43 install/migration fixture, then new ZIP installer; no old backend launched.
 home=T/'upgrade/home';seed(home);install(V043,home,'v043-seed');assert version(home)=='0.43'
 for name in ('esde-sync.desktop','esde-familyroom-update.desktop'):write(home,'.local/share/applications/'+name,b'[Desktop Entry]\nName=legacy fixture\n')
 old=protected(home);install(SOURCE,home,'upgrade');validate(home);preserve(home,old)
 results['v043_to_v044_upgrade_from_zip']='PASS';results['upgrade_legacy_menu_cleanup']='PASS';results['upgrade_user_data_preserved']='PASS'
 # Post-commit failure must restore real v0.43 fixture files/menus and protected data.
 home=T/'rollback/home';seed(home);install(V043,home,'rollback-v043-seed');assert version(home)=='0.43'
 for name in ('esde-sync.desktop','esde-familyroom-update.desktop'):write(home,'.local/share/applications/'+name,b'[Desktop Entry]\nName=legacy rollback fixture\n')
 old=protected(home)
 code={str(p.relative_to(home)):digest(p) for name in ('.local/share/esde-sync','.local/share/esde-sync-hub','.local/share/esde-familyroom-updater','.local/share/applications') for p in (home/name).rglob('*') if p.is_file()};code['.local/bin/esde-sync']=digest(home/'.local/bin/esde-sync')
 spec=importlib.util.spec_from_file_location('zip_installer',SOURCE/'install-transaction.py');i=importlib.util.module_from_spec(spec);spec.loader.exec_module(i);real=i.manifest;app=home/'.local/share/esde-sync'
 def fail(root):
  if root==app:raise RuntimeError('fixture failure after commit')
  return real(root)
 old_env=os.environ.copy()
 try:
  os.environ.update(env(home))
  with patch.object(i,'manifest',side_effect=fail):
   try:i.install(SOURCE)
   except RuntimeError as error:assert 'fixture failure' in str(error)
   else:raise AssertionError('failure not injected')
 finally:os.environ.clear();os.environ.update(old_env)
 assert version(home)=='0.43'
 for name,row in code.items():assert (home/name).exists() and digest(home/name)==row,name
 preserve(home,old)
 assert not list((home/'.local/share').glob('.esde-sync-stage-*'))
 assert not list((home/'.local/bin').glob('.esde-sync-backup-*'))
 results['post_commit_rollback_restores_v043_code_launcher_menus']='PASS';results['rollback_user_data_preserved']='PASS';results['rollback_temporary_cleanup']='PASS'
 print(json.dumps({'status':'PASS','tests':results,'actual_install_or_sync':False},indent=2))
 return results

if __name__ == '__main__':
 with tempfile.TemporaryDirectory(prefix='esde-v044-public-test-') as temporary:
  fixture_root=Path(temporary).resolve()
  run(fixture_root)
 assert not fixture_root.exists(), 'Temporary fixture cleanup failed'
 print('Temporary fixture cleanup: PASS')
