import base64
import copy
import io
import json
import wave
import zipfile
from xml.etree import ElementTree as ET

import pytest
from PIL import Image
from pydantic import ValidationError

from app.diagrams import NumberLine, Cartesian, Geometry, render_diagram
from app.exporters import make_slides, plain_text, write_package
from app.models import IllustratedLesson, JobRequest, Question
from app.providers import bind_knowledge_sources,verify_variants
from tests.test_core import setup


def number_line():
    return dict(kind='number_line',minimum=-4,maximum=3,ticks=[-4,-3,-2,-1,0,1,2,3],
                intervals=[dict(start=-1,end=None,start_closed=True,end_closed=False,label='本题：x ≥ −1'),
                           dict(start=-1,end=None,start_closed=False,end_closed=False,label='对比：x > −1')],
                caption='等号决定是否包含 −1；右侧表示比 −1 大的数。',alt='第一行 −1 处实心点向右；第二行 −1 处空心点向右。')


def lesson():
    point=dict(title='理解解集',display=r'$x\geq-1$',notes='等号表示包含边界，大于表示数轴右侧。')
    pages=[dict(kind='原题',title='先看边界与方向',display='留意不等号是否含等号。',narration='这道题需要同时确定边界、端点与方向。'),
           dict(kind='知识',title='实心点表示包含边界',display='依据：等号允许取到边界。\n本题：−1 是解，用实心点表示。',narration='把负一代入原不等式得到零大于等于零，成立，因此边界包含在解集中。',knowledge_indices=[0],diagram=number_line()),
           dict(kind='解题',title='移项后确定方向',display=r'$x+1\geq0\Rightarrow x\geq-1$',narration='两边同时减去一，不等号方向不变，所以从负一向右表示所有解。',solution_indices=[0]),
           dict(kind='答案',title='用代入核对',display='选择 C。',narration='选实心点向右的图。代入负一和零均成立，代入负二不成立。')]
    for p in pages:p['visual_reason']='用数轴解释含等号的边界。' if p.get('diagram') else '本页使用原图或短公式。'
    return IllustratedLesson(title='理解数轴上的解集',objective='把不等式与数轴对应。',complexity='simple',
        knowledge_points=[{**point,'why':'包含边界时等式也成立。','application':'负一代入得零大于等于零。'}],
        solution_steps=[{**point,'narration':'两边减一可得边界为负一，包含边界并向右表示所有解。'}],answer='C',pitfalls=[point],summary=['边界为负一，实心点向右。'],pages=pages).model_dump()


def test_number_line_exact_endpoints_and_safe_labels():
    normalized=number_line();normalized['intervals'][0]['label']=r'$x\geq-1$'
    assert NumberLine.model_validate(normalized).intervals[0].label=='x≥-1'
    spec=number_line();spec['intervals'][0]['label']='<script>bad</script>'
    figure=render_diagram(spec)
    svg=base64.b64decode(figure['svg'].split(',',1)[1]);root=ET.fromstring(svg)
    ns={'s':'http://www.w3.org/2000/svg'}
    assert root.findall('s:script',ns)==[]
    assert b'&lt;script&gt;' in svg
    circles=root.findall('s:circle',ns)
    assert len(circles)==2
    expected=70+3/7*650
    assert all(abs(float(p.attrib['cx'])-expected)<1e-6 for p in circles)
    assert circles[0].attrib['fill']=='#287253' and circles[1].attrib['fill']=='#f4f8ed'
    with Image.open(io.BytesIO(base64.b64decode(figure['png'].split(',',1)[1]))) as image:
        assert image.size==(1600,800)
        assert len(image.getcolors(2_000_000))>2


def test_diagram_rejects_wrong_coordinates_and_endpoints():
    spec=number_line();spec['intervals'][0]['end_closed']=True
    with pytest.raises(ValidationError,match='无穷端点'):NumberLine.model_validate(spec)
    spec=number_line();spec['minimum']=float('nan')
    with pytest.raises(ValidationError):NumberLine.model_validate(spec)
    with pytest.raises(ValidationError,match='不在所给函数曲线上'):
        Cartesian.model_validate(dict(kind='cartesian',caption='直线',alt='直线',x_min=-1,x_max=3,y_min=-1,y_max=5,
            curves=[dict(coefficients=[0,2,0],label='y=2x')],points=[dict(x=1,y=3,label='错误点')]))
    with pytest.raises(ValidationError,match='引用无效'):
        Geometry.model_validate(dict(kind='geometry',caption='三角形',alt='三角形',vertices=[dict(id='A',x=1,y=1)],segments=[dict(start='A',end='B')]))


