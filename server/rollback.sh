#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo '请使用 sudo。'; exit 1; }
[[ -f /etc/chat-workspace.integration ]] || { echo '没有找到集成备份记录。'; exit 1; }
SNAPSHOT="$(cat /etc/chat-workspace.integration)"
python3 /opt/chat-workspace/integrate_blog.py rollback "$SNAPSHOT"
nginx -t
systemctl restart stinkypuppy-blog
systemctl reload nginx
systemctl disable --now chat-workspace.service chat-workspace-backup.timer
echo '已撤下工作台入口并恢复博客配置。聊天数据库和备份均保留。'
