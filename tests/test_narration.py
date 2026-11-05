import copy
import json

import httpx
import pytest

from app.config import Settings
from app.exporters import make_slides
from app.models import IllustratedLesson, Question, GenerationOptions, Review
from app.narration import narration_length
from app.providers import Qwen, GenerationError, response_format
from tests.test_illustrated_lessons import lesson


def long_lesson():
    draft=lesson()
    draft['pages'][0]['narration']='观察题中的条件。'*100
    draft['pages'][1]['narration']='根据等式判断边界。'*100
    draft['solution_steps'][0]['narration']='先移项再判断方向。'*100
    return draft


def test_full_script_is_exported_without_a_character_budget():
    draft=long_lesson();result=IllustratedLesson.model_validate(draft)
    actual=sum(len(p['narration']) for p in make_slides({},result.model_dump()))
    assert actual==narration_length(draft)>420
    assert result.solution_steps[0].narration==draft['solution_steps'][0]['narration']


def test_solution_page_summary_is_never_a_second_narration_source():
    draft=lesson();expected=narration_length(draft)
    draft['pages'][2]['narration']='不会朗读的旧摘要。'*1000
    result=IllustratedLesson.model_validate(draft)
    assert narration_length(result.model_dump())==expected
    assert result.pages[2].narration==result.solution_steps[0].narration


@pytest.mark.asyncio
async def test_long_script_generates_once_without_compression_or_review(tmp_path):
    draft=long_lesson();original=copy.deepcopy(draft);calls=[]
    qwen=Qwen(Settings(_env_file=None))
    async def generate(instruction,data,schema,images=(),progress=None):
        calls.append(schema)
        assert schema is IllustratedLesson
        return schema.model_validate(draft)
    qwen.json=generate
    result,report=await qwen.lesson(Question(question_id='unlimited',stem='判断数轴。'),[],
        GenerationOptions(variant_count=0),lambda *a:None,checkpoint=tmp_path/'draft.json')
    assert calls==[IllustratedLesson] and report['type']=='not_reviewed'
    assert result['solution_steps']==original['solution_steps']
    assert result['pages'][0]['narration']==original['pages'][0]['narration']


@pytest.mark.asyncio
async def test_legacy_budget_failure_resumes_full_script_without_an_llm_call(tmp_path):
    saved=tmp_path/'draft.json';qwen=Qwen(Settings(_env_file=None))
    async def generate(instruction,data,schema,images=(),progress=None):return schema.model_validate(long_lesson())
    qwen.json=generate
    args=(Question(question_id='resume-budget',stem='判断数轴。'),[],GenerationOptions(variant_count=0),lambda *a:None)
    original,_=await qwen.lesson(*args,checkpoint=saved)
    cached=json.loads(saved.read_text(encoding='utf-8'));cached['phase']='narration'
    saved.write_text(json.dumps(cached),encoding='utf-8')
    async def forbidden(*a,**kw):raise AssertionError('不得重新生成或精简已保存的台词')
    qwen.json=forbidden
    resumed,_=await qwen.lesson(*args,checkpoint=saved)
    assert resumed==original and narration_length(resumed)>420


@pytest.mark.asyncio
async def test_unparseable_output_has_no_automatic_content_repair_loop():
    qwen=Qwen(Settings(_env_file=None));calls=[]
    async def broken(*a,**kw):
        calls.append(1)
        raise GenerationError('qwen_structure','模型输出不是可解析的课件 JSON')
    qwen.json=broken
    with pytest.raises(GenerationError):
        await qwen.lesson(Question(question_id='bad-json',stem='生成课件。'),[],GenerationOptions(),lambda *a:None)
    assert len(calls)==1


def test_strict_schema_only_for_supported_text_models():
    assert response_format('qwen-plus',Review)=={'type':'json_object'}
    assert response_format('qwen3-vl-plus',Review,['image'])=={'type':'json_object'}
    assert response_format('qwen3.8-max',Review,['image'])=={'type':'json_object'}
    strict=response_format('qwen3.7-plus',Review)
    assert strict['type']=='json_schema' and strict['json_schema']['strict']


@pytest.mark.asyncio
async def test_json_accepts_full_narration_without_budget_context(monkeypatch):
    client=httpx.AsyncClient
    def handler(request):
        assert 'max_tokens' not in json.loads(request.content)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(long_lesson())}}]})
    monkeypatch.setattr('app.providers.httpx.AsyncClient',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    result=await Qwen(Settings(_env_file=None,dashscope_api_key='test')).json('JSON',{},IllustratedLesson)
    assert narration_length(result.model_dump())>420
