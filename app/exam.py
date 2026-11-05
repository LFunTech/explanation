"""远端 exam 连接器：固定公共资源表，数据库级只读事务。"""
import html
import json
import re
import queue
import threading
import time
from contextlib import contextmanager
from html.parser import HTMLParser

import psycopg
from pydantic import ValidationError
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .exam_storage import ExamImageStorage
from .models import Question
from .providers import GenerationError
from .grades import GRADE_NAMES, GRADE_CODES, normalize_grades
from .question_layout import question_layout
from .exam_cache import ExamCache


SUBJECTS = {'GB-MTH':'数学','GB-PHY':'物理','GB-CHE':'化学','GB-BIO':'生物','GB-SCI':'科学',
            'GB-CHN':'语文','GB-FLG':'英语','GB-HIS':'历史','GB-GEO':'地理','GB-MRL':'道德与法治'}


def public_grades(row):
    """Use the public question's own codes, without a tenant grade dictionary."""
    codes=list(dict.fromkeys([*(row.get('grade_codes') or []),row.get('grade_code')]))
    return [GRADE_NAMES.get(code,code) for code in codes if code]


def apply_public_grades(question, grades, *, preserve_selection=False):
    question.source_grades=normalize_grades(grades)
    if not preserve_selection or not question.grades:
        question.grades=list(question.source_grades)
    # A search/default grade is never evidence of the source question's grade.
    question.grade=next(iter(question.grades),'')


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.images=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='img' and attrs.get('src'):
            self.images.append(attrs['src'])
        if tag in {'p','div','br','li','tr'}:
            self.parts.append('\n')
    def handle_data(self,data):
        self.parts.append(data)


def plain(value):
    # 题库中既有结构化 JSON/LaTeX，也有 HTML。数学中的 < 不能作为标签删除。
    tags=r'/?(?:p|div|br|img|span|table|tr|td|li|ul|ol|strong|em|b|i|sup|sub)(?:\s+[^<>]*?)?\s*/?>'
    if not re.search('<'+tags,value or '',re.I):
        return html.unescape(value or ''),[]
    parser=PlainHTML(); parser.feed(re.sub('<(?!'+tags+')','&lt;',value or '',flags=re.I))
    return html.unescape(''.join(parser.parts)).strip(),parser.images


def parse_stem(value):
    try:
        body=json.loads(value)
    except (ValueError,TypeError):
        body=None
    if not isinstance(body,dict) or 'stem' not in body:
        stem,images=plain(value)
        return dict(stem=stem,options=[],sub_questions=[],image_urls=images,
            requires_images=bool(images) or bool(re.search(r'<img\b',value or '',re.I)))
    images=[]
    def text(value,collect_images=True):
        result,assets=plain(str(value or ''))
        if collect_images:
            images.extend(assets)
        return result
    def options(values):
        result=[]
        for index,option in enumerate(values or []):
            if isinstance(option,dict):
                label=option.get('label') or option.get('key') or chr(65+index)
                content=option.get('content') or option.get('text') or option.get('value') or ''
                result.append(str(label)+'. '+text(content))
            else:
                result.append(text(option))
        return result
    stem=text(body.get('stem'))
    if body.get('prompt'):
        stem+='\n'+text(body['prompt'])
    sub_questions=[]
    for index,sub in enumerate(body.get('subQuestions',[])):
        if not isinstance(sub,dict):
            continue
        content=text(sub.get('stem'))
        if sub.get('prompt'):
            content+='\n'+text(sub['prompt'])
        content+=''.join('\n'+entry for entry in options(sub.get('options',[])))
        sub_questions.append(dict(id=str(sub.get('label') or index+1),stem=content.strip() or '请结合共用题干作答。',answer=text(sub.get('answer'),False),analysis=text(sub.get('analysis'),False)))
    def has_assets(node):
        if isinstance(node,dict):
            return any(any(not isinstance(asset,dict) or str(asset.get('usageType') or 'MATERIAL').upper() not in {'ANSWER','ANALYSIS','SOLUTION'} for asset in (value or []))
                if key in {'attachments','inlineAssets'} else has_assets(value)
                for key,value in node.items() if key not in {'answerInlineAssets','analysisInlineAssets','answer','analysis'})
        if isinstance(node,str) and re.search(r'<img\b',node,re.I):
            return True
        return isinstance(node,list) and any(has_assets(v) for v in node)
    parsed_options=options(body.get('options',[]))
    requires_images=bool(images) or has_assets(body)
    if not stem.strip() and sub_questions:
        stem='以下为题库小题（原题无共用题干）：'
    if not stem.strip() and requires_images:
        stem='本题题干位于原题图片中，请结合图片作答。'
    return dict(stem=stem,options=parsed_options,sub_questions=sub_questions,
                image_urls=images,requires_images=requires_images)


