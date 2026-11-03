"""单进程持久化任务队列。所有状态转换和回调入队都在事务中完成。"""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(dump(value).encode('utf-8')).hexdigest()


def aggregate(items):
    states = [x['status'] for x in items]
    if not states:
        return 'not_generated'
    if 'generating' in states:
        return 'generating'
    if 'pending' in states:
        return 'pending'
    if all(x == 'completed' for x in states):
        return 'completed'
    if all(x == 'failed' for x in states):
        return 'failed'
    return 'partial_failed'


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL, title TEXT NOT NULL,
                    request_json TEXT NOT NULL, request_hash TEXT NOT NULL, idem_key TEXT UNIQUE,
                    revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_assignment ON jobs(assignment_id, created_at);
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
                    question_id TEXT NOT NULL, question_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', stage TEXT NOT NULL DEFAULT '等待生成',
                    progress INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                    context_json TEXT, package_id TEXT, reused INTEGER NOT NULL DEFAULT 0,
                    error_json TEXT, updated_at TEXT NOT NULL, UNIQUE(job_id, question_id));
                CREATE TABLE IF NOT EXISTS packages (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    manifest_json TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS package_fingerprint ON packages(fingerprint, created_at DESC);
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS callbacks (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), revision INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_at REAL NOT NULL DEFAULT 0, error TEXT, payload TEXT NOT NULL,
                    UNIQUE(job_id, revision));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_job(self, request, idem_key=None):
        body, hash_ = request.model_dump(), digest(request.model_dump())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if idem_key:
                previous = db.execute('SELECT id,request_hash FROM jobs WHERE idem_key=?', (idem_key,)).fetchone()
                if previous:
                    if previous['request_hash'] != hash_:
                        raise Conflict('同一 Idempotency-Key 不能用于不同的请求内容')
                    return previous['id'], True
            job_id, stamp = uuid4().hex, now()
            db.execute('INSERT INTO jobs(id,assignment_id,title,request_json,request_hash,idem_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                       (job_id, request.assignment_id, request.title, dump(body), hash_, idem_key, stamp, stamp))
            for q in request.questions:
                db.execute('INSERT INTO items(id,job_id,question_id,question_json,updated_at) VALUES(?,?,?,?,?)',
                           (uuid4().hex, job_id, q.question_id, dump(q.model_dump()), stamp))
        return job_id, False

    def job(self, job_id, db=None):
        if db is None:
            with self.connect() as connection:
                return self.job(job_id, connection)
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            return None
        job = dict(row)
        job['request'] = json.loads(job.pop('request_json'))
        job.pop('request_hash')
        job.pop('idem_key')
        job['items'] = []
        for row in db.execute('SELECT * FROM items WHERE job_id=? ORDER BY rowid', (job_id,)):
            item = dict(row)
            item['question'] = json.loads(item.pop('question_json'))
            item['error'] = json.loads(item.pop('error_json') or 'null')
            item.pop('context_json')
            item['reused'] = bool(item['reused'])
            job['items'].append(item)
        job['status'] = aggregate(job['items'])
        job['progress'] = round(sum(i['progress'] for i in job['items']) / len(job['items']))
        job['callbacks'] = [dict(r) for r in db.execute('SELECT id,revision,status,attempts,error FROM callbacks WHERE job_id=? ORDER BY revision', (job_id,))]
        return job

    def list_jobs(self, assignment_id=None, limit=100):
        with self.connect() as db:
            rows = db.execute('SELECT id FROM jobs ' + ('WHERE assignment_id=? ' if assignment_id else '') + 'ORDER BY created_at DESC LIMIT ?',
                              (assignment_id, limit) if assignment_id else (limit,)).fetchall()
            return [self.job(r['id'], db) for r in rows]

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE items SET status='pending',stage='服务重启，恢复检查点' WHERE status='generating'")

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM items WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE items SET status='generating',attempts=attempts+1,stage='检索知识资源',progress=5,updated_at=? WHERE id=?", (now(), row['id']))
            return dict(row)

    def progress(self, item_id, stage, value):
        with self.connect() as db:
            db.execute('UPDATE items SET stage=?,progress=?,updated_at=? WHERE id=?', (stage, value, now(), item_id))

    def context(self, item_id, value):
        with self.connect() as db:
            db.execute('UPDATE items SET context_json=? WHERE id=?', (dump(value), item_id))

    def finish(self, item_id, *, package_id=None, error=None, reused=False):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE items SET status=?,stage=?,progress=?,package_id=?,error_json=?,reused=?,updated_at=? WHERE id=?',
                       ('failed' if error else 'completed', '生成失败' if error else '已完成', 0 if error else 100,
                        package_id, dump(error) if error else None, int(reused), now(), item_id))
            job_id = db.execute('SELECT job_id FROM items WHERE id=?', (item_id,)).fetchone()['job_id']
            db.execute('UPDATE jobs SET updated_at=? WHERE id=?', (now(), job_id))
            job = self.job(job_id, db)
            if job['status'] in {'completed', 'failed', 'partial_failed'} and job['request']['callback_url']:
                event = dict(event='explanation.completed', event_id=uuid4().hex, job_id=job_id,
                             assignment_id=job['assignment_id'], revision=job['revision'], status=job['status'],
                             items=[dict(question_id=i['question_id'], status=i['status'], package_id=i['package_id'], error=i['error']) for i in job['items']])
                db.execute('INSERT OR IGNORE INTO callbacks(id,job_id,revision,payload) VALUES(?,?,?,?)',
                           (event['event_id'], job_id, job['revision'], dump(event)))

    def retry(self, job_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self.job(job_id, db)
            if not job:
                raise KeyError(job_id)
            if job['status'] in {'pending', 'generating'}:
                raise Conflict('当前作业仍在生成，请等待结束后重试失败题目')
            if not any(i['status'] == 'failed' for i in job['items']):
                raise Conflict('没有失败题目；需要新版本时请创建 force=true 的新任务')
            db.execute("UPDATE items SET status='pending',stage='等待重试',error_json=NULL,progress=0 WHERE job_id=? AND status='failed'", (job_id,))
            db.execute('UPDATE jobs SET revision=revision+1,updated_at=? WHERE id=?', (now(), job_id))

    def find_package(self, fingerprint):
        with self.connect() as db:
            row = db.execute('SELECT manifest_json FROM packages WHERE fingerprint=? ORDER BY created_at DESC LIMIT 1', (fingerprint,)).fetchone()
            return json.loads(row[0]) if row else None

    def package(self, package_id):
        with self.connect() as db:
            row = db.execute('SELECT manifest_json FROM packages WHERE id=?', (package_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_package(self, manifest):
        with self.connect() as db:
            db.execute('INSERT INTO packages VALUES(?,?,?,?)', (manifest['package_id'], manifest['fingerprint'], dump(manifest), now()))

    def resources(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM resources ORDER BY updated_at DESC,id')]

    def import_resources(self, resources, *, seed=False):
        with self.connect() as db:
            for r in resources:
                db.execute('INSERT INTO resources VALUES(?,?,?) ON CONFLICT(id) ' + ('DO NOTHING' if seed else 'DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at'),
                           (r['id'], dump(r), now()))
