"""Small conversation turns, social acts and source-backed interaction feedback."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import secrets
import time
from style_memory import grams


def words(text):
    return re.sub(r'@\d+|\[[^\]]*\]', '', str(text)).strip()


def social_act(text):
    value = words(text)
    plain = value.strip('（()） ，。！？,.!?\n')
    if re.search(r'画|绘|生成|做.*表情|换成|换为|改成|帮我|给我|(?:锐评|点评|评价|评估|分析)(?:一下|下|这|我|你|他的|她的)|^(?:请|你)?(?:总结|概括|看一下|看下|看一眼|读一下|解释一下)', value):
        return 'request'
    if plain in {'妈妈', '妈', '麻麻', '主人', '宝宝', '乖宝宝'} or (plain and all(
            line.strip('（()） ，。！？,.!?') in {'妈妈', '妈', '麻麻', '主人', '宝宝', '乖宝宝'}
            for line in plain.splitlines() if line.strip())):
        return 'role_play'
    if re.fullmatch(r'喵[喵呜~～]*|汪[汪~～]*|[哈啊呜嘻嘿草]{2,}|y.{1,8}|次.{0,8}', plain, re.I):
        return 'play'
    if re.search(r'拔你网线|炒鱿鱼|笨蛋|没工资|捉到你|抓到你|逮到你|拿捏你|和我.{1,6}$|贴贴|抱抱', plain) or re.fullmatch(
            r'(?:敲|摸|捏|rua|蹭)(?:你|你一下|你脸|你耳朵)[~～!！。]*|'
            r'你是(?:一[个只])?(?:可爱|笨蛋|傻|猫娘|小猫)[\w\u4e00-\u9fff ]*|我(?:傻了|大脑空空)(?:怎么办)?[？?]*',plain,re.I):
        return 'banter'
    if re.fullmatch(r'在吗|你在吗|你好|早[啊呀]?|晚安|[早晚]上好|Ciallo.*', plain, re.I):
        return 'greeting'
    if re.fullmatch(r'嗯嗯?|好[的吧]?|可以|确实|草|对[的啊]?|这(个|样)?(很|挺)?正常|特性', plain):
        return 'acknowledgement'
    if re.search(r'是谁|哪位|怎么|咋|为什么|为啥|如何|多少|什么|哪[个里张]|看看|有没有|能不能|[吗?？]$', value):
        return 'question'
    return 'statement'


def group_mentions(incoming, wants_image):
    """Combine one speaker's short adjacent followups, preserving separate tasks."""
    from dialogue import direct_candidates
    direct_ids = {item.get('_id') for item in direct_candidates(incoming)}
    turns = []
    last_speaker = None
    for item in incoming:
        previous = turns[-1] if turns else None
        gap = item.get('_time', 0) - previous.get('_time', 0) if previous else 999
        quoted_elsewhere = item.get('_reply_id') and item['_reply_id'] not in (previous or {}).get('_source_ids', [])
        independent_request = item.get('_id') in direct_ids and (
            wants_image({**item, 'at_bot': True}) or social_act(item.get('text', '')) in {'request', 'question'})
        if (previous and item.get('_uid') == previous.get('_uid') and item.get('_uid') == last_speaker and 0 <= gap <= 10
                and item.get('_audience') != 'others' and not quoted_elsewhere
                and not independent_request and len(previous['_source_ids']) < 6
                and len(previous['text']) + len(item['text']) < 600):
            previous['text'] += '\n' + item['text']
            previous['_time'] = item['_time']
            previous['_source_ids'].append(item['_id'])
            previous['_images'] = list(dict.fromkeys(previous.get('_images', []) + item.get('_images', [])))[:2]
            previous['_sticker_ids'] = list(dict.fromkeys(previous.get('_sticker_ids', []) + item.get('_sticker_ids', [])))
        elif item.get('_id') in direct_ids:
            turns.append({**item, '_source_ids': [item['_id']], '_anchor_time': item['_time']})
        last_speaker = item.get('_uid')
    return turns


def select_hook(candidates, history):
    """Pick a meaningful hook from the round instead of replying to its last token."""
    if not candidates:
        return None
    newest = max(item.get('_time', 0) for item in candidates)
    def score(item):
        text = words(item['text'])
        act = social_act(text)
        value = {'request': 4, 'question': 3, 'statement': 2, 'banter': 2,
            'play': 1.5, 'role_play': 1, 'greeting': .5, 'acknowledgement': 0}[act]
        value += min(1.5, len(text) / 30)
        value += 1 if item.get('_images') and len(text) >= 4 else 0
        value -= min(1.5, max(0, newest - item.get('_time', 0)) / 30)
        return value
    return max(candidates, key=lambda item: (score(item), item.get('_time', 0)))


