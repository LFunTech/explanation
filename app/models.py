from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .diagrams import Diagram
from .narration import page_narration
from .scope import TeachingScope
from .grades import GRADE_NAMES, normalize_grades
from .page_content import complete_page_content


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class SubQuestion(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    stem: str = Field(min_length=1, max_length=10000)
    answer: str = Field(default='', max_length=10000)
    analysis: str = Field(default='', max_length=20000)


class TargetKnowledgePoint(StrictModel):
    # Old clients/model outputs may include metadata fields (including the
    # retired knowledge_point_id). They must not block otherwise usable targets.
    model_config = ConfigDict(extra='ignore', str_strip_whitespace=True, json_schema_extra={'additionalProperties':False})
    knowledge_point: str = Field(min_length=1, max_length=2000)
    grade: str = Field(default='', description='本条知识点及课标要求对应的唯一年级；不同年级必须分成独立条目')
    grade_source: Literal['knowledge_base','llm','manual','unknown'] = Field(default='unknown', description='年级标签来源，由程序设置')
    curriculum_requirements: list[str] = Field(default_factory=list, description='仅与当前知识点关联的课标要求')

    @model_validator(mode='before')
    @classmethod
    def legacy_grade(cls, data):
        if isinstance(data,dict) and 'grade' not in data:
            data=dict(data)
            values=data.get('grades',[])
            grades=normalize_grades(values) if isinstance(values,list) and all(isinstance(v,str) for v in values) else []
            # Shared requirements cannot be assigned to several grades by copying
            # them. Keep ambiguous legacy entries unassigned for re-analysis.
            data['grade']=grades[0] if len(grades)==1 else ''
        return data

    @field_validator('grade')
    @classmethod
    def valid_grade(cls, value):
        if not value:return ''
        grade=normalize_grades([value])[0]
        if grade not in GRADE_NAMES.values():
            raise ValueError('每条课标只能选择一个年级，须为一年级至高三')
        return grade

    @model_validator(mode='after')
    def label_source(self):
        if not self.grade:
            self.grade_source='unknown'
        elif self.grade_source=='unknown':
            self.grade_source='manual'
        return self

    @field_validator('curriculum_requirements')
    @classmethod
    def valid_requirements(cls, values):
        if any(not v.strip() or len(v)>2000 for v in values):
            raise ValueError('课标要求不能为空或超过 2000 字符')
        return list(dict.fromkeys(v.strip() for v in values))


class Question(StrictModel,TeachingScope):
    question_id: str = Field(min_length=1, max_length=100)
    stem: str = Field(min_length=1, max_length=20000)
    subject: Literal['数学', '物理', '化学', '生物', '科学', '语文', '英语', '历史', '地理', '道德与法治'] = '数学'
    grade: str = Field(default='', max_length=30)
    grades: list[str] = Field(default_factory=list,description='题目适用年级，可多选；grade 兼容旧客户端')
    source_grades: list[str] = Field(default_factory=list,description='公共题库记录的原始年级')
    region: str = Field(default='上海', min_length=1, max_length=40)
    knowledge_points: list[str] = Field(default_factory=list, max_length=20)
    knowledge_point_ids: list[str] = Field(default_factory=list, max_length=20)
    target_grade: str = Field(default='', max_length=30, description='独立指定的教学目标年级；提交生成时必填，不从题目年级推断')
    target_semester: Literal['全学期','上半学期','下半学期'] = Field(default='全学期', description='目标学期，省略时为全学期')
    target_curriculum: list[TargetKnowledgePoint] = Field(default_factory=list, description='目标课标：知识点与对应课标要求的 JSON 数组；为空时采用原题知识点')
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

    @field_validator('target_grade')
    @classmethod
    def valid_target_grade(cls, value):
        if not value:
            return ''  # Reading legacy packages and previewing source questions remain supported.
        grade=normalize_grades([value])[0]
        if grade not in GRADE_NAMES.values():
            raise ValueError('目标年级须为一年级至高三中的一个年级')
        return grade

    @model_validator(mode='after')
    def unique_subquestions(self):
        # Preserve fields_set so legacy scalar defaults can be distinguished from
        # an explicit multi-grade choice when resolving public source metadata.
        object.__setattr__(self,'grades',normalize_grades(self.grades or self.source_grades or [self.grade]))
        object.__setattr__(self,'grade',next(iter(self.grades),''))
        ids = [q.id for q in self.sub_questions]
        if len(ids) != len(set(ids)):
            raise ValueError('小题 ID 不得重复')
        return self


class GenerationOptions(StrictModel):
    mode: Literal['qwen', 'sample'] = 'qwen'
    depth: Literal['brief', 'standard', 'deep'] = 'standard'
    variant_count: int = Field(default=2, ge=0)
    voice: bool = True
    force: bool = False
    generation_requirements: str = Field(default='', max_length=8000, description='本次生成的补充要求；仍须遵守题目事实、目标年级及课标')


class CoursewareRequest(StrictModel):
    question_id: str = Field(min_length=1, max_length=100, description='exam 公共题库 UUID，兼容 exam:public:UUID')
    target_grade: str = Field(min_length=1, max_length=30)
    target_semester: Literal['全学期','上半学期','下半学期'] = '全学期'
    target_curriculum: list[TargetKnowledgePoint] = Field(default_factory=list)
    options: GenerationOptions = Field(default_factory=GenerationOptions)
    assignment_id: str = Field(default='', max_length=100)
    title: str = Field(default='', max_length=150)
    generation_requirements: str = Field(default='', max_length=8000)

    @field_validator('question_id')
    @classmethod
    def public_question_id(cls, value):
        from uuid import UUID
        try:return 'exam:public:'+str(UUID(value.removeprefix('exam:public:')))
        except ValueError:raise ValueError('题目 ID 须为 exam 公共题库 UUID') from None

    @field_validator('target_grade')
    @classmethod
    def required_target_grade(cls, value):
        return Question.valid_target_grade(value)


class CoursewareUpdate(StrictModel):
    title: str = Field(default='', min_length=1, max_length=150)
    target_grade: str = Field(default='', min_length=1, max_length=30)
    target_semester: Literal['全学期','上半学期','下半学期'] = '全学期'
    target_curriculum: list[TargetKnowledgePoint] = Field(default_factory=list)
    generation_requirements: str = Field(default='', max_length=8000)
    options: GenerationOptions = Field(default_factory=GenerationOptions)

    @field_validator('target_grade')
    @classmethod
    def required_target_grade(cls, value):
        return Question.valid_target_grade(value)


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


class GeneratedModel(BaseModel):
    # Presentation content is not rejected for size, counts or stylistic metadata.
    model_config = ConfigDict(extra='ignore', str_strip_whitespace=True)


class Point(GeneratedModel):
    title: str = ''
    display: str = ''
    notes: str = ''


class Variant(GeneratedModel):
    question_id: str | None = None
    source: str = 'generated'
    stem: str = ''
    answer: str = ''
    analysis: str = ''
    knowledge_points: list[str] = Field(default_factory=list)
    source_image_indices: list[int] = Field(default_factory=list, description='沿用原题配图时填写附件索引（从0开始）；独立变式不得引用无关原图')


class Lesson(GeneratedModel):
    title: str = '题目讲解'
    objective: str = ''
    knowledge_points: list[Point] = Field(default_factory=list)
    solution_steps: list[Point] = Field(default_factory=list)
    answer: str = ''
    pitfalls: list[Point] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)


