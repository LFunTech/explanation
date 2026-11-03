import json
from pathlib import Path
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from dotenv import dotenv_values

root = Path(__file__).resolve().parent.parent
cfg = dotenv_values(root / '.env')
with psycopg.connect(host=cfg['EXAM_DB_HOST'], port=cfg.get('EXAM_DB_PORT', 5432),
        dbname=cfg['EXAM_DB_NAME'], user=cfg['EXAM_DB_USER'], password=cfg['EXAM_DB_PASSWORD'],
        connect_timeout=10, options='-c default_transaction_read_only=on -c statement_timeout=30000',
        row_factory=dict_row) as conn:
    report = {}
    for schema in ['public', 'tenant_kg', 'tenant_kg_primary']:
        report[schema] = conn.execute(sql.SQL('SELECT subject_code, phase, grade_code, count(*) AS count FROM {}.knowledge_point WHERE effective_enabled GROUP BY 1,2,3 ORDER BY 1,2,3').format(sql.Identifier(schema))).fetchall()
        if schema != 'public':
            report[schema+'_grades'] = conn.execute(sql.SQL('SELECT grade_code,grade_name,phase FROM {}.grade WHERE enabled ORDER BY sort_order').format(sql.Identifier(schema))).fetchall()
            report[schema+'_subjects'] = conn.execute(sql.SQL('SELECT subject_code,subject_name FROM {}.subject WHERE enabled ORDER BY sort_order').format(sql.Identifier(schema))).fetchall()
    report['public_samples'] = conn.execute('SELECT subject_code,phase,grade_code,knowledge_point_name FROM public.knowledge_point WHERE effective_enabled ORDER BY subject_code,sort_order LIMIT 12').fetchall()
    report['resource_tables'] = conn.execute("SELECT table_schema,table_name FROM information_schema.tables WHERE table_schema IN ('public','tenant_kg','tenant_kg_primary') AND (table_name LIKE '%curriculum%' OR table_name LIKE '%chapter%' OR table_name LIKE '%vector%') ORDER BY 1,2").fetchall()
    report['question_tables'] = conn.execute("SELECT table_name,array_agg(column_name ORDER BY ordinal_position) AS columns FROM information_schema.columns WHERE table_schema='public' AND table_name LIKE 'question%' GROUP BY table_name ORDER BY table_name").fetchall()
    (root / 'data').mkdir(exist_ok=True)
    (root / 'data/exam-inventory.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['question_tables'], ensure_ascii=False, indent=2))
