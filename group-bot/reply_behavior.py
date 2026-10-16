"""Choose delivery styles without turning every incoming message into a reply."""
import random
import re
from peer_reply import spoken_text


def reaction_only(target):
    """Media annotations/OCR are not a request for a caption or a visual answer."""
    words=re.sub(r'@\d+|\[[^\]]*\]','',spoken_text(target.get('text',''))).strip()
    if words:return False
    if target.get('_image_response_mode') in {'answer','context','none'}:return False
    if target.get('_sticker_ids'):return True
    # A quoted picture alone does not turn a new text turn into a sticker turn.
    own_image=bool(target.get('_images') or target.get('_image_hashes')
        or re.search(r'\[(?:图片|商城表情|表情包)[^\]]*\]',str(target.get('text',''))))
    return own_image and target.get('_image_response_mode') not in {'answer','context','none'}


def restrict_visual_reaction(cfg,target):
    """Apply after context classification, and again at the execution boundary."""
    if not reaction_only(target):return
    style=target.get('_reply_style')
    if style is None:
        style=preferences(cfg,any(item.get('kind') in {'sticker','market_face'}
            for item in target.get('_media_choices',[])),target)
    target['_reply_style']={**style,'image_reaction_only':True,
        'prefer_reaction':bool(style.get('allow_no_words_reaction'))}


def delivery_preferences(cfg):
    """One draw per reply; successful draws become actual grounded attachments."""
    return {'allow_at':random.random()<cfg.get('at_reply_probability',.5),
        'allow_quote':random.random()<cfg.get('quote_reply_probability',.5),
        'auto_target':True}


def preferences(cfg, has_stickers, target=None):
    from interaction import social_act
    target = target or {}
    text = spoken_text(target.get('text', ''))
    act = social_act(text)
    factual = bool(target.get('_feedback_on_answer')) or (act in {'request', 'question'} and bool(re.search(r'怎么|为什么|如何|报错|安装|代码|多少|参数|ffmpeg|nvenc|配置|API|模型|总结|概括|解释|网页|链接|看一下|看下|看一眼', text, re.I)))
    technical = act in {'request','question'} and bool(re.search(
        r'代码|报错|安装|参数|ffmpeg|nvenc|配置|API|模型|原版|内部|伤害|物理|法术|机制|怎么|为什么|如何',
        text,re.I))
    # Light ribbing is a relationship behavior, not the default voice for a
    # stranger.  A prior exchange is the smallest trustworthy familiarity cue.
    familiar = bool(target.get('_interaction_memory',{}).get('recent_interaction_count'))
    friendly_tease = bool(technical and familiar and random.random()
        < cfg.get('friendly_tease_probability',.18))
    incoming_sticker = bool(target.get('_sticker_ids'))
    words=re.sub(r'@\d+|\[[^\]]*\]','',text).strip()
    incoming_sticker_only=incoming_sticker and not words
    recent_media = int(target.get('_recent_bot_media_count', 0) or 0)
    last_bot_media = bool(target.get('_last_bot_sent_media'))
    playful = act in {'role_play', 'play', 'banter', 'greeting', 'acknowledgement'}

    # Separate the action's availability from a soft preference. The planner
    # decides whether/which emotion fits; execution never takes another lottery.
    # Recent media lowers preference instead of banning an entire conversation.
    suppressed = not has_stickers or factual
    pressure=min(1,max(recent_media/4,.5 if last_bot_media else 0))
    factor=max(.1,1-cfg.get('sticker_recent_suppression_strength',.5)*pressure)
    def rate(key,default):
        value=cfg.get(key,default)
        return min(1,value*(1.25 if incoming_sticker else 1)*factor) if value>0 else 0
    reaction_rate=rate('sticker_reply_probability',.55)
    followup_rate=rate('followup_sticker_probability',.5)
    no_words_rate=rate('no_words_sticker_probability',.4)
    allow_reaction = not suppressed and reaction_rate>0 and no_words_rate>0
    allow_followup = not suppressed and followup_rate>0
    reaction_first = bool(allow_reaction and (incoming_sticker_only or random.random()<reaction_rate))
    followup = bool(allow_followup and not reaction_first and random.random()<followup_rate)
    policy = ('text_only' if not allow_reaction and not allow_followup else
        'reaction_or_followup' if allow_reaction and allow_followup else
        'reaction_optional' if allow_reaction else 'followup_optional')
    return {
        'prefer_reaction': reaction_first,
        'incoming_sticker_only':incoming_sticker_only,
        'allow_no_words_reaction': allow_reaction,
        'prefer_no_words_reaction':bool(allow_reaction and random.random()<no_words_rate),
        'allow_followup_sticker': allow_followup,
        'prefer_followup_sticker': followup,
        'allow_burst': cfg.get('burst_reply_probability', 0.65)>0,
        'prefer_burst': random.random() < cfg.get('burst_reply_probability', 0.65),
        'max_text_parts':cfg.get('max_reply_parts',6),
        'max_stickers':cfg.get('max_stickers_per_reply',3),
        'social_act': act,
        'peer_voice': 'light_tease' if friendly_tease else 'casual_peer' if technical else 'natural',
        'allow_light_teasing': friendly_tease,
        'opening_reaction': 'optional',
        'must_return_to_current_question': bool(act in {'request','question'}),
        'delivery_mode': 'reaction_first' if reaction_first else 'words_then_sticker' if followup else 'words',
        'sticker_policy': policy,
        'sticker_preferences':{'reaction':round(reaction_rate,3),'no_words':round(no_words_rate,3),
            'followup':round(followup_rate,3),'recent_media_pressure':round(pressure,3)},
        'sticker_context': ('incoming_sticker' if incoming_sticker else 'playful_chat' if playful
            else 'casual_statement' if allow_followup or allow_reaction
            else 'factual_or_no_candidate'),
    }


