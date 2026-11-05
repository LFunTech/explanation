# 课件生成与播放对接文档

更新日期：2026-11-05。本文供业务后端和前端使用，按当前代码整理。完整请求模型也可在服务的 `/docs` 和 `/openapi.json` 查看。

## 1. 推荐方案：直接打开包内播放器

**课件包提供 `player.html`，打开即可播放，无需接入 JS SDK。** 文件自带目录、翻页、讲稿、完整解析、朗读、连续播放和打印。图文、数学字体及已合成音频均内嵌，可以从 MinIO 打开，也可以下载后单独打开。

| 场景 | 使用入口 |
|---|---|
| 播放上传到 MinIO 的完整课件 | `result.minio_package_player_url`，服务校验权限后跳转到对象文件 |
| 播放服务目录中的完整课件 | `result.package_player_url`，对应包内 `player.html` |
| 下载后播放 | 解压 `package.zip`，打开 `player.html` |
| 快速在线预览、音频按页读取 | `result.player_url` 或 `result.minio_player_url`，需要生成服务提供外壳 |
| 业务系统完全自定义控件 | `result.embed_config_url` + JS SDK，见第 6 节 |
| 只显示课件内容 | `result.html_url` 或 `result.content_url`，不含目录和播放条 |

播放器默认开启动画，内部画布保持 16:9，并随容器等比例缩放；不需要全屏按钮。显示正文和朗读讲稿分开保存。完整播放器内嵌语音，文件可能较大，首次打开需下载该文件；需要更快首屏时使用轻量在线预览入口。

正文在固定的 `80rem × 45rem` 画布中使用 `rem` 字号，内容少时放大，内容多时分页；正文和公式均不使用滚动条。容器尺寸变化或目录开合仅缩放整张画布，不改变文字换行、图文布局和分页。导航使用 `state.totalPages` 和 `outline`，以包含原始页因内容过多而拆出的续页。

## 2. 服务地址与鉴权

本地示例基地址为 `http://127.0.0.1:18765`。部署后替换为真实 HTTPS 地址，并配置 `PUBLIC_BASE_URL`，确保返回的地址能被业务浏览器访问。

| 调用方式 | 鉴权要求 |
|---|---|
| 业务后端调用 `/api/*` | 配置 `API_KEY` 后发送 `X-API-Key: <服务访问密钥>` |
| 本服务工作台 | `POST /api/session` 登录后使用 HttpOnly Cookie |
| 浏览器打开课件、播放器和配置接口 | 使用返回的完整签名 URL，或本服务已登录会话 |
| 私有 MinIO 对象 | 使用服务端签发的对象 URL，无需浏览器持有 MinIO 密钥 |

未配置 `API_KEY` 时允许匿名访问。服务课件签名默认有效期 7 天，MinIO 对象签名有效期 1 小时。过期后重新查询任务或重新打开服务播放入口取得新链接，不把临时签名 URL 当作永久业务主键。

跨域接入时，由业务后端调用生成服务并把结果返回给自己的前端。本服务没有开放通用 API CORS；不要把服务 API Key 或 MinIO secret key 放入浏览器。当前实例没有学校/租户级数据隔离，业务权限由接入方控制。

## 3. 后端生成流程

1. 单题创建后保存顶层 `courseware_id`，这是固定课件 ID。`job_id`（兼容字段 `id`）为任务 ID。
2. 轮询 `GET /api/courseware/{courseware_id}`；批量模式仍查询 `/api/jobs/{id}` 或接收回调。
3. 用 `items[].question_id` 关联业务题目，用 `items[].status` 判断是否完成。
4. 成功后保存 `package_id`，打开结果中的播放地址。

### 3.1 接口选择

| 接口 | 说明 |
|---|---|
| `POST /api/courseware` | 推荐对外单题创建：传题目 ID、目标年级、可选学期和目标课标，后端读取公共题库原题与附件；返回任务 |
| `POST /api/jobs?single_question=true` | 工作台单题模式，一次一题，不接受 `callback_url`；每次创建独立课件，同题同目标允许多份；更新须指定课件 ID |
| `POST /api/jobs` | 批量模式，一次 1—50 题，批次内题目 ID 唯一；支持回调 |
| `GET /api/jobs?unique_questions=true` | 按题目 ID、目标年级、学期和目标课标返回各自当前课件记录 |
| `GET /api/courseware/{courseware_id}` | 用固定课件 ID 查询状态、进度、当前步骤和播放结果 |
| `PATCH /api/courseware/{courseware_id}` | 更新要求并重新生成，固定 ID 不变，revision 递增 |
| `GET /api/jobs/{id}` | 查询任务和各题状态 |
| `GET /api/assignments/{assignment_id}` | 查询作业生成批次，没有历史时状态为 `not_generated` |
| `POST /api/jobs/{id}/retry` | 仅重新排队失败题目，成功项不重新生成 |

