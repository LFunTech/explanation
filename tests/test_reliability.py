import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.diagrams import NumberLine, Geometry, render_diagram
from app.models import Question,GenerationOptions,Review
from app.providers import Qwen,GenerationError,post_with_retry
from app.scope import scope_matches,validate_document_metadata
from tests.test_illustrated_lessons import lesson,number_line


def test_missing_ticks_added_without_changing_interval():
    value=number_line();value['ticks']=[-4,0,3]
    result=NumberLine.model_validate(value)
    assert result.ticks==[-4,-1,0,3]
    assert result.intervals[0].start==-1 and result.intervals[0].start_closed
    value['intervals'][0]['end']=-2
    with pytest.raises(ValidationError,match='start='):
        NumberLine.model_validate(value)


def test_negative_geometry_keeps_angles_and_circles_in_canvas():
    import base64
    from xml.etree import ElementTree as ET
    spec=dict(kind='geometry',caption='方位示意',alt='B 在原点，A 向东北，C 向西北。',
        vertices=[dict(id='B',x=0,y=0),dict(id='A',x=4,y=3),dict(id='C',x=-3,y=4)],
        segments=[dict(start='B',end='A'),dict(start='B',end='C')],circles=[dict(center='B',radius=1)])
    assert Geometry.model_validate(spec).vertices[2].x==-3
    svg=ET.fromstring(base64.b64decode(render_diagram(spec)['svg'].split(',')[1]))
    ns={'s':'http://www.w3.org/2000/svg'}
    circles=svg.findall('s:circle',ns)
    points=[(float(c.attrib['cx']),float(c.attrib['cy'])) for c in circles[-3:]]
    b,a,c=points
    assert abs((a[0]-b[0])*(c[0]-b[0])+(a[1]-b[1])*(c[1]-b[1]))<1e-5
    assert all(0<x<800 and 0<y<400 for x,y in points)


@pytest.mark.asyncio
async def test_transient_retry_does_not_retry_authentication(monkeypatch):
    monkeypatch.setattr('app.providers.asyncio.sleep',lambda _:noop())
    calls=[];stages=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:raise httpx.ReadTimeout('temporary')
        return httpx.Response(200,json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await post_with_retry(client,'https://example.test',progress=stages.append)
    assert result.status_code==200 and len(calls)==2 and '自动重试' in stages[0]
    calls.clear()
    def unauthorized(request):
        calls.append(request);return httpx.Response(401)
    async with httpx.AsyncClient(transport=httpx.MockTransport(unauthorized)) as client:
        assert (await post_with_retry(client,'https://example.test')).status_code==401
    assert len(calls)==1


async def noop():pass


@pytest.mark.asyncio
async def test_review_timeout_resumes_saved_candidate(tmp_path):
    from app.models import IllustratedLesson
    qwen=Qwen(Settings(_env_file=None));calls=[]
    draft=lesson();saved=tmp_path/'draft.json'
    async def fake(instruction,data,schema,images=(),progress=None):
        calls.append(schema)
        if schema is IllustratedLesson:return IllustratedLesson.model_validate(draft)
        raise GenerationError('qwen_timeout','超时')
    qwen.json=fake
    question=Question(question_id='resume',stem='解不等式 x+1≥0。')
    opts=GenerationOptions(variant_count=0)
    with pytest.raises(GenerationError):
        await qwen.lesson(question,[],opts,lambda *a:None,checkpoint=saved)
    assert json.loads(saved.read_text(encoding='utf-8'))['phase']=='review'
    async def review(instruction,data,schema,images=(),progress=None):
        calls.append(schema)
        assert schema is Review
        return Review(checks={k:dict(reason='已核对',errors=[]) for k in ['accuracy','grounding','coverage','pedagogy','variants']})
    qwen.json=review
    _,report=await qwen.lesson(question,[],opts,lambda *a:None,checkpoint=saved)
    assert report['passed'] and calls.count(IllustratedLesson)==1


def test_curriculum_and_textbook_scope_are_separate():
    textbook=dict(title='教材',subject='数学',source='授权资料',source_type='textbook',version='2024',grades=['六年级'],
        school_system='五四制',publisher='上海教育出版社',textbook_version='2024修订',volume='上册',curriculum_version='2022')
    meta=validate_document_metadata(textbook)
    assert scope_matches(meta,meta,'textbook')
    for key,value in [('publisher','其他出版社'),('textbook_version','旧版'),('volume','下册'),('school_system','六三制'),('curriculum_version','2011')]:
        assert not scope_matches(meta,{**meta,key:value},'textbook')
    assert not scope_matches(meta,{},'textbook')
    with pytest.raises(ValueError,match='教材须明确'):
        validate_document_metadata({**textbook,'grades':[]})
    assert scope_matches({'version':'2022'},{'curriculum_version':'2022'},'curriculum')
    assert not scope_matches({'version':'2011'},{'curriculum_version':'2022'},'curriculum')
