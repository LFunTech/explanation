"""Lossless display projection for question pages; keep the source question intact."""
import re


MATH = re.compile(r'\$\$[\s\S]*?\$\$|\$[^$\n]*\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]')
MARKER = re.compile(r'(?<!\S)(?:([A-HＡ-Ｈ])\s*[.．、:：)）]|[（(]([A-HＡ-Ｈ])[)）])\s*')


def split_choices(text):
    """Recognize an ordered A… list outside math, preserving all option text."""
    masked = MATH.sub(lambda m: '~'*len(m.group()), text)
    matches = list(MARKER.finditer(masked))
    labels = [(m.group(1) or m.group(2)).translate(str.maketrans('ＡＢＣＤＥＦＧＨ','ABCDEFGH')) for m in matches]
    if len(labels)<2 or labels != list('ABCDEFGH'[:len(labels)]):
        return text, []
    choices = [dict(label=label, text=text[m.end():matches[i+1].start() if i+1<len(matches) else len(text)].strip())
               for i,(m,label) in enumerate(zip(matches,labels))]
    # A prose mention such as "从 A. B. C. D. 中选择" is not an option list.
    if any(c['text'] for c in choices) and not all(c['text'] for c in choices):
        return text, []
    return text[:matches[0].start()].rstrip(), choices


def explicit_choices(options):
    if not options:
        return []
    prefix, choices = split_choices('\n'.join(options))
    if choices and not prefix.strip():
        return choices
    # Explicit unlabelled options still have a known order; don't parse their formulas.
    result = []
    for index, option in enumerate(options):
        match = MARKER.match(option)
        label = (match.group(1) or match.group(2)) if match else chr(65+index)
        result.append(dict(label=label, text=option[match.end():].strip() if match else option))
    return result


def question_layout(question):
    stem = question.get('stem','')
    choices = explicit_choices(question.get('options',[]))
    images = question.get('images',[])
    is_choice = question.get('question_type') in {'单选题','多选题','选择题'}
    if not choices and is_choice:
        stem, choices = split_choices(stem)
    subs = []
    raw_subs = question.get('sub_questions',[])
    for sub in raw_subs:
        prompt, nested_choices = split_choices(sub['stem'])
        if len(raw_subs)==1 and is_choice and not choices and nested_choices and not prompt:
            choices = nested_choices
        elif nested_choices:
            subs.append({**sub, 'stem':prompt, 'choices':nested_choices})
        elif sub['stem'].strip() != '请结合共用题干作答。':
            subs.append(dict(sub))
    # The exam importer preserves document order for option-only attachments.
    # Never infer choices from image count alone or flatten genuine subquestions.
    image_choices=[]
    if (question.get('source','').startswith('exam/public/question/')
            and is_choice and choices and not subs and all(not c['text'] for c in choices)):
        labels=[c['label'] for c in choices]
        if labels==[chr(65+i) for i in range(len(labels))] and len(labels)==len(images):
            image_choices=[dict(label=label,image_index=i) for i,label in enumerate(labels)]
            choices=[{**c, 'image_index':i} for i,c in enumerate(choices)]
    options=[] if image_choices else [c['label']+'. '+c['text'] for c in choices]
    return dict(stem=stem,options=options,choices=choices,sub_questions=subs,
                image_choices=image_choices,image_indices=[] if image_choices else list(range(len(images))))


def variant_layout(variant):
    stem=variant.get('stem','')
    # Older exam exports prefixed an option-only child with its synthetic ID.
    # Remove it only when it introduces one complete A/B/... option list.
    normalized=re.sub(r'(?m)^[ \t]*[（(]1[）)][ \t]*(?=[AＡ][.．、:：)）])','',stem)
    _,choices=split_choices(normalized)
    if choices and not re.search(r'(?m)^[ \t]*[（(]\d+[）)]',normalized):
        stem=normalized
    source=variant.get('source','')
    if source=='resource' and (variant.get('question_id') or '').startswith('exam:public:'):
        source='exam/public/question/'+variant['question_id'].split(':')[-1]
    return question_layout(dict(stem=stem,question_type='选择题',images=variant.get('images',[]),source=source))
