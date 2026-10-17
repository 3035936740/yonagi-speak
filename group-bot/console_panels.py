"""Keyboard-driven status/config views for the existing terminal supervisor."""
import json
from console_config import Config,PROTECTED,runtime,read_json
from config_metadata import metadata
from member_labels import MemberLabels,store


def status(ui):
    cfg=Config(ui.root,ui.group).load();folder=runtime(ui.root,ui.group,cfg)
    state=read_json(folder/'state.json');rows=ui.rows.get(ui.group,[])
    profiles=MemberLabels(folder).facts();cache=read_json(folder/'image-memory.json')
    return '\n'.join(['运行状态  /status',
        '群聊       '+ui.names.get(ui.group,str(ui.group))+' ('+str(ui.group)+')',
        'QQ连接     '+('在线' if state.get('online') else '等待连接 / 已停止')+'   PID '+str(state.get('pid','—')),
        '最近轮询   '+str(state.get('last_poll','—')),
        '模型       '+cfg['model']+' · '+cfg['reasoning_effort']+' · fast='+str(cfg['fast_mode']),
        '超时       聊天 '+str(cfg['model_timeout_seconds'])+'s / 识图 '+str(cfg['vision_timeout_seconds'])+'s / 绘图 '+str(cfg['image_timeout_seconds'])+'s',
        '今日调用   '+str(state.get('model_calls_today',0))+' / '+str(cfg['max_model_calls_per_day']),
        '对话窗口   '+str(cfg['console_max_text_chars'])+'字符；保留 '+str(len(rows))+' 条记录',
        '近期对话   '+str(cfg['recent_chat_journal_max_messages'])+'条 / '+str(cfg['recent_chat_journal_hours'])+'小时',
        '图片摘要   '+str(len(cache.get('entries',{})))+'；已观察消息 '+str(cache.get('sequence',0)),
        '群友资料   '+str(len(profiles))+' 位；屏蔽 '+str(sum(row['blocked'] for row in profiles.values()))+' 位',
        '后台监听   '+cfg['webui_host']+':'+str(cfg['webui_port'] or '随机端口')+'；key '+('自定义（已隐藏）' if cfg.get('webui_key') else '随机'),
        '快捷入口   /usage refresh 查询5小时/1周额度与token   /config 设置面板   /member 群友标记   /webui 网页后台'])


def config_rows(ui):
    panel=ui.panel;scoped=Config(ui.root,ui.group if panel['scope']=='local' else None)
    cfg=scoped.load();meta=metadata(cfg);query=panel.get('query','').casefold()
    return scoped,[(key,meta[key],value) for key,value in cfg.items() if key not in PROTECTED
        and query in (key+' '+meta[key]['label']+' '+meta[key]['help']).casefold()]


