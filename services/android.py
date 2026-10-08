"""Adapter for unchanged v0.41 policies and the unchanged shell engine."""
import os, subprocess


class AndroidService:
    def __init__(self, host):
        self.host = host
        self.process = None

    def state(self):
        h = self.host
        device = h.adb_info()
        profile = h.profile_info(device['serial']) if device['connected'] else {'exists':False,'systems':'—','path':''}
        return {'device':device,'profile':profile,'available_systems':h.available_systems(),
                'running':bool(self.process and self.process.poll() is None),
                'status':h.read_status(),'result':h.read_result()}

    def validate_start(self):
        h = self.host
        device = h.adb_info()
        if not device['connected']: raise ValueError('Android 기기가 연결되어 있지 않습니다.')
        if not h.profile_info(device['serial'])['exists']:
            raise ValueError('이 기기는 아직 등록되지 않았습니다. 먼저 기기 등록을 해 주세요.')
        ok, reason = h.is_safe_to_sync()
        if not ok: raise ValueError(reason)
        return device['serial']

    def run(self, serial, progress):
        h = self.host
        if self.validate_start() != serial: raise RuntimeError('작업 시작 전 연결된 기기가 변경되었습니다.')
        h.STATE_DIR.mkdir(parents=True, exist_ok=True)
        for path in (h.STATUS, h.RESULT): path.unlink(missing_ok=True)
        env = os.environ.copy()
        env.update(ESDE_SYNC_GUI_MODE='1',ESDE_SYNC_STATUS_FILE=str(h.STATUS),ESDE_SYNC_RESULT_FILE=str(h.RESULT))
        self.process = subprocess.Popen([str(h.ENGINE)], env=env)
        h.process = self.process
        while True:
            status = h.read_status()
            progress(status['value'],status['max'],status['label'])
            try:
                code = self.process.wait(timeout=.25)
                break
            except subprocess.TimeoutExpired: pass
        status = h.read_status()
        progress(status['value'],status['max'],status['label'])
        result = h.read_result()
        ok = code == 0 and result is not None and result['kind'] == 'OK'
        detail = result['text'] if result else f'engine 결과가 없습니다. 종료 코드: {code}'
        return {'ok':ok,'summary':'Android 동기화 완료' if ok else 'Android 동기화 오류',
                'detail':detail,'error':'' if ok else detail}
