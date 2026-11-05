import base64
import io
import json
import re
import zipfile
import html
from pathlib import Path
from xml.etree import ElementTree as ET

from latex2mathml.converter import convert
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

from .config import ROOT
from .diagrams import render_diagram
from .question_layout import question_layout, variant_layout
from .narration import plain_text, page_narration
from .store import dump
from .teaching import presentation_scope
from .variant_visuals import bind_variant_visuals
from .knowledge_pages import ensure_knowledge_pages
from .page_content import complete_page_content

PRESENTATION_VERSION = 'content-16x9-v17'


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
    pages = lesson.get('pages')
    if not pages:
        # Curated legacy samples: keep the full source in the reading section,
        # group connected calculations instead of one page per arithmetic action.
        pages = [dict(kind='原题', title='从这道题出发', display=lesson['objective'], narration=lesson['objective'])]
        pages += [dict(kind='知识', title=p['title'], display=p['display'], narration=p['notes']) for p in lesson['knowledge_points']]
        pages += [dict(kind='解题', title='把知识用到题目中', display='\n'.join(p['display'] for p in lesson['solution_steps']),
                       narration='\n'.join(p['notes'] for p in lesson['solution_steps']),solution_indices=list(range(len(lesson['solution_steps'])))),
                  dict(kind='答案', title='答案与检验', display=lesson['answer'], narration=lesson['answer']+'\n'+(lesson['pitfalls'][0]['notes'] if lesson['pitfalls'] else ''))]
    pages=complete_page_content(pages,lesson)
    pages=ensure_knowledge_pages(pages,lesson.get('knowledge_points',[]))
    display_pages=[]
    for page in pages:
        indices=page.get('solution_indices',[]) if page['kind']=='解题' else []
        if len(indices)>4:
            for offset in range(0,len(indices),4):
                display_pages.append({**page,'solution_indices':indices[offset:offset+4],
                    'title':page['title']+(f'（续 {offset//4}）' if offset else '')})
        else:display_pages.append(page)
    slides=[]
    for page in display_pages:
        slide=dict(id=f'page-{len(slides)+1:03}',kind=page['kind'],title=page['title'],display=page['display'],
                   notes=page['narration'],narration=page_narration(page, lesson['solution_steps']))
        if page.get('variant_index') is not None:
            slide['variant_index']=page['variant_index']
        if page.get('knowledge_indices'):
            slide['knowledge_indices']=page['knowledge_indices']
        if page['kind']=='解题' and page.get('solution_indices'):
            slide['flow']=[dict(number=i+1,title=lesson['solution_steps'][i]['title'],display=lesson['solution_steps'][i]['display'],
                                narration=plain_text(lesson['solution_steps'][i].get('narration',lesson['solution_steps'][i]['notes']))) for i in page['solution_indices']]
            slide['narration']='\n'.join(step['narration'] for step in slide['flow'])
            slide['notes']=slide['narration']
        if page['kind']=='原题':
            slide['original']=True
        if page.get('diagram'):
            try:
                slide['diagram']=render_diagram(page['diagram'])
            except (ValueError,KeyError,ArithmeticError):
                pass  # Optional drawing failure cannot block the text and PPT.
        image_index=page.get('source_image_index')
        early=bool({'一年级','二年级'} & set(question.get('grades') or [question.get('grade')]))
        if early and slide.get('diagram',{}).get('kind') in {'number_line','cartesian','process'}:
            slide.pop('diagram',None)
        if early and page['kind']!='变式' and question.get('images') and image_index is None:
            image_index=0
        if isinstance(image_index,int) and 0<=image_index<len(question.get('images',[])) and not slide.get('original'):
            slide['diagram']=source_figure(question['images'][image_index],page.get('source_image_caption') or '观察原题中的对象和位置。',image_index)
        slides.append(slide)
    if slides and question.get('images') and not any(s.get('original') for s in slides) and re.search('读题|观察|题目',slides[0]['title']):
        slides[0]['original']=True;slides[0]['kind']='原题';slides[0].pop('diagram',None)
    bind_variant_visuals(question,lesson.get('variants',[]),slides)
    return slides


def source_figure(image, caption, index):
    from PIL import Image
    raw=base64.b64decode(image.split(',',1)[1])
    with Image.open(io.BytesIO(raw)) as im:width,height=im.size
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><image width="{width}" height="{height}" href="{html.escape(image,quote=True)}"/></svg>'
    return dict(kind='source_image',svg='data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode(),png=image,
        caption='原题配图 '+str(index+1)+' · '+caption,alt=caption,source_image_index=index)


