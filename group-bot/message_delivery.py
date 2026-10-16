"""Keep native QQ market expressions intact and resume partially sent replies."""
import re
import asyncio
import random
import httpx
from emoji_library import authorized_groups, market_face_packet
from napcat_errors import NapCatAPIError

AUTHORIZED_GROUP_IDS = {985569438, 855374626}
MAX_DELIVERY_ATTEMPTS = 3


class DeliveryAborted(RuntimeError):
    """A reply has failed definitively; the caller must retire its pending outbox."""


def attach_market_fallbacks(plan, items):
    """Upgrade old persisted outboxes from the approved, isolated-expression library."""
    for part in plan:
        if part.get('action') != 'forward_group_single_msg' or part.get('_market_face'):
            continue
        source = str(part.get('params', {}).get('message_id', ''))
        item = next((item for item in items if item.get('kind') == 'market_face'
            and str(item.get('source_message_id')) == source
            and item.get('source_group_id') in authorized_groups()), {})
        packet = market_face_packet(item.get('data'))
        if packet:
            part['_market_face'] = packet


def delivery_plan(segments, group_id):
    if group_id not in authorized_groups():
        raise ValueError('Unauthorized destination')
    plan, regular, media_ids = [], [], []

    def flush():
        if any(s['type'] != 'reply' for s in regular):
            plan.append({'action': 'send_group_msg', 'params': {
                'group_id': group_id, 'message': list(regular)}})
            if media_ids:plan[-1]['_media_ids']=list(media_ids)
        regular.clear()
        media_ids.clear()

    for segment in segments:
        if segment['type'] == 'reply' and not re.fullmatch(r'-?\d+',str(segment.get('data',{}).get('id',''))):
            raise ValueError('Invalid reply target')
        if segment['type'] == 'at' and not re.fullmatch(r'\d+',str(segment.get('data',{}).get('qq',''))):
            raise ValueError('Invalid at target')
        if segment['type'] == '_message_break':
            flush()
        elif segment['type'] == '_native_market_face':
            source = str(segment.get('data', {}).get('message_id', ''))
            if not re.fullmatch(r'-?\d+', source):
                raise ValueError('Invalid expression source')
            flush()
            part = {'action': 'forward_group_single_msg', 'params': {
                'group_id': group_id, 'message_id': source}}
            packet = market_face_packet(segment.get('data', {}).get('market_face'))
            if packet:
                part['_market_face'] = packet
            if segment.get('_media_id'):part['_media_ids']=[segment['_media_id']]
            plan.append(part)
        else:
            # Selection bookkeeping belongs to the outbox, never the QQ API.
            regular.append({'type':segment['type'],'data':dict(segment.get('data',{}))})
            if segment.get('_media_id') and segment['_media_id'] not in media_ids:
                media_ids.append(segment['_media_id'])
    flush()
    return plan


async def deliver_one(api, client, env, plan):
    """Pop a part only after success, so a later failure cannot resend earlier text."""
    part = plan[0]
    if (part['action'] not in {'send_group_msg', 'forward_group_single_msg'}
            or part['params'].get('group_id') not in authorized_groups()):
        raise ValueError('Invalid delivery action')
    while True:
        if part.get('_attempts', 0) >= MAX_DELIVERY_ATTEMPTS:
            raise DeliveryAborted(f"Delivery retry limit reached; action={part['action']}; attempts={part['_attempts']}")
        part['_attempts'] = part.get('_attempts', 0) + 1
        try:
            result = await api(client, env, part['action'], part['params'])
            break
        except NapCatAPIError as exc:
            packet = market_face_packet(part.get('_market_face'))
            if exc.missing_forward_source and packet:
                # This rejection occurs before QQ forwards anything. Switching is safe;
                # never fall back after a timeout or an unknown forwarding failure.
                part['_unavailable_source_id'] = str(part['params']['message_id'])
                part['action'] = 'send_group_msg'
                part['params'] = {'group_id': part['params']['group_id'],
                    'message': [{'type': 'mface', 'data': packet}]}
                continue
            if exc.retryable and part['_attempts'] < MAX_DELIVERY_ATTEMPTS:
                raise
            raise DeliveryAborted(str(exc)) from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError,
                httpx.RemoteProtocolError) as exc:
            raise DeliveryAborted(f'Delivery result uncertain; do not resend automatically; {type(exc).__name__}') from exc
        except (httpx.HTTPError, OSError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                raise DeliveryAborted(f'Delivery HTTP rejection; status={exc.response.status_code}') from exc
            if part['_attempts'] >= MAX_DELIVERY_ATTEMPTS:
                raise DeliveryAborted(f'Delivery retry limit reached; {type(exc).__name__}') from exc
            raise
    plan.pop(0)
    return result or {}


def typing_delay(part):
    text=''.join(str(s.get('data',{}).get('text','')) for s in part.get('params',{}).get('message',[])
        if s.get('type')=='text')
    return random.uniform(.55,1.05)+min(2,len(text)*.035)


async def deliver_burst(api, client, env, plan, allowed, on_sent, delay=None):
    """Send short parts together, checking budgets and checkpointing every success."""
    last = {}
    sent = 0
    while plan and allowed():
        part = plan[0]
        last = await deliver_one(api, client, env, plan)
        on_sent(last, part)
        sent += 1
        # Bound one burst even if an old pending file contains more messages.
        if not plan or sent >= 15 or not allowed():
            break
        await asyncio.sleep(random.uniform(*delay) if delay is not None else typing_delay(plan[0]))
    return last
