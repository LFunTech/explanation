import copy
import json
import zipfile

import pytest

from app.config import Settings
from app.diagrams import Cartesian, Curve, render_diagram
from app.models import GenerationOptions, IllustratedLesson, JobRequest, Question
from app.providers import GenerationError, Qwen
from tests.test_core import setup
from tests.test_illustrated_lessons import lesson


def test_linear_curve_uses_named_parameters_and_normalizes_legacy_order():
    assert Curve(slope=1,intercept=4,label='y=x+4').coefficients==[0,1,4]
    assert Curve(coefficients=[1,4,0],label='AB: y = x + 4').coefficients==[0,1,4]
    assert Curve(coefficients=[-.5,1,0],label='CD: y=-½x+1').coefficients==[0,-.5,1]
    assert Curve(coefficients=[1,4,0],label='y=x²+4x').coefficients==[1,4,0]
    assert Curve(coefficients=[0,1,4],label='y=x+4').coefficients==[0,1,4]


def test_free_point_is_rendered_without_forcing_it_onto_a_curve():
    figure=Cartesian(kind='cartesian',caption='直线与动点',alt='独立的动点M',
        x_min=-2,x_max=2,y_min=-1,y_max=3,
        curves=[dict(slope=1,intercept=4,label='y=x+4')],points=[dict(x=0,y=-2,label='M')])
    assert figure.points[0].y==-2 and figure.y_min<=-2
    assert render_diagram(figure.model_dump())['svg'].startswith('data:image/svg+xml;base64,')


@pytest.mark.asyncio
async def test_long_display_and_free_points_do_not_trigger_generation_retries():
    draft=lesson()
    draft['solution_steps'][0]['display']='根据条件逐步计算，保留完整公式与理由。'*12
    draft['pages'][1]['display']='完整知识说明。'*40
    draft['pages'][1]['diagram']=dict(kind='cartesian',caption='直线与动点',alt='独立动点M',
        x_min=-2,x_max=2,y_min=-2,y_max=5,
        curves=[dict(slope=1,intercept=4,label='y=x+4')],points=[dict(x=0,y=-2,label='M')])
    original=copy.deepcopy(draft);calls=[]
    qwen=Qwen(Settings(_env_file=None))
    async def generate(instruction,data,schema,images=(),progress=None):
        calls.append(schema)
        assert schema is IllustratedLesson
        return schema.model_validate(draft)
    qwen.json=generate
    result,report=await qwen.lesson(Question(question_id='no-content-gates',stem='直接生成课件。'),[],
        GenerationOptions(variant_count=0),lambda *a:None)
    assert calls==[IllustratedLesson] and report['passed'] is None
    assert result['solution_steps'][0]['display']==original['solution_steps'][0]['display']
    assert result['pages'][1]['display']==original['pages'][1]['display']
    assert result['pages'][1]['diagram']['points'][0]['y']==-2


@pytest.mark.asyncio
async def test_long_lesson_exports_all_pages_with_optional_bad_figure(tmp_path):
    store,engine,_=setup(tmp_path)
    draft=lesson()
    draft['pages']+=copy.deepcopy(draft['pages'][1:3])*6
    draft['pages'][0]['narration']='完整台词不会因为超过四十五字而被截断。'*100
    draft['pages'][1]['diagram']={'kind':'unsupported','caption':'无法绘制的可选配图'}
    validated=IllustratedLesson.model_validate(draft)
    assert validated.pages[1].diagram is None
    class FakeQwen:
        async def lesson(self,*args,**kwargs):
            return validated.model_dump(),dict(type='not_reviewed',passed=None)
    engine.qwen=FakeQwen()
    job,_=store.create_job(JobRequest(assignment_id='long-ppt',
        questions=[Question(question_id='long-ppt',stem='保留全部讲解内容。',target_grade='七年级')],
        options=dict(mode='qwen',voice=False,variant_count=0)))
    await engine.generate(store.claim())
    item=store.job(job)['items'][0]
    assert item['status']=='completed'
    package=tmp_path/'packages'/item['package_id']
    slides=json.loads((package/'slides.json').read_text(encoding='utf-8'))
    assert len(slides)==16
    assert slides[0]['narration']==draft['pages'][0]['narration']
    with zipfile.ZipFile(package/'courseware.pptx') as archive:
        pages=[p for p in archive.namelist() if p.startswith('ppt/slides/slide') and p.endswith('.xml')]
        assert len(pages)==16
    assert (package/'package.zip').is_file()


@pytest.mark.asyncio
async def test_speech_failure_still_exports_ppt_with_browser_narration(tmp_path):
    store,engine,cfg=setup(tmp_path);cfg.tts_mode='qwen'
    class FakeQwen:
        async def lesson(self,*args,**kwargs):
            return lesson(),dict(type='not_reviewed',passed=None)
        async def speech(self,text):
            raise GenerationError('tts_http','语音接口暂不可用')
    engine.qwen=FakeQwen()
    job,_=store.create_job(JobRequest(assignment_id='voice-fallback',
        questions=[Question(question_id='voice-fallback',stem='语音失败仍生成课件。',target_grade='七年级')],
        options=dict(mode='qwen',voice=True,variant_count=0)))
    await engine.generate(store.claim())
    item=store.job(job)['items'][0]
    assert item['status']=='completed'
    package=tmp_path/'packages'/item['package_id']
    manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['voice_mode']=='browser'
    assert (package/'courseware.pptx').is_file() and (package/'package.zip').is_file()
