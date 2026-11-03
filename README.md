# 题解工坊

给一道题，生成知识点讲解、详细解题、易错提醒、总结和变式题，交付可独立打开的 HTML、PPTX、逐页讲稿和语音。默认上海，可指定学科、年级、知识点、题型和难度。

## 启动

需要 Python 3.12。前端是原生 ES modules，数学渲染资源已随仓库保存，无需 Node.js 构建，也不依赖 CDN。

```powershell
cd C:\Users\TMACY\lfun-github\explanation
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env  # 仅首次执行，不要覆盖已有配置
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18765
```

Linux/macOS 对应使用 `.venv/bin/python`。打开 <http://127.0.0.1:18765>，接口文档为 <http://127.0.0.1:18765/docs>。

本机已配置的 `.env`、数据库、原始课标、生成课件和运行日志不提交 Git。`start.ps1` 可启动已安装依赖的 Windows 工作环境。运行时只允许一个进程使用同一数据目录，不要使用 `--workers` 或多副本共享目录。

## 已实现

- 题目工作台：手动输入、选项、小题结构、参考答案/解析、图片上传、从 exam 公共题库选题。
- 千问生成：结构化 PPT 原稿 → 独立五项复核 → 最多三轮定向反馈重写 → 再复核 → 分页与打包。上屏内容 `display` 与补充讲稿 `notes` 分开保存。参考项目的直接课件流程被抽取为独立服务，不依赖宿主登录。
- HTML：公式、分页、目录、逐页讲稿、语音、连续播放、全屏、打印。字体、图片和已合成语音内嵌，下载后可离线打开。
- PPTX：16:9、可编辑文字、Office Math 常用公式、逐页演讲者备注。复杂 LaTeX 的兼容性需在目标 PowerPoint/WPS 中复核；不承诺两种办公软件的渲染完全一致。
- 异步作业接口：逐题状态、部分成功、幂等请求、内容复用、不可变结果版本、失败题重试、检查点恢复。
- 回调通知：白名单、HMAC、持久化通知、指数退避、手动补发。回调失败不撤销课件。
- 存储：本地持久化，或上传到 S3/MinIO；全部文件上传成功后发布完成状态。
- 知识资源：本地 PostgreSQL 图谱与向量库、exam 只读导入、PDF/DOCX/TXT/MD 切分、千问 `text-embedding-v4` 1024 维向量、范围过滤和可追溯检索。
- 理科样例：数学因式分解、物理欧姆定律、化学质量守恒。样例模式只接受完全相同的原题和教学条件，不把样例结果用于任意新题。

## 千问与语音

```dotenv
DASHSCOPE_API_KEY=填写自己的密钥
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
QWEN_VISION_MODEL=qwen3-vl-plus
TTS_MODE=qwen
```

`TTS_MODE=qwen` 合成逐页 WAV，长稿按供应商长度限制切分后无损拼接，音频存入讲解包，不依赖会过期的供应商 URL。`browser` 使用设备语音，没有可下载的音频文件，设备没有中文语音时会明确提示。`none` 关闭语音。内置样例使用设备朗读，不产生模型费用。

调用方式依据 [千问 Chat Completions](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions)、[千问 TTS](https://help.aliyun.com/en/model-studio/qwen-tts-api) 和 [文本向量模型](https://help.aliyun.com/en/model-studio/text-embedding-v4)。密钥只由后端使用，不返回前端。未配置或真实生成失败时，不静默替换成样例。

## 知识库与题库

详见 [知识库说明](docs/knowledge-base.md)、[资源来源清单](docs/resources.md)。

远端 `exam` 连接一律以 `default_transaction_read_only=on` 建立，只读取公共教学资源及字典，不访问学生答卷或更改业务表。本地知识库使用独立 PostgreSQL 数据库。当前实现用 `REAL[]` 保存真实向量，以 SQL 计算精确余弦相似度，建立学科、区域、年级和知识点索引；不要求安装扩展。`compose.yaml` 提供可选的 pgvector PostgreSQL 实例，但当前检索仍为精确检索，没有启用 ANN/HNSW。

```powershell
# 同步知识图谱，保留源节点 ID、父子关系和语义关系
.\.venv\Scripts\python.exe -m scripts.sync_knowledge
# 可断点继续；已完成且模型/维度未变化的向量不会重复生成
.\.venv\Scripts\python.exe -m scripts.index_knowledge
```

也可在“知识资源”页面操作。索引任务通过数据库锁防止 CLI 与页面重复启动。首次导入只是建立图谱/文本记录，只有完成向量化的内容参与语义检索。扫描课标使用 `scripts.import_scanned_pdf` 逐页 OCR，命令和识别边界见知识库说明。

原题和变式题来自 `exam.public.question`。知识点、答案、解析、源 ID 一并保留。题库含图但图片未补齐时阻止生成；图片可通过工作台上传，或直接传入 base64 data URL。当前未接入 exam 文件服务的私有附件签名，含图题不会作为变式候选。

上海五四学制与源库六三年级字典不自动等同；现有公共图谱的年级为空时按学段索引，原始字典保留，具体教材/年级应通过已确认的版本或导入元数据补充。高中缺少源图谱时可导入高中教材与课标，或使用千问生成，界面不伪造已存在的高中资源。

## 作业系统接入

完整契约见 [API 对接说明](docs/integration.md)。作业系统发布作业后调用 `POST /api/jobs`，保存 `job_id`，查询或接收回调，再用 `question_id` 关联 `html_url`。

```json
{
  "assignment_id": "homework-20261103-001",
  "title": "九年级数学练习",
  "questions": [{
    "question_id": "q-001",
    "stem": "解方程：$x^2-5x+6=0$。",
    "subject": "数学",
    "grade": "九年级",
    "region": "上海",
    "knowledge_points": ["一元二次方程", "因式分解"],
    "question_type": "解答题",
    "difficulty": "中等"
  }],
  "options": {"mode": "qwen", "depth": "standard", "variant_count": 2, "voice": true, "force": false}
}
```

本地服务默认只监听 `127.0.0.1`。部署到局域网或公网时设置 `API_KEY`、反向代理 HTTPS 和 `PUBLIC_BASE_URL`；应用是面向一个受信作业系统的服务实例，没有实现学校/租户级身份与数据隔离。浏览器工作台使用 HttpOnly 会话；作业调用使用 `X-API-Key`。结果链接可带 7 天签名，过期后重新查询任务取得新链接。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

测试覆盖幂等冲突、跨作业查询、成功内容复用、新版本保留、部分失败重试、回调事件、公式分页、上海年级边界、样例范围及题库变式来源。`scripts.check_live` 和 `scripts.verify_workflow` 是显式调用真实千问及本机服务的联调入口，会产生模型调用费用；普通测试不调用真实模型。

作业发布触发与回调接收仍需作业系统按契约接线。S3/MinIO、生产回调和外部作业嵌入效果需在双方实际环境验证。

2026-11-03 本机联调结果：9 项自动化测试通过；3 道理科样例及 1 道真实千问题完成 HTML/PPTX/ZIP 下载检查。真实课件为 19 页，包含 19 个语音文件、约 520 秒音频和 2 道 exam 变式，浏览器已验证播放、停止、翻页及公式显示；工作台验证了题库选题与 390px 移动端布局。已导入 13,788 个图谱节点、19,313 条关系和官方物理课标 71 页 OCR，共 13,859 条真实向量，索引错误数为 0。课标检索“欧姆定律”命中物理课标物理页码 31。上述运行数据与课件保留在本机，不随源码上传。