class KnowledgePoint(Point):
    why: str = Field(default='', description='原理或适用条件')
    application: str = Field(default='', description='用本题条件说明如何应用')
    resource_ids: list[str] = Field(default_factory=list, description='实际采用的知识资源 ID')


class SolutionStep(Point):
    narration: str = Field(default='', description='本步骤的实际口语讲解，不限制字数')


class LecturePage(GeneratedModel):
    kind: str = Field(default='知识', description='原题、知识、解题、答案、总结等页面类型')
    title: str = ''
    display: str = Field(default='', description='上屏内容，不限制字数，由排版负责呈现')
    narration: str = Field(default='', description='本页口语讲解；解题页可由步骤台词组合')
    knowledge_indices: list[int] = Field(default_factory=list)
    solution_indices: list[int] = Field(default_factory=list)
    variant_index: int | None = Field(default=None, description='变式页对应 variants 的索引，从0开始；其他页为空')
    diagram: Diagram | None = None
    source_image_index: int | None = Field(default=None,description='直接引用原题附件，索引从0开始；看原图、数实物及对比选项时优先使用')
    source_image_caption: str = Field(default='',description='指出应观察原图中的哪些对象、位置或条件')
    visual_reason: str = ''

    @field_validator('diagram', mode='wrap')
    @classmethod
    def optional_figure(cls, value, handler):
        # An unusable optional illustration must not block the PPT and full text.
        try:
            return handler(value)
        except (ValueError, TypeError):
            return None


class IllustratedLesson(Lesson):
    complexity: str = Field(default='simple', description='描述性标签，不控制页数、字数或时长')
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list)
    solution_steps: list[SolutionStep] = Field(default_factory=list)
    pages: list[LecturePage] = Field(default_factory=list)

    @model_validator(mode='after')
    def prepare_presentation(self):
        for step in self.solution_steps:
            step.narration=step.narration or step.notes or step.display
        steps=[step.model_dump() for step in self.solution_steps]
        used=set()
        for page in self.pages:
            page.knowledge_indices=list(dict.fromkeys(i for i in page.knowledge_indices if 0<=i<len(self.knowledge_points)))
            page.solution_indices=list(dict.fromkeys(i for i in page.solution_indices if 0<=i<len(steps))) if page.kind=='解题' else []
            used.update(page.solution_indices)
            if page.solution_indices:
                page.narration=page_narration(page.model_dump(),steps)
            else:
                page.narration=page.narration or page.display
        # Missing references become additional display pages, not generation errors.
        if self.pages:
            missing=[i for i in range(len(steps)) if i not in used]
            position=next((i for i,p in enumerate(self.pages) if p.kind=='答案'),len(self.pages))
            for offset in range(0,len(missing),4):
                indices=missing[offset:offset+4]
                self.pages.insert(position,LecturePage(kind='解题',title='解题过程',solution_indices=indices,
                    display='\n'.join(steps[i]['display'] for i in indices),
                    narration='\n'.join(steps[i]['narration'] for i in indices)))
                position+=1
        reading=self.model_dump()
        self.pages=[LecturePage.model_validate(page) for page in complete_page_content(reading['pages'],reading)]
        self.summary=reading['summary']
        self.pitfalls=[Point.model_validate(point) for point in reading['pitfalls']]
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
