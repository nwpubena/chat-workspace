from contextlib import closing
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app import Workspace, InputError
from integrate_blog import apply, integration_plan, rollback, server_blocks

TOKEN = 'a' * 64


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Workspace(self.tmp.name, TOKEN)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, path, body=None, **extra):
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else b''
        environ = {'REQUEST_METHOD': 'POST' if body is not None else 'GET', 'PATH_INFO': path,
                   'HTTP_HOST': '127.0.0.1:8765', 'HTTP_X_WORKSPACE_GATEWAY': TOKEN,
                   'HTTP_X_CONTROL_TOKEN': self.app.csrf, 'CONTENT_TYPE': 'application/json',
                   'CONTENT_LENGTH': str(len(payload)), 'wsgi.input': io.BytesIO(payload), 'wsgi.errors': io.StringIO()}
        environ.update(extra)
        response = []
        content = b''.join(self.app(environ, lambda status, headers: response.extend([status, dict(headers)])))
        return int(response[0].split()[0]), response[1], content

    def load(self, rows, platform='wechat', conversation=''):
        return self.app.import_records({'platform': platform, 'conversation': conversation, 'text': '\n'.join(json.dumps(row, ensure_ascii=False) for row in rows)})

    def message(self, id='msg-1', content='今天吃火锅', stamp=1792022400, sender='我'):
        return {'_type': 'message', 'platformMessageId': id, 'accountName': sender, 'timestamp': stamp, 'content': content, 'type': 0}

    def test_auth_gateway_csrf_and_origin(self):
        self.assertEqual(self.request('/api/bootstrap', HTTP_X_WORKSPACE_GATEWAY='')[0], 403)
        self.assertEqual(self.request('/api/bootstrap', HTTP_X_WORKSPACE_GATEWAY='中文')[0], 403)
        self.assertEqual(self.request('/api/bootstrap', HTTP_HOST='evil.example')[0], 403)
        self.assertEqual(self.request('/api/bootstrap', HTTP_ORIGIN='https://evil.example')[0], 403)
        self.assertEqual(self.request('/api/bootstrap', HTTP_SEC_FETCH_SITE='cross-site')[0], 403)
        self.assertEqual(self.request('/api/search', {'query': 'hi'}, HTTP_X_CONTROL_TOKEN='')[0], 403)
        self.assertEqual(self.request('/api/search', {'query': 'hi'}, HTTP_X_CONTROL_TOKEN='中文')[0], 403)
        status, headers, content = self.request('/api/bootstrap')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)['token'], self.app.csrf)
        self.assertNotIn(TOKEN.encode(), content)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_import_deduplication_and_atomic_errors(self):
        self.assertEqual(self.load([self.message()])['inserted'], 1)
        self.assertEqual(self.load([self.message()])['skipped'], 1)
        with self.assertRaises(InputError):
            self.load([self.message('new'), self.message('bad', stamp='broken')])
        self.assertEqual(self.app.overview()['records'][0]['count'], 1)
        status, _, content = self.request('/api/import', {'platform': 'wechat', 'text': '{broken'})
        self.assertEqual(status, 400)
        self.assertEqual(self.app.overview()['records'][0]['count'], 1)

    def test_chatlab_header_members_and_rag_format(self):
        rows = [{'_type': 'header', 'meta': {'platform': 'weixin', 'sourceSessionId': 'alice'}},
                {'_type': 'member', 'platformId': 'wx-1', 'accountName': '好友'},
                {'_type': 'message', 'sender': 'wx-1', 'timestamp': 1792022400, 'content': '你好', 'type': 0}]
        result = self.load(rows)
        self.assertEqual(result['conversation'], 'alice')
        self.assertEqual(self.app.search({'query': '你好'})['messages'][0]['sender'], '好友')
        with self.assertRaises(InputError):
            self.load(rows, 'douyin')
        rag = {'id': 'rag-1', 'sender_id': 'a', 'sender_name': '我', 'timestamp': 1792022400000, 'text': 'RAG 导入', 'type': 0}
        self.assertEqual(self.load([rag], conversation='rag')['inserted'], 1)
        self.assertEqual(self.load([rag], conversation='rag')['skipped'], 1)
        result = self.app.import_records({'platform': 'douyin', 'text': json.dumps([self.message('dy')])})
        self.assertEqual(result['inserted'], 1)

    def test_context_does_not_cross_conversations_or_platforms(self):
        self.load([self.message('a1', '上一条', stamp=1792022399), self.message('a2', '火锅'), self.message('a3', '下一条', stamp=1792022401)], conversation='private-A')
        self.load([self.message('b1', '不该进入A的上下文')], conversation='private-B')
        self.load([self.message('d1', '另一平台')], platform='douyin', conversation='private-A')
        result = self.app.search({'query': '火锅', 'conversation': 'private-A'})
        self.assertEqual(result['total'], 1)
        self.assertEqual([r['content'] for r in result['messages'][0]['context']], ['上一条', '下一条'])

    def test_literal_wildcards_dates_and_sql_parameters(self):
        self.load([self.message('one', '100%_完成'), self.message('two', '别的文字', sender='朋友')])
        self.assertEqual(self.app.search({'query': '%_'})['total'], 1)
        self.assertEqual(self.app.search({'query': "' OR 1=1 --"})['total'], 0)
        self.assertEqual(self.app.search({'query': '文字', 'sender': '我'})['total'], 0)
        self.assertEqual(self.app.search({'query': '文字', 'since': '2020-01-01', 'until': '2030-01-01'})['total'], 1)
        with self.assertRaises(InputError): self.app.search({'query': '文字', 'since': '2030-01-01', 'until': '2020-01-01'})
        with self.assertRaises(InputError): self.app.search({'query': '文字', 'since': 'broken'})

    def test_limits_and_type_validation(self):
        for body in ({'query': ''}, {'query': 'x', 'limit': True}, {'query': 'x', 'limit': 500}, {'query': 'x', 'platform': '../../etc'}):
            with self.assertRaises(InputError): self.app.search(body)
        self.assertEqual(self.request('/api/import', {}, CONTENT_LENGTH=str(26 * 1024 * 1024))[0], 413)
        self.assertEqual(self.request('/api/search', {}, CONTENT_LENGTH='0')[0], 411)
        self.assertEqual(self.request('/api/search', {}, CONTENT_TYPE='text/plain')[0], 415)
        self.assertEqual(self.request('/api/search', [1])[0], 400)
        self.assertEqual(self.request('/static/../../app.py')[0], 404)

    def test_static_pages_and_no_windows_execution_endpoints(self):
        self.assertEqual(self.request('/')[0], 200)
        self.assertEqual(self.request('/static/app.js')[0], 200)
        self.assertEqual(self.request('/static/style.css')[0], 200)
        self.assertEqual(self.request('/health')[0], 200)
        self.assertEqual(self.request('/api/jobs', {'flow': 'wechat_extract'})[0], 404)

    def test_persistence_workers_and_backup(self):
        self.load([self.message()])
        second = Workspace(self.tmp.name, TOKEN)
        self.assertEqual(second.csrf, self.app.csrf)
        self.assertEqual(second.search({'query': '火锅'})['total'], 1)
        backup = Path(self.tmp.name) / 'backup.sqlite3'
        with closing(sqlite3.connect(self.app.path)) as source:
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
                self.assertEqual(target.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(target.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 1)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.files = {
            'opt/stinkypuppy-blog/wsgi.py': 'from app import create_app\n\napp = create_app()\n',
            'opt/stinkypuppy-blog/templates/base.html': '<header><nav aria-label="主导航"><a href="/">文章</a><a href="/about">关于</a></nav></header>',
            'etc/nginx/sites-available/stinkypuppy-blog': '''server {
  listen 80;
  server_name stinkypuppy.duckdns.org;
  if ($host = stinkypuppy.duckdns.org) { return 301 https://$host$request_uri; }
}
server {
  listen 443 ssl;
  listen [::]:443 ssl;
  server_name stinkypuppy.duckdns.org;
  location / { proxy_pass http://127.0.0.1:18765; }
  # Comment containing a } should not end this block.
  add_header X-Example "{some text}";
}
'''}
        for name, content in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            path.chmod(0o644)

    def tearDown(self):
        self.tmp.cleanup()

    def test_auth_gateway_and_button_are_added_only_to_https(self):
        plan = integration_plan(self.root, TOKEN)
        snippet = plan[self.root / 'etc/nginx/snippets/chat-workspace.conf'].decode()
        self.assertIn('auth_request /_chat_workspace_auth;', snippet)
        self.assertIn('proxy_set_header X-Workspace-Gateway ' + TOKEN, snippet)
        self.assertIn('internal;', snippet)
        nginx = plan[self.root / 'etc/nginx/sites-available/stinkypuppy-blog'].decode()
        blocks = list(server_blocks(nginx))
        self.assertNotIn('include /etc/nginx/snippets/chat-workspace.conf', blocks[0][2])
        self.assertIn('include /etc/nginx/snippets/chat-workspace.conf', blocks[1][2])
        self.assertIn('对话工作台', plan[self.root / 'opt/stinkypuppy-blog/templates/base.html'].decode())

    def test_apply_and_rollback_restore_original_files(self):
        snapshot = apply(self.root, TOKEN)
        self.assertEqual((self.root / 'etc/nginx/snippets/chat-workspace.conf').stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError): integration_plan(self.root, TOKEN)
        rollback(snapshot)
        for name, content in self.files.items():
            self.assertEqual((self.root / name).read_text(), content)
        self.assertFalse((self.root / 'etc/nginx/snippets/chat-workspace.conf').exists())
        rollback(snapshot)  # Repeated rollback is safe.

    def test_rollback_preserves_later_user_edits(self):
        snapshot = apply(self.root, TOKEN)
        path = self.root / 'opt/stinkypuppy-blog/templates/base.html'
        path.write_text(path.read_text() + '<!-- later user edit -->')
        with self.assertRaises(ValueError): rollback(snapshot)
        self.assertIn('later user edit', path.read_text())

    def test_missing_https_fails_before_modification(self):
        path = self.root / 'etc/nginx/sites-available/stinkypuppy-blog'
        path.write_text('server { listen 80; server_name stinkypuppy.duckdns.org; }')
        with self.assertRaises(ValueError): apply(self.root, TOKEN)
        self.assertFalse((self.root / 'var/backups/chat-workspace').exists())

    def test_blog_auth_route_uses_existing_admin_state(self):
        # Execute the generated WSGI adapter with tiny stand-ins; no Flask dependency.
        import sys
        import types
        from unittest.mock import patch
        g = types.SimpleNamespace(admin=None)
        registry = {}
        fake_app = types.SimpleNamespace(add_url_rule=lambda path, name, view, **kwargs: registry.update({path: view}))
        content = integration_plan(self.root, TOKEN)[self.root / 'opt/stinkypuppy-blog/wsgi.py'].decode()
        with patch.dict(sys.modules, {'app': types.SimpleNamespace(create_app=lambda: fake_app), 'flask': types.SimpleNamespace(g=g)}):
            exec(content, {})
        view = registry['/_internal/workspace-auth']
        self.assertEqual(view(), ('', 401))
        g.admin = {'id': 1}
        self.assertEqual(view(), ('', 204))


if __name__ == '__main__':
    unittest.main()