服务默认同时生成 20 道题，由 `WORKER_CONCURRENCY` 控制，范围 1—20。单题网络重试不阻塞其他 worker。HTTP 202 只代表已持久化受理，关闭浏览器不会取消任务。

### 3.2 按题目 ID 创建（推荐）

发送 `Content-Type: application/json`，启用鉴权时带 `X-API-Key`。每次独立提交使用新的 `Idempotency-Key`；网络重试沿用同一个键。

```http
POST /api/courseware
Idempotency-Key: courseware-question-001-attempt-1
```

```json
{
  "question_id": "756ac6cd-befa-4a40-9972-f0013f6f7444",
  "target_grade": "七年级",
  "target_semester": "上半学期",
  "target_curriculum": [
    {
      "knowledge_point": "一元一次方程",
      "grade": "七年级",
      "curriculum_requirements": ["理解方程解的含义", "用等式性质说明每一步变形"]
    }
  ]
}
```

示例 UUID 需替换为实际公共题库 ID。`question_id` 支持 UUID 或 `exam:public:UUID`，后端统一使用后者；当前只支持 exam 公共题库，不查询租户私有题目。服务按 ID 获取题干、选项、小题、学科、原题年级、知识点、答案、解析和原图，无需调用方重复传递原题。

`target_grade` 必填。`target_semester` 可省略，默认 `全学期`，仅接受 `全学期`、`上半学期`、`下半学期`。`target_curriculum` 每项为一个知识点、一个对应年级和多条课标要求；条目年级必须与 `target_grade` 一致，混入其他年级返回 422；省略或为空时回退原题知识点，不自动查询本地课标。学期随教学目标进入模型、任务与 manifest，同题不同学期分别保存和生成。

可选 `options` 与下文生成设置一致，默认千问生成、标准深度、2 道变式、开启语音；可选 `assignment_id` 和 `title`，不传由服务补充。此单题接口不接收回调配置。创建页保持本地测试用途，选入公共题库原题后调用本接口；手工输入或修改原题内容时使用下方完整题目接口。

返回 HTTP 202，保留任务响应的兼容字段，并增加固定 `courseware_id`、`job_id`、`status_url`、`stage`、`error`、`generation_requirements`、`result` 和 `previous_result`。保存 `courseware_id` 后轮询 `GET /api/courseware/{courseware_id}`。幂等重试不重新读取题库；同一幂等键改动请求返回 409。每次使用新幂等键（或不传幂等键）创建都会返回新的课件 ID；同题、同年级、同学期、同课标也可同时创建多份，分别调用模型生成。非法 ID、年级或学期返回 422；题目不存在、停用或内容不可用返回 404；必要图片缺失返回 422，题库或存储连接异常返回 503，不创建缺图任务。

### 用课件 ID 查询和更新

```http
GET /api/courseware/{courseware_id}
PATCH /api/courseware/{courseware_id}
Idempotency-Key: courseware-update-002
Content-Type: application/json
```

PATCH 请求示例：

```json
{
  "generation_requirements": "变式题配对应图形，答案使用同页弹窗；详细解释每一步依据。",
  "options": {"depth": "deep"}
}
```

创建也支持顶层 `generation_requirements`（可选，最多 8000 字符）。提示词实际进入模型输入并参与缓存标识，仍须服从题目事实与目标课标。更新不复用旧内容检查点，每次生成新的课件包。

更新可传 `title`、`target_grade`、`target_semester`、`target_curriculum`、`options`、`generation_requirements`；未传字段保留，options 按字段合并，`{}` 表示原配置重新生成。新提示词替换旧提示词，空字符串清除提示词。更换目标年级且不传目标课标时清空旧年级课标。原题保持不变，换题使用创建接口。

响应的 `courseware_id` 保持不变，`revision` 加一，`status=pending`，`progress=0`。仅被更新的课件正在生成时返回 409；不存在返回 404。同题其他课件及其教学目标不构成冲突。更新使用新的幂等键，网络重试沿用该键，不会重复排队。

GET 返回 `status`（pending / generating / completed / failed）、`progress`（0—100）、`stage`（当前步骤）、`revision` 和 `error`。顶层 `result` 仅在本次生成成功时提供，更新期间旧版放在 `previous_result`；兼容字段 `items[].result` 可能保留旧版，请不要用它判断新版本完成。

每道变式只保留一个题目页，点击“查看答案”在播放器视口打开答案与解析，目录不另列解答页。PPTX 的答案与解析保存在该页备注中。

### 3.2.1 完整题目输入（本地测试与批量兼容）

发送 `Content-Type: application/json`，启用鉴权时带 `X-API-Key`。建议每次独立提交携带 `Idempotency-Key`。

```http
POST /api/jobs?single_question=true
Idempotency-Key: lesson-q-001-attempt-1
```

