"""Keep curriculum editions separate from textbook editions and school years."""
from pydantic import BaseModel, Field


SCOPE_FIELDS=('curriculum_version','school_system','publisher','textbook_version','volume')


class TeachingScope(BaseModel):
    curriculum_version: str = Field(default='',max_length=80,description='课标版本，如 2022；不是教材版次')
    school_system: str = Field(default='',max_length=40,description='学制，如五四制、六三制')
    publisher: str = Field(default='',max_length=80)
    textbook_version: str = Field(default='',max_length=80,description='教材版次或修订版本')
    volume: str = Field(default='',max_length=40,description='上册、下册、全一册或具体模块')


def scope_matches(metadata, requested, source_type):
    # Do not infer a textbook from region, subject, or a knowledge-point name.
    if source_type=='textbook':
        if not all(requested.get(k) and metadata.get(k)==requested[k] for k in ('school_system','publisher','textbook_version','volume')):
            return False
    for key in SCOPE_FIELDS:
        wanted=requested.get(key,'')
        actual=metadata.get(key,'')
        if key=='curriculum_version' and source_type=='curriculum':
            actual=actual or metadata.get('version','')
        if key=='curriculum_version' and wanted and source_type in {'curriculum','textbook'} and actual!=wanted:
            return False
        if wanted and actual and wanted!=actual:
            return False
    return True


def validate_document_metadata(meta):
    if not isinstance(meta,dict) or not all(isinstance(meta.get(k),str) and meta[k].strip() for k in ('title','subject','source','version')):
        raise ValueError('元数据须包含 title、subject、source、version')
    if meta.get('source_type') not in {'curriculum','textbook','reference'}:
        raise ValueError('source_type 须为 curriculum、textbook 或 reference')
    if not isinstance(meta.get('grades',[]),list) or not all(isinstance(x,str) and x.strip() for x in meta.get('grades',[])):
        raise ValueError('grades 须为非空年级字符串的数组')
    scope=TeachingScope.model_validate({k:meta[k] for k in SCOPE_FIELDS if k in meta}).model_dump()
    if meta['source_type']=='curriculum':
        scope['curriculum_version']=scope['curriculum_version'] or meta['version']
    if meta['source_type']=='textbook' and (not meta.get('grades') or not all(scope[k] for k in ('school_system','publisher','textbook_version','volume'))):
        raise ValueError('教材须明确适用年级、学制、出版社、教材版本和册次；未知资料可先按教学参考登记')
    return {**meta,**scope}


def generation_question(question):
    # Empty new selectors should not invalidate already reviewed content.
    return {k:v for k,v in question.model_dump().items() if k not in SCOPE_FIELDS or v}
