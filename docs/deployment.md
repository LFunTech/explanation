# 题解工坊构建与部署

本项目使用与 `school-oms` 相同的 Woodpecker → Kaniko → Helm 发布流程。所有仓库设置与发布目标都属于 `LFunTech/explanation`（Woodpecker 仓库 65）；仓库 7 只作为参考。

前端为原生 ES modules，由 FastAPI 同源提供，不需要单独 Node 构建或上传 COS。镜像包含 API、工作台、完整播放器和微应用资源。

## 文件与运行方式

| 文件 | 用途 |
| --- | --- |
| `Dockerfile` / `.dockerignore` | Python、中文字体、非 root 运行；排除密钥、数据、备份与测试产物 |
| `.woodpecker/validate.yaml` | 普通 push、PR、tag、manual 执行测试及 Helm 检查 |
| `.woodpecker/build.yaml` | 发布标签通过验证后构建 AMD64 镜像 |
| `.woodpecker/deploy-test.yaml` | `test-vX.Y.Z` 发布到 `explanation-test` 命名空间 |
| `.woodpecker/deploy-prod.yaml` | `prod-vX.Y.Z` 发布到 `explanation` 命名空间 |
| `deploy/chart/` | 本项目 Helm Chart：Deployment、Service、Ingress、PVC |
| `deploy/values.yaml` / `deploy/values-prod.yaml` | 测试 / 生产容量与资源参数 |
| `deploy/compose.yaml` | 独立服务器部署；根目录 Compose 仅提供可选知识库 |
| `scripts/ci/` | 镜像构建、配置校验、集群认证、发布及 Chart 验证 |

镜像仓库为 `docker-hub.f123.pub/lfun/explanation`，每次发布写入版本标签及对应 `-amd64` 标签，不依赖 `latest`。CI 验证与镜像构建使用参考项目已采用的 `docker-hub.f123.pub/base/python:3.11-slim`，避免构建节点无法直连 Docker Hub；本地 Docker 默认使用 Python 3.12，也可用 `PYTHON_IMAGE` 构建参数覆盖。

任务与课件元数据保存在 `/data/app.sqlite3`，生成产物、缓存及恢复检查点也在 `/data`。应用固定 **一个 Uvicorn worker、一个 Pod**，更新策略为 **Recreate**，允许短暂不可用。`WORKER_CONCURRENCY` 是这个进程内部的异步任务数，不能用多个 Pod 替代。重启会从任务库恢复未完成工作。

项目 Chart 明确管理这些约束，因此未直接套用参考项目的通用 `lfun-application` Chart。Service 对外端口 80，容器端口 18765，健康检查为 `/health`。

## Woodpecker 仓库配置

在 <https://woodpecker.f123.pub/repos/65/settings> 配置。已确定测试域名为 `https://explanation-test.f123.pub`，测试命名空间为 `explanation-test`：

- Pipeline path：`.woodpecker/`（带末尾斜杠）；留空也会默认发现此目录。
- Timeout：120 分钟，给首次安装依赖和镜像构建留出时间。
- Agent：须有 `platform=linux/amd64` 标签；无需给本项目开启宿主卷或特权执行权限。
- 保留 fork PR 审批。普通提交只执行无部署凭证的验证步骤。
- 发布通过 tag 事件触发，不需要开启平台的手动 Deployment 功能。

Secret 在仓库 65 的 Secrets 页面配置，至少允许 **tag** 事件。凭证不要开放给 PR。若全局已提供同名 Secret，可直接引用，不能修改全局 Secret 或仓库 7 的配置。

| Secret | 内容 |
| --- | --- |
| `DOCKER_USERNAME` / `DOCKER_PASSWORD` | 对目标镜像仓库有推送权限的凭证；用于构建和创建当前命名空间的拉取 Secret |
| `kubeconfig_test` / `kubeconfig_prod` | 对应集群配置，可为完整 YAML/JSON 或 Base64；须有有效 current-context |
| `test_config` / `prod_config` | **本项目**完整环境配置，采用下方 env 格式；不能复制 school-oms 的应用配置 |

仅部署一个环境时，只需准备对应的 kubeconfig 和应用配置。Kubeconfig 应能管理目标命名空间、Secret、Deployment、Service、Ingress、PVC 与 Helm release；如没有创建 namespace 权限，先由集群管理员创建并授权。

### 应用配置

以根目录 `.env.example` 为模板，将真实值保存到仓库 65 的环境 Secret。格式为逐行 `KEY=value`，支持空行、整行注释及一对外层引号；值按字面量保留，不做 `$VAR` 展开，不支持多行值、`export` 或行尾注释。不要打印 Secret 或把运行 env 加入源码。

必填项：

