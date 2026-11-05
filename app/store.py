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


def lesson_key(question):
    """A workspace lesson belongs to a question AND its teaching target."""
    from .teaching import teaching_target
    data=question.model_dump() if hasattr(question,'model_dump') else question
    target=teaching_target(data)
    # Selection order and provenance do not change what is being taught.
    points=sorted({dump(dict(knowledge_point=p['knowledge_point'],grade=p['grade'],
                            curriculum_requirements=sorted(set(p['curriculum_requirements']))))
                   for p in target['target_curriculum']})
    identity=dict(question_id=data['question_id'],target_grade=target['target_grade'],target_curriculum=points)
    # Keep historical (unspecified/full-term) canonical records addressable.
    if target['target_semester']!='全学期':identity['target_semester']=target['target_semester']
    return digest(identity)


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
                CREATE TABLE IF NOT EXISTS lessons (
                    question_id TEXT PRIMARY KEY, item_id TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS lesson_targets (
                    lesson_key TEXT PRIMARY KEY, question_id TEXT NOT NULL, item_id TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS lesson_requests (
                    idem_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, job_id TEXT NOT NULL);
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
                CREATE TABLE IF NOT EXISTS item_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL REFERENCES items(id),
                    message TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS item_events_item ON item_events(item_id,id DESC);
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS callbacks (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), revision INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_at REAL NOT NULL DEFAULT 0, error TEXT, payload TEXT NOT NULL,
                    UNIQUE(job_id, revision));
            ''')
            if 'generation' not in {r['name'] for r in db.execute('PRAGMA table_info(items)')}:
                db.execute('ALTER TABLE items ADD COLUMN generation INTEGER NOT NULL DEFAULT 0')
            # Preserve the previous canonical records without deleting history.
            for row in db.execute('SELECT i.id,i.question_id,i.question_json FROM lessons l JOIN items i ON i.id=l.item_id').fetchall():
                db.execute('INSERT OR IGNORE INTO lesson_targets VALUES(?,?,?)',
                           (lesson_key(json.loads(row['question_json'])),row['question_id'],row['id']))

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
            item['events'] = [dict(e) for e in db.execute('SELECT message,created_at FROM item_events WHERE item_id=? ORDER BY id DESC LIMIT 12',(item['id'],))][::-1]
            job['items'].append(item)
        job['status'] = aggregate(job['items'])
        job['progress'] = round(sum(i['progress'] for i in job['items']) / len(job['items']))
        job['callbacks'] = [dict(r) for r in db.execute('SELECT id,revision,status,attempts,error FROM callbacks WHERE job_id=? ORDER BY revision', (job_id,))]
        return job

    def lesson_replay(self, idem_key, request_hash):
        if not idem_key:return None
        with self.connect() as db:
            replay=db.execute('SELECT * FROM lesson_requests WHERE idem_key=?',(idem_key,)).fetchone()
            if replay:
                if replay['request_hash']!=request_hash:raise Conflict('同一 Idempotency-Key 不能用于不同的请求内容')
                return replay['job_id']

    def create_lesson(self, request, idem_key=None, *, request_hash=None):
        """Create an independent courseware; only a repeated idempotency key replays it."""
        if len(request.questions)!=1 or request.callback_url:
            raise ValueError('课件工作台一次生成一道题，不使用作业回调')
        body=request.model_dump();hash_=request_hash or digest(body);question=request.questions[0]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if idem_key:
                replay=db.execute('SELECT * FROM lesson_requests WHERE idem_key=?',(idem_key,)).fetchone()
                if replay:
                    if replay['request_hash']!=hash_:raise Conflict('同一 Idempotency-Key 不能用于不同的请求内容')
                    return replay['job_id'],True
            # A fresh courseware must call the generator, even for identical inputs.
            # Keep the original request hash for retries of the caller's request.
            body['options']['force']=True
            job_id,item_id,stamp=uuid4().hex,uuid4().hex,now()
            db.execute('INSERT INTO jobs(id,assignment_id,title,request_json,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                       (job_id,request.assignment_id,request.title,dump(body),hash_,stamp,stamp))
            db.execute('INSERT INTO items(id,job_id,question_id,question_json,updated_at) VALUES(?,?,?,?,?)',
                       (item_id,job_id,question.question_id,dump(question.model_dump()),stamp))
            if idem_key:db.execute('INSERT INTO lesson_requests VALUES(?,?,?)',(idem_key,hash_,job_id))
        return job_id,False

    def lesson_page(self, page=1, page_size=20, *, query='', grade='', knowledge_point='', status='all'):
        """Filter all courseware metadata, then hydrate only the requested page."""
        with self.connect() as db:
            rows=db.execute("""SELECT i.id,i.job_id,i.status,j.title,j.assignment_id,
                json_extract(i.question_json,'$.stem') AS stem,
                json_extract(i.question_json,'$.target_grade') AS target_grade,
                json_extract(i.question_json,'$.grade') AS grade,
                json_extract(i.question_json,'$.grades') AS grades,
                json_extract(i.question_json,'$.source_grades') AS source_grades,
                json_extract(i.question_json,'$.target_curriculum') AS target_curriculum,
                json_extract(i.question_json,'$.knowledge_points') AS knowledge_points
                FROM items i JOIN jobs j ON i.job_id=j.id
                ORDER BY j.created_at DESC,i.rowid DESC""").fetchall()
            counts=dict(total=len(rows),completed=0,active=0,failed=0)
            grades=set();points=set();matched=[];needle=query.strip().casefold()
            for row in rows:
                counts['active' if row['status'] in {'pending','generating'} else row['status']]+=1
                row_grades=([row['target_grade']] if row['target_grade'] else
                    json.loads(row['grades'] or '[]') or json.loads(row['source_grades'] or '[]') or
                    [row['grade'] or '未标注年级'])
                targets=json.loads(row['target_curriculum'] or '[]')
                row_points=[p['knowledge_point'] for p in targets] if targets else json.loads(row['knowledge_points'] or '[]')
                grades.update(row_grades);points.update(filter(None,row_points))
                if grade and grade not in row_grades:continue
                if knowledge_point and knowledge_point not in row_points:continue
                if status!='all' and not (row['status'] in {'pending','generating'} if status=='active' else row['status']==status):continue
                if needle and needle not in (row['title']+' '+row['assignment_id']+' '+(row['stem'] or '')+' '+row['id']).casefold():continue
                matched.append(row)
            total=len(matched);pages=max(1,(total+page_size-1)//page_size);page=min(max(1,page),pages)
            result=[]
            for row in matched[(page-1)*page_size:page*page_size]:
                job=self.job(row['job_id'],db);item=next(i for i in job['items'] if i['id']==row['id'])
                job['items']=[item];job['status']=item['status'];job['progress']=item['progress']
                job['request']['questions']=[item['question']];job['callbacks']=[]
                result.append(job)
            return dict(items=result,pagination=dict(page=page,page_size=page_size,total=total,pages=pages),
                        stats=counts,filters=dict(grades=sorted(grades),knowledge_points=sorted(points)))

    def list_lessons(self, limit=100):
        # Legacy workspace query now preserves every independent courseware.
        return self.lesson_page(page_size=limit)['items']

    def courseware_job(self, courseware_id, db=None):
        if db is None:
            with self.connect() as connection:
                return self.courseware_job(courseware_id,connection)
        row=db.execute('SELECT job_id FROM items WHERE id=?',(courseware_id,)).fetchone()
        if not row:return None
        job=self.job(row['job_id'],db)
        # The public update operation owns a single lesson, never a batch.
        return job if len(job['items'])==1 and not job['request'].get('callback_url') else None

    def update_courseware(self, courseware_id, changes, idem_key=None):
        from .models import JobRequest
        from .teaching import validate_target_curriculum
        hash_=digest(dict(endpoint='update_courseware',courseware_id=courseware_id,changes=changes))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job=self.courseware_job(courseware_id,db)
            if not job:raise KeyError(courseware_id)
            if idem_key:
                replay=db.execute('SELECT * FROM lesson_requests WHERE idem_key=?',(idem_key,)).fetchone()
                if replay:
                    if replay['request_hash']!=hash_:raise Conflict('同一 Idempotency-Key 不能用于不同的请求内容')
                    return job['id'],True
            if job['status'] in {'pending','generating'}:
                raise Conflict('课件正在生成，请完成或失败后再提交更新')
            body=job['request'];question=body['questions'][0]
            if 'target_grade' in changes and changes['target_grade']!=question.get('target_grade') and 'target_curriculum' not in changes:
                question['target_curriculum']=[]
            for key in ('target_grade','target_semester','target_curriculum'):
                if key in changes:question[key]=changes[key]
            if 'title' in changes:body['title']=changes['title']
            body['options'].update(changes.get('options',{}))
            if 'generation_requirements' in changes:body['options']['generation_requirements']=changes['generation_requirements']
            body['options']['force']=True
            validated=JobRequest.model_validate(body)
            validate_target_curriculum(validated.questions[0])
            body=validated.model_dump();question=body['questions'][0]
            stamp=now()
            db.execute('UPDATE jobs SET title=?,request_json=?,request_hash=?,revision=revision+1,updated_at=? WHERE id=?',
                       (body['title'],dump(body),digest(body),stamp,job['id']))
            db.execute("""UPDATE items SET question_json=?,status='pending',stage='等待重新生成',progress=0,
                context_json=NULL,error_json=NULL,reused=0,generation=generation+1,updated_at=? WHERE id=?""",
                (dump(question),stamp,courseware_id))
            db.execute('INSERT INTO item_events(item_id,message,created_at) VALUES(?,?,?)',
                       (courseware_id,'已提交更新要求，重新生成课件',stamp))
            if idem_key:db.execute('INSERT INTO lesson_requests VALUES(?,?,?)',(idem_key,hash_,job['id']))
            return job['id'],False

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
            db.execute("UPDATE items SET status='generating',attempts=attempts+1,stage='准备生成资料',progress=5,updated_at=? WHERE id=?", (now(), row['id']))
            return dict(row)

    def progress(self, item_id, stage, value):
        with self.connect() as db:
            stamp=now()
            db.execute('UPDATE items SET stage=?,progress=?,updated_at=? WHERE id=?', (stage[:180], value, stamp, item_id))
            db.execute('INSERT INTO item_events(item_id,message,created_at) VALUES(?,?,?)',(item_id,stage[:2000],stamp))
            db.execute('DELETE FROM item_events WHERE item_id=? AND id NOT IN (SELECT id FROM item_events WHERE item_id=? ORDER BY id DESC LIMIT 100)',(item_id,item_id))

    def context(self, item_id, value):
        with self.connect() as db:
            db.execute('UPDATE items SET context_json=? WHERE id=?', (dump(value), item_id))

    def finish(self, item_id, *, package_id=None, error=None, reused=False):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO item_events(item_id,message,created_at) VALUES(?,?,?)',
                       (item_id,(error['code']+'：'+error['message'])[:2000] if error else '已完成',now()))
            db.execute('UPDATE items SET status=?,stage=?,progress=?,package_id=COALESCE(?,package_id),error_json=?,reused=?,updated_at=? WHERE id=?',
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

    def record_storage(self, package_id, storage):
        """Record successful publication without changing existing package files."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT manifest_json FROM packages WHERE id=?',(package_id,)).fetchone()
            if not row:
                raise KeyError(package_id)
            manifest=json.loads(row[0])
            manifest.update(storage=storage,object_prefix=storage['prefix'])
            db.execute('UPDATE packages SET manifest_json=? WHERE id=?',(dump(manifest),package_id))

    def resources(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM resources ORDER BY updated_at DESC,id')]

    def import_resources(self, resources, *, seed=False):
        with self.connect() as db:
            for r in resources:
                db.execute('INSERT INTO resources VALUES(?,?,?) ON CONFLICT(id) ' + ('DO NOTHING' if seed else 'DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at'),
                           (r['id'], dump(r), now()))
