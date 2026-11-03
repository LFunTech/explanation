from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class SubQuestion(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    stem: str = Field(min_length=1, max_length=10000)
    answer: str = Field(default='', max_length=10000)
    analysis: str = Field(default='', max_length=20000)


class Question(StrictModel):
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


class Resource(StrictModel):
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