def inline_math_assets():
    root = ROOT / 'web/vendor/katex'
    css = (root/'katex.min.css').read_text(encoding='utf-8')
    def font(match):
        file = re.search(r'url\(([^)]+\.woff2)\)', match.group(1))
        if not file:
            return match.group(0)
        value = base64.b64encode((root/file.group(1)).read_bytes()).decode('ascii')
        return 'src:url(data:font/woff2;base64,'+value+') format("woff2");'
    css = re.sub(r'src:([^;}]+);?', font, css)
    return css, (root/'katex.min.js').read_text(encoding='utf-8'), (root/'contrib/auto-render.min.js').read_text(encoding='utf-8')


def html_package(manifest, slides, *, inline_audio=True):
    css, katex, auto = inline_math_assets()
    template = (ROOT/'web/content.html').read_text(encoding='utf-8')
    player_css = '\n'.join((ROOT/'web'/name).read_text(encoding='utf-8') for name in ('question.css','content.css','figure-viewer.css'))
    player_js = '\n'.join((ROOT/'web'/name).read_text(encoding='utf-8') for name in ('question.js','layout-metrics.js','question-pagination.js','slide-pagination.js','answer-dialog.js','content.js'))
    rendered=presentation_slides(slides)
    if not inline_audio:
        rendered=[{k:v for k,v in slide.items() if k!='audio' or not slide.get('audio_file')} for slide in rendered]
    value = dump(dict(manifest=manifest, slides=rendered,questionLayout=question_layout(manifest.get('question',{})),variantLayouts=[variant_layout(v) for v in manifest.get('variants',[])])).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    replacements={'MATH_CSS':css,'PLAYER_CSS':player_css,'MATH_JS':katex,'MATH_AUTO':auto,
                  'LESSON_DATA':'window.COURSEWARE='+value+';','PLAYER_JS':player_js}
    # 一次替换模板，题目正文中的占位符不会被当成代码再次展开。
    return re.sub(r'/\*('+'|'.join(replacements)+r')\*/',lambda m:replacements[m.group(1)],template)


def html_player(content_url='', audio_urls=None, *, fallback=None, content_html=None):
    """Player shell for remote content or a self-contained packaged lesson."""
    css,katex,auto=inline_math_assets()
    template=(ROOT/'web/player.html').read_text(encoding='utf-8')
    styles='\n'.join((ROOT/'web'/name).read_text(encoding='utf-8') for name in ('player.css','presentation.css','question.css','viewport-player.css','figure-viewer.css'))
    script='\n'.join((ROOT/'web'/name).read_text(encoding='utf-8') for name in ('question.js','answer-dialog.js','player.js'))
    config=dict(contentUrl=content_url,audio=audio_urls or {},fallback=fallback)
    if content_html is not None:config['contentHtml']=content_html
    data=dump(config).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    replacements=dict(MATH_CSS=css,PLAYER_CSS=styles,MATH_JS=katex,MATH_AUTO=auto,LESSON_DATA='window.COURSEWARE_HOST='+data+';',PLAYER_JS=script)
    return re.sub(r'/\*('+'|'.join(replacements)+r')\*/',lambda m:replacements[m.group(1)],template)


MATH_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'


