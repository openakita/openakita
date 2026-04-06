# MVP API 集成方案清单

**编制时间**: 2026-03-18  
**编制人**: 全栈工程师 A  
**用途**: MVP 阶段 API 集成预研

---

## 10 个常用 API 集成方案

| 序号 | API 类别 | 首选方案 | 备选方案 | 集成复杂度 | 优先级 | 使用场景 |
|------|----------|----------|----------|------------|--------|----------|
| 1 | **大模型 API** | Claude API (Anthropic) | 通义千问 API (阿里云) | 中 | P0 | AI Agent 核心能力、工作流智能节点 |
| 2 | **企业微信 API** | 企业微信自建应用 | 企业微信机器人 Webhook | 低 | P0 | 消息通知、审批提醒、机器人交互 |
| 3 | **钉钉 API** | 钉钉自建应用 | 钉钉机器人 Webhook | 低 | P0 | 消息通知、审批提醒、机器人交互 |
| 4 | **飞书 API** | 飞书自建应用 | 飞书机器人 Webhook | 低 | P1 | 消息通知、多维表格集成、机器人交互 |
| 5 | **向量数据库 API** | Qdrant | Pinecone / Milvus | 中 | P0 | 向量存储、语义检索、RAG 功能 |
| 6 | **邮件服务 API** | SendGrid | 阿里云邮件推送 | 低 | P1 | 用户注册验证、通知邮件、营销邮件 |
| 7 | **对象存储 API** | 阿里云 OSS | 腾讯云 COS / AWS S3 | 低 | P1 | 文件上传、工作流附件、静态资源 |
| 8 | **短信 API** | 阿里云短信 | 腾讯云短信 | 低 | P1 | 验证码、重要通知、双因素认证 |
| 9 | **OAuth 认证 API** | GitHub OAuth | 微信登录 / Google OAuth | 中 | P2 | 第三方登录、账号绑定 |
| 10 | **支付 API** | 支付宝 | 微信支付 | 高 | P2 | 订阅付费、按量计费 |

---

## 各 API 详细说明

### 1. 大模型 API (P0)
**首选**: Claude API (Anthropic)
- **文档**: https://docs.anthropic.com/claude/reference
- **认证方式**: API Key (Bearer Token)
- **计费**: 按 Token 计费 (输入$0.015/1K, 输出$0.075/1K)
- **集成要点**: 
  - 流式响应支持
  - 系统提示词配置
  - Token 计数与成本控制

**备选**: 通义千问 API (阿里云)
- **文档**: https://help.aliyun.com/zh/dashscope/
- **认证方式**: API Key + SDK
- **计费**: 按 Token 计费 (国产模型更便宜)
- **优势**: 国内访问速度快、合规性好

---

### 2. 企业微信 API (P0)
**首选**: 企业微信自建应用
- **文档**: https://developer.work.weixin.qq.com/document
- **认证方式**: CorpID + Secret 获取 Access Token
- **主要接口**:
  - 消息推送 (文本/卡片/Markdown)
  - 应用消息回调
  - 成员/部门管理
- **集成要点**:
  - Access Token 缓存 (2 小时有效期)
  - 消息签名验证
  - 回调 URL 配置

**备选**: 企业微信机器人 Webhook
- **文档**: https://developer.work.weixin.qq.com/document/path/91770
- **认证方式**: Webhook URL (含 key 参数)
- **优势**: 无需鉴权、快速集成
- **限制**: 仅支持群聊、功能有限

---

### 3. 钉钉 API (P0)
**首选**: 钉钉自建应用
- **文档**: https://open.dingtalk.com/document/
- **认证方式**: AppKey + AppSecret 获取 Access Token
- **主要接口**:
  - 工作通知消息
  - 互动卡片消息
  - 审批流程集成
- **集成要点**:
  - Access Token 缓存
  - 消息加密解密
  - 回调事件处理

**备选**: 钉钉机器人 Webhook
- **文档**: https://open.dingtalk.com/document/robots/custom-robot-access
- **认证方式**: Webhook URL + 签名 (可选)
- **优势**: 快速集成、无需复杂鉴权
- **限制**: 仅支持群聊

