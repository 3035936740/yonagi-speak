"""Ground conversational style in visible turns, not fixed catchphrases."""
import re
import time


def normalize(text):
    return re.sub(r'[\W_]+','',re.sub(r'@\d+|\[[^\]]*\]','',str(text))).lower()


def visual_facts(text):
    return re.findall(r'\[图片摘要[:：]([^\]]+)\]',str(text))


def spoken_text(text):
    """A recognition annotation is not a sentence spoken by the sender."""
    return re.sub(r'\[图片摘要[:：][^\]]+\]','[图片]',str(text))


VISUAL_INSTRUCTION=('\n图片接话：先分清群友在问图中信息，还是发图表达情绪、分享或接梗。'
    'visual_context和image_facts是内部视觉材料，不是群友说的话，也不是要你复述的任务。'
    'answer按实际提问说明细节；react回应发图的语气、当前话题或互动，可发合适表情，也可旁观；'
    'context只把图作背景，回答群友真正说的话。没人问看见什么时，不把物体、动作、表情串成一句配图解说，'
    '不替图中角色猜一段小故事，不为了展示识别成功而逐图点评；短反应可以提到图里有趣之处，别套固定台词。'
    '群友发图不代表对你提问，找不到自然接话点可以不回。表情候选拼图仅供选表情，不属于群友发来的图。\n')


def image_description_requested(target):
    mode=target.get('_image_response_mode')
    if mode=='answer':return True
    # Captions injected by recognition are not a user's request to read an image.
    text=re.sub(r'@\d+|\[(?:图片|商城表情)[^\]]*\]','',str(target.get('text',''))).strip()
    if mode in {'react','context','none'}:return False
    return bool(text and re.search(r'这(?:张|个|是)?(?:什么|啥)|图.{0,10}(?:什么|啥|谁|文字|意思)|'
        r'(?:描述|识别|分析|解释|读|翻译|看看|看下|看一眼|认认)|写(?:着|了|的).{0,6}(?:什么|啥)|什么(?:角色|人物)',text))


def visual_summary_leakage(text,target):
    if image_description_requested(target):return False
    facts=target.get('_cached_vision',[]) or ([target['_role_analysis']['vision']]
        if target.get('_role_analysis',{}).get('vision') else [])
    if not facts:facts=[{'description':fact} for fact in visual_facts(target.get('text',''))]
    if not facts and not (target.get('_images') or target.get('_image_hashes') or target.get('_passive_visual')):return False
    value=str(text)
    if re.search(r'\[(?:图片摘要|识别结果)|(?:图片|图像)(?:识别|摘要|描述)[:：]',value):return True
    key=normalize(value)
    if len(key)<8:return False
    # A short actual reaction can mention a visual feature; don't equate that
    # with reciting an inventory of hair, eye color, positions and OCR.
    from style_memory import grams
    pieces=grams(value)
    for fact in facts:
        observed=str(fact.get('description',''))+' '+str(fact.get('visible_text',''))
        if key in normalize(observed) or pieces and len(pieces & grams(observed))/len(pieces)>=.6:return True
    # Catch paraphrased captions too. Keep actual interpersonal reactions such
    # as asking for a carrot; do not ban every mention of a visible object.
    interpersonal=bool(re.search(r'分我|给我|留(?:点|一|个|根)|让我|带我|我也|我的|你的|你也|你这|咱',value))
    narrator=bool(re.match(r'^(?:这|一[只个位]|图[里中]|看起来)',value.strip()))
    pose=bool(re.search(r'抱着|拿着|戴着|穿着|坐着|站着|躺着|手里|怀里|手捧',value))
    narration=bool(re.search(r'一脸|表情|神情|像是|仿佛|背景|配字|上面写|右上角|左上角',value))
    if len(key)>=12 and narrator and pose and narration and not interpersonal:return True
    return bool(re.search(r'右上角|左上角|右下角|左下角|背景|图中|图里|上面写',value)
        and re.search(r'写|字|头发|白发|眼|角色|猫耳|人物|颜色',value))


def scene(target,history):
    now=target.get('_time',time.time());text=target.get('text','');key=normalize(text)
    recent=[item for item in list(history)[-30:] if item.get('_id')!=target.get('_id')
        and 0<=now-item.get('_time',0)<=180]
    equal=[item for item in recent if key and normalize(item.get('text',''))==key]
    own=[item for item in recent if item.get('is_bot')][-4:]
    suffixes=[bool(re.search(r'[（()）]\s*$',item.get('text',''))) for item in own]
    streak=0
    for suffix in reversed(suffixes):
        if not suffix:break
        streak+=1
    return {'repeated_from_bot':any(item.get('is_bot') for item in equal),
        'repeated_by_people':len({str(item.get('_uid')) for item in equal if not item.get('is_bot')}),
        'repeat_source_ids':[str(item.get('_id','')) for item in equal][-4:],
        'recent_bot_bracket_streak':streak,
        'current_health_mention':bool(re.search(r'感冒|头晕|发晕|发烧|发冷|胸痛|喘不过|晕倒|体温|难受',text)),
        'punctuation_topic':bool(re.search(r'括号|函数|代码|正则',text))}


