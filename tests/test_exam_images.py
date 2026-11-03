import base64
import io
import json
from contextlib import contextmanager

import pytest
from PIL import Image
from botocore.exceptions import ClientError

from app.config import Settings
from app.exam import Exam,parse_stem
from app.exam_storage import ExamImageStorage,object_location
from app.images import raster_data_url
from app.providers import GenerationError


def settings(tmp_path):
    return Settings(_env_file=None,data_dir=tmp_path,exam_minio_endpoint='https://storage.invalid',
        exam_minio_access_key='test-key',exam_minio_secret_key='test-secret',exam_minio_allowed_buckets='exam')


@pytest.mark.parametrize('uri',[
    'https://other.invalid/exam/picture.png','minio://private/picture.png',
    'minio://exam/../picture.png','minio://exam/a.png?token=anything','minio://exam//a.png'])
def test_storage_only_accepts_configured_bucket_objects(uri):
    with pytest.raises(GenerationError,match='配置范围'):
        object_location(uri,{'exam'})


def test_minio_image_download_converts_transparency_and_closes_stream(tmp_path):
    image=Image.new('RGBA',(20,20),(0,0,0,0))
    image.putpixel((10,10),(0,0,0,255))
    raw=io.BytesIO();image.save(raw,format='PNG')
    body=io.BytesIO(raw.getvalue())
    class Client:
        def get_object(self,**kwargs):
            assert kwargs==dict(Bucket='exam',Key='questions/source.png')
            return dict(Body=body,ContentLength=len(raw.getvalue()))
    reader=ExamImageStorage(settings(tmp_path));reader.client=Client()
    data=reader.read('minio://exam/questions/source.png')
    assert body.closed
    with Image.open(io.BytesIO(base64.b64decode(data.split(',',1)[1]))) as output:
        assert output.size==(20,20)
        assert min(output.getpixel((0,0)))>240
        assert max(output.getpixel((10,10)))<100


def test_storage_errors_never_return_provider_credentials(tmp_path):
    class Client:
        def get_object(self,**kwargs):
            raise ClientError({'Error':{'Code':'AccessDenied','Message':'test-secret'}},'GetObject')
    reader=ExamImageStorage(settings(tmp_path));reader.client=Client()
    with pytest.raises(GenerationError,match='读取失败') as error:
        reader.read('minio://exam/questions/source.png')
    assert 'test-secret' not in str(error.value)


def test_required_question_images_exclude_answer_only_figures():
    assert parse_stem('<p>根据图形回答</p><img data-file-id="source-image">')['requires_images']
    body={'stem':'计算 1+1','attributes':{'answerInlineAssets':[{'fileId':'answer-image'}]}}
    body['subQuestions']=[{'stem':'求结果','analysis':'<img src="https://example.com/solution.png">'}]
    assert not parse_stem(json.dumps(body))['requires_images']
    body['options']=[{'label':'A','content':'<img data-file-id="option-image">'}]
    assert parse_stem(json.dumps(body))['requires_images']


def test_missing_image_association_does_not_return_empty_success(tmp_path):
    class Result:
        def fetchone(self):return {'stem':'<img data-file-id="missing-image">'}
        def fetchall(self):return []
    class Database:
        def execute(self,*args):return Result()
    class Source(Exam):
        @contextmanager
        def connect(self):yield Database()
    with pytest.raises(GenerationError,match='尚未同步附件'):
        Source(settings(tmp_path)).images('756ac6cd-befa-4a40-9972-f0013f6f7444')


@pytest.mark.asyncio
async def test_submission_loads_canonical_exam_image_before_saving(tmp_path,monkeypatch):
    import httpx
    from app.main import create_app
    from app.samples import SAMPLES
    cfg=settings(tmp_path)
    cfg.vector_database_url='';cfg.exam_db_host='';cfg.api_key=''
    image=io.BytesIO();Image.new('RGB',(20,20),'white').save(image,format='PNG')
    data=raster_data_url(image.getvalue())
    calls=[]
    def load_images(self,question_id):
        calls.append(question_id)
        return {'images':[data]}
    monkeypatch.setattr(Exam,'images',load_images)
    app=create_app(cfg)
    question={**SAMPLES[0]['question'],'question_id':'exam:public:756ac6cd-befa-4a40-9972-f0013f6f7444',
        'source':'exam/public/question/756ac6cd-befa-4a40-9972-f0013f6f7444','requires_images':True,'images':[]}
    request={'assignment_id':'image-submission-test','questions':[question],
        'options':{'mode':'qwen','voice':False,'variant_count':0}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/api/jobs',json=request,headers={'Idempotency-Key':'image-request'})
        assert response.status_code==202
        saved=response.json()['items'][0]['question']
        assert saved['images'][0].startswith('data:image/jpeg;base64,')
        assert calls==[question['question_id']]
        replay=await client.post('/api/jobs',json=request,headers={'Idempotency-Key':'image-request'})
        assert replay.json()['idempotent_replay']
        question['source']='different-source'
        invalid=await client.post('/api/jobs',json=request)
        assert invalid.status_code==422
