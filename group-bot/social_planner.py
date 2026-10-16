"""Ground model-selected actions and memories in the actual supplied candidates."""
import json
from emoji_library import ReplyText

PLAN_FIELDS = {
    'action':{'type':'string','enum':['reply','wait','silent','search','sticker','draw','inspect_image']},
    'topic':{'type':'string','maxLength':60},
    'expects_response':{'type':'boolean'},
    'wait_seconds':{'type':'integer','minimum':0,'maximum':20},
    'expression_ids':{'type':'array','maxItems':3,'items':{'type':'string'}},
    'memory_ids':{'type':'array','maxItems':3,'items':{'type':'string'}},
    'memory_query':{'type':'string','maxLength':60}}
PLAN_SCHEMA = {'type':'object', 'additionalProperties':False,
    'properties':PLAN_FIELDS, 'required':list(PLAN_FIELDS)}


def checked_plan(value):
    if not isinstance(value, dict) or set(value) != set(PLAN_FIELDS):
        return False
    if value['action'] not in PLAN_FIELDS['action']['enum']:
        return False
    for key in ('topic', 'memory_query'):
        if not isinstance(value[key], str) or len(value[key]) > 60:
            return False
    if not isinstance(value['expects_response'], bool):
        return False
    if type(value['wait_seconds']) is not int or not 0 <= value['wait_seconds'] <= 20:
        return False
    return all(isinstance(value[key], list) and len(value[key]) <= 3
        and all(isinstance(item, str) and len(item) <= 60 for item in value[key])
        for key in ('expression_ids', 'memory_ids'))


def apply_plan(cfg, target, context_result):
    if (not cfg.get('social_planner_enabled', True) and not cfg.get('semantic_selection_enabled',True)) or context_result.get('confidence', 0) < .65:
        return None
    plan = context_result.get('plan')
    if not checked_plan(plan):
        return None
    expressions = {item['id']:item for item in target.get('_expression_candidates', [])}
    memories = {item['id']:item for item in target.get('_memory_candidates', [])}
    if any(key not in expressions for key in plan['expression_ids']) or any(key not in memories for key in plan['memory_ids']):
        return None
    plan = dict(plan)
    # A sticker action is an executable media-only decision, not another hint.
    # If it is disabled or the pool is empty, keep the ordinary reply route.
    if plan['action']=='sticker' and (not target.get('_media_choices') or
            not target.get('_reply_style',{}).get('allow_no_words_reaction')):
        plan['action']='reply'
    # A mandatory direct response never enters an endless wait/silence loop.
    if target.get('at_bot') and plan['action'] in {'wait', 'silent'}:
        plan['action'] = 'reply'
    if target.get('_wait_attempts', 0) >= 1 and plan['action'] == 'wait':
        plan['action'] = 'reply'
    if cfg.get('semantic_selection_enabled', True):
        target['_expressions'] = [{key:value for key,value in expressions[identity].items()
            if key != 'id'} for identity in plan['expression_ids']]
        target['_memories'] = [memories[identity] for identity in plan['memory_ids']]
        target['_selected_expression_ids'] = list(plan['expression_ids'])
    if not cfg.get('social_planner_enabled',True):return None
    state = cfg.get('_social_state')
    if state:
        state.plan(target, plan)
    target['_accepted_plan'] = plan
    if plan['action']=='sticker' and target.get('_media_choices') and target.get('_reply_style',{}).get('sticker_policy') in {'reaction_optional','reaction_or_followup'}:
        target['_reply_style']={**target['_reply_style'],'prefer_reaction':True,
            'allow_no_words_reaction':True,'delivery_mode':'reaction','planned_sticker':True}
    if plan['action'] == 'silent':
        reply=ReplyText('', route='silent')
        reply.reserved_reply_unused=True
        return reply
    if plan['action'] == 'wait' and plan['wait_seconds']:
        reply = ReplyText('', route='wait')
        reply.wait_seconds = plan['wait_seconds']
        reply.reserved_reply_unused=True
        return reply
    return None
