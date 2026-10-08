#!/usr/bin/env python3
"""Compatibility entry point; no independent backend or browser."""
import os
from pathlib import Path
if __name__ == '__main__':
    launcher = Path(__file__).resolve().parents[1]/'esde-sync' if os.environ.get('ESDE_SYNC_TEST_MODE')=='1' else Path.home()/'.local/bin/esde-sync'
    os.execv(str(launcher), [str(launcher), '--page=dropbox'])
