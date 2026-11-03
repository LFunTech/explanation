from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .diagrams import Diagram
from .scope import TeachingScope


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class SubQuestion(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    stem: str = Field(min_length=1, max_length=10000)
    answer: str = Field(default='', max_length=10000)
    analysis: str = Field(default='', max_length=20000)


class Question(StrictModel,TeachingScope):
    question_id: str = Field(min_length=1, max_length=100)
    stem: str = Field(min_length=1, max_length=20000)
    subject: Literal['数学', '物理', '化学', '生物', '科学', '语文', '英语', '历史', '地理', '道德与法治'] = '数学'
    grade: str = Field(default='九年级', min_length=1, max_length=30)
    region: str = Field(default='上海', min_length=1, max_length=40)
    knowledge_points: list[str] = Field(default_factory=list, max_length=20)
    knowledge_point_ids: list[str] = Field(default_factory=list, max_length=20)
    source: str = Field(default='', max_length=200)
    requires_images: bool = False
    question_type: str = Field(default='解答题', min_length=1, max_length=30)
    difficulty: Literal['基础', '中等', '较难'] = '中等'
    options: list[str] = Field(default_factory=list, max_length=12)
    images: list[str] = Field(default_factory=list, max_length=8)
    sub_questions: list[SubQuestion] = Field(default_factory=list, max_length=20)
    answer: str = Field(default='', max_length=10000)
    analysis: str = Field(default='', max_length=30000)

    @field_validator('knowledge_points', 'options')
    @classmethod
    def valid_entries(cls, values):
        if any(not v.strip() or len(v) > 2000 for v in values):
            raise ValueError('条目不能为空或超过 2000 字符')
        return list(dict.fromkeys(v.strip() for v in values))

    @model_validator(mode='after')
    def unique_subquestions(self):
        ids = [q.id for q in self.sub_questions]
        if len(ids) != len(set(ids)):
            raise ValueError('小题 ID 不得重复')
        return self


class GenerationOptions(StrictModel):
    mode: Literal['qwen', 'sample'] = 'qwen'
    depth: Literal['brief', 'standard', 'deep'] = 'standard'
    variant_count: int = Field(default=2, ge=0, le=5)
    voice: bool = True
    force: bool = False


class JobRequest(StrictModel):
    assignment_id: str = Field(min_length=1, max_length=100)
    title: str = Field(default='题目讲解', min_length=1, max_length=150)
    questions: list[Question] = Field(min_length=1, max_length=50)
    options: GenerationOptions = Field(default_factory=GenerationOptions)
    callback_url: str | None = Field(default=None, max_length=1000)

    @model_validator(mode='after')
    def unique_questions(self):
        ids = [q.question_id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError('同一批次 question_id 不得重复')
        return self


class Point(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    display: str = Field(min_length=1, max_length=12000)
    notes: str = Field(min_length=1, max_length=20000)


class Variant(StrictModel):
    question_id: str | None = Field(default=None, max_length=100)
    source: Literal['generated', 'resource'] = 'generated'
    stem: str = Field(min_length=1, max_length=10000)
    answer: str = Field(min_length=1, max_length=10000)
    analysis: str = Field(min_length=1, max_length=20000)
    knowledge_points: list[str] = Field(default_factory=list, max_length=20)


class Lesson(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=1000)
    knowledge_points: list[Point] = Field(min_length=1, max_length=16)
    solution_steps: list[Point] = Field(min_length=1, max_length=20)
    answer: str = Field(min_length=1, max_length=10000)
    pitfalls: list[Point] = Field(min_length=1, max_length=6)
    summary: list[str] = Field(min_length=1, max_length=8)
    variants: list[Variant] = Field(default_factory=list, max_length=5)


class KnowledgePoint(Point):
    why: str = Field(min_length=1, max_length=2000, description='原理为什么成立、适用条件；不能只复述定义')
    application: str = Field(min_length=1, max_length=2000, description='用本题具体条件、数值或图形说明如何应用')
    resource_ids: list[str] = Field(default_factory=list,max_length=6,description='本知识实际依据的课标/知识资源 ID；资源暂缺时留空，不编造课标依据')


class SolutionStep(Point):
    title: str = Field(min_length=1, max_length=45)
    display: str = Field(min_length=1, max_length=160, description='流程图节点中的关键运算、公式或判断；不能只有空泛动作')
    narration: str = Field(min_length=1, max_length=220, description='这个步骤对应的简明口语讲解，说明动作及依据；详细推导仍放 notes')


class LecturePage(StrictModel):
    kind: Literal['原题', '知识', '解题', '答案', '总结']
    title: str = Field(min_length=1, max_length=35)
    display: str = Field(min_length=1, max_length=230, description='上屏重点，最多四个短段落；长题干由服务端原样展示')
    narration: str = Field(min_length=1, max_length=800, description='本页实际口语台词，解释为什么；不要与 display 拼接重复朗读')
    knowledge_indices: list[int] = Field(default_factory=list, max_length=4, description='本页讲解的 knowledge_points 从零开始索引')
    solution_indices: list[int] = Field(default_factory=list, max_length=4, description='仅解题页填写，从零开始；对应步骤会原样画成流程节点，并依次朗读各步骤 narration')
    diagram: Diagram | None = None
    visual_reason: str = Field(min_length=1, max_length=300, description='配图帮助理解哪个关系；无需图时说明原因')


class IllustratedLesson(Lesson):
    complexity: Literal['simple', 'complex'] = Field(description='按实际题目步骤与推理判断；常规单步或两步题必须 simple')
    knowledge_points: list[KnowledgePoint] = Field(min_length=1, max_length=4)
    solution_steps: list[SolutionStep] = Field(min_length=1, max_length=16)
    pages: list[LecturePage] = Field(min_length=3, max_length=10)

    @model_validator(mode='after')
    def lecture_coverage(self):
        if self.complexity == 'simple' and len(self.pages) > 5:
            raise ValueError('简单题最多 5 页；完整解析和变式题放阅读区')
        if sum(p.kind == '原题' for p in self.pages) != 1 or self.pages[0].kind != '原题':
            raise ValueError('必须以唯一的原题页开场，不增加空泛封面')
        if not any(p.kind == '答案' for p in self.pages):
            raise ValueError('讲解中须有答案与检验页')
        for field, points in [('knowledge_indices', self.knowledge_points), ('solution_indices', self.solution_steps)]:
            used = {i for p in self.pages for i in getattr(p, field)}
            if used != set(range(len(points))):
                raise ValueError(field+' 必须准确覆盖所有知识点或解题步骤')
        for page in self.pages:
            if page.kind=='解题' and not page.solution_indices or page.kind!='解题' and page.solution_indices:
                raise ValueError('解题步骤须集中在解题页，以流程图呈现，每页最多四个节点')
            if page.kind=='解题':
                page.narration='\n'.join(self.solution_steps[i].narration for i in page.solution_indices)
        if self.complexity == 'simple' and sum(len(p.narration) for p in self.pages) > 420:
            raise ValueError('简单题台词总计最多 420 字符，目标一至两分钟；完整推导保留在详细解析')
        return self


class ReviewCheck(StrictModel):
    reason: str = Field(min_length=1, max_length=4000)
    errors: list[str] = Field(max_length=20)


class ReviewChecks(StrictModel):
    accuracy: ReviewCheck
    grounding: ReviewCheck
    coverage: ReviewCheck
    pedagogy: ReviewCheck
    variants: ReviewCheck


class Review(StrictModel):
    checks: ReviewChecks

    def errors(self):
        return [f'{name}: {error}' for name, check in self.checks.model_dump().items() for error in check['errors']]


class Resource(StrictModel,TeachingScope):
    id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.:-]+$')
    title: str = Field(min_length=1, max_length=150)
    kind: Literal['knowledge', 'curriculum', 'question'] = 'knowledge'
    subject: str = Field(min_length=1, max_length=30)
    grades: list[str] = Field(min_length=1, max_length=12)
    region: str = Field(default='上海', min_length=1, max_length=40)
    knowledge_points: list[str] = Field(min_length=1, max_length=30)
    content: str = Field(min_length=1, max_length=30000)
    source: str = Field(min_length=1, max_length=200)
    version: str = Field(default='1', min_length=1, max_length=80)
    is_sample: bool = False
    answer: str = Field(default='', max_length=10000)
    analysis: str = Field(default='', max_length=20000)

    @model_validator(mode='after')
    def question_has_solution(self):
        if self.kind == 'question' and not (self.answer and self.analysis):
            raise ValueError('题库资源必须提供答案和解析')
        if any(not s.strip() for s in self.grades + self.knowledge_points):
            raise ValueError('年级与知识点不能为空')
        return self


class ResourceImport(StrictModel):
    resources: list[Resource] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique_ids(self):
        ids = [r.id for r in self.resources]
        if len(ids) != len(set(ids)):
            raise ValueError('同一导入批次资源 ID 不得重复')
        return self