def read_source_available(target):
    return bool(target.get('_source_access', {}).get('pages_read')) or any(
        page.get('status') == 'read' and page.get('body') for page in target.get('_reference_pages', []))


def claims_source_unavailable(text):
    return bool(re.search(
        r'(?:没有|没能|未能|没)(?:看到|看出|读到|读取)[^，。；\n]{0,30}(?:网页|主页|页面|正文|内容|github)'
        r'|(?:看不了|看不到|打不开|读不了|读不到)[^，。；\n]{0,30}(?:网页|主页|页面|正文|内容|github)'
        r'|(?:网页|主页|页面|正文|github)[^，。；\n]{0,15}(?:打不开|看不了|看不到|读不到|读不了|没读到|无法打开|无法读取)'
        r'|我[^，。；\n]{0,12}(?:无法|没法|不能)[^，。；\n]{0,8}(?:看|打开|访问|读取)',
        str(text), re.I))


def quality_issue(text, target, previous=()):
    value = words(text)
    act = target.get('_social_act') or social_act(target.get('text', ''))
    if re.search(r'你说得对[，,].{0,45}(?:确实|刚才|前面).{0,50}(?:抱歉|道歉)|'
            r'(?:抱歉|对不起).{0,12}(?:作为AI|我会改进|感谢.{0,4}指出)',value,re.S):
        return 'formal_self_review'
    end=re.sub(r'[\s（()）。，！!？?]+$','',value)
    if len(end)>1 and (act in {'request','question'} or read_source_available(target)) and re.search(r'[:：]|(?:原因是|比如说|例如|分别是|主要有|分别为|首先是)$',end):
        if re.search(r'(?:[:：]|原因是|比如说|例如|分别是|主要有|分别为|首先是)$',end):
            return 'unfinished_answer'
    if (read_source_available(target) and re.search(r'锐评|总结|看到.{0,4}[啥什]|看到了啥|再看|看看',target.get('text',''))
            and re.search(r'能看到几个项目|有(?:一些|几个|不少)项目',value)):
        facts='\n'.join(str(page.get('body','')) for page in target.get('_reference_pages',[])) or str(target.get('_transient_summary',{}).get('source_fact',{}).get('body',''))
        names=re.findall(r'(?:^|\n)([\w.-]{3,100})\s*\|',facts)
        if names and not any(name.lower() in value.lower() for name in names):
            return 'vague_source_answer'
    if read_source_available(target) and claims_source_unavailable(value):
        return 'source_access_contradiction'
    visual = target.get('_cached_vision', []) or ([target['_role_analysis']['vision']]
        if target.get('_role_analysis',{}).get('vision') else [])
    if (any(item.get('confidence',0)>=.6 and item.get('description') for item in visual)
            and re.search(r'这(?:是|个是|张是|啥)|看(?:看|一下|下)|图(?:里|中|是什么)',target.get('text',''))
            and re.search(r'没看出来|没看清|没识别出来|图片不可见|没收到图|图没读到|没弄出结果',value)):
        return 'vision_access_contradiction'
    if re.search(r'刚才.{0,8}被清空|那条.{0,8}清空|没话留下来', value):
        return 'invented_cache_excuse'
    if (act in {'request','question'} and len(value)<80
            and re.search(r'(?:给你|我来|我给你).{0,14}(?:捋|整理|总结|精简|讲)|(?:精简版|详细版)[:：]$',value)):
        return 'promised_answer_without_content'
    if re.search(r'作为AI|主打一个|反差感', value, re.I) or (act not in {'request', 'question'} and
            re.search(r'有什么.{0,8}(需要|帮)|需要我做什么|请提供更多|我可以为你|这图配字', value)):
        return 'service_or_caption'
    if act in {'role_play', 'play', 'banter', 'greeting'} and re.search(r'怎么突然|为什么.{0,6}(叫|说)|什么意思|想让我.{0,6}什么', value):
        return 'question_instead_of_play'
    if act in {'role_play', 'play', 'banter'} and len(value) >= 4 and re.search(r'[什啥]么?.{0,6}[呀呢吗?？][（()）]*$', value):
        return 'literal_question_instead_of_banter'
    def normal(v):
        return re.sub(r'[\W_]+', '', words(v)).lower()
    if value and any(normal(value) == normal(old) for old in previous[-3:]):
        return 'repeated_response'
    generic = {'嗯', '咋啦', '咋了', '怎么了', '啥事', '咋回事'}
    if normal(value) in generic and sum(normal(old) in generic for old in previous[-4:]) >= 2:
        return 'repeated_ping_question'
    if not target.get('at_bot') and act not in {'role_play', 'play'}:
        target_words = normal(target.get('text', ''))
        content = re.sub(r'^[啊哇嗯哦诶唉]+|[啊呀呢哦吧嘛哈呗唉（()）?？]+$', '', value)
        if len(value) >= 5 and len(grams(content) & grams(target_words)) >= max(2, len(grams(content)) * .65):
            return 'passive_restatement'
    return ''


