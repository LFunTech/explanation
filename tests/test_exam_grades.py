import copy
from contextlib import contextmanager

import httpx
import pytest

from app.config import Settings
from app.exam import Exam, apply_public_grades, public_grades
from app.kb import phase_for
from app.main import create_app
from app.models import Question


def row(id,code,related=()):
    return dict(id=id,stem='判断几何图形。',answer='C',analysis='由图形特征判断。',
        subject_code='GB-MTH',source_schema='tenant_kg',source_question_id='origin',content_revision=1,
        question_type='SINGLE_CHOICE',difficulty='EASY',labels=[],knowledge_ids=[],
        grade_code=code,grade_codes=list(related),has_attachments=False)


def source(rows):
    queries=[]
    class Database:
        def execute(self,sql,args):
            queries.append((sql,args))
            return self
        def fetchall(self):return copy.deepcopy(rows)
    class Source(Exam):
        @contextmanager
        def connect(self):yield Database()
    return Source(Settings(_env_file=None)),queries


def test_public_source_grades_replace_the_search_default_and_preserve_unknowns():
    exam,queries=source([row('seven','G7'),row('eight','G8',['G8']),row('unknown',None)])
    result=exam.search()
    assert [r['question']['grade'] for r in result]==['七年级','八年级','']
    assert [r['question']['source_grades'] for r in result]==[['七年级'],['八年级'],[]]
    assert result[-1]['grade_scope']=='题库未标注年级'
    assert all(r['question']['source']=='exam/public/question/'+r['id'] for r in result)
    sql,_=queries[0]
    assert 'tenant_kg.' not in sql and 'FROM public.question q' in sql
    assert 'k.phase=' not in sql  # Grade-tagged questions need no knowledge graph node.


def test_search_uses_exact_public_grade_codes():
    exam,queries=source([row('seven','G7')])
    assert exam.search(grade='七年级')[0]['question']['grade']=='七年级'
    sql,args=queries[0]
    assert 'q.grade_code=ANY(%s::text[])' in sql and 'qg.grade_code=ANY(%s::text[])' in sql
    assert args[-4:-1]==(['G7'],['G7'],['G7'])


def test_source_grades_allow_multiple_grades_without_guessing():
    grades=public_grades(dict(grade_code='G8',grade_codes=['G7','G8','G7']))
    assert grades==['七年级','八年级']
    question=Question(question_id='multi',stem='测试',grade='八年级')
    apply_public_grades(question,grades)
    assert question.grades==grades and question.source_grades==grades
    assert question.grade=='七年级'
    question.grade='九年级'
    apply_public_grades(question,grades)
    assert question.grade=='七年级'
    apply_public_grades(question,[])
    assert question.grade=='' and phase_for(question.grade)==''
    assert Question(question_id='manual',stem='未选择年级').grade==''


def test_metadata_lookup_uses_only_public_question_tables():
    id='a33b5467-7e7c-4edc-be94-258e5812f974'
    exam,queries=source([row(id,'G7',['G7'])])
    result=exam.question_metadata(['exam:public:'+id,'invalid'])
    assert result['exam:public:'+id]['grades']==['七年级']
    sql,args=queries[0]
    assert 'tenant_kg.' not in sql and args==([id],)


@pytest.mark.asyncio
async def test_submission_preserves_public_grade_separately_from_target(tmp_path,monkeypatch):
    id='exam:public:a33b5467-7e7c-4edc-be94-258e5812f974'
    cfg=Settings(_env_file=None,data_dir=tmp_path,vector_database_url='',exam_db_host='test',api_key='')
    calls=[]
    def metadata(self,ids):
        calls.append(ids)
        return {id:dict(grades=['七年级'],grade_codes=['G7'])}
    monkeypatch.setattr(Exam,'question_metadata',metadata)
    app=create_app(cfg)
    body=dict(assignment_id='public-grades',questions=[dict(question_id=id,stem='识别三棱柱。',
        grade='九年级',target_grade='六年级',source='exam/public/question/'+id.removeprefix('exam:public:'))],
        options=dict(mode='qwen',voice=False,variant_count=0))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/jobs?single_question=true',json=body,headers={'Idempotency-Key':'grades'})
        assert response.status_code==202
        saved=response.json()
        assert saved['items'][0]['question']['grade']=='七年级'
        assert saved['items'][0]['question']['target_grade']=='六年级'
        assert saved['request']['questions'][0]['source_grades']==['七年级']
        replay=await client.post('/api/jobs?single_question=true',json=body,headers={'Idempotency-Key':'grades'})
        assert replay.json()['idempotent_replay'] and replay.json()['id']==saved['id']
        app.state.store.finish(saved['items'][0]['id'],error={'code':'test','message':'retry'})
        regenerated=await client.post('/api/jobs?single_question=true',json=body)
        assert regenerated.status_code==202
        assert regenerated.json()['id']!=saved['id']
        assert regenerated.json()['courseware_id']!=saved['courseware_id']
        assert regenerated.json()['items'][0]['question']['grade']=='七年级'
        assert regenerated.json()['items'][0]['question']['target_grade']=='六年级'
        body['questions'][0]['grades']=['七年级','八年级']
        explicit=await client.post('/api/jobs?single_question=true',json=body)
        assert explicit.status_code==202
        assert explicit.json()['items'][0]['question']['grades']==['七年级','八年级']
        assert explicit.json()['items'][0]['question']['source_grades']==['七年级']
    assert calls==[[id],[id],[id],[id]]


