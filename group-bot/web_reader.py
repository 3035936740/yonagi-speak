"""Bounded public-page GETs: no local networks, cookies, credentials or browser session."""
import asyncio
import base64
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import logging
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 2 * 1024 * 1024
log = logging.getLogger('qq-group-bot')


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.body, self.main, self.title = [], [], [], []

    def handle_starttag(self, tag, attrs):
        if tag in {'br', 'hr', 'img', 'meta', 'link', 'input', 'source', 'wbr', 'area', 'embed'}:
            if tag == 'br': self.body.append('\n'); self.main.append('\n')
            return
        attrs = dict(attrs)
        names = attrs.get('class', '') + ' ' + attrs.get('id', '')
        hidden = tag in {'script', 'style', 'noscript', 'nav', 'footer', 'header', 'form', 'aside', 'svg'} or attrs.get('aria-hidden') == 'true'
        hidden = hidden or bool(re.search(r'\b(?:menu|navbar|sidebar|breadcrumb|comments?|advertisement)\b', names, re.I))
        preferred = tag in {'main', 'article'} or bool(re.search(r'\b(?:text-area|item-content|entry-content|article-content)\b', names))
        self.stack.append((tag, hidden, preferred))
        if tag in {'p', 'div', 'li', 'h1', 'h2', 'h3', 'tr'}:
            self.body.append('\n'); self.main.append('\n')

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if any(tag == 'title' for tag, _, _ in self.stack):
            self.title.append(data)
        elif not any(hidden for _, hidden, _ in self.stack):
            self.body.append(data)
            if any(preferred for _, _, preferred in self.stack): self.main.append(data)

    def result(self, limit):
        def clean(parts):
            return '\n'.join(line for line in (re.sub(r'\s+', ' ', s).strip() for s in ''.join(parts).splitlines()) if line)
        preferred = clean(self.main)
        return clean(self.title)[:160], (preferred if len(preferred) >= 40 else clean(self.body))[:limit]


def public_address(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443} or re.search(r'[\x00-\x20\x7f]', url)):
        raise ValueError('unsupported_url')
    host = parsed.hostname.encode('idna').decode('ascii')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    def allowed(value):
        address = ipaddress.ip_address(value.split('%')[0])
        return address.is_global and not (address.is_multicast or address.is_reserved or address.is_unspecified)
    if not addresses or any(not allowed(a[4][0]) for a in addresses):
        raise ValueError('non_public_address')
    return parsed, host, port, addresses[0][4][0]


