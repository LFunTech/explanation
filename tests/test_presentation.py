import copy
import json

import pytest

from app.question_layout import question_layout,variant_layout
from app.exporters import PRESENTATION_VERSION, presentation_slides
from tests.test_core import setup,request


def choice_question():
    return dict(stem='在数轴上表示解集，正确的是（ ）',options=[],
        sub_questions=[dict(id='1',stem='A.\nB.\nC.\nD.',answer='C',analysis='端点实心，向右。')],
        images=['image-a','image-b','image-c','image-d'],question_type='单选题',
        source='exam/public/question/source-id')


def test_legacy_variant_option_wrapper_is_not_a_subquestion_number():
    variant=dict(stem='下列图形中具有稳定性的是（ ）\n（1）A. 平行四边形\nB. 长方形\nC. 正方形\nD. 三角形')
    before=copy.deepcopy(variant);layout=variant_layout(variant)
    assert layout['stem']=='下列图形中具有稳定性的是（ ）'
    assert [c['label'] for c in layout['choices']]==list('ABCD')
    assert layout['choices'][-1]['text']=='三角形'
    assert variant==before
    real=dict(stem='（1）求三角形面积。\n（2）求四边形面积。')
    assert variant_layout(real)['stem']==real['stem']


def test_option_only_exam_wrapper_becomes_labeled_figures_without_changing_source():
    question=choice_question();before=copy.deepcopy(question)
    layout=question_layout(question)
    assert layout['sub_questions']==[]
    assert layout['image_choices']==[dict(label=label,image_index=i) for i,label in enumerate('ABCD')]
    assert layout['image_indices']==[]
    assert question==before


def test_real_subquestions_and_unmatched_images_are_not_inferred_as_choices():
    question=choice_question();question['sub_questions'][0]['stem']='先解不等式，再从 A. B. C. D. 中选择。'
    assert question_layout(question)['sub_questions']==question['sub_questions']
    assert not question_layout(question)['image_choices']
    question=choice_question();question['images'].append('diagram-in-stem')
    assert not question_layout(question)['image_choices']
    assert question_layout(question)['image_indices']==list(range(5))


@pytest.mark.parametrize('options',[
    'A. $x<1$\nB. $x>1$\nC. $x=1$\nD. $x=0$',
    'A．$x<1$  B．$x>1$  C．$x=1$  D．$x=0$',
    '（A）$x<1$\n（B）$x>1$\n（C）$x=1$\n（D）$x=0$',
])
def test_text_option_wrapper_preserves_formulas_and_becomes_choices(options):
    q=choice_question();q['images']=['stem-diagram'];q['sub_questions'][0]['stem']=options
    before=copy.deepcopy(q);layout=question_layout(q)
    assert layout['sub_questions']==[]
    assert [c['text'] for c in layout['choices']]==['$x<1$','$x>1$','$x=1$','$x=0$']
    assert [c['label'] for c in layout['choices']]==list('ABCD')
    assert layout['image_indices']==[0] and layout['image_choices']==[] and q==before


def test_genuine_subquestions_keep_their_own_options_and_numbering():
    q=choice_question();q['question_type']='解答题'
    q['sub_questions']=[dict(id='1',stem='判断符号。\nA. $x>0$\nB. $x<0$'),dict(id='2',stem='请证明第二问。')]
    layout=question_layout(q)
    assert not layout['choices']
    assert layout['sub_questions'][0]['stem']=='判断符号。'
    assert len(layout['sub_questions'][0]['choices'])==2
    assert layout['sub_questions'][1]==q['sub_questions'][1]


def test_options_inside_stem_do_not_parse_labels_inside_math():
    q=choice_question();q['sub_questions']=[];q['images']=[]
    q['stem']=r'选择正确的关系：$A. B. C.$'+'\nA. $x=1$\nB. $x=2$'
    layout=question_layout(q)
    assert layout['stem']==r'选择正确的关系：$A. B. C.$'
    assert layout['options']==['A. $x=1$','B. $x=2$']


def test_unlabelled_explicit_choices_get_one_label_each():
    q=choice_question();q['sub_questions']=[];q['images']=[]
    q['options']=['北偏东 $30^{\\circ}$','北偏西 $30^{\\circ}$']
    assert question_layout(q)['choices']==[dict(label='A',text=q['options'][0]),dict(label='B',text=q['options'][1])]


def test_legacy_original_page_uses_source_without_changing_audio_or_page_ids():
    slides=[dict(id='page-001',kind='导入'),dict(id='page-002',kind='原题',display='旧文本',audio='retained'),
            dict(id='page-003',kind='原题',image='source-image')]
    original=copy.deepcopy(slides)
    projected=presentation_slides(slides)
    assert projected[1]=={**slides[1],'original':True}
    assert projected[0]==slides[0] and projected[2]==slides[2] and slides==original


@pytest.mark.asyncio
async def test_question_preview_uses_same_lossless_layout(tmp_path):
    import httpx
    from app.main import create_app
    _,_,cfg=setup(tmp_path)
    q=choice_question();q['question_id']='preview'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(cfg)),base_url='http://test') as client:
        response=await client.post('/api/questions/layout',json=q)
    assert response.status_code==200
    assert response.json()==question_layout(q)


@pytest.mark.asyncio
async def test_restyle_preserves_reviewed_content_and_old_package(tmp_path):
    store,engine,_=setup(tmp_path)
    job,_=store.create_job(request());await engine.generate(store.claim())
    item=store.job(job)['items'][0];old=store.package(item['package_id'])
    path=tmp_path/'packages'/old['package_id'];html=(path/'index.html').read_bytes()
    audio=path/'audio';audio.mkdir();(audio/'retained.wav').write_bytes(b'original audio bytes')
    result=engine.restyle(old);new=tmp_path/'packages'/result['package_id']
    assert result['package_id']!=old['package_id']
    assert result['presentation']==dict(version=PRESENTATION_VERSION,derived_from=old['package_id'])
    assert result['question']==old['question'] and result['review']==old['review']
    assert result['reading']==old['reading'] and result['fingerprint']==old['fingerprint']
    assert (path/'index.html').read_bytes()==html
    assert (new/'slides.json').read_bytes()==(path/'slides.json').read_bytes()
    assert (new/'audio'/'retained.wav').read_bytes()==b'original audio bytes'
