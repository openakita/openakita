# OpenAkita 用户反馈函数（阿里云函数计算 FC 3.0）

接收用户反馈（错误报告 / 功能建议），存储到阿里云 OSS，自动创建 GitHub Issue 用于跟踪管理。
支持用户查询反馈处理进度、接收开发者回复的邮件通知、邮件退订。

替代之前的 Cloudflare Worker，确保中国大陆用户可正常提交反馈。

---

## 架构概览（Pre-signed URL 直传方案）

```
用户 → FeedbackModal → Python 后端 ──POST /prepare──→ 【FC 函数】→ 验证码 + 频率限制
                                  ←─ upload_url ────←
                                  ──PUT zip───────→ 【OSS 直传】
                                  ──POST /complete──→ 【FC 函数】→ 创建 GitHub Issue + 生成 feedback_token
                                           ↕
                                    阿里云人机验证 2.0

用户 → 我的反馈页面 → Python 后端 ──GET /status/{id}──→ 【FC 函数】→ 读取 OSS 元数据
                              ──POST /status/batch──→ 【FC 函数】→ 批量读取状态
                              ──POST /reply/{id}───→ 【FC 函数】→ 代发评论到 GitHub + 写入 OSS

GitHub Issue ──webhook──→ 【FC 函数 /webhook/github】→ 更新 OSS 元数据 + 邮件通知用户
                          （双重去重：[User Reply] 前缀 + PAT 账号匹配）
```

**核心设计**：大文件（ZIP）通过 Pre-signed URL 直传到 OSS，FC 函数只处理轻量 JSON 请求，
不承受大文件传输的内存压力和 payload 限制。

所有敏感密钥 **只存在于** FC 函数的环境变量中（在阿里云控制台配置），
开源代码仓库中不包含任何密钥。

---

## 分步部署指南（FC 3.0）

### 第 1 步：创建 OSS Bucket（已有可跳过）

