"""Atomic in-process reservation plus cooperative process-wide file locks."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import copy, fcntl, json, os, threading, uuid


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


class BusyError(RuntimeError):
    pass


class JobManager:
    def __init__(self, state_dir, blocker=None):
        self.state_dir = Path(state_dir)
        self.mutex = threading.RLock()
        self.blocker = blocker or (lambda: '')
        self.active = None
        self.records = {}
        self.handles = []
        self.path = self.state_dir/'jobs.json'
        try:
            self.records = json.loads(self.path.read_text())['jobs']
            for job in self.records.values():
                if job['state'] == 'running':
                    job.update(state='error', finished_at=now(), label='오류', summary='이전 작업 중단', error='이전 backend가 종료되어 작업 완료를 확인할 수 없습니다.')
        except (OSError, ValueError, KeyError, TypeError):
            self.records = {}

    def _locks(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        handles = []
        try:
            # Same order everywhere. Holding update.lock also blocks v0.41 installers.
            for name in ('operation.lock', 'update.lock'):
                handle = (self.state_dir/name).open('a+')
                handles.append(handle)
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handles
        except BlockingIOError:
            for handle in handles: handle.close()
            raise BusyError('다른 작업 또는 앱 업데이트가 진행 중입니다.')
        except BaseException:
            for handle in handles: handle.close()
            raise

    def _check(self):
        if self.active:
            raise BusyError('이미 작업이 실행 중입니다: '+self.records[self.active]['kind'])
        reason = self.blocker()
        if reason: raise BusyError(reason)

    def _save(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'jobs':self.records}, ensure_ascii=False, indent=2)+'\n')
        os.replace(temporary, self.path)

    def reserve(self, kind, log_path=''):
        with self.mutex:
            self._check()
            handles = self._locks()
            try:
                self._check()
                job_id = uuid.uuid4().hex
                self.records[job_id] = {'job_id':job_id,'kind':kind,'state':'running',
                    'started_at':now(),'finished_at':None,'progress':{'value':0,'max':0,'percent':0},
                    'label':'준비 중','summary':'','detail':'','error':'','log_path':str(log_path)}
                self.active = job_id
                self.handles = handles
                self._save()
                return job_id
            except BaseException:
                self.active = None
                self.handles = []
                for handle in handles: handle.close()
                raise

    def update_handle(self, job_id):
        with self.mutex:
            if self.active != job_id: raise RuntimeError('활성 작업이 아닙니다.')
            return self.handles[1]

    def progress(self, job_id, value, maximum, label):
        with self.mutex:
            if self.active != job_id: return
            self.records[job_id].update(progress={'value':value,'max':maximum,
                'percent':max(0,min(100,value*100//maximum)) if maximum else 0},label=label)

    def finish(self, job_id, outcome):
        with self.mutex:
            if self.active != job_id: return
            job = self.records[job_id]
            job.update(state='success' if outcome.get('ok') else 'error', finished_at=now(),
                label='완료' if outcome.get('ok') else '오류', summary=outcome.get('summary',''),
                detail=outcome.get('detail',''),error=outcome.get('error',''))
            if 'counts' in outcome: job['counts'] = outcome['counts']
            try: self._save()
            finally:
                self.active = None
                # Close, never LOCK_UN: a detached updater can hold inherited copies.
                for handle in self.handles: handle.close()
                self.handles = []

    def launch(self, job_id, runner):
        def work():
            try:
                outcome = runner(lambda value, maximum, label:self.progress(job_id,value,maximum,label))
            except Exception as error:
                outcome = {'ok':False,'summary':'작업 오류','detail':str(error),'error':f'{type(error).__name__}: {error}'}
            self.finish(job_id, outcome)
        thread = threading.Thread(target=work, daemon=False, name='job-'+job_id)
        try: thread.start()
        except Exception as error:
            self.finish(job_id, {'ok':False,'error':str(error)})
            raise
        return thread

    @contextmanager
    def configuration(self):
        with self.mutex:
            self._check()
            handles = self._locks()
            try:
                self._check()
                yield
            finally:
                for handle in handles: handle.close()

    def get(self, job_id):
        with self.mutex: return copy.deepcopy(self.records.get(job_id))

    def snapshot(self):
        with self.mutex:
            latest = {}; recent = {}
            for job in self.records.values():
                latest[job['kind']] = copy.deepcopy(job)
                if job['state'] != 'running': recent[job['kind']] = copy.deepcopy(job)
            return {'active_job':copy.deepcopy(self.records.get(self.active)), 'latest':latest, 'recent':recent}
