import copy
import json

import httpx
import pytest
from pydantic import ValidationError

from app.main import create_app
from app.models import GenerationOptions, IllustratedLesson, JobRequest, Question
from app.providers import Qwen
from app.scope import generation_question
from app.teaching import package_prefix, teaching_question, teaching_target
from tests.test_core import setup
from tests.test_illustrated_lessons import lesson


def target_question(**changes):
    data=dict(question_id='target-example',stem='解方程：2x+3=11。',grade='九年级',
        knowledge_points=['原题标签'],knowledge_point_ids=['source-id'],target_grade='七年级',
        target_curriculum=[dict(knowledge_point='一元一次方程',curriculum_requirements=['用等式性质求解，不使用函数方法']),
            dict(knowledge_point='等式性质',curriculum_requirements=['说明每一步变形的依据'])])
    data.update(changes)
    return Question.model_validate(data)


def test_target_curriculum_keeps_associations_and_ignores_source_classification():
    source=target_question();effective=teaching_question(source)
    assert source.grade=='九年级' and source.knowledge_points==['原题标签']
    assert effective.grade=='七年级' and effective.grades==['七年级'] and effective.source_grades==[]
    assert effective.knowledge_points==['一元一次方程','等式性质'] and effective.knowledge_point_ids==[]
    target=teaching_target(source)
    assert target['target_curriculum'][1]['curriculum_requirements']==['说明每一步变形的依据']
    changed=source.model_copy(update=dict(grade='高一',grades=['高一'],source_grades=['高一'],
        knowledge_points=['另一个原题标签'],knowledge_point_ids=['different-source-id']))
    assert generation_question(source)==generation_question(changed)
    assert 'target_school_year' not in source.model_dump()


def test_empty_curriculum_falls_back_to_source_points_but_not_source_grade():
    question=target_question(target_curriculum=[])
    target=teaching_target(question)
    assert target['knowledge_point_source']=='question'
    assert target['target_curriculum'][0]['knowledge_point']=='原题标签'
    assert teaching_question(question).knowledge_point_ids==['source-id']
    assert teaching_question(question).grade=='七年级'
    question.target_grade=''
    assert teaching_question(question).grade==''


def test_point_grades_are_normalized_and_independent_from_target_and_source():
    question=target_question(target_grade='八年级',target_curriculum=[dict(knowledge_point='等式性质',
        grade='G7',curriculum_requirements=['解释等式变形'])])
    point=question.target_curriculum[0]
    assert point.grade=='七年级' and point.grade_source=='manual'
    assert question.grade=='九年级' and teaching_question(question).grade=='八年级'
    original=generation_question(question)
    point.grade='八年级'
    assert generation_question(question)!=original


def test_target_metadata_does_not_block_six_points_and_ids_are_not_serialized():
    points=[dict(knowledge_point=f'知识点{i}',curriculum_requirements=[f'要求{i}']) for i in range(1,7)]
    points[2].update(knowledge_point_id='',confidence=0.9,explanation='附加说明',grades=['七年级'])
    question=target_question(target_curriculum=points)
    serialized=question.model_dump()['target_curriculum']
    assert len(serialized)==6
    assert serialized[2]['knowledge_point']=='知识点3'
    assert serialized[2]['curriculum_requirements']==['要求3']
    assert serialized[2]['grade']=='七年级' and 'grades' not in serialized[2]
    assert not {'knowledge_point_id','confidence','explanation'}.intersection(serialized[2])
    assert len(teaching_target(question)['target_curriculum'])==6
    from app.models import TargetKnowledgePoint
    schema=TargetKnowledgePoint.model_json_schema()
    assert 'knowledge_point_id' not in schema['properties'] and schema['additionalProperties'] is False
    assert schema['properties']['grade']['type']=='string' and 'grades' not in schema['properties']


def test_legacy_multi_grade_is_not_copied_to_separate_standards():
    question=target_question(target_curriculum=[dict(knowledge_point='逻辑推理与论证',
        grades=['七年级','八年级'],grade_source='llm',curriculum_requirements=['旧的共用要求'])])
    point=question.target_curriculum[0]
    assert point.grade=='' and point.grade_source=='unknown'
    assert point.curriculum_requirements==['旧的共用要求']
    assert len(question.target_curriculum)==1 and 'grades' not in point.model_dump()


def test_same_point_different_grades_keep_distinct_requirements():
    question=target_question(target_curriculum=[dict(knowledge_point='逻辑推理与论证',grade='七年级',curriculum_requirements=['依据给定条件说明判断']),
        dict(knowledge_point='逻辑推理与论证',grade='八年级',curriculum_requirements=['区分条件和结论，书写完整论证'])])
    targets=teaching_target(question)['target_curriculum']
    assert len(targets)==2
    assert targets[0]['grade']=='七年级' and targets[1]['grade']=='八年级'
    assert targets[0]['curriculum_requirements']!=targets[1]['curriculum_requirements']
    assert teaching_question(question).knowledge_points==['逻辑推理与论证']


