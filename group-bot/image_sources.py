"""QQ image source validation and diagnostics without signed URLs or payloads."""
import re
from urllib.parse import urlsplit

import httpx
from PIL import UnidentifiedImageError


class ImageDownloadError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_source(url, *, allow_avatar=False):
    try:
        parsed = urlsplit(url)
        allowed = {'multimedia.nt.qq.com.cn', 'gchat.qpic.cn', 'gxh.vip.qq.com'}
        if allow_avatar:
            allowed.add('q1.qlogo.cn')
        valid = (parsed.scheme in {'http', 'https'} and parsed.hostname in allowed
            and not parsed.username and not parsed.password and parsed.port in {None, 80, 443})
        if parsed.hostname == 'gxh.vip.qq.com':
            valid = valid and parsed.scheme == 'https' and parsed.path.startswith('/club/item/parcel/item/')
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ImageDownloadError('image_source_not_allowed')
    return parsed


def failure_details(error, url):
    try:
        host = urlsplit(url).hostname or 'unknown'
    except (TypeError, ValueError, AttributeError):
        host = 'invalid'
    host = re.sub(r'[^a-zA-Z0-9.-]', '_', host)[:100]
    if isinstance(error, ImageDownloadError):
        reason = error.code
    elif isinstance(error, httpx.HTTPStatusError):
        reason = 'image_http_' + str(error.response.status_code)
    elif isinstance(error, httpx.TimeoutException):
        reason = 'image_download_timeout'
    elif isinstance(error, UnidentifiedImageError):
        reason = 'image_invalid_data'
    elif type(error).__name__ == 'PromptBudgetError':
        reason = 'image_prompt_budget_exceeded'
    else:
        reason = type(error).__name__
    return 'reason=' + reason + '; source=' + host
