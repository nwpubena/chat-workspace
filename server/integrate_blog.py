"""Install or roll back the blog link and Nginx authentication gateway."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

DOMAIN = 'stinkypuppy.duckdns.org'
MARKER = '# CHAT-WORKSPACE INTEGRATION v1'


def server_blocks(source):
    # Match braces outside comments and quoted strings (including Nginx if blocks).
    cleaned, quote, comment, escaped = [], None, False, False
    for char in source:
        if comment:
            cleaned.append('\n' if char == '\n' else ' ')
            if char == '\n':
                comment = False
        elif quote:
            cleaned.append(' ')
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
            cleaned.append(' ')
        elif char == '#':
            comment = True
            cleaned.append(' ')
        else:
            cleaned.append(char)
    cleaned = ''.join(cleaned)
    for match in re.finditer(r'\bserver\s*\{', cleaned):
        start, depth, end = match.end(), 1, match.end()
        while depth and end < len(source):
            depth += int(cleaned[end] == '{') - int(cleaned[end] == '}')
            end += 1
        if depth:
            raise ValueError('Nginx server block has unmatched braces')
        yield start, end - 1, source[start:end - 1]


def integration_plan(root, token):
    if not re.fullmatch(r'[0-9a-f]{64}', token):
        raise ValueError('Invalid gateway token')
    root = Path(root)
    wsgi = root / 'opt/stinkypuppy-blog/wsgi.py'
    template = root / 'opt/stinkypuppy-blog/templates/base.html'
    nginx = root / 'etc/nginx/sites-available/stinkypuppy-blog'
    snippet = root / 'etc/nginx/snippets/chat-workspace.conf'
    original_wsgi, original_template, original_nginx = [p.read_text(encoding='utf-8') for p in (wsgi, template, nginx)]
    if any(MARKER in value for value in (original_wsgi, original_nginx)) or '/workspace/' in original_template or snippet.exists():
        raise ValueError('Existing workspace integration found; refusing to overwrite it')
    if 'from app import create_app' not in original_wsgi or 'app = create_app()' not in original_wsgi:
        raise ValueError('Blog wsgi.py differs from the supported blog; inspect it before integrating')
    if '<nav aria-label="主导航">' not in original_template:
        raise ValueError('Blog navigation differs from the supported template; inspect it before integrating')
    candidates = []
    for start, end, block in server_blocks(original_nginx):
        names = re.findall(r'\bserver_name\s+([^;]+);', block)
        if any(DOMAIN in n.split() for n in names) and re.search(r'\blisten\s+[^;]*\b443\b[^;]*\bssl\b', block):
            candidates.append(end)
    if len(candidates) != 1:
        raise ValueError('Expected exactly one HTTPS server block for the blog domain; finish HTTPS first')
    location = candidates[0]
    new_wsgi = original_wsgi.rstrip() + '''\n\n# CHAT-WORKSPACE INTEGRATION v1
from flask import g as _workspace_g

def _workspace_authorization():
    return ('', 204) if getattr(_workspace_g, 'admin', None) else ('', 401)

app.add_url_rule('/_internal/workspace-auth', '_workspace_authorization', _workspace_authorization, methods=['GET'])
'''
    new_template = original_template.replace('</nav>', '<a href="/workspace/">对话工作台 ↗</a></nav>', 1)
    new_nginx = original_nginx[:location] + '    ' + MARKER + '\n    include /etc/nginx/snippets/chat-workspace.conf;\n' + original_nginx[location:]
    configuration = '''# CHAT-WORKSPACE INTEGRATION v1
location = /workspace {
    return 302 /workspace/;
}
location = /_chat_workspace_auth {
    internal;
    proxy_pass http://127.0.0.1:18765/_internal/workspace-auth;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header Host stinkypuppy.duckdns.org;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Cookie $http_cookie;
}
location ^~ /workspace/ {
    auth_request /_chat_workspace_auth;
    error_page 401 = @chat_workspace_login;
    client_max_body_size 25m;
    proxy_pass http://127.0.0.1:8765/;
    proxy_set_header Host 127.0.0.1:8765;
    proxy_set_header X-Workspace-Gateway __TOKEN__;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_read_timeout 90s;
}
location @chat_workspace_login {
    return 302 /admin/login;
}
'''.replace('__TOKEN__', token)
    return {wsgi: new_wsgi.encode(), template: new_template.encode(), nginx: new_nginx.encode(), snippet: configuration.encode()}


def digest(content):
    return hashlib.sha256(content).hexdigest()


def atomic_write(path, content, mode=0o600, uid=None, gid=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.workspace-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        if uid is not None:
            os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(root, token):
    root = Path(root).resolve()
    plan = integration_plan(root, token)  # Validate every target before writing.
    snapshot = root / 'var/backups/chat-workspace' / ('integration-' + uuid.uuid4().hex)
    snapshot.mkdir(parents=True, mode=0o700)
    os.chmod(snapshot.parent, 0o700)
    entries = []
    for index, (path, content) in enumerate(plan.items()):
        exists = path.exists()
        stat = path.stat() if exists else None
        before = path.read_bytes() if exists else None
        if exists:
            atomic_write(snapshot / str(index), before)
        entries.append({'path': str(path.relative_to(root)), 'backup': str(index), 'before': digest(before) if exists else None,
                        'after': digest(content), 'mode': (stat.st_mode & 0o777) if exists else 0o600,
                        'uid': stat.st_uid if exists else os.getuid(), 'gid': stat.st_gid if exists else os.getgid()})
    atomic_write(snapshot / 'manifest.json', json.dumps({'root': str(root), 'entries': entries}).encode())
    try:
        for entry, (path, content) in zip(entries, plan.items()):
            atomic_write(path, content, entry['mode'], entry['uid'], entry['gid'])
    except Exception:
        rollback(snapshot)
        raise
    return snapshot


def rollback(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    root = Path(manifest['root']).resolve()
    allowed = (root / 'var/backups/chat-workspace').resolve()
    if snapshot.parent != allowed:
        raise ValueError('Unexpected backup directory')
    # Refuse partial restoration if somebody subsequently edited any target.
    changes = []
    for entry in manifest['entries']:
        path = root / entry['path']
        if not path.resolve().is_relative_to(root):
            raise ValueError('Unexpected backup target')
        current = digest(path.read_bytes()) if path.exists() else None
        if current == entry['before']:
            continue
        if current != entry['after']:
            raise ValueError('Target changed after installation; manual merge required: ' + entry['path'])
        changes.append((entry, path))
    for entry, path in reversed(changes):
        if entry['before'] is None:
            path.unlink()
        else:
            original = (snapshot / entry['backup']).read_bytes()
            if digest(original) != entry['before']:
                raise ValueError('Backup checksum mismatch')
            atomic_write(path, original, entry['mode'], entry['uid'], entry['gid'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['apply', 'rollback'])
    parser.add_argument('snapshot', nargs='?')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo')
    if args.action == 'apply':
        values = dict(line.split('=', 1) for line in Path('/etc/chat-workspace.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
        print(apply('/', values['WORKSPACE_GATEWAY_TOKEN']))
    elif args.snapshot:
        rollback(args.snapshot)
    else:
        parser.error('rollback requires a snapshot path')
