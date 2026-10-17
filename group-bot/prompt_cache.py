"""Stable transport schemas; retain dynamic identifier checks locally."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from storage_io import atomic_write_text

IDENTIFIERS={'reply_to','media_id','media_ids','at_message_ids','qq_id','evidence_message_ids','id','source_ids','expression_ids','memory_ids'}


def stable_schema(spec):
    result=deepcopy(spec)
    def visit(value,field=''):
        if not isinstance(value,dict):return
        if field in IDENTIFIERS:
            value.pop('const',None);value.pop('enum',None)
        for name,child in value.get('properties',{}).items():visit(child,name)
        if isinstance(value.get('items'),dict):visit(value['items'],field)
    visit(result);return result


def check_constraints(spec,value):
    """Provider validates the stable schema; application rechecks original enums/consts."""
    if not isinstance(spec,dict):return
    if 'const' in spec and value!=spec['const']:raise ValueError('Model output failed local const constraint')
    if 'enum' in spec and value not in spec['enum']:raise ValueError('Model output failed local enum constraint')
    if isinstance(value,dict):
        for name,child in spec.get('properties',{}).items():
            if name in value:check_constraints(child,value[name])
    if isinstance(value,list) and isinstance(spec.get('items'),dict):
        for item in value:check_constraints(spec['items'],item)


def transport_schema(source,runtime):
    spec=json.loads(Path(source).read_text(encoding='utf-8'));stable=stable_schema(spec)
    raw=json.dumps(stable,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    folder=Path(runtime)/'prompt-cache-schemas';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(hashlib.sha256(raw.encode()).hexdigest()+'.json')
    if not path.exists():atomic_write_text(path,raw)
    return path,spec
