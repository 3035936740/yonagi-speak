"""Message-bound image inspection and operator changes for the local WebUI."""
import io
import hashlib
import json
from pathlib import Path
import re

import httpx
from dotenv import dotenv_values
from PIL import Image
from console_config import Config, runtime, read_json, deleted_ids
from debug_store import library, approve_image
from emoji_assets import managed_asset_path
from image_archive import ImageArchive, SummaryOverrides, hash_id, message_image_keys
from image_memory import ImageMemory, source_key
from image_sources import validate_source, failure_details
from runtime_paths import project_path


def selection(root, group, message_id, key):
    root = Path(root); cfg = Config(root, int(group)).load()
    if not re.fullmatch(r'-?\d+', str(message_id)): raise ValueError('无效消息ID')
    hash_id(key)
    folder = runtime(root, group, cfg)
    if str(message_id) in deleted_ids(folder): raise ValueError('消息已在本地删除')
    memory = ImageMemory(folder / 'image-memory.json'); memory.configure(cfg)
    keys = memory.data['messages'].get(str(message_id), [])
    for name in ('recent-chat.jsonl', 'debug-events.jsonl'):
        if keys: break
        path = folder / name
        if not path.exists(): continue
        for line in path.read_text(encoding='utf-8').splitlines():
            try: row = json.loads(line)
            except ValueError: continue
            if str(row.get('_id') or row.get('message_id') or '') == str(message_id):
                keys = row.get('_image_hashes') or row.get('image_hashes') or []
                if not keys:keys=message_image_keys(group,message_id,row.get('_image_count',row.get('image_count',0)))
                if keys: break
    if key not in keys[:2]: raise ValueError('未找到这条消息的指定图片，不能用其他图片代替')
    return cfg, folder, memory, list(keys[:2]).index(key)


def original(root, group, message_id, key):
    cfg, folder, memory, index = selection(root, group, message_id, key)
    archive = ImageArchive(folder)
    digest = archive.digest(key, memory.data['sources'].get(key, {}).get('digest'))
    if digest and (path := archive.get(digest)): return path
    lib = library(root)
    entry = lib.items.get(lib.hashes.get(digest), {})
    if entry:
        path = managed_asset_path(entry, 'path', lib.root)
        if path.is_file():
            archive.put(path.read_bytes(), key)
            return path
    env = dotenv_values(project_path(cfg['mcp_env_file'], root))
    if not env.get('NAPCAT_HOST'): raise ValueError('原图未缓存，NapCat连接配置不可用')
    try:
        with httpx.Client(trust_env=False, timeout=15) as client:
            response = client.post(env['NAPCAT_HOST'].rstrip('/') + '/get_msg',
                headers={'Authorization': 'Bearer ' + env.get('NAPCAT_TOKEN', '')}, json={'message_id': str(message_id)})
            response.raise_for_status(); result = response.json()
            if result.get('retcode') != 0: raise ValueError('QQ原消息不可读取，原图尚未缓存')
            message = result.get('data') or {}
            if str(message.get('group_id')) != str(group): raise ValueError('QQ返回的消息不属于当前群')
            segments = message.get('message')
            if not isinstance(segments, list): raise ValueError('原消息没有可读取的图片组件')
            images = [row.get('data', {}) for row in segments if row.get('type') == 'image']
            matches = [data for data in images if source_key(data) == key]
            if matches:
                selected_image = matches[0]
            elif (digest or key in message_image_keys(group,message_id,2)) and index < len(images):
                # New outgoing images use the verified message's exact slot.
                # Legacy signed-URL refreshes must match the known digest below.
                selected_image = images[index]
            else:
                raise ValueError('原消息中未找到能确认匹配的指定图片')
            url = selected_image.get('url', ''); validate_source(url)
            data = bytearray()
            with client.stream('GET', url, follow_redirects=False) as download:
                if download.is_redirect: raise ValueError('原图地址发生跳转，未读取')
                download.raise_for_status()
                for chunk in download.iter_bytes():
                    data.extend(chunk)
                    if len(data) > min(cfg.get('max_image_bytes', 8388608), 8388608): raise ValueError('原图超过8MB读取上限')
            fresh = hashlib.sha256(data).hexdigest()
            if digest and fresh != digest: raise ValueError('原图与已识别内容哈希不一致，未替换原图')
            archive.put(bytes(data), key)
            return archive.get(fresh)
    except httpx.HTTPError as error:
        raise ValueError('原图读取失败：' + failure_details(error, locals().get('url', ''))) from None


def image_bytes(root, group, message_id, key):
    path = original(root, group, message_id, key)
    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as image:
        mime = Image.MIME.get(image.format, 'application/octet-stream'); image.verify()
    return raw, mime


def mutate(root, group, mode, message_id, key, values=None):
    cfg, folder, memory, index = selection(root, group, message_id, key)
    values = values or {}
    digest = ImageArchive(folder).digest(key, memory.data['sources'].get(key, {}).get('digest'))
    if mode == 'summary':
        summary = values.get('summary')
        entry = SummaryOverrides(folder).set(key, digest, summary, values.get('expected_revision'))
        return {'ok': True, 'message': '图片摘要已保存，后续自然回复使用人工摘要', 'revision': entry['revision']}
    if mode == 'collect':
        path = original(root, group, message_id, key)
        return approve_image(library(root), path, values)
    raise ValueError('未知图片管理操作')
