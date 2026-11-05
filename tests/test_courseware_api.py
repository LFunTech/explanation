import copy
import io
import json

import httpx
import pytest
from PIL import Image

from app.exam import Exam
from app.images import raster_data_url
from app.kb import KnowledgeBase
from app.main import create_app
from app.models import IllustratedLesson, JobRequest
from app.providers import GenerationError
from app.scope import generation_question
from app.store import digest, lesson_key
from app.teaching import package_prefix, teaching_target
from tests.test_core import setup
from tests.test_exam_grades import row, source
from tests.test_illustrated_lessons import lesson
from tests.test_teaching_targets import target_question


QUESTION_ID='756ac6cd-befa-4a40-9972-f0013f6f7444'


def request_data(**changes):
    data=dict(question_id=QUESTION_ID,target_grade='七年级',target_curriculum=[
        dict(knowledge_point='一元一次方程',grade='七年级',curriculum_requirements=['用等式性质解释变形'])],
        options=dict(voice=False,variant_count=0))
    data.update(changes)
    return data


def reject_local_lookup(*args,**kwargs):
    raise AssertionError('Generation must not query local curriculum')


@pytest.mark.asyncio
async def test_id_creation_resolves_original_images_and_replays_without_source_lookup(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);app=create_app(cfg);calls=[]
    image=io.BytesIO();Image.new('RGB',(20,20),'white').save(image,format='PNG')
    data_url=raster_data_url(image.getvalue())
    original=target_question(question_id='exam:public:'+QUESTION_ID,source='exam/public/question/'+QUESTION_ID,
        options=['A. 4','B. 5'],requires_images=True,answer='A',analysis='原题解析',target_curriculum=[])
    def load_question(self,question_id):calls.append(('question',question_id));return original.model_copy(deep=True)
    def load_images(self,question_id):calls.append(('images',question_id));return dict(images=[data_url])
    monkeypatch.setattr(Exam,'question',load_question);monkeypatch.setattr(Exam,'images',load_images)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        body=request_data();headers={'Idempotency-Key':'create-by-id'}
        first=await client.post('/api/courseware',json=body,headers=headers)
        assert first.status_code==202,first.text
        saved=first.json()['items'][0]['question']
        assert saved['question_id']=='exam:public:'+QUESTION_ID
        assert saved['stem']==original.stem and saved['options']==original.options
        assert saved['grade']=='九年级' and saved['target_grade']=='七年级'
        assert saved['answer']=='A' and saved['analysis']=='原题解析' and len(saved['images'])==1
        assert saved['target_semester']=='全学期'
        assert saved['target_curriculum'][0]['curriculum_requirements']==['用等式性质解释变形']
        monkeypatch.setattr(Exam,'question',reject_local_lookup)
        body.update(question_id='exam:public:'+QUESTION_ID,target_semester='全学期')
        replay=await client.post('/api/courseware',json=body,headers=headers)
        assert replay.status_code==202 and replay.json()['id']==first.json()['id']
        assert replay.json()['idempotent_replay'] and len(calls)==2
        body['target_semester']='下半学期'
        assert (await client.post('/api/courseware',json=body,headers=headers)).status_code==409


