"""Wait for Nginx's new HTTPS routing and anonymous-login protection."""
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

DOMAIN = 'stinkypuppy.duckdns.org'


def expected_redirect(status, location):
    try:
        destination = urlsplit(location)
    except ValueError:
        return False
    return (status == 302 and destination.path == '/admin/login'
            and destination.scheme in ('', 'https')
            and destination.netloc in ('', DOMAIN)
            and not destination.query and not destination.fragment)


def check_once():
    with tempfile.TemporaryDirectory(prefix='chat-workspace-check-') as directory:
        headers = Path(directory) / 'headers'
        command = ['curl', '--disable', '--silent', '--show-error', '--noproxy', '*',
                   '--max-time', '4', '--proto', '=https',
                   '--resolve', DOMAIN + ':443:127.0.0.1',
                   '--dump-header', str(headers), '--output', '/dev/null',
                   '--write-out', '%{http_code}', 'https://' + DOMAIN + '/workspace/']
        response = subprocess.run(command, capture_output=True, text=True, timeout=6)
        if response.returncode:
            return 0, '', 'curl ' + str(response.returncode)
        try:
            status = int(response.stdout.strip())
        except ValueError:
            return 0, '', 'invalid HTTP status'
        location = ''
        if headers.exists():
            for line in headers.read_text(encoding='utf-8', errors='replace').splitlines():
                if line.lower().startswith('location:'):
                    location = line.split(':', 1)[1].strip()
        return status, location, ''


def wait_for_gateway(check=check_once, attempts=30, delay=1, sleep=time.sleep, report=print):
    previous = None
    for attempt in range(attempts):
        try:
            status, location, error = check()
        except (OSError, subprocess.TimeoutExpired):
            status, location, error = 0, '', 'connection unavailable'
        if expected_redirect(status, location):
            report('HTTPS 工作台入口及匿名登录保护检查通过。')
            return True
        message = 'HTTP ' + str(status) if status else error
        if message != previous:
            report('等待 Nginx 新入口就绪：' + message)
            previous = message
        if attempt + 1 < attempts:
            sleep(delay)
    report('入口检查仍未通过，最后状态：' + (previous or 'unknown'))
    report('请保留最后输出继续排查；不会把 HTTP 200 或其他重定向当作登录保护成功。')
    return False


if __name__ == '__main__':
    raise SystemExit(0 if wait_for_gateway() else 1)
