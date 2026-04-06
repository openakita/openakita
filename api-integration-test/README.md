# API 集成验证项目

Sprint 1 技术评审会批准的 10 个常用 API 集成验证项目。

## 验证的 API 清单

1. **邮件服务** (SMTP/SendGrid) - 发送通知邮件
2. **日历服务** (Google Calendar) - 日程管理
3. **表格服务** (Google Sheets) - 数据读写
4. **CRM 服务** (HubSpot) - 客户管理
5. **即时通讯** (钉钉/企业微信) - 消息推送
6. **云存储** (阿里云 OSS) - 文件上传下载
7. **HTTP Webhook** - 通用 HTTP 请求
8. **数据库** (PostgreSQL) - 数据持久化
9. **文档处理** (PDF 生成) - 报告生成
10. **短信服务** (阿里云短信) - 短信通知

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp config/.env.example config/.env
# 编辑 config/.env 填入实际 API 密钥

# 3. 运行验证测试
python src/run_all_tests.py

# 4. 查看测试报告
cat logs/test_report.md
```

## 项目结构

```
api-integration-test/
├── config/
│   └── .env.example      # 配置模板
├── src/
│   ├── __init__.py
│   ├── api_clients.py    # API 客户端封装
│   └── run_all_tests.py  # 测试执行入口
├── tests/
│   └── test_apis.py      # 测试用例
├── logs/                  # 测试日志和报告
├── requirements.txt
└── README.md
```

## 交付时间

- 开始日期：2026-03-13
- 截止日期：2026-03-25
- 里程碑：03-20 工作流架构评审会
