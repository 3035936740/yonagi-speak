# QQ Group AI Bot & Debugger

基于 NapCat / OneBot 的 QQ 群聊 AI Bot，包含 CLI、WebUI、图片理解、共享表情库、群记忆和按群覆盖的配置。

## 运行环境与安装入口

Bot 支持 Windows / Linux / macOS，Python 3.10+；安装脚本使用 uv 安装独立 Python 3.12 和项目 `.venv`。模型需要本机已认证、可运行的 Codex CLI；安装脚本不包含账号登录、模型授权或自动设置群白名单。

根目录保留两类启动入口，以及两个安装入口。四者均提供 `.ps1`、`.cmd`、`.bat`、`.sh`：

| 入口 | 用途 |
| --- | --- |
| `Start-QQ-Group-Bot` | 默认启动 Debug CLI + WebUI，替代原 `Start-QQ-Group-Bot-Debug` |
| `Start-QQ-NapCat` | 启动已配置的 NapCat；已有本机 WebUI 时避免重复启动 |
| `Install-Environment` | 安装项目 Python、第三方依赖；初始化缺失配置，保留已有配置和人设 |
| `Install-NapCat` | Windows 官方 OneKey 安装包；Linux/macOS 使用官方 Docker 镜像 |

旧顶层启动/停止脚本归档到 `ignore/launchers/`，保留原内容供查阅，不作为新入口。`ignore/`、`tools/`、`.venv/`、`launch.local.json`、运行数据和凭据均不纳入 Git。上游 QQ MCP 及 NapCat 内部自己的脚本保留原位置。

## 首次安装

Windows 双击 `Install-Environment.cmd`（或 `.bat`）；随后运行 `Install-NapCat.cmd`。PowerShell 也可执行：

```powershell
.\Install-Environment.ps1
.\Install-NapCat.ps1
.\Start-QQ-NapCat.ps1
```

Linux/macOS 在项目目录执行：

```sh
sh ./Install-Environment.sh
sh ./Install-NapCat.sh
sh ./Start-QQ-NapCat.sh
```

