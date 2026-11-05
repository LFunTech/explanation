"""本地 PostgreSQL 知识库：来源分层、可恢复向量化、元数据约束与余弦检索。"""
import hashlib
import io
import json
import math
import re
from contextlib import contextmanager
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .providers import GenerationError
from .store import digest
from .scope import scope_matches


DDL = '''
CREATE TABLE IF NOT EXISTS kb_nodes (
    id TEXT PRIMARY KEY, source_schema TEXT NOT NULL, source_id TEXT NOT NULL,
    subject_code TEXT NOT NULL, subject TEXT NOT NULL, phase TEXT NOT NULL,
    grade_code TEXT, parent_id TEXT, code TEXT NOT NULL, name TEXT NOT NULL,
    metadata JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS kb_nodes_scope ON kb_nodes(subject,phase);
CREATE TABLE IF NOT EXISTS kb_edges (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
    relation_type TEXT NOT NULL, metadata JSONB NOT NULL);
CREATE INDEX IF NOT EXISTS kb_edges_source ON kb_edges(source_id);
CREATE INDEX IF NOT EXISTS kb_edges_target ON kb_edges(target_id);
CREATE TABLE IF NOT EXISTS kb_documents (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, source_type TEXT NOT NULL,
    metadata JSONB NOT NULL, content_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS kb_chunks (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES kb_documents(id),
    content TEXT NOT NULL, content_hash TEXT NOT NULL,
    subject TEXT NOT NULL, phase TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '全国',
    grades TEXT[] NOT NULL DEFAULT '{}', knowledge_ids TEXT[] NOT NULL DEFAULT '{}',
    labels TEXT[] NOT NULL DEFAULT '{}', source_type TEXT NOT NULL, metadata JSONB NOT NULL,
    embedding REAL[], embedding_model TEXT, embedding_dimensions INTEGER,
    embedding_error TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS kb_chunks_scope ON kb_chunks(subject,region,source_type);
CREATE INDEX IF NOT EXISTS kb_chunks_grades ON kb_chunks USING gin(grades);
CREATE INDEX IF NOT EXISTS kb_chunks_knowledge ON kb_chunks USING gin(knowledge_ids);
CREATE OR REPLACE FUNCTION kb_cosine(a real[], b real[]) RETURNS double precision
LANGUAGE SQL IMMUTABLE STRICT PARALLEL SAFE AS $$
SELECT sum(x::double precision*y)/NULLIF(sqrt(sum(x::double precision*x))*sqrt(sum(y::double precision*y)),0)
FROM unnest(a,b) AS t(x,y)
WHERE cardinality(a)=cardinality(b)
$$;
'''


def phase_for(grade, region='上海', school_system=''):
    if not grade:return ''
    if grade in {'高一','高二','高三','十年级','十一年级','十二年级'}:
        return 'senior'
    if grade in {'七年级','八年级','九年级','初一','初二','初三','预备年级'} or (grade=='六年级' and (school_system=='五四制' or (not school_system and region=='上海'))):
        return 'junior'
    return 'primary'


