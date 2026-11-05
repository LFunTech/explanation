"""Keep the authored knowledge explanations visible before the solution."""
import re


def ensure_knowledge_pages(pages, points, *, legacy=False):
    if not pages or not points:
        return pages
    pages=[dict(page) for page in pages]
    covered=set()
    for page in pages:
        if page.get('kind')!='知识' or re.search('易错|提醒|总结',page.get('title','')):
            continue
        indices=[i for i in page.get('knowledge_indices',[]) if type(i) is int and 0<=i<len(points)]
        if not indices:
            # Older exports dropped the index. Only exact titles or sequential
            # explicitly numbered knowledge/step pages are safe to recover.
            indices=[i for i,p in enumerate(points) if p['title']==page.get('title')]
            if legacy and not indices and re.match(r'(?:第[一二三四五六七八九十\d]+步|知识点\s*\d+)',page.get('title','')):
                indices=next(([i] for i in range(len(points)) if i not in covered),[])
        if not indices:
            continue
        covered.update(indices)
        page['knowledge_indices']=indices
        page['title']='知识点'+str(indices[0]+1)+'：'+'；'.join(points[i]['title'] for i in indices)
    missing=[i for i in range(len(points)) if i not in covered]
    position=next((i for i,p in enumerate(pages) if p.get('kind') in {'解题','答案','总结','变式'}),len(pages))
    for i in missing:
        point=points[i]
        display='\n'.join(filter(None,[point.get('display'),('原理与条件：'+point['why']) if point.get('why') else '',('用于本题：'+point['application']) if point.get('application') else '']))
        narration='\n'.join(dict.fromkeys(value for value in [point.get('why'),point.get('notes'),point.get('application'),point.get('display')] if value))
        pages.insert(position,dict(id=f'knowledge-{i+1:03}',kind='知识',title=f'知识点{i+1}：'+point['title'],
            display=display,narration=narration,notes=narration,knowledge_indices=[i]))
        position+=1
    return pages
