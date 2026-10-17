"""Validated local operator attachments, ordered OneBot segments and durable originals."""
import base64
import hashlib
import io
from pathlib import Path
import re
from PIL import Image
from emoji_assets import write_bytes
from image_archive import ImageArchive,message_image_keys

MAX_IMAGES=2
MAX_IMAGE_BYTES=5*1024*1024
MAX_TOTAL_BYTES=8*1024*1024
MAX_REQUEST_BYTES=12*1024*1024
MARKER=re.compile(r'\[图片(\d{1,3})?\]')


def decode_images(images):
    if images is None:return []
    if not isinstance(images,list) or len(images)>MAX_IMAGES:raise ValueError('最多附加2张图片')
    result=[];slots=set();total=0
    for entry in images:
        if not isinstance(entry,dict):raise ValueError('图片附件格式错误')
        slot=entry.get('slot');encoded=entry.get('data')
        if type(slot) is not int or not 1<=slot<=999 or slot in slots:raise ValueError('图片编号无效或重复')
        if not isinstance(encoded,str) or len(encoded)>((MAX_IMAGE_BYTES+2)//3)*4:raise ValueError('单张图片上限5MB')
        try:raw=base64.b64decode(encoded,validate=True)
        except ValueError:raise ValueError('图片编码无效')
        if not raw or len(raw)>MAX_IMAGE_BYTES:raise ValueError('单张图片上限5MB')
        total+=len(raw)
        if total>MAX_TOTAL_BYTES:raise ValueError('附件合计上限8MB')
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {'PNG','JPEG','GIF','WEBP'} or image.width*image.height>20000000:raise ValueError('图片格式或尺寸不支持')
                extension=image.format.lower();image.verify()
        except (OSError,Image.DecompressionBombError) as error:raise ValueError('图片文件损坏或尺寸过大') from error
        digest=hashlib.sha256(raw).hexdigest();slots.add(slot)
        result.append({'slot':slot,'digest':digest,'size':len(raw),'file':str(slot)+'-'+digest+'.'+extension,'raw':raw})
    return result


def plan(text,images):
    by_slot={image['slot']:image for image in images};used=set();result=[];cursor=0
    for match in MARKER.finditer(text):
        slot=int(match.group(1)) if match.group(1) else next((slot for slot in by_slot if slot not in used),None)
        if slot not in by_slot:continue
        if slot in used:raise ValueError('同一张图片的占位符只能使用一次')
        if match.start()>cursor:result.append(('text',text[cursor:match.start()]))
        result.append(('image',by_slot[slot]));used.add(slot);cursor=match.end()
    if cursor<len(text):result.append(('text',text[cursor:]))
    result.extend(('image',image) for image in images if image['slot'] not in used)
    return result


def save_images(folder,identity,images):
    directory=Path(folder)/'assets'/identity
    directory.mkdir(parents=True,exist_ok=True)
    for image in images:write_bytes(directory/image['file'],image['raw'])
    return [{key:value for key,value in image.items() if key!='raw'} for image in images]


def read_image(folder,row,image):
    name=image.get('file','')
    if not re.fullmatch(r'[1-9]\d{0,2}-[a-f0-9]{64}\.(?:png|jpeg|gif|webp)',str(name)):raise ValueError('无效附件路径')
    directory=(Path(folder)/'assets'/row['id']).resolve();path=(directory/name).resolve()
    if path.parent!=directory:raise ValueError('无效附件路径')
    if not 0<path.stat().st_size<=MAX_IMAGE_BYTES:raise ValueError('附件大小异常')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=image.get('digest'):raise ValueError('图片附件已变化')
    return raw


def segments(folder,row):
    images=row.get('images',[])
    if len(images)>MAX_IMAGES:raise ValueError('最多附加2张图片')
    result=[]
    for kind,value in plan(row['text'],images):
        result.append({'type':kind,'data':{'text':value} if kind=='text' else {'file':'base64://'+base64.b64encode(read_image(folder,row,value)).decode('ascii')}})
    return result


def archive_images(folder,row):
    images=[value for kind,value in plan(row['text'],row.get('images',[])) if kind=='image']
    archive=ImageArchive(Path(folder).parent);keys=message_image_keys(row['group_id'],row['message_id'],len(images))
    for key,image in zip(keys,images):archive.put(read_image(folder,row,image),key)
    return keys


def display_text(row):
    return ''.join(value if kind=='text' else '[图片]' for kind,value in plan(row['text'],row.get('images',[])))


def remove_upload_copies(folder,row):
    directory=(Path(folder)/'assets'/row['id']).resolve()
    for image in row.get('images',[]):
        name=str(image.get('file',''))
        if re.fullmatch(r'[1-9]\d{0,2}-[a-f0-9]{64}\.(?:png|jpeg|gif|webp)',name):
            path=(directory/name).resolve()
            if path.parent==directory:path.unlink(missing_ok=True)
    try:directory.rmdir()
    except OSError:pass
