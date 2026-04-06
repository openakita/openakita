# MVP API 集成示例代码

**说明**: 本目录包含 10 个常用 API 的 Python 集成示例代码

## 目录结构

```
examples/
├── config.py              # 配置文件（API Key 等敏感信息）
├── 01_claude_api.py       # 大模型 API (Claude)
├── 02_wechat_work_api.py  # 企业微信 API
├── 03_dingtalk_api.py     # 钉钉 API
├── 04_feishu_api.py       # 飞书 API
├── 05_qdrant_api.py       # 向量数据库 API (Qdrant)
├── 06_sendgrid_api.py     # 邮件服务 API (SendGrid)
├── 07_aliyun_oss_api.py   # 对象存储 API (阿里云 OSS)
├── 08_aliyun_sms_api.py   # 短信 API (阿里云短信)
├── 09_github_oauth_api.py # OAuth 认证 API (GitHub)
└── 10_alipay_api.py       # 支付 API (支付宝)
```

## 使用方法

1. 复制 `config.example.py` 为 `config.py`
2. 填写各 API 的认证信息（API Key、Secret 等）
3. 运行示例：`python 01_claude_api.py`

## 注意事项

- ⚠️ `config.py` 包含敏感信息，请勿提交到 Git
- 各示例代码独立运行，互不依赖
- 示例代码包含错误处理和日志输出
- 生产环境请根据实际需求调整