```json
{
  "assignment_id": "lesson-q-001",
  "title": "一元一次方程 · 题目讲解",
  "questions": [{
    "question_id": "business:q-001",
    "stem": "解方程：$2x+3=11$。",
    "subject": "数学",
    "grades": ["七年级", "八年级"],
    "region": "上海",
    "knowledge_points": ["一元一次方程"],
    "target_grade": "七年级",
    "target_curriculum": [
      {"knowledge_point": "一元一次方程", "grade": "七年级", "grade_source": "manual", "curriculum_requirements": ["理解方程解的含义", "用等式性质求解，不使用函数方法"]},
      {"knowledge_point": "等式性质", "grade": "七年级", "grade_source": "manual", "curriculum_requirements": ["说明每一步变形的依据"]}
    ],
    "question_type": "解答题",
    "difficulty": "基础",
    "options": [],
    "images": [],
    "requires_images": false,
    "answer": "$x=4$",
    "analysis": "等式两边减去3，再除以2。"
  }],
  "options": {
    "mode": "qwen",
    "depth": "standard",
    "variant_count": 2,
    "voice": true,
    "force": false
  }
}
```

| 字段 | 约定 |
|---|---|
| `assignment_id` / `question_id` | 必填、稳定，使用业务标识，不根据题干猜 ID |
| `stem` / `options` | 完整题干和选项字符串数组，数学公式可用 LaTeX |
| `sub_questions` | `{id,stem,answer,analysis}` 数组，小题 ID 唯一 |
| `images` | 原图 data URL 数组，顺序稳定；最多 8 张，单图最大 8 MB |
| `requires_images` | 题目必须带图时为 `true`，缺图时不能直接生成 |
| `grades` | 原题年级数组，支持多选筛选；兼容旧字段 `grade`，不决定生成的教学年级 |
| `source_grades` | 公共题库原始年级，保留作来源信息 |
| `target_grade` | 提交生成时必填，独立指定一年级至高三中的一个目标年级；不从原题年级推断 |
| `target_semester` | 选填：`全学期`（默认）、`上半学期`、`下半学期`；不同学期独立保存和生成 |
| `target_curriculum` | 目标课标 JSON 数组，每项为 `{knowledge_point,curriculum_requirements,grade?,grade_source?}`；要求和适用年级与该项知识点关联 |
| `subject` / `region` | 学科和教学区域；区域默认上海，不显示在课件页脚 |
| `answer` / `analysis` | 可选参考答案和解析；当前默认关闭生成后的独立内容复核 |
| `knowledge_points` / `knowledge_point_ids` | 原题知识点与公共 ID；未提供目标课标时作为知识点回退来源 |
| `source` | 公共题库使用 `exam/public/question/{UUID}` |
| `curriculum_version` / `school_system` / `publisher` / `textbook_version` / `volume` | 可选课标版本、学制、出版社、教材版本及册次；未知时留空 |
| `options.mode` | `qwen`：模型生成；`sample`：必须与内置样例及范围完全匹配 |
| `options.depth` | `brief`、`standard`、`deep` |
| `options.variant_count` | 非负整数，默认 2，表示变式题数量 |
| `options.voice` | 请求语音；最终实际语音类型以结果 `voice_mode` 为准 |
| `options.force` | 批量模式跳过内容复用；单题创建与按 ID 更新均自动设为 true，确保重新调用模型 |

传输模型仍有字段类型、题干长度、图片大小等约束；生成出的页数和台词字数不再作为硬性预算。内容多时调整字号或分页，不设置“简单题最多 5 页”等限制。

生成以 `target_grade`、`target_semester` 和 `target_curriculum` 为教学依据。`curriculum_requirements` 是文字数组，可填写多条范围、深度或解题方法要求，必须放在所属知识点对象中。`target_curriculum` 省略或为 `[]` 时仅回退原题知识点，目标年级仍须明确填写。用户填写的课标要求不自动成为已核实的官方出处。当前不设置学年字段。

`POST /api/jobs` 和单题创建均不再自动查询本地资源表或向量库，不向生成模型追加本地课标片段。本地课标查询保留在下述知识点列表和分析接口中，调用方可先获取、编辑，再把确认后的要求放入 `target_curriculum`；也可直接提交自己的要求。未填写目标课标时同样不会自动查询本地库。旧任务重试时会更新资源快照，移除旧版本自动检索的本地片段；已完成的历史课件保留。

显式配置的外部知识资源接口、题库变式查找、插图与语言深度、HTML/PPT 页脚均使用目标年级。原题信息保存在 `manifest.question`；本次生效目标保存在 `manifest.teaching_target`，任务结果也提供 `result.teaching_target`。修改目标课标或目标年级会隔离内容复用和生成检查点；同题不同教学目标分别保留记录，可同时生成。历史课件可继续打开，重新生成前需补充目标年级。

### 3.2.2 多选课标与 LLM 分析

创建页提供可搜索的知识点多选框，知识点可以多选，但每条课标只对应一个年级。每项使用单个 `grade` 字符串，例如 `"grade":"七年级"`。每条 grade 必须与当前 target_grade 一致，不能在一份课件中混入其他年级的课标。切换目标年级会清空原要求，须重新匹配该年级；不同年级分别创建课件。

