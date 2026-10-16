# QQ Group AI Bot & Debugger

基于 NapCat / OneBot 的 QQ 群聊 AI Bot，配有 CLI 控制台和本地 WebUI 管理页面。支持群聊参与决策、身份与引用识别、图片理解、共享表情库、记忆与表达学习，以及按群覆盖的配置和提示词。

## 运行环境

- Windows、PowerShell 和 Python 3.10 或以上版本。
- 已安装并登录的 QQ / NapCat，启用 OneBot HTTP 服务。
- 本机可用且已完成认证的 Codex CLI，以及对应的模型与生图权限。
- QQ NapCat MCP 项目和它的 Python 虚拟环境；本项目使用的上游是 [ptrel1/napcat_mcp](https://github.com/ptrel1/napcat_mcp)。

本项目当前包含 Windows 控制台和进程管理代码，其他平台未验证。NapCat、QQ 和模型服务需要分别安装、配置；本仓库不包含这些程序及账号凭据。

## 目录结构

部署目录使用以下布局，移动目录时请整体保留相对关系：

```text
qqmcp/
├── Start-QQ-Group-Bot-Debug.cmd
├── Start-QQ-Group-Bot.ps1
├── Start-QQ-NapCat.ps1
├── Stop-QQ-All.cmd
├── group-bot/
│   ├── bot.py / debug_console.py / debug_webui.py
│   ├── webui/                       # 前端源码
│   ├── settings.example.json        # 无私人数据的配置模板
│   ├── settings.json                # 全局配置，本地保存
│   ├── persona-default.txt          # 初始人设模板
│   ├── persona.txt / reply_protocol.txt
│   ├── group-config/<群号>/         # 群级配置与提示词覆盖
│   ├── runtime/                     # 默认群运行数据
│   ├── runtime-<群号>/              # 其他群运行数据
│   └── shared-emoji-library/        # 共享表情原图、预览与索引
├── qq-mcp/                          # 独立安装的上游项目（不纳入 Git）
│   ├── .venv/
│   └── .env
├── runtime/                         # 启动器日志
└── licenses/                        # 上游许可证与来源说明
```

开发工作区的代码位于 `work/group-bot/`，启动器位于 `work/launchers/`。部署时将它们分别复制为上图中的 `group-bot/` 和根目录启动脚本。`work/maibot-reference/`、下载的运行时和 `outputs/` 是本地参考或调试产物，不属于发布源码。

## 首次配置与启动

以下命令在部署目录执行。已有配置的安装请跳过模板复制，避免覆盖自己的设置。

1. 单独取得 QQ NapCat MCP 项目，放入 `qq-mcp/`。在该目录创建虚拟环境、安装上游依赖，再安装 Bot 使用的图片处理依赖：

   ```powershell
   py -3 -m venv .\qq-mcp\.venv
   .\qq-mcp\.venv\Scripts\python.exe -m pip install -e .\qq-mcp
   .\qq-mcp\.venv\Scripts\python.exe -m pip install Pillow
   ```

2. 复制 `qq-mcp/.env.example` 为 `qq-mcp/.env`，按实际 NapCat HTTP 地址和访问令牌填写 `NAPCAT_HOST`、`NAPCAT_TOKEN`。Bot 通过 HTTP 直接访问 NapCat；群聊授权使用 Bot 自己的 `chat_group_whitelist`。MCP 服务端自己的群限制和只读选项需独立配置。

3. 创建 Bot 本地配置：

   ```powershell
   Copy-Item .\group-bot\settings.example.json .\group-bot\settings.json
   ```

   将 `group_id` 和 `chat_group_whitelist` 中的示例群号替换成自己的群号，确认 `model` 是当前账号可用的模型，再设置 `enabled: true`。模板默认关闭回复和生图，便于先核对配置。默认人设是 yonagi；首次读取人设时，如缺少 `persona.txt`，会从随源码提供的 `persona-default.txt` 自动创建，已有的个人和群级人设不会被覆盖。

4. 修改 `Start-QQ-NapCat.ps1` 开头的 `$napcatDir` 和 `$qqExe`。现有脚本保留了本机 `D:\QQNT\NapCat`、`D:\QQNT\QQ.exe` 安装位置，迁移到其他电脑时必须调整。

5. 在 CMD 中运行：

   ```bat
   Start-QQ-Group-Bot-Debug.cmd
   ```

   CLI 会打印 WebUI 地址并打开浏览器。普通 CLI 模式下输入 `/webui` 也可打开。服务绑定本机地址，端口动态分配；带访问令牌的调试链接应保留在本机。

默认启动器启用无冷却调试模式；需要遵循配置冷却时，可运行：

```powershell
.\Start-QQ-Group-Bot.ps1 -WebUI -NoCooldown:$false
```

无冷却模式仍受其他参与决策和发送限制影响。调试完成后可在 WebUI「行为设置 → 参与群聊的节奏 → 回复冷却」切换。

## WebUI 与 CLI

WebUI 左侧是群列表，右侧按群分为聊天记录（聊天气泡 + 执行过程）、运行状态、行为设置、人设与规则、群记忆；所有群总览、共享表情库和命令行在左侧栏底部。设置按用途分组，开关和概率直接拖动，每项都附中文说明。默认浅色主题，可切换深色模式。

- 会话列表按群名、群号搜索；对话流程按事件、角色、消息 ID、级别筛选。
- 事件详情显示触发消息、上下文 ID、引用原 ID、引用摘要、执行阶段与结构化结果。找不到引用时标记 `[引用记录未找到]`。
- 数据库和表情库支持查询与管理；修改、删除会影响 Bot 后续使用的数据。
- 删除本地对话记录会同时清除对应 Bot 上下文，不会撤回 QQ 消息。
- 日志展示执行阶段、模型返回的分析摘要与错误，不代表模型完整内部思考过程。

CLI 保留短命令匹配和 Tab 补全。常用命令：

| 命令或按键 | 用途 |
| --- | --- |
| `/help` | 查看当前支持的完整命令与参数 |
| `/status` | 查看运行、队列与记录状态 |
| `/config` | 查看或调整配置 |
| `/whitelist` | 管理授权群 |
| `/webui` | 输出链接并打开调试页面 |
| `/exit`、`/quit` | 保存待处理状态并退出调试后台及其工作进程 |
| ← / → | 切换群聊 |
| Ctrl+F | 搜索群名或群号 |

关闭浏览器页签不会退出后台。需要更新代码时，先 `/exit` 再手动启动；避免重复运行多个后台写入同一数据目录。

## 配置保存与继承

在 WebUI 点“保存”会校验并写入磁盘，修改前保留备份。重启会重新读取配置，无需每次重新设置。

| 内容 | 保存位置 | 生效方式 |
| --- | --- | --- |
| 全局配置 | `group-bot/settings.json` | 所有群默认使用 |
| 群级覆盖 | `group-bot/group-config/<群号>/settings.json` | 只覆盖该群明确设置的字段 |
| 全局人设/回复协议 | `group-bot/persona.txt`、`reply_protocol.txt` | 所有群默认使用 |
| 群级提示词 | `group-bot/group-config/<群号>/` 下同名文本 | 为空时继承全局 |

群级输入框的灰色 placeholder 展示全局值，不代表已写入局部配置；重置覆盖后恢复继承。正在执行的任务保留启动时的配置，后续任务使用新配置。群白名单、启动路径等参数只允许全局设置。

可配置项包括名字和别名、模型、参与频率、主动开话题概率、@ 与引用回复概率、表情包选择、风格学习、上下文预算、调用限额与图片清理等。具体范围与含义以 WebUI 参数说明为准。

“参与频率”并不等于每条消息独立有同样概率必回；消息批次、对话对象、冷却、预算和近期互动共同决定是否回复。自动接话、主动开话题和发表情包是不同阶段的配置。

## 图片、记忆与本地数据

- 新图片先读取摘要，再提供给后续分析与回复；已识别图片按内容 hash 复用，摘要和原图索引分别保留。
- 图片摘要缓存默认使用最近 2000 条消息的滑动窗口，同一 hash 再出现会刷新有效窗口并累计重复次数。
- 近期 10 条中同图达到 3 次、连续同图 2 次或 3 次会产生不同强度的复读信号；重复度是表情审核线索，入库还需判断内容和可用原图。
- 已收录表情在授权群之间共享，按 hash 去重，保存原图和预览。QQ 商城表情按包名/单个表情名称展示，普通图片直接预览。
- AI 生图放在运行目录的 `temp/ai-images/`，与长期表情库分开；不会因发送成功立即删除。清理模式可选择分钟间隔、每天固定时间、逐图到期或 Windows 任务计划程序，启动时也检查时间戳。默认间隔 120 分钟、图片保留 2 小时；保存 Windows 清理模式时会同步该项目的计划任务。需要手动同步时，在部署目录执行 `.\qq-mcp\.venv\Scripts\python.exe .\group-bot\windows_image_cleanup.py --sync`。
- 记忆和表达学习保存在本地运行数据中；学习群内表达时保留核心人设。
- 默认回复失败只记后台日志，不向群里发送兜底套话；可通过配置调整。

`max_prompt_chars` 是模型输入的字符预算；`console_max_text_chars` 是调试记录队列的总文本预算，默认 6000 字符。近期聊天存档另有条数与时间限制，磁盘存档不会全部送入模型。

备份时保留 `settings.json`、个人提示词、`group-config/`、各群 `runtime*` 和 `shared-emoji-library/`。`.gitignore` 只是防止误提交，不会删除这些文件，也不能替代备份。已被 Git 跟踪的文件不会因新增忽略规则自动取消跟踪。

重新克隆只会取回源码和默认模板，不会取回被忽略的个人配置、聊天记录、记忆数据库和表情原图。更新源码前先 `/exit` 并单独备份这些数据；不要删除旧目录后仅靠重新克隆恢复运行环境。默认人设可自动补建，历史聊天和学习数据不能凭默认模板重建。

## 排查与测试

优先按上下文 ID 找到最早失败阶段，检查结构化错误和运行目录日志：

| 现象 | 检查方向 |
| --- | --- |
| NapCat 连接失败 | QQ 登录、OneBot HTTP 地址、访问令牌、端口 |
| 模型可执行文件不存在 | `codex_exe` 可设 `auto`；确认本机 CLI 可运行 |
| 回复未发送 | 参与决策、回复失败事件、群白名单、调用限额 |
| `Required prompt content exceeds character budget` | 检查预算事件及人设、回复协议长度；必要内容不能无限压缩 |
| 图片不可用 | 区分临时生成图、识别缓存与长期表情原图；检查磁盘路径与清理记录 |
| 写文件拒绝访问 | 检查文件权限、是否重复启动、文件占用和原子写入错误详情 |

运行离线单元测试（在部署目录执行）：

```powershell
Push-Location .\group-bot
..\qq-mcp\.venv\Scripts\python.exe -m unittest discover -q
Pop-Location
```

真实生图、QQ 发送及上游 API 连通性需要分别做实际验证，离线测试通过不代表这些外部服务已连通。

## 头像与系统干预

群列表、群总览和消息头像支持显示真实 QQ 头像。启用 `avatar_remote_enabled` 后，本地调试服务向腾讯 `qlogo.cn` 提交群号/QQ号获取头像，不提交聊天内容。成功结果本地缓存 6 小时；找不到账号、网络失败或图片不可用时保留默认字母头像。关闭联网后仍可使用有效本地缓存。

聊天页工具栏提供“系统干预”，筛选栏提供“只看系统提示词”。系统干预是操作员控制数据，通过 Codex 的 [developer_instructions 指令通道](https://learn.chatgpt.com/docs/config-file/config-reference#developer_instructions) 注入，和群友发言分开；提交不创建待回复消息、不立即调用模型、不向 QQ 发送消息，也不加入群聊记忆和表达学习。

- 范围：默认当前群，可选择所有当前已授权的群；之后新增的群不会自动继承旧干预。
- 默认有效期：当前对话文本窗口。按 `console_max_text_chars`（默认 6000 字符）计算，干预后产生的聊天文字将其挤出窗口时失效；执行日志、历史回放不消耗有效期。修改窗口大小会重新判断是否到期。
- 可选有效期：下一次成功的自然回复、定时失效、直到手动清除。下一次回复模式在发送成功且使用了该干预后消耗；失败或旁观不会消耗。
- 查看与清除：系统提示词筛选显示干预内容、生效/失效/清除/使用状态；干预面板可以清除单条或当前群所有生效项。有效期与审计记录保存在各群运行目录，重启不会自动重置。
- 输入预算：生效的干预计入模型输入字符预算；已有生成任务使用创建时的指令快照，后续自然触发的任务采用新内容。

CLI 支持短命令与 Tab 补全，例如：

```text
/system add 接下来少用括号，接当前话题
/sys list
/system add --scope all --mode timed --minutes 30 先不要主动开新话题
/system add --mode once 下一次回复只说重点
/system remove 完整干预ID
/system clear
/system clear --scope all
/view system
```

新安装代码后，在旧 CLI 输入 `/exit`，再手动运行 `Start-QQ-Group-Bot-Debug.cmd` 加载后台功能。只刷新页面不能替换已加载的 Python 后台代码。

## 底部输入框与手动消息

聊天记录底部常驻输入框默认发送系统干预，可切换为“yonagi 消息（发到群）”。Enter 换行，Ctrl+Enter 提交；草稿按群和类型分别保留。系统干预可选择范围和有效期，默认当前群、当前对话窗口，不立即触发模型或 QQ 回复。

系统提示词列表和管理面板可逐条“删除”，删除后从列表隐藏并停止影响后续任务；本地保留删除审计，已经开始的任务仍使用创建时的快照。CLI 支持 `/sys delete 干预ID`；`remove` 停用单条，`clear` 停用生效项。

手动消息以当前群 Bot 的身份原样发送，不经过模型改写，最多 2000 字符。需要已加载此功能且在线的群后台；排队超过 5 分钟不再发送。界面区分排队、已发 QQ、本地记录待补写和成功；发送结果不明时不会自动重发，请先核对 QQ。

发送成功后写入最近聊天存档、当前上下文以及群数据库的 `manual_messages` 表（数据库查看页名称“yonagi手动消息”）。消息按 Bot 发言记录，不冒充群友学习样本；QQ 回显按消息 ID 去重。长期结构化记录保留 30 天，当前上下文仍受窗口和条数限制。CLI 可使用 `/say 消息内容`。

这些入口需要更新后的 WebUI 和群后台一起运行。更新后手动在旧 CLI 输入 `/exit`，再运行 `Start-QQ-Group-Bot-Debug.cmd`。

## 许可证与致谢

本项目原创代码和文档采用 [MIT License](LICENSE)，Copyright (c) **2026 BingAlpha**。本授权不覆盖第三方程序、参考源码、模型服务或用户图片。

感谢 MaiBot 的群聊交互设计参考、NapCat 的 QQ 接口和 QQ NapCat MCP 的工具接口。第三方许可证原文、版本与来源见 [licenses/README.md](licenses/README.md)：MaiBot 为 GPLv3，NapCat 使用其自定义许可证；当前核对的 QQ NapCat MCP 版本未提供独立 LICENSE，已附情况说明，不能将其视为本项目 MIT 授权。
