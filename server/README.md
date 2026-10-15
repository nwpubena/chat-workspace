# Ubuntu 24.04 服务器伴随服务

此目录为原 Windows 项目增加一个独立的服务器入口，适配已部署在 `stinkypuppy.duckdns.org` 的 Flask 博客。Windows 原程序保持原样。

## 功能边界

| 功能 | 服务器版 |
| --- | --- |
| 原项目的 ChatLab JSONL、RAG JSONL 导入 | 支持，也接受相同消息对象构成的 JSON 数组 |
| 增量导入去重 | 以平台、会话、消息标识联合去重；没有消息标识时生成稳定摘要 |
| 关键词、发送人、日期筛选 | 支持；关键词中的 `%`、`_` 按普通字符匹配 |
| 上下文 | 返回同平台、同会话的前后最多各两条记录 |
| 会话、发送人、日期统计 | 支持；不推断关系或人格 |
| 本次搜索结果导出 | 支持，浏览器下载 JSON |
| 微信本机提取、抖音扫码采集 | 继续在 Windows 上使用原程序 |
| ChatLab 语义检索、云端模型生成 | 本服务器版本未提供，避免显示不可用功能 |

没有演示聊天、真实账号、预置密钥或自动上传。首次安装数据为空，需要自行导入有权处理的导出文件。单个文件最多 10 MiB、100000 条消息；服务端 JSON 请求最多 25 MiB。

## 安装前提

- Ubuntu 24.04；已有 Nginx、HTTPS 证书和正常运行的 `stinkypuppy-blog` 服务。
- 博客代码在 `/opt/stinkypuppy-blog`，Gunicorn 监听 `127.0.0.1:18765`。
- 博客 WSGI 文件使用 `app = create_app()`，后台中间件在 `flask.g.admin` 保存有效管理员状态。
- 博客导航模板为 `/opt/stinkypuppy-blog/templates/base.html`；Nginx 配置在 `/etc/nginx/sites-available/stinkypuppy-blog`。
- 域名为 `stinkypuppy.duckdns.org`，Nginx HTTPS server block 已启用。

这些是与前面博客安装包一致的约定。脚本会验证结构；若站点已经改过，请先适配集成脚本，不要强行覆盖。

## 部署

将包含本目录的分支克隆到服务器，然后在仓库目录执行：

```bash
sudo bash server/deploy.sh
```

脚本会：

1. 验证 Ubuntu 版本、现有博客、Nginx auth_request 模块及端口占用。
2. 安装 Ubuntu 仓库的 Gunicorn，创建独立运行账号。服务本身只使用 Python 标准库。
3. 创建 root 只读的网关令牌、应用 systemd 服务和仅运行账号可读写的数据目录。
4. 备份博客 WSGI、导航模板及 Nginx 配置，再添加“对话工作台”按钮。
5. 在博客增加仅用于会话校验的端点，并用 Nginx auth_request 保护 `/workspace/` 下的所有页面和接口。
6. 检查 Nginx、后端就绪状态和匿名访问跳转，设置每日备份。

不需要新增公网端口或证书。安装后打开：

<https://stinkypuppy.duckdns.org/workspace/>

未登录时先进入博客后台登录；登录后点击导航中的“对话工作台”。博客退出登录或密码重置造成会话失效后，工作台的下次请求也会被拦截。

若安装出错，会尝试恢复本次修改的博客配置并停止新工作台服务，保留工作台数据库。脚本不会覆盖已有工作台安装；失败重试前请先检查错误与当前服务状态。

## 导入方式

在 Windows 上使用原项目完成采集和转换，取转换产生的 ChatLab JSONL 或 RAG JSONL。在工作台“导入记录”选择平台、文件并勾选上传确认，再点导入。

ChatLab 文件支持：

```json
{"_type":"header","meta":{"platform":"weixin","sourceSessionId":"example-conversation"}}
{"_type":"member","platformId":"example-user","accountName":"示例用户"}
{"_type":"message","sender":"example-user","timestamp":1700000000,"type":0,"content":"仅用于说明格式的合成消息","platformMessageId":"example-message-1"}
```

RAG 文件支持 `id`、`timestamp`、`sender_id`、`sender_name`、`text`、`type` 字段。时间戳支持秒和毫秒。没有会话头时，可手动填写会话标识；同一会话后续导入应保持标识一致，避免重复入库。

不接受直接上传微信原始加密数据库或浏览器登录目录。不要将聊天文件、截图、数据库或完整运行日志提交到 GitHub 排错。

## 运维

```bash
sudo systemctl status chat-workspace stinkypuppy-blog nginx --no-pager
sudo journalctl -u chat-workspace -n 50 --no-pager
sudo nginx -t
sudo /usr/local/sbin/chat-workspace-backup
```

- 程序：`/opt/chat-workspace`
- 数据：`/var/lib/chat-workspace/workspace.sqlite3`
- 配置：`/etc/chat-workspace.env`，仅 root 可读。
- 网关：`/etc/nginx/snippets/chat-workspace.conf`，仅 root 可读。
- 自动备份：`/var/backups/chat-workspace/workspace-*.sqlite3`，约保留 14 天。
- 集成前快照：`/var/backups/chat-workspace/integration-*`。

服务器内备份不提供虚拟机丢失时的恢复保障，请定期另存备份。备份包含聊天，应按私有数据保护。

## 撤下服务器入口

```bash
sudo /usr/local/sbin/chat-workspace-rollback
```

这会恢复集成前的博客入口与 Nginx 配置、停止工作台服务及备份定时器，保留工作台代码、数据库和备份。若安装后有人修改过这些博客配置，回滚会停止，要求人工合并，避免覆盖后续改动。

## 验证

本地无需 Flask、FastAPI 或 Windows 依赖即可运行服务器测试：

```bash
python3 -m unittest discover -s server -p 'test_*.py' -v
bash -n server/deploy.sh server/backup.sh server/rollback.sh
node --check server/web/app.js
```

覆盖网关与 CSRF 校验、跨站拦截、导入原子性和去重、文件格式兼容、会话上下文隔离、SQL 参数与通配符、输入限制、数据持久化和备份，以及博客会话校验、HTTPS 定位、配置备份和回滚。

本地测试不等于 Azure 已部署。还需要在目标服务器安装并验证：匿名访问跳转、管理员能进入工作台、导入合成样本、搜索结果正确、退出登录后接口不可访问。此服务未启用云端模型，不会自动将聊天发送到第三方。
