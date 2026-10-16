"""Shared approved stickers, content-hash deduplication and intact native faces."""
import asyncio
import base64
from contextlib import contextmanager
from functools import wraps
import hashlib
import io
import json
from pathlib import Path
import re
import random
import time
import tempfile
import uuid
import os
from image_sources import validate_source, ImageDownloadError
from PIL import Image, ImageDraw
from style_memory import grams
from sticker_filter import approved, accepts, valid_classification, FILTER_VERSION, EMOTIONS
from console_config import read_json, pid_alive
from emoji_assets import asset_path, managed_asset_path, own_asset
from storage_io import atomic_write_text

AUTHORIZED_GROUP_IDS = {985569438, 855374626}


def authorized_groups():
    return set(read_json(Path(__file__).with_name('settings.json')).get('chat_group_whitelist',list(AUTHORIZED_GROUP_IDS)))


def serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.transaction():
            return method(self, *args, **kwargs)
    return wrapped


def sticker_source_key(data):
    file = str(data.get('file', ''))
    # Ordinary filenames can be reused for unrelated pictures. QQ hash names
    # are stable; otherwise bind identity to both filename and download source.
    identity = file if re.fullmatch(r'[a-fA-F0-9]{32,64}(?:\.[A-Za-z0-9]+)?', file) else file + '|' + str(data.get('url', ''))
    return 's' + hashlib.sha256(identity.encode()).hexdigest()[:16]


class ReplyText(str):
    def __new__(cls, text, segments=(), route=None, action='chat', image_request='', chunks=None, media_id=None,
            quote_reply=None, at_uids=(), media_ids=()):
        obj = super().__new__(cls, text)
        obj.segments = list(segments)
        obj.route = route
        obj.action = action
        obj.image_request = image_request
        obj.chunks = chunks
        obj.media_id = media_id
        obj.media_ids = list(media_ids) or ([media_id] if media_id else [])
        obj.quote_reply = quote_reply
        obj.at_uids = list(at_uids)
        return obj

    def __bool__(self):
        return bool(str(self)) or bool(self.segments) or self.action == 'generate_image'


def selected_media(result, choices, limit=3, track=False):
    """Resolve ordered, distinct selections from the supplied candidate shortlist."""
    identities=result.get('media_ids',[])
    identities=identities if isinstance(identities,list) else []
    # Old reply records and old models still use the single-selection fields.
    if not identities:
        identities=[result.get('media_id')] if result.get('media_id') else []
        choices=[item for item in choices if item['kind']==result.get('media_kind')]
    segments=[];seen=set();count=0
    for identity in identities[:20]:
        if not isinstance(identity,str):continue
        item=next((item for item in choices if item['id']==identity),None)
        if not item:continue
        asset=item.get('content_hash') or item.get('hash') or item.get('sha256') or item['id']
        if asset in seen:continue
        seen.add(asset)
        media=_single_media({'media_id':identity,'media_kind':item['kind']},[item])
        if not media:continue
        if track:
            for segment in media:segment['_media_id']=identity
        if segments:segments.append({'type':'_message_break','data':{}})
        segments.extend(media);count+=1
        if count>=max(1,min(5,limit)):break
    return segments


def _single_media(result, choices):
    selected = next((item for item in choices if item['id'] == result.get('media_id')
        and item['kind'] == result.get('media_kind')), None)
    if not selected:
        return []
    if selected['kind'] == 'qq_face':
        if not selected.get('explicitly_requested'):
            return []
        return [{'type': 'face', 'data': {'id': selected['face_id']}}]
    if selected['kind'] == 'market_face':
        source = selected.get('source_message_id')
        if selected.get('source_group_id') in authorized_groups() and re.fullmatch(r'-?\d+', str(source or '')):
            # Rebuilding MFACE loses QQ's rendering metadata for some packs.
            # This is an internal instruction, never a OneBot message segment.
            packet = market_face_packet(selected.get('data'))
            if packet and str(selected.get('unavailable_source_id')) == str(source):
                return [{'type': 'mface', 'data': packet}]
            data = {'message_id': str(source)}
            if packet:
                data['market_face'] = packet
            return [{'type': '_native_market_face', 'data': data}]
        return []
    if selected['kind'] == 'sticker':
        if not approved(selected):
            return []
        path = asset_path(selected)
        if path.is_file() and path.stat().st_size <= 2 * 1024 * 1024:
            return [{'type': 'image', 'data': {'file': 'base64://' + base64.b64encode(path.read_bytes()).decode('ascii')}}]
    return []