def presentation_slides(slides):
    # Legacy packages stored the original as plain text. Project that page from the
    # preserved question too, without changing its page ID, audio or stored slides.
    if any(s.get('original') for s in slides):
        return slides
    first = next((i for i,s in enumerate(slides) if s['kind']=='原题' and not s.get('image')), None)
    return [{**s, 'original':True} if i==first else s for i,s in enumerate(slides)]


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
    for index, page in enumerate(presentation_slides(slides)):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        slide.background.fill.solid(); slide.background.fill.fore_color.rgb = RGBColor.from_string('F7F8F3')
        for x,y,w,h,text,size,color in [(.65,.45,11,.4, page['kind']+'  /  '+manifest['question']['subject'],13,'327762'),
                                       (.65,1.05,12,1.1,page['title'],29,'183C32'),
                                       (.65,6.95,11,.3,presentation_scope(manifest)+' · '+manifest['question']['subject'],11,'6B7E75'),
                                       (12,6.95,.6,.3,f'{index+1:02}',11,'327762')]:
            box=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h))
            add_rich_text(box.text_frame.paragraphs[0],text,size,color)
        diagram=page.get('diagram')
        original=page.get('original') or page.get('variant')
        if original:
            question=manifest['variants'][page['variant_index']] if page.get('variant') else manifest['question']
            layout=variant_layout(question) if page.get('variant') else question_layout(question)
            diagram=None
            display=layout['stem']+'\n'+'\n'.join(layout['options'])+'\n'+'\n'.join(
                '（'+q['id']+'）'+q['stem']+''.join('\n'+c['label']+'. '+c['text'] for c in q.get('choices',[]))
                for q in layout['sub_questions'])
        else:
            display=page['display']
        if original and layout['image_choices']:
            box=slide.shapes.add_textbox(Inches(.75),Inches(1.95),Inches(11.8),Inches(1.0))
            box.text_frame.word_wrap=True
            add_rich_text(box.text_frame.paragraphs[0],display.strip(),24,'243E36')
            from PIL import Image
            choices=layout['image_choices'];cols=2 if len(choices)<=4 else 3;rows=(len(choices)+cols-1)//cols
            for n,choice in enumerate(choices):
                x=.85+(n%cols)*11.7/cols;y=3.15+(n//cols)*3.35/rows
                box=slide.shapes.add_textbox(Inches(x),Inches(y+.15),Inches(.5),Inches(.45))
                add_rich_text(box.text_frame.paragraphs[0],choice['label']+'.',24,'327762')
                raw=base64.b64decode(question['images'][choice['image_index']].split(',',1)[1])
                with Image.open(io.BytesIO(raw)) as im:w,h=im.size
                factor=min((11.7/cols-.85)/w,(3.35/rows-.25)/h)
                slide.shapes.add_picture(io.BytesIO(raw),Inches(x+.65),Inches(y),width=Inches(w*factor),height=Inches(h*factor))
        elif original and layout['choices'] and not layout['sub_questions']:
            images=[question['images'][i] for i in layout['image_indices']]
            box=slide.shapes.add_textbox(Inches(.8),Inches(1.9),Inches(8 if images else 11.7),Inches(1.55))
            box.text_frame.word_wrap=True
            add_rich_text(box.text_frame.paragraphs[0],layout['stem'],21 if len(layout['stem'])<180 else 17,'243E36')
            if images:
                from PIL import Image
                for i,value in enumerate(images):
                    raw=base64.b64decode(value.split(',',1)[1])
                    with Image.open(io.BytesIO(raw)) as im:w,h=im.size
                    factor=min(3.1/w,1.6/len(images)/h)
                    slide.shapes.add_picture(io.BytesIO(raw),Inches(9.3),Inches(1.85+i*1.6/len(images)),
                                             width=Inches(w*factor),height=Inches(h*factor))
            choices=layout['choices'];cols=1 if any(len(c['text'])>100 or '\n' in c['text'] for c in choices) else 2
            rows=(len(choices)+cols-1)//cols;row_height=3.15/rows
            for i,choice in enumerate(choices):
                x=.8+(i%cols)*11.7/cols;y=3.6+(i//cols)*row_height
                box=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(.45),Inches(.4))
                add_rich_text(box.text_frame.paragraphs[0],choice['label']+'.',21,'327762')
                box=slide.shapes.add_textbox(Inches(x+.5),Inches(y),Inches(11.7/cols-.7),Inches(row_height-.1))
                box.text_frame.word_wrap=True
                add_rich_text(box.text_frame.paragraphs[0],choice['text'] or '见原题配图',20 if rows<=3 else 16,'243E36')
        elif page.get('flow'):
            flow=page['flow'];inline_diagram=diagram if diagram and diagram['kind']!='process' else None
            cols=2 if len(flow)>1 else 1;rows=(len(flow)+cols-1)//cols
            area_width=7.2 if inline_diagram else 11.7
            width=(area_width-.3*(cols-1))/cols;height=4.5/rows
            for i,step in enumerate(flow):
                x=.8+(i%cols)*(width+.3);y=2.05+(i//cols)*height
                node=slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,Inches(x),Inches(y),Inches(width),Inches(height-.25))
                node.fill.solid();node.fill.fore_color.rgb=RGBColor.from_string('EFF5E8')
                node.line.color.rgb=RGBColor.from_string('B9CCAE')
                frame=node.text_frame;frame.word_wrap=True
                add_rich_text(frame.paragraphs[0],step['title'],16 if not inline_diagram else 13,'327762')
                add_rich_text(frame.add_paragraph(),step['display'],20 if not inline_diagram else 16,'243E36')
                if i<len(flow)-1 and i%cols==0 and cols==2:
                    arrow=slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW,Inches(x+width+.04),Inches(y+(height-.25)/2-.08),Inches(.22),Inches(.16))
                    arrow.fill.solid();arrow.fill.fore_color.rgb=RGBColor.from_string('6F9577');arrow.line.fill.background()
            if inline_diagram:
                raw=base64.b64decode(inline_diagram['png'].split(',',1)[1])
                slide.shapes.add_picture(io.BytesIO(raw),Inches(8.3),Inches(2.8),width=Inches(4.4),height=Inches(2.2))
        elif page.get('image'):
            raw=base64.b64decode(page['image'].split(',',1)[1])
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as image:
                width,height=image.size
            factor=min(11/width,4.4/height)
            slide.shapes.add_picture(io.BytesIO(raw),Inches((13.333-width*factor)/2),Inches(2.15),width=Inches(width*factor),height=Inches(height*factor))
        else:
            has_images=original and question.get('images')
            box=slide.shapes.add_textbox(Inches(.8),Inches(2.05),Inches(4.7 if diagram else 11.65),Inches(2.1 if has_images else 4.4))
            box.text_frame.word_wrap=True
            size=21 if len(display)<220 else 17
            for n,line in enumerate(display.splitlines()):
                p=box.text_frame.paragraphs[0] if n==0 else box.text_frame.add_paragraph()
                p.space_after=Pt(12)
                add_rich_text(p,line,size,'243E36')
            if diagram:
                raw=base64.b64decode(diagram['png'].split(',',1)[1])
                slide.shapes.add_picture(io.BytesIO(raw),Inches(5.7),Inches(2.45),width=Inches(6.9),height=Inches(3.45))
                box=slide.shapes.add_textbox(Inches(5.8),Inches(6),Inches(6.6),Inches(.65))
                box.text_frame.word_wrap=True
                add_rich_text(box.text_frame.paragraphs[0],diagram['caption'],12,'557962')
            if has_images:
                from PIL import Image
                images=question['images'];cols=min(4,len(images));rows=(len(images)+cols-1)//cols
                for n,value in enumerate(images):
                    raw=base64.b64decode(value.split(',',1)[1])
                    with Image.open(io.BytesIO(raw)) as im:width,height=im.size
                    factor=min(11.4/cols/width,2.3/rows/height)
                    slide.shapes.add_picture(io.BytesIO(raw),Inches(.8+(n%cols)*11.7/cols),Inches(4.4+(n//cols)*2.4/rows),
                                             width=Inches(width*factor),height=Inches(height*factor))
        notes=page['narration']
        if page.get('variant'):
            variant=manifest['variants'][page['variant_index']]
            notes+='\n答案：'+variant.get('answer','')+'\n解析：'+variant.get('analysis','')
        slide.notes_slide.notes_text_frame.text=notes
    prs.save(path)


def write_package(directory, manifest, slides):
    directory.mkdir(parents=True, exist_ok=True)
    slides[:]=complete_page_content(slides,manifest.get('reading',{}))
    bind_variant_visuals(manifest['question'],manifest.get('variants',[]),slides)
    manifest['slide_count']=len(slides)
    manifest.setdefault('quality',{}).update(page_count=len(slides),diagram_count=sum(bool(s.get('diagram')) or bool(s.get('variant') and manifest['variants'][s['variant_index']].get('images')) for s in slides))
    manifest['files']=list(dict.fromkeys([*manifest.get('files',[]),'online.html','player.html']))
    for slide in slides:
        if slide.get('diagram'):
            assets=directory/'figures';assets.mkdir(exist_ok=True)
            for kind in ('svg','png'):
                (assets/(slide['id']+'.'+kind)).write_bytes(base64.b64decode(slide['diagram'][kind].split(',',1)[1]))
    content=html_package(manifest, slides)
    (directory/'index.html').write_text(content, encoding='utf-8')
    # A single signed MinIO object must be sufficient even for private buckets.
    # Embed the complete content (including recorded audio) in a sandboxed frame,
    # so no unsigned sibling requests or expiring URLs are baked into the file.
    (directory/'player.html').write_text(html_player(content_html=content),encoding='utf-8')
    (directory/'online.html').write_text(html_package(manifest, slides,inline_audio=False), encoding='utf-8')
    (directory/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory/'slides.json').write_text(json.dumps(slides, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory/'narration.json').write_text(dump([dict(page_id=s['id'],text=s['narration'],audio_file=s.get('audio_file')) for s in slides]), encoding='utf-8')
    export_pptx(directory/'courseware.pptx',slides,manifest)
    with zipfile.ZipFile(directory/'package.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob('*')):
            if path.is_file() and path.name != 'package.zip' and not path.name.startswith('.'):
                archive.write(path, path.relative_to(directory))
