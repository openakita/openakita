# API 集成验证项目

## 项目结构
```
api-integration/
├── adapters/          # API 适配器统一接口
├── examples/          # 各 API 使用示例
├── tests/             # 集成测试
├── docs/              # 技术文档
└── config/            # 配置文件
```

## 10 个 API 清单
1. 邮件发送 - 阿里云邮件推送
2. 短信通知 - 阿里云短信
3. 表格处理 - 飞书多维表格
4. 企业微信 - 消息推送
5. 钉钉 - 机器人 webhook
6. 飞书 - 开放平台 API
7. HTTP 请求 - 通用 HTTP 调用
8. OSS 存储 - 阿里云 OSS
9. 数据库 - PostgreSQL
10. 日历 - Google Calendar
11. CRM - Salesforce

## 技术栈
- Python 3.9+
- requests (HTTP 客户端)
- psycopg2 (PostgreSQL)
- oss2 (阿里云 OSS)

## 快速开始
```bash
pip install requests psycopg2-binary oss2
python examples/email_demo.py
```
