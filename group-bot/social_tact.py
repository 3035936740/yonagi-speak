"""Conversational tact: scoped feedback evidence, without canned replies or extra calls."""
import re
import time


INSTRUCTION=('\n高情商接话：先辨清对方是在倾诉、打趣、夸奖、批评，还是认真求证，并确认说的是谁。'
    '同一人相邻的几句话连起来理解，尤其是先贬后夸或先夸后贬，别只挑对自己有利的半句。'
    '对自己的批评先接住具体不满，可以简短承认没接好、自然自嘲或认真改进；不忙着自夸、争输赢、辩称至少聪明，也不借机贬低对方。'
    '真诚夸奖自然接受，轻松互损可接梗；对方认真难过时先回应感受，有具体求助再给建议，不抢着说教、诊断或比较谁更惨。'
    '保持当前人设和群聊节奏，不套客服道歉、奉承或强行正能量，不复读对方的负面标签。'
    '体贴不等于无条件附和：事实错误仍可温和纠正，合理边界仍保留；不要把旁人的评价或引用认作在说自己。可短回、用合适表情，没自然接点时也可旁观。\n')

ANALYSIS_INSTRUCTION=('\n反馈判断：同一发言者的相邻消息可能合起来表达态度，不能将前半句的批评与后半句的客套夸奖拆开。'
    '根据称呼、引用和上下文确认评价对象；对第三方的评价不自动指向bot。对bot的能力或接话表现作评价可判feedback；'
    '批评夹着“挺智能/挺聪明”不等于单纯夸奖。语气建议应回应整体意思，而不是让bot证明自己。\n')


def feedback_scene(cfg,target,context):
    if not cfg.get('high_eq_enabled',True):return {}
    self_id=str(target.get('_self_id',''))
    other_ats=set(map(str,target.get('_at_ids',[])))-{self_id,'all'}
    if other_ats and not target.get('at_bot'):return {}
    quoted=target.get('_quoted_message') or {}
    if quoted and not quoted.get('is_bot') and not target.get('at_bot'):return {}
    analysis=target.get('_role_analysis',{}).get('context',{})
    if analysis.get('feedback_target') in {'other','none'}:return {}
    current=str(target.get('text',''))
    if re.search(r'你觉得|你认为|你怎么看|评价一下|如何评价',current):return {}
    # Only the current speaker's recent adjacent turns may supply missing halves.
    stamp=target.get('_time',time.time());previous=[]
    for row in reversed(list(context)):
        if row.get('_id')==target.get('_id'):continue
        if not 0<=stamp-row.get('_time',0)<=90:break
        if row.get('is_bot'):continue
        if str(row.get('_uid',''))!=str(target.get('_uid','')):break
        if row.get('_at_ids') and not row.get('at_bot'):break
        previous.insert(0,str(row.get('text','')))
        if len(previous)>=2:break
    text='\n'.join(previous+[current])
    text=re.sub(r'情商(?:并不|也不|不|没那么|不是很)(?:太)?(?:低|差)','情商尚可',text)
    criticism=bool(re.search(r'情商.{0,6}(?:非常低|很低|太低|低得|低|差|不高|没有)|'
        r'(?:不会|不懂|不太会|不太懂).{0,3}(?:聊天|接话|看气氛)|没(?:有)?接住|答非所问|听不懂人话|太生硬',text))
    if not criticism:return {}
    names=[cfg.get('bot_name','yonagi')]+cfg.get('bot_aliases',[])
    named=any(name and re.search(re.escape(name),text,re.I) for name in names)
    direct=bool(target.get('at_bot') or quoted.get('is_bot') or analysis.get('addressed_to_bot') or analysis.get('feedback_target')=='self' or named
        or re.search(r'你.{0,5}(?:情商|不会聊天|不会接话|没接住|答非所问)',current))
    if not direct:return {}
    return {'about_self':True,'criticism':True,
        'mixed_praise':bool(re.search(r'(?:挺|很|蛮|还挺|有点|比较).{0,2}(?:智能|聪明|厉害)|但是.{0,6}(?:智能|聪明)',text)),
        'continuation':bool(previous)}


def quality_issue(text,target):
    if not target.get('_high_eq_enabled',True):return ''
    scene=target.get('_social_feedback',{})
    if not scene.get('about_self') or not scene.get('criticism'):return ''
    if re.search(r'(?:智能|聪明|智商|实力|本事|能力).{0,5}(?:还是有的|还是在线|还是可以|可不低|可不差|没问题)|'
        r'我(?:至少|起码|可|还是|可是|确实).{0,6}(?:聪明|智能|厉害|不笨|很强)|'
        r'(?:总比|至少比|起码比)你.{0,5}(?:强|聪明|好)',str(text)):
        return 'defensive_self_praise'
    return ''
