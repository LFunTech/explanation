# 作业系统接入契约 v1

服务以一题一包为生成和复用单位。每次请求接受 1—50 道题，直接传入题目内容，避免生成过程中题库变动导致读到另一版本。页面也可先调用 `GET /api/exam/questions` 选题。

随机选题使用 `GET /api/exam/questions/random`：支持 `subject`、`grade`、`region`、`limit`（默认 3，最多 6）和 `exclude`（逗号分隔的上一批题目 ID）。在当前学科和对应学段的 ACTIVE 题目中随机抽取；候选不足时允许补回上一批题目，同批 ID 不重复。未标注具体年级时继续返回范围说明，不将学段匹配冒充年级匹配。

题图读取使用 `GET /api/exam/questions/{公共题目UUID}/images`，返回 `{question_id,images:[data_url],attachments:[{id,usage_type,sort_order}]}`。仅解析该 ACTIVE 公共题目的附件关联，按排序去重；不接受请求方传入对象地址。答案/解析专用附件不当作原题图。所有原图下载成功后一次返回，最多 8 张，单图最大 8 MB。MinIO 访问配置由服务端保管。

## 请求

`POST /api/jobs` 返回 HTTP 202。可携带 `Idempotency-Key`（最大 200 字符）；同键同内容返回同一任务，同键不同内容返回 409。请求 JSON 格式见 README 和 `/docs`。

| 字段 | 约定 |
|---|---|
| assignment_id | 必填，作业系统稳定标识 |
| questions[].question_id | 必填，批次内唯一，不根据题干猜 ID |
| stem / options / images | 完整题干、选项数组、图片 data URL 数组；原图随输出打包 |
| sub_questions | `{id,stem,answer,analysis}` 数组，小题 ID 唯一 |
| answer / analysis | 参考答案与解析，仍须模型独立核验 |
| subject / grade / region | 学科、年级、区域；区域默认上海 |
| knowledge_points / knowledge_point_ids | 知识点名称，以及可选 exam 公共知识点 ID |
| question_type / difficulty | 题型；难度为基础、中等、较难 |
| source / requires_images | 来源引用；源 ID 对应 exam 公共题目且缺图时自动读取 MinIO。其他缺图输入返回 422；存储不可用时明确返回错误 |
| options.mode | qwen 或 sample；样例必须与内置题及范围完全相同 |
| options.depth | brief、standard、deep |
| options.variant_count | 0—5；内置样例最多 2 道 |
| options.voice / force | 生成语音；是否跳过内容复用创建新版本 |
| callback_url | 可选，必须完整匹配服务端白名单 |

HTTP 202 仅表示已持久化受理，不代表生成成功。任务在服务端后台运行；关闭浏览器不取消任务。

## 状态与结果

`GET /api/jobs/{job_id}` 返回每道题的 `status/stage/progress/error/package_id/result`。`GET /api/assignments/{assignment_id}` 返回该作业的历史生成批次；无历史时为 `not_generated`。

| 状态 | 含义 |
|---|---|
| not_generated | 作业没有提交过生成任务 |
| pending | 已受理，等待处理 |
| generating | 生成、复核、语音或打包中 |
| completed | 全部题目完成，可打开结果 |
| partial_failed | 部分失败，其余结果可用（作业汇总状态） |
| failed | 全部失败或当前题目失败 |

一题完成后立即提供 HTML、PPTX、ZIP、manifest 地址及变式数组。原题 ID 在 `items[].question_id` 和包内 `manifest.question.question_id` 中一致。题库变式 `source=resource`，ID 为实际资源 ID；自拟变式 `source=generated`，ID 为 null，接收侧不能把它当已有题库记录。

`manifest.schema_version=1.1` 的 `slides` 为主讲解页：简单题 3–5 页、复杂题最多 10 页，题干与原图合并在原题页。完整知识说明、解题步骤、答案、易错和总结位于 `manifest.reading`，相关变式仍在 `manifest.variants`，HTML 提供展开阅读。`quality` 记录 `page_count/page_limit/diagram_count/audio_duration_seconds/duration_limit_seconds`；仅千问语音存在实测时长，其他模式为 null。独立内容复核包含实际知识示意图与逐页台词，不代表教师验收。

