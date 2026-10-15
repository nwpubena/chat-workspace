"""Ubuntu companion for Chat Workspace: authenticated import, search and statistics.

The Windows extraction application remains in control-center/. This WSGI service
uses the Python standard library and is published only behind the blog gateway.
"""
import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
PLATFORMS = {'wechat': '微信', 'douyin': '抖音'}
MAX_UPLOAD = 25 * 1024 * 1024


class InputError(ValueError):
    pass


def integer(value, minimum, maximum, label):
    if isinstance(value, bool):
        raise InputError(label + '必须是整数')
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        raise InputError(label + '必须是整数')
    if str(result) != str(value) and not isinstance(value, int):
        raise InputError(label + '必须是整数')
    if not minimum <= result <= maximum:
        raise InputError(label + '超出允许范围')
    return result


def text(value, limit, label, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise InputError(label + '格式或长度不正确')
    if required and not value.strip():
        raise InputError(label + '不能为空')
    return value


def display_time(stamp):
    return datetime.fromtimestamp(stamp, TZ).isoformat()


class Workspace:
    def __init__(self, data_dir, gateway, origin='https://stinkypuppy.duckdns.org'):
        if not gateway or len(gateway) < 32:
            raise ValueError('WORKSPACE_GATEWAY_TOKEN must contain at least 32 characters')
        self.data = Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.path = self.data / 'workspace.sqlite3'
        self.gateway = gateway
        self.csrf = hmac.new(gateway.encode(), b'workspace-csrf-v1', hashlib.sha256).hexdigest()
        self.origin = origin
        with self.connect() as conn:
            conn.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY, platform TEXT NOT NULL,
                  conversation TEXT NOT NULL, message_key TEXT NOT NULL,
                  ts INTEGER NOT NULL, sender TEXT NOT NULL, content TEXT NOT NULL,
                  type INTEGER NOT NULL DEFAULT 0,
                  UNIQUE(platform,conversation,message_key));
                CREATE INDEX IF NOT EXISTS message_order
                  ON messages(platform,conversation,ts,id);
            ''')

    def connect(self):
        # The context manager closes the handle, including on import rollback.
        from contextlib import contextmanager

        @contextmanager
        def connection():
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                with conn:
                    yield conn
            finally:
                conn.close()
        return connection()

    def import_records(self, body):
        platform = body.get('platform')
        if platform not in PLATFORMS:
            raise InputError('请选择微信或抖音')
        raw = text(body.get('text'), MAX_UPLOAD, '导入内容', True).lstrip('\ufeff')
        conversation = text(body.get('conversation', ''), 200, '会话标识').strip()
        if raw.lstrip().startswith('['):
            try:
                rows = json.loads(raw)
            except (ValueError, RecursionError):
                raise InputError('JSON 格式不正确')
        else:
            rows = []
            for number, line in enumerate(raw.splitlines(), 1):
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except (ValueError, RecursionError):
                        raise InputError(f'第 {number} 行 JSON 格式不正确；未导入任何记录')
        if not isinstance(rows, list) or not rows or len(rows) > 100003:
            raise InputError('文件为空或超过 100000 条消息限制')
        members, messages = {}, []
        headers = [r for r in rows if isinstance(r, dict) and r.get('_type') == 'header']
        if len(headers) > 1:
            raise InputError('一次只导入一个会话文件')
        if headers:
            meta = headers[0].get('meta', {})
            if not isinstance(meta, dict):
                raise InputError('会话头格式不正确')
            source_platform = {'weixin': 'wechat', 'wechat': 'wechat', 'douyin': 'douyin'}.get(meta.get('platform'))
            if source_platform and source_platform != platform:
                raise InputError('文件中的平台与所选平台不同')
            conversation = conversation or meta.get('sourceSessionId') or meta.get('name') or 'default'
        conversation = text(conversation or 'default', 200, '会话标识', True)
        for row in rows:
            if not isinstance(row, dict):
                raise InputError('每条记录必须是 JSON 对象')
            if row.get('_type') == 'member':
                member_id = str(row.get('platformId', ''))
                members[member_id] = str(row.get('accountName', member_id))
        for number, row in enumerate(rows, 1):
            kind = row.get('_type')
            if kind in ('header', 'member'):
                continue
            if kind not in (None, 'message'):
                raise InputError(f'第 {number} 条记录类型不受支持')
            stamp = row.get('timestamp', row.get('ts'))
            stamp = integer(stamp, 0, 4102444800000, f'第 {number} 条消息时间戳')
            if stamp > 4102444800:
                stamp //= 1000
            sender_id = str(row.get('sender', row.get('sender_id', '')))
            sender = text(row.get('accountName', row.get('sender_name', members.get(sender_id, sender_id))) or '未知发送人', 200, '发送人', True)
            content = text(row.get('content', row.get('text', '')), 100000, '消息正文')
            message_type = integer(row.get('type', 0), 0, 10000, '消息类型')
            key = row.get('platformMessageId', row.get('id'))
            if key is None or key == '':
                key = 'hash:' + hashlib.sha256(json.dumps([stamp, sender_id, sender, message_type, content], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            key = text(str(key), 500, '消息标识', True)
            messages.append((platform, conversation, key, stamp, sender, content, message_type))
        if not messages:
            raise InputError('文件中没有消息。请使用 ChatLab JSONL 或转换后的 RAG JSONL')
        if len(messages) > 100000:
            raise InputError('一次最多导入 100000 条消息')
        with self.connect() as conn:
            before = conn.total_changes
            conn.executemany('INSERT OR IGNORE INTO messages(platform,conversation,message_key,ts,sender,content,type) VALUES(?,?,?,?,?,?,?)', messages)
            inserted = conn.total_changes - before
        return {'inserted': inserted, 'skipped': len(messages) - inserted, 'conversation': conversation, 'platform': platform}

    @staticmethod
    def evidence(row):
        return {'id': row['id'], 'platform': row['platform'], 'conversation': row['conversation'],
                'platform_message_id': row['message_key'], 'time': display_time(row['ts']),
                'sender': row['sender'], 'content': row['content'], 'type': row['type']}

    def search(self, body):
        query = text(body.get('query', ''), 400, '关键词', True).strip()
        platform = body.get('platform', 'all')
        if platform not in ('all', *PLATFORMS):
            raise InputError('平台不正确')
        limit = integer(body.get('limit', 20), 1, 50, '结果数量')
        conditions, params = ["content LIKE ? ESCAPE '\\'", 'type NOT IN (80,81)'], ['%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%']
        if platform != 'all':
            conditions.append('platform=?')
            params.append(platform)
        for column in ('conversation', 'sender'):
            value = text(body.get(column, ''), 200, column).strip()
            if value:
                conditions.append(column + '=?')
                params.append(value)
        dates = []
        for field, operator in [('since', '>='), ('until', '<')]:
            value = text(body.get(field, ''), 10, field)
            if value:
                try:
                    date = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=TZ)
                except ValueError:
                    raise InputError('日期格式必须是 YYYY-MM-DD')
                if field == 'until':
                    date += timedelta(days=1)
                stamp = int(date.timestamp())
                dates.append(stamp)
                conditions.append('ts' + operator + '?')
                params.append(stamp)
        if len(dates) == 2 and dates[0] >= dates[1]:
            raise InputError('开始日期不能晚于结束日期')
        where = ' AND '.join(conditions)
        with self.connect() as conn:
            total = conn.execute('SELECT COUNT(*) FROM messages WHERE ' + where, params).fetchone()[0]
            rows = conn.execute('SELECT * FROM messages WHERE ' + where + ' ORDER BY ts DESC,id DESC LIMIT ?', params + [limit]).fetchall()
            results = []
            for row in rows:
                before = conn.execute('SELECT * FROM messages WHERE platform=? AND conversation=? AND (ts,id)<(?,?) ORDER BY ts DESC,id DESC LIMIT 2', (row['platform'], row['conversation'], row['ts'], row['id'])).fetchall()
                after = conn.execute('SELECT * FROM messages WHERE platform=? AND conversation=? AND (ts,id)>(?,?) ORDER BY ts,id LIMIT 2', (row['platform'], row['conversation'], row['ts'], row['id'])).fetchall()
                results.append({**self.evidence(row), 'context': [self.evidence(r) for r in [*reversed(before), *after]]})
        return {'messages': results, 'total': total, 'truncated': total > len(results)}

    def overview(self):
        with self.connect() as conn:
            platforms = []
            for platform, label in PLATFORMS.items():
                counts = conn.execute('SELECT COUNT(*) count,MIN(ts) first,MAX(ts) last FROM messages WHERE platform=?', (platform,)).fetchone()
                platforms.append({'platform': platform, 'name': label, 'count': counts['count'], 'first': display_time(counts['first']) if counts['first'] is not None else None, 'last': display_time(counts['last']) if counts['last'] is not None else None})
            conversations = [dict(r) for r in conn.execute('SELECT platform,conversation,COUNT(*) count FROM messages GROUP BY platform,conversation ORDER BY platform,conversation')]
            senders = [dict(r) for r in conn.execute('SELECT platform,sender,COUNT(*) count FROM messages WHERE type NOT IN(80,81) GROUP BY platform,sender ORDER BY count DESC LIMIT 20')]
            days = [dict(r) for r in conn.execute("SELECT date(ts,'unixepoch','+8 hours') day,COUNT(*) count FROM messages WHERE type NOT IN(80,81) GROUP BY day ORDER BY day DESC LIMIT 30")]
        return {'records': platforms, 'conversations': conversations, 'senders': senders, 'days': list(reversed(days))}

    def __call__(self, environ, start_response):
        status, headers, content = '200 OK', [], b''
        path, method = environ.get('PATH_INFO', '/'), environ.get('REQUEST_METHOD', 'GET')
        def result(code, body):
            return code, [('Content-Type', 'application/json; charset=utf-8')], json.dumps(body, ensure_ascii=False).encode()
        try:
            supplied = environ.get('HTTP_X_WORKSPACE_GATEWAY', '')
            if not hmac.compare_digest(supplied.encode(), self.gateway.encode()):
                status, headers, content = result('403 Forbidden', {'detail': '请通过博客登录后访问工作台'})
            elif environ.get('HTTP_HOST', '') not in ('127.0.0.1:8765', 'localhost:8765'):
                status, headers, content = result('403 Forbidden', {'detail': '不允许的访问地址'})
            elif environ.get('HTTP_ORIGIN') and environ['HTTP_ORIGIN'] != self.origin:
                status, headers, content = result('403 Forbidden', {'detail': '不允许跨站请求'})
            elif environ.get('HTTP_SEC_FETCH_SITE') == 'cross-site':
                status, headers, content = result('403 Forbidden', {'detail': '不允许跨站请求'})
            elif method == 'POST' and not hmac.compare_digest(environ.get('HTTP_X_CONTROL_TOKEN', '').encode(), self.csrf.encode()):
                status, headers, content = result('403 Forbidden', {'detail': '页面会话过期，请刷新'})
            elif method == 'GET' and path in ('/', '/static/app.js', '/static/style.css'):
                files = {'/': ('index.html', 'text/html'), '/static/app.js': ('app.js', 'text/javascript'), '/static/style.css': ('style.css', 'text/css')}
                filename, mime = files[path]
                content = (HERE / 'web' / filename).read_bytes()
                headers = [('Content-Type', mime + '; charset=utf-8')]
            elif method == 'GET' and path == '/health':
                status, headers, content = result('200 OK', {'app': 'chat-workspace-server', 'version': 1})
            elif method == 'GET' and path == '/api/bootstrap':
                status, headers, content = result('200 OK', {'token': self.csrf, 'mode': 'server', 'capabilities': ['import', 'keyword-search', 'statistics'], 'platforms': PLATFORMS})
            elif method == 'GET' and path == '/api/overview':
                status, headers, content = result('200 OK', self.overview())
            elif method == 'POST' and path in ('/api/import', '/api/search'):
                maximum = MAX_UPLOAD if path == '/api/import' else 65536
                try:
                    size = int(environ.get('CONTENT_LENGTH', '0'))
                except ValueError:
                    size = -1
                if size <= 0:
                    status, headers, content = result('411 Length Required', {'detail': '需要有效的请求长度'})
                elif size > maximum:
                    status, headers, content = result('413 Content Too Large', {'detail': '请求过大，请将文件拆分后导入'})
                elif 'application/json' not in environ.get('CONTENT_TYPE', ''):
                    status, headers, content = result('415 Unsupported Media Type', {'detail': '请求必须是 JSON'})
                else:
                    try:
                        body = json.loads(environ['wsgi.input'].read(size))
                    except (ValueError, RecursionError):
                        raise InputError('请求 JSON 格式不正确')
                    if not isinstance(body, dict):
                        raise InputError('请求必须是 JSON 对象')
                    action = self.import_records if path == '/api/import' else self.search
                    status, headers, content = result('200 OK', action(body))
            else:
                status, headers, content = result('404 Not Found', {'detail': '页面不存在'})
        except InputError as error:
            status, headers, content = result('400 Bad Request', {'detail': str(error)})
        except Exception as error:
            environ['wsgi.errors'].write('workspace request failed: ' + type(error).__name__ + '\n')
            status, headers, content = result('500 Internal Server Error', {'detail': '服务暂时出错，请检查服务状态；没有回显聊天或密钥'})
        headers += [('Content-Length', str(len(content))), ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'), ('X-Frame-Options', 'DENY'), ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")]
        start_response(status, headers)
        return [content]


def create_app():
    return Workspace(os.environ.get('WORKSPACE_DATA_DIR', str(HERE / 'data')), os.environ.get('WORKSPACE_GATEWAY_TOKEN', ''), os.environ.get('WORKSPACE_PUBLIC_ORIGIN', 'https://stinkypuppy.duckdns.org'))
