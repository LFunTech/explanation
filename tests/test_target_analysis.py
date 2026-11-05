import asyncio
from contextlib import contextmanager

import httpx
import pytest

from app.kb import KnowledgeBase
from app.main import create_app
from app.providers import GenerationError, Qwen
from app.target_analysis import CurriculumSelection, analyze_targets, while_connected
from tests.test_core import setup
from tests.test_teaching_targets import target_question


def test_catalog_preserves_explicit_links_and_merges_duplicate_names(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);calls=[]
    nodes=[dict(id='exam:public:a',name='方程',source_id='source-a',grade_code='G7',metadata={}),
        dict(id='exam:public:b',name='方程',source_id='source-b',metadata={}),
        dict(id='exam:public:c',name='等式',source_id='source-c',metadata={})]
    chunks=[dict(content='理解等式性质',knowledge_ids=['c'],labels=[],metadata={}),
        dict(content='解一元一次方程',knowledge_ids=['source-b'],labels=[],metadata={}),
        dict(content='说明变形依据',knowledge_ids=[],labels=['方程'],metadata={}),
        dict(content='不相关的要求',knowledge_ids=['other'],labels=[],metadata={})]
    class DB:
        def execute(self,sql,params):
            calls.append((sql,params))
            return self
        def fetchall(self):return nodes if len(calls)==1 else chunks
    @contextmanager
    def connect(self):yield DB()
    monkeypatch.setattr(KnowledgeBase,'connect',connect)
    result=KnowledgeBase(cfg).target_curriculum('数学','七年级','方','上海')
    assert len(result)==3
    assert result[0]['curriculum_requirements']==['说明变形依据']
    assert result[1]['curriculum_requirements']==['解一元一次方程','说明变形依据']
    assert result[2]['curriculum_requirements']==['理解等式性质']
    assert 'knowledge_point_id' not in result[0]
    assert result[0]['grade']=='七年级' and result[0]['grade_source']=='knowledge_base'
    assert result[1]['grade']=='' and result[1]['grade_source']=='unknown'
    assert calls[0][1][:3]==('数学','G7','junior')
    assert 'source_type=\'curriculum\'' in calls[1][0]


@pytest.mark.asyncio
async def test_analysis_selects_library_text_without_generating_requirements(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);captured={}
    question=target_question(target_grade='一年级',knowledge_points=['数的认识'],knowledge_point_ids=['real-id'],
        images=['data:image/png;base64,AA=='],options=['A. 三只','B. 四只'],requires_images=True)
    references=[dict(knowledge_point='数的认识',grade='一年级',curriculum_requirements=['库内一年级原文']),
        dict(knowledge_point='数的认识',grade='二年级',curriculum_requirements=['库内二年级原文'])]
    async def fake(self,instruction,data,schema,images=()):
        captured.update(instruction=instruction,data=data,images=images,schema=schema.model_json_schema())
        return schema.model_validate(dict(reference_indices=[0,1,0,999,-1],note='匹配到库内条目'))
    monkeypatch.setattr(Qwen,'json',fake)
    result=await analyze_targets(question,cfg,references)
    assert captured['data']['target_grade']=='一年级' and 'grade' not in captured['data']['question']
    assert captured['data']['question']['options']==question.options and captured['images']==question.images
    assert len(result.target_curriculum)==1
    assert result.target_curriculum[0].curriculum_requirements==['库内一年级原文']
    assert result.target_curriculum[0].grade=='一年级'
    assert len(captured['data']['provided_curriculum'])==1
    assert all(p.grade_source=='knowledge_base' for p in result.target_curriculum)
    assert set(captured['schema']['properties'])=={'reference_indices','note'}
    assert '不生成或改写课标要求' in captured['instruction'] and '已忽略' in result.note


@pytest.mark.asyncio
async def test_missing_library_text_or_grade_never_falls_back_to_model_advice(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path)
    async def unexpected(*args,**kwargs):raise AssertionError('No local reference must not invoke LLM')
    monkeypatch.setattr(Qwen,'json',unexpected)
    for references in [[],[dict(knowledge_point='方程',grade='七年级',curriculum_requirements=[])],
        [dict(knowledge_point='方程',grade='',curriculum_requirements=['无年级的要求'])]]:
        result=await analyze_targets(target_question(),cfg,references)
        assert result.target_curriculum==[] and '未匹配' in result.note


@pytest.mark.asyncio
async def test_analysis_retries_transport_errors_without_creating_job(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);calls=[];sleeps=[]
    async def fake(self,*args,**kwargs):
        calls.append(True)
        if len(calls)<3:raise GenerationError('qwen_connection','断开')
        return CurriculumSelection(reference_indices=[0])
    async def sleep(delay):sleeps.append(delay)
    monkeypatch.setattr(Qwen,'json',fake)
    monkeypatch.setattr('app.target_analysis.asyncio.sleep',sleep)
    result=await analyze_targets(target_question(),cfg,[dict(knowledge_point='方程',grade='七年级',curriculum_requirements=['库内要求'])])
    assert sleeps==[1,2] and result.target_curriculum[0].curriculum_requirements==['库内要求']


