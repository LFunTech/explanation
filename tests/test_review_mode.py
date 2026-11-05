import copy
import json

import pytest

from app.config import Settings
from app.models import GenerationOptions, IllustratedLesson, Question
from app.providers import GenerationError, Qwen
from tests.test_illustrated_lessons import lesson


@pytest.mark.asyncio
async def test_default_generation_skips_content_review_and_retains_unlabelled_knowledge(tmp_path):
    cfg=Settings(_env_file=None)
    assert cfg.qwen_independent_review is False
    draft=lesson();draft['pages'][1]['knowledge_indices']=[]
    original=copy.deepcopy(draft);calls=[]
    async def generate(instruction,data,schema,images=(),progress=None):
        calls.append(schema)
        assert schema is IllustratedLesson
        return schema.model_validate(draft)
    qwen=Qwen(cfg);qwen.json=generate;saved=tmp_path/'draft.json'
    result,report=await qwen.lesson(Question(question_id='direct',stem='直接生成。'),[],
        GenerationOptions(variant_count=0),lambda *args:None,checkpoint=saved)
    assert calls==[IllustratedLesson]
    assert result['knowledge_points']==original['knowledge_points']
    assert result['pages'][1]['knowledge_indices']==[]
    assert result['answer']==original['answer']
    assert report['type']=='not_reviewed' and report['passed'] is None and report['rounds']==[]
    assert json.loads(saved.read_text(encoding='utf-8'))['phase']=='ready'


@pytest.mark.asyncio
async def test_disabling_review_can_resume_existing_draft_without_llm_calls(tmp_path):
    cfg=Settings(_env_file=None,qwen_independent_review=True)
    qwen=Qwen(cfg);calls=[];saved=tmp_path/'draft.json'
    async def generate(instruction,data,schema,images=(),progress=None):
        calls.append(schema)
        if schema is IllustratedLesson:return schema.model_validate(lesson())
        raise GenerationError('qwen_timeout','复核超时')
    qwen.json=generate
    args=(Question(question_id='resume-direct',stem='恢复草稿。'),[],GenerationOptions(variant_count=0),lambda *a:None)
    with pytest.raises(GenerationError):await qwen.lesson(*args,checkpoint=saved)
    previous_identity=cfg.generation_identity()
    cfg.qwen_independent_review=False
    assert cfg.generation_identity()!=previous_identity
    count=len(calls)
    result,report=await qwen.lesson(*args,checkpoint=saved)
    assert len(calls)==count and report['passed'] is None and result['answer']=='C'
