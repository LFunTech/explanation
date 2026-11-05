"""Import the verified mathematics standard from complete page OCR caches.

Dry-run by default; --apply writes two idempotent local documents. The full book
is retained, while only explicitly scoped 内容要求 clauses can fill teaching
targets. No model-generated paraphrases or grade assignments are used here.
"""
import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

from app.config import Settings
from app.grades import GRADE_NAMES
from app.kb import KnowledgeBase, document_chunks


PDF_SHA256='01f4a577f991c4a5c63eaeeed22008c0e99172d0d8771b3636efcacea7e4d601'
PDF_PAGES=186
TITLE='义务教育数学课程标准（2022年版2025年修订）'
VERSION='2022年版2025年修订'
SOURCE='https://fxzx.qpedu.cn/wcm.files/upload/CMSfxzx/202603/202603180320047.pdf'
LANDING='https://fxzx.qpedu.cn/jhzx/kcjs/kcbz/511597.htm'
STAGES={
    '一':('第一学段（1～2年级）',1,2,'primary'),
    '二':('第二学段（3～4年级）',3,4,'primary'),
    '三':('第三学段（5～6年级）',5,6,'primary'),
    '四':('第四学段（7～9年级）',7,9,'junior'),
}
# Conservative lexical links to existing exam nodes. Each concept must occur
# both in the node name and in an actual clause. These are search aliases, not
# curriculum text. Prefer the longest concept present in each knowledge name.
CONCEPTS='''万以内 万以上 十进制 数位 数感 符号意识 数量关系 运算律 估算
整数 小数 分数 百分数 约分 通分 公倍数 公因数 质数 合数
加法 减法 乘法 除法 四则运算 混合运算 比例尺 正比例 反比例
有理数 负数 数轴 相反数 绝对值 无理数 实数 平方根 算术平方根
立方根 乘方 近似数 二次根式 代数式 指数幂 科学记数法 整式
合并同类项 去括号 平方差公式 完全平方公式 因式分解 提公因式 分式
等式 方程 一元一次方程 二元一次方程组 三元一次方程组 一元二次方程
配方法 判别式 根与系数 不等式 一元一次不等式 消元
函数 一次函数 二次函数 反比例函数 正比例函数 待定系数
三角形 等腰三角形 等边三角形 直角三角形 全等三角形 相似三角形
平行四边形 矩形 菱形 正方形 长方形 梯形 多边形 四边形
线段 直线 射线 中点 角平分线 垂直平分线 对顶角 余角 补角
垂线 平行线 同位角 内错角 同旁内角 内角和 中位线 勾股定理 重心
圆 圆心角 圆周角 垂径定理 切线 内心 外心 弧长 扇形 环形 正多边形
周长 面积 体积 表面积 圆柱 圆锥 长方体 正方体
定义 命题 定理 反证法 反例 轴对称 中心对称 旋转 平移 相似 位似
黄金分割 锐角三角函数 直角坐标系 坐标 方向 位置 投影 视图 展开图
平均数 加权平均数 中位数 众数 方差 四分位数 百分位数 箱线图
频数 频率 直方图 条形统计图 折线统计图 扇形统计图 统计图
抽样 样本 总体 概率 可能性 随机 分类 树状图 货币 时间单位'''.split()
ALIASES={'因式分解':('分解因式',),'矩形':('长方形',),'相似':('相似图形',),
         '锐角三角函数':('三角函数',),'根与系数':('韦达定理',)}


def grade_names(first,last):
    return [GRADE_NAMES[f'G{n}'] for n in range(first,last+1)]


def content_clauses(pages):
    """Join continuations across pages; stop at 学业要求/教学提示 boundaries."""
    result=[]
    stage=None
    domain=topic=subtopic=''
    active=False
    lines=[]
    page_numbers=[]

    def flush():
        nonlocal lines,page_numbers
        if lines and active and stage:
            text=''.join(lines).strip()
            if len(text)>3:
                label,first,last,phase=STAGES[stage]
                result.append(dict(content=text,metadata=dict(
                    page=page_numbers[0],page_end=page_numbers[-1],pages=list(dict.fromkeys(page_numbers)),
                    printed_page=page_numbers[0]-3,printed_page_end=page_numbers[-1]-3,
                    stage=label,grade_scope='stage',grades=grade_names(first,last),phase=phase,
                    section='课程内容 / '+domain,topic=topic,subtopic=subtopic,clause_kind='内容要求')))
        lines=[]
        page_numbers=[]

    for number in range(20,83):
        if number not in pages:continue
        for raw in pages[number].splitlines():
            line=raw.strip().lstrip('#').strip()
            if not line or re.fullmatch(r'\d{1,3}',line):continue
            compact=re.sub(r'\s+','',line)
            if compact in {'小学部分','初中部分'}:
                flush();active=False;stage=None;topic=subtopic=''
                continue
            heading=re.fullmatch(r'[（(][一二三四][）)](数与代数|图形与几何|统计与概率|综合与实践)',compact)
            if heading:
                flush();domain=heading[1];active=False;stage=None;topic=subtopic=''
                continue
            matched=re.fullmatch(r'第([一二三四])学段[（(][1-9][～~—－\-至][1-9]年级[）)]',compact)
            if matched:
                flush();stage=matched[1];active=False;topic=subtopic=''
                continue
            kind=re.fullmatch(r'【(内容要求|学业要求|教学提示)】',compact)
            if kind:
                flush();active=kind[1]=='内容要求';topic=subtopic=''
                continue
            if not active or not stage:continue
            # Printed footnotes remain in the full-text document. Keep optional
            # stars in clauses; never turn elective content into compulsory text.
            if re.match(r'^\[\d+\]',line):continue
            heading=re.fullmatch(r'\d+[.．、]\s*([^。；：:]{2,24})',line)
            if heading:
                flush();topic=heading[1];subtopic=''
                continue
            heading=re.fullmatch(r'[（(]\d+[）)]\s*([^。；：:]{1,16})',line)
            if heading and stage=='四':
                flush();subtopic=heading[1]
                continue
            if re.match(r'^(?:[（(]\d+[）)]|[①-⑳])',line):flush()
            lines.append(line)
            page_numbers.append(number)
    flush()
    return result


