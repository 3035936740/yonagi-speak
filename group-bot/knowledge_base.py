"""Local document knowledge with scoped, indexed lexical retrieval; no model calls."""
from contextlib import contextmanager
from pathlib import Path
import json
import math
import re
import secrets
import sqlite3
import time
import unicodedata

MAX_CONTENT=200000


def terms(text):
    text=unicodedata.normalize('NFKC',str(text)).casefold()
    result=set(re.findall(r'[a-z0-9_]{2,40}',text))
    for word in re.findall(r'[\u3400-\u9fff]+',text):
        if len(word)==1:result.add(word)
        else:result.update(word[n:n+2] for n in range(len(word)-1))
    return result


def chunks(text):
    # Overlap preserves phrases crossing a boundary and caps individual excerpts.
    return [text[n:n+700] for n in range(0,len(text),600) if text[n:n+700].strip()]


class KnowledgeBase:
    def __init__(self,root):
        self.path=Path(root)/'runtime'/'knowledge.sqlite3'

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, scope INTEGER NOT NULL,
                    title TEXT NOT NULL, source TEXT NOT NULL, content TEXT NOT NULL,
                    enabled INTEGER NOT NULL, version TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, document_id TEXT NOT NULL
                    REFERENCES documents(id) ON DELETE CASCADE, position INTEGER, content TEXT);
                CREATE TABLE IF NOT EXISTS terms(chunk_id INTEGER REFERENCES chunks(id) ON DELETE CASCADE,
                    term TEXT, PRIMARY KEY(chunk_id,term));
                CREATE INDEX IF NOT EXISTS knowledge_terms ON terms(term,chunk_id);
            ''')
            yield db
        finally:db.close()

    def backup(self,db):
        folder=self.path.parent/'knowledge-backups';folder.mkdir(exist_ok=True)
        with sqlite3.connect(folder/(str(time.time_ns())+'.sqlite3')) as saved:db.backup(saved)

    def save(self,group,values,identity='',expected=None):
        if not isinstance(values,dict):raise ValueError('知识内容必须为对象')
        title=values.get('title','');content=values.get('content','');source=values.get('source','')
        scope=values.get('scope','group');enabled=values.get('enabled',True)
        if not isinstance(title,str) or not 1<=len(title.strip())<=150:raise ValueError('标题须为1～150字')
        if not isinstance(content,str) or not 1<=len(content.strip())<=MAX_CONTENT:raise ValueError('正文须为1～200000字')
        if '\x00' in content:raise ValueError('仅支持文本内容，请导入TXT或Markdown')
        if not isinstance(source,str) or len(source)>200:raise ValueError('来源最多200字')
        if scope not in {'group','shared'} or type(enabled) is not bool:raise ValueError('未知知识范围或启用状态')
        with self.connect() as db:
            self.backup(db)
            with db:
                db.execute('BEGIN IMMEDIATE')
                if identity:
                    old=db.execute('SELECT * FROM documents WHERE id=? AND scope IN (0,?)',(identity,group)).fetchone()
                    if not old:raise ValueError('知识不存在或不属于当前群')
                    if expected!=old['version']:raise ValueError('知识已被修改，请刷新后重试')
                    db.execute('DELETE FROM chunks WHERE document_id=?',(identity,))
                else:identity=secrets.token_hex(12)
                version=str(time.time_ns())
                db.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?)',
                    (identity,0 if scope=='shared' else group,title.strip(),source.strip(),content.strip(),int(enabled),version,time.time()))
                for position,part in enumerate(chunks(content.strip())):
                    cursor=db.execute('INSERT INTO chunks(document_id,position,content) VALUES (?,?,?)',(identity,position,part))
                    db.executemany('INSERT INTO terms VALUES (?,?)',[(cursor.lastrowid,term) for term in terms(title+' '+part)])
        return {'id':identity,'version':version}

    def delete(self,group,identity,expected):
        with self.connect() as db:
            self.backup(db)
            with db:
                cursor=db.execute('DELETE FROM documents WHERE id=? AND scope IN (0,?) AND version=?',(identity,group,expected))
                if not cursor.rowcount:raise ValueError('知识已被修改或删除，请刷新后重试')

    def view(self,group,query='',offset=0,scope='all',identity=''):
        if not self.path.exists():return {'rows':[],'total':0,'offset':0,'limit':20}
        if scope not in {'all','group','shared'}:raise ValueError('未知知识范围')
        offset=max(0,int(offset));query=str(query)[:200]
        with self.connect() as db:
            if identity:
                row=db.execute('SELECT * FROM documents WHERE id=? AND scope IN (0,?)',(identity,group)).fetchone()
                if not row:raise ValueError('知识不存在或不属于当前群')
                return self.public(dict(row),group)
            condition='scope IN (0,?)';args=[group]
            if scope!='all':condition+=' AND scope=?';args.append(0 if scope=='shared' else group)
            condition+=' AND (instr(lower(title),lower(?)) OR instr(lower(content),lower(?)))';args.extend([query,query])
            total=db.execute('SELECT count(*) FROM documents WHERE '+condition,args).fetchone()[0]
            rows=db.execute('SELECT id,scope,title,source,enabled,version,updated,length(content) AS chars,substr(content,1,180) AS preview FROM documents WHERE '+condition+' ORDER BY updated DESC,id LIMIT 20 OFFSET ?',args+[offset]).fetchall()
            return {'rows':[self.public(dict(row),group) for row in rows],'total':total,'offset':offset,'limit':20}

    @staticmethod
    def public(row,group):
        row['scope']='shared' if row['scope']==0 else 'group';row['enabled']=bool(row['enabled'])
        return row

    def retrieve(self,group,query,budget=1800):
        if not self.path.exists():return []
        tokens=sorted(terms(query))[:64]
        if not tokens:return []
        with self.connect() as db:
            placeholders=','.join('?' for _ in tokens)
            rows=db.execute('''SELECT c.id,c.position,c.content,d.id AS document_id,d.title,d.source,d.scope,
                count(*) AS hits FROM terms t JOIN chunks c ON c.id=t.chunk_id
                JOIN documents d ON d.id=c.document_id WHERE t.term IN ('''+placeholders+''')
                AND d.enabled=1 AND d.scope IN (0,?) GROUP BY c.id ORDER BY hits DESC,c.position,c.id DESC LIMIT 40''',tokens+[group]).fetchall()
        ranked=sorted(rows,key=lambda row:row['hits']/math.sqrt(max(1,len(terms(row['content'])))),reverse=True)
        result=[];used=set();remaining=max(0,int(budget)-2)
        for row in ranked:
            # Avoid spending the budget on adjacent, heavily overlapping excerpts.
            key=(row['document_id'],row['position'])
            if any((key[0],key[1]+delta) in used for delta in (-1,0,1)):continue
            reference={'id':row['document_id'],'title':row['title'],'source':row['source'],
                'scope':'shared' if row['scope']==0 else 'group','content':row['content']}
            overhead=len(json.dumps({**reference,'content':''},ensure_ascii=False,separators=(',',':')))+1
            if remaining-overhead<80:continue
            reference['content']=reference['content'][:remaining-overhead]
            while reference['content'] and len(json.dumps(reference,ensure_ascii=False,separators=(',',':')))+1>remaining:
                reference['content']=reference['content'][:max(0,len(reference['content'])-40)]
            if not reference['content']:continue
            result.append(reference);used.add(key)
            remaining-=len(json.dumps(reference,ensure_ascii=False,separators=(',',':')))+1
            if len(result)>=4:break
        return result


def checked(root,group):
    from console_config import Config
    group=int(group)
    if group not in Config(root).load()['chat_group_whitelist']:raise ValueError('该群不在白名单内')
    return KnowledgeBase(root),group