class InteractionMemory:
    def __init__(self, path):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        self.salt = data.get('salt') or secrets.token_hex(16)
        self.turns = data.get('turns', [])[-48:]
        self.feedback = data.get('feedback', [])[-32:]
        self.prune()

    def digest(self, value):
        return hashlib.sha256((self.salt + str(value)).encode()).hexdigest()[:20]

    def prune(self):
        self.turns = [item for item in self.turns if time.time() - item['time'] < 86400][-48:]
        self.feedback = [item for item in self.feedback if time.time() - item['time'] < 7 * 86400][-32:]

    def save(self):
        self.prune()
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps({'salt': self.salt, 'turns': self.turns, 'feedback': self.feedback}, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)

    def remember(self, reply, target):
        if target.get('_ephemeral_content'):
            return
        from group_memory import memory_text
        self.turns.append({'id': reply.get('_id'), 'time': reply['_time'], 'speaker': self.digest(target.get('_uid')),
            'user_said': memory_text(target.get('text', ''), 600)[:160],
            'bot_said': memory_text(reply.get('text', ''), 160) or ('发了一张图片或表情' if reply.get('_media_sent') else ''),
            'expression_ids': target.get('_selected_expression_ids', [])[:3],
            'act': social_act(target.get('text', ''))})
        self.save()

    def observe(self, item):
        quoted = item.get('_reply_id')
        own_quote = quoted and any(turn.get('id') == quoted for turn in self.turns)
        if quoted and not own_quote:
            return
        last = next((turn for turn in reversed(self.turns)
            if turn['speaker'] == self.digest(item.get('_uid')) and 0 <= item['_time'] - turn['time'] <= 120), None)
        if own_quote:
            last=next((turn for turn in reversed(self.turns) if turn.get('id')==quoted),None)
        if not own_quote and not (last and item.get('_audience') in {'direct', 'possibly_direct'}):
            return
        text = words(item['text'])
        tags = []
        if re.search(r'人机|太AI|像AI|客服|尬', text, re.I):
            tags.append('avoid_service_and_commentary')
        if re.search(r'复读|重复|别再问', text):
            tags.append('vary_short_reactions')
        if re.search(r'没问你|答非所问|不是.{0,4}说', text):
            tags.append('check_addressee_and_topic')
        for tag in tags:
            self.feedback.append({'time': item['_time'], 'tag': tag, 'source_id': item.get('_id')})
        if tags:
            self.save()
        if last and last.get('expression_ids'):
            if tags:
                return {'expression_ids':last['expression_ids'],'positive':False}
            if re.search(r'这就对|像群友|舒服了|好耶|懂了|谢谢|谢了',text):
                return {'expression_ids':last['expression_ids'],'positive':True}

    def retrieve(self, target, limit=500):
        self.prune()
        query = grams(target.get('text', ''))
        speaker = self.digest(target.get('_uid'))
        recent_count = sum(1 for turn in self.turns
            if turn['speaker'] == speaker and time.time() - turn['time'] < 86400)
        recall = bool(re.search(r'上次|之前|记得|刚才|那个图', target.get('text', '')))
        hits = [turn for turn in reversed(self.turns) if turn['speaker'] == speaker
            and (recall or (time.time() - turn['time'] < 1200 and len(query & grams(turn['user_said'] + turn['bot_said'])) >= 2))][:2]
        result = {'earlier_exchanges': [{'user_said': item['user_said'], 'bot_said': item['bot_said'],
            'minutes_ago': int((time.time() - item['time']) / 60), 'historical': True,
            'social_act': item.get('act', 'statement')} for item in hits],
            'recent_interaction_count': min(5, recent_count),
            'feedback_hints': [tag for tag, _ in Counter(item['tag'] for item in self.feedback).most_common(3)]}
        while len(json.dumps(result, ensure_ascii=False)) > limit and result['earlier_exchanges']:
            result['earlier_exchanges'].pop()
        return result
