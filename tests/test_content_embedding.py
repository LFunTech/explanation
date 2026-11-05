import json
import re
import zipfile
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from lxml import html
from pptx import Presentation

from app.engine import package_links
from app.main import create_app
from tests.test_core import request,setup


@pytest.mark.asyncio
async def test_exported_content_is_independent_of_application_player(tmp_path):
    _,_,cfg=setup(tmp_path)
    app=create_app(cfg);store=app.state.store;engine=app.state.engine
    job,_=store.create_job(request(voice=False));await engine.generate(store.claim())
    package_id=store.job(job)['items'][0]['package_id']
    links=package_links(cfg,package_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        content=await client.get(links['html_url']);player=await client.get(links['player_url'])
        packaged=await client.get(links['package_player_url'])
    assert content.status_code==player.status_code==200
    document=html.fromstring(content.text)
    assert document.xpath('//*[@id="stage"]')
    assert not document.xpath('//header|//nav|//iframe|//*[@id="speak" or @id="progress" or @id="motion-toggle"]')
    shell=html.fromstring(player.text)
    assert shell.xpath('//*[@id="lesson-content"]') and shell.xpath('//*[@id="speak"]')
    assert not shell.xpath('//*[@id="motion-toggle"]')
    config=json.loads(re.search(r'window.COURSEWARE_HOST=(.*?);',player.text).group(1))
    assert config['contentUrl']==links['content_url']
    directory=cfg.data_dir/'packages'/package_id
    assert packaged.status_code==200
    packaged_config=json.JSONDecoder().raw_decode(packaged.text.split('window.COURSEWARE_HOST=',1)[1])[0]
    assert packaged_config['contentHtml']==content.text.replace('\r\n','\n')
    assert packaged_config['contentUrl']=='' and packaged_config['audio']=={}
    assert html.fromstring(packaged.text).xpath('//*[@id="speak"]')
    with zipfile.ZipFile(directory/'package.zip') as archive:
        assert archive.read('index.html').decode('utf-8')==content.text
        assert archive.read('player.html').decode('utf-8')==packaged.text
        assert 'player.html' in json.loads(archive.read('manifest.json'))['files']
    question=store.package(package_id)['question']
    footer=' · '+question['subject']
    for slide in Presentation(directory/'courseware.pptx').slides:
        labels=[shape.text for shape in slide.shapes if shape.has_text_frame]
        assert any(label.endswith(footer) for label in labels)
        assert not any(label.endswith(' · '+question['region']) for label in labels)


@pytest.mark.asyncio
async def test_player_access_and_embedded_content_use_signed_links(tmp_path):
    _,_,cfg=setup(tmp_path);cfg.api_key='test-only-key'
    app=create_app(cfg);store=app.state.store;engine=app.state.engine
    job,_=store.create_job(request(voice=False));await engine.generate(store.claim())
    package_id=store.job(job)['items'][0]['package_id']
    links=package_links(cfg,package_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/player/'+package_id)).status_code==403
        assert (await client.get('/packages/'+package_id+'/player.html')).status_code==403
        assert (await client.get(links['package_player_url'])).status_code==200
        assert (await client.get('/embed/'+package_id+'/config')).status_code==403
        settings=await client.get(links['embed_config_url'])
        assert settings.status_code==200 and settings.headers['cache-control']=='no-store'
        assert settings.json()['motion'] is True
        assert (await client.get(settings.json()['contentUrl'])).status_code==200
        response=await client.get(links['player_url'])
        assert response.status_code==200
        config=json.loads(re.search(r'window.COURSEWARE_HOST=(.*?);',response.text).group(1))
        assert (await client.get(config['contentUrl'])).status_code==200
        assert (await client.get('/player/not-a-package')).status_code==404


@pytest.mark.asyncio
async def test_minio_player_loads_the_published_object_directly(tmp_path):
    _,_,cfg=setup(tmp_path)
    app=create_app(cfg);store=app.state.store;engine=app.state.engine
    job,_=store.create_job(request(voice=False));await engine.generate(store.claim())
    package_id=store.job(job)['items'][0]['package_id']
    cfg.s3_bucket='exam';cfg.s3_endpoint_url='https://internal.example'
    cfg.s3_public_endpoint_url='https://objects.example'
    cfg.s3_access_key_id='test-access';cfg.s3_secret_access_key='test-secret'
    cfg.api_key='test-api'
    links=package_links(cfg,package_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/minio-player/'+package_id)).status_code==403
        assert (await client.get('/minio-package/'+package_id+'/player')).status_code==403
        assert (await client.get(links['minio_package_player_url'])).status_code==409
        assert (await client.get(links['minio_player_url'])).status_code==409
        store.record_storage(package_id,dict(bucket='exam',prefix='explanations/test/'+package_id))
        response=await client.get(links['minio_player_url'])
        embed=await client.get(links['embed_config_url']+'&source=minio')
        assert embed.status_code==200
        assert urlsplit(embed.json()['contentUrl']).netloc=='objects.example'
        assert urlsplit(embed.json()['fallback']['contentUrl']).path==urlsplit(links['content_url']).path
        assert (await client.get(embed.json()['fallback']['contentUrl'])).status_code==200
        assert 'test-secret' not in embed.text
        packaged=await client.get(links['minio_package_player_url'])
        assert packaged.status_code==307 and packaged.headers['cache-control']=='no-store'
        location=urlsplit(packaged.headers['location'])
        assert location.netloc=='objects.example' and location.path.endswith('/player.html')
        assert parse_qs(location.query)['X-Amz-Signature']
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    config=json.loads(re.search(r'window.COURSEWARE_HOST=(.*?);',response.text).group(1))
    url=urlsplit(config['contentUrl']);query=parse_qs(url.query)
    assert url.netloc=='objects.example'
    assert url.path==f'/exam/explanations/test/{package_id}/online.html'
    assert query['X-Amz-Expires']==['3600'] and query['X-Amz-Signature']
    assert query['response-content-disposition']==['inline']
    assert 'test-secret' not in response.text
    assert urlsplit(config['fallback']['contentUrl']).path==urlsplit(links['content_url']).path
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get(config['fallback']['contentUrl'])).status_code==200
