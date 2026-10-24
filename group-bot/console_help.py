"""Shared command inventory and complete help for CLI and WebUI command output."""
HELP = {
    'knowledge':('/knowledge [查询内容]','查看当前群及共享知识；带查询内容时预览本地检索片段，不调用模型、不发群消息。新增、导入TXT/Markdown、编辑、停用和删除请打开WebUI的“知识库”。/config knowledge_enabled off可关闭回复检索。'),
    'reconnect':('/reconnect napcat|mcp','napcat：保存并重启当前群连接，恢复暂停群，其他群不受影响；mcp：建立新stdio MCP会话并读取当前群信息，不重启QQ或其他应用。其他应用已有MCP会话需在其宿主重连。'),
    'usage':('/usage [refresh] [all]','查看5小时/1周账号额度与本项目token统计；refresh只读查询当前Codex登录的额度（60秒缓存），all统计全部群，默认当前群。账号额度不是固定token总量；旧调用无usage记录不能补算。'),
    'status':('/status','运行总览：QQ连接、模型、超时、调用额度、12800字默认窗口、屏蔽数量和后台状态。密钥隐藏；Esc返回聊天。'),
    'config':('/config [local|global] [show|get|set|edit|inherit|字段=值]',
        '不带参数打开可搜索面板：↑↓选择，Enter编辑/保存，空格切换开关，Tab切换全局/当前群，Ctrl+U清空编辑，Esc取消/返回。\n'
        '交互面板仅CLI；WebUI请用“行为设置”，命令行仍可使用show/get/set等文本命令。\n'
        '/config local：当前群面板；/config global：全局默认。\n'
        '/config show [字段关键词]：查看配置；/config get 字段：查看单项。\n'
        '/config set 字段 值 或 /config 字段=值：直接保存；数组/对象用JSON，开关用on/off。\n'
        '/config local set 字段 值：只改当前群；/config local inherit 字段：恢复继承全局。\n'
        '/config key=xxxx：设置全局WebUI密钥；/config set webui_key：留空恢复随机密钥。密钥、监听地址和端口需手动/stop后重新启动生效。\n'
        '/config prompt 文本 或 /config persona 文本：修改人设；/config reply_protocol 文本：修改回复规则。\n'
        '/config edit [settings|persona|reply_protocol]：外部编辑器（仅CLI）；网页请用设置/人设页。\n'
        '/config cooldown on|off：回复冷却开关；local只影响当前群，global影响授权群。\n'
        '常用字段：max_prompt_chars / console_max_text_chars（默认12800）；model_timeout_seconds / vision_timeout_seconds / image_timeout_seconds（默认30/60/150）。\n'
        '群友策略：member_prompt_owner / member_prompt_human / member_prompt_AI / member_prompt_bot；自动屏蔽member_model_block_enabled默认off，规则member_model_block_rules。\n'
        '示例：/config local set passive_probability 0.35；/config local inherit passive_probability。'),
    'groups':('/groups','列出授权群的名称和群号。'),
    'group':('/group 群名或群号','切换当前群；支持唯一匹配，重名或多个匹配时继续输入。←→切群，Ctrl+F搜索群。'),
    'whitelist':('/whitelist show|add|remove [群号]','查看、添加或移除授权群。添加后同步对应后台；移除会停止该群，至少保留一个群。'),
    'search':('/search [文本]','筛选当前群的聊天/执行记录；留空取消筛选。'),
    'view':('/view all|chat|flow|system','显示全部、聊天、执行流程或系统提示词记录；返回记录视图。'),
    'delete':('/delete 消息ID','删除当前群本地记录及可追踪上下文，不撤回QQ消息；处理时保存并重启当前群后台。'),
    'help':('/help [命令]','完整命令帮助，↑↓逐行、PgUp/PgDn翻页、Esc返回。可用/help config、/help member、/help system查看详情。'),
    'restart':('/restart','保存并重启当前群后台。'),
    'webui':('/webui','从CLI打开授权网页后台；终端继续运行。已有窗口可重新打开，链接包含当前访问密钥，请妥善保存。'),
    'quit':('/quit','保存并退出控制台，停止授权群后台与WebUI。与/exit、/stop相同。'),
    'exit':('/exit','保存并退出控制台，停止授权群后台与WebUI。与/quit、/stop相同。'),
    'stop':('/stop','保存并退出控制台，停止授权群后台与WebUI。更新代码后手动执行，再启动Start-QQ-Group-Bot加载更新。'),
    'system':('/system add|list|remove|delete|clear [选项] [内容或ID]',
        '/system list：查看系统干预；/system add 内容：加入当前群当前对话窗口，不立即触发回复。\n'
        '/system remove ID 或 /system delete ID：删除指定干预；/system clear：清除干预。\n'
        '选项放在内容前：--scope group|all，--mode context|once|timed|persistent，--minutes 分钟。\n'
        'context：默认，随对话窗口滚动到期（默认12800字）；once：下一次自然回复；timed：指定分钟；persistent：直到手动清除。\n'
        '示例：/system add --scope group --mode timed --minutes 30 这半小时回复简短一些。\n'
        '这是System→AI干预，不是User→AI消息，不会直接发送到QQ群。'),
    'say':('/say 消息内容','以bot身份发送文字到当前QQ群；成功后加入上下文与手动消息记忆。WebUI的“消息（发到群）”还支持上传、粘贴图片并与文字同条发送。不会将内容当作系统提示词。'),
    'member':('/member list|show|type|block|unblock [QQ号] [类型]',
        '群友类型 AI/bot/真人/bot主、昵称说明、屏蔽与证据；仅当前群生效。\n'
        '/member list：列出当前群已有资料的账号；/member show QQ号：查看类型、昵称、说明、屏蔽和证据。\n'
        '/member type QQ号 owner|human|AI|bot|auto|clear：指定bot主、真人、AI、bot，恢复自动判断或清除类型与判断记录。\n'
        '/member block QQ号：加入当前群屏蔽名单；/member unblock QQ号：人工解除，模型不覆盖这次解除决定。\n'
        '屏蔽后WebUI/CLI仍保留消息并标注“屏蔽”，但不交给模型处理或触发回复；不踢人、不禁言。清除类型不解除屏蔽。\n'
        'bot主仅能人工指定：明确任务优先，普通聊天仍正常接话；不绕过工具授权和上层规则。\n'
        '额外昵称、账号说明及屏蔽也可在WebUI点击群友头像编辑；各类型提示词在/config配置。\n'
        '自动屏蔽默认关闭，使用member_model_block_enabled、member_model_block_rules、member_model_block_min_messages配置。'),
}
COMMANDS=list(HELP)


