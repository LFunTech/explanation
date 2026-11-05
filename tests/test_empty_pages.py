import copy
import json

from pptx import Presentation

from app.exporters import make_slides,write_package
from app.models import IllustratedLesson
from app.page_content import complete_page_content
from tests.test_illustrated_lessons import lesson


def test_narration_only_sections_are_recovered_in_model_and_exports(tmp_path):
    draft=lesson();draft['pitfalls']=[];draft['summary']=[]
    draft['pages'] += [dict(kind='知识',title='易错提醒',display='  ',narration='第一，确认端点；第二，检查方向。'),
                       dict(kind='总结',title='总结',narration='先确定边界，再判断端点是否包含。')]
    validated=IllustratedLesson.model_validate(draft).model_dump()
    assert validated['pages'][-2]['display'].strip()
    assert validated['pages'][-1]['display'].strip()
    assert validated['pages'][-2]['narration']==draft['pages'][-2]['narration']
    assert validated['pitfalls'] and validated['summary']
    q=dict(stem='解不等式',images=[],subject='数学',grade='七年级')
    slides=make_slides(q,validated)
    manifest=dict(question=q,title=validated['title'],variants=[],voice_mode='none',
                  reading={key:validated[key] for key in ['answer','knowledge_points','solution_steps','pitfalls','summary']})
    write_package(tmp_path,manifest,slides)
    saved=json.loads((tmp_path/'slides.json').read_text(encoding='utf-8'))
    assert all(s['display'].strip() for s in saved if s['title'] in {'易错提醒','总结'})
    ppt=Presentation(tmp_path/'courseware.pptx')
    text='\n'.join(s.text for page in ppt.slides for s in page.shapes if s.has_text_frame)
    assert '确认端点' in text and '判断端点是否包含' in text
    assert '第一，确认端点' in (tmp_path/'index.html').read_text(encoding='utf-8')


def test_section_display_takes_precedence_over_spoken_script_and_keeps_audio():
    pages=[dict(id='p1',kind='知识',title='易错提醒',display='',narration='很长的完整口语讲稿',audio='keep.wav'),
           dict(id='p2',kind='总结',title='总结',display='',narration='另一段完整台词')]
    original=copy.deepcopy(pages)
    output=complete_page_content(pages,dict(pitfalls=[dict(display='等号不能遗漏')],summary=['先移项','再检验']))
    assert output[0]['display']=='等号不能遗漏' and output[0]['audio']=='keep.wav'
    assert output[1]['display']=='先移项\n再检验'
    assert pages==original


def test_visual_pages_remain_but_empty_optional_cards_do_not_export():
    pages=[dict(kind='原题',title='原题',display=''),dict(kind='知识',title='看图',display='',diagram={'kind':'geometry'}),
           dict(kind='解题',title='流程',display='',solution_indices=[0]),dict(kind='总结',title='总结',display='',narration='')]
    result=complete_page_content(pages,{})
    assert [p['title'] for p in result]==['原题','看图','流程']