def market_face_packet(data):
    """Only reuse complete original metadata, never reconstruct IDs from model text."""
    if (not isinstance(data, dict) or type(data.get('emoji_package_id')) is not int
            or data['emoji_package_id'] <= 0
            or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', str(data.get('emoji_id', '')))
            or not isinstance(data.get('key'), str) or not data['key']
            or len(data['key']) > 160 or not isinstance(data.get('summary'), str)):
        return None
    return {key: data[key] for key in ('emoji_package_id', 'emoji_id', 'key', 'summary')}


class EmojiLibrary:
    def __init__(self, root, face_catalog, classifier=None, shared=False):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'index.json'
        self.shared = shared
        self._depth = 0
        self.aliases_path = self.root / 'aliases.json'
        self.hashes_path = self.root / 'hashes.json'
        self.aliases = self._read(self.aliases_path)
        self.hashes = self._read(self.hashes_path)
        self.classifier = classifier
        self.usage_by_key = {}
        self.review_min_count=5;self.review_step=3;self.upload_retry_seconds=300
        self._upload_origin=uuid.uuid4().hex
        self.rejected_path = self.root / 'rejected.json'
        try:
            self.rejected = json.loads(self.rejected_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.rejected = {}
        try:
            self.items = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.items = {}
        try:
            self.faces = json.loads(Path(face_catalog).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.faces = {}
        try:
            self.faces.update(json.loads((self.root / 'observed-faces.json').read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
        self.queue = asyncio.Queue(maxsize=64)
        self.queued = set()

    @staticmethod
    def _read(path):
        try:
            result = json.loads(path.read_text(encoding='utf-8'))
            return result if isinstance(result, dict) else {}
        except (OSError, ValueError):
            return {}

    def reload(self):
        self.items = self._read(self.path)
        self.rejected = self._read(self.rejected_path)
        self.aliases = self._read(self.aliases_path)
        self.hashes = self._read(self.hashes_path)
        self.faces.update(self._read(self.root / 'observed-faces.json'))

    @contextmanager
    def transaction(self):
        if not self.shared or self._depth:
            yield
            return
        import msvcrt
        with (self.root / 'index.lock').open('a+b') as lock:
            if lock.seek(0, 2) == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                self.reload()
                self._depth += 1
                yield
            finally:
                self._depth -= 1
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

    def _write(self, path, data):
        atomic_write_text(path, json.dumps(data, ensure_ascii=False))

    def save(self):
        if self.shared and not self._depth:
            # All mutators take a transaction. Shutdown must not publish a stale snapshot.
            with self.transaction():
                self.save()
            return
        self._write(self.path, self.items)
        self._write(self.aliases_path, self.aliases)
        self._write(self.hashes_path, self.hashes)
        self._write(self.rejected_path, self.rejected)

    @serialized
    def ensure_assets(self, portable=True):
        # The one-time repair can retain absolute owned paths while old workers
        # are still running. Normal startup converts them to library-relative paths.
        report = {'migrated':0,'unavailable':0}
        backup = False
        for key,item in self.items.items():
            if not approved(item):continue
            try:
                source = managed_asset_path(item,'path',self.root)
                owned = own_asset(self.root,key,source,item.get('content_hash'))
                if not portable:
                    owned.update({field:str((self.root/owned[field]).resolve()) for field in ('path','preview')})
                if all(item.get(field) == value for field,value in owned.items()):continue
                if not backup:
                    folder=self.root/'admin-backups';folder.mkdir(exist_ok=True)
                    self._write(folder/('asset-migration-'+str(time.time_ns())+'.json'),self.items)
                    backup=True
                item.update(owned);report['migrated'] += 1
            except (OSError,ValueError):report['unavailable'] += 1
        if report['migrated']:self.save()
        return report

    @serialized
    def import_library(self, old_root):
        old_root = Path(old_root)
        report = {'added': 0, 'duplicates': 0, 'ignored': 0}
        for old_key, item in self._read(old_root / 'index.json').items():
            if approved(item) and asset_path(item,'path',old_root).is_file():
                source=asset_path(item,'path',old_root)
                if not source.is_relative_to(Path(old_root).resolve()):
                    report['ignored'] += 1;continue
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                key = self.hashes.get(digest, 's' + digest[:16])
                self.aliases[old_key] = key
                if digest in self.hashes:
                    report['duplicates'] += 1
                    continue
                self.hashes[digest] = key
                owned=own_asset(self.root,key,source,digest)
                self.items[key] = {**item, 'id': key, **owned}
                report['added'] += 1
            elif (item.get('kind') == 'market_face' and item.get('source_group_id') in authorized_groups()
                    and re.fullmatch(r'-?\d+', str(item.get('source_message_id', '')))):
                if old_key not in self.items:
                    self.items[old_key] = item
                    report['added'] += 1
            else:
                report['ignored'] += 1
        self.rejected.update(self._read(old_root / 'rejected.json'))
        self.faces.update(self._read(old_root / 'observed-faces.json'))
        self._write(self.root / 'observed-faces.json', self.faces)
        self.prune()
        self.save()
        return report

    @serialized
    def offer_candidate(self, data, usage=None):
        """Repetition is a candidate signal; normal visual admission still applies."""
        key = sticker_source_key(data)
        canonical = self.aliases.get(key, key)
        if usage:
            self.usage_by_key[key]=dict(usage)
            while len(self.usage_by_key)>1024:self.usage_by_key.pop(next(iter(self.usage_by_key)))
        if key in self.rejected or canonical in self.rejected:
            rejected=self.rejected.get(canonical,self.rejected.get(key,{}))
            count=int((usage or {}).get('seen_count',0))
            previous=int(rejected.get('review_seen_count',0))
            if rejected.get('asset_type') not in {'photo','uncertain','other'} or count<self.review_min_count or count<previous+self.review_step:return
            # Frequent reaction use is new evidence. Reconsider it once counts
            # materially increase; screenshots/memes never bypass exclusion.
            if key not in self.queued and not self.queue.full():
                self.queue.put_nowait((key,dict(data)));self.queued.add(key)
            return
        if canonical not in self.items and canonical not in self.hashes.values() and key not in self.queued and not self.queue.full():
            self.queue.put_nowait((key, dict(data)))
            self.queued.add(key)
        elif canonical in self.items:
            self.items[canonical]['time'] = time.time()
            self.save()

    @serialized
    def observe(self, msg):
        segments = msg.get('message', [])
        if not isinstance(segments, list):
            return
        for segment in segments:
            kind, data = segment.get('type'), segment.get('data', {})
            if kind == 'face' and str(data.get('id', '')).isdigit():
                face_id = str(data['id'])
                self.faces.setdefault(face_id, str(data.get('name') or 'QQ表情' + face_id)[:30])
                self._write(self.root / 'observed-faces.json', self.faces)
            elif kind == 'mface' or (kind == 'image' and data.get('emoji_id') and data.get('emoji_package_id')):
                if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', str(data.get('emoji_id', ''))):
                    continue
                packet = {key: data.get(key) for key in ('emoji_package_id', 'emoji_id', 'key', 'summary')}
                if not isinstance(packet['emoji_package_id'], int) or not isinstance(packet['key'], str):
                    continue
                packet['summary'] = str(packet['summary'] or '[商城表情]')[:50]
                key = 'm' + hashlib.sha256(packet['emoji_id'].encode()).hexdigest()[:16]
                previous = self.items.get(key, {})
                item = {**previous,'id': key, 'kind': 'market_face', 'label':previous.get('label',packet['summary']) if previous.get('metadata_edited') else packet['summary'], 'data': packet, 'time': time.time()}
                item['package_name']=str(previous.get('package_name') if previous.get('metadata_edited') else data.get('emoji_package_name') or data.get('package_name') or previous.get('package_name') or '')[:80]
                item['emoji_name']=str((previous.get('emoji_name') or previous.get('label') or packet['summary']) if previous.get('metadata_edited') else data.get('emoji_name') or data.get('name') or previous.get('emoji_name') or packet['summary']).strip('[]')[:80]
                for field in ('source_message_id', 'source_group_id'):
                    if field in previous:
                        item[field] = previous[field]
                # Forward only an isolated expression from the authorized group.
                # Never replay a person's text, @ mentions, or quoted conversation.
                source = msg.get('message_id')
                if (len(segments) == 1 and msg.get('group_id') in authorized_groups()
                        and re.fullmatch(r'-?\d+', str(source or ''))):
                    item.update(source_message_id=str(source), source_group_id=msg.get('group_id'))
                self.items[key] = item
                self.prune()
                self.save()
            elif kind == 'image' and str(data.get('sub_type')) == '1':
                self.offer_candidate(data)

    @serialized
    def mark_used(self, key):
        key = self.aliases.get(key, key)
        if key in self.items:
            self.items[key]['last_used'] = time.time()
            self.items[key]['uses'] = self.items[key].get('uses', 0) + 1
            self.save()

    @serialized
    def mark_source_unavailable(self, source):
        for item in self.items.values():
            if item.get('kind') == 'market_face' and str(item.get('source_message_id')) == str(source):
                item['unavailable_source_id'] = str(source)
        self.save()

    @serialized
    def reject(self, key, category):
        item = self.items.pop(key, {})
        for field in ('path', 'preview'):
            if item.get(field):
                path = asset_path(item,field,self.root)
                if path.is_relative_to(self.root.resolve()) and path.is_file():
                    path.unlink()
        self.rejected[key] = {'asset_type': category, 'time': time.time()}
        self.rejected = dict(sorted(self.rejected.items(), key=lambda pair: pair[1]['time'], reverse=True)[:512])
        self.save()

    @serialized
    def apply_admission(self, entry, key):
        if not valid_classification(entry, key) or key not in self.items or self.items[key]['kind'] != 'sticker':
            return False
        item=self.items[key]
        if not accepts(entry, key, item.get('usage_evidence'), item.get('repeat_review_min_count', 5)):
            self.reject(key, 'uncertain' if entry['asset_type'] == 'sticker' else entry['asset_type'])
            return False
        self.items[key].update(asset_type='sticker', sticker_confidence=entry['confidence'], filter_version=FILTER_VERSION,
            admission_reason=str(entry.get('reason', ''))[:50])
        if entry.get('emotion') in EMOTIONS:
            self.items[key].update(emotion=entry['emotion'], description=str(entry.get('description', ''))[:35],
                use_when=str(entry.get('use_when', ''))[:30], classified_at=time.time())
        self.save()
        return True

    @serialized
    def learn_emotions(self, observations, choices, maximum=3):
        allowed = {item['id'] for item in choices if approved(item)}
        emotions = {'开心', '无语', '疑惑', '震惊', '难过', '赞同', '安慰', '打招呼', '撒娇', '调侃', '其他'}
        added = 0
        if not isinstance(observations, list):
            return 0
        for entry in observations[:min(8, maximum)]:
            if not isinstance(entry, dict):
                continue
            item = self.items.get(entry.get('id'))
            if (not item or item['id'] not in allowed or item.get('emotion')
                    or entry.get('emotion') not in emotions):
                continue
            description = str(entry.get('description', '')).strip()[:35]
            use_when = str(entry.get('use_when', '')).strip()[:30]
            if not description or not use_when:
                continue
            item.update(emotion=entry['emotion'], description=description, use_when=use_when,
                classified_at=time.time())
            added += 1
        if added:
            self.save()
        return added

    @serialized
    def received_ids(self, msg):
        keys = []
        for segment in msg.get('message', []) if isinstance(msg.get('message'), list) else []:
            data = segment.get('data', {})
            if segment.get('type') == 'mface' or (segment.get('type') == 'image' and data.get('emoji_id')):
                key = 'm' + hashlib.sha256(str(data.get('emoji_id', '')).encode()).hexdigest()[:16]
            elif segment.get('type') == 'image':
                key = sticker_source_key(data)
            else:
                continue
            key = self.aliases.get(key, key)
            if key in self.items and (self.items[key]['kind'] == 'market_face' or approved(self.items[key])):
                keys.append(key)
        return keys

    def offer_uploads(self):
        for manifest in (self.root/'incoming').glob('*.json'):
            try:
                data=json.loads(manifest.read_text(encoding='utf-8'))
                if data.get('next_retry',0)>time.time():continue
                path=(manifest.parent/str(data['path'])).resolve()
                if path.parent!=manifest.parent.resolve() or not path.is_file() or path.stat().st_size>2*1024*1024:continue
                digest=hashlib.sha256(path.read_bytes()).hexdigest()
                key=sticker_source_key({'file':digest+path.suffix})
                if key in self.queued or self.queue.full():continue
                claim=manifest.with_suffix('.claim')
                if claim.exists():
                    owner=self._read(claim)
                    if pid_alive(owner.get('pid')):continue
                    claim.unlink(missing_ok=True)
                try:
                    handle=os.open(claim,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
                except FileExistsError:continue
                with os.fdopen(handle,'w',encoding='utf-8') as output:json.dump({'pid':os.getpid()},output)
                self.offer_candidate({'file':digest+path.suffix,'summary':str(data.get('label',''))[:80],
                    '_local_path':str(path),'_upload_manifest':str(manifest),'_upload_claim':str(claim),'_upload_origin':self._upload_origin})
                if key not in self.queued:claim.unlink(missing_ok=True)
            except (OSError,ValueError,KeyError):continue

    async def collector(self, client):
        while True:
            key, data = await self.queue.get()
            reviewed=False
            local_upload=data.get('_upload_origin')==self._upload_origin
            try:
                if data.get('_local_path'):
                    if not local_upload:continue
                    path=Path(data['_local_path']).resolve()
                    if path.parent!=(self.root/'incoming').resolve() or not path.is_file() or path.stat().st_size>2*1024*1024:continue
                    raw=path.read_bytes()
                else:
                    url = str(data.get('url', ''))
                    try:
                        validate_source(url)
                    except ImageDownloadError:
                        continue
                    raw = bytearray()
                    async with client.stream('GET', url, follow_redirects=False) as response:
                        response.raise_for_status()
                        if response.is_redirect:continue
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 2 * 1024 * 1024:raise ValueError('Sticker too large')
                with Image.open(io.BytesIO(raw)) as image:
                    if image.width * image.height > 20000000 or image.format not in {'GIF', 'PNG', 'JPEG', 'WEBP'}:
                        continue
                    extension = image.format.lower()
                    preview = image.convert('RGB')
                    preview.thumbnail((192, 192))
                digest = hashlib.sha256(raw).hexdigest()
                with self.transaction():
                    existing = self.hashes.get(digest)
                    usage=self.usage_by_key.get(key,{})
                    reconsider=existing in self.rejected and self.rejected[existing].get('asset_type') in {'photo','uncertain','other'} and usage.get('seen_count',0)>=max(self.review_min_count,self.rejected[existing].get('review_seen_count',0)+self.review_step)
                    if existing and not reconsider:
                        self.aliases[key] = existing
                        if existing in self.items:
                            self.items[existing]['time'] = time.time()
                        self.save()
                        reviewed=True
                        continue
                canonical = 's' + digest[:16]
                # Temporary candidates are never exposed as sendable stickers.
                # Neither a QQ sub_type nor an emotion label proves admission.
                if self.classifier is None:
                    continue
                with tempfile.TemporaryDirectory(prefix='check-', dir=self.root) as folder:
                    candidate = Path(folder) / ('candidate.' + extension)
                    candidate.write_bytes(raw)
                    entry = await self.classifier(candidate, canonical)
                if not accepts(entry, canonical, usage, self.review_min_count):
                    if valid_classification(entry, canonical):
                        reviewed=True
                        with self.transaction():
                            if digest in self.hashes:
                                self.aliases[key] = self.hashes[digest]
                                if canonical in self.rejected:
                                    self.reject(canonical, 'uncertain' if entry['asset_type']=='sticker' else entry['asset_type'])
                                    self.rejected[canonical]['review_seen_count']=usage.get('seen_count',0)
                            else:
                                self.hashes[digest] = canonical
                                self.aliases[key] = canonical
                                self.reject(canonical, 'uncertain' if entry['asset_type'] == 'sticker' else entry['asset_type'])
                                self.rejected[canonical]['review_seen_count']=usage.get('seen_count',0)
                            self.save()
                    continue
                with self.transaction():
                    if self.rejected.get(canonical,{}).get('asset_type')=='manual':
                        reviewed=True;continue
                    existing = self.hashes.get(digest)
                    if existing and existing not in self.rejected:
                        self.aliases[key] = existing
                        self.save()
                        continue
                    path = self.root / (canonical + '.' + extension)
                    path.write_bytes(raw)
                    thumb = self.root / (canonical + '-preview.jpg')
                    preview.save(thumb, quality=75)
                    self.aliases[key] = canonical
                    self.hashes[digest] = canonical
                    self.rejected.pop(canonical,None)
                    self.items[canonical] = {'id': canonical, 'kind': 'sticker', 'content_hash': digest,
                        'label': str(data.get('summary') or '[群表情包]')[:40],
                        'path': path.name, 'preview': thumb.name, 'time': time.time(), 'asset_type': 'sticker',
                        'sticker_confidence': entry['confidence'], 'filter_version': FILTER_VERSION,
                        'admission_reason': str(entry.get('reason', ''))[:50],
                        'emotion': entry.get('emotion', '其他'), 'description': str(entry.get('description', ''))[:35],
                        'usage_evidence':dict(usage), 'repeat_review_min_count':self.review_min_count,
                        'use_when': str(entry.get('use_when', ''))[:30], 'classified_at': time.time()}
                    self.prune()
                    self.save()
                    reviewed=True
            except Exception:
                # Signed download URLs must not enter logs.
                pass
            finally:
                if local_upload and data.get('_upload_manifest'):
                    manifest=Path(data['_upload_manifest'])
                    if reviewed:
                        manifest.unlink(missing_ok=True);Path(data['_local_path']).unlink(missing_ok=True)
                    elif manifest.exists():
                        meta=self._read(manifest);meta['next_retry']=time.time()+self.upload_retry_seconds;self._write(manifest,meta)
                    Path(data['_upload_claim']).unlink(missing_ok=True)
                self.queued.discard(key)
                self.queue.task_done()

    @serialized
    def prune(self):
        ordered = sorted(self.items, key=lambda k: self.items[k]['time'], reverse=True)
        for key in ordered[256 if self.shared else 64:]:
            item = self.items.pop(key)
            for field in ('path', 'preview'):
                if item.get(field):
                    path = asset_path(item,field,self.root)
                    if path.is_relative_to(self.root.resolve()) and path.is_file():
                        path.unlink()

    @serialized
    def choices(self, text, received=()):
        received = [self.aliases.get(key, key) for key in received]
        query = grams(text)
        common = {'微笑', '呲牙', '龇牙', '狗头', '喵喵', '汪汪', '捂脸', '笑哭', '吃瓜', '斜眼笑'}
        request = re.sub(r'@\d+|\[引用回复\]', '', text).strip()
        face_request = (re.search(r'^(?:请|你)?(?:给我|帮我)?(?:发|来|用|回复|试|测试)', request)
            and re.search(r'(?:QQ(?:自带|内置|默认|原生)?|默认|内置|自带)表情', request, re.I)
            and not re.search(r'不|别|禁止', request))
        native = ([{'id': 'f' + face_id, 'kind': 'qq_face', 'label': label, 'face_id': face_id,
            'explicitly_requested': True} for face_id, label in self.faces.items()] if face_request else [])
        native.sort(key=lambda item: (len(query & grams(item['label'])), item['label'] in common), reverse=True)
        market = [item for item in self.items.values() if item['kind'] == 'market_face'
            and item.get('source_group_id') in authorized_groups() and item.get('source_message_id')]
        market.sort(key=lambda item: (len(query & grams(item['label'])), item['time']), reverse=True)
        pool = [{**item,'_asset_root':str(self.root.resolve())} for item in self.items.values() if approved(item)
            and asset_path(item,'path',self.root).is_file() and asset_path(item,'preview',self.root).is_file()]
        # Reserve two recent expressions; rotate the rest of the library, with
        # a penalty for recently sent assets. Older collected stickers stay useful.
        pool.sort(key=lambda item: item['time'], reverse=True)
        received_items = [item for item in pool if item['id'] in received][:2]
        for item in received_items:
            item['recently_received'] = True
        recent = received_items + [item for item in pool if item['id'] not in received
            and time.time() - item.get('last_used', 0) > 180][:2 - len(received_items)]
        candidates = [item for item in pool if item not in recent]
        rotated = []
        while candidates and len(rotated) < 6:
            weights = [(1 + min(4, len(query & grams(item.get('description', '') + item.get('use_when', '') + item.get('emotion', '')))))
                / (1 + item.get('uses', 0)) * (0.1 if time.time() - item.get('last_used', 0) < 180 else 1)
                for item in candidates]
            chosen = random.choices(candidates, weights=weights, k=1)[0]
            rotated.append(chosen)
            candidates.remove(chosen)
        stickers = recent + rotated
        for i, item in enumerate(stickers):
            item['visual_index'] = i + 1
            if item.get('description'):
                item['label'] = item['description']
        market.sort(key=lambda item: item['id'] in received, reverse=True)
        market = [{**item, 'recently_received': item['id'] in received} for item in market[:3]]
        return native[:6] + market + stickers

    @staticmethod
    def contact_sheet(choices, path):
        stickers = [item for item in choices if item['kind'] == 'sticker']
        if not stickers:
            return False
        sheet = Image.new('RGB', (384, 212 * ((len(stickers) + 1) // 2)), 'white')
        draw = ImageDraw.Draw(sheet)
        for i, item in enumerate(stickers):
            x, y = (i % 2) * 192, (i // 2) * 212
            with Image.open(asset_path(item,'preview')) as image:
                sheet.paste(image, (x, y + 20))
            draw.text((x + 4, y + 3), str(item['visual_index']), fill='black')
        sheet.save(path, quality=80)
        return True
