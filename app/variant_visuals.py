"""One question page per variant, with answers kept for the same-page dialog."""
import re
from difflib import SequenceMatcher
from .narration import plain_text
from .diagrams import render_diagram
from .shape_gallery import enumerated_gallery

SAME_FIGURE = re.compile(r'同一[^。；\n]{0,12}图|(?:沿用|根据|参照|利用)?(?:原题|上题|原来)[^。；\n]{0,8}图')
ANSWER_TITLE = re.compile(r'解答|答案|解析|解题')


def bind_variant_visuals(question, variants, slides):
    originals=question.get('images',[])
    for variant in variants:
        if variant.get('source')=='resource':continue
        indices=variant.get('source_image_indices',[])
        if not indices and SAME_FIGURE.search(variant.get('stem','')):
            indices=list(range(len(originals)))
        indices=list(dict.fromkeys(i for i in indices if type(i) is int and 0<=i<len(originals)))
        if indices:
            variant['source_image_indices']=indices
            variant['images']=[originals[i] for i in indices]
    groups={};indices={};ordinal=0
    for position,slide in enumerate(slides):
        number=re.search(r'变式(?:题)?\s*([1-9]\d*)',slide.get('title',''))
        if slide.get('kind')!='变式' and slide.get('variant_index') is None and not number:continue
        index=slide.get('variant_index')
        if index is None:index=int(number.group(1))-1 if number else ordinal
        ordinal+=1
        if type(index) is not int or not 0<=index<len(variants):continue
        indices[position]=index;groups.setdefault(index,[]).append(slide)
    selected={}
    for index,variant in enumerate(variants):
        candidates=groups.get(index,[])
        slide=min(candidates,key=lambda s:bool(ANSWER_TITLE.search(s.get('title','')))) if candidates else dict(
            id=f'variant-{index+1:03d}',title=f'变式题{index+1}：练习与应用',display=variant['stem'])
        changed_resource=variant.get('source')=='resource' and SequenceMatcher(None,slide.get('display',''),variant['stem']).ratio()<.45
        if changed_resource:
            slide['title']=f'变式题{index+1}：'+next(iter(variant.get('knowledge_points',[])),'练习与应用')
            slide.pop('diagram',None)
        diagram=slide.get('diagram')
        if not variant.get('images') and diagram and variant.get('source')!='resource':
            source_index=diagram.get('source_image_index')
            if diagram.get('kind')!='source_image' or (type(source_index) is int and 0<=source_index<len(originals)):
                variant['images']=[diagram['png']]
        if not variant.get('images'):
            spec=enumerated_gallery(variant['stem'])
            if spec:
                diagram=render_diagram(spec);slide['diagram']=diagram
                variant['images']=[diagram['png']]
                variant['visual_source']='题干图形示意'
        # A question's narration must not reveal its answer. Legacy answer pages
        # are removed, while the variant's canonical answer/analysis is untouched.
        narration=plain_text(variant['stem'])+'。请先独立思考，完成后点击查看答案。'
        if slide.get('narration')!=narration:
            for key in ('audio','audio_file','cues','duration_seconds'):slide.pop(key,None)
        for key in ('flow','original'):slide.pop(key,None)
        slide.update(kind='变式',variant=True,variant_index=index,display=variant['stem'],notes=narration,narration=narration)
        if ANSWER_TITLE.search(slide.get('title','')):
            slide['title']=f'变式题{index+1}：练习与应用'
        selected[index]=slide
    result=[];seen=set()
    for position,slide in enumerate(slides):
        if position not in indices:result.append(slide);continue
        index=indices[position]
        if index not in seen:result.append(selected[index]);seen.add(index)
    result.extend(slide for index,slide in selected.items() if index not in seen)
    slides[:]=result
