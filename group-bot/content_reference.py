"""Resolve current/quoted/recent media references without importing unrelated threads."""
import re


def urls(text):
    # Chinese request text is often immediately adjacent to a pasted URL.
    found = re.findall(r'https?://[^\s<>"\x00-\x1f\u3000-\u9fff]+', text or '', re.I)
    return list(dict.fromkeys(u.rstrip('.,;!?)]}，。；！？）') for u in found))[:2]


def assets(item):
    links = urls(item.get('text', ''))
    images = [u for u in item.get('_images', []) if u][:2]
    return {'links': links, 'images': images, 'source_id': item.get('_id'),
        'image_hashes':item.get('_image_hashes',[]) or list(item.get('_image_keys',{}).values()),
        'image_keys': {url: key for url, key in item.get('_image_keys', {}).items() if url in images}}


def resolve(target, context, *, for_drawing=False):
    own = assets(target)
    if own['links'] or own['images']:
        return own
    quoted = target.get('_quoted_message') or next((m for m in context
        if target.get('_reply_id') and m.get('_id') == target['_reply_id']), None)
    if quoted is not None:
        # A quote anchors the request; don't substitute somebody else's later image.
        found=assets(quoted)
        if quoted.get('is_bot') and not (found['links'] or found['images']):
            found['links']=urls(quoted.get('_reply_to_text',''))
            if found['links']:
                found['source_id']=quoted.get('_reply_to_id')
                found['original_request']=quoted.get('_reply_to_text','')[:600]
        return found
    words = re.sub(r'@\d+|\[[^\]]*\]|\s+', '', target.get('text', ''))
    if for_drawing and not re.search(
            r'这(?:个|张|幅)?图|这张|上(?:面|一张)?(?:的)?图|刚(?:才|刚).{0,8}图|'
            r'原图|参考(?:一下)?(?:这|上|前)|按(?:照)?(?:这|上|前)|把.{0,8}(?:图|表情)|'
            r'(?:修改|改成|重画|重绘|编辑|照着|基于)', words):
        # A fresh drawing's noun “图片/表情包” is not a reference to the last
        # group's picture. Explicit attachments and quotes were handled above.
        return own
    image_request = for_drawing or bool(re.search(r'这图|这张|图片|看图|上图|图里|图中|表情包|image', words, re.I))
    link_request = bool(re.search(r'链接|网页|页面|网址|这篇|文章', words))
    generic = bool(re.search(r'^(?:你)?(?:帮我|给我)?(?:看(?:一下|一眼|看|下)(?:这个|这是什么|这啥)?|总结(?:一下)?(?:这个)?|这是(?:什么|啥)|这啥|讲讲|解释(?:一下)?(?:这个)?)[（(!！?？\s]*$', words))
    if not (image_request or link_request or generic or not words):
        return own
    for item in reversed(list(context)):
        age = target.get('_time', 0) - item.get('_time', 0)
        if item.get('is_bot') or not 0 <= age <= 180 or item.get('_id') == target.get('_id'):
            continue
        found = assets(item)
        if not (found['images'] or found['links']):
            continue
        if not words and (item.get('_uid') != target.get('_uid') or age > 60):
            return own
        if image_request and not link_request and not found['images']:
            continue
        if link_request and not image_request and not found['links']:
            continue
        return found
    return own
