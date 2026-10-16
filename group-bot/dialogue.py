"""Explicit conversation routing and send-time freshness checks."""
import re
from style_memory import grams
from console_config import Config
from pathlib import Path
from group_identity import account_mentioned, name_mentioned


BOT_NAME_PATTERN = re.compile(
    r'(?<![A-Za-z0-9_])(?:yonagi|Ethereal\s*Paradise|EP)(?![A-Za-z0-9_])', re.I)


def bot_name_mentioned(text):
    """Names are explicit mentions anywhere in the message, as in MaiBot."""
    cfg=Config(Path(__file__).parent).load()
    names=[cfg['bot_name']]+cfg['bot_aliases']
    pattern=r'(?<![A-Za-z0-9_])(?:'+'|'.join(re.escape(name).replace(r'\ ',r'\s*') for name in names)+r')(?![A-Za-z0-9_])'
    return bool(re.search(pattern,str(text or ''),re.I))


def feedback_about_reply(text):
    """Comprehension/correction feedback can be a request without a question mark."""
    return bool(re.search(r'看不懂|没看懂|听不懂|没听懂|不明白|没明白|没懂|看懵|听懵|'
        r'啥意思|什么意思|何意味|说人话|讲人话|人话点|简单[一点]+|通俗[一点]+|太复杂|太绕|'
        r'你(?:在|这)?说(?:的)?(?:啥|什么)|什么.{0,6}玩意|答非所问|没问你这个|不是这个意思', text))


def question_or_request(text):
    text = re.sub(r'@\d+|\[[^\]]*\]', '', text).strip()
    return bool(re.search(r'是谁|哪位|怎么|咋|为什么|为啥|如何|什么意思|多少|哪[个张里]|有没有|能不能|可不可以|帮我|给我|看看|试试|[吗呢?？][（()）\s]*$', text))


def audience(item, history, self_id):
    if item.get('at_bot'):
        return 'direct'
    if account_mentioned(item.get('text',''),self_id):
        item['_named_bot']=True
        item['_identity_mention']='qq_id'
        return 'direct'
    text = re.sub(r'@\d+|\[[^\]]*\]', '', item.get('text', '')).strip()
    configured_names=item.get('_unique_bot_names',[])+item.get('_ambiguous_bot_names',[])
    called=(any(name_mentioned(text,name) for name in configured_names) if configured_names else bot_name_mentioned(text))
    if called or any(name_mentioned(text,name) for name in item.get('_self_names',[])):
        item['_named_bot'] = True
        item['_identity_mention']='name'
        if (any(name_mentioned(text,name) for name in item.get('_ambiguous_bot_names',[]))
                and not any(name_mentioned(text,name) for name in item.get('_unique_bot_names',[]))):
            item['_name_collision']=True
            return 'possibly_direct'
        return 'direct'
    ats = {str(value) for value in item.get('_at_ids', [])}
    if ats - {str(self_id), 'all'}:
        return 'others'
    if item.get('_quotes_bot'):
        return 'direct'
    quote = next((m for m in history if m.get('_id') == item.get('_reply_id') and item.get('_reply_id')), None)
    if quote:
        return 'direct' if quote.get('is_bot') else 'others'
    if item.get('_reply_id'):
        return 'others'
    previous = [m for m in history if m.get('_id') != item.get('_id')
        and 0 <= item.get('_time', 0) - m.get('_time', 0) <= 180]
    recent_bot = next((m for m in reversed(previous) if m.get('is_bot')), None)
    if (recent_bot and previous[-1].get('is_bot')
            and 0 <= item.get('_time',0)-recent_bot.get('_time',0) <= 45
            and feedback_about_reply(text)):
        item['_feedback_on_answer']=True
        item['_continuation_bot_id']=recent_bot.get('_id')
        return 'possibly_direct'
    if recent_bot and item.get('_uid') and item.get('_uid') == recent_bot.get('_reply_to_uid'):
        age = item.get('_time', 0) - recent_bot.get('_time', 0)
        switched = any(not m.get('is_bot') and m.get('_uid') == item.get('_uid')
            and m.get('_time', 0) > recent_bot.get('_time', 0)
            and m.get('_audience') == 'others' for m in previous)
        if 0 <= age <= 120 and not switched and feedback_about_reply(text):
            item['_feedback_on_answer'] = True
            item['_continuation_bot_id'] = recent_bot.get('_id')
            return 'possibly_direct'
        # A recent participant may continue with a statement or a new request
        # without using pronouns. Let the context role/planner decide, while
        # cheap acknowledgements and explicitly opened human threads stay out.
        plain=text.strip('（()） ，。！？,.!?~～')
        acknowledgement=bool(re.fullmatch(r'嗯+|哦+|好(?:的|吧)?|可以|确实|对(?:的|啊)?|草|[哈啊]{1,6}|6+|谢谢|谢了',plain))
        if (0 <= age <= 120 and not switched and 2 <= len(plain) <= 180
                and not acknowledgement and not re.fullmatch(r'\[[^\]]+\]',text)
                and not re.search(r'你|帮我|给我|能不能|可不可以',text)):
            item['_continuation_bot_id']=recent_bot.get('_id')
            return 'possibly_direct'
    # Followups can be jokes or statements, not only questions. Keep them
    # ambiguous so the planner can reject a newly opened human conversation.
    if recent_bot and item.get('_uid') == recent_bot.get('_reply_to_uid') and not question_or_request(text):
        age = item.get('_time', 0) - recent_bot.get('_time', 0)
        latest_human = next((m for m in reversed(previous) if not m.get('is_bot')
            and m.get('_uid') == item.get('_uid')), None)
        switched = latest_human and latest_human.get('_time', 0) > recent_bot.get('_time', 0) and latest_human.get('_audience') == 'others'
        if 0 <= age <= 45 and switched and re.search(r'你|这个|那个|所以', text):
            return 'others'
        if 0 <= age <= 45 and not switched and (re.search(r'你|这|那|所以|喵|妈妈|主人', text)
                or len(grams(text) & grams(recent_bot.get('text', '') + recent_bot.get('_reply_to_text', ''))) >= 2):
            return 'possibly_direct'
    if recent_bot and previous and not previous[-1].get('is_bot') and question_or_request(text):
        same_sender = next((m for m in reversed(previous) if not m.get('is_bot')
            and item.get('_uid') and m.get('_uid') == item['_uid']), None)
        generic_followup = bool(re.match(r'^(那|这个|这样|所以|还是)?(具体|到底|应该|要)?(怎么|咋|如何|为什么|为啥)', text))
        if (generic_followup and same_sender and same_sender.get('_time', 0) > recent_bot.get('_time', 0)
                and same_sender.get('_audience') == 'others'):
            # A participant who switched to a human thread is no longer in our focus.
            return 'others'
    if previous and previous[-1].get('is_bot'):
        if re.search(r'你|帮我|给我|能不能|可不可以|你觉得', text):
            # Pronouns are evidence, not an explicit @. The context role can
            # distinguish a question to us from a newly opened human exchange.
            return 'possibly_direct'
        if question_or_request(text):
            return 'possibly_direct'
    if recent_bot and question_or_request(text):
        same_person = item.get('_uid') and item['_uid'] == recent_bot.get('_reply_to_uid')
        topic = grams(recent_bot.get('text', '') + recent_bot.get('_reply_to_text', ''))
        referring_back = bool(re.match(r'那|所以|还是|刚才|这个|这样|为什么|为啥|怎么|咋', text))
        if same_person and (referring_back or len(topic & grams(text)) >= 2):
            return 'possibly_direct'
        if re.search(r'你', text) and len(topic & grams(text)) >= 2:
            return 'possibly_direct'
    if re.match(r'^\s*你', text):
        if re.match(r'^你[A-Za-z][\w.-]+(?:叔叔|哥|姐|老师)', text):
            return 'open'
        return 'others'
    return 'open'


