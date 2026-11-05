"""Recover omitted display text without changing the authored spoken script."""
import re


def display_points(text):
    # Split at spoken enumeration boundaries, not inside formulas or equations.
    # Keep each statement intact rather than truncating a lesson to a word limit.
    return re.sub(r'[；;]\s*(?=第[一二三四五六七八九十]+[，、,：:])', '\n', text.strip())


def point_text(points):
    return '\n'.join(p.get('display','').strip() or p.get('notes','').strip() for p in points if isinstance(p,dict))


def complete_page_content(pages, lesson):
    """Fill empty text pages from their section, then their existing narration.

    Original-question, image and flow pages may legitimately omit display.
    Completely empty optional pages are omitted rather than exported as cards.
    """
    result=[]
    for original in pages:
        page=dict(original)
        if page.get('display','').strip() or page.get('original') or page.get('kind')=='原题':
            result.append(page)
            continue
        title=page.get('title','')
        text=''
        if '易错' in title or page.get('kind')=='易错':
            text=point_text(lesson.get('pitfalls',[]))
        elif '总结' in title or page.get('kind')=='总结':
            text='\n'.join(lesson.get('summary',[]))
        elif page.get('kind')=='答案':
            text=lesson.get('answer','')
        else:
            field,indices=('solution_steps','solution_indices') if page.get('kind')=='解题' else ('knowledge_points','knowledge_indices')
            points=lesson.get(field,[])
            text=point_text([points[i] for i in page.get(indices,[]) if type(i) is int and 0<=i<len(points)])
        if not text.strip():
            text=page.get('narration','').strip() or page.get('notes','').strip()
        if text.strip():
            page['display']=display_points(text)
        if page.get('display','').strip() or any(page.get(k) for k in ('diagram','image','flow','solution_indices')):
            result.append(page)
    # The reading view must not remain empty when the corresponding authored
    # section exists only as a page/script.
    if not lesson.get('summary'):
        lesson['summary']=[p['display'] for p in result if p.get('display') and (p.get('kind')=='总结' or '总结' in p.get('title',''))]
    if not lesson.get('pitfalls'):
        lesson['pitfalls']=[dict(title=p['title'],display=p['display'],notes=p.get('narration',p.get('notes','')))
                           for p in result if p.get('display') and ('易错' in p.get('title','') or p.get('kind')=='易错')]
    return result