右上角“LLM 分析获取”根据当前题目（含选项、小题、原图）、目标年级、学期和原题知识点匹配库内课标。模型只返回候选条目索引，知识点、年级与要求由后端从库内原文取回，模型不能编写或改写要求。只有具备明确年级且已关联课标正文的条目可以自动填充；库未配置、读取失败或无匹配时返回空数组和原因，不改用模型建议。人工修改的要求保留；重新分析会移除旧版 `grade_source=llm` 建议。`grade_source` 为 `knowledge_base`（本地库）、`manual`（手动）或 `unknown`（待确定），`llm` 仅兼容历史数据。标签及要求随 `target_curriculum` 存入任务、课件清单和生成输入；`grade` 不替代本次 `target_grade`，不回写原题库全局节点。

重选题目会清空上一题的目标课标、知识点勾选与分析结果，并取消未完成的分析；目标年级保留。“调整教学目标”会恢复该课件自己的目标。右侧“换一批”优先按目标年级选题，切换目标年级自动刷新；下方多选题库年级用于手动搜索。课件列表支持年级、知识点、状态和关键词组合筛选，年级优先取目标年级，知识点优先取目标课标；历史课件回退原题标签。

旧数据中的 `grades` 只有一个年级时自动转成 `grade`；有多个不同年级时保留要求作为待整理内容，年级置空，需选定一个年级后重填或重新分析，不拆成几份相同要求。下拉框与 LLM 分析仅返回当前目标年级条目；有明确学段原文时按当前目标年级匹配，并展示原始学段与页码。目标课标已去掉 `knowledge_point_id`，旧 ID 和其他附加字段会自动忽略；原题 `knowledge_point_ids` 仍作为来源信息。

列表、分析和生成接口统一使用单个 `grade` 字段，不再根据客户端版本返回 `grades` 数组。兼容读取旧输入时，仅有一个年级的 `grades` 会转为 `grade`；多个年级不复制课标。重新分析不依赖旧课标能否解析。

- `GET /api/teaching-targets?subject=数学&target_grade=七年级&query=方程`：返回 `{items,notice}`。`items` 与 `target_curriculum` 使用相同结构。知识点按目标年级/学段筛选，要求只从明确关联的课标片段获取；没有关联时返回空数组。知识库暂时不可用时返回空列表和提示，仍可分析或自定义。
- `POST /api/teaching-targets/analyze`：请求体为单个 `Question` 对象（与生成请求 `questions` 内的对象相同），`stem` 和 `target_grade` 必填。响应为 `{target_curriculum,note,source:"knowledge_base"}`。不创建课件任务，不上传文件。当前已选课标不作为分析依据，题库年级不覆盖目标年级。

分析期间的连接中断自动重试；鉴权或模型配置错误直接返回错误。客户端中止请求后，服务端取消模型调用及重试。页面切换题目、修改目标年级或题目内容会取消旧分析，防止旧结果填回新题。外部系统可以用 `AbortController` 取消请求。将返回的 `target_curriculum` 放入生成请求即可，生成接口结构不变。

### 原生微应用接入（无需 iframe）

创建完成结果新增 `result.microapp_data_url`，指向受签名保护的 `/microapp/{package_id}/data`。通过 ES module 直接挂载到 DOM 容器，样式与元素 ID 使用 Shadow DOM 隔离，复用原播放器的目录、公式、配图、语音、动画、讲稿、完整解析及答案弹窗。不修改宿主路由、标题或全局播放器变量。

```html
<div id="courseware" style="height:80vh"></div>
<script type="module">
  import { mount } from '/static/courseware-microapp.js';
  // result 由业务后端查询完成课件得到，不在浏览器中放 API Key。
  const app = await mount({
    container: document.querySelector('#courseware'),
    dataUrl: result.microapp_data_url,
    onEvent: ({type, payload}) => console.log(type, payload),
    motion: true
  });
</script>
```

上例只负责挂载。保存返回的 `app` 实例，在宿主切换课件、点击控件或组件销毁时分别调用以下方法，勿在挂载后立即卸载：

```js
// 切换课件时停止旧语音、取消旧请求，从新课件首页开始。
await app.update({dataUrl: nextResult.microapp_data_url});
app.next();
app.play();
// 仅在宿主组件销毁时执行：
app.unmount();
```

相对数据 URL 按生成服务地址补全（例如 `new URL(result.microapp_data_url, serviceOrigin).href`），保留签名查询参数。工作台“对外接口文档 → 播放器接入”集中提供微应用与 iframe 的入口、示例及结果字段说明。

实例提供 `next / previous / goTo(index) / play / stop / getState / setAutoPlay / setMotion`；goTo 索引从 0 开始，play 可暂停/继续。事件为 `loading / mounted / ready / state / error / unmounted`，其中 state 保留原播放器的 `page/totalPages/playing/paused/outline`。同一容器重新 mount 会卸载旧实例，不同容器可并存；unmount 清理语音、监听器、ResizeObserver、动画和定时器，可重复调用。

