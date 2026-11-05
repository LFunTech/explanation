import httpx
import pytest

from app.main import create_app
from app.engine import package_links
from tests.test_core import setup, request


@pytest.mark.asyncio
async def test_native_assets_and_signed_data_use_existing_package(tmp_path):
    _,_,cfg=setup(tmp_path);cfg.api_key='test-microapp-key';app=create_app(cfg);store=app.state.store
    job,_=store.create_lesson(request(voice=False,variant_count=0))
    await app.state.engine.generate(store.claim())
    package=store.job(job)['items'][0]['package_id'];links=package_links(cfg,package)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/microapp/'+package+'/data')).status_code==403
        data=await client.get(links['microapp_data_url'])
        assert data.status_code==200,data.text
        assert data.json()['manifest']['package_id']==package and data.json()['slides']
        assert data.json()['questionLayout']['stem']
        assert 'variantLayouts' in data.json()
        assert (await client.get('/microapp/'+package+'/data?expires=1&token=invalid')).status_code==403
        assets=(await client.get('/static/microapp-assets.json')).json()
        assert '<iframe' not in assets['shellHtml'] and 'id="lesson-content"' in assets['shellHtml']
        assert ':host' in assets['shellCss'] and '.micro-body' in assets['contentCss']
        runtime=await client.get('/static/microapp-runtime.js')
        assert 'javascript' in runtime.headers['content-type']
        assert 'export function createContent(scope)' in runtime.text and 'export function createShell(scope)' in runtime.text
        module=(await client.get('/static/courseware-microapp.js')).text
        assert 'export async function mount(' in module and 'export function unmount(' in module


def test_math_font_inlining_preserves_every_font_face():
    import re
    from app.config import ROOT
    from app.exporters import inline_math_assets
    source=(ROOT/'web/vendor/katex/katex.min.css').read_text(encoding='utf-8')
    css,_,_=inline_math_assets()
    assert len(re.findall(r'@font-face',css))==len(re.findall(r'@font-face',source))==20
    assert len(re.findall(r'src:url\(data:font/woff2;base64,',css))==20
    assert 'font-family:KaTeX_Main' in css and 'url(fonts/' not in css