INSTRUCTION=('\n群聊表达：接当前这句话的动作和情绪，不替群友解释梗、不分析大家在干什么；'
    '认亲、轻逗、夸你可以顺着接，不自动问为什么、怎么啦或解释自己刚才为什么说那句话。'
    '语境不需要时不把上一话题硬塞进来，图片摘要只是理解材料，不要求点评配字。'
    '表达习惯选0～少量贴合的接法，没合适的不选；群里有人复读你时通常是在接龙或逗你，不写自辩。'
    '标点是观察不是配额，连续用了句尾括号时换自然节奏；不改技术内容里的括号，也不靠固定认证、盖章口头禅塑造人格。'
    '身体不适认真关心，日常用简短贴近当事人的话；存在明显危险信号时保留必要提醒，不为玩梗弱化安全建议。\n')

PERSONA_BOUNDARY=('\n人设里的性别只供内部把握；不强调性别包括被问时也自然绕开话题。'
    '别解释你有性别设定、遵守什么规则或为什么不回答，别念“不主动强调性别”“你直接叫我就好”。'
    '可轻轻接梗、反问一句俏皮话或换个自然话头，由当前语境决定，不用固定台词。'
    '猫娘等称呼是群友给的玩笑标签，可接一下，不当成性别核验。'
    '群聊保持yonagi角色，被追问身份也自然接梗或绕开，不用“我是AI/模型/机器人”或“没有生理性别”解释自己。'
    '不要转而声称是真人、人类或编现实身体经历；这是角色聊天，不是冒充真人。讨论AI圈、模型和技术仍可正常提到这些词。\n')


def gender_question(target):
    text=re.sub(r'@\d+','',str(target.get('text','')))
    names=['你',str(target.get('_bot_name','yonagi'))]+list(target.get('_self_names',[]))
    forms=r'性别|男生|女生|男的|女的|男孩子|女孩子|公的|母的'
    return bool(re.search('(?:'+'|'.join(re.escape(name) for name in names if name)+r').{0,10}(?:'+forms+')',text)
        or re.fullmatch(r'\s*(?:'+forms+r')(?:吗|呢|？|\?)?\s*',text))


def quality_issue(text,target):
    from social_tact import quality_issue as tact_issue
    issue=tact_issue(text,target)
    if issue:return issue
    if visual_summary_leakage(text,target):return 'visual_summary_leakage'
    value=str(text)
    if re.search(r'(?:我(?:就是|只是|其实是|本身是|算是(?:个|一个)?|是(?:个|一个)?)|作为|as\s+an?)\s*(?:AI\b|人工智能|(?:语言|大语言)?模型|机器人|chatgpt|gpt[-\s]?\d)|'
            r'我.{0,8}(?:没有|不具备|不存在).{0,14}(?:生理性别|真人性别|身体)|没有男女生这种生理性别',value,re.I):
        return 'out_of_character_identity'
    if re.search(r'我(?:是|就是|本身是)(?:真人|人类)|我是活人|(?:不是AI|不是机器人).{0,8}(?:真人|人类)|我有(?:真实|现实).{0,4}身体',value,re.I):
        return 'unsupported_real_identity'
    if re.search(r'(?:不(?:主动)?(?:透露|强调|讨论|回答).{0,4}性别)|(?:设定上|内部设定|人设里).{0,10}(?:性别|女生|男生)|'
            r'(?:我的(?:系统|规则|提示词)|(?:内部|系统)提示词|人设).{0,8}(?:要求|规定|不允许|不能说)',value):
        return 'persona_rule_disclosure'
    if gender_question(target) and re.search(r'你(?:直接)?叫我.{0,15}(?:就好|就行)|性别.{0,12}(?:不重要|不用纠结)',value):
        return 'gender_policy_explanation'
    if gender_question(target) and re.search(r'^(?:我(?:当然)?(?:是|算是)|当然是|是(?:的|啊)?[，, ]*)?(?:女生|女孩子|女的|男生|男孩子|男的)',value.strip()):
        return 'gender_topic_answer'
    if not target.get('_peer_reply_enabled',True):return ''
    scene_info=target.get('_peer_scene',{})
    act=target.get('_social_act','')
    casual=act in {'role_play','play','banter','greeting','acknowledgement'} or scene_info.get('repeated_from_bot')
    if casual and re.search(r'我(?:刚才|刚刚|只是).{0,20}(?:接你|接了|顺嘴|顺口|说了|回应|那句|那条)|'
        r'你(?:刚才|刚刚).{0,12}(?:也|先).{0,8}(?:喊|叫|说)',value):
        return 'defensive_self_explanation'
    if casual and re.search(r'怎么突然|为什么.{0,8}(?:叫|喊|敲|摸)|敲我干嘛|我(?:该|要|能)怎么(?:回|接)',value):
        return 'role_play_interrogation'
    if not scene_info.get('current_health_mention') and casual and re.search(r'多喝水|喝点水|记得.{0,5}休息|注意休息',value):
        return 'unrequested_topic_advice'
    if casual and re.search(r'来围观.{0,10}了|这张(?:也|就)是.{0,10}(?:people|可爱|宝宝)',value):
        return 'caption_instead_of_interaction'
    return ''
