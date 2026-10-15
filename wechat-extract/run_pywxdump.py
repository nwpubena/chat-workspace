"""Orchestrate unmodified PyWxDump APIs; no custom key/decryption/parser code.

Keys stay in memory. Temporary full databases are removed on exit.
Only the selected private conversation is exported permanently.
"""
import contextlib
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import time

logging.disable(logging.CRITICAL)  # Upstream error text may include database keys.
import psutil
import pywxdump as wx
from pywxdump.wx_core.utils import verify_key
from pywxdump.db.dbbase import DatabaseSingletonBase

ROOT = Path(__file__).resolve().parent
ACCOUNT = Path(r'C:\path\to\WeChat Files\wxid_your_account')
TARGET = 'wxid_your_contact'


def emit(**items):
    print(json.dumps(items, ensure_ascii=False), flush=True)


def run():
    # Suppress upstream incidental personal-info prints, not extraction results.
    with open(os.devnull, 'w') as null, contextlib.redirect_stdout(null):
        infos = wx.get_wx_info(wx.WX_OFFS, is_print=False, save_path=None)
    matches = [i for i in infos if i.get('wxid') == ACCOUNT.name and i.get('key')]
    if len(matches) != 1:
        raise RuntimeError('PyWxDump could not identify the expected logged-in account')
    secret = matches[0]['key']
    if not verify_key(bytes.fromhex(secret), str(ACCOUNT / 'Msg/MicroMsg.db')):
        raise RuntimeError('PyWxDump key verification failed')
    pid = matches[0]['pid']
    infos.clear()
    matches.clear()
    workbase = ROOT / 'temporary'
    workbase.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pywxdump_', dir=workbase) as tmpname:
        tmp = Path(tmpname).resolve()
        if not tmp.is_relative_to(workbase.resolve()):
            raise RuntimeError('Temporary path outside expected workspace')
        probe = tmp / 'probe.db'
        result = wx.decrypt(secret, str(ACCOUNT / 'Msg/MicroMsg.db'), str(probe))
        if not result or not result[0]:
            raise RuntimeError('PyWxDump native decrypt failed (key omitted)')
        del result
        con = sqlite3.connect(probe)
        checks = con.execute('PRAGMA quick_check').fetchall()
        contact = con.execute('SELECT UserName,NickName,Remark FROM Contact WHERE UserName=?', (TARGET,)).fetchone()
        con.close()
        if checks != [('ok',)] or not contact or contact[1] != '目标联系人':
            raise RuntimeError('Native-decrypted contact database failed validation')
        emit(stage='native_key_and_decrypt_verified', project_version=wx.__version__, target=contact[1])
        emit(stage='waiting_for_wechat_exit', reason='Let WeChat checkpoint its WAL normally; key retained only in memory')
        deadline = time.monotonic() + 1800
        while psutil.pid_exists(pid):
            if time.monotonic() > deadline:
                raise RuntimeError('Timed out waiting for normal WeChat exit; rerun while logged in')
            time.sleep(2)
        time.sleep(1)
        sources = [ACCOUNT / 'Msg/MicroMsg.db', *sorted((ACCOUNT / 'Msg/Multi').glob('MSG[0-9]*.db'))]
        pending_wals = [p.name for p in sources if Path(str(p) + '-wal').exists() and Path(str(p) + '-wal').stat().st_size > 32]
        if pending_wals:
            raise RuntimeError('Nonempty WAL files remain after exit; native offline export cannot promise latest records')
        emit(stage='wechat_closed', databases=len(sources))
        audit = []
        decrypted = []
        target_count = 0
        for source in sources:
            dest = tmp / ('de_' + source.name)
            result = wx.decrypt(secret, str(source), str(dest))
            if not result or not result[0]:
                raise RuntimeError('Native decryption failed for ' + source.name)
            del result
            conn = sqlite3.connect(dest)
            integrity = conn.execute('PRAGMA quick_check').fetchall()
            if integrity != [('ok',)]:
                raise RuntimeError('Native decrypted database failed quick_check: ' + source.name)
            count = conn.execute('SELECT COUNT(*) FROM MSG WHERE StrTalker=?', (TARGET,)).fetchone()[0] if source.name != 'MicroMsg.db' else 0
            conn.close()
            target_count += count
            audit.append({'database': source.name, 'bytes': source.stat().st_size,
                          'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'target_rows': count, 'integrity': 'ok'})
            decrypted.append({'db_path': str(source), 'de_path': str(dest)})
            emit(stage='native_decrypt', database=source.name, target_rows=count, integrity='ok')
        secret = None
        merge = tmp / 'merged.db'
        emit(stage='native_merge_started')
        merged = wx.merge_db(decrypted, str(merge))
        if merged != str(merge):
            raise RuntimeError('PyWxDump native merge failed')
        output = ROOT / 'pywxdump-exports' / datetime.now().strftime('%Y%m%d_%H%M%S')
        output.mkdir(parents=True, exist_ok=False)
        config = {'key': str(output), 'type': 'sqlite', 'path': str(merge)}
        conn = sqlite3.connect(merge)
        merged_count = conn.execute('SELECT COUNT(*) FROM MSG WHERE StrTalker=?', (TARGET,)).fetchone()[0]
        times = conn.execute('SELECT MIN(CreateTime),MAX(CreateTime) FROM MSG WHERE StrTalker=?', (TARGET,)).fetchone()
        conn.close()
        try:
            emit(stage='native_json_export_started', target_rows=merged_count)
            result = wx.export_json(TARGET, str(output), config, my_wxid=ACCOUNT.name)
            if not result or not result[0]:
                raise RuntimeError('PyWxDump native JSON export failed')
        finally:
            for pool in list(DatabaseSingletonBase._db_pool.values()):
                pool.close()
            DatabaseSingletonBase._db_pool.clear()
        files = list(output.glob(TARGET + '_*.json'))
        records = []
        for path in files:
            records.extend(json.loads(path.read_text(encoding='utf-8')))
        if len(records) != merged_count or any(not isinstance(r, dict) or r['room_name'] != TARGET for r in records):
            raise RuntimeError('Exported JSON count or target validation failed')
        required = {'MsgSvrID','CreateTime','talker','is_sender','msg'}
        if any(not required <= r.keys() or not r['CreateTime'] for r in records):
            raise RuntimeError('Exported JSON is missing required time/sender/message fields')
        report = {'status': 'complete', 'project': 'https://github.com/alanhzw/PyWxDump',
                  'commit': '8330cb974a8ef2155a308799dd943b218b9dfc3d', 'version': wx.__version__,
                  'extraction': 'Unmodified PyWxDump get_wx_info / decrypt / merge_db / export_json',
                  'target': {'wxid': TARGET, 'nickname': contact[1], 'remark': contact[2]},
                  'source_rows': target_count, 'merged_rows': merged_count, 'exported_messages': len(records),
                  'first_message': datetime.fromtimestamp(times[0]).isoformat(),
                  'last_message': datetime.fromtimestamp(times[1]).isoformat(), 'timezone': 'Asia/Shanghai',
                  'databases': audit, 'native_json_fields': sorted(required),
                  'limitations': ['Only history saved in this PC account.',
                                 'JSON export retains media metadata/paths, not image/video/voice binaries.',
                                 'Native quote rendering combines reply body and quote in msg; normalize before RAG.',
                                 'Native sender labels for system events need normalization before behavioral statistics.'],
                  'files': {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in output.iterdir()}}
        (output / '提取报告.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (ROOT / 'pywxdump-latest.json').write_text(json.dumps({'output': str(output), 'message_count': len(records)}, ensure_ascii=False, indent=2), encoding='utf-8')
        emit(stage='export_verified', messages=len(records), first=report['first_message'], last=report['last_message'], output=str(output))
    emit(stage='temporary_full_databases_removed', message='Only target exports retained; WeChat can be reopened')


if __name__ == '__main__':
    run()