解题页的 `flow` 是 `{number,title,display,narration}` 数组，`cues` 是 `{node,start_seconds,end_seconds}` 数组；`node` 从 0 开始，时间相对当前页 WAV，按各节点实际合成时长计算。单页 WAV 按节点顺序无损合并，暂停/继续不重置当前节点，翻页停止旧页音频。知识点 `resource_ids` 指向 `manifest.resources` 中实际采用的课标或知识资源；空数组表示没有对应引用，不能对外声称已匹配课标。

## 回调

所有题目结束后入队 `explanation.completed` 事件，包括部分失败。回调 POST JSON：

```json
{
  "event": "explanation.completed",
  "event_id": "唯一事件标识",
  "job_id": "任务标识",
  "assignment_id": "作业标识",
  "revision": 1,
  "status": "completed",
  "items": [{"question_id":"q-001","status":"completed","package_id":"包标识","error":null,"result":{"html_url":"...","pptx_url":"...","zip_url":"...","manifest_url":"..."},"variants":[]}]
}
```

接收方使用 `X-Explanation-Event` 去重，使用 `revision` 防止失败重试后的新状态被旧事件覆盖；允许一个旧事件晚于新事件到达。签名为：

```text
X-Explanation-Timestamp: Unix 秒
X-Explanation-Signature: sha256=<hex HMAC>
HMAC_SHA256(CALLBACK_SECRET, timestamp + "." + 原始请求体字节)
```

验签使用原始字节和恒定时间比较，可设 ±5 分钟时钟窗口。返回任意 2xx 表示接收成功，3xx 不跟随。失败自动重试最多 5 次，约等待 5、10、20、40 秒；最终失败可 `POST /api/callbacks/{event_id}/retry`。回调送达状态独立于题目生成状态。

## 复用、覆盖与重试

- 复用指纹包括完整原题和 ID、年级/区域/题型/难度、生成选项、实际选用资源快照及生成配置版本。
- `force=false` 复用匹配的已完成结果；同时进入队列的相同内容在单服务实例中串行核对，避免重复计费。
- `force=true` 新建不可变结果目录，不覆盖旧 HTML、音频或 PPTX。作业侧仅在新版本完成后切换关联。
- `POST /api/jobs/{id}/retry` 只把失败题目重新排队。已经成功的题目保持可读，通过复核的讲解恢复检查点，语音失败只补缺少的音频。
- 正常停机取消后台执行，重启将未完成项重新排队；已完成结果不重生成。缺失/错误内容不发布为成功。

## 包、存储与嵌入

```text
index.html              # 自包含页面，已合成语音和数学字体均内嵌
courseware.pptx         # 16:9 PPTX + 每页演讲者备注
manifest.json           # 原题、资源出处、复核摘要、语音模式与变式关联
slides.json             # 稳定 page_id、display、notes、narration 和音频
narration.json          # page_id → 台词 → audio_file
audio/page-001-{hash}.wav # 仅千问语音模式存在；hash 绑定台词，重写不复用旧音频
figures/page-002.svg    # 按需生成的知识图；HTML 内嵌同一 SVG
figures/page-002.png    # 相同绘图指令输出，供 PPTX 和模型图文复核
package.zip
```

本地目录：`data/packages/{package_id}/`。对象存储目录：`{S3_PREFIX}/{question_id哈希前16位}/{package_id}/`，原始业务 ID 不直接拼接路径。成功上传全部对象后才完成任务，服务地址继续提供同源访问；如改用对象存储直接访问，由部署方配置访问域名与授权，不把密钥放进包中。

```html
<iframe src="从 result.html_url 读取"
        title="题目讲解" style="width:100%;height:760px;border:0"
        sandbox="allow-scripts allow-downloads allow-modals"
        allow="fullscreen"></iframe>
```

页面不需要调用作业系统接口；不传 API_KEY 给 iframe。签名链接有效期 7 天，重新查询任务取得更新链接。同源嵌入、外部域名 CSP、语音设备兼容性需由双方在真实作业系统验证。
