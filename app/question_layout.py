"""Lossless display projection for question pages; keep the source question intact."""
import re


def question_layout(question):
    options=list(question.get('options',[]))
    subs=list(question.get('sub_questions',[]))
    images=question.get('images',[])
    label_only=re.compile(r'(?:\s*[A-H][.．、:：]\s*){2,8}')
    candidate='\n'.join(options)
    nested=False
    if not options and len(subs)==1:
        candidate=subs[0]['stem'];nested=True
    # The exam importer preserves document order for option-only attachments.
    # Never infer choices from image count alone or flatten genuine subquestions.
    image_choices=[]
    if (question.get('source','').startswith('exam/public/question/')
            and question.get('question_type') in {'单选题','多选题','选择题'}
            and label_only.fullmatch(candidate)):
        labels=re.findall(r'([A-H])[.．、:：]',candidate)
        if labels==[chr(65+i) for i in range(len(labels))] and len(labels)==len(images):
            image_choices=[dict(label=label,image_index=i) for i,label in enumerate(labels)]
            if nested:subs=[]
            else:options=[]
    return dict(stem=question.get('stem',''),options=options,sub_questions=subs,
                image_choices=image_choices,image_indices=[] if image_choices else list(range(len(images))))
