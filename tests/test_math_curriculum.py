from contextlib import contextmanager

import pytest

from app.kb import KnowledgeBase
from app.providers import Qwen
from app.target_analysis import analyze_targets
from scripts.import_math_curriculum import content_clauses, link_clauses, load_pages
from tests.test_core import setup
from tests.test_teaching_targets import target_question


def test_clauses_keep_short_requirements_page_continuations_and_original_scope():
    pages={56:'初中部分\n（一）数与代数',57:'''第四学段（7～9 年级）
【内容要求】
1. 数与式
（1）有理数
③理解乘方的意义。
④会用科学记数法
''',58:'''表示数。
【学业要求】
这里是另一类要求，不应并入上一条。
【教学提示】
不要作为内容要求自动填入。
'''}
    result=content_clauses(pages)
    assert [c['content'] for c in result]==['③理解乘方的意义。','④会用科学记数法表示数。']
    meta=result[1]['metadata']
    assert meta['pages']==[57,58] and meta['printed_page']==54 and meta['printed_page_end']==55
    assert meta['grades']==['七年级','八年级','九年级'] and meta['grade_scope']=='stage'


def test_incomplete_ocr_never_publishes_book(tmp_path):
    with pytest.raises(ValueError,match='第 1 页 OCR 未完成'):load_pages(tmp_path)


def test_links_require_all_concepts_and_respect_phase_and_known_grade():
    clauses=[dict(content='探索整数加法的算理。',metadata=dict(phase='primary',grades=['一年级','二年级'])),
        dict(content='理解平均数的意义。',metadata=dict(phase='junior',grades=['七年级','八年级','九年级']))]
    nodes=[dict(id='a',name='整数加法',phase='primary',grade_code=None),
        dict(id='b',name='分数加法',phase='primary',grade_code=None),
        dict(id='c',name='整数加法',phase='primary',grade_code='G5'),
        dict(id='d',name='整数加法',phase='junior',grade_code=None),
        dict(id='e',name='利用已知的平均数求相关数据的平均数',phase='junior',grade_code=None),
        dict(id='f',name='求加权平均数',phase='junior',grade_code=None)]
    rows=link_clauses(clauses,nodes)
    assert rows[0]['metadata']['knowledge_ids']==['a']
    assert rows[1]['metadata']['knowledge_ids']==['e']


def test_document_chunk_scope_and_multiple_links_override_book_scope(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);calls=[]
    class DB:
        def execute(self,sql,params):calls.append((sql,params))
    @contextmanager
    def connect(self):yield DB()
    monkeypatch.setattr(KnowledgeBase,'connect',connect)
    KnowledgeBase(cfg).put_document('math','数学课标','curriculum',dict(subject='数学',grades=['一年级','九年级'],labels=[]),[
        dict(content='小学原文',metadata=dict(grades=['一年级','二年级'],phase='primary',knowledge_ids=['a','b'],labels=['分类'])),
        dict(content='初中原文',metadata=dict(grades=['七年级','八年级','九年级'],phase='junior',knowledge_ids=['c'],labels=['平均数']))])
    rows=[p for sql,p in calls if 'INSERT INTO kb_chunks' in sql]
    assert rows[0][5]=='primary' and rows[0][7:10]==(['一年级','二年级'],['a','b'],['分类'])
    assert rows[1][5]=='junior' and rows[1][7:10]==(['七年级','八年级','九年级'],['c'],['平均数'])


def test_stage_catalog_resolves_one_target_without_relabeling_graph(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);calls=[]
    nodes=[dict(id='exam:public:a',name='平均数',source_id='source-a',grade_code=None,metadata={})]
    chunks=[dict(content='初中原文',knowledge_ids=['exam:public:a'],labels=[],grades=['七年级','八年级','九年级'],
        metadata=dict(grade_scope='stage',stage='第四学段（7～9年级）',title='数学课标',page=76)),
        dict(content='小学原文',knowledge_ids=['exam:public:a'],labels=[],grades=['三年级','四年级'],
        metadata=dict(grade_scope='stage',stage='第二学段（3～4年级）'))]
    class DB:
        def execute(self,sql,params):calls.append(sql);return self
        def fetchall(self):return nodes if len(calls)==1 else chunks
    @contextmanager
    def connect(self):yield DB()
    monkeypatch.setattr(KnowledgeBase,'connect',connect)
    result=KnowledgeBase(cfg).target_curriculum('数学','九年级','平均数')
    assert result[0]['grade']=='九年级'
    assert result[0]['curriculum_requirements']==['初中原文']
    assert result[0]['curriculum_sources'][0]['stage']=='第四学段（7～9年级）'
    assert nodes[0]['grade_code'] is None
    assert all('UPDATE ' not in sql for sql in calls)


@pytest.mark.asyncio
async def test_analysis_explains_stage_basis_and_keeps_original_text(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path)
    async def select(self,instruction,data,schema,**kwargs):return schema(reference_indices=[0])
    monkeypatch.setattr(Qwen,'json',select)
    result=await analyze_targets(target_question(target_grade='九年级'),cfg,[dict(knowledge_point='平均数',grade='九年级',
        curriculum_requirements=['原文要求，不加入本题数值。'],curriculum_sources=[dict(title='数学课标',stage='第四学段（7～9年级）')])])
    assert result.target_curriculum[0].curriculum_requirements==['原文要求，不加入本题数值。']
    assert '来源：数学课标' in result.note and '不代表该年级或学期独有' in result.note
