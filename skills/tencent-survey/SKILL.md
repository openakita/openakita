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

# Tencent Survey

Tencent Survey MCP provides capabilities for survey querying, creation, editing, and response viewing.

## Trigger Scenarios

Use when the user mentions "survey", "questionnaire", "form", "poll", "exam", "assessment" keywords, or provides a wj.qq.com link.

## Configuration

### Method 1: Environment Variable
```bash
TENCENT_SURVEY_TOKEN=xxx bash setup.sh wj_check_and_start_auth
```

### Method 2: OAuth Device Authorization
Run `setup.sh wj_check_and_start_auth` and follow the prompts to complete authorization.

The token prefix is always `wjpt_`, with a length of 70 characters.

## Tool List

| Tool | Function |
|------|------|
| get_survey | Get survey details |
| create_survey | Create a survey (supports surveys/exams/assessments/polls) |
| update_question | Update survey questions |
| list_answers | Get response list (cursor-based pagination) |

## URL Parsing

`wj.qq.com/s2/{survey_id}/{hash}` → Extract `survey_id` and call `get_survey`.

## Pre-built Scripts

### scripts/survey_auth.py
Tencent Survey authentication helper script. Supports `TENCENT_SURVEY_TOKEN` environment variable or OAuth flow.

```bash
python3 scripts/survey_auth.py check
python3 scripts/survey_auth.py configure
python3 scripts/survey_auth.py status
```
