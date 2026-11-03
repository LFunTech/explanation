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

当前实际正文只有《义务教育物理课程标准（2022年版）》的 71 个 OCR 片段；13,788 个 exam 图谱节点只是知识目录，**不是已经导入了上海全部学科、年级或教材**。完整教材及其章节映射仍待补齐。原始 OCR 保留机器提取标记，不能视为已人工校核正文。

## 文档入库

在工作台导入 PDF、DOCX、TXT 或 Markdown；记录标题、学科、区域、版本、学段/年级与来源。文本先按页提取，再在段落或句末切分，目标 900 字符、重叠 120 字符，保留页码与字符偏移。空白/纯扫描 PDF 不假装完成，须先 OCR；PDF 图像、复杂表格和公式转换仍需人工核对。

稳定文档 ID 由文件内容与元数据决定。相同内容重复导入不新增重复记录；变更产生新版本。每个片段持有内容哈希，改变文本时仅使该片段向量失效。每批 10 条调用千问，最多 4 批并发，成功批次落盘；重新执行跳过已完成且模型/维度一致的向量。

扫描版课标可使用以下命令逐页识别。使用千问视觉模型，会产生调用费用；页级缓存支持失败后继续。整本成功后才入库，保留原始 PDF 哈希、物理页码、来源 URL 与 `review_status=machine_extracted`，OCR 文字仍需核对公式和表格。原始 PDF 与识别缓存只存本地 `data/`。

```powershell
.\.venv\Scripts\python.exe -m scripts.import_scanned_pdf data/physics-curriculum-2022.pdf --title "义务教育物理课程标准（2022年版）" --subject 物理 --phase junior --source "官方 PDF 地址" --embed
```

CLI 和工作台的向量化使用 PostgreSQL advisory lock 防止重复运行；更改模型或维度后，检索只使用与当前配置一致的向量。

检索顺序：同学科、对应区域和年级的登记资源/文档 → exam 相关变式题 → 千问正确学科知识。未有对应资源时允许生成，但包内不编造来源或把自拟内容标成教材。已配置的检索服务不可用时报告失败，不用失败掩盖成“没有资源”。

## 扩展检索适配器

`KNOWLEDGE_API_URL` 接受固定服务端地址。POST `{question,limit}`，返回 `{resources:[Resource]}`，Resource 结构见 `/docs`。返回数据再次校验学科、年级和区域；不接受请求方指定任意检索 URL。后续可接向量检索重排或学校资源服务。