模块还导出 `bootstrap()`、`mount(props)`、`update({container,...props})`、`unmount({container})`，供宿主微前端适配层调用。Vue/React 使用组件挂载与卸载生命周期包裹这些方法即可；此入口为原生模块生命周期，不是 qiankun HTML Entry，不直接加载旧 player.html。示例页为 `/static/microapp-example.html`，打开后自动读取库内已完成课件并挂载，支持下拉切换或粘贴数据地址；需要登录时沿用工作台会话，不会为演示新建生成任务。

容器必须有明确高度。模块、`microapp-runtime.js` 和 `microapp-assets.json` 需保持同一目录；建议同源反向代理 `/static/`、`/microapp/`、`/packages/`，保留签名查询参数。跨域部署需由网关为 JS 模块、数据和音频配置宿主域名 CORS；服务不会自动放开全部域名。签名过期后由业务后端重新查询课件获取链接。需要现代浏览器支持 ES module、Shadow DOM、ResizeObserver 和 dialog。

### 3.3 幂等和重新生成

`Idempotency-Key` 最大 200 字符。同一提交因网络重试而重发时继续使用原键；同键同内容重放已有任务，同键不同内容返回 409。**主动重新生成必须换一个新键。**

创建（POST）始终新增独立 `courseware_id`，不按题目或教学目标合并，默认重新调用模型生成；只有同一幂等键的网络重试重放原任务。更新（PATCH）仅修改指定课件，保留 ID 并增加 `revision`；旧 `package_id/result` 可能仍可读，新结果完成后更新关联，因此不能仅凭 `items[].result` 存在判断本次生成完成。指定课件正在排队或生成时更新返回 409，其他课件不受影响。

### 课件列表分页

`GET /api/courseware?page=1&page_size=20` 返回工作台课件列表，每份课件独立显示。支持 `query`（题干、标题、作业标识或课件 ID）、`grade`、`knowledge_point`、`status`（all / active / pending / generating / completed / failed）。先对全部记录筛选，再分页；年级与知识点优先匹配教学目标，历史记录回退原题标签。

响应为 `{items, pagination, stats, filters}`：`items` 为本页任务（每项仅一份课件，其 `items[0].id` 为课件 ID）；`pagination` 包含 `page/page_size/total/pages`；`stats` 包含全量课件的 `total/completed/active/failed`；`filters` 包含全量的 `grades/knowledge_points`。页码从 1 起，每页默认 20，最大 100；超出末页自动返回末页，空结果返回第 1 页。工作台可选每页 10、20、50 条；更改筛选或每页数量回到第一页。

旧的 `GET /api/jobs?unique_questions=true` 兼容保留，但不再合并同题记录，最多返回 100 条；需要完整遍历请使用分页接口。

### 3.4 状态和结果

任务查询响应顶层标识名为 `id`，回调中的同一标识名为 `job_id`。以下为完成结果字段节选，示例地址省略签名参数：

```json
{
  "id": "任务ID",
  "assignment_id": "lesson-q-001",
  "revision": 1,
  "status": "completed",
  "progress": 100,
  "items": [{
    "question_id": "business:q-001",
    "status": "completed",
    "package_id": "32位课件包ID",
    "error": null,
    "result": {
      "title": "一元一次方程",
      "slide_count": 6,
      "voice_mode": "qwen",
      "package_player_url": "/packages/课件包ID/player.html",
      "minio_package_player_url": "/minio-package/课件包ID/player",
      "html_url": "/packages/课件包ID/index.html",
      "content_url": "/packages/课件包ID/online.html",
      "player_url": "/player/课件包ID",
      "minio_player_url": "/minio-player/课件包ID",
      "embed_config_url": "/embed/课件包ID/config",
      "pptx_url": "/packages/课件包ID/courseware.pptx",
      "zip_url": "/packages/课件包ID/package.zip",
      "manifest_url": "/packages/课件包ID/manifest.json",
      "storage": {"bucket": "exam", "prefix": "explanations/七年级/题目摘要/课件包ID"}
    }
  }]
}
```

`minio_*` 地址仅在配置对象存储时返回，`storage` 可能为空。实际响应还包含题目、`stage/progress/events`、变式和其他数据。

| 状态 | 含义 |
|---|---|
| `pending` | 已受理，等待处理 |
| `generating` | 生成、语音、打包、上传或自动重试中 |
| `completed` | 本次生成成功；启用 MinIO 时完整上传后才完成 |
| `failed` | 当前题失败，查看 `error` 和 `events` |
| `partial_failed` | 批量任务部分失败，成功题目仍可用 |

建议每 2—3 秒轮询一次，终态后停止。网络重试期间可长期保持 `generating`，展示服务返回的阶段信息，不由前端自行判定生成超时。服务重启后恢复持久化队列及检查点，不重复生成已经完成的题目。

