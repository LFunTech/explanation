"""远端 exam 连接器：固定公共资源表，数据库级只读事务。"""
import html
import json
import re
from contextlib import contextmanager
from html.parser import HTMLParser

import psycopg
from pydantic import ValidationError
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .kb import phase_for
from .models import Question
from .providers import GenerationError


SUBJECTS = {'GB-MTH':'数学','GB-PHY':'物理','GB-CHE':'化学','GB-BIO':'生物','GB-SCI':'科学',
            'GB-CHN':'语文','GB-FLG':'英语','GB-HIS':'历史','GB-GEO':'地理','GB-MRL':'道德与法治'}


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
        return dict(stem=stem,options=[],sub_questions=[],image_urls=images,requires_images=bool(images))
    images=[]
    def text(value):
        result,assets=plain(str(value or ''));images.extend(assets);return result
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
        sub_questions.append(dict(id=str(sub.get('label') or index+1),stem=content.strip() or '请结合共用题干作答。',answer=text(sub.get('answer')),analysis=text(sub.get('analysis'))))
    def has_assets(node):
        if isinstance(node,dict):
            return any(bool(value) if key in {'attachments','inlineAssets','answerInlineAssets','analysisInlineAssets'} else has_assets(value) for key,value in node.items())
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
    def __init__(self,settings):
        self.cfg=settings

    @contextmanager
    def connect(self):
        if not self.cfg.exam_db_host:
            raise GenerationError('exam_not_configured','尚未配置 exam 题库连接。')
        with psycopg.connect(host=self.cfg.exam_db_host,port=self.cfg.exam_db_port,
            dbname=self.cfg.exam_db_name,user=self.cfg.exam_db_user,password=self.cfg.exam_db_password,
            connect_timeout=8,options='-c default_transaction_read_only=on -c statement_timeout=20000',
            row_factory=dict_row) as db:
            yield db

    def search(self,subject='数学',grade='九年级',query='',limit=12,knowledge_ids=None,exclude_id=None,
               *,randomize=False,exclude_ids=None,region='上海'):
        code=next((k for k,v in SUBJECTS.items() if v==subject),'')
        phase=phase_for(grade,region)
        ids=[v.removeprefix('exam:public:') for v in (knowledge_ids or [])]
        excluded=[v.removeprefix('exam:public:') for v in (exclude_ids or [])]
        order='random()' if randomize else 'q.updated_at DESC,q.id'
        with self.connect() as db:
            # 精确年级来自题目标签；无标签只返回同学段，并在 UI 明示待核对。
            rows=db.execute('''SELECT q.id::text,q.stem,q.answer,q.analysis,q.question_type,q.difficulty,
                    q.subject_code,q.source_schema,q.source_question_id,q.content_revision,
                    COALESCE((SELECT array_agg(k.knowledge_point_name ORDER BY r.sort_order) FROM public.question_taxonomy_relation r
                        JOIN public.knowledge_point k ON k.id=r.knowledge_point_id WHERE r.question_id=q.id AND k.effective_enabled),'{}') AS labels,
                    COALESCE((SELECT array_agg(k.id::text ORDER BY r.sort_order) FROM public.question_taxonomy_relation r
                        JOIN public.knowledge_point k ON k.id=r.knowledge_point_id WHERE r.question_id=q.id AND k.effective_enabled),'{}') AS knowledge_ids,
                    COALESCE((SELECT array_agg(DISTINCT g.grade_name) FROM public.question_grade qg
                        JOIN tenant_kg.grade g ON g.grade_code=qg.grade_code WHERE qg.question_id=q.id),'{}') AS grades,
                    EXISTS(SELECT 1 FROM public.question_attachment a WHERE a.question_id=q.id) AS has_attachments
                FROM public.question q WHERE q.status='ACTIVE' AND q.subject_code=%s
                    AND (%s='' OR q.stem ILIKE %s OR EXISTS(SELECT 1 FROM public.question_taxonomy_relation r
                        JOIN public.knowledge_point k ON k.id=r.knowledge_point_id WHERE r.question_id=q.id AND k.knowledge_point_name ILIKE %s))
                    AND (%s::text[]='{}' OR EXISTS(SELECT 1 FROM public.question_taxonomy_relation r WHERE r.question_id=q.id AND r.knowledge_point_id::text=ANY(%s)))
                    AND (%s::text IS NULL OR q.id::text<>%s)
                    AND NOT(q.id::text=ANY(%s::text[]))
                    AND EXISTS(SELECT 1 FROM public.question_taxonomy_relation r JOIN public.knowledge_point k ON k.id=r.knowledge_point_id
                        WHERE r.question_id=q.id AND k.phase=%s AND k.effective_enabled)
                ORDER BY '''+order+''' LIMIT %s''',
                (code,query,'%'+query+'%','%'+query+'%',ids,ids,exclude_id,exclude_id,excluded,phase,limit*3)).fetchall()
        result=[]
        for row in rows:
            parsed=parse_stem(row['stem'])
            row['stem'],row['image_urls']=parsed['stem'],parsed['image_urls']
            row['has_attachments']=row['has_attachments'] or parsed['requires_images']
            row['answer'],_=plain(row['answer'])
            row['analysis'],_=plain(row['analysis'])
            row['grade_scope']='题库年级' if row['grades'] else '仅学段，年级待核对'
            # 上海五四与源六三年级不做隐式等价映射。
            try:
                question=Question(question_id='exam:public:'+row['id'],stem=row['stem'],subject=subject,
                grade=grade,region=region,knowledge_points=list(dict.fromkeys(row['labels']))[:20],
                options=parsed['options'],sub_questions=parsed['sub_questions'],
                knowledge_point_ids=list(dict.fromkeys(row['knowledge_ids']))[:20],
                question_type={'SINGLE_CHOICE':'单选题','MULTIPLE_CHOICE':'多选题','FILL_BLANK':'填空题','SHORT_ANSWER':'解答题'}.get(row['question_type'],'解答题'),
                difficulty={'EASY':'基础','RELATIVELY_EASY':'基础','NORMAL':'中等','HARD':'较难','VERY_HARD':'较难'}.get(row['difficulty'],'中等'),
                answer=row['answer'],analysis=row['analysis'],source='exam/public/question/'+row['id'],
                    requires_images=bool(row['image_urls'] or row['has_attachments']))
            except ValidationError:
                # 空题干、重复小题或超过单题容量的源记录不阻断整个检索结果。
                continue
            row['question']=question.model_dump()
            result.append(row)
            if len(result)>=limit:
                break
        return result

    def random_questions(self,subject='数学',grade='九年级',region='上海',limit=3,exclude_ids=None):
        rows=self.search(subject,grade,limit=limit,randomize=True,exclude_ids=exclude_ids,region=region)
        # 小题库不足一批时允许补回旧题，但同一批不重复。
        if len(rows)<limit and exclude_ids:
            extra=self.search(subject,grade,limit=limit-len(rows),randomize=True,
                exclude_ids=[row['id'] for row in rows],region=region)
            rows.extend(extra)
        return rows

    def variants(self,question,limit):
        if not limit or not (question.knowledge_point_ids or question.knowledge_points):
            return []
        rows=self.search(question.subject,question.grade,
            '' if question.knowledge_point_ids else question.knowledge_points[0],limit*3,
            question.knowledge_point_ids,question.question_id.removeprefix('exam:public:'))
        # 缺图题不冒充完整题；待图片服务接入后可纳入候选。
        rows=[r for r in rows if not r['image_urls'] and not r['has_attachments'] and r['answer'] and r['analysis'] and r['stem']!=question.stem]
        rows.sort(key=lambda r:(r['question']['difficulty']!=question.difficulty,len(r['question']['sub_questions'])>2,len(r['analysis'])))
        return [dict(id='exam:public:'+r['id'],title='题库变式 · '+r['stem'][:35],kind='question',subject=question.subject,
            grades=r['grades'],region='全国',knowledge_points=r['labels'],content=r['stem']+''.join('\n'+s for s in r['question']['options'])+''.join('\n（'+s['id']+'）'+s['stem'] for s in r['question']['sub_questions']),answer=r['answer'],analysis=r['analysis'],
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
