import base64
import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from latex2mathml.converter import convert
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

from .config import ROOT
from .store import dump


def paginate(text, capacity=330):
    """保持公式原子性，按段落/句子分组；不截字丢内容。"""
    tokens = re.findall(r'\$\$[\s\S]*?\$\$|\$[^$\n]+\$|[^$]+|\$', text)
    pieces = []
    for token in tokens:
        if token.startswith('$'):
            pieces.append(token)
        else:
            pieces.extend(filter(None, re.split(r'(?<=[。；\n])', token)))
    pages, current = [], ''
    for piece in pieces:
        if len(piece) > capacity and not piece.startswith('$'):
            # 超长无标点题干也完整保留。
            fragments = [piece[i:i+capacity] for i in range(0, len(piece), capacity)]
        else:
            fragments = [piece]
        for fragment in fragments:
            if current and len(current)+len(fragment) > capacity:
                pages.append(current)
                current = ''
            current += fragment
    if current:
        pages.append(current)
    return pages or ['']


def make_slides(question, lesson):
    slides = []

    def add(kind, title, display, notes='', **extra):
        pages = paginate(display)
        for index, page in enumerate(pages):
            suffix = f' · {index+1}/{len(pages)}' if len(pages) > 1 else ''
            note = notes if index == len(pages)-1 else ''
            slides.append(dict(id=f'page-{len(slides)+1:03}', kind=kind, title=title+suffix,
                               display=page, notes=note, narration=plain_text(page+'\n'+note), **extra))

    add('导入', lesson['title'], lesson['objective'], '本节课我们围绕这道题，先理解相关知识，再完成解题与迁移练习。')
    original = question['stem'] + ''.join('\n'+v for v in question['options'])
    original += ''.join('\n（'+q['id']+'）'+q['stem'] for q in question['sub_questions'])
    add('原题', '一起读题', original)
    for index, image in enumerate(question['images']):
        add('原题', f'题目配图 {index+1}', '结合题干观察图片中的条件与关系。', image=image)
    for point in lesson['knowledge_points']:
        add('知识', point['title'], point['display'], point['notes'])
    for point in lesson['solution_steps']:
        add('解题', point['title'], point['display'], point['notes'])
    add('答案', '回到问题，给出答案', lesson['answer'])
    for point in lesson['pitfalls']:
        add('易错', point['title'], point['display'], point['notes'])
    add('总结', '把解题方法带走', '\n'.join('• '+x for x in lesson['summary']))
    for index, variant in enumerate(lesson['variants']):
        label = '题库变式' if variant['source'] == 'resource' else '自拟变式'
        add('变式', f'举一反三 · {index+1}', variant['stem'], variant_source=label, question_id=variant['question_id'])
        add('变式解析', f'变式 {index+1} · 思路与答案', variant['answer'], variant['analysis'])
    return slides


def plain_text(text):
    text = re.sub(r'\\(?:mathrm|text|operatorname)\{([^{}]*)\}', r'\1', text)
    text = re.sub(r'\\frac\{([^{}]*)\}\{([^{}]*)\}', r'(\1) 除以 (\2)', text)
    for key, value in {'\\times':'乘以','\\div':'除以','\\ne':'不等于','\\le':'小于等于','\\ge':'大于等于','\\Omega':'欧姆','\\,':'','^2':'的平方','^3':'的三次方'}.items():
        text = text.replace(key, value)
    return re.sub(r'[$#*_{}]', '', text)


def inline_math_assets():
    root = ROOT / 'web/vendor/katex'
    css = (root/'katex.min.css').read_text(encoding='utf-8')
    def font(match):
        file = re.search(r'url\(([^)]+\.woff2)\)', match.group(1))
        if not file:
            return match.group(0)
        value = base64.b64encode((root/file.group(1)).read_bytes()).decode('ascii')
        return 'src:url(data:font/woff2;base64,'+value+') format("woff2");'
    css = re.sub(r'src:([^;]+);', font, css)
    return css, (root/'katex.min.js').read_text(encoding='utf-8'), (root/'contrib/auto-render.min.js').read_text(encoding='utf-8')


def html_package(manifest, slides):
    css, katex, auto = inline_math_assets()
    template = (ROOT/'web/player.html').read_text(encoding='utf-8')
    player_css = (ROOT/'web/player.css').read_text(encoding='utf-8')
    player_js = (ROOT/'web/player.js').read_text(encoding='utf-8')
    value = dump(dict(manifest=manifest, slides=slides)).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    replacements={'MATH_CSS':css,'PLAYER_CSS':player_css,'MATH_JS':katex,'MATH_AUTO':auto,
                  'LESSON_DATA':'window.COURSEWARE='+value+';','PLAYER_JS':player_js}
    # 一次替换模板，题目正文中的占位符不会被当成代码再次展开。
    return re.sub(r'/\*('+'|'.join(replacements)+r')\*/',lambda m:replacements[m.group(1)],template)


