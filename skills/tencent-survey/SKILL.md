---
name: openakita/skills@tencent-survey
description: "Tencent Survey (wj.qq.com) online survey platform skill. Supports survey creation, question management, answer collection, and data export. Use when user mentions surveys, questionnaires, forms, polls, exams, or assessments."
license: MIT
metadata:
  author: tencent-survey
  version: "1.0.2"
requires:
  env: [TENCENT_SURVEY_TOKEN]
---

# 腾讯问卷

腾讯问卷 MCP 提供问卷查询、创建、编辑与回答查看能力。

## 触发场景

用户提到「问卷」「调查」「表单」「投票」「考试」「测评」等关键词或提供 wj.qq.com 链接。

## 配置

### 方式一：环境变量
TENCENT_SURVEY_TOKEN=xxx bash setup.sh wj_check_and_start_auth

### 方式二：OAuth 设备授权
执行 setup.sh wj_check_and_start_auth，按提示完成授权。

Token 前缀固定为 wjpt_，长度 70 字符。

## 工具列表

| 工具 | 功能 |
|------|------|
| get_survey | 获取问卷详情 |
| create_survey | 创建问卷（支持调查/考试/测评/投票） |
| update_question | 更新问卷题目 |
| list_answers | 获取回答列表（游标分页） |

## URL 解析

wj.qq.com/s2/{survey_id}/{hash} → 取 survey_id 调用 get_survey。

## 预置脚本

### scripts/survey_auth.py
腾讯问卷认证辅助脚本，支持 TENCENT_SURVEY_TOKEN 环境变量或 OAuth 流程。

```bash
python3 scripts/survey_auth.py check
python3 scripts/survey_auth.py configure
python3 scripts/survey_auth.py status
```

