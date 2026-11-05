"""Export the local knowledge PostgreSQL and task SQLite without credentials.

python -m scripts.backup_local --output backups/2026-11-03
Requires PostgreSQL client tools (PATH or --pg-bin). Never reads the remote exam DB.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime,timezone
from zipfile import ZipFile,ZIP_DEFLATED

import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.config import Settings


def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pg-bin',type=Path)
    args=parser.parse_args()
    cfg=Settings()
    if not cfg.vector_database_url:
        parser.error('VECTOR_DATABASE_URL is not configured')
    connection=conninfo_to_dict(cfg.vector_database_url)
    if connection.get('host') not in {'localhost','127.0.0.1','::1'} or connection.get('hostaddr',connection.get('host')) not in {'localhost','127.0.0.1','::1'}:
        parser.error('Only an explicitly local knowledge database can be backed up by this script')
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Output directory must be empty; previous backups are immutable')
    args.output.mkdir(parents=True,exist_ok=True)
    suffix='.exe' if os.name=='nt' else ''
    binary=str(args.pg_bin/('pg_dump'+suffix)) if args.pg_bin else shutil.which('pg_dump')
    restore=str(args.pg_bin/('pg_restore'+suffix)) if args.pg_bin else shutil.which('pg_restore')
    if not binary or not restore:
        parser.error('pg_dump and pg_restore are required; pass --pg-bin')
    # Passwords are child-process environment only, never CLI arguments or files.
    env=os.environ.copy()
    for key,name in [('host','PGHOST'),('port','PGPORT'),('dbname','PGDATABASE'),('user','PGUSER'),('password','PGPASSWORD')]:
        if key in connection:env[name]=connection[key]
    env['PGCONNECT_TIMEOUT']='10'
    tables=('kb_nodes','kb_edges','kb_documents','kb_chunks')
    with tempfile.TemporaryDirectory(prefix='explanation-backup-') as temporary:
        staging=Path(temporary)
        pg_file=staging/'knowledge.dump'
        with psycopg.connect(cfg.vector_database_url) as db:
            db.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            snapshot=db.execute('SELECT pg_export_snapshot()').fetchone()[0]
            counts={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in tables}
            version=db.execute('SHOW server_version').fetchone()[0]
            command=[binary,'--format=custom','--compress=9','--no-owner','--no-acl','--schema=public',
                     '--snapshot='+snapshot,'--file='+str(pg_file)]
            result=subprocess.run(command,env=env,capture_output=True)
            if result.returncode:
                raise RuntimeError('pg_dump failed; check local database access and client/server versions')
        listing=subprocess.run([restore,'--list',str(pg_file)],capture_output=True,check=True).stdout.decode('utf-8')
        if not all('TABLE DATA public '+table+' ' in listing for table in tables):
            raise RuntimeError('Backup archive is missing required knowledge tables')
        sqlite_file=staging/'app.sqlite3'
        with closing(sqlite3.connect(cfg.data_dir/'app.sqlite3')) as source,closing(sqlite3.connect(sqlite_file)) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise RuntimeError('SQLite integrity check failed')
            sqlite_counts={name:target.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('jobs','items','packages','resources','callbacks','item_events')}
        with ZipFile(args.output/'app.sqlite3.zip','w',ZIP_DEFLATED,compresslevel=9) as archive:
            archive.write(sqlite_file,'app.sqlite3')
        # Keep each Git object comfortably below the hosting limit, even as vectors grow.
        parts=[]
        with pg_file.open('rb') as stream:
            number=1
            while block:=stream.read(20*1024*1024):
                path=args.output/f'knowledge.dump.part{number:03}'
                path.write_bytes(block);parts.append(path.name);number+=1
        manifest=dict(format_version=1,created_at=datetime.now(timezone.utc).isoformat(),
            database=connection['dbname'],postgres_version=version,knowledge_tables=counts,task_tables=sqlite_counts,
            postgres_parts=parts,postgres_sha256=sha256(pg_file),sqlite_sha256=sha256(sqlite_file),
            included=['Local PostgreSQL knowledge documents, chunks, vectors, graph and schema','Task SQLite consistent online backup'],
            excluded=['.env and credentials','Remote exam business database','Generated package files and audio','Original PDFs, OCR caches and working checkpoints'],
            restore_notes='Restore into an empty local database. Restore data/packages separately if old lesson URLs are needed. Pending/generating tasks resume on startup; callbacks may resume too.')
        manifest['files']={p.name:dict(bytes=p.stat().st_size,sha256=sha256(p)) for p in args.output.iterdir() if p.is_file()}
        (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(output=str(args.output),knowledge=counts,tasks=sqlite_counts,files=manifest['files']),ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
