"""The spoken script shared by validation, repair and slide export."""
import re


def plain_text(text):
    text = re.sub(r'\\(?:mathrm|text|operatorname)\{([^{}]*)\}', r'\1', text)
    text = re.sub(r'\\frac\{([^{}]*)\}\{([^{}]*)\}', r'(\1) 除以 (\2)', text)
    for key, value in {'\\times':'乘以','\\div':'除以','\\neq':'不等于','\\ne':'不等于','\\leq':'小于等于','\\geq':'大于等于','\\le':'小于等于','\\ge':'大于等于','\\Omega':'欧姆','\\,':'','^2':'的平方','^3':'的三次方'}.items():
        text = text.replace(key, value)
    return re.sub(r'[$#*_{}]', '', text)


def page_narration(page, steps):
    if page['kind'] == '解题' and page.get('solution_indices'):
        return '\n'.join(plain_text(steps[i].get('narration', steps[i]['notes'])) for i in page['solution_indices'])
    return plain_text(page['narration'])


def narration_length(lesson):
    return sum(len(page_narration(page, lesson['solution_steps'])) for page in lesson['pages'])
