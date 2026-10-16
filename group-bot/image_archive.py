"""Original incoming images and operator corrections, separate from AI temp files."""
from contextlib import contextmanager
import hashlib
import io
import json
from platform_runtime import msvcrt
from pathlib import Path
import re
import time

from PIL import Image
from emoji_assets import write_bytes
from storage_io import atomic_write_text


def read(path):
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError): return {}


def hash_id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('无效图片哈希')
    return value


def message_image_keys(group, message_id, count):
    """Stable message/slot references while a sent image has no download hash yet."""
    if not isinstance(count,int) or isinstance(count,bool) or not 0<=count<=2:return []
    return [hashlib.sha256(f'qq-image:{int(group)}:{message_id}:{index}'.encode()).hexdigest() for index in range(count)]


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.seek(0, 2) == 0: stream.write(b'0'); stream.flush()
        deadline = time.monotonic() + 5
        while True:
            try: stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1); break
            except OSError:
                if time.monotonic() >= deadline: raise ValueError('图片资料正在更新，请重试')
                time.sleep(.02)
        try: yield
        finally: stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


class ImageArchive:
    def __init__(self, runtime):
        self.root = Path(runtime) / 'image-originals'

    def put(self, raw, key):
        hash_id(key)
        if not raw or len(raw) > 8 * 1024 * 1024: raise ValueError('群图片原图上限8MB')
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {'GIF', 'PNG', 'JPEG', 'WEBP'} or image.width * image.height > 20000000:
                raise ValueError('不支持的图片格式或尺寸')
            extension = image.format.lower(); image.verify()
        digest = hashlib.sha256(raw).hexdigest()
        with locked(self.root / 'archive.lock'):
            path = self.root / (digest + '.' + extension)
            if not path.is_file(): write_bytes(path, raw)
            sources = read(self.root / 'sources.json')
            sources[key] = digest
            atomic_write_text(self.root / 'sources.json', json.dumps(sources))
        return digest

    def digest(self, key, fallback=None):
        hash_id(key)
        return read(self.root / 'sources.json').get(key) or fallback

    def get(self, digest):
        hash_id(digest)
        for suffix in ('gif', 'png', 'jpeg', 'webp'):
            path = self.root / (digest + '.' + suffix)
            if path.is_file():
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != digest: raise ValueError('原图哈希校验失败')
                return path
        return None

    def prune(self, keys):
        if not self.root.exists(): return
        with locked(self.root / 'archive.lock'):
            sources = read(self.root / 'sources.json')
            # Keep recent writes while recognition tasks are binding their hashes.
            kept = {key: value for key, value in sources.items() if key in keys}
            referenced = set(kept.values())
            for path in self.root.iterdir():
                if re.fullmatch(r'[a-f0-9]{64}\.(?:gif|png|jpeg|webp)', path.name):
                    if path.stem not in referenced and time.time() - path.stat().st_mtime > 300:
                        path.unlink(missing_ok=True)
                    elif path.stem not in referenced:
                        kept.update({key: value for key, value in sources.items() if value == path.stem})
            atomic_write_text(self.root / 'sources.json', json.dumps(kept))


class SummaryOverrides:
    def __init__(self, runtime):
        self.path = Path(runtime) / 'image-summary-edits.json'

    def get(self, key=None, digest=None):
        rows = read(self.path)
        return rows.get(digest) or rows.get(key)

    def set(self, key, digest, summary, expected=None):
        from role_analysis import checked
        hash_id(key)
        if digest: hash_id(digest)
        if not checked('vision', summary, set()) or not (summary['description'].strip() or summary['visible_text'].strip()):
            raise ValueError('摘要需填写图片内容或可见文字，描述≤120字、文字≤140字、不确定说明≤70字')
        with locked(self.path.with_suffix('.lock')):
            data = read(self.path); old = data.get(digest) or data.get(key)
            if expected is not None and expected != (old or {}).get('revision', ''):
                raise ValueError('摘要已被其他页面修改，请刷新后重试')
            backup = self.path.parent / 'image-edit-backups'; backup.mkdir(exist_ok=True)
            revision = str(time.time_ns())
            atomic_write_text(backup / (revision + '.json'), json.dumps({'key': key, 'digest': digest, 'previous': old}, ensure_ascii=False))
            entry = {'summary': dict(summary), 'revision': revision, 'edited_at': time.time(), 'origin': 'operator'}
            data[key] = entry
            if digest: data[digest] = entry
            atomic_write_text(self.path, json.dumps(data, ensure_ascii=False))
        return entry


def replace_summaries(text, summaries):
    values = iter(summaries)
    def replace(match):
        summary = next(values, None)
        if not summary: return match.group(0)
        detail = (summary.get('description') or summary.get('visible_text') or '').replace(']', '］')
        return '[图片摘要：' + detail + ']'
    return re.sub(r'\[图片(?:，(?:未读取内容|读取中|读取失败)|摘要[:：][^\]]*)\]', replace, text)