## 4. 前端直接引用播放文件

把 `src` 设置为结果中的 `minio_package_player_url`；未使用 MinIO 时设为 `package_player_url`，保留完整签名参数。外层 iframe 可以铺满页面，内部课件保持 16:9。

```html
<iframe
  src="从 result.minio_package_player_url 或 result.package_player_url 读取"
  title="题目讲解"
  style="display:block;width:100%;height:100dvh;border:0"
  sandbox="allow-scripts allow-downloads allow-modals"
  allow="autoplay">
</iframe>
```

每层嵌套 iframe 都需要 `allow="autoplay"`，播放由用户点击按钮触发；`allow-modals` 用于打印。直接打开 `index.html/online.html` 只有内容没有控件，这是预期行为；不自定义外壳时应打开 `player.html`。

### MinIO 播放入口

新生成对象目录为 `{S3_PREFIX}/{目标年级}/{目标学期}/{题目ID摘要}/{package_id}/`，例如 `explanations/七年级/全学期/题目摘要/课件包ID/`。以返回的 `storage.bucket/prefix` 为准，不自己推算摘要。原有课件目录和链接继续有效，不根据旧题库年级猜测目标后迁移。

`GET /minio-package/{package_id}/player` 校验权限后返回 307，`Location` 指向签名后的 MinIO `player.html`；播放文件尚未上传时返回 409。文件自包含，不需要给同目录图片或音频额外签名，也不会内嵌服务密钥或会过期的资源地址。

有对象存储权限的接入后端也可自行签名 `storage.prefix + '/player.html'`，响应类型使用 `text/html`，处置方式为 `inline`。对象签名过期后重新取得地址。浏览器必须能访问 MinIO 公网域名；`S3_PUBLIC_ENDPOINT_URL` 可与上传端点不同。

## 5. 包结构、语音及已有包升级

```text
player.html               # 自包含完整播放器，推荐打开
index.html                # 自包含纯内容，保留给自定义宿主
online.html               # 轻量纯内容，语音按页读取
courseware.pptx            # 16:9 PPTX，含演讲者备注
manifest.json             # 原题、资源、结果信息、语音类型、变式、文件列表
slides.json               # display、narration、flow、cues、audio_file 等
narration.json            # page_id、text、audio_file 映射
audio/page-001-{hash}.wav  # 已合成语音存在时提供
figures/                  # 配图资源
package.zip               # 整个目录，不递归包含 ZIP 自身
```

`voice_mode=qwen` 为已打包音频；`browser` 为设备朗读；`none` 为无语音。合成服务不可用时可能降为设备朗读，不要仅根据请求 `voice=true` 推断有 WAV。设备朗读效果及离线能力取决于浏览器语音支持。

`manifest.reading` 保存完整解析；`variants` 保存变式。题库变式 `source=resource`，自拟变式 `source=generated` 且题目 ID 可为空。`resource_ids` 为空表示未关联对应知识资源，不能对外标注已匹配课标。当前默认独立复核关闭，结果不代表教师验收。

已有包补入播放入口的维护命令：

```bash
python scripts/add_package_player.py --publish
# 只处理指定课件（可重复 --package-id）：
python scripts/add_package_player.py --package-id 课件包ID --publish
# 同时更新纯内容入口的显示样式：
python scripts/add_package_player.py --refresh-content --publish
```

该命令更新播放文件、ZIP 和 manifest，不调用模型或重新合成语音。不加 `--publish` 时只更新本地。`manifest.files` 中的 `player.html` 表示该包已经包含此入口。

## 6. 可选：JS SDK 自定义外壳

只有业务页面要自行设计目录、按钮和讲稿窗口时才需要 SDK。**SDK 接入纯内容页，不要把完整 `player.html` 作为 SDK 内容。**

SDK 文件：`/static/courseware-sdk.js`。构造方式：`new CoursewareSDK.Player(iframe, {timeout:12000})`。不依赖前端框架，不生成 UI。

### 6.1 获取配置

业务后端请求 `result.embed_config_url`，默认读取服务目录。需要 MinIO 内容时使用 URL API 设置参数，保留现有签名：

```js
const url = new URL(result.embed_config_url, GENERATION_SERVICE_BASE_URL);
url.searchParams.set('source', 'minio');
// 业务后端读取该 URL，将 JSON 返回给自己的前端。
```

配置格式示意：

```json
{
  "contentUrl": "https://对象域名/桶/目录/online.html?签名参数",
  "audio": {"page-001": "https://对象域名/桶/目录/audio/page-001-hash.wav?签名参数"},
  "motion": true,
  "fallback": {
    "contentUrl": "https://生成服务/packages/课件包ID/online.html?访问签名",
    "audio": {"page-001": "https://生成服务/packages/课件包ID/audio/page-001-hash.wav?访问签名"},
    "motion": true
  }
}
```

