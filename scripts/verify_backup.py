"""Verify hashes, SQLite integrity and a full restore into an isolated temporary PG cluster."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from zipfile import ZipFile

import psycopg

from scripts.backup_local import sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup',type=Path)
    parser.add_argument('--pg-bin',type=Path,required=True)
    args=parser.parse_args();root=args.backup
    meta=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for name,info in meta['files'].items():
        if Path(name).name!=name or sha256(root/name)!=info['sha256']:
            raise ValueError('Backup file checksum mismatch')
    suffix='.exe' if os.name=='nt' else ''
    def tool(name):return str(args.pg_bin/(name+suffix))
    def run(*command,**kwargs):
        # On Windows, postgres inherits pipe handles even after pg_ctl exits.
        # Redirect to an actual file so communicate() cannot wait on the server.
        with tempfile.TemporaryFile() as log:
            result=subprocess.run(command,stdout=log,stderr=log,**kwargs)
            if result.returncode:
                log.seek(0)
                raise RuntimeError(Path(command[0]).name+' failed: '+log.read().decode('utf-8',errors='replace')[-2000:])
        return result
    with tempfile.TemporaryDirectory(prefix='explanation-restore-') as temporary:
        temp=Path(temporary)
        archive=temp/'knowledge.dump'
        with archive.open('wb') as output:
            for name in meta['postgres_parts']:
                if name not in meta['files'] or Path(name).name!=name:raise ValueError('Invalid backup part')
                with (root/name).open('rb') as source:shutil.copyfileobj(source,output)
        assert sha256(archive)==meta['postgres_sha256']
        with ZipFile(root/'app.sqlite3.zip') as zip_:
            sqlite_file=temp/'app.sqlite3';sqlite_file.write_bytes(zip_.read('app.sqlite3'))
        assert sha256(sqlite_file)==meta['sqlite_sha256']
        with closing(sqlite3.connect(sqlite_file)) as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in meta['task_tables'] if t in {'jobs','items','packages','resources','callbacks','item_events'}}
            assert counts==meta['task_tables']
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        cluster=temp/'postgres'
        run(tool('initdb'),'--pgdata='+str(cluster),'--username=backup_verify','--auth=trust','--encoding=UTF8','--no-locale')
        started=False
        try:
            run(tool('pg_ctl'),'-D',str(cluster),'-l',str(temp/'postgres.log'),'-o',f'-h 127.0.0.1 -p {port}','--wait','start')
            started=True
            run(tool('pg_restore'),'--host=127.0.0.1','--port='+str(port),'--username=backup_verify','--dbname=postgres','--clean','--if-exists','--no-owner','--no-acl','--exit-on-error',str(archive))
            with psycopg.connect(host='127.0.0.1',port=port,user='backup_verify',dbname='postgres') as db:
                counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('kb_nodes','kb_edges','kb_documents','kb_chunks')}
                assert counts==meta['knowledge_tables']
                embeddings=db.execute('SELECT count(embedding) FROM kb_chunks').fetchone()[0]
                assert db.execute('SELECT kb_cosine(ARRAY[1,0]::real[],ARRAY[1,0]::real[])').fetchone()[0]==1
        finally:
            if started:run(tool('pg_ctl'),'-D',str(cluster),'--mode=fast','--wait','stop')
        print(json.dumps(dict(verified=True,knowledge=counts,embedded=embeddings,sqlite_integrity='ok',isolated_restore='passed'),ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
