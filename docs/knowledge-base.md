# 本地知识库与 exam 数据映射

## 存储

`VECTOR_DATABASE_URL` 指向独立本地 PostgreSQL。应用任务和回调使用 `data/app.sqlite3`，向量、知识文本和图谱使用 PostgreSQL；二者职责独立。切勿把 VECTOR_DATABASE_URL 配到远端 exam 业务库。

| 本地表 | 内容 |
|---|---|
| kb_nodes | 公共图谱节点，源 ID、源 schema、学科、学段、原始年级、父节点、版本 |
| kb_edges | 父子关系与源库语义关系，保留推断/显式标记、证据强弱和依据 |
| kb_documents | 文档与来源、版本、内容哈希 |
| kb_chunks | 文本片段、页码/字符范围、学科/区域/年级、知识点、真实向量和错误状态 |

向量为 `text-embedding-v4` 的 1024 维 float 数组。SQL 精确余弦检索前按学科、区域、年级/学段过滤。没有以关键词命中冒充语义向量；API 返回索引模型、真实相似度与匹配范围。当前版本未采用 pgvector ANN；数据规模上升时可增加扩展列和 HNSW 索引，但应保持模型版本、维度及权限边界。

## exam 的只读导入

- 节点来自 `public.knowledge_point`，只导入 `effective_enabled=true`。
- 本地 ID 为 `exam:public:{公共节点UUID}`，同时保留 `source_schema` 和 `source_knowledge_point_id`，避免租户同名或同 ID 碰撞。
- 父子结构来自 `parent_id`，语义关系来自 `public.kg_knowledge_relation`，保留 `assertion_level/evidence_strength/reason_text/model_version`。只纳入两端都可用的边。
- 年级字典只读查询 `tenant_kg.grade`，学科映射采用源 `GB-*` 编码。所有源连接设置只读与语句超时。
- 原题/变式来自 `public.question`，关联 `question_taxonomy_relation`、`question_grade` 和 `question_attachment`；只读取 ACTIVE 教学题目。
- 不导入租户学生数据、作业答卷、用户身份或租户私有题库。

公共图谱已经汇总了小学/初中来源，因此不重复把 `tenant_kg`、`tenant_kg_primary` 的同一节点再建一份图。同步更新本地已有源节点，清理已禁用/移除的源节点和边；不会写回 exam。

## 年级与教材版本

2026-11-03 的只读盘点：13,788 个可用公共知识点，具体 `grade_code` 全为空；图谱只支持按学段定位。不能把初中节点全部标成九年级。源字典六年级属于小学，而上海六年级通常属于预备年级；应用按区域确定检索学段，但不伪造二者之间的教材映射。

教材入库时由提供方明确学制、版本、学期、年级、章节和知识点。课标跨多个年级时，可保留 `grades=[]` 和适用 `phase`，结果标注匹配范围。图谱名称只是目录和检索词，不等于教材正文，也不被当成官方课标依据。

生成输入、知识检索和文档导入共同使用 `curriculum_version`（课标版本）、`school_system`（学制）、`publisher`（教材出版社）、`textbook_version`（教材版本）、`volume`（册次/模块）。这些字段不互相替代：例如同一 2022 课标可对应不同出版社、年级和修订版本的教材。

教材入库必须声明具体年级、学制、出版社、版本和册次；生成或检索时未指定完整教材范围，不把任意教材当作对应教材。已指定课标版本时排除其他版本，教材按完整范围精确匹配。未指定课标版本且检索结果有多个版本时，不混用这些课标，需选定版本后再检索。课标跨年级时仍按学段匹配，不伪造具体教材映射。界面可填写以上条件，命中记录及讲解包保留原始来源和版本。

已登记正文包括《义务教育物理课程标准（2022年版）》和《义务教育数学课程标准（2022年版2025年修订）》。数学覆盖义务教育一至九年级；高中数学及完整教材尚未导入。13,788 个 exam 图谱节点只是知识目录，**不代表全部学科、年级或教材均有正文**。扫描文字保留机器提取标记，不能视为整本已人工校核。

