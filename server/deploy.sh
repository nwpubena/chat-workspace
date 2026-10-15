#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo '请运行 sudo bash server/deploy.sh'; exit 1; }
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || { echo '适用于 Ubuntu 24.04。'; exit 1; }
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP='/opt/chat-workspace'
DATA='/var/lib/chat-workspace'
SNAPSHOT=''
CREATED_SERVICE=0
failure() {
    local failed_status=$?
    trap - ERR
    echo '安装失败，正在恢复博客入口和 Nginx 配置。' >&2
    if [[ -n "$SNAPSHOT" ]]; then
        python3 "$APP/integrate_blog.py" rollback "$SNAPSHOT" || echo '自动恢复遇到冲突，请保留输出以便人工处理。' >&2
        systemctl restart stinkypuppy-blog || true
        nginx -t && systemctl reload nginx || true
    fi
    if [[ "$CREATED_SERVICE" == 1 ]]; then
        systemctl disable --now chat-workspace.service || true
        systemctl disable --now chat-workspace-backup.timer 2>/dev/null || true
    fi
    echo '未删除工作台数据库。请发送本段错误信息继续排查。' >&2
    exit "$failed_status"
}
trap failure ERR
[[ -f /opt/stinkypuppy-blog/wsgi.py && -f /etc/nginx/sites-available/stinkypuppy-blog ]] || { echo '没有找到前面部署的博客，请先核对安装路径。'; exit 1; }
[[ ! -e "$APP" && ! -e /etc/chat-workspace.env && ! -e /etc/systemd/system/chat-workspace.service ]] || { echo '已有工作台安装或未完成安装。为避免覆盖，停止；请发送之前的报错。'; exit 1; }
systemctl is-active --quiet stinkypuppy-blog
nginx -t
nginx -V 2>&1 | grep -q -- --with-http_auth_request_module
if ss -ltnH | awk '{print $4}' | grep -Eq '(^|:)8765$'; then echo '端口 8765 已占用。'; exit 1; fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y gunicorn python3 curl
id chat-workspace &>/dev/null || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin chat-workspace
install -d -m 755 "$APP"
install -d -m 700 -o chat-workspace -g chat-workspace "$DATA"
install -m 644 "$SOURCE/app.py" "$SOURCE/wsgi.py" "$SOURCE/integrate_blog.py" "$APP/"
cp -R -- "$SOURCE/web" "$APP/"
find "$APP" -type d -exec chmod 755 {} +
find "$APP" -type f -exec chmod 644 {} +
python3 - <<'PY'
import os, secrets
from pathlib import Path
p = Path('/etc/chat-workspace.env')
with p.open('x') as f:
    f.write('WORKSPACE_GATEWAY_TOKEN=' + secrets.token_hex(32) + '\nWORKSPACE_DATA_DIR=/var/lib/chat-workspace\nWORKSPACE_PUBLIC_ORIGIN=https://stinkypuppy.duckdns.org\nPYTHONDONTWRITEBYTECODE=1\n')
os.chmod(p, 0o600)
PY
cat > /etc/systemd/system/chat-workspace.service <<'EOF'
[Unit]
Description=Chat Workspace Ubuntu companion
After=network.target
[Service]
User=chat-workspace
Group=chat-workspace
WorkingDirectory=/opt/chat-workspace
EnvironmentFile=/etc/chat-workspace.env
ExecStart=/usr/bin/gunicorn --workers 2 --threads 2 --bind 127.0.0.1:8765 --timeout 120 --error-logfile - wsgi:application
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/chat-workspace
[Install]
WantedBy=multi-user.target
EOF
CREATED_SERVICE=1
systemctl daemon-reload
systemctl enable --now chat-workspace
# Verify WSGI readiness through the local gateway without printing its token.
python3 - <<'PY'
from pathlib import Path
import time, urllib.request
config = dict(line.split('=',1) for line in Path('/etc/chat-workspace.env').read_text().splitlines() if '=' in line)
for attempt in range(20):
    try:
        request = urllib.request.Request('http://127.0.0.1:8765/health', headers={'X-Workspace-Gateway':config['WORKSPACE_GATEWAY_TOKEN']})
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
        break
    except Exception:
        if attempt == 19: raise
        time.sleep(1)
PY
SNAPSHOT="$(python3 "$APP/integrate_blog.py" apply)"
nginx -t
systemctl restart stinkypuppy-blog
# Check the blog recognizes the auth endpoint and denies anonymous access.
for attempt in {1..20}; do
    AUTH_STATUS="$(curl --max-time 2 -s -o /dev/null -w '%{http_code}' -H 'Host: stinkypuppy.duckdns.org' -H 'X-Forwarded-Proto: https' http://127.0.0.1:18765/_internal/workspace-auth || true)"
    if [[ "$AUTH_STATUS" == 401 ]]; then break; fi
    sleep 1
done
[[ "$AUTH_STATUS" == 401 ]]
systemctl reload nginx
# An anonymous HTTPS request must redirect to the blog login, never reveal data.
curl --fail --silent --show-error --resolve stinkypuppy.duckdns.org:443:127.0.0.1 -D /run/chat-workspace-check.headers -o /dev/null https://stinkypuppy.duckdns.org/workspace/
grep -Eq '^HTTP/[^ ]+ 302' /run/chat-workspace-check.headers
grep -Eiq '^location: (https://stinkypuppy.duckdns.org)?/admin/login' /run/chat-workspace-check.headers
rm -f /run/chat-workspace-check.headers
printf '%s\n' "$SNAPSHOT" > /etc/chat-workspace.integration
chmod 600 /etc/chat-workspace.integration
install -m 750 "$SOURCE/backup.sh" /usr/local/sbin/chat-workspace-backup
install -m 750 "$SOURCE/rollback.sh" /usr/local/sbin/chat-workspace-rollback
cat > /etc/systemd/system/chat-workspace-backup.service <<'EOF'
[Unit]
Description=Back up Chat Workspace database
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/chat-workspace-backup
EOF
cat > /etc/systemd/system/chat-workspace-backup.timer <<'EOF'
[Unit]
Description=Daily Chat Workspace backup
[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=600
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now chat-workspace-backup.timer
systemctl start chat-workspace-backup.service
trap - ERR
echo '工作台安装完成；博客已添加“对话工作台”按钮。'
echo '访问：https://stinkypuppy.duckdns.org/workspace/'
echo '先使用已有博客后台账号登录，再点击“对话工作台”。'
echo 'Windows 采集继续在电脑上执行，已导出的记录可在工作台导入。'
