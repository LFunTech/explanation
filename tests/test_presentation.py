import copy
import json

import pytest

from app.question_layout import question_layout
from app.exporters import PRESENTATION_VERSION
from tests.test_core import setup,request


def choice_question():
    return dict(stem='在数轴上表示解集，正确的是（ ）',options=[],
        sub_questions=[dict(id='1',stem='A.\nB.\nC.\nD.',answer='C',analysis='端点实心，向右。')],
        images=['image-a','image-b','image-c','image-d'],question_type='单选题',
        source='exam/public/question/source-id')


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