def help_text(topic=''):
    from console_config import unique
    topic=topic.strip().lstrip('/')
    if topic and topic!='all':
        name=unique(topic,COMMANDS);usage,detail=HELP[name]
        return '命令帮助 · /'+name+'\n\n'+usage+'\n'+detail+'\n\n/help 返回完整帮助；Esc返回聊天。'
    groups=[('面板与帮助',['help','status','usage','knowledge','config','webui']),
        ('群友资料与消息',['member','say','system']),('群与记录',['groups','group','whitelist','search','view','delete']),
        ('后台与退出',['reconnect','restart','stop','exit','quit'])]
    lines=['命令帮助  /help','CLI与WebUI共用此命令清单；只操作当前群的命令以当前选中群为准。',
        '详细用法：/help config  /help member  /help system','']
    for title,names in groups:
        lines.append('【'+title+'】')
        for name in names:
            usage,detail=HELP[name]
            lines.extend([usage,'  '+detail.split('\n')[0]])
        lines.append('')
    lines.extend(['【快捷键】','↑↓ / PgUp/PgDn：帮助翻页；Esc：返回聊天；/：输入新命令。',
        '聊天视图：←→切群，Ctrl+F搜群，Tab补全命令，PgUp/PgDn翻记录，Ctrl+C保存并退出。',
        '配置面板：↑↓选择，Enter编辑/保存，空格切换开关，Tab切换作用域，Ctrl+U清空编辑。'])
    return '\n'.join(lines)