`audio` 可为空，`fallback` 可省略。接口使用与课件相同的访问校验，响应 `Cache-Control: no-store`。跨域使用应返回浏览器可访问的绝对地址；未配置 `PUBLIC_BASE_URL` 时，相对地址需在业务后端按生成服务地址补全。

### 6.2 最小接入示例

下例约定业务后端提供同源 `/your-api/courseware-config`，返回上述配置；SDK 地址换成实际托管地址。

```html
<iframe id="lesson" title="课件内容"
        style="width:100%;aspect-ratio:16/9;border:1px solid #dce4dd"
        sandbox="allow-scripts allow-downloads allow-modals" allow="autoplay"></iframe>
<button id="prev" disabled>上一页</button>
<button id="play" disabled>播放 / 暂停</button>
<button id="next" disabled>下一页</button>
<span id="status" role="status"></span>
<script src="/static/courseware-sdk.js"></script>
<script>
const $ = id => document.getElementById(id);
const player = new CoursewareSDK.Player($('lesson'));
player.on('ready', data => {
  // data.title：标题，data.state.outline：目录，data.reading：完整解析。
  $('play').disabled = !data.state.canPlay;
});
player.on('state', state => {
  $('status').textContent = `${state.page + 1} / ${state.totalPages}`;
  $('prev').disabled = state.page === 0;
  $('next').disabled = state.page === state.totalPages - 1;
  $('play').textContent = state.playing && !state.paused ? '暂停' : '播放';
});
player.on('error', ({message}) => { $('status').textContent = message; });
player.on('fallback', ({message}) => { console.info(message); });
$('prev').onclick = () => player.previous();
$('next').onclick = () => player.next();
$('play').onclick = () => player.play();
(async () => {
  const response = await fetch('/your-api/courseware-config');
  if (!response.ok) throw new Error('读取课件配置失败');
  player.load(await response.json());
})().catch(error => { $('status').textContent = error.message; });
window.addEventListener('pagehide', () => player.destroy());
</script>
```

可运行的完整示例为 [`web/embed-example.html`](../web/embed-example.html)。访问 `/static/embed-example.html?config=` 加 URL 编码后的同源配置地址，例如 `/embed/{package_id}/config?source=minio`。也支持 `?content=` 传公开纯内容 URL。单页应用卸载组件时调用 `destroy()`。

### 6.3 SDK 方法

| 方法 | 语义 |
|---|---|
| `load(config)` / `load(url)` | 加载纯内容，返回实例，不是 Promise；等待 `ready` 后再控制 |
| `on(event, handler)` | 订阅事件，返回取消订阅函数 |
| `next()` / `previous()` | 按实际显示页翻页 |
| `goTo(index)` | 跳转原始幻灯片索引，使用目录条目 `index` |
| `seek(page)` | 跳转自适应分页后的全局显示页索引 |
| `play()` | 播放中再次调用为暂停，暂停中调用为继续 |
| `stop()` | 停止朗读；翻页也会停止旧页音频 |
| `setAutoPlay(boolean)` | 当前节讲完后继续下一节，不自动触发首次播放 |
| `setMotion(boolean)` | 默认 true；尊重系统“减少动态效果”偏好 |
| `getState()` | 最近状态，尚未就绪时为 null |
| `print()` | 打印课件 |
| `destroy()` | 停止朗读并清理计时器、消息和订阅；不删除宿主 iframe |

所有索引从 0 开始。控制方法返回是否已发出消息，不代表执行或播放已经成功；未就绪时控制不会排队，最终以事件为准。`load/setMotion/destroy` 按各自约定使用。

### 6.4 事件与状态

| 事件 | 内容 |
|---|---|
| `loading` | `{contentUrl}`，开始加载 |
| `ready` | 标题、`voiceMode/duration/reading/variants/resources/questionLayout/images/state`；可能多次发出，应幂等更新 UI |
| `state` | 最新页码、目录和播放状态 |
| `fallback` | `{message}`，远程未就绪，开始切换本地副本 |
| `error` | `{message}`，加载或播放错误，不等同于生成任务失败 |

`state` 包含 `index`（原始页）、`part`（该页分页位置）、`page`（全局显示页）、`totalPages`、`outline`、`notes`（本页讲稿）、`playing/paused/autoNext/canPlay/motion/motionActive`。目录项为 `{index,title,kind,page}`。界面页码用 `page+1`，总页数用 `totalPages`，不能用结果 `slide_count` 替代实际分页数。讲稿与正文不要求逐字相同。

默认 12 秒未就绪时，有 `fallback` 则切换本地副本并发出事件，否则发出 `error`。这不是强制终止，内容稍后仍可就绪。长期打开导致音频签名过期时，重新获取配置并 `load`；SDK 不自动刷新签名。

### 6.5 原始消息协议

```js
lessonFrame.contentWindow.postMessage({
  channel: 'courseware', version: 1,
  type: 'command', command: 'goTo', value: 0
}, '*');
```