def test_multi_grade_search_matches_any_selected_grade_and_keeps_all_source_grades():
    exam,queries=source([row('multi','G8',['G7','G8'])])
    result=exam.search(grades=['七年级','八年级','七年级'])
    assert queries[0][1][-4:-1]==(['G7','G8'],['G7','G8'],['G7','G8'])
    assert result[0]['question']['grades']==['七年级','八年级']
    assert result[0]['question']['source_grades']==['七年级','八年级']
    exam.random_questions(grades=['七年级','八年级'])
    assert queries[-1][1][-4]==['G7','G8']


def test_grade_lists_are_normalized_and_legacy_single_grade_still_works():
    assert Question(question_id='old',stem='题目',grade='初一').grades==['七年级']
    q=Question(question_id='new',stem='题目',grade='九年级',grades=['七年级','八年级','七年级',''])
    assert q.grades==['七年级','八年级'] and q.grade=='七年级'
    from app.scope import generation_question
    assert generation_question(q)['grade']==''
    q.target_grade='六年级'
    assert generation_question(q)['grade']=='六年级'
    assert 'grades' not in generation_question(Question(question_id='old',stem='题目',grade='七年级'))


@pytest.mark.asyncio
async def test_both_exam_routes_forward_multiple_grade_filters(tmp_path,monkeypatch):
    calls=[]
    def search(self,*args,**kwargs):calls.append(kwargs['grades']);return []
    monkeypatch.setattr(Exam,'search',search)
    monkeypatch.setattr(Exam,'random_questions',search)
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,vector_database_url='',exam_db_host='',api_key=''))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        params=[('grades','七年级'),('grades','八年级')]
        for route in ['/api/exam/questions','/api/exam/questions/random']:
            response=await client.get(route,params=params)
            assert response.status_code==200
    assert calls==[['七年级','八年级']]*2


@pytest.mark.asyncio
async def test_multi_grade_source_is_preserved_but_generation_and_ppt_use_target(tmp_path):
    import json
    import zipfile
    from tests.test_illustrated_lessons import lesson
    cfg=Settings(_env_file=None,data_dir=tmp_path,vector_database_url='',exam_db_host='',api_key='')
    app=create_app(cfg)
    class FakeQwen:
        async def lesson(self,question,*args,**kwargs):
            assert question.grades==['六年级']
            return lesson(),dict(type='not_reviewed',passed=None)
    app.state.engine.qwen=FakeQwen()
    body=dict(assignment_id='multi-grades',questions=[dict(question_id='multi-grade',stem='测试多选年级。',
        grades=['七年级','八年级'],target_grade='六年级')],options=dict(mode='qwen',voice=False,variant_count=0))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/jobs?single_question=true',json=body)
        assert response.status_code==202
        job=response.json()
        assert job['items'][0]['question']['grades']==['七年级','八年级']
        await app.state.engine.generate(app.state.store.claim())
        item=app.state.store.job(job['id'])['items'][0]
        assert item['status']=='completed'
        package=tmp_path/'packages'/item['package_id']
        manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
        assert manifest['question']['grades']==['七年级','八年级']
        assert manifest['teaching_target']['target_grade']=='六年级'
        with zipfile.ZipFile(package/'courseware.pptx') as archive:
            pages=[n for n in archive.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')]
            assert all('六年级' in archive.read(n).decode() and '七年级、八年级' not in archive.read(n).decode() for n in pages)


@pytest.mark.asyncio
async def test_external_resources_use_target_grade_independently_of_source_grades(tmp_path,monkeypatch):
    from tests.test_core import setup
    from app.models import GenerationOptions
    _,engine,cfg=setup(tmp_path);cfg.knowledge_api_url='https://knowledge.example/resources'
    resources=[dict(id='r-'+str(i),subject='数学',kind='knowledge',region='全国',grades=[g],
        knowledge_points=['三角形'],title='三角形',content='三角形知识',source='外部资源') for i,g in enumerate(['七年级','八年级','九年级'])]
    transport=httpx.MockTransport(lambda request:httpx.Response(200,json=dict(resources=resources)))
    client=httpx.AsyncClient
    monkeypatch.setattr('app.engine.httpx.AsyncClient',lambda **kwargs:client(transport=transport,**kwargs))
    question=Question(question_id='resources',stem='三角形',grades=['七年级','八年级'],target_grade='九年级')
    hits=await engine.resources(question,GenerationOptions(variant_count=0))
    assert {r['id'] for r in hits}=={'r-2'}


@pytest.mark.asyncio
async def test_vector_retrieval_uses_all_grades_in_one_embedding_call(tmp_path):
    from app.kb import KnowledgeBase
    calls=[]
    class Database:
        def execute(self,sql,args):calls.append((sql,args));return self
        def fetchall(self):return []
    class KB(KnowledgeBase):
        @contextmanager
        def connect(self):yield Database()
        async def embeddings(self,texts):return [[1.0]]
    result=await KB(Settings(_env_file=None,data_dir=tmp_path)).search('三角形','数学','五年级',grades=['五年级','八年级'])
    assert result==[]
    sql,args=calls[0]
    assert 'grades && %s::text[]' in sql
    assert args[1]==args[4]==['五年级','八年级']
    assert set(args[5])=={'','primary','junior'}
