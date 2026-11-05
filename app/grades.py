"""Grade lists shared by search, generation and presentation."""
GRADE_NAMES = dict(zip((f'G{i}' for i in range(1,13)),
    ['一年级','二年级','三年级','四年级','五年级','六年级','七年级','八年级','九年级','高一','高二','高三']))
GRADE_CODES = {name:code for code,name in GRADE_NAMES.items()}
GRADE_CODES.update({'初一':'G7','初二':'G8','初三':'G9','十年级':'G10','十一年级':'G11','十二年级':'G12'})


def normalize_grades(values):
    return list(dict.fromkeys(GRADE_NAMES.get(GRADE_CODES.get(v.strip(),v.strip()),v.strip()) for v in values if v.strip()))


def question_grades(question):
    return normalize_grades(question.get('grades') or question.get('source_grades') or [question.get('grade','')])


def grade_label(question):
    return '、'.join(question_grades(question)) or '年级未标注'