def allowed_media(media, text, style):
    """Enforce the planner's contextual sticker lane after model selection."""
    if not media:
        return []
    if text:
        return media if style.get('allow_followup_sticker') else []
    return media if style.get('allow_no_words_reaction') else []


def reply_chunks(result, text, limit, allow_burst, has_media=False, maximum=6):
    """Honor semantic chunks; preserve URLs, emoticons and short one-liners."""
    supplied = result.get('text_parts', [])
    maximum=max(1,min(10,maximum))
    if isinstance(supplied, list) and 1 <= len(supplied) <= 10 and all(isinstance(p, str) for p in supplied):
        parts = [p.strip().rstrip('。').rstrip() for p in supplied if p.strip()]
        # Reject mismatched alternate text: validate the exact content being sent.
        if not parts or sum(map(len, parts)) > limit or any('[CQ:' in p or '```' in p for p in parts):
            parts = [text] if text else []
    else:
        parts = [text] if text else []
    single=parts[0] if len(parts)==1 else ''
    threshold=24
    if allow_burst and len(single)>=threshold and not re.search(r'https?://|www\.|[A-Za-z]:[\\/]',single):
        # Even a model's one oversized text_part may be split at semantic
        # punctuation. Keep every character of every clause, never hard-cut.
        depth,start,candidate=0,0,[]
        for i,char in enumerate(single):
            if char in '（(【[「“':depth+=1
            elif char in '）)】]」”':depth=max(0,depth-1)
            elif not depth and char in '\n。！？；，' and i-start>=8 and len(single)-i>8:
                end=i if char in '\n。，；' else i+1
                candidate.append(single[start:end].strip());start=i+1
        if candidate:
            candidate.append(single[start:].strip());parts=[p for p in candidate if p]
    if not allow_burst:
        return ['，'.join(parts)] if parts else []
    if len(parts) > maximum:
        parts = parts[:maximum - 1] + ['，'.join(parts[maximum - 1:])]
    return parts


def reply_segments(reply):
    parts = getattr(reply, 'chunks', None) or ([str(reply)] if str(reply) else [])
    segments = []
    for part in parts:
        if segments:
            segments.append({'type': '_message_break', 'data': {}})
        segments.append({'type': 'text', 'data': {'text': part}})
    media = getattr(reply, 'segments', [])
    if media and segments:
        segments.append({'type': '_message_break', 'data': {}})
    return segments + media