def test_lecture_limits_and_coverage_are_not_silently_truncated():
    draft=lesson();draft['pages']+=copy.deepcopy(draft['pages'][1:3])
    with pytest.raises(ValidationError,match='最多 5 页'):IllustratedLesson.model_validate(draft)
    draft=lesson();draft['pages'][1]['knowledge_indices']=[]
    with pytest.raises(ValidationError,match='覆盖所有'):IllustratedLesson.model_validate(draft)
    draft=lesson();draft['pages'][1]['narration']='解释'*220
    with pytest.raises(ValidationError,match='420'):IllustratedLesson.model_validate(draft)
    assert plain_text('$x\\geq-1$，$x\\leq2$')=='x大于等于-1，x小于等于2'


def test_knowledge_labels_cannot_become_curriculum_sources():
    draft=IllustratedLesson.model_validate(lesson())
    draft.knowledge_points[0].resource_ids=['a-knowledge-point-tag']
    bind_knowledge_sources(draft,[])
    assert draft.knowledge_points[0].resource_ids==[]
    draft.knowledge_points[0].resource_ids=['a-knowledge-point-tag']
    with pytest.raises(ValueError,match='本轮目录'):
        bind_knowledge_sources(draft,[dict(id='actual-curriculum',kind='curriculum')])


def test_generated_variant_requires_self_contained_question():
    from app.models import Variant
    draft=IllustratedLesson.model_validate(lesson())
    draft.variants=[Variant(stem='把 x<2 的解集表示在数轴上，正确的是（ ）',answer='B',analysis='选项B符合。')]
    with pytest.raises(ValueError,match='缺少完整选项'):verify_variants(draft,[])
    draft.variants=[Variant(stem='说明下列变形的依据：x+1≥0 得 x≥−1。',answer='两边同时减一，不等号方向不变。',analysis='依据不等式基本性质。')]
    verify_variants(draft,[])


@pytest.mark.asyncio
async def test_actual_audio_budget_rewrites_and_never_reuses_stale_audio(tmp_path):
    store,engine,cfg=setup(tmp_path);cfg.tts_mode='qwen'
    question=Question(question_id='line-test',stem=r'把 $x+1\geq0$ 的解集表示在数轴上。',difficulty='基础')
    req=JobRequest(assignment_id='illustration',questions=[question],options=dict(mode='qwen',variant_count=0))
    job,_=store.create_job(req)
    class FakeQwen:
        calls=0
        speech_calls=0
        async def lesson(self,question,resources,options,progress,feedback=None):
            self.calls+=1
            result=lesson()
            result['solution_steps']+=[dict(title='确定端点',display='−1 处画实心点。',notes='代入边界满足原式。',narration='边界负一满足原式，所以用实心点。'),
                                       dict(title='确定方向',display='向右延伸。',notes='数轴上右侧的数较大。',narration='向右表示所有比负一大的数。')]
            result['pages'][2]['solution_indices']=[0,1,2]
            if self.calls>1:
                assert '120' in feedback
                for p in result['pages']:p['narration']='精简讲解：'+p['narration']
                for step in result['solution_steps']:step['narration']='精简讲解：'+step['narration']
            return result,dict(passed=True)
        async def speech(self,text):
            self.speech_calls+=1
            raw=io.BytesIO()
            with wave.open(raw,'wb') as wav:
                wav.setparams((1,2,8000,0,'NONE','not compressed'))
                wav.writeframes(b'\0\0'*8000*(35 if self.calls==1 else 20))
            return raw.getvalue()
    engine.qwen=FakeQwen()
    await engine.generate(store.claim())
    item=store.job(job)['items'][0];assert item['status']=='completed'
    package=tmp_path/'packages'/item['package_id']
    manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    assert engine.qwen.calls==2 and engine.qwen.speech_calls==12
    assert manifest['quality']['audio_duration_seconds']==120
    assert manifest['quality']['diagram_count']==1 and manifest['slide_count']==4
    assert len(list((package/'audio').glob('*.wav')))==4
    slides=json.loads((package/'slides.json').read_text(encoding='utf-8'))
    assert slides[2]['cues']==[dict(node=i,start_seconds=i*20,end_seconds=(i+1)*20) for i in range(3)]
    with zipfile.ZipFile(package/'package.zip') as archive:
        assert 'figures/page-002.svg' in archive.namelist()
        assert 'figures/page-002.png' in archive.namelist()
        assert len([n for n in archive.namelist() if n.endswith('.wav')])==4
    with zipfile.ZipFile(package/'courseware.pptx') as archive:
        assert any(n.startswith('ppt/media/') for n in archive.namelist())
    assert manifest['reading']['knowledge_points'][0]['why']
    assert [{k:v for k,v in p.items() if k!='narration'} for p in manifest['reading']['solution_steps'][:1]]==[
        {k:v for k,v in p.items() if k!='narration'} for p in lesson()['solution_steps']]
