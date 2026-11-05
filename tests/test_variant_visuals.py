import copy

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.exporters import export_pptx
from app.knowledge_pages import ensure_knowledge_pages
from app.question_layout import variant_layout
from app.variant_visuals import bind_variant_visuals
from tests.test_illustrated_lessons import number_line
from app.diagrams import render_diagram


def test_same_figure_variant_reuses_real_image_in_layout_and_ppt(tmp_path):
    image=render_diagram(number_line())['png']
    question=dict(images=[image],subject='数学',grade='九年级')
    variants=[dict(stem='根据同一雷达图，哪个月份温差更大？\nA. 一月\nB. 二月',source='generated')]
    slides=[dict(id='v1',kind='变式',title='变式题1：温差',display='根据同一雷达图',narration='比较温差')]
    bind_variant_visuals(question,variants,slides)
    assert variants[0]['images']==[image]
    assert variant_layout(variants[0])['image_indices']==[0]
    assert slides[0]['variant_index']==0
    path=tmp_path/'lesson.pptx'
    export_pptx(path,slides,dict(question=question,variants=variants))
    assert any(s.shape_type==MSO_SHAPE_TYPE.PICTURE for s in Presentation(path).slides[0].shapes)
    before=copy.deepcopy((variants,slides));bind_variant_visuals(question,variants,slides)
    assert (variants,slides)==before


def test_independent_variants_never_borrow_an_unrelated_original():
    variants=[dict(stem='如图，新三角形的面积是多少？',source='generated'),
              dict(stem='根据同一雷达图作答',source='resource',source_image_indices=[0])]
    bind_variant_visuals(dict(images=['original']),variants,[])
    assert all(not v.get('images') for v in variants)


def test_explicit_indices_ignore_invalid_and_do_not_duplicate():
    variants=[dict(stem='观察题图',source_image_indices=[1,-1,False,1,99],source='generated')]
    bind_variant_visuals(dict(images=['first','second']),variants,[])
    assert variants[0]['images']==['second']
    assert variants[0]['source_image_indices']==[1]


def test_changed_resource_does_not_keep_wrong_diagram_or_recording():
    variants=[dict(source='resource',stem='甲乙丙中谁拿到了硬币？',knowledge_points=['逻辑推理'],analysis='逐项排除')]
    slides=[dict(kind='变式',title='变式题1：三角形旋转',display='如图，三角形旋转60度，计算角度。',
                 diagram=dict(png='unrelated',kind='geometry'),audio='old',audio_file='old.wav',cues=[1])]
    bind_variant_visuals(dict(images=['original']),variants,slides)
    assert slides[0]['display']==variants[0]['stem']
    assert slides[0]['title']=='变式题1：逻辑推理'
    assert not any(k in slides[0] for k in ['diagram','audio','audio_file','cues'])
    assert not variants[0].get('images')


def test_knowledge_pages_name_the_knowledge_and_fill_missing_before_solution():
    points=[dict(title='旋转性质',display='对应边相等',notes='说明旋转对应关系'),
            dict(title='逻辑推理',display='每步都有依据',why='从已知条件出发',application='由旋转找到相等关系',notes='说明每步依据')]
    pages=[dict(id='original',kind='原题'),dict(id='known',kind='知识',title='第一步：旋转',knowledge_indices=[0],audio='keep'),dict(id='solution',kind='解题')]
    result=ensure_knowledge_pages(pages,points)
    assert [p['id'] for p in result]==['original','known','knowledge-002','solution']
    assert result[1]['title']=='知识点1：旋转性质' and result[1]['audio']=='keep'
    assert '原理与条件：从已知条件出发' in result[2]['display']
    assert '用于本题：由旋转找到相等关系' in result[2]['display']
    assert ensure_knowledge_pages(result,points)==result
    assert pages[1]['title']=='第一步：旋转'


def test_variant_answer_slides_collapse_without_overwriting_answers_and_with_figures(tmp_path):
    variants=[dict(source='resource',stem='下列图形：①三角形；②长方形；③正方体；④圆；⑤圆锥；⑥圆柱。立体图形有几个？',answer='3',analysis='正方体、圆锥、圆柱都是立体图形。')]
    slides=[dict(id='main',kind='知识',title='概念',display='认识图形',narration='概念'),
            dict(id='v1',kind='知识',variant_index=0,title='变式题1',display=variants[0]['stem'],narration='提前泄露答案3'),
            dict(id='v1-answer',kind='解题',variant_index=0,title='变式题1解答',display='答案3',narration='答案3')]
    bind_variant_visuals({},variants,slides)
    assert [s['id'] for s in slides]==['main','v1']
    assert slides[1]['kind']=='变式' and '提前泄露' not in slides[1]['narration']
    assert variants[0]['answer']=='3' and variants[0]['analysis'].startswith('正方体')
    assert variants[0]['images'][0].startswith('data:image/png')
    assert slides[1]['diagram']['kind']=='shape_gallery'
    before=copy.deepcopy((slides,variants));bind_variant_visuals({},variants,slides)
    assert (slides,variants)==before
    path=tmp_path/'illustrated.pptx'
    export_pptx(path,slides,dict(question=dict(grade='七年级',subject='数学'),variants=variants))
    deck=Presentation(path)
    assert len(deck.slides)==2
    assert any(s.shape_type==MSO_SHAPE_TYPE.PICTURE for s in deck.slides[1].shapes)
    assert '答案：3' in deck.slides[1].notes_slide.notes_text_frame.text


def test_gallery_does_not_draw_the_missing_option_as_a_question_figure():
    from app.shape_gallery import enumerated_gallery
    assert enumerated_gallery('有一个球和圆柱，哪种没有出现？ A. 正方体 B. 圆锥') is None
    d=enumerated_gallery('观察①圆；②三角形。没有出现的是？ A. 圆锥 B. 正方体')
    assert [s['shape'] for s in d['shapes']]==['circle','triangle']