def fetch(url, limit=1800, expect_json=False, timeout_seconds=15):
    start = time.monotonic()
    budget = min(15, max(.1, timeout_seconds))
    for _ in range(4):
        parsed, host, port, address = public_address(url)
        remaining = min(8, budget - (time.monotonic() - start))
        if remaining <= 0: raise TimeoutError('page_timeout')
        connection = http.client.HTTPConnection(host, port, timeout=remaining)
        sock = socket.create_connection((address, port), timeout=remaining)
        try:
            if parsed.scheme == 'https':
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            # Pin the checked public address; never resolve the hostname again on connect.
            connection.sock = sock
            path = parsed.path or '/'
            if parsed.query: path += '?' + parsed.query
            connection.request('GET', path, headers={'Host': host + (':' + str(port) if parsed.port else ''),
                'User-Agent': 'Mozilla/5.0 (compatible; QQGroupPublicReader/1.0)',
                'Accept': 'application/vnd.github+json' if expect_json else 'text/html,text/plain;q=0.9',
                'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                if not location: raise ValueError('redirect_without_location')
                url = urljoin(url, location)
                continue  # every hop repeats DNS, address and scheme checks
            if response.status != 200: raise ValueError('http_' + str(response.status))
            content_type = response.getheader('Content-Type', '').lower()
            if not any(t in content_type for t in (('application/json',) if expect_json else
                    ('text/html', 'text/plain', 'application/xhtml+xml'))):
                raise ValueError('unsupported_content_type')
            if response.getheader('Content-Encoding', 'identity') != 'identity':
                raise ValueError('unsupported_encoding')
            data = bytearray()
            while len(data) <= MAX_BYTES:
                sock.settimeout(max(.1, min(8, budget - (time.monotonic() - start))))
                chunk = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
                if not chunk: break
                data.extend(chunk)
                if time.monotonic() - start >= budget: raise TimeoutError('page_timeout')
            if len(data) > MAX_BYTES: raise ValueError('page_too_large')
            charset = re.search(r'charset\s*=\s*["\']?([\w-]+)', content_type)
            if not charset:
                charset = re.search(r'charset\s*=\s*["\']?([\w-]+)', data[:4096].decode('ascii', errors='ignore'))
            try: text = data.decode(charset.group(1) if charset else 'utf-8', errors='replace')
            except LookupError: text = data.decode('utf-8', errors='replace')
            if expect_json:
                return {'url': url, 'data': json.loads(text)}
            if 'text/plain' in content_type:
                title, body = '', text[:limit]
            else:
                parser = PageText(); parser.feed(text); title, body = parser.result(limit)
            if len(body.strip()) < 20: raise ValueError('empty_or_script_only_page')
            return {'status': 'read', 'url': url[:500], 'title': title, 'body': body,
                'truncated':len(body)>=limit,
                'scope': '网页正文；不包含视频播放内容'}
        finally:
            connection.close()
            sock.close()
    raise ValueError('too_many_redirects')


def github_target(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in {'github.com', 'www.github.com'}
            or parsed.username or parsed.password or parsed.port not in {None, 443}):
        return []
    parts = parsed.path.strip('/').split('/')
    if not 1 <= len(parts) <= 2 or any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', p) for p in parts):
        return []
    return parts


def fetch_github(url, limit=1800, timeout_seconds=10):
    """Official public REST evidence when the GitHub HTML connection times out."""
    parts = github_target(url)
    if not parts: return None
    deadline = time.monotonic() + timeout_seconds
    sources = []
    def get(path, optional=False):
        remaining = deadline - time.monotonic()
        if remaining <= .2: return None
        endpoint = 'https://api.github.com/' + path
        try:
            response = fetch(endpoint, expect_json=True, timeout_seconds=remaining)
            sources.append(endpoint)
            return response['data']
        except Exception:
            if not optional: raise
            return None
    owner = parts[0]
    if len(parts) == 1:
        profile = get('users/' + owner)
        if not isinstance(profile, dict) or str(profile.get('login', '')).lower() != owner.lower():
            raise ValueError('invalid_github_profile')
        lines = ['GitHub用户：' + str(profile.get('login')), '显示名：' + str(profile.get('name') or ''),
            '简介：' + str(profile.get('bio') or ''), '公开仓库数：' + str(profile.get('public_repos'))]
        repo_path = owner + '/' + owner
        readme = get('repos/' + repo_path + '/readme', optional=True)
        repositories = get('users/' + owner + '/repos?per_page=8&sort=updated', optional=True)
        title = owner + ' GitHub公开主页'
        scope = 'GitHub官方公开API；仓库列表按更新时间排序，不代表置顶；未读取源码'
    else:
        repo_path = '/'.join(parts)
        repo = get('repos/' + repo_path)
        if not isinstance(repo, dict) or str(repo.get('full_name', '')).lower() != repo_path.lower():
            raise ValueError('invalid_github_repository')
        lines = []
        readme = get('repos/' + repo_path + '/readme', optional=True)
        repositories = [repo]
        title = repo_path + ' GitHub仓库'
        scope = 'GitHub官方公开API；仓库元数据与已读取的README，不是完整源码审计'
    if isinstance(readme, dict) and readme.get('encoding') == 'base64' and isinstance(readme.get('content'), str):
        try:
            text = base64.b64decode(readme['content']).decode('utf-8', errors='replace')
            text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
            parser = PageText()
            parser.feed(text)
            _, excerpt = parser.result(max(300, limit // 2))
            lines.append('公开README摘录：\n' + excerpt)
        except (ValueError, TypeError):
            pass
    if isinstance(repositories, list):
        lines.append('已读公开仓库：')
        for repo in repositories[:8]:
            if not isinstance(repo, dict): continue
            lines.append(str(repo.get('name', ''))[:100] + ' | '
                + str(repo.get('description') or '')[:120] + ' | 语言：'
                + str(repo.get('language') or '未标记') + ' | star：'
                + str(repo.get('stargazers_count', 0))
                + (' | fork' if repo.get('fork') else '')
                + (' | 已归档' if repo.get('archived') else ''))
    body = '\n'.join(lines)
    return {'status': 'read', 'url': url[:500], 'title': title, 'body': body[:limit],
        'truncated': len(body) > limit, 'scope': scope, 'backend': 'github_public_api',
        'source_urls': sources}


async def read_pages(urls, limit=1800):
    results = []
    # At most one page per reply leaves room for the live conversation in the 6000-char budget.
    for url in urls[:1]:
        # Source text belongs only to this reply job. Never retain full pages in
        # a module cache; a small delivered summary has its own two-minute TTL.
        deadline = time.monotonic() + 18
        # Profile/repository HTML often spends the short excerpt on navigation
        # and hides the repo list. Prefer structured public evidence for GitHub.
        if github_target(url):
            try:
                recovered=await asyncio.wait_for(asyncio.to_thread(fetch_github,url,limit,10),timeout=10.5)
                if recovered and recovered.get('body'):
                    results.append(recovered)
                    log.info('GitHub public evidence read; body_chars=%s',len(recovered['body']))
                    continue
            except Exception as exc:
                log.info('GitHub structured evidence unavailable; trying HTML; error_type=%s',type(exc).__name__)
        for attempt in range(1, 3):
            try:
                result = await asyncio.wait_for(asyncio.to_thread(fetch, url, limit) if limit!=1800
                    else asyncio.to_thread(fetch,url), timeout=max(.1, deadline-time.monotonic()))
                break
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                retryable = isinstance(exc, OSError) or reason in {'http_429', 'http_500',
                    'http_502', 'http_503', 'http_504'}
                result = {'status': 'unavailable', 'url': url[:500], 'reason': reason[:80],
                    'attempts': attempt}
                if retryable and github_target(url) and deadline-time.monotonic() >= 3:
                    try:
                        remaining = deadline-time.monotonic()
                        recovered = await asyncio.wait_for(asyncio.to_thread(fetch_github, url, limit,
                            min(10, remaining)), timeout=remaining)
                        if recovered:
                            result = recovered
                            log.info('GitHub public API recovered page; body_chars=%s', len(result['body']))
                            break
                    except Exception as api_exc:
                        log.info('GitHub public API fallback unavailable; error_type=%s', type(api_exc).__name__)
                if not retryable or attempt == 2 or deadline-time.monotonic() < 3:
                    break
                log.info('Transient page read failure; reason=%s; retry=1', reason[:80])
        results.append(result)
    return results
