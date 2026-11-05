"""Teaching intent is independent of the source question's classification."""
from .grades import grade_label

TARGET_FIELDS=('target_grade','target_curriculum')


def validate_target_curriculum(question):
    wrong=[p for p in question.target_curriculum if p.grade and p.grade!=question.target_grade]
    if wrong:
        raise ValueError(f'目标年级为{question.target_grade}，目标课标只能填写该年级；请移除其他年级的条目后重新匹配')


def teaching_target(question):
    data=question.model_dump() if hasattr(question,'model_dump') else question
    points=data.get('target_curriculum') or [dict(knowledge_point=name,grade='',grade_source='unknown',curriculum_requirements=[]) for name in data.get('knowledge_points',[])]
    from .models import TargetKnowledgePoint
    points=[TargetKnowledgePoint.model_validate(point).model_dump() for point in points]
    return dict(target_grade=data.get('target_grade',''),
        target_semester=data.get('target_semester','全学期'),
        target_curriculum=list(points),
        knowledge_point_source='target' if data.get('target_curriculum') else 'question')


def teaching_question(question):
    """A retrieval/model view; never change the stored original question."""
    target=teaching_target(question)
    source_ids=dict(zip(question.knowledge_points,question.knowledge_point_ids))
    return question.model_copy(update=dict(grade=target['target_grade'],
        grades=[target['target_grade']] if target['target_grade'] else [],source_grades=[],
        knowledge_points=list(dict.fromkeys(point['knowledge_point'] for point in target['target_curriculum'])),
        knowledge_point_ids=list(dict.fromkeys(source_ids[point.knowledge_point] for point in question.target_curriculum if source_ids.get(point.knowledge_point))) if question.target_curriculum else question.knowledge_point_ids))


def package_prefix(settings, question, package_id):
    from .store import digest
    target=teaching_target(question)
    # Validated grade labels are also human-readable MinIO directory names.
    grade=target['target_grade'] or '未指定年级'
    return '/'.join(filter(None,[settings.s3_prefix.strip('/'),grade,target['target_semester'],digest(question['question_id'])[:16],package_id]))


def presentation_scope(manifest):
    target=manifest.get('teaching_target') or teaching_target(manifest['question'])
    if target['target_grade']:
        return target['target_grade']
    return grade_label(manifest['question'])