MATH_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'


def math_element(name, *children):
    element = etree.Element('{'+MATH_NS+'}'+name)
    for child in children:
        element.append(child)
    return element


def mathml_to_office(node):
    name = node.tag.rsplit('}', 1)[-1]
    children = list(node)
    if name in {'mi','mn','mo','mtext','ms'}:
        text = math_element('t'); text.text = node.text or ''
        return math_element('r', text)
    if name == 'mfrac' and len(children) == 2:
        return math_element('f', math_element('num', mathml_to_office(children[0])), math_element('den', mathml_to_office(children[1])))
    if name in {'msup','msub','msubsup'}:
        tag = {'msup':'sSup','msub':'sSub','msubsup':'sSubSup'}[name]
        fields = ['e', 'sub', 'sup'] if name == 'msubsup' else ['e', 'sup' if name == 'msup' else 'sub']
        return math_element(tag, *[math_element(f, mathml_to_office(c)) for f,c in zip(fields,children)])
    if name == 'msqrt':
        return math_element('rad', math_element('deg'), math_element('e', *[mathml_to_office(c) for c in children]))
    content=math_element('e', *[mathml_to_office(c) for c in children])
    return content if name=='math' else math_element('box',content)


def add_rich_text(paragraph, text, size, color):
    for part in re.split(r'(\$\$[\s\S]*?\$\$|\$[^$\n]+\$)', text):
        if part.startswith('$') and part.endswith('$'):
            try:
                math = mathml_to_office(ET.fromstring(convert(part.strip('$'))))
                container = etree.Element('{http://schemas.microsoft.com/office/drawing/2010/main}m', nsmap={'a14':'http://schemas.microsoft.com/office/drawing/2010/main','m':MATH_NS})
                formula = math_element('oMath')
                for child in list(math):
                    formula.append(child)
                container.append(formula)
                paragraph._p.append(container)
                continue
            except Exception:
                # 仍保留完整公式原稿，兼容未知的 MathML 表达。
                part = plain_text(part)
        run = paragraph.add_run()
        run.text = part.replace('**', '')
        run.font.size = Pt(size); run.font.name = 'Microsoft YaHei'
        run.font.color.rgb = RGBColor.from_string(color)


def export_pptx(path, slides, manifest):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for index, page in enumerate(slides):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        slide.background.fill.solid(); slide.background.fill.fore_color.rgb = RGBColor.from_string('F7F8F3')
        for x,y,w,h,text,size,color in [(.65,.45,11,.4, page['kind']+'  /  '+manifest['question']['subject'],13,'327762'),
                                       (.65,1.05,12,1.1,page['title'],29,'183C32'),
                                       (.65,6.95,11,.3,manifest['question']['grade']+' · '+manifest['question']['region'],11,'6B7E75'),
                                       (12,6.95,.6,.3,f'{index+1:02}',11,'327762')]:
            box=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h))
            add_rich_text(box.text_frame.paragraphs[0],text,size,color)
        if page.get('image'):
            raw=base64.b64decode(page['image'].split(',',1)[1])
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as image:
                width,height=image.size
            factor=min(11/width,4.4/height)
            slide.shapes.add_picture(io.BytesIO(raw),Inches((13.333-width*factor)/2),Inches(2.15),width=Inches(width*factor),height=Inches(height*factor))
        else:
            box=slide.shapes.add_textbox(Inches(.8),Inches(2.35),Inches(11.65),Inches(4.3))
            box.text_frame.word_wrap=True
            size=23 if len(page['display'])<220 else 19
            for n,line in enumerate(page['display'].splitlines()):
                p=box.text_frame.paragraphs[0] if n==0 else box.text_frame.add_paragraph()
                p.space_after=Pt(18)
                add_rich_text(p,line,size,'243E36')
        slide.notes_slide.notes_text_frame.text=page['narration']
    prs.save(path)


def write_package(directory, manifest, slides):
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'index.html').write_text(html_package(manifest, slides), encoding='utf-8')
    (directory/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory/'slides.json').write_text(json.dumps(slides, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory/'narration.json').write_text(dump([dict(page_id=s['id'],text=s['narration'],audio_file=s.get('audio_file')) for s in slides]), encoding='utf-8')
    export_pptx(directory/'courseware.pptx',slides,manifest)
    with zipfile.ZipFile(directory/'package.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob('*')):
            if path.is_file() and path.name != 'package.zip' and not path.name.startswith('.'):
                archive.write(path, path.relative_to(directory))