class KnowledgeBase:
    def __init__(self, settings):
        self.cfg = settings

    @contextmanager
    def connect(self):
        if not self.cfg.vector_database_url:
            raise GenerationError('knowledge_not_configured', '尚未配置本地 PostgreSQL 知识库。')
        with psycopg.connect(self.cfg.vector_database_url, connect_timeout=8, row_factory=dict_row,
                             options='-c statement_timeout=30000') as db:
            yield db

    def initialize(self):
        with self.connect() as db:
            db.execute(DDL)

    def stats(self):
        with self.connect() as db:
            result = db.execute('SELECT count(*) AS chunks,count(embedding) AS embedded,count(*) FILTER(WHERE embedding_error IS NOT NULL) AS errors FROM kb_chunks').fetchone()
            result['nodes'] = db.execute('SELECT count(*) AS n FROM kb_nodes').fetchone()['n']
            result['edges'] = db.execute('SELECT count(*) AS n FROM kb_edges').fetchone()['n']
            result['documents'] = db.execute('SELECT count(*) AS n FROM kb_documents').fetchone()['n']
            result['by_source'] = db.execute('SELECT source_type,count(*) AS chunks,count(embedding) AS embedded FROM kb_chunks GROUP BY source_type ORDER BY source_type').fetchall()
            result['by_subject'] = db.execute('SELECT subject,count(*) AS nodes FROM kb_nodes GROUP BY subject ORDER BY subject').fetchall()
            result['engine'] = 'PostgreSQL · 精确余弦检索'
            return result

    def put_document(self, document_id, title, source_type, metadata, chunks):
        with self.connect() as db:
            db.execute('''INSERT INTO kb_documents(id,title,source_type,metadata,content_hash) VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,metadata=excluded.metadata,content_hash=excluded.content_hash''',
                (document_id,title,source_type,Jsonb(metadata),digest(chunks)))
            ids = []
            for index, chunk in enumerate(chunks):
                scoped={**metadata,**chunk.get('metadata',{})}
                key = f'{document_id}:{index}'
                ids.append(key)
                content_hash = digest(chunk)
                db.execute('''INSERT INTO kb_chunks(id,document_id,content,content_hash,subject,phase,region,grades,knowledge_ids,labels,source_type,metadata)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(id) DO UPDATE SET content=excluded.content,content_hash=excluded.content_hash,
                    subject=excluded.subject,phase=excluded.phase,region=excluded.region,grades=excluded.grades,
                    knowledge_ids=excluded.knowledge_ids,labels=excluded.labels,metadata=excluded.metadata,
                    embedding=CASE WHEN kb_chunks.content_hash=excluded.content_hash THEN kb_chunks.embedding ELSE NULL END,
                    embedding_error=NULL,updated_at=now()''',
                    (key,document_id,chunk['content'],content_hash,scoped['subject'],scoped.get('phase',''),
                     scoped.get('region','全国'),scoped.get('grades',[]),
                     [scoped['knowledge_id']] if scoped.get('knowledge_id') else scoped.get('knowledge_ids',[]),scoped.get('labels',[]),
                     source_type,Jsonb({**scoped,'title':title})))
            db.execute('DELETE FROM kb_chunks WHERE document_id=%s AND NOT(id=ANY(%s))',(document_id,ids))

    async def embeddings(self, texts):
        cfg = self.cfg
        if not cfg.dashscope_api_key:
            raise GenerationError('qwen_not_configured', '向量化需要配置千问 API Key。')
        async with httpx.AsyncClient(timeout=cfg.qwen_timeout,trust_env=False) as client:
            result = await client.post(cfg.qwen_base_url.rstrip('/')+'/embeddings',
                headers={'Authorization':'Bearer '+cfg.dashscope_api_key},
                json=dict(model=cfg.embedding_model,input=texts,dimensions=cfg.embedding_dimensions,encoding_format='float'))
        if result.status_code != 200:
            raise GenerationError('embedding_http',f'千问向量接口返回 HTTP {result.status_code}')
        try:
            data = sorted(result.json()['data'],key=lambda x:x['index'])
            if [x['index'] for x in data] != list(range(len(texts))):
                raise ValueError('embedding response index mismatch')
            vectors = [x['embedding'] for x in data]
            if any(len(v)!=cfg.embedding_dimensions or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in v) or sum(x*x for x in v)==0 for v in vectors):
                raise ValueError('invalid embedding')
            return vectors
        except (KeyError,TypeError,ValueError) as error:
            raise GenerationError('embedding_invalid','向量数量、维度或数值不符合约定，未写入索引。') from error

    async def index_pending(self, limit=10000, progress=None):
        # 跨 CLI / Web 进程互斥，避免重复调用计费接口；连接退出自动释放。
        with self.connect() as guard:
            if not guard.execute('SELECT pg_try_advisory_lock(187651024) AS locked').fetchone()['locked']:
                raise GenerationError('index_running','已有向量化任务正在运行，请等待完成。')
            try:
                return await self._index_pending(limit,progress)
            finally:
                guard.execute('SELECT pg_advisory_unlock(187651024)')

    async def _index_pending(self, limit, progress):
        done = 0
        while done < limit:
            with self.connect() as db:
                rows = db.execute('''SELECT id,content,content_hash,subject,labels FROM kb_chunks
                    WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM %s OR embedding_dimensions IS DISTINCT FROM %s
                    ORDER BY CASE WHEN source_type='graph' THEN 1 ELSE 0 END,id LIMIT %s''',
                    (self.cfg.embedding_model,self.cfg.embedding_dimensions,min(40,limit-done))).fetchall()
            if not rows:
                break
            try:
                import asyncio
                groups=[rows[i:i+10] for i in range(0,len(rows),10)]
                batches=await asyncio.gather(*[self.embeddings([r['subject']+'；'+'、'.join(r['labels'])+'\n'+r['content'] for r in group]) for group in groups])
                vectors=[vector for batch in batches for vector in batch]
            except Exception as error:
                with self.connect() as db:
                    db.execute('UPDATE kb_chunks SET embedding_error=%s WHERE id=ANY(%s)',
                               (getattr(error,'code','embedding_connection'),[r['id'] for r in rows]))
                raise
            with self.connect() as db:
                for row,vector in zip(rows,vectors):
                    db.execute('''UPDATE kb_chunks SET embedding=%s,embedding_model=%s,embedding_dimensions=%s,embedding_error=NULL
                        WHERE id=%s AND content_hash=%s''',
                        (vector,self.cfg.embedding_model,self.cfg.embedding_dimensions,row['id'],row['content_hash']))
            done += len(rows)
            if progress:
                progress(done)
        return done

    async def search(self, query, subject, grade, region='上海', limit=8, include_graph=False, scope=None,grades=None):
        scope=scope or {}
        from .grades import normalize_grades
        selected=normalize_grades(grades if grades is not None else [grade])
        phases=list(dict.fromkeys(['',*[phase_for(g,region,scope.get('school_system','')) for g in selected]]))
        vector = (await self.embeddings([subject+'；'+query]))[0]
        with self.connect() as db:
            rows = db.execute('''SELECT id,content,source_type,grades,labels,metadata,embedding_model,embedding_dimensions,
                    kb_cosine(embedding,%s::real[]) AS score,
                    CASE WHEN grades && %s::text[] THEN 'grade' ELSE 'phase' END AS match_scope
                FROM kb_chunks WHERE subject=%s AND region IN (%s,'全国')
                    AND (grades && %s::text[] OR (cardinality(grades)=0 AND phase=ANY(%s::text[])))
                    AND (%s OR source_type<>'graph') AND embedding IS NOT NULL
                    AND (source_type<>'curriculum' OR %s='' OR COALESCE(NULLIF(metadata->>'curriculum_version',''),metadata->>'version')=%s)
                    AND (source_type<>'textbook' OR (%s<>'' AND %s<>'' AND %s<>'' AND %s<>''
                        AND metadata->>'school_system'=%s AND metadata->>'publisher'=%s
                        AND metadata->>'textbook_version'=%s AND metadata->>'volume'=%s))
                    AND embedding_model=%s AND embedding_dimensions=%s
                ORDER BY score DESC NULLS LAST LIMIT %s''',
                (vector,selected,subject,region,selected,phases,include_graph,
                 scope.get('curriculum_version',''),scope.get('curriculum_version',''),
                 *[scope.get(k,'') for k in ('school_system','publisher','textbook_version','volume')]*2,
                 self.cfg.embedding_model,self.cfg.embedding_dimensions,limit*4)).fetchall()
        rows=[r for r in rows if r['score'] is not None and r['score']>=0.2 and scope_matches(r['metadata'],scope,r['source_type'])]
        # If multiple curriculum editions compete, require an explicit selection.
        if not scope.get('curriculum_version'):
            versions={r['metadata'].get('curriculum_version') or r['metadata'].get('version') for r in rows if r['source_type']=='curriculum'}
            if len(versions)>1:
                rows=[r for r in rows if r['source_type']!='curriculum']
        return rows[:limit]

    def graph(self, subject='', query='', limit=80):
        with self.connect() as db:
            nodes=db.execute('''SELECT id,name,subject,phase,parent_id,grade_code,metadata FROM kb_nodes
                WHERE (%s='' OR subject=%s) AND (%s='' OR name ILIKE %s) ORDER BY name LIMIT %s''',
                (subject,subject,query,'%'+query+'%',limit)).fetchall()
            ids=[n['id'] for n in nodes]
            edges=db.execute('SELECT * FROM kb_edges WHERE source_id=ANY(%s) AND target_id=ANY(%s) LIMIT 300',(ids,ids)).fetchall()
            return dict(nodes=nodes,edges=edges)

    def target_curriculum(self, subject, grade, query='', region='上海', limit=60):
        """Selectable graph nodes with only explicitly linked curriculum excerpts.

        A graph label alone is not a curriculum requirement. No embeddings or LLM
        requests are needed to browse the catalogue.
        """
        from .grades import GRADE_CODES, GRADE_NAMES, normalize_grades
        code=GRADE_CODES.get(grade,grade)
        phase=phase_for(grade,region)
        with self.connect() as db:
            nodes=db.execute('''SELECT id,name,source_id,grade_code,metadata FROM kb_nodes n
                WHERE subject=%s AND (grade_code=%s OR
                    (COALESCE(grade_code,'')='' AND phase IN (%s,%s,'')))
                    AND (%s='' OR name ILIKE %s)
                ORDER BY CASE WHEN name=%s THEN 0 ELSE 1 END,
                    CASE WHEN EXISTS (SELECT 1 FROM kb_chunks c WHERE c.source_type='curriculum'
                        AND c.subject=n.subject AND c.region IN (%s,'全国')
                        AND (c.grades @> ARRAY[%s]::text[] OR (cardinality(c.grades)=0 AND c.phase IN (%s,'')))
                        AND (c.knowledge_ids && ARRAY[n.id,n.source_id]::text[] OR c.labels @> ARRAY[n.name]::text[]))
                    THEN 0 ELSE 1 END,name,id LIMIT %s''',
                (subject,code,phase,'primary' if grade=='六年级' else phase,query,'%'+query+'%',query,region,grade,phase,limit)).fetchall()
            if not nodes:
                return []
            aliases=list({key for n in nodes for key in (n['id'],n['id'].rsplit(':',1)[-1],n['source_id']) if key})
            names=list({n['name'] for n in nodes})
            chunks=db.execute('''SELECT content,knowledge_ids,labels,grades,metadata FROM kb_chunks
                WHERE source_type='curriculum' AND subject=%s AND region IN (%s,'全国')
                    AND (grades @> ARRAY[%s]::text[] OR (cardinality(grades)=0 AND phase IN (%s,'')))
                    AND (knowledge_ids && %s::text[] OR labels && %s::text[])
                ORDER BY document_id,id''',(subject,region,grade,phase,aliases,names)).fetchall()
        result=[]
        seen=set()
        for node in nodes:
            normalized=normalize_grades([node.get('grade_code') or ''])
            point_grade=next((g for g in normalized if g in GRADE_NAMES.values()),'')
            node_ids={node['id'],node['id'].rsplit(':',1)[-1],node['source_id']}-{None,''}
            # National standards define stages, not textbook progress by year.
            # A known stage may supply an excerpt for the selected target grade;
            # never write that grade back to the source graph or clone entries.
            stage_matches=[c for c in chunks if c.get('metadata',{}).get('grade_scope')=='stage'
                and grade in c.get('grades',[])
                and (node_ids.intersection(c['knowledge_ids']) or node['name'] in c['labels'])]
            if not point_grade and stage_matches:
                point_grade=grade
            key=(node['name'],point_grade)
            if key in seen:
                continue
            seen.add(key)
            group=[n for n in nodes if n['name']==node['name'] and
                ((normalize_grades([n.get('grade_code') or '']) or [''])[0]==point_grade
                 or (stage_matches and not n.get('grade_code')))]
            ids={key for n in group
                for key in (n['id'],n['id'].rsplit(':',1)[-1],n['source_id'])}-{None,''}
            linked=[c for c in chunks if (ids.intersection(c['knowledge_ids']) or node['name'] in c['labels'])
                and (not c.get('grades') or point_grade in c['grades'])]
            point=dict(knowledge_point=node['name'],
                grade=point_grade,grade_source='knowledge_base' if point_grade else 'unknown',
                curriculum_requirements=list(dict.fromkeys(c['content'].strip() for c in linked if c['content'].strip())))
            sources=[dict(title=c['metadata'].get('title',''),source=c['metadata'].get('source',''),
                page=c['metadata'].get('page'),printed_page=c['metadata'].get('printed_page'),
                stage=c['metadata'].get('stage','')) for c in linked if c.get('metadata',{}).get('grade_scope')=='stage']
            if sources:point['curriculum_sources']=sources
            result.append(point)
        return result


