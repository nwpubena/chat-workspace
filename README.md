# Chat Workspace / 对话工作台

Windows 本地微信、抖音聊天记录工作台，复用 PyWxDump、抖音采集器与 ChatLab，把已有命令行流程组合成可视化单步任务和一键工作流。

本仓库是脱敏源码版本：不包含任何真实聊天、联系人标识、登录状态、API 密钥、统计报告、截图或预装运行环境。代码中的“目标联系人”、`wxid_your_account`、`wxid_your_contact`、`wechat_example_session`、`douyin_example_session` 均为占位符。

## 功能

- 微信本机记录提取、转换、校验、增量导入与基础报告。
- 抖音扫码登录、增量采集、导出与 ChatLab 导入。
- 单步执行、一键流程、任务互斥、失败步骤重试和步骤结束后的安全停止。
- 关键词／语义检索，支持双平台查询，显示时间、发送人、消息 ID 和附近对话。
- 本地基础统计与证据导出。
- 可选的兼容 Chat Completions 的云端生成接口；密钥使用 Windows DPAPI 加密，发送片段前需确认。

语义召回以对话片段为单位，摘录不是逐句精确命中；有限检索结果不代表全部历史。没有自动向联系人发送消息的功能。

## 目录

```text
control-center/         本地服务、网页、任务调度与测试
wechat-extract/         原生工具编排、转换、检索与测试
*.ps1 / *.cmd           Windows 启动和更新入口
upstreams.json          外部依赖来源与原测试版本
```

## 配置与运行

这不是下载即可运行的预装软件。原项目绑定本机账号与运行环境，公开版移除了这些数据，需要自行配置。修改前阅读 `SECURITY.md`。

1. 准备 Windows、Git、Node.js 和 Python。参考 `upstreams.json` 获取测试使用的外部依赖；第三方项目未复制到此仓库，也未继承它们的 Git 历史。
2. 在 `douyin-chat-export` 放置对应上游项目，按其说明安装依赖，并在 `douyin-chat-export/.venv` 创建 Python 环境；用该环境安装本仓库 `requirements.txt`。不要向本仓库添加其登录资料或采集结果。
3. 在 `wechat-extract/PyWxDump` 放置所列 PyWxDump 源码，创建 `wechat-extract/.venv-pywxdump` 环境并按上游要求安装。其微信版本兼容性需自行核验，不能保证支持所有新版微信。
4. 当前启动器期望 Node 可执行文件位于 `chatlab-runtime/node-v22.23.2-win-x64/node.exe`，ChatLab CLI 位于 `chatlab-runtime/app/node_modules/chatlab-cli/bin/chatlab.mjs`。可安装到这些位置，或统一修改启动器及 `control-center/app.py` 中的路径。
5. 在自己的本地副本中配置联系人昵称、微信账号目录、账号／联系人 ID 和 ChatLab 会话 ID。相关位置：`wechat-extract/run_pywxdump.py`、`convert_pywxdump.py`、`Import-WeChat.ps1`、`control-center/app.py`、根目录更新脚本。各处须保持一致；不要把配置后的个人标识提交到公开仓库。
6. 准备对应的 ChatLab 会话与本地数据目录。首次未导入数据时，总览或检索显示缺少数据库属于预期，不应使用别人的私聊作为测试样本。
7. 双击 `启动对话工作台.cmd`，访问 `http://127.0.0.1:8765/`。日常从 Windows 正常用户会话启动，避免从受限环境启动导致登录浏览器无法创建。

其他本机地址：ChatLab `http://127.0.0.1:3110/`；抖音面板 `http://127.0.0.1:8000/panel`。

### 微信更新

先登录电脑微信并完成同步，再启动更新；收到等待提示后从系统托盘正常退出微信。原生提取仍读取本机完整历史，ChatLab 按稳定消息 ID 跳过重复消息，仅追加缺少记录，并非只解密新增部分。正常结束后清理完整解密中间库。手机独有历史与媒体原件不保证覆盖。

### 任务与服务

服务就绪后启动任务结束，服务本身继续后台运行；关闭网页不停止后台。不要同时使用旧脚本、原生面板和工作台采集同一账号。安全停止在当前步骤结束后生效，不强杀解密进程。

### 云端生成

默认不配置云端模型。填写自己的服务地址、模型与密钥后先测试；生成时仅发送确认的有限片段，仍需自行评估服务商隐私规则和费用。工作台模型设置独立于 ChatLab 的模型设置。

## 测试

安装对应依赖后可运行：

```powershell
& '.\douyin-chat-export\.venv\Scripts\python.exe' -m unittest discover -s '.\control-center' -p 'test_*.py'
& '.\wechat-extract\.venv-pywxdump\Scripts\python.exe' -m unittest discover -s '.\wechat-extract' -p 'test_*.py'
```

单元测试使用合成消息；DPAPI 测试要求 Windows。`control-center/check_browser.py` 是针对已配置工作台的集成检查，会读取本机已有数据并生成截图，不适合未经配置的空仓库，也不要上传其截图。

## 发布边界

`.gitignore` 使用文件白名单，新增源码需人工审核后再加入。不要使用 `git add -f` 添加运行数据。任何本地配置改动都应在提交前审查差异，忽略规则不能保护已经跟踪的文件内容。第三方依赖与许可范围见 `THIRD_PARTY.md`。

## Ubuntu 服务器与博客入口

新增独立的 `server/` 伴随服务，支持已导出 ChatLab / RAG JSONL 的导入、去重、关键词检索、同会话上下文和基础统计，可接入 StinkyPuppy 博客的登录保护。Windows 原生采集程序仍在本机运行；服务器版未提供微信提取、抖音扫码采集、ChatLab 语义索引或云端生成。安装及边界见 [server/README.md](server/README.md)。