def direct_candidates(incoming):
    return [item for item in incoming if item.get('at_bot') or item.get('_audience') in {'direct', 'possibly_direct'}]


def reply_lane(item, cfg):
    # An ambiguous request must first be confirmed by the reply planner.
    from_image = item.get('_image_request') and (item.get('at_bot') or item.get('_audience') == 'direct')
    return 'image' if from_image and cfg.get('image_generation_enabled', False) else 'text'


def passive_candidate(item, history, self_id):
    if audience(item, history, self_id) == 'others':
        return False
    text = re.sub(r'@\d+|\[[^\]]*\]|\s+', '', item.get('text', ''))
    previous = [m for m in history if m.get('_id') != item.get('_id')
        and 0 <= item.get('_time', 0) - m.get('_time', 0) <= 120]
    acknowledgement = re.fullmatch(r'(这(个|样)?(很|挺)?正常|特性|对[啊的吧]?|确实|可以|是的|嗯嗯?|好[的吧]?)[（()）x]*', text)
    if acknowledgement and previous and not previous[-1].get('is_bot') and not item.get('at_bot'):
        # A short confirmation in someone else's exchange is not a new topic.
        return False
    return bool(text) or bool(item.get('_sticker_ids'))


def fresh_reply(target, history, now):
    if target.get('_audience') == 'direct' or target.get('at_bot'):
        return True
    if now - target.get('_time', now) > 180:
        return False
    newer = [m for m in history if not m.get('is_bot') and m.get('_time', 0) > target.get('_time', 0)]
    if len(newer) < 3:
        return True
    topic = grams(target.get('text', ''))
    return any(len(topic & grams(m.get('text', ''))) >= 2 for m in newer[-3:])


def quote_needed(target, history, now):
    intervening = sum(not m.get('is_bot') and m.get('_time', 0) > target.get('_time', now) for m in history)
    return target.get('_id') and (now - target.get('_anchor_time', target.get('_time', now)) >= 45 or intervening >= 4)


def personal_claim(text, target_text=''):
    explicit = re.search(r'我(?:最近|今天|这几天|刚|以前|现在|还在).{0,8}(?:上班|下班|工作|加班|上学|大学|学校|工资|公司)|我(?:家|爸|妈|老婆|老公)|我住在', text)
    implied = '上班' in target_text and re.search(r'可能是|最近.*累|是啊|加班', text) and not re.search(r'没|不是|不在', text)
    return bool(explicit or implied)


def call_only(text):
    words = re.sub(r'@\d+|\s+|[，。！？,.!?]', '', text)
    return words in {'', '妈妈', '妈', '麻麻', '主人', '宝宝', '乖宝宝'}


def coalesce_calls(queue, item):
    retained = [old for old in queue if not (
        old.get('_uid') == item.get('_uid') and call_only(old.get('text', ''))
        and not old.get('_image_request') and not item.get('_image_request')
        and 0 <= item.get('_time', 0) - old.get('_time', 0) <= 30)]
    return retained + [item]


def superseded_call(target, queue):
    return call_only(target.get('text', '')) and any(
        item.get('_uid') == target.get('_uid') and not item.get('_image_request')
        and 0 < item.get('_time', 0) - target.get('_time', 0) <= 30 for item in queue)