class Exam:
    _pools={}
    _pool_lock=threading.Lock()
    def __init__(self,settings):
        self.cfg=settings

    @contextmanager
    def connect(self):
        if not self.cfg.exam_db_host:
            raise GenerationError('exam_not_configured','尚未配置 exam 题库连接。')
        key=(self.cfg.exam_db_host,self.cfg.exam_db_port,self.cfg.exam_db_name,self.cfg.exam_db_user,self.cfg.exam_db_password)
        with self._pool_lock:
            pool=self._pools.setdefault(key,queue.LifoQueue(maxsize=4))
        db=None
        for attempt in range(3):
            try:
                try:db=pool.get_nowait()
                except queue.Empty:
                    db=psycopg.connect(host=self.cfg.exam_db_host,port=self.cfg.exam_db_port,
                        dbname=self.cfg.exam_db_name,user=self.cfg.exam_db_user,password=self.cfg.exam_db_password,
                        connect_timeout=12,options='-c default_transaction_read_only=on -c statement_timeout=20000',
                        keepalives=1,keepalives_idle=15,keepalives_interval=5,keepalives_count=3,
                        row_factory=dict_row,autocommit=True)
                db.execute('SELECT 1')
                break
            except (psycopg.OperationalError,psycopg.InterfaceError) as error:
                if db is not None:db.close();db=None
                if getattr(error,'sqlstate',None) in {'28P01','28000'}:
                    raise GenerationError('exam_configuration','题库连接配置不可用，请联系管理员检查。') from None
                if attempt==2:
                    raise GenerationError('exam_unavailable','题库连接暂时中断，已自动重连，请稍后重试。') from None
                time.sleep(.3*(attempt+1))
        try:
            # Every caller gets a separate read-only transaction; idle connections
            # are reused so checking several grades does not repeat TCP handshakes.
            with db.transaction():yield db
        except (psycopg.OperationalError,psycopg.InterfaceError):
            db.close()
            raise GenerationError('exam_unavailable','题库读取时连接中断，请重试当前操作。') from None
        finally:
            if db is not None and not db.closed:
                try:pool.put_nowait(db)
                except queue.Full:db.close()

    @classmethod
    def close_connections(cls):
        with cls._pool_lock:
            for pool in cls._pools.values():
                while not pool.empty():
                    try:pool.get_nowait().close()
                    except queue.Empty:break
            cls._pools.clear()

    def search(self,subject='数学',grade='',query='',limit=12,knowledge_ids=None,exclude_id=None,
               *,randomize=False,exclude_ids=None,region='上海',school_system='',grades=None,question_id=None):
        code=next((k for k,v in SUBJECTS.items() if v==subject),'')
        selected_grades=normalize_grades(grades if grades is not None else [grade])
        grade_codes=[GRADE_CODES.get(g,g) for g in selected_grades]
        ids=[v.removeprefix('exam:public:') for v in (knowledge_ids or [])]
        excluded=[v.removeprefix('exam:public:') for v in (exclude_ids or [])]
        order='random()' if randomize else 'q.updated_at DESC,q.id'
        with self.connect() as db:
            # Both the source and grade filter stay inside the exam public schema.
            rows=db.execute('''SELECT q.id::text,q.stem,q.answer,q.analysis,q.question_type,q.difficulty,
                    q.subject_code,q.source_schema,q.source_question_id,q.content_revision,q.grade_code,
                    COALESCE((SELECT array_agg(k.knowledge_point_name ORDER BY r.sort_order) FROM public.question_taxonomy_relation r
                        JOIN public.knowledge_point k ON k.id=r.knowledge_point_id WHERE r.question_id=q.id AND k.effective_enabled),'{}') AS labels,
                    COALESCE((SELECT array_agg(k.id::text ORDER BY r.sort_order) FROM public.question_taxonomy_relation r
                        JOIN public.knowledge_point k ON k.id=r.knowledge_point_id WHERE r.question_id=q.id AND k.effective_enabled),'{}') AS knowledge_ids,
                    COALESCE((SELECT array_agg(qg.grade_code ORDER BY qg.sort_order,qg.grade_code)
                        FROM public.question_grade qg WHERE qg.question_id=q.id),'{}') AS grade_codes,
                    EXISTS(SELECT 1 FROM public.question_attachment a WHERE a.question_id=q.id
                        AND upper(COALESCE(a.usage_type,'MATERIAL')) NOT IN ('ANSWER','ANALYSIS','SOLUTION')) AS has_attachments
                FROM public.question q WHERE q.status='ACTIVE' AND (%s::boolean OR q.subject_code=%s)
                    AND (%s::text IS NULL OR q.id::text=%s)
                    AND (%s='' OR q.stem ILIKE %s OR EXISTS(SELECT 1 FROM public.question_taxonomy_relation r
                        JOIN public.knowledge_point k ON k.id=r.knowledge_point_id WHERE r.question_id=q.id AND k.knowledge_point_name ILIKE %s))
                    AND (%s::text[]='{}' OR EXISTS(SELECT 1 FROM public.question_taxonomy_relation r WHERE r.question_id=q.id AND r.knowledge_point_id::text=ANY(%s)))
                    AND (%s::text IS NULL OR q.id::text<>%s)
                    AND NOT(q.id::text=ANY(%s::text[]))
                    AND (%s::text[]='{}' OR q.grade_code=ANY(%s::text[]) OR EXISTS(SELECT 1 FROM public.question_grade qg
                        WHERE qg.question_id=q.id AND qg.grade_code=ANY(%s::text[])))
                ORDER BY '''+order+''' LIMIT %s''',
                (question_id is not None,code,question_id,question_id,query,'%'+query+'%','%'+query+'%',ids,ids,exclude_id,exclude_id,excluded,
                    grade_codes,grade_codes,grade_codes,limit*3)).fetchall()
        result=[]
        for row in rows:
            parsed=parse_stem(row['stem'])
            row['stem'],row['image_urls']=parsed['stem'],parsed['image_urls']
            row['has_attachments']=row['has_attachments'] or parsed['requires_images']
            row['answer'],_=plain(row['answer'])
            row['analysis'],_=plain(row['analysis'])
            row['grades']=public_grades(row)
            row['grade_scope']='题库年级' if row['grades'] else '题库未标注年级'
            try:
                question=Question(question_id='exam:public:'+row['id'],stem=row['stem'],subject=SUBJECTS.get(row['subject_code'],subject),
                grades=row['grades'],region=region,knowledge_points=list(dict.fromkeys(row['labels']))[:20],
                options=parsed['options'],sub_questions=parsed['sub_questions'],
                knowledge_point_ids=list(dict.fromkeys(row['knowledge_ids']))[:20],
                question_type={'SINGLE_CHOICE':'单选题','MULTIPLE_CHOICE':'多选题','FILL_BLANK':'填空题','SHORT_ANSWER':'解答题'}.get(row['question_type'],'解答题'),
                difficulty={'EASY':'基础','RELATIVELY_EASY':'基础','NORMAL':'中等','HARD':'较难','VERY_HARD':'较难'}.get(row['difficulty'],'中等'),
                answer=row['answer'],analysis=row['analysis'],source='exam/public/question/'+row['id'],
                    requires_images=bool(row['image_urls'] or row['has_attachments']))
                apply_public_grades(question,row['grades'])
            except ValidationError:
                # 空题干、重复小题或超过单题容量的源记录不阻断整个检索结果。
                continue
            row['question']=question.model_dump()
            result.append(row)
            if len(result)>=limit:
                break
        if self.cfg.exam_db_host:
            ExamCache(self.cfg).put(result)
        return result

    def question(self, question_id):
        """Load one complete, active public question by its canonical ID."""
        from uuid import UUID
        try:question_id=str(UUID(question_id.removeprefix('exam:public:')))
        except ValueError:raise GenerationError('exam_question_missing','题库题目标识无效。') from None
        rows=self.search(subject='',limit=1,question_id=question_id)
        if not rows:
            raise GenerationError('exam_question_missing','题库题目不存在、已停用或内容不完整。')
        return Question.model_validate(rows[0]['question'])

    def random_questions(self,subject='数学',grade='',region='上海',limit=3,exclude_ids=None,*,grades=None):
        rows=self.search(subject,grade,limit=limit,randomize=True,exclude_ids=exclude_ids,region=region,grades=grades)
        # 小题库不足一批时允许补回旧题，但同一批不重复。
        if len(rows)<limit and exclude_ids:
            extra=self.search(subject,grade,limit=limit-len(rows),randomize=True,
                exclude_ids=[row['id'] for row in rows],region=region,grades=grades)
            rows.extend(extra)
        return rows

    def question_metadata(self,question_ids):
        """Resolve canonical public grades for submissions and existing courseware."""
        from uuid import UUID
        ids=[]
        for value in question_ids:
            try:ids.append(str(UUID(value.removeprefix('exam:public:'))))
            except ValueError:continue
        if not ids:return {}
        with self.connect() as db:
            rows=db.execute('''SELECT q.id::text,q.grade_code,
                COALESCE((SELECT array_agg(qg.grade_code ORDER BY qg.sort_order,qg.grade_code)
                    FROM public.question_grade qg WHERE qg.question_id=q.id),'{}') AS grade_codes
                FROM public.question q WHERE q.id::text=ANY(%s) AND q.status='ACTIVE' ''',(ids,)).fetchall()
        return {'exam:public:'+r['id']:dict(grades=public_grades(r),grade_codes=r['grade_codes']) for r in rows}

    def images(self,question_id):
        """关联只在公共题库内查询；下载全成功后一次性返回，避免缺图提交。"""
        from uuid import UUID
        try:
            question_id=str(UUID(question_id.removeprefix('exam:public:')))
        except ValueError:
            raise GenerationError('exam_question_missing','题库题目标识无效。') from None
        with self.connect() as db:
            question=db.execute("SELECT stem FROM public.question WHERE id=%s AND status='ACTIVE'",(question_id,)).fetchone()
            if not question:
                raise GenerationError('exam_question_missing','题库题目不存在或已停用。')
            rows=db.execute('''SELECT a.id::text,a.source_uri,a.file_id::text,a.usage_type,a.sort_order,
                    f.bucket_name,f.object_key FROM public.question_attachment a
                LEFT JOIN public.file_metadata f ON f.id::text=a.file_id::text
                WHERE a.question_id=%s AND upper(COALESCE(a.usage_type,'MATERIAL')) NOT IN ('ANSWER','ANALYSIS','SOLUTION')
                ORDER BY a.sort_order,a.id''',(question_id,)).fetchall()
        locations=[]
        for row in rows:
            uri=row['source_uri'] or ('minio://'+row['bucket_name']+'/'+row['object_key'].lstrip('/') if row['bucket_name'] and row['object_key'] else '')
            if not uri:
                raise GenerationError('exam_image_missing','题库已关联图片，但缺少可读取的存储位置，请补充原图。')
            if uri not in [entry[0] for entry in locations]:
                locations.append((uri,row))
        if not locations and parse_stem(question['stem'])['requires_images']:
            raise GenerationError('exam_image_missing','题干引用了图片，但公共题库尚未同步附件关联，请补充原图。')
        if len(locations)>8:
            raise GenerationError('exam_image_count','该题包含超过 8 张原图，请先整理后上传。')
        storage=ExamImageStorage(self.cfg)
        images=[storage.read(uri) for uri,row in locations]
        return dict(question_id='exam:public:'+question_id,images=images,
            attachments=[dict(id=row['id'],usage_type=row['usage_type'],sort_order=row['sort_order']) for _,row in locations])

    def variants(self,question,limit):
        if not limit or not (question.knowledge_point_ids or question.knowledge_points):
            return []
        rows=self.search(question.subject,question.grade,
            '' if question.knowledge_point_ids else question.knowledge_points[0],limit*3,
            question.knowledge_point_ids,question.question_id.removeprefix('exam:public:'),region=question.region,school_system=question.school_system,grades=question.grades)
        # 缺图题不冒充完整题；待图片服务接入后可纳入候选。
        rows=[r for r in rows if not r['image_urls'] and not r['has_attachments'] and r['answer'] and r['analysis'] and r['stem']!=question.stem]
        rows.sort(key=lambda r:(r['question']['difficulty']!=question.difficulty,len(r['question']['sub_questions'])>2,len(r['analysis'])))
        def content(row):
            layout=question_layout(row['question'])
            return layout['stem']+''.join('\n'+s for s in layout['options'])+''.join('\n（'+s['id']+'）'+s['stem']+''.join('\n'+c['label']+'. '+c['text'] for c in s.get('choices',[])) for s in layout['sub_questions'])
        return [dict(id='exam:public:'+r['id'],title='题库变式 · '+r['stem'][:35],kind='question',subject=question.subject,
            grades=r['grades'],region='全国',knowledge_points=r['labels'],content=content(r),answer=r['answer'],analysis=r['analysis'],
            source='exam 公共题库',version=str(r['content_revision']),is_sample=False,grade_scope=r['grade_scope']) for r in rows[:limit]]

    def import_graph(self,kb):
        with self.connect() as source:
            nodes=source.execute('''SELECT id::text,source_schema,source_knowledge_point_id,subject_code,phase,grade_code,
                parent_id::text,knowledge_point_code,knowledge_point_name,revision FROM public.knowledge_point WHERE effective_enabled ORDER BY id''').fetchall()
            edges=source.execute('''SELECT id::text,source_knowledge_point_id::text,target_knowledge_point_id::text,
                relation_type,assertion_level,evidence_strength,reason_text,model_version FROM public.kg_knowledge_relation''').fetchall()
        node_ids={n['id'] for n in nodes}
        with kb.connect() as dest:
            for node in nodes:
                subject=SUBJECTS.get(node['subject_code'],node['subject_code'])
                dest.execute('''INSERT INTO kb_nodes(id,source_schema,source_id,subject_code,subject,phase,grade_code,parent_id,code,name,metadata)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                    parent_id=excluded.parent_id,grade_code=excluded.grade_code,metadata=excluded.metadata,updated_at=now()''',
                    ('exam:public:'+node['id'],node['source_schema'],node['source_knowledge_point_id'],node['subject_code'],subject,
                     node['phase'],node['grade_code'],'exam:public:'+node['parent_id'] if node['parent_id'] else None,
                     node['knowledge_point_code'],node['knowledge_point_name'],Jsonb(node)))
            edge_records=[]
            for n in nodes:
                if n['parent_id'] in node_ids:
                    edge_records.append(('hierarchy:'+n['id'],'exam:public:'+n['parent_id'],'exam:public:'+n['id'],'parent_of',Jsonb({'source':'exam/public/knowledge_point'})))
            for e in edges:
                if e['source_knowledge_point_id'] in node_ids and e['target_knowledge_point_id'] in node_ids:
                    edge_records.append(('exam:public:'+e['id'],'exam:public:'+e['source_knowledge_point_id'],'exam:public:'+e['target_knowledge_point_id'],e['relation_type'],Jsonb(e)))
            for row in edge_records:
                dest.execute('INSERT INTO kb_edges VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET source_id=excluded.source_id,target_id=excluded.target_id,relation_type=excluded.relation_type,metadata=excluded.metadata',row)
            dest.execute("DELETE FROM kb_edges WHERE (id LIKE 'exam:public:%%' OR id LIKE 'hierarchy:%%') AND NOT(id=ANY(%s))",([r[0] for r in edge_records],))
            dest.execute("DELETE FROM kb_nodes WHERE id LIKE 'exam:public:%%' AND NOT(id=ANY(%s))",(['exam:public:'+n['id'] for n in nodes],))
        # 图谱节点是索引词，不当作教材讲解正文。按学科/学段分组，保留逐点 ID。
        groups={}
        for n in nodes:
            groups.setdefault((n['subject_code'],n['phase']),[]).append(n)
        for (subject_code,phase),entries in groups.items():
            kb.put_document('exam-graph-'+subject_code+'-'+phase,'exam 知识图谱 · '+SUBJECTS.get(subject_code,subject_code),'graph',
                dict(subject=SUBJECTS.get(subject_code,subject_code),phase=phase,region='全国',grades=[],source='exam/public/knowledge_point',
                     grade_mapping='源知识点年级为空时仅按学段检索；未自动映射上海五四学制'),
                [dict(content=n['knowledge_point_name'],metadata=dict(knowledge_id='exam:public:'+n['id'],source_id=n['source_knowledge_point_id'],
                      parent_id=n['parent_id'],revision=n['revision'],grade_code=n['grade_code'])) for n in entries])
        return dict(nodes=len(nodes),edges=len(edge_records),grade_unmapped=sum(n['grade_code'] is None for n in nodes))