@pytest.mark.asyncio
async def test_semesters_are_separate_and_reach_model_manifest_and_storage(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);cfg.vector_database_url='postgresql://unavailable';app=create_app(cfg)
    monkeypatch.setattr(Exam,'question',lambda self,id:target_question(question_id=id))
    monkeypatch.setattr(KnowledgeBase,'connect',reject_local_lookup)
    monkeypatch.setattr(app.state.store,'resources',reject_local_lookup)
    captured=[]
    async def respond(instruction,data,*args,**kwargs):
        captured.append((instruction,copy.deepcopy(data)))
        return IllustratedLesson.model_validate(lesson())
    monkeypatch.setattr(app.state.engine.qwen,'json',respond)
    jobs=[]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        for semester in ['全学期','上半学期','下半学期']:
            body=request_data(target_semester=semester)
            response=await client.post('/api/courseware',json=body)
            assert response.status_code==202,response.text
            jobs.append(response.json()['id'])
        assert len(set(jobs))==len(app.state.store.list_lessons())==3
        duplicate=await client.post('/api/courseware',json=request_data(target_semester='下半学期'))
        assert duplicate.status_code==202 and duplicate.json()['id'] not in jobs
    for expected in ['全学期','上半学期','下半学期','下半学期']:
        item=app.state.store.claim();await app.state.engine.generate(item)
        saved=app.state.store.job(item['job_id'])['items'][0]
        assert saved['status']=='completed'
        manifest=app.state.store.package(saved['package_id'])
        assert manifest['teaching_target']['target_semester']==expected
        assert '/七年级/'+expected+'/' in package_prefix(cfg,manifest['question'],manifest['package_id'])
        assert captured[-1][1]['resources']==[] and captured[-1][1]['knowledge_resource_catalog']==[]
        assert captured[-1][1]['teaching_target']['target_semester']==expected
        assert 'target_semester' in captured[-1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('single_question',[False,True])
@pytest.mark.parametrize('with_curriculum',[False,True])
async def test_existing_creation_and_cached_retries_do_not_add_local_standards(tmp_path,monkeypatch,single_question,with_curriculum):
    _,_,cfg=setup(tmp_path);cfg.vector_database_url='postgresql://unavailable';app=create_app(cfg)
    monkeypatch.setattr(KnowledgeBase,'connect',reject_local_lookup)
    monkeypatch.setattr(app.state.store,'resources',reject_local_lookup)
    question=target_question()
    if not with_curriculum:question.target_curriculum=[]
    async def respond(instruction,data,*args,**kwargs):
        assert data['resources']==[] and data['knowledge_resource_catalog']==[]
        assert data['teaching_target']==teaching_target(question)
        return IllustratedLesson.model_validate(lesson())
    monkeypatch.setattr(app.state.engine.qwen,'json',respond)
    body=JobRequest(assignment_id='no-local-lookup',questions=[question],options=dict(voice=False,variant_count=0))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/jobs',params=dict(single_question=str(single_question).lower()),json=body.model_dump())
        assert response.status_code==202
    item_id=response.json()['items'][0]['id']
    app.state.store.context(item_id,dict(version=4,question_key=digest(generation_question(question)),
        resources=[dict(id='old-local-standard',kind='curriculum',content='旧的本地要求')]))
    await app.state.engine.generate(app.state.store.claim())
    assert app.state.store.job(response.json()['id'])['items'][0]['status']=='completed'
    with app.state.store.connect() as db:
        context=json.loads(db.execute('SELECT context_json FROM items WHERE id=?',(item_id,)).fetchone()[0])
    assert context['resources']==[] and context['version']>4


@pytest.mark.asyncio
async def test_id_creation_rejects_invalid_targets_missing_questions_and_images(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);app=create_app(cfg)
    monkeypatch.setattr(Exam,'question',reject_local_lookup)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        for change in [dict(target_semester='暑假'),dict(target_grade=''),dict(question_id='not-an-id')]:
            response=await client.post('/api/courseware',json=request_data(**change))
            assert response.status_code==422
        def missing(*args):raise GenerationError('exam_question_missing','题库题目不存在或已停用。')
        monkeypatch.setattr(Exam,'question',missing)
        assert (await client.post('/api/courseware',json=request_data())).status_code==404
        monkeypatch.setattr(Exam,'question',lambda self,id:target_question(question_id=id,requires_images=True))
        monkeypatch.setattr(Exam,'images',lambda *args:dict(images=[]))
        assert (await client.post('/api/courseware',json=request_data())).status_code==422
    assert not app.state.store.list_jobs()


def test_id_lookup_uses_exact_public_id_and_source_subject():
    record=row(QUESTION_ID,'G9');record['subject_code']='GB-PHY'
    record['stem']=json.dumps(dict(schema='exam.question.stem.v2',stem='比较电流。',options=[dict(label='A',content='相等')]))
    exam,queries=source([record]);question=exam.question(QUESTION_ID)
    assert question.subject=='物理' and question.options==['A. 相等']
    sql,params=queries[0]
    assert 'q.id::text=%s' in sql and params[2:4]==(QUESTION_ID,QUESTION_ID)
    assert 'tenant_kg.' not in sql and "q.status='ACTIVE'" in sql


def test_unspecified_semester_preserves_legacy_lesson_identity():
    question=target_question().model_dump();legacy=dict(question);legacy.pop('target_semester')
    assert lesson_key(question)==lesson_key(legacy)
    assert lesson_key({**question,'target_semester':'上半学期'})!=lesson_key(question)


@pytest.mark.asyncio
async def test_variant_lookup_still_uses_submitted_teaching_target(tmp_path,monkeypatch):
    from app.models import GenerationOptions
    store,engine,cfg=setup(tmp_path);cfg.exam_db_host='exam.test';cfg.vector_database_url='postgresql://unused'
    monkeypatch.setattr(store,'resources',reject_local_lookup)
    monkeypatch.setattr(KnowledgeBase,'connect',reject_local_lookup)
    def variants(self,question,limit):
        assert question.grades==['七年级'] and question.target_semester=='下半学期'
        assert question.knowledge_points==['一元一次方程','等式性质'] and limit==2
        return [dict(id='variant-1',kind='question')]
    monkeypatch.setattr(Exam,'variants',variants)
    result=await engine.resources(target_question(target_semester='下半学期'),GenerationOptions(variant_count=2))
    assert result==[dict(id='variant-1',kind='question')]


@pytest.mark.asyncio
async def test_public_creation_uses_existing_api_key_authentication(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);cfg.api_key='integration-test-key';app=create_app(cfg)
    calls=[]
    def question(self,id):calls.append(id);return target_question(question_id=id)
    monkeypatch.setattr(Exam,'question',question)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/courseware',json=request_data())
        assert response.status_code==401 and not calls
        response=await client.post('/api/courseware',json=request_data(),headers={'X-API-Key':'integration-test-key'})
        assert response.status_code==202 and calls==['exam:public:'+QUESTION_ID]


@pytest.mark.asyncio
async def test_stable_courseware_id_update_and_prompt_reach_generation(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);app=create_app(cfg)
    monkeypatch.setattr(Exam,'question',lambda self,id:target_question(question_id=id))
    captured=[]
    async def respond(instruction,data,*args,**kwargs):
        captured.append(copy.deepcopy(data))
        return IllustratedLesson.model_validate(lesson())
    monkeypatch.setattr(app.state.engine.qwen,'json',respond)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        created=(await client.post('/api/courseware',json=request_data(generation_requirements='先讲概念'))).json()
        cid=created['courseware_id'];url='/api/courseware/'+cid
        assert cid==created['items'][0]['id'] and created['result'] is None
        assert (await client.get(url)).json()['status']=='pending'
        assert (await client.patch(url,json={})).status_code==409
        await app.state.engine.generate(app.state.store.claim())
        first=(await client.get(url)).json()
        assert first['status']=='completed' and first['result']['package_player_url']
        assert captured[-1]['generation_requirements']=='先讲概念'
        first_package=first['items'][0]['package_id']
        body=dict(generation_requirements='每一步解释依据，变式配图',options=dict(depth='deep'))
        response=await client.patch(url,json=body,headers={'Idempotency-Key':'update-1'})
        assert response.status_code==202,response.text
        updated=response.json()
        assert updated['courseware_id']==cid and updated['revision']==2 and updated['progress']==0
        assert updated['result'] is None and updated['previous_result']==first['result']
        assert updated['request']['options']['voice'] is False
        assert updated['request']['options']['variant_count']==0
        replay=(await client.patch(url,json=body,headers={'Idempotency-Key':'update-1'})).json()
        assert replay['idempotent_replay'] and replay['revision']==2
        assert (await client.patch(url,json={},headers={'Idempotency-Key':'update-1'})).status_code==409
        await app.state.engine.generate(app.state.store.claim())
        finished=(await client.get(url)).json()
        assert finished['status']=='completed' and finished['courseware_id']==cid
        assert finished['previous_result'] is None and finished['revision']==2
        assert finished['items'][0]['package_id']!=first_package
        assert captured[-1]['generation_requirements']==body['generation_requirements'] and captured[-1]['depth']=='deep'
        manifest=app.state.store.package(finished['items'][0]['package_id'])
        assert manifest['courseware_id']==cid and manifest['generation_requirements']==body['generation_requirements']
        assert (await client.patch(url,json=dict(target_grade='八年级',generation_requirements=''))).status_code==202
        changed=(await client.get(url)).json()
        assert changed['items'][0]['question']['target_curriculum']==[]
        assert changed['generation_requirements']=='' and changed['courseware_id']==cid


@pytest.mark.asyncio
async def test_courseware_updates_validate_identity_and_do_not_mutate_other_lesson(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);app=create_app(cfg)
    monkeypatch.setattr(Exam,'question',lambda self,id:target_question(question_id=id))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        a=(await client.post('/api/courseware',json=request_data(target_curriculum=[]))).json()
        b=(await client.post('/api/courseware',json=request_data(target_grade='八年级',target_curriculum=[]))).json()
        url='/api/courseware/'+a['courseware_id']
        app.state.store.finish(a['courseware_id'],error=dict(code='example',message='test failure'))
        for body in [dict(target_grade=''),dict(target_semester='暑假'),dict(generation_requirements=None),dict(question_id=QUESTION_ID)]:
            assert (await client.patch(url,json=body)).status_code==422
        assert (await client.patch('/api/courseware/missing',json={})).status_code==404
        assert (await client.get('/api/courseware/missing')).status_code==404
        assert (await client.get(url)).json()['revision']==1
        assert app.state.store.job(b['id'])['revision']==1
        # Failed lessons can be explicitly regenerated, not only successful ones.
        assert (await client.patch(url,json=dict(target_grade='八年级'))).status_code==202
        assert app.state.store.job(b['id'])['revision']==1
        assert (await client.patch(url,json={})).status_code==409


@pytest.mark.asyncio
async def test_eighth_grade_creation_rejects_seventh_grade_requirements(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);app=create_app(cfg)
    monkeypatch.setattr(Exam,'question',lambda self,id:target_question(question_id=id))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/courseware',json=request_data(target_grade='八年级'))
        assert response.status_code==422 and '只能填写该年级' in response.json()['detail']
        assert not app.state.store.list_jobs()
        data=request_data(target_grade='八年级');data['target_curriculum'][0]['grade']='八年级'
        created=(await client.post('/api/courseware',json=data)).json()
        app.state.store.finish(created['courseware_id'],error=dict(code='test',message='failed'))
        response=await client.patch('/api/courseware/'+created['courseware_id'],json=dict(target_curriculum=request_data()['target_curriculum']))
        assert response.status_code==422 and app.state.store.job(created['id'])['revision']==1
