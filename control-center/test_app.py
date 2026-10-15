import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import threading
import sqlite3
from contextlib import contextmanager

from fastapi.testclient import TestClient
import app as module


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(module.app, base_url='http://127.0.0.1:8765')
        self.headers = {'X-Control-Token': module.TOKEN}

    def test_private_api_needs_token(self):
        self.assertEqual(self.client.get('/api/jobs').status_code, 403)

    def test_cross_origin_rejected(self):
        self.assertEqual(self.client.get('/api/bootstrap', headers={'Origin': 'https://evil.example'}).status_code, 403)

    def test_rebinding_host_rejected(self):
        self.assertEqual(self.client.get('/api/bootstrap', headers={'Host': 'evil.example:8765'}).status_code, 403)

    def test_bootstrap(self):
        r = self.client.get('/api/bootstrap')
        self.assertEqual(r.status_code, 200)
        self.assertIn('wechat_update', r.json()['flows'])

    def test_unknown_job_rejected(self):
        r = self.client.post('/api/jobs', json={'flow': 'arbitrary_command'}, headers=self.headers)
        self.assertEqual(r.status_code, 400)

    def test_cloud_requires_consent(self):
        r = self.client.post('/api/analyze', json={'evidence': {'query': 'test'}, 'consent': False}, headers=self.headers)
        self.assertEqual(r.status_code, 400)

    def test_cloud_rejects_http(self):
        r = self.client.post('/api/model', json={'base_url': 'http://example.com/v1', 'model': 'test', 'api_key': 'test'}, headers=self.headers)
        self.assertEqual(r.status_code, 400)

    def test_key_not_reused_for_another_provider(self):
        with patch.object(module, 'config', return_value={'base_url': 'https://old.example/v1', 'key': 'encrypted'}):
            r = self.client.post('/api/model', json={'base_url': 'https://new.example/v1', 'model': 'test'}, headers=self.headers)
        self.assertEqual(r.status_code, 400)

    def test_semantic_filters_rejected(self):
        with self.assertRaises(ValueError):
            module.search(module.Search(query='test', mode='semantic', sender='目标联系人'))

    def test_ready_service_does_not_wait_for_launcher(self):
        with patch.object(module, 'service_ready', return_value=True), patch.object(module.jobs, 'log'), patch.object(module.subprocess, 'Popen') as spawn:
            module.start_service(8000, {})
            spawn.assert_not_called()

    def test_semantic_keeps_blocks_not_first_thirty_messages(self):
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        c.execute('CREATE TABLE message(id INT,ts INT,content TEXT,type INT,sender_account_name TEXT,platform_message_id TEXT)')
        c.executemany('INSERT INTO message VALUES(?,?,?,?,?,?)', [(i,i,'哈哈' if i < 40 else '想去云南旅行',0,'目标联系人',str(i)) for i in range(1,42)])
        @contextmanager
        def fake_db(p): yield c
        blocks = [{'score': .8, 'messages': [{'id': i} for i in range(1,41)]}, {'score': .7, 'messages': [{'id':41}]}]
        with patch.object(module, 'db', fake_db), patch.object(module, 'local', return_value={'blocks':blocks,'partial':False}):
            r = module.search(module.Search(query='云南旅行',mode='semantic'))
        self.assertEqual([m['id'] for m in r['messages']], [40,41])
        c.close()

    def test_both_platform_keyword_merges_and_labels(self):
        c = sqlite3.connect(':memory:')
        # Each worker needs its own connection (real SQLite is thread-bound).
        @contextmanager
        def fake_db(p):
            con=sqlite3.connect(':memory:'); con.row_factory=sqlite3.Row
            con.execute('CREATE TABLE message(id INT,ts INT,content TEXT,type INT,sender_account_name TEXT,platform_message_id TEXT)')
            con.execute('INSERT INTO message VALUES(1,100,?,0,?,?)',('test','目标联系人','1'))
            try: yield con
            finally: con.close()
        with patch.object(module, 'db', fake_db):
            r = module.search(module.Search(platform='all',query='test'))
        self.assertEqual(r['total'],2)
        self.assertEqual({m['platform'] for m in r['messages']},{'wechat','douyin'})
        c.close()

    def test_date_order_rejected(self):
        with self.assertRaises(ValueError):
            module.search(module.Search(query='test', since='2026-09-20', until='2026-09-01'))

    def test_literal_wildcard_and_context(self):
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        c.execute('CREATE TABLE message(id INT,ts INT,content TEXT,type INT,sender_account_name TEXT,platform_message_id TEXT)')
        c.executemany('INSERT INTO message VALUES(?,?,?,?,?,?)', [(1,100,'之前',0,'我','1'),(2,101,'100%喜欢',0,'目标联系人','2'),(3,102,'1000喜欢',0,'目标联系人','3'),(4,103,'100%系统',80,'系统','4')])
        @contextmanager
        def fake_db(p): yield c
        with patch.object(module, 'db', fake_db):
            r = module.search(module.Search(query='100%'))
        self.assertEqual(r['total'], 1)
        self.assertEqual(r['messages'][0]['platform_message_id'], '2')
        self.assertTrue(r['messages'][0]['context'])
        c.close()

    def test_jobs_exclusion_failure_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = module.Jobs(Path(tmp))
            entered, finish = threading.Event(), threading.Event()
            def fail_step(key, job):
                entered.set()
                finish.wait(3)
                raise RuntimeError('simulated failure')
            with patch.object(module, 'execute', fail_step):
                first = manager.start('wechat_check')
                self.assertTrue(entered.wait(2))
                with self.assertRaises(module.HTTPException): manager.start('wechat_check')
                finish.set()
                self.wait_idle(manager)
            self.assertEqual(manager.items[0]['status'], 'failed')
            with patch.object(module, 'execute', return_value=None):
                manager.start('wechat_check', first)
                self.wait_idle(manager)
            self.assertEqual(manager.items[-1]['status'], 'completed')

    def test_interrupted_not_silently_restarted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'tasks.json').write_text(json.dumps([{'id':'old','flow':'wechat_check','status':'running','steps':[{'key':'wechat_check','status':'running'}]}]), encoding='utf-8')
            manager = module.Jobs(path)
            self.assertEqual(manager.items[0]['status'], 'interrupted')
            with self.assertRaises(module.HTTPException): manager.start('wechat_check', 'old')

    def test_dpapi_roundtrip(self):
        value = module.protect('unit-test-not-a-real-key')
        self.assertNotIn('unit-test-not-a-real-key', value)
        self.assertEqual(module.protect(value, True), 'unit-test-not-a-real-key')

    def test_retry_skips_completed_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = module.Jobs(Path(tmp))
            ran = []
            def step(key, job):
                ran.append(key)
                if key == 'wechat_convert': raise RuntimeError('conversion failed')
            with patch.object(module, 'execute', step):
                original = manager.start('wechat_update')
                self.wait_idle(manager)
            self.assertEqual(ran, ['wechat_check', 'wechat_extract', 'wechat_convert'])
            ran.clear()
            with patch.object(module, 'execute', lambda key, job: ran.append(key)):
                manager.start('wechat_update', original)
                self.wait_idle(manager)
            self.assertEqual(ran[0], 'wechat_convert')
            self.assertNotIn('wechat_extract', ran)

    def test_cancel_only_after_current_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = module.Jobs(Path(tmp))
            ran = []
            def step(key, job):
                ran.append(key)
                job['cancel'] = True
            with patch.object(module, 'execute', step):
                manager.start('wechat_update')
                self.wait_idle(manager)
            self.assertEqual(ran, ['wechat_check'])
            self.assertEqual(manager.items[0]['steps'][0]['status'], 'completed')
            self.assertEqual(manager.items[0]['status'], 'cancelled')

    def test_no_evidence_no_cloud_request(self):
        with patch.object(module, 'search', return_value={'messages': []}), patch.object(module, 'generate') as cloud:
            r = self.client.post('/api/analyze', json={'evidence': {'query': 'nothing'}, 'consent': True}, headers=self.headers)
            self.assertEqual(r.status_code, 400)
            cloud.assert_not_called()

    def test_key_never_echoed(self):
        with patch.object(module, 'config', return_value={'base_url': 'https://example.com/v1', 'model': 'x', 'key': 'encrypted-secret'}):
            r = self.client.get('/api/model', headers=self.headers)
            self.assertTrue(r.json()['has_key'])
            self.assertNotIn('encrypted-secret', r.text)

    def wait_idle(self, manager):
        import time
        for _ in range(100):
            if manager.busy is None: return
            time.sleep(.03)
        self.fail('job did not finish')


if __name__ == '__main__': unittest.main()