def render(ui,width,height):
    from debug_console import wrap
    panel=ui.panel
    if panel['kind']=='help':
        lines=[]
        for line in ui.notice.splitlines():lines+=wrap(line,width-1)
        room=max(1,height-3);offset=max(0,min(panel.get('scroll',0),len(lines)-room))
        panel.update(scroll=offset,page_size=room,line_count=len(lines))
        header='命令帮助  '+str(offset+1)+'–'+str(min(len(lines),offset+room))+' / '+str(len(lines))+' 行'
        return '\n'.join([header,'─'*(width-1)]+lines[offset:offset+room]+['↑↓ / PgUp/PgDn 翻页 · Esc 返回聊天 · / 输入命令'])
    if panel['kind']=='status':
        lines=[]
        for line in status(ui).splitlines():lines+=wrap(line,width-1)
        return '\n'.join((lines[:height-2]+['','Esc 返回聊天 · / 输入命令'])[:height])
    scoped,rows=config_rows(ui);count=len(rows);index=min(panel['index'],max(0,count-1));panel['index']=index
    title='配置  /config  · '+('当前群 '+str(ui.group) if panel['scope']=='local' else '全局默认')
    lines=[title,'搜索：'+panel.get('query','')+'   '+str(count)+' 项','─'*(width-1)]
    if panel.get('edit'):
        key,meta,value=rows[index]
        draft=panel['draft']
        lines+=wrap(meta['label']+'  '+key,width-1)+wrap(meta['help'],width-1)
        lines+=['输入新值（数组/对象用JSON；on/off开关）','> '+('•'*len(draft) if key=='webui_key' else draft),panel.get('message',''),'Enter 保存 · Ctrl+U 清空 · Esc 取消']
    else:
        room=max(1,height-9);start=max(0,index-room//2);start=min(start,max(0,count-room))
        overrides=scoped.overrides() if scoped.group_id is not None else {}
        for n,(key,meta,value) in enumerate(rows[start:start+room],start):
            shown=('已设置' if value else '随机生成') if key=='webui_key' else json.dumps(value,ensure_ascii=False)
            source=' [继承]' if panel['scope']=='local' and key not in overrides else ''
            line=('› ' if n==index else '  ')+meta['label']+source+' = '+shown
            wrapped=wrap(line,width-1);lines.append(wrapped[0]+('…' if len(wrapped)>1 else ''))
        if rows:
            key,meta,_=rows[index];lines+=['─'*(width-1),key]+wrap(meta['help'],width-1)[:2]
        lines+=[panel.get('message',''),'↑↓ 选择 · Enter 编辑 · 空格切换开关 · Tab 作用域 · 输入搜索 · Esc 返回']
    return '\n'.join(lines[:height])


def key(ui,char,code):
    panel=ui.panel
    if panel['kind']=='help':
        if char=='\x1b':ui.panel=None;ui.notice='已返回聊天';ui.scroll=0
        elif char=='/':ui.panel=None;ui.buffer='/'
        else:
            steps={'H':-1,'P':1,'I':-panel.get('page_size',20),'Q':panel.get('page_size',20)}
            if code in steps:panel['scroll']=max(0,min(max(0,panel.get('line_count',1)-panel.get('page_size',20)),panel.get('scroll',0)+steps[code]))
        return
    if panel['kind']=='status':
        if char=='\x1b':ui.panel=None;ui.notice='已返回聊天';ui.scroll=0
        elif char=='/':ui.panel=None;ui.buffer='/'
        return
    if panel.get('edit'):
        if char=='\x1b':panel['edit']=False
        elif char=='\x15':panel.update(draft='',selected=False)
        elif char=='\b':panel.update(draft='' if panel.get('selected') else panel['draft'][:-1],selected=False)
        elif char=='\r':
            try:
                ui.configure(panel['scope']+' set '+panel['edit_key']+' '+panel['draft'])
                panel.update(edit=False,message='已保存，下一轮使用新设置')
            except (ValueError,KeyError,OSError) as exc:panel['message']='错误：'+str(exc)
        elif char.isprintable():panel.update(draft=('' if panel.get('selected') else panel['draft'])+char,selected=False)
        return
    if char=='\x1b':ui.panel=None;ui.notice='已返回聊天';ui.scroll=0;return
    if char=='/':ui.panel=None;ui.buffer='/';return
    if char=='\t':panel.update(scope='local' if panel['scope']=='global' else 'global',index=0);return
    _,rows=config_rows(ui)
    if code in {'H','P'}:panel['index']=max(0,min(len(rows)-1,panel['index']+(-1 if code=='H' else 1)))
    elif char=='\r' and rows:
        field,_,value=rows[min(panel['index'],len(rows)-1)]
        draft=('on' if value else 'off') if isinstance(value,bool) else value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        panel.update(edit=True,edit_key=field,draft=draft,message='',selected=True)
    elif char==' ' and rows and isinstance(rows[min(panel['index'],len(rows)-1)][2],bool):
        field,_,value=rows[min(panel['index'],len(rows)-1)]
        try:
            ui.configure(panel['scope']+' set '+field+' '+('off' if value else 'on'));panel['message']='开关已保存'
        except (ValueError,KeyError,OSError) as exc:panel['message']='错误：'+str(exc)
    elif char=='\b':panel.update(query=panel.get('query','')[:-1],index=0)
    elif char.isprintable():panel.update(query=panel.get('query','')+char,index=0)


def member_command(ui,text):
    words=text.split(maxsplit=2);action=words[0] if words else 'list'
    labels=store(ui.root,ui.group)
    if action=='list':
        ui.notice='群友标记（本群）\n'+'\n'.join(uid+'  '+('屏蔽 · ' if row['blocked'] else '')+{'owner':'bot主','human':'真人'}.get(row['label'],row['label'] or '未标记')+'  '+', '.join(row['aliases']) for uid,row in labels.facts().items())+'\n/member block|unblock QQ号  /member type QQ号 AI|bot|human|owner|auto  /member show QQ号'
        return
    if len(words)<2:raise ValueError('/member block|unblock|show QQ号 或 /member type QQ号 类型')
    uid=words[1];row=labels.facts().get(uid,labels.effective({}))
    if action=='show':ui.notice=json.dumps({'qq_id':uid,**row},ensure_ascii=False,indent=2);return
    if action not in {'block','unblock','type'}:raise ValueError('群友操作：list / show / block / unblock / type')
    label=words[2] if action=='type' and len(words)==3 else row['manual_label'] or 'auto'
    result=labels.mark(uid,label,row['note'],None,blocked=action=='block' if action in {'block','unblock'} else None)
    ui.notice='群友资料已保存：'+uid+' · '+('屏蔽' if result['blocked'] else '允许读取')+' · '+result['label']