def document_chunks(raw, suffix, size=900, overlap=120):
    if suffix == '.pdf':
        from pypdf import PdfReader
        pages = [(n+1,p.extract_text() or '') for n,p in enumerate(PdfReader(io.BytesIO(raw)).pages)]
    elif suffix == '.docx':
        from docx import Document
        doc=Document(io.BytesIO(raw))
        pages=[(None,'\n'.join([p.text for p in doc.paragraphs]+[' | '.join(c.text for c in row.cells) for table in doc.tables for row in table.rows]))]
    elif suffix in {'.txt','.md'}:
        pages=[(None,raw.decode('utf-8-sig'))]
    else:
        raise ValueError('支持 PDF、DOCX、UTF-8 TXT 和 Markdown 文件')
    result=[]
    for page,text in pages:
        text=re.sub(r'[ \t]+',' ',text).strip()
        if not text:
            continue
        start=0
        while start<len(text):
            end=min(start+size,len(text))
            if end<len(text):
                boundary=max(text.rfind('\n',start+size//2,end),text.rfind('。',start+size//2,end))
                if boundary>start:
                    end=boundary+1
            result.append(dict(content=text[start:end],metadata=dict(page=page,char_start=start,char_end=end)))
            if end==len(text):
                break
            start=end-overlap
    if not result:
        raise ValueError('未能提取可索引文字；扫描版 PDF 请先 OCR 后导入')
    return result
