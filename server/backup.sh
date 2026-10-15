#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo '请使用 sudo。'; exit 1; }
umask 077
install -d -m 700 /var/backups/chat-workspace
python3 - <<'PY'
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import os, sqlite3
target = Path('/var/backups/chat-workspace') / ('workspace-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + str(os.getpid()) + '.sqlite3')
with closing(sqlite3.connect('file:/var/lib/chat-workspace/workspace.sqlite3?mode=ro', uri=True)) as source:
    with closing(sqlite3.connect(target)) as backup:
        source.backup(backup)
        if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup integrity check failed')
print('工作台数据库备份完成。')
PY
find /var/backups/chat-workspace -maxdepth 1 -type f -name 'workspace-*.sqlite3' -mtime +14 -delete
