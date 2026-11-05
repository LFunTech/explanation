from pathlib import Path

import pytest

from tests.test_core import setup,request


@pytest.mark.asyncio
async def test_complete_package_uploads_as_a_folder_and_manifest_is_last(tmp_path,monkeypatch):
    store,engine,cfg=setup(tmp_path)
    cfg.s3_bucket='exam';cfg.s3_endpoint_url='https://minio.example';cfg.s3_prefix='explanations'
    uploaded=[]
    class Client:
        def head_object(self,**kwargs):
            return {}
        def upload_file(self,path,bucket,key,ExtraArgs,Config):
            assert Path(path).is_file() and bucket=='exam'
            assert ExtraArgs['ContentType']
            assert len(ExtraArgs['Metadata']['sha256'])==64
            assert Config.max_concurrency==2
            uploaded.append(key)
    def client(service,**kwargs):
        assert service=='s3' and kwargs['config'].s3['addressing_style']=='path'
        return Client()
    monkeypatch.setattr('boto3.client',client)
    job,_=store.create_job(request(voice=False))
    await engine.generate(store.claim())
    item=store.job(job)['items'][0]
    assert item['status']=='completed'
    manifest=store.package(item['package_id']);prefix=manifest['object_prefix']
    assert manifest['storage']==dict(bucket='exam',prefix=prefix)
    assert all(key.startswith(prefix+'/') for key in uploaded)
    assert {key.removeprefix(prefix+'/') for key in uploaded}>={'index.html','player.html','online.html','courseware.pptx','slides.json','narration.json','manifest.json','package.zip'}
    assert uploaded[-1]==prefix+'/manifest.json'


@pytest.mark.asyncio
async def test_cached_package_publishes_when_storage_is_enabled_later(tmp_path,monkeypatch):
    store,engine,cfg=setup(tmp_path)
    first,_=store.create_job(request(voice=False))
    await engine.generate(store.claim())
    package_id=store.job(first)['items'][0]['package_id']
    original=(tmp_path/'packages'/package_id/'manifest.json').read_bytes()
    cfg.s3_bucket='exam';cfg.s3_prefix='explanations'
    uploads=[]
    def upload(directory,prefix):
        assert directory.name==package_id
        assert store.package(package_id).get('storage') is None
        uploads.append(prefix)
    monkeypatch.setattr(engine,'upload',upload)
    for _ in range(2):
        job,_=store.create_job(request(voice=False))
        await engine.generate(store.claim())
        item=store.job(job)['items'][0]
        assert item['status']=='completed' and item['reused']
        assert item['package_id']==package_id
    assert len(uploads)==1
    assert store.package(package_id)['storage']==dict(bucket='exam',prefix=uploads[0])
    assert (tmp_path/'packages'/package_id/'manifest.json').read_bytes()==original


@pytest.mark.asyncio
async def test_upload_recovers_network_failure_without_resending_completed_files(tmp_path,monkeypatch):
    from botocore.exceptions import EndpointConnectionError
    _,engine,cfg=setup(tmp_path)
    cfg.s3_bucket='exam'
    directory=tmp_path/'folder';directory.mkdir()
    for name in ('index.html','courseware.pptx','manifest.json'):
        (directory/name).write_text(name)
    remote={};attempts=[];sleeps=[]
    class Client:
        def head_object(self,Bucket,Key):
            return {'Metadata':remote.get(Key,{})}
        def upload_file(self,path,bucket,key,ExtraArgs,Config):
            attempts.append(key)
            if key.endswith('index.html') and attempts.count(key)==1:
                raise EndpointConnectionError(endpoint_url='https://minio.example')
            remote[key]=ExtraArgs['Metadata']
    monkeypatch.setattr('boto3.client',lambda *a,**kw:Client())
    async def sleep(delay):sleeps.append(delay)
    monkeypatch.setattr('app.engine.asyncio.sleep',sleep)
    await engine.publish(directory,'folder')
    assert attempts.count('folder/courseware.pptx')==1
    assert attempts.count('folder/index.html')==2
    assert attempts[-1]=='folder/manifest.json'
    assert len(remote)==3 and sleeps==[1]
    (directory/'index.html').write_text('new content')
    await engine.publish(directory,'folder')
    assert attempts.count('folder/index.html')==3
    assert attempts.count('folder/courseware.pptx')==1


@pytest.mark.asyncio
async def test_upload_does_not_retry_access_denied(tmp_path,monkeypatch):
    from botocore.exceptions import ClientError
    _,engine,_=setup(tmp_path)
    calls=[]
    def upload(*args):
        calls.append(args)
        raise ClientError({'Error':{'Code':'AccessDenied'},'ResponseMetadata':{'HTTPStatusCode':403}},'PutObject')
    monkeypatch.setattr(engine,'upload',upload)
    with pytest.raises(ClientError):
        await engine.publish(tmp_path,'folder')
    assert len(calls)==1


@pytest.mark.asyncio
async def test_storage_resume_uses_exported_package_without_rebuilding(tmp_path,monkeypatch):
    import app.engine as module
    store,engine,cfg=setup(tmp_path)
    cfg.s3_bucket='exam'
    job,_=store.create_job(request(voice=False))
    item=store.claim()
    async def interrupted(*args):raise RuntimeError('storage unavailable')
    monkeypatch.setattr(engine,'publish',interrupted)
    with pytest.raises(RuntimeError):await engine.generate(item)
    def no_rebuild(*args,**kwargs):raise AssertionError('Export checkpoint must be reused')
    monkeypatch.setattr(module,'write_package',no_rebuild)
    monkeypatch.setattr(module,'make_slides',no_rebuild)
    calls=[]
    async def published(directory,prefix,item_id):calls.append(prefix)
    monkeypatch.setattr(engine,'publish',published)
    await engine.generate(item)
    assert len(calls)==1 and store.job(job)['items'][0]['status']=='completed'