@pytest.mark.asyncio
async def test_disconnect_cancels_inflight_model():
    started=asyncio.Event();cancelled=asyncio.Event()
    async def work():
        started.set()
        try:await asyncio.Event().wait()
        finally:cancelled.set()
    class Request:
        async def is_disconnected(self):return True
    with pytest.raises(asyncio.CancelledError):await while_connected(Request(),work())
    assert started.is_set() and cancelled.is_set()


@pytest.mark.asyncio
async def test_analysis_api_returns_unmatched_when_local_catalog_is_unavailable(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);app=create_app(cfg)
    async def unexpected(*args,**kwargs):raise AssertionError('No model-authored standards')
    monkeypatch.setattr(Qwen,'json',unexpected)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get('/api/teaching-targets?target_grade=七年级')
        assert response.status_code==200 and response.json()['items']==[]
        assert '尚未配置' in response.json()['notice']
        question=target_question().model_dump();question['target_grade']=''
        assert (await client.post('/api/teaching-targets/analyze',json=question)).status_code==422
        question['target_grade']='七年级'
        for headers in [{},{'X-Teaching-Targets-Version':'2'},{'X-Teaching-Targets-Version':'3'}]:
            response=await client.post('/api/teaching-targets/analyze',json=question,headers=headers)
            assert response.status_code==200
            assert response.json()['source']=='knowledge_base' and response.json()['target_curriculum']==[]
            assert '未匹配' in response.json()['note']
        cfg.vector_database_url='postgresql://test'
        def unavailable(*args):raise RuntimeError('offline')
        monkeypatch.setattr(KnowledgeBase,'target_curriculum',unavailable)
        response=await client.post('/api/teaching-targets/analyze',json=question)
        assert response.json()['target_curriculum']==[] and '暂时无法读取' in response.json()['note']
        assert app.state.store.list_jobs()==[]


@pytest.mark.asyncio
async def test_catalog_old_and_new_clients_receive_compatible_shapes(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);cfg.vector_database_url='postgresql://test'
    monkeypatch.setattr(KnowledgeBase,'target_curriculum',lambda *args:[dict(knowledge_point='方程',knowledge_point_id='id',
        curriculum_requirements=[],grade='七年级',grade_source='knowledge_base')])
    app=create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        old=await client.get('/api/teaching-targets?target_grade=七年级')
        new=await client.get('/api/teaching-targets?target_grade=七年级',headers={'X-Teaching-Targets-Version':'2'})
        single=await client.get('/api/teaching-targets?target_grade=七年级',headers={'X-Teaching-Targets-Version':'3'})
    assert set(old.json()['items'][0])=={'knowledge_point','grade','grade_source','curriculum_requirements'}
    assert new.json()['items'][0]['grade']=='七年级' and 'grades' not in new.json()['items'][0]
    assert 'knowledge_point_id' not in new.json()['items'][0]
    assert single.json()['items'][0]['grade']=='七年级' and 'grades' not in single.json()['items'][0]


@pytest.mark.asyncio
async def test_analysis_still_receives_local_references_before_generation(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);cfg.vector_database_url='postgresql://test';app=create_app(cfg)
    references=[dict(knowledge_point='方程',grade='七年级',grade_source='knowledge_base',curriculum_requirements=['说明等式变形的依据'])]
    calls=[]
    def lookup(self,subject,grade,name,region,limit):
        calls.append((subject,grade,name))
        return references
    async def respond(self,instruction,data,schema,**kwargs):
        assert data['provided_curriculum']==references
        assert data['target_semester']=='上半学期'
        return schema.model_validate(dict(reference_indices=[0]))
    monkeypatch.setattr(KnowledgeBase,'target_curriculum',lookup)
    monkeypatch.setattr(Qwen,'json',respond)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/teaching-targets/analyze',json=target_question(target_semester='上半学期').model_dump())
    assert response.status_code==200 and calls==[('数学','七年级','原题标签')]
    assert not app.state.store.list_jobs()


@pytest.mark.asyncio
async def test_eighth_grade_analysis_keeps_library_citation_and_never_seventh_grade(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path)
    clause='②能画一次函数的图象；理解正比例函数。'
    citation=dict(title='数学课标',source='https://example.org/standard.pdf',page=60,printed_page=57,stage='第四学段（7～9年级）')
    refs=[dict(knowledge_point='函数',grade=grade,curriculum_requirements=[clause],curriculum_sources=[citation]) for grade in ['七年级','八年级','九年级']]
    async def choose(self,instruction,data,schema,**kwargs):
        assert [p['grade'] for p in data['provided_curriculum']]==['八年级']
        return CurriculumSelection(reference_indices=[0],note='匹配')
    monkeypatch.setattr(Qwen,'json',choose)
    result=await analyze_targets(target_question(target_grade='八年级'),cfg,refs)
    assert [p.grade for p in result.target_curriculum]==['八年级']
    assert result.curriculum_sources[0]['sources']==[citation]
    assert result.curriculum_sources[0]['curriculum_requirements']==[clause]