def link_clauses(clauses,nodes):
    indexed=[]
    for node in nodes:
        name=node['name']
        concepts=[t for t in CONCEPTS if t in name or any(a in name for a in ALIASES.get(t,()))]
        concepts=[t for t in concepts if not any(t!=other and t in other for other in concepts)]
        indexed.append((node,concepts))
    for clause in clauses:
        meta=clause['metadata'];text=clause['content']
        linked=[]
        for node,concepts in indexed:
            if node['phase']!=meta['phase']:continue
            code=node.get('grade_code')
            if code and GRADE_NAMES.get(code,code) not in meta['grades']:continue
            # All named concepts must be supported: a first-stage integer
            # addition clause must not link to fraction/polynomial addition.
            if node['name'] in text or (concepts and all(t in text for t in concepts)):
                linked.append(node)
        meta['knowledge_ids']=[n['id'] for n in linked]
        meta['labels']=list(dict.fromkeys(n['name'] for n in linked))
        meta['link_method']='explicit_concept_in_node_and_original_clause'
    return clauses


def load_pages(cache):
    pages={}
    for number in range(1,PDF_PAGES+1):
        path=cache/f'{number:04}.json'
        if not path.exists():raise ValueError(f'第 {number} 页 OCR 未完成，未导入')
        record=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(record.get('text'),str) or not isinstance(record.get('blank'),bool):
            raise ValueError(f'第 {number} 页 OCR 格式不完整')
        if not record['text'].strip() and not record['blank']:raise ValueError(f'第 {number} 页无有效文字')
        pages[number]=record['text']
    if '2025' not in pages[1] or '数学课程标准' not in re.sub(r'\s+','',pages[1]):
        raise ValueError('封面版本与导入清单不一致')
    return pages


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file',type=Path,default=Path('data/math-curriculum-2025.pdf'))
    parser.add_argument('--model',default='qwen3-vl-plus')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--embed',action='store_true')
    args=parser.parse_args()
    if hashlib.sha256(args.file.read_bytes()).hexdigest()!=PDF_SHA256:
        raise SystemExit('PDF 与已核对版本哈希不同，须先重新核对目录和页码')
    cfg=Settings();kb=KnowledgeBase(cfg)
    pages=load_pages(cfg.data_dir/'ocr'/PDF_SHA256/args.model)
    with kb.connect() as db:
        nodes=db.execute("SELECT id,name,phase,grade_code FROM kb_nodes WHERE subject='数学' ORDER BY id").fetchall()
    clauses=link_clauses(content_clauses(pages),nodes)
    if len(clauses)<150 or {c['metadata']['stage'] for c in clauses}!={s[0] for s in STAGES.values()}:
        raise SystemExit('章节提取不完整，未写入')
    if any(len(c['content'])>2000 or '[无法辨识]' in c['content'] for c in clauses):
        raise SystemExit('内容要求含超长或无法辨识条款，须核对后导入')
    full=[]
    for number,text in pages.items():
        if not text.strip():continue
        parts=document_chunks(text.encode(),'.txt')
        for chunk in parts:
            chunk['metadata'].update(page=number,printed_page=number-3 if number>=4 else None,
                grades=grade_names(1,6) if 20<=number<=55 else grade_names(7,9) if 56<=number<=82 else grade_names(1,9))
        full.extend(parts)
    meta=dict(subject='数学',phase='',grades=grade_names(1,9),region='全国',source=SOURCE,
        source_page=LANDING,source_publisher='中华人民共和国教育部',download_host='上海市青浦区凤溪中学',
        version=VERSION,curriculum_version=VERSION,pdf_sha256=PDF_SHA256,pdf_pages=PDF_PAGES,
        extraction='qwen_ocr',ocr_model=args.model,review_status='machine_extracted',
        grade_scope='compulsory_education',knowledge_ids=[],labels=[])
    report=dict(title=TITLE,pages=len(pages),full_text_chunks=len(full),content_clauses=len(clauses),
        linked_clauses=sum(bool(c['metadata']['knowledge_ids']) for c in clauses),
        linked_knowledge_points=len({k for c in clauses for k in c['metadata']['knowledge_ids']}),
        by_stage={s[0]:sum(c['metadata']['stage']==s[0] for c in clauses) for s in STAGES.values()},
        pdf_sha256=PDF_SHA256,source=SOURCE,applied=args.apply)
    output=cfg.data_dir/'imports'/'math-curriculum-2025'
    output.mkdir(parents=True,exist_ok=True)
    (output/'clauses.json').write_text(json.dumps(clauses,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.apply:
        kb.initialize()
        base='curriculum-math-'+PDF_SHA256[:20]
        kb.put_document(base+'-full',TITLE,'curriculum',{**meta,'document_role':'full_text'},full)
        kb.put_document(base+'-requirements',TITLE,'curriculum',{**meta,'document_role':'content_requirements'},clauses)
        report['document_ids']=[base+'-full',base+'-requirements']
    (output/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    if args.embed and args.apply:
        await kb.index_pending(limit=100000,progress=lambda n:print('Indexed',n,flush=True))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