数学 PDF 来自[上海市青浦区凤溪中学公开下载页](https://fxzx.qpedu.cn/jhzx/kcjs/kcbz/511597.htm)，已核对封面版本和目录，共 186 个 PDF 物理页。文件 SHA-256：`01f4a577f991c4a5c63eaeeed22008c0e99172d0d8771b3636efcacea7e4d601`。库内保存完整正文与单独的“内容要求”条款；页码、印刷页码、原始来源、版本、学段范围、OCR 方法和知识点关联方式随片段保存。

2026-11-05 实际导入：190 个完整正文片段、226 条内容要求，共 416 个片段，均已完成向量索引。212 条内容要求通过显式概念规则关联到 1,629 个现有知识点；关联是本地检索映射，不是教育部发布的题库知识点对照表。平均数原文分别位于印刷第 38 页（第二学段）及第 73—74 页（第四学段），已对照扫描页核验。

数学课标本身按第一至第四学段规定要求。原始适用范围保留为 1—2、3—4、5—6、7—9 年级，不能宣称是某一单独年级或上/下学期的教材进度。目标课标接口按当前选择的一个年级匹配其学段原文，每条结果仍为一个 `grade`，同时提示学段依据；不会改写 exam 图谱的空年级，也不会为多个年级复制自拟要求。名称关联只使用课标原句和知识点名称中实际共有的概念，保留关联规则；未关联到原文的条目不由模型补写。

## 文档入库

在工作台导入 PDF、DOCX、TXT 或 Markdown；记录标题、学科、区域、版本、学段/年级与来源。文本先按页提取，再在段落或句末切分，目标 900 字符、重叠 120 字符，保留页码与字符偏移。空白/纯扫描 PDF 不假装完成，须先 OCR；PDF 图像、复杂表格和公式转换仍需人工核对。

稳定文档 ID 由文件内容与元数据决定。相同内容重复导入不新增重复记录；变更产生新版本。每个片段持有内容哈希，改变文本时仅使该片段向量失效。每批 10 条调用千问，最多 4 批并发，成功批次落盘；重新执行跳过已完成且模型/维度一致的向量。

扫描版课标可使用以下命令逐页识别。使用千问视觉模型，会产生调用费用；页级缓存支持失败后继续。整本成功后才入库，保留原始 PDF 哈希、物理页码、来源 URL 与 `review_status=machine_extracted`，OCR 文字仍需核对公式和表格。原始 PDF 与识别缓存只存本地 `data/`。

```powershell
.\.venv\Scripts\python.exe -m scripts.import_scanned_pdf data/physics-curriculum-2022.pdf --title "义务教育物理课程标准（2022年版）" --subject 物理 --phase junior --source "官方 PDF 地址" --embed
```

CLI 和工作台的向量化使用 PostgreSQL advisory lock 防止重复运行；更改模型或维度后，检索只使用与当前配置一致的向量。

数学入库分两步，第一步完整识别，第二步验证固定版本、按学段切分条款并关联现有知识点。第二步默认只输出检查清单，加 `--apply` 才写库，加 `--embed` 才向量化；重跑使用相同文档 ID，不重复新增。

```powershell
.\.venv\Scripts\python.exe -m scripts.import_scanned_pdf data/math-curriculum-2025.pdf --title "义务教育数学课程标准（2022年版2025年修订）" --subject 数学 --phase junior --version "2022年版2025年修订" --source "https://fxzx.qpedu.cn/wcm.files/upload/CMSfxzx/202603/202603180320047.pdf" --extract-only
.\.venv\Scripts\python.exe -m scripts.import_math_curriculum
.\.venv\Scripts\python.exe -m scripts.import_math_curriculum --apply --embed
```

`--extract-only` 不入库，示例中的 `--phase` 不用于第二步的范围判断。完整缓存保存于 `data/ocr/<PDF哈希>/<模型>/`，检查清单和逐条关联结果保存于 `data/imports/math-curriculum-2025/`。扫描页面缺失、封面版本不同、条款含无法辨识标记或章节提取不完整时，不写入数据库。

本地库用于知识浏览、课标原文选择与“LLM 分析获取”。模型只选择库内条目索引，最终课标文字由服务端复制原文。对外 `POST /api/courseware` 创建使用调用方传入的目标年级、学期和目标课标，生成阶段不再隐式检索或混入本地课标。配置的外部知识服务和 exam 变式检索仍独立工作。

## 扩展检索适配器

`KNOWLEDGE_API_URL` 接受固定服务端地址。POST `{question,limit}`，返回 `{resources:[Resource]}`，Resource 结构见 `/docs`。返回数据再次校验学科、年级和区域；不接受请求方指定任意检索 URL。后续可接向量检索重排或学校资源服务。