@pytest.mark.asyncio
async def test_generation_accepts_legacy_target_metadata_and_saves_clean_points(tmp_path):
    _,_,cfg=setup(tmp_path);app=create_app(cfg)
    body=JobRequest(assignment_id='legacy-target',questions=[target_question()]).model_dump()
    body['questions'][0]['target_curriculum'][0].update(knowledge_point_id='',annotation='旧版附加字段')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/jobs?single_question=true',json=body)
        assert response.status_code==202
        point=response.json()['items'][0]['question']['target_curriculum'][0]
        assert 'knowledge_point_id' not in point and 'annotation' not in point
        assert point['curriculum_requirements']==body['questions'][0]['target_curriculum'][0]['curriculum_requirements']
        home=await client.get('/')
        js=await client.get('/static/app.js')
        assert home.headers['cache-control']=='no-cache'
        assert js.headers['cache-control']=='no-cache'


@pytest.mark.parametrize('value',[[{'curriculum_requirements':['描述']}],[{'knowledge_point':'方程','curriculum_requirements':'不是数组'}],['一元一次方程']])
def test_curriculum_rejects_ambiguous_mapping(value):
    with pytest.raises(ValidationError):target_question(target_curriculum=value)


@pytest.mark.asyncio
async def test_api_requires_independent_target_and_preserves_curriculum_json(tmp_path):
    _,_,cfg=setup(tmp_path);app=create_app(cfg)
    question=target_question()
    question.target_curriculum[0].grade='七年级';question.target_curriculum[0].grade_source='manual'
    body=JobRequest(assignment_id='target-api',questions=[question]).model_dump()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        missing=copy.deepcopy(body);missing['questions'][0].pop('target_grade')
        response=await client.post('/api/jobs?single_question=true',json=missing)
        assert response.status_code==422 and '目标年级' in response.json()['detail']
        response=await client.post('/api/jobs?single_question=true',json=body)
        assert response.status_code==202
        stored=response.json()['items'][0]['question']
        assert stored['grade']=='九年级' and stored['target_grade']=='七年级'
        assert stored['target_curriculum']==question.model_dump()['target_curriculum']


@pytest.mark.asyncio
async def test_prompt_and_illustrations_follow_target_grade_and_linked_standards(tmp_path,monkeypatch):
    _,_,cfg=setup(tmp_path);cfg.qwen_independent_review=False
    provider=Qwen(cfg);captured={}
    async def respond(instruction,data,*args,**kwargs):
        captured.update(instruction=instruction,data=data)
        return IllustratedLesson.model_validate(lesson())
    monkeypatch.setattr(provider,'json',respond)
    question=target_question(target_grade='一年级')
    await provider.lesson(question,[],GenerationOptions(voice=False,variant_count=0),lambda *a:None)
    data=captured['data']
    assert data['question']['grade']=='一年级' and data['visual_guidance']['grades']==['一年级']
    assert data['question']['knowledge_points']==['一元一次方程','等式性质']
    assert data['teaching_target']['target_curriculum']==question.model_dump()['target_curriculum']
    assert '不用数轴' in data['visual_guidance']['approach']
    assert '一一关联' in captured['instruction'] and '不暗中引入' in captured['instruction']


@pytest.mark.asyncio
async def test_changed_targets_invalidate_cached_content_and_partition_storage(tmp_path,monkeypatch):
    store,engine,cfg=setup(tmp_path);cfg.s3_bucket='exam';cfg.s3_prefix='explanations'
    captured=[];uploads=[]
    class FakeQwen:
        async def lesson(self,question,*args,**kwargs):
            captured.append(question.model_dump())
            return lesson(),dict(type='not_reviewed',passed=None)
    engine.qwen=FakeQwen()
    async def publish(directory,prefix,*args):uploads.append(prefix)
    monkeypatch.setattr(engine,'publish',publish)
    async def generate(question):
        request=JobRequest(assignment_id='target-cache',questions=[question],options=dict(voice=False,variant_count=0))
        job,_=store.create_job(request);await engine.generate(store.claim())
        item=store.job(job)['items'][0]
        return item,store.package(item['package_id'])
    first,manifest=await generate(target_question())
    repeated,_=await generate(target_question())
    assert repeated['reused'] and repeated['package_id']==first['package_id'] and len(captured)==1
    changed=target_question();changed.target_curriculum[0].curriculum_requirements=['只能用等式两边同加同减和同乘同除解释']
    second,_=await generate(changed)
    third,last=await generate(target_question(target_grade='八年级'))
    assert len({first['package_id'],second['package_id'],third['package_id']})==3
    assert uploads[0].startswith('explanations/七年级/') and uploads[-1].startswith('explanations/八年级/')
    assert manifest['question']['grade']=='九年级' and manifest['teaching_target']['target_grade']=='七年级'
    assert last['storage']['prefix']==package_prefix(cfg,last['question'],third['package_id'])
    assert captured[-1]['grades']==['八年级'] and captured[-1]['knowledge_points']==['一元一次方程','等式性质']
    stored=json.loads((tmp_path/'packages'/third['package_id']/'manifest.json').read_text(encoding='utf-8'))
    assert stored['teaching_target']==last['teaching_target']