支持 `init/next/previous/goTo/seek/play/stop/print/setAutoPlay/setMotion/getState`，`init` 可传 `{audio,motion}`。内容发出 `ready/state/error`，数据位于 `payload`。接收时检查 `event.source === lessonFrame.contentWindow`、`channel` 和 `version`；推荐 sandbox 为不透明源，发送目标因此使用 `'*'`。`loading/fallback` 是 SDK 事件，不是内容页协议事件。

## 7. 题库、多选年级和原图

题目来源固定为 `exam.public`。`GET /api/exam/questions` 按 `subject/query/region/grades` 查询；`GET /api/exam/questions/random` 随机选题，支持重复参数 `grades=七年级&grades=八年级&grades=九年级`，匹配任一年级即可。不要把数组拼成一个 `grade` 字符串。

随机接口支持 `limit`（1—6，默认 3）和 `exclude`（逗号分隔的上一批 UUID）。候选不足时允许补回上一批，同批不重复。题库暂不可用时可返回最近 24 小时成功读取的真实题目，`cached:true` 应在界面说明。

`GET /api/exam/questions/{UUID}/images` 返回 `{question_id,images,attachments}`。原图按题库排序去重，一次完整返回，答案/解析专用附件不作为题干原图。生成请求使用 `question_id=exam:public:{UUID}`、对应 `source=exam/public/question/{UUID}` 且 `requires_images=true` 时，缺图会尝试从题库补读。

保持原图顺序，图片选项依赖该顺序。不要下载失败后自行画图替代原题。用户明确传入 `grades` 时保留多选原题年级，题库年级另存为 `source_grades`；实际讲解使用独立的 `target_grade`。目标为低年级时优先引用题目实物原图，不用抽象数轴代替看图数数。

## 8. 批量作业完成回调

仅批量接口支持 `callback_url`。服务须配置 `CALLBACK_SECRET`，地址须完整匹配 `CALLBACK_ALLOWED_URLS`。所有题目结束后发送 `explanation.completed`，包括部分失败。

```json
{
  "event": "explanation.completed",
  "event_id": "事件ID",
  "job_id": "任务ID",
  "assignment_id": "作业ID",
  "revision": 1,
  "status": "completed",
  "items": [{
    "question_id": "business:q-001",
    "status": "completed",
    "package_id": "课件包ID",
    "error": null,
    "result": {"package_player_url": "...", "minio_package_player_url": "..."},
    "variants": []
  }]
}
```

示例省略其他结果链接。请求头包含 `X-Explanation-Event`、`X-Explanation-Timestamp` 和 `X-Explanation-Signature`。签名为 `sha256=<HMAC_SHA256(CALLBACK_SECRET, 时间戳 + "." + 原始请求体字节)>`。

使用原始字节验签并按事件 ID 去重，按 `revision` 防止旧状态覆盖新状态，可设置 ±5 分钟时钟窗口。返回任意 2xx 表示成功，3xx 不跟随。失败最多尝试 5 次，间隔约 5、10、20、40 秒；最终失败可 `POST /api/callbacks/{event_id}/retry`。通知投递状态独立于生成状态。

## 9. 错误处理与联调

| 情况 | 处理方式 |
|---|---|
| 401 | API 未登录或缺少有效 `X-API-Key` |
| 403 | 核对签名有效期；跨域写入也可能被拒绝 |
| 404 | 核对任务、包 ID 和路径；尚未完成的包不可打开 |
| 409 | 被更新课件正在生成、幂等键冲突或播放文件尚未上传，读取 `detail` 区分 |
| 422 | 请求字段不合法、缺少必需原图、单题模式传了回调等，修正请求 |
| 503 | 题库或图片存储暂不可用，通常返回 `{detail,code}`，保留输入并允许重试 |
| 有内容没有控件 | 打开了纯内容入口，改用 `player.html` 或 SDK |
| 无声音 | 检查 `voice_mode/canPlay`、每层 iframe 的 autoplay、用户点击及音频签名 |
| 无动画 | 默认开启；检查 `motionActive` 和系统减少动态效果设置 |
| 直接打开正常，嵌入失败 | 检查业务页面 frame-src、代理 CSP、对象域名及内联脚本策略 |

`detail` 可能是字符串，也可能是参数校验数组。请求传输上限为 30 MB，不要与生成出的课件文件大小混淆。

联调应确认：重复网络提交不重复生成；同题同年级创建多份均能独立查询；按 ID 更新只改变指定课件；分页筛选覆盖全量记录；多选年级保留；原图和选项对应；MinIO 的播放文件能翻页、朗读；下载后可打开；签名过期后重新取得链接；可选 SDK 的 ready/state 与控件同步。

配图放大由 `player.html` 在播放器完整视口打开，不受课件 16:9 iframe 限制。使用 SDK 自建外壳时，在 `load` 配置中传入 `figureHost: true`，监听 `player.on('figure', ({src, alt, caption}) => ...)` 并由宿主展示大图；未声明此能力时，纯内容页保留自身的大图查看功能。
