"""Own sticker bytes in the library, independently of old group runtime files."""
import hashlib
import io
from pathlib import Path
import re
import tempfile
from PIL import Image
from storage_io import replace_with_retry


def asset_path(item, field='path', root=None):
    base = Path(root or item.get('_asset_root') or Path(__file__).with_name('shared-emoji-library'))
    path = Path(item.get(field) or '__missing_asset__')
    return (path if path.is_absolute() else base/path).resolve()


def managed_asset_path(item, field, root):
    """Only library assets and explicit legacy runtime/emoji-library layouts."""
    root = Path(root).resolve()
    raw = Path(item.get(field) or '__missing_asset__')
    path = asset_path(item, field, root)
    def allowed(candidate):
        if candidate.is_relative_to(root):return True
        parent = candidate.parent
        return (parent.name == 'emoji-library' and re.fullmatch(r'runtime(?:-\d+)?', parent.parent.name)
            and parent.parent.parent == root.parent)
    if allowed(path) and path.is_file():return path
    # Old absolute paths can be rebased after moving the complete project.
    if raw.is_absolute() and len(raw.parts) >= 3:
        if raw.parent.name == root.name:
            candidate = (root/raw.name).resolve()
        elif raw.parent.name == 'emoji-library' and re.fullmatch(r'runtime(?:-\d+)?', raw.parent.parent.name):
            candidate = (root.parent/raw.parent.parent.name/'emoji-library'/raw.name).resolve()
        else:raise ValueError('图片路径不在表情库内')
        if allowed(candidate) and candidate.is_file():return candidate
    if not allowed(path):raise ValueError('图片路径不在表情库内')
    return path


def write_bytes(path, raw):
    path = Path(path)
    with tempfile.NamedTemporaryFile(prefix=path.name+'.',suffix='.tmp',dir=path.parent,delete=False) as stream:
        stream.write(raw)
        temporary = Path(stream.name)
    try:replace_with_retry(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def own_asset(root, key, source, expected_hash=None):
    """Copy original animation bytes, generate a thumbnail, return relative paths."""
    if not re.fullmatch(r's[a-f0-9]{16}', key):raise ValueError('无效图片ID')
    root = Path(root).resolve(); source = Path(source)
    if not source.is_file() or source.stat().st_size > 2*1024*1024:raise ValueError('图片文件不可用')
    raw = source.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
    if expected_hash and expected_hash != digest:raise ValueError('图片哈希与索引不一致')
    with Image.open(io.BytesIO(raw)) as image:
        if image.format not in {'GIF','PNG','JPEG','WEBP'} or image.width*image.height > 20000000:
            raise ValueError('不支持的图片')
        suffix = image.format.lower()
        preview = image.convert('RGB');preview.thumbnail((192,192))
    path = root/(key+'.'+suffix);thumb = root/(key+'-preview.jpg')
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:write_bytes(path,raw)
    try:
        with Image.open(thumb) as image:image.verify()
    except (OSError,ValueError):
        output = io.BytesIO();preview.save(output,format='JPEG',quality=75);write_bytes(thumb,output.getvalue())
    return {'path':path.name,'preview':thumb.name,'content_hash':digest}