---

### 4. 飞书 API (P1)
**首选**: 飞书自建应用
- **文档**: https://open.feishu.cn/document/
- **认证方式**: App ID + App Secret 获取 Tenant Access Token
- **主要接口**:
  - 消息发送 (文本/富文本/卡片)
  - 多维表格操作
  - 日历/会议集成
- **集成要点**:
  - Token 刷新机制
  - 事件订阅回调
  - 卡片交互处理

---

### 5. 向量数据库 API (P0)
**首选**: Qdrant
- **文档**: https://qdrant.tech/documentation/
- **部署方式**: Docker / 云服务 / 本地
- **认证方式**: API Key (云服务) / 无 (本地)
- **主要操作**:
  - 向量 Upsert/Delete
  - 相似度搜索
  - 集合管理
- **集成要点**:
  - 向量维度配置 (通常 768/1536)
  - 距离度量选择 (Cosine/Euclidean)
  - 批量操作优化

**备选**: Pinecone
- **文档**: https://docs.pinecone.io/
- **优势**: 全托管服务、无需运维
- **劣势**: 成本较高、国内访问慢

---

### 6. 邮件服务 API (P1)
**首选**: SendGrid
- **文档**: https://docs.sendgrid.com/
- **认证方式**: API Key
- **主要功能**:
  - 单封邮件发送
  - 批量邮件发送
  - 模板邮件
- **集成要点**:
  - 发件人域名验证
  - 退订处理
  - 送达率监控

**备选**: 阿里云邮件推送
- **文档**: https://help.aliyun.com/product/29426.html
- **优势**: 国内到达率高、价格便宜
- **集成**: SMTP 协议 / HTTP API

---

### 7. 对象存储 API (P1)
**首选**: 阿里云 OSS
- **文档**: https://help.aliyun.com/product/31815.html
- **认证方式**: AccessKey ID + AccessKey Secret
- **主要操作**:
  - 文件上传/下载
  - 文件列表
  - 权限控制 (签名 URL)
- **集成要点**:
  - 分片上传 (大文件)
  - CDN 加速配置
  - 生命周期管理

---

### 8. 短信 API (P1)
**首选**: 阿里云短信
- **文档**: https://help.aliyun.com/product/44276.html
- **认证方式**: AccessKey + 签名
- **主要功能**:
  - 验证码短信
  - 通知短信
  - 国际短信
- **集成要点**:
  - 短信签名审核
  - 模板审核
  - 发送频率限制

---

### 9. OAuth 认证 API (P2)
**首选**: GitHub OAuth
- **文档**: https://docs.github.com/en/developers/apps/building-oauth-apps
- **认证流程**:
  1. 重定向到 GitHub 授权页
  2. 用户授权
  3. 换取 Access Token
  4. 获取用户信息
- **集成要点**:
  - 回调 URL 配置
  - State 参数防 CSRF
  - Token 存储与刷新

---

### 10. 支付 API (P2)
**首选**: 支付宝
- **文档**: https://opendocs.alipay.com/
- **认证方式**: AppID + 私钥签名
- **主要功能**:
  - 网页支付
  - APP 支付
  - 订阅扣款
- **集成要点**:
  - 签名验签
  - 异步通知处理
  - 退款流程

---

## 集成优先级说明

**P0 (立即集成)**: MVP 核心功能必需
- 大模型 API: AI Agent 核心能力
- 企业微信/钉钉: 主要通知渠道
- 向量数据库: RAG 功能基础

**P1 (Sprint 2-3 集成)**: 重要但可延后
- 飞书: 补充通知渠道
- 邮件/短信: 用户通知
- 对象存储: 文件管理

**P2 (V1.0 后集成)**: 商业化功能
- OAuth: 第三方登录
- 支付: 订阅收费

---

## 下一步

1. 调研各 API 详细文档
2. 编写 Python 示例代码
3. 模拟测试验证可行性
4. 输出集成指南文档