```dotenv
API_KEY=设置独立的服务访问密钥
DASHSCOPE_API_KEY=配置千问密钥
PUBLIC_BASE_URL=https://替换为实际课件域名
EXAM_DB_HOST=集群可访问的公共题库地址
EXAM_DB_PORT=5432
EXAM_DB_NAME=exam
EXAM_DB_USER=公共题库只读账号
EXAM_DB_PASSWORD=对应密码
```

按业务配置以下项：

- `QWEN_MODEL`、`QWEN_VISION_MODEL`、`TTS_MODE=qwen`、`QWEN_TTS_*`：模型与打包语音。
- `EXAM_MINIO_*`：只读获取题库原图。含图原题需要可用的图片凭证。
- `S3_*`：课件输出桶、服务端上传地址、浏览器可访问地址与对应凭证。启用时需同时设置访问密钥。
- `VECTOR_DATABASE_URL`：本项目独立课标库。可留空；生成仍使用请求传入的课标，但本地课标选择和分析需要库内数据。生产课标正文应迁移或重新导入。
- `WORKER_CONCURRENCY`：1—20，默认 20；按模型限流与容器内存调整。

`PUBLIC_BASE_URL` 必须是 HTTPS 根域名，不支持部署到 `/explanation` 之类的子路径。发布脚本从此字段提取 Ingress host；DNS 需指向对应 Ingress，配置可用的默认 TLS 证书，或在环境 values 中指定 `ingress.tlsSecret`。`DATA_DIR` 始终由部署覆盖为 `/data`。

不要把本机 `127.0.0.1`、Windows 路径或桌面数据库地址原样用于容器。外部 PostgreSQL、MinIO 和模型接口必须从集群可达。运行配置以 Kubernetes Secret 注入，不写入镜像层。

## 存储与迁移

首次部署默认申请测试 50Gi / 生产 100Gi 的 ReadWriteOnce PVC。填写环境 values 中的 `persistence.storageClass`，或确认集群默认类可用；存储必须正确支持文件锁与 SQLite，优先使用块存储。需要挂载现有数据时设置 `persistence.existingClaim`。

容器 UID/GID 为 10001，Pod 使用 fsGroup 10001，存储必须支持该用户读写。PVC 标注 `helm.sh/resource-policy: keep`，卸载 release 时保留数据；重新安装需通过 existingClaim 接回原卷。

新部署不会自动带入开发机的历史课件。迁移前暂停生成、停止源服务，再一致性复制整个 `data/` 到 PVC；不能只复制正在写入的 SQLite 主文件。课标 PostgreSQL 独立迁移，可使用 `scripts.backup_local` 导出、验证后恢复到新库。`backups/`、运行数据库、原始 PDF 与课件不随源码和镜像上传。

## 发布与验证

先将代码提交到本仓库，普通 main push 会执行测试和 Chart 校验。确认环境配置、域名、PVC 和外部连接后创建发布标签；下面命令会触发真实发布：

```bash
git tag test-v0.1.0
git push origin test-v0.1.0
# 测试环境验证后，按需创建新的生产版本标签：
git tag prod-v0.1.0
git push origin prod-v0.1.0
```

流水线顺序为 validate → build → 对应 deploy。应用 Secret 与镜像拉取 Secret 只写入目标命名空间；配置摘要写入 Pod annotation，修改配置后重跑同一版本也会更新实例。发布等待 readiness 通过，超时则流水线失败并输出 Pod/PVC 状态及集群事件，不声称服务已就绪。

```bash
kubectl -n explanation-test get pods,pvc,ingress
kubectl -n explanation-test logs deployment/explanation --tail=100
curl -fsS https://实际测试域名/health
```

验证工作台登录、查询课件、题库配图、生成与语音打包、MinIO 链接、完整播放器、微应用自动示例。旧数据未迁移时，示例页会提示暂无完成课件。

回滚使用 `helm history explanation -n explanation-test` 找到旧版本，再执行 `helm rollback explanation 版本号 -n explanation-test --wait --timeout 10m`。只回滚应用，不恢复数据库、课件文件或部署脚本在 Helm 外更新的 Secret；涉及数据库结构或配置变化时，应配套恢复兼容的配置与数据备份。不要删除 PVC。

## 本地构建与单机运行

```bash
docker build -t explanation:local .
cp .env.example deploy/runtime.env
# 编辑 runtime.env；在外部反向代理配置 HTTPS 与域名。
EXPLANATION_IMAGE=explanation:local docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml logs -f explanation
```

上述后续 Compose 命令也需在环境中保留 `EXPLANATION_IMAGE`。数据保存在命名卷；不要执行带 `-v` 的 down。镜像可通过构建参数 `PYTHON_IMAGE` 指定团队已同步的 Python 3.12 基础镜像。

离线配置检查：`sh scripts/ci/validate-chart.sh`（需要 Helm 3）；应用回归：`python -m pytest -q`。没有 Docker 引擎时只能完成代码与 Chart 检查，实际镜像构建以 CI 结果为准。