环境安装从 [uv 官方来源](https://docs.astral.sh/uv/getting-started/installation/)下载工具到 `tools/uv/`，下载 Python 后创建 `.venv`，安装 `requirements.txt` 并检查依赖。若已有 `qq-mcp/pyproject.toml`，同时安装其依赖；Bot 直接调用 OneBot HTTP，不要求额外启动 MCP 服务。已有 `.venv` 不会删除或重建。脚本失败时返回非零退出码，可以重试。

NapCat 安装来源遵循 [官方安装说明](https://napneko.github.io/guide/boot/Shell)：Windows x64 下载 `NapCat.Shell.Windows.OneKey.zip` 后运行官方 `NapCatInstaller.exe`（按其提示完成 QQ/NapCat 安装）；Linux/macOS 需先安装并启动 Docker Engine/Desktop 与 Compose v2，脚本生成持久化 Compose 并拉取 `mlikiowa/napcat-docker:latest`，支持 amd64/arm64。Windows 也可使用 `Install-NapCat.ps1 -Backend docker`。macOS 当前提供 Docker 入口，不自动修改本机 QQ.app。安装和启动分开，不会在安装后自动发消息。

已有 `launch.local.json` 时安装器保留它，不覆盖已有 NapCat。可复制 `launch.local.example.json` 为 `launch.local.json` 并填写已有 Windows Hook 安装路径；路径相对项目根目录，也支持绝对路径。安装器生成的路径默认相对根目录。迁移时仍需保留 QQ/NapCat 的实际目录或更新此本机配置。

首次设置：

1. 在 NapCat WebUI `http://127.0.0.1:6099/webui` 登录 QQ，开启 OneBot HTTP，配置访问令牌。Docker 模式在容器内监听 `0.0.0.0:3000`，主机仅映射 `127.0.0.1:3000`；WebUI 和 WS 端口也仅映射到本机。随机 WebUI 令牌保存在本地 `runtime/napcat-docker/compose.json`。
2. 新安装填写 `group-bot/napcat.env` 的 `NAPCAT_HOST` 和 `NAPCAT_TOKEN`。已有安装继续使用 `settings.json` 的 `mcp_env_file`，例如 `../qq-mcp/.env`，不替换既有连接凭据。
3. 修改 `group-bot/settings.json` 的 `group_id`、`chat_group_whitelist`、账号和模型，确认后开启 `enabled`。安装模板默认禁用回复；缺少的人设从 `persona-default.txt` 初始化为 yonagi，已有个人/群级人设保留。
4. Windows 运行 `Start-QQ-Group-Bot.cmd`；Linux/macOS 运行 `sh ./Start-QQ-Group-Bot.sh`。启动 CLI 并打开 WebUI，终端也会显示地址。无图形界面时使用终端地址。

启动参数：

| PowerShell | Shell | 用途 |
| --- | --- | --- |
| `-CLI` | `--cli` | 只打开终端控制台 |
| `-SkipNapCat` | `--skip-napcat` | 连接已有/远程 NapCat，不代为启动 |
| `-Model 名称 -ReasoningEffort low -FastMode off` | `--model 名称 --effort low --fast off` | 校验并保存模型设置 |
| `-NoCooldown` / `-WithCooldown` | `--cooldown off` / `--cooldown on` | 明确调整冷却标记；不传则保留现状 |
| `-DryRun` | `--dry-run` | 仅显示计划，不安装、不启动 |

启动器从项目根目录定位文件，不依赖当前工作目录。优先使用 `.venv`，兼容已有 `qq-mcp/.venv`；也可用 `QQ_BOT_PYTHON` 指定解释器。PowerShell 入口支持 Windows PowerShell 5.1 / PowerShell 7；Linux/macOS 可用 `.sh` 或已安装的 PowerShell 7。

## 数据与目录

```text
qqmcp/
├── Start-QQ-Group-Bot.{cmd,bat,ps1,sh}
├── Start-QQ-NapCat.{cmd,bat,ps1,sh}
├── Install-Environment.{cmd,bat,ps1,sh}
├── Install-NapCat.{cmd,bat,ps1,sh}
├── scripts/                       # 共享启动与安装逻辑
├── requirements.txt / .venv/
├── launch.local.json              # 本机NapCat路径（不提交）
├── group-bot/
│   ├── settings.json / persona.txt
│   ├── group-config/<群号>/        # 群级覆盖
│   ├── runtime*/                  # 记录、记忆、群友标记、原图
│   └── shared-emoji-library/       # 表情资产
├── runtime/                       # NapCat启动日志和Docker持久化数据
├── ignore/launchers/               # 旧入口归档
└── licenses/
```

开发工作区源码可位于 `work/group-bot/`；同一启动器也能找到该布局。安装、脚本整理和更新不会清空聊天、数据库、表情库或人设。重新克隆源码不能恢复本地忽略的数据，请另行备份这些目录。

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

新安装代码后，在旧 CLI 输入 `/exit`，再手动运行 `Start-QQ-Group-Bot.cmd` 加载后台功能。只刷新页面不能替换已加载的 Python 后台代码。

## 底部输入框与手动消息

聊天记录底部常驻输入框默认发送系统干预，可切换为“yonagi 消息（发到群）”。Enter 换行，Ctrl+Enter 提交；草稿按群和类型分别保留。系统干预可选择范围和有效期，默认当前群、当前对话窗口，不立即触发模型或 QQ 回复。

系统提示词列表和管理面板可逐条“删除”，删除后从列表隐藏并停止影响后续任务；本地保留删除审计，已经开始的任务仍使用创建时的快照。CLI 支持 `/sys delete 干预ID`；`remove` 停用单条，`clear` 停用生效项。

手动消息以当前群 Bot 的身份原样发送，不经过模型改写，最多 2000 字符。需要已加载此功能且在线的群后台；排队超过 5 分钟不再发送。界面区分排队、已发 QQ、本地记录待补写和成功；发送结果不明时不会自动重发，请先核对 QQ。

发送成功后写入最近聊天存档、当前上下文以及群数据库的 `manual_messages` 表（数据库查看页名称“yonagi手动消息”）。消息按 Bot 发言记录，不冒充群友学习样本；QQ 回显按消息 ID 去重。长期结构化记录保留 30 天，当前上下文仍受窗口和条数限制。CLI 可使用 `/say 消息内容`。

这些入口需要更新后的 WebUI 和群后台一起运行。更新后手动在旧 CLI 输入 `/exit`，再运行 `Start-QQ-Group-Bot.cmd`。

## 图片原图、人工摘要与表情收录

点选聊天图片消息后，右侧图片详情提供“查看原图”“修改摘要”“收录表情包”。共享表情库还提供“手动录入表情包”，可上传 PNG、JPEG、GIF、WebP（上限 2MB），填写名称、说明、情绪和适用场景；收录方式可选人工确认直接入库或交给自动审核。

- 原图和缩放识图附件分开保存：原始字节放在各群 `runtime*/image-originals/`，按内容 SHA-256 去重，使用相对路径并保留 GIF 动画。识图压缩不会替换原图。
- 查看原图优先使用本地文件；旧消息未缓存时，只从该群对应 QQ 消息的指定图片组件重新读取，校验内容哈希。原消息不可读或哈希不一致时明确报错，不用其他图片替代，也不触发模型或 QQ 回复。
- 普通群原图跟随图片识别滑动窗口回收（默认 2000 条消息，近期写入有短暂保护期）；人工入库的表情另存到 `shared-emoji-library/`，不受群图片窗口或 AI 生图 temp 定时清理影响。
- 人工摘要独立保存到 `runtime*/image-summary-edits.json`，修改前备份到 `image-edit-backups/`，覆盖自动摘要的优先级更高。后续自然回复、同图复用和当前聊天上下文使用修改结果；保存不立即回复、不增加出现次数，也不改变原有 2000 条消息有效期。摘要修改只影响当前群。
- 人工确认收录直接覆盖自动审核拒绝，原图复制到共享表情库，所有授权群可用；这与修改识别摘要是两个独立操作。人工收录不调用模型、不立即发表情。
- 更新后仍在执行的回复保留已取到的数据；后续任务使用新摘要。备份个人数据时请完整保留 `runtime*/` 与 `shared-emoji-library/`，包括原图及人工修改文件。

控制台新增 `/stop`，与 `/exit`、`/quit` 相同，保存记录后停止群后台与 WebUI。第一次加载此更新请使用旧版本已支持的 `/exit`，再手动打开 `Start-QQ-Group-Bot.cmd`。

## 人工收录时 AI 识别填表

从聊天图片收录、或在共享表情库上传图片时，人工收录弹窗均提供“AI 识别填入”。点击后使用当前群配置的识图模型（未单独配置则使用回复模型），填入表情名称、图片说明、情绪和适用场景。GIF 会取不同帧供识别。

识别只生成建议，不自动点击保存、不修改识图摘要、不收录、不发送 QQ 消息。完成后弹窗保持打开，仍需操作者检查并手动保存；等待期间手动修改的字段会保留。识别期间暂时禁用保存，换图、关闭弹窗或取消时丢弃旧结果。

需要已加载此功能且在线、启用的群后台。每群同时处理一个填入任务，每次占用已有每日模型调用额度一次；任务通过后台异步处理，CLI 和 WebUI 不等待模型完成。排队超过 3 分钟失败，单次模型超时上限 45 秒；后台重启不会重试旧任务。输入图片在完成、失败或取消处理后删除，任务结果最多保留 24 小时。

更新后手动 `/exit` 或 `/stop`，再启动 `Start-QQ-Group-Bot.cmd` 加载新 WebUI 与群后台。

## 聊天图片显示与组合回复

聊天图片可以双击气泡在原图与文字摘要之间切换，也可使用气泡下的“显示图片/文字”按钮；“放大原图”单独打开原始文件。行为设置 `chat_image_display` 可选 `image`（默认图片/表情）或 `text`（文字摘要），支持全局默认和按群覆盖。单条消息的临时切换只影响本次页面，不改聊天文本、识图记忆或模型上下文。图片按需加载、最多三个请求并行，原图失效时保留摘要与重试入口。发送后的图片也可预览；引用中的摘要仍留在引用栏。

普通回复支持多条文字后跟多张表情：`max_reply_parts` 默认 6（范围 1～10），`max_stickers_per_reply` 默认 3（范围 1～5），两项均支持群覆盖。总文字仍受 `max_reply_chars` 限制，条数是上限，不要求凑满。模型按自然停顿给出 `text_parts`，按情绪和用途选择候选 `media_ids`；正文先逐条发送，表情随后逐张发送。不会再因跟图把文字压为两条，也不在模型选好分段后再次抽签合并。`burst_reply_probability=0` 时文字合为一条，正值只影响分段倾向。

`at_reply_probability`、`quote_reply_probability` 默认仍为 0.5。每轮有效回复只抽一次，命中后真正使用 QQ 的 at/reply 组件，不再只当作“允许模型选择”的概率。模型可以选本轮真实消息的发送者作为 @ 对象，未选其他人时指向当前回复对象；不编 QQ 号、不 @ 自己。@ 和引用只附首条；纯 QQ 商城原生转发表情无法承载这些附件时，不另发一条空 @。

表情只从本轮可见、已收录且可用的候选解析，去除重复 ID/内容；使用次数在对应表情真正发送成功后更新，每张分别计数。上下文以带括号的 `[表情包：说明]` 记住已发表情，不把内部说明发进 QQ 正文。后台的 `reply_delivery` 与 `delivery_queue` 决策分别显示分段规划及实际出队条数、表情 ID、引用目标和 @ 对象，方便排查“规划了但没发”。

每条都检查停止状态和消息额度，逐条写入存档并保存未发队列。中途失败/重启仅恢复尚未成功的部分，发送结果不明时停止自动重发。参考 MaiBot 的语义断句、文字附件解析及逐段发送设计，在现有一次回复调用中完成组合规划，不额外增加断句模型调用；核心 yonagi 人设不变。参考来源：[断句](https://github.com/Mai-with-u/MaiBot/blob/main/src/chat/utils/llm_sentence_splitter.py)、[回复与附件](https://github.com/Mai-with-u/MaiBot/blob/main/src/maisaka/builtin_tool/context.py)。

更新后在旧 CLI 手动 `/exit` 或 `/stop`，再打开 `Start-QQ-Group-Bot.cmd`；只刷新页面不能加载新的 Python 发送逻辑。更新不会改写已有全局/群设置、头像授权或清理模式。

## WebUI 监听、滚动窗口和消息去重

全局配置新增：

```json
{
  "webui_host": "127.0.0.1",
  "webui_port": 0
}
```

默认仅本机监听，`webui_port=0` 自动分配端口；可设 1～65535 的固定端口，占用时明确报错。`webui_host` 接受 IPv4/IPv6 地址，例如 `0.0.0.0`（所有IPv4接口）、`::`（所有IPv6接口）、`::1`。浏览器访问IPv6时使用 `[地址]:端口`。通配监听时 CLI 的本机链接使用回环地址；其他设备使用本机实际IP和同一端口，并带 `/webui` 提供的 `#key=...` 访问密钥。Host 仅接受对应IP地址，变更操作仍需密钥及同源校验。这两项只在全局设置，保存后退出重开 Debug 才更换监听。

聊天与执行日志各自使用 `console_max_text_chars`（默认6000）和 `console_max_records` 的滚动窗口，超限逐条移除该类最旧记录，日志不再挤掉聊天。WebUI 和 CLI 还会只读合并近期聊天存档，以补回旧版本被日志挤出但仍存在存档中的消息；人工删除的记录不会恢复。模型输入预算、长期数据库和近期存档容量仍分别管理，达到显示窗口上限不会清空它们。

新消息、图片剩余有效期等元数据变化时，聊天列表复用已有消息与图片节点，只更新变化项，保留图片/GIF和手动图片/文字选择。默认显示方式为图片/表情包。下拉选项面板与明暗主题统一，保留键盘选择；表情库和数据库分页支持输入页码后点击跳转或按回车。

QQ 原生表情转发成功但没有返回消息ID时，发送确认只记执行日志，聊天以随后QQ回显的真实消息为准，避免两条气泡及空ID原图重试。已返回ID的发送会立即写入去重集合；每张表情的使用次数仍只计成功发送一次。已收录的普通图片也按表情身份匹配，不依赖QQ把 `sub_type` 标为1。没有额外文字/识图问题的已收录表情优先选择匹配表情接话，不写配图点评；没有适用表情可沉默，明确问图时仍回答问题，关闭纯表情配置时仍尊重配置。

上下文/情绪分工遵守 `role_timeout_seconds`（3～30秒，且不超过聊天模型超时）；视觉分工独立使用 `vision_timeout_seconds`（默认60秒），不再暗中把情绪/视觉截为12秒。后台降级日志包含原因、实际超时预算和脱敏错误详情，单个可选分工失败不会向群里发送故障话术。

更新完成后手动 `/stop` 或 `/exit`，再启动 `Start-QQ-Group-Bot.cmd`。代码更新不主动重启服务，不改已有群配置、人设、聊天数据库和表情资产。

## 群友标记、额外昵称和聊天输入

点击聊天中的群友头像打开资料弹窗，手动选择 **AI / bot / 真人**，设置额外昵称和后台说明。例如 `千柒, aaaa`，逗号或换行分隔，最多10个昵称，每个最多40字。昵称绑定QQ号，不替换QQ昵称或群名片；后续身份识别和对话可据此理解称呼，同名仍按账号区分。另有“群友标记”页用于搜索、查看自动证据和管理未显示在聊天中的账号。仅对当前群生效，持久保存在 `runtime*/member-labels.json`。

手动标记优先；恢复自动判断会移除人工覆盖，清除标记会同时移除判断记录，保留额外昵称；昵称输入框留空保存可单独清除昵称。自动标记只推断指令 bot，不靠文风猜AI或真人：默认至少3次独立 `/pgr` 发图或 `/a` 引用/@指令响应；同一指令多次输出只计一次，歧义场景不计。自动结果带证据，供后台纠正。`member_auto_bot_marking_enabled` 和 `member_auto_bot_min_observations` 可全局/按群设置；达到阈值仍只是推断，不主动在群里宣布。

聊天输入框使用 **Enter发送，Shift+Enter换行**，兼容Ctrl+Enter；中文输入法组合输入期间不发送。输入框placeholder和下方提示均说明快捷键。系统提示词的发送仍只写后台，不立刻触发QQ回复。

未收录的纯图片/表情在没有识图提问时也只选择合适表情或旁观，不写画面点评；无候选、配置禁用或没有适用表情时可静默。明确问图或以图片为背景另有文字问题时保留正常回答。群列表按群号复用头像和按钮节点，新消息、名称、状态更新不会反复重建头像。

Python文件锁、CLI键盘、进程组退出已增加POSIX实现，Windows仍使用原生Job/字节锁。Windows任务计划清理仅供Windows，其余平台使用间隔、每日或逐图到期清理。已添加Windows/Linux/macOS离线检查工作流；本机实际验证是Windows，Linux/macOS安装和QQ实机行为仍需对应平台验证。

## 模型阶段超时

全局及群级配置支持 `model_timeout_seconds=30`（文字聊天）、`vision_timeout_seconds=60`（识图）、`image_timeout_seconds=150`（绘图），单位秒。图文在同一次模型调用中使用30+60=90秒；识图后绘图的阶段预算合计60+150=210秒；同时还需要文字时是60+150+30=240秒。只使用实际参与的阶段，不按图片张数重复累加。

纯视觉分工、后台识图、表情审核和人工AI识别填入独立使用识图预算，不会再被聊天30秒或上下文/情绪分工上限提前截断。串联阶段分别计时，不用一个30秒超时包住整个混合任务；辅助分工判断、排队、下载、清理进程等仍有各自耗时，阶段预算合计不是严格的端到端耗时承诺。超时只记后台失败，遵循原有静默失败设置。

## 许可证与致谢

本项目原创代码和文档采用 [MIT License](LICENSE)，Copyright (c) **2026 BingAlpha**。本授权不覆盖第三方程序、参考源码、模型服务或用户图片。

感谢 MaiBot 的群聊交互设计参考、NapCat 的 QQ 接口和 QQ NapCat MCP 的工具接口。第三方许可证原文、版本与来源见 [licenses/README.md](licenses/README.md)：MaiBot 为 GPLv3，NapCat 使用其自定义许可证；当前核对的 QQ NapCat MCP 版本未提供独立 LICENSE，已附情况说明，不能将其视为本项目 MIT 授权。