1. 登录 [OSS 控制台](https://oss.console.aliyun.com/)
2. 创建 Bucket（例如 `openakita-feedback`），地域选 **华东1（杭州）**
3. 读写权限：**私有**（FC 函数通过 AK/SK 服务端访问）
4. 记下：
   - **内网 Endpoint**：`https://oss-cn-hangzhou-internal.aliyuncs.com`（FC 同地域使用，免流量费）
   - **外网 Endpoint**：`https://oss-cn-hangzhou.aliyuncs.com`（Pre-signed URL 及本地管理脚本使用）

### 第 2 步：创建 RAM 子账号

1. 进入 [RAM 控制台](https://ram.console.aliyun.com/) → 用户 → 创建用户
2. 创建编程访问用户（例如 `feedback-oss-rw`），勾选 **OpenAPI 调用访问**
3. 授权以下两个策略：
   - `AliyunOSSFullAccess`（或自定义策略限定到具体 Bucket）
   - `AliyunYundunAFSFullAccess`（人机验证 2.0 服务端校验所需）
4. **保存 AccessKey ID 和 AccessKey Secret**（仅显示一次！）

### 第 3 步：配置阿里云人机验证 2.0

1. 进入 [人机验证控制台](https://yundun.console.aliyun.com/?p=cas)
2. 创建验证场景，接入方式选 **Web/H5**
3. 创建完成后，在控制台能看到以下参数：

| 控制台参数 | 说明 | 用在哪里 |
|-----------|------|---------|
| **prefix**（身份标） | 前端公开标识，用于初始化验证码组件 | 后端 `.env` → `CAPTCHA_PREFIX` |
| **ekey**（密钥） | 旧版 AFS 接口使用，**本项目不需要** | — |
| **场景名称** | 仅供人类辨识，代码中不使用 | — |
| **场景ID** | 前端初始化 + 服务端验证都需要 | 后端 `.env` → `CAPTCHA_SCENE_ID` **且** FC 环境变量 → `CAPTCHA_SCENE_ID` |

> **服务端校验说明**：CAPTCHA 2.0 的服务端校验使用 `VerifyIntelligentCaptcha` OpenAPI，
> 通过 RAM 子账号的 AccessKey 认证（即第 2 步创建的同一账号）。
> 控制台中的 `ekey` 是旧版 AFS 接口的密钥，本项目使用新版 API，**不需要配置 ekey**。
>
> **安全说明**：`prefix` 和 `场景ID` 是公开标识（类似 reCAPTCHA site key），不是密钥，可以下发到前端。

### 第 4 步：创建 GitHub Fine-grained PAT

1. 进入 [GitHub → Settings → Developer Settings → Fine-grained tokens](https://github.com/settings/tokens?type=beta)
2. 创建新 Token：
   - 名称：`openakita-feedback-bot`
   - 仓库范围：**Only select repositories** → `openakita/openakita`
   - 权限：**Issues: Read and write**
   - 有效期：1 年（到期前记得续期）
3. 复制 Token（`github_pat_xxx...`）

### 第 4.5 步：配置 GitHub Webhook（接收 Issue 事件）

> **此步骤在第 5 步之后执行**（需要先获得 FC 的公网 URL），这里提前列出便于通读流程。

1. 进入 GitHub 仓库 → **Settings** → **Webhooks** → **Add webhook**
2. 配置：
   - **Payload URL**: `https://<你的FC触发器URL>/webhook/github`
   - **Content type**: `application/json`
   - **Secret**: 自定义一个随机字符串（必须与 FC 环境变量 `GITHUB_WEBHOOK_SECRET` 一致）
   - **Which events**: 选择 **Let me select individual events** → 勾选 **Issues** 和 **Issue comments**
3. 点击 **Add webhook**
4. 在 Webhook 页面查看 **Recent Deliveries**，确认 `ping` 事件返回 `200`

### 第 5 步：创建 FC 函数

1. 进入 [函数计算 FC 3.0 控制台](https://fcnext.console.aliyun.com/)

2. 点击 **创建函数** → 选择 **事件函数** → **创建事件函数**：
   - **函数名称**：`openakita-feedback`
   - **运行环境**：Python 3.10
   - **请求处理程序（Handler）**：`index.handler`
   - **内存规格**：256 MB
   - **执行超时时间**：60 秒
   - 点击创建

3. **上传代码**：
   - 进入函数详情页 → **代码** 页签
   - 方式一（在线编辑器）：粘贴 `index.py` 内容，创建 `requirements.txt`
   - 方式二（ZIP 上传）：将 `index.py` 和 `requirements.txt` 打包为 ZIP 上传
   - 上传后点击 **部署代码**

4. **安装依赖**（重要！）：
   - 在代码页签的 **终端** 中执行：
     ```bash
     cd /code && pip install -r requirements.txt -t .
     ```
   - 安装完成后，**再次点击「部署代码」**，否则依赖不会生效
   - 或者使用 **层（Layer）**：创建一个包含 `oss2` 和 `requests` 的层，绑定到函数（无需重复部署）

5. **配置环境变量**：
   - 进入 **配置** 页签 → **环境变量** → 编辑
   - 添加以下变量：

   | 环境变量名 | 值 | 来源 |
   |-----------|---|------|
   | `OSS_ENDPOINT` | `https://oss-cn-hangzhou-internal.aliyuncs.com` | 第 1 步（内网 Endpoint，FC 同地域免流量费） |
   | `OSS_PUBLIC_ENDPOINT` | `https://oss-cn-hangzhou.aliyuncs.com` | 第 1 步（外网 Endpoint，用于生成 Pre-signed URL）<br>若不设置，自动从 `OSS_ENDPOINT` 去掉 `-internal` 推导 |
   | `OSS_BUCKET` | `openakita-feedback` | 第 1 步 |
   | `OSS_ACCESS_KEY_ID` | `LTAI5t...` | 第 2 步 RAM 子账号（同时用于 CAPTCHA 校验） |
   | `OSS_ACCESS_KEY_SECRET` | `xxxxxx` | 第 2 步 RAM 子账号 |
   | `GITHUB_TOKEN` | `github_pat_xxx` | 第 4 步 |
   | `GITHUB_REPO` | `openakita/openakita` | 固定值 |
   | `GITHUB_WEBHOOK_SECRET` | 自定义随机字符串 | 第 4.5 步（GitHub Webhook 签名校验，与 GitHub 端配置一致） |
   | `GITHUB_PAT_LOGIN` | PAT 所属的 GitHub 用户名 | 可选，用于 Webhook 去重（识别 PAT 代发的评论）。填写第 4 步中 PAT 所属的 GitHub 账户用户名 |
   | `CAPTCHA_SCENE_ID` | 控制台的「场景ID」 | 第 3 步（留空则跳过验证码校验） |
   | `NOTIFY_EMAIL` | `dev@example.com` | 可选，接收开发者内部邮件通知 |
   | `RESEND_API_KEY` | `re_xxx` | 可选，Resend 邮件服务（同时用于开发者通知和用户回复通知） |
   | `PUBLIC_URL` | `https://feedback-openakita.fzstack.com` | 可选，FC 公网地址（用于生成退订链接）。注意：FC 保留 `FC_` 前缀，不能用 `FC_PUBLIC_URL` |

6. **创建 HTTP 触发器**：
   - 进入 **触发器** 页签 → **创建触发器**
   - 触发器类型：**HTTP 触发器**
   - 认证方式：**无需认证**（函数内部通过验证码 + 频率限制来防滥用）
   - 请求方法：勾选 **GET**、**POST**、**OPTIONS**
   - 点击确定

7. 记下 **公网访问地址**，格式为：
   ```
   https://<trigger-id>.cn-hangzhou.fcapp.run
   ```
   也可以在 FC 控制台或 DNS 中绑定自定义域名（例如 `feedback-openakita.fzstack.com`）。

### 第 6 步：测试函数

```bash
# 健康检查
curl https://<你的触发器URL>/health

# 预期响应：
# {"status": "ok", "service": "feedback-fc"}
```

### 第 7 步：配置 OpenAkita 后端

> **官方发行版用户**：`config.py` 已预填了官方 FC 地址和验证码标识的默认值，
> **无需任何配置即可开箱使用**。以下仅适用于 fork 用户或自建 FC 部署。

如果你使用自己的 FC 部署，在 `.env` 文件中覆盖（**不要** 提交到 Git）：

```bash
# FC 函数 URL（或自定义域名）
BUG_REPORT_ENDPOINT=https://你的FC触发器URL

# 人机验证 2.0 公开标识（来自第 3 步，这两个不是密钥，可以下发到前端）
CAPTCHA_SCENE_ID=你的场景ID
CAPTCHA_PREFIX=你的prefix身份标
```

修改后重启 OpenAkita 后端使配置生效。

### 第 8 步：端到端验证

1. 打开 Setup Center 的反馈弹窗
2. 提交一个测试错误报告
3. 检查：
   - [ ] FC `/prepare` 正常返回 `upload_url` 和 `report_date`
   - [ ] OSS Bucket 中出现了 `feedback/<日期>/<id>/report.zip` 和 `metadata.json`
   - [ ] `metadata.json` 包含 `contact_email`、`feedback_token` 等新字段
   - [ ] OSS `_index/<id>.json` 索引文件已创建
   - [ ] FC `/complete/{id}` 正常返回 `feedback_token` 和 `issue_url`
   - [ ] `openakita/openakita` 仓库中出现了带标签的 Issue
   - [ ] FC `/status/{id}?token=xxx` 正确返回状态和回复列表
   - [ ] 在 GitHub Issue 下评论后，Webhook 触发 FC 更新 `developer_replies`
   - [ ] 用户邮件通知正常发送（如已配置 `RESEND_API_KEY` 和 `contact_email`）
   - [ ] 退订链接正常工作（`/unsubscribe/{id}?token=xxx`）
   - [ ] 用户回复功能：前端发送回复 → FC 代发到 GitHub Issue → 本地列表即时显示
   - [ ] 用户回复 Webhook 去重：代发的 `[User Reply]` 评论不会被再次追加到 `developer_replies`
   - [ ] 人机验证弹窗正常弹出（如已配置）

---

## 安全分层总结

```
开源代码仓库（公开可见）：
  ├── config.py 默认值        → 预填官方发行版公开标识（FC URL、场景ID、prefix）
  │                             ※ 这些不是密钥，fork 用户可通过 .env 覆盖
  ├── FC 函数代码              → index.py（纯逻辑，零密钥）
  └── 前端验证码逻辑           → 运行时从 /api/feedback-config 获取配置

阿里云 FC 环境变量（控制台配置，仅服务端可见）：
  ├── OSS_ACCESS_KEY_ID       → ⚠️ 密钥（同时用于 CAPTCHA 2.0 校验）
  ├── OSS_ACCESS_KEY_SECRET   → ⚠️ 密钥
  ├── OSS_PUBLIC_ENDPOINT     → 外网 Endpoint（可选，用于生成 Pre-signed URL）
  ├── GITHUB_TOKEN            → ⚠️ 密钥
  ├── GITHUB_WEBHOOK_SECRET   → ⚠️ 密钥（Webhook 签名校验）
  ├── GITHUB_PAT_LOGIN        → 公开用户名（可选，Webhook 去重）
  ├── RESEND_API_KEY          → ⚠️ 密钥（可选）
  └── PUBLIC_URL              → 公开地址（用于退订链接，非密钥）
```

**开源代码仓库中不包含任何密钥。** Pre-signed URL 有效期仅 10 分钟，
限定路径且仅允许 PUT 操作，无法用于读取或遍历 OSS 数据。

---

## OSS 目录结构

```
openakita-feedback/
  feedback/
    2026-03-08/
      abc123def456/
        report.zip         # 反馈包（ZIP，含 metadata、日志、截图）
        metadata.json      # 结构化元数据 + 状态 + Issue 链接 + 开发者回复
    2026-03-09/
      ...
  _index/
    abc123def456.json      # 快速索引: {date, feedback_token} — 避免遍历日期目录
    xyz789.json
  _ratelimit/
    ip/<ip>/<date>.txt     # 单 IP 每日计数器
    global/<date>.txt      # 全局每日计数器
```

### metadata.json 完整结构

```json
{
  "id": "abc123def456",
  "type": "bug",
  "title": "应用启动后崩溃",
  "summary": "点击设置后白屏...",
  "system_info": "OS: Windows 10 ...",
  "status": "open",
  "ip": "1.2.3.4",
  "date": "2026-03-08",
  "contact_email": "user@example.com",
  "email_unsubscribed": false,
  "feedback_token": "aBcDeFgHiJkL_1234567890ab",
  "labels": ["bug", "status:open", "os:Windows"],
  "developer_replies": [
    { "author": "dev-username", "body": "已确认此问题", "created_at": "2026-03-09T12:30:00Z" },
    { "author": "user", "body": "感谢回复，已解决", "created_at": "2026-03-10T08:00:00Z", "source": "user_reply" }
  ],
  "created_at": "2026-03-08T08:00:00+00:00",
  "completed_at": "2026-03-08T08:01:30+00:00",
  "resolved_at": null,
  "github_issue_url": "https://github.com/openakita/openakita/issues/42",
  "size_bytes": 123456
}
```

## API 接口

### POST /prepare

鉴权 + 签发预签名上传 URL。FC 函数不接收大文件，只处理轻量 JSON。

**请求体（JSON）：**
```json
{
  "report_id": "abc123def456",
  "title": "应用启动后崩溃",
  "type": "bug",
  "summary": "点击设置后白屏...",
  "system_info": "OS: Windows 10 | Python: 3.11 | OpenAkita: 1.25.9",
  "contact_email": "user@example.com",
  "captcha_verify_param": "{\"sceneId\":\"xxx\",\"certifyId\":\"xxx\",\"deviceToken\":\"xxx==\",...}"
}
```

新增字段：
- `contact_email`（可选）— 用户邮箱，用于接收开发者回复通知

**响应（200）：**
```json
{
  "upload_url": "https://openakita-feedback.oss-cn-hangzhou.aliyuncs.com/feedback/2026-03-09/abc123/report.zip?Expires=...&Signature=...",
  "report_id": "abc123def456",
  "report_date": "2026-03-09"
}
```

客户端拿到 `upload_url` 后，用 HTTP PUT 直接把 ZIP 文件上传到 OSS（无需凭证）。

### POST /complete/{id}

确认上传完成，创建 GitHub Issue，生成 `feedback_token` 用于用户后续查询。

**请求体（JSON）：**
```json
{
  "report_date": "2026-03-09"
}
```

**响应（200）：**
```json
{
  "status": "ok",
  "report_id": "abc123def456",
  "feedback_token": "aBcDeFgHiJkL_1234567890ab",
  "issue_url": "https://github.com/openakita/openakita/issues/42"
}
```

`feedback_token` 由客户端本地存储，用于后续查询状态（无需登录）。

### GET /status/{report_id}?token=xxx

查询单条反馈的处理状态和开发者回复。

**查询参数：**
- `token`（必填）— 提交时返回的 `feedback_token`

**响应（200）：**
```json
{
  "report_id": "abc123def456",
  "title": "应用启动后崩溃",
  "type": "bug",
  "status": "in_progress",
  "labels": ["bug", "status:in_progress", "os:Windows"],
  "created_at": "2026-03-09T08:00:00+00:00",
  "completed_at": "2026-03-09T08:01:30+00:00",
  "resolved_at": null,
  "github_issue_url": "https://github.com/openakita/openakita/issues/42",
  "developer_replies": [
    {
      "author": "dev-username",
      "body": "已确认此问题，将在下个版本修复。",
      "created_at": "2026-03-09T12:30:00Z",
      "source": "developer"
    }
  ],
  "latest_reply_at": "2026-03-09T12:30:00Z"
}
```

### POST /status/batch

批量查询多条反馈状态（最多 50 条）。

**请求体（JSON）：**
```json
{
  "items": [
    { "report_id": "abc123def456", "token": "aBcDeFgHiJkL_1234567890ab" },
    { "report_id": "xyz789", "token": "mNoPqRsT_9876543210yz" }
  ]
}
```

**响应（200）：**
```json
{
  "results": {
    "abc123def456": { "report_id": "abc123def456", "status": "in_progress", "..." : "..." },
    "xyz789": { "error": "not_found" }
  }
}
```

### POST /reply/{report_id}

用户通过前端回复反馈，由 bot 账号代发评论到 GitHub Issue。

**请求体（JSON）：**
```json
{
  "token": "aBcDeFgHiJkL_1234567890ab",
  "body": "谢谢回复，问题在升级后已解决。"
}
```

**验证：**
- `token` 必须与提交时返回的 `feedback_token` 一致
- `body` 非空，最长 2000 字符
- 每条反馈每天最多 20 条用户回复（防滥用）

**处理逻辑：**
1. 验证 token 和频率限制
2. 读取 metadata 获取 `github_issue_url`，提取 issue number
3. 调用 GitHub API 发评论，格式为 `**[User Reply]**\n\n{用户内容}`
4. 将回复追加到 OSS metadata 的 `developer_replies`，带 `"source": "user_reply"` 标记

**响应（200）：**
```json
{
  "status": "ok",
  "comment_url": "https://github.com/openakita/openakita/issues/42#issuecomment-xxx"
}
```

**错误响应：**
- `400` — 未关联 GitHub Issue、reply body 为空或过长
- `401` — 缺少 token
- `403` — token 无效
- `429` — 当日回复次数已达上限（20 条）
- `502` — GitHub API 调用失败

### POST /webhook/github

接收 GitHub Webhook 事件（`issues` 和 `issue_comment`），自动更新 OSS 中的反馈元数据。

**验证方式**：HMAC-SHA256 签名（`X-Hub-Signature-256` 头）

**处理逻辑**：
- `issue_comment.created` — 提取评论内容追加到 `developer_replies`，触发用户邮件通知
  - 自动跳过 Bot 账号评论（`user.type == "Bot"` 或 `login` 以 `[bot]` 结尾）
  - 双重去重跳过用户代发回复：评论 body 以 `**[User Reply]**` 开头，或评论者匹配 `GITHUB_PAT_LOGIN`
- `issues.closed` — 更新状态为 `resolved`
- `issues.reopened` — 更新状态为 `open`
- `issues.labeled/unlabeled` — 同步 `status:xxx` 标签到 metadata

**响应（200）：**
```json
{
  "status": "ok",
  "report_id": "abc123def456",
  "updated": true
}
```

### GET /unsubscribe/{report_id}?token=xxx

用户点击邮件中的退订链接，关闭该反馈的邮件通知。

返回 HTML 页面确认退订成功。

### GET /health

健康检查。返回 `{ "status": "ok", "service": "feedback-fc" }`。

## 管理工具

使用 `scripts/feedback.py` 管理 OSS 上的反馈：

```bash
# 设置凭证（或创建 ~/.openakita/feedback.env）
export OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
export OSS_BUCKET=openakita-feedback
export OSS_ACCESS_KEY_ID=xxx
export OSS_ACCESS_KEY_SECRET=xxx

python scripts/feedback.py list               # 列出最近的反馈
python scripts/feedback.py download <id>       # 下载指定反馈的 ZIP
python scripts/feedback.py stats               # 按日期/类型统计
```
