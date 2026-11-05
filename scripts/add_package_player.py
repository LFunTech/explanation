"""Add standalone player.html to existing packages without regenerating lessons."""
import argparse
import hashlib
import json
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.engine import retryable_storage_error
from app.exporters import PRESENTATION_VERSION, html_package, html_player, export_pptx
from app.store import Store, dump
from app.variant_visuals import bind_variant_visuals
from app.knowledge_pages import ensure_knowledge_pages
from app.narration import plain_text
from app.page_content import complete_page_content


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-id',action='append',default=[])
    parser.add_argument('--publish',action='store_true',help='Sync changed files to the existing MinIO prefix')
    parser.add_argument('--refresh-content',action='store_true',help='Refresh index.html and online.html with the current renderer too')
    args=parser.parse_args();cfg=Settings();store=Store(cfg.data_dir/'app.sqlite3')
    ids=args.package_id or sorted({i['package_id'] for job in store.list_lessons() for i in job['items']
        if i.get('package_id') and i['status']=='completed'})
    pending=[]
    def save(manifest):
        with store.connect() as db:
            db.execute('UPDATE packages SET manifest_json=? WHERE id=?',(dump(manifest),manifest['package_id']))
    for package_id in ids:
        manifest=store.package(package_id)
        if not manifest:raise ValueError('Unknown package: '+package_id)
        directory=cfg.data_dir/'packages'/package_id
        slides=json.loads((directory/'slides.json').read_text(encoding='utf-8'))
        changed_data=[]
        if args.refresh_content:
            previous=dump(slides)
            slides=complete_page_content(slides,manifest.get('reading',{}))
            bind_variant_visuals(manifest['question'],manifest.get('variants',[]),slides)
            slides=ensure_knowledge_pages(slides,manifest.get('reading',{}).get('knowledge_points',[]),legacy=True)
            for slide in slides:
                slide['narration']=plain_text(slide.get('narration',slide.get('notes','')))
            if dump(slides)!=previous:
                (directory/'slides.json').write_text(json.dumps(slides,ensure_ascii=False,indent=2),encoding='utf-8')
                (directory/'narration.json').write_text(dump([dict(page_id=s['id'],text=s['narration'],audio_file=s.get('audio_file')) for s in slides]),encoding='utf-8')
                export_pptx(directory/'.courseware.pptx.tmp',slides,manifest)
                (directory/'.courseware.pptx.tmp').replace(directory/'courseware.pptx')
                changed_data=[('slides.json','application/json'),('narration.json','application/json'),('courseware.pptx','application/vnd.openxmlformats-officedocument.presentationml.presentation')]
                manifest['slide_count']=len(slides)
        manifest['files']=list(dict.fromkeys([*manifest.get('files',[]),'player.html',*(['online.html'] if args.refresh_content else [])]))
        manifest.setdefault('presentation',{})['version']=PRESENTATION_VERSION
        content=html_package(manifest,slides)
        if args.refresh_content:
            for name,value in [('index.html',content),('online.html',html_package(manifest,slides,inline_audio=False))]:
                (directory/('.'+name+'.tmp')).write_text(value,encoding='utf-8')
                (directory/('.'+name+'.tmp')).replace(directory/name)
        (directory/'.player.html.tmp').write_text(html_player(content_html=content),encoding='utf-8')
        (directory/'.player.html.tmp').replace(directory/'player.html')
        (directory/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary=directory/'.package.zip.tmp'
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(directory.rglob('*')):
                if path.is_file() and path.name!='package.zip' and not any(part.startswith('.') for part in path.relative_to(directory).parts):
                    archive.write(path,path.relative_to(directory))
        temporary.replace(directory/'package.zip')
        print(dump(dict(package_id=package_id,local_ready=True,player_bytes=(directory/'player.html').stat().st_size)),flush=True)
        if args.publish and manifest.get('storage'):pending.append((directory,manifest,changed_data))
        else:save(manifest)
    if pending:
        import boto3
        from boto3.s3.transfer import TransferConfig
        from botocore.config import Config
        client=boto3.client('s3',endpoint_url=cfg.s3_endpoint_url or None,region_name=cfg.s3_region,
            aws_access_key_id=cfg.s3_access_key_id,aws_secret_access_key=cfg.s3_secret_access_key,
            config=Config(signature_version='s3v4',s3={'addressing_style':'path'},connect_timeout=15,
                read_timeout=120,retries={'max_attempts':2},proxies={}))
        transfer=TransferConfig(max_concurrency=2,multipart_threshold=8*1024*1024)
        def publish(entry):
            directory,manifest,changed_data=entry;storage=manifest['storage']
            files=[('player.html','text/html; charset=utf-8'),('package.zip','application/zip'),('manifest.json','application/json')]
            if args.refresh_content:files[0:0]=[('index.html','text/html; charset=utf-8'),('online.html','text/html; charset=utf-8'),('slides.json','application/json'),('narration.json','application/json'),('courseware.pptx','application/vnd.openxmlformats-officedocument.presentationml.presentation')]
            for name,mime in files:
                path=directory/name;checksum=hashlib.sha256(path.read_bytes()).hexdigest();attempt=0
                while True:
                    try:
                        client.upload_file(str(path),storage['bucket'],storage['prefix'].rstrip('/')+'/'+name,Config=transfer,
                            ExtraArgs={'ContentType':mime,'Metadata':{'sha256':checksum}})
                        break
                    except Exception as error:
                        if not retryable_storage_error(error):raise
                        attempt+=1;print(dump(dict(package_id=manifest['package_id'],file=name,retry=attempt)),flush=True)
                        time.sleep(min(30,2**min(attempt,5)))
            save(manifest)
            print(dump(dict(package_id=manifest['package_id'],published=True)),flush=True)
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                for future in as_completed([pool.submit(publish,entry) for entry in pending]):future.result()
        finally:client.close()
    print(dump(dict(local_updated=len(ids),published=len(pending))),flush=True)


if __name__=='__main__':main()
