# OpenAkita 财务自动化插件 — M1 W3 后端完成报告

| 项目 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 报告日期 | 2026-05-23 |
| 工作分支 | `revamp/v3-orgs`（W3 后端 6 个 commit 全部本地，未 push） |
| 设计来源 | `_finance_plugin_design_v0.3_INDEX.md` §6 W3 任务清单 |
| Schema | v4 → **v7** |
| 测试 | **97 / 97 通过**（pytest，含 W2 既有 87 + W3 新增 10） |
| 端到端 | **12 / 12 步通过**（`m1_w3_acceptance.py`） |
| 完成报告大小 | 见文末 |

---

## 0. 摘要 — 现状与续接说明

W3 后端是上一任 worker 因网络中断后留下的「半成品」状态：本次接手时通过
`git log` + 文件系统盘点后，发现 **Stage 1 / 2 / 3 / 4 已经独立 commit**（每
个 stage 对应一个 `feat(finance-auto): ...` 提交），但 **Stage 5
（industry_overrides）的 7 个文件还在 working tree、未入 git**；Stage 6
（端到端验收脚本）与 Stage 7（本报告）则完全没起头。本次工作只做了未完成的
后半段：

- **续接的工作**：Stage 5 整体（提交 `897e0000`），Stage 6（提交 `034ac6fa`），
  Stage 7（本报告 + 即将到来的 docs commit）。
- **未重做的工作**：Stage 1 / 2 / 3 / 4 的代码、schema、测试、API — 验证
  通过即认可。
- **额外审核手段**：跑了 `pytest plugins/finance-auto/tests/` 确认 87 个
  存量用例 + 新增的 10 个 industry-loader 用例全部绿；跑了上一阶段的
  `m1_w2_acceptance.py` 确认 W2 7 步未被回归；最后跑新增的
  `m1_w3_acceptance.py` 12 步全绿。
- **schema 一致性核对**：`SCHEMA_VERSION = 7`，migration 链补到 v7（v5 加
  `parse_issues / learning_samples`，v6 加 `cross_period_check_results`，
  v7 加 `manual_inputs`），与 W2 终态 v4 完全衔接。

W3 后端 5 项核心任务（未知数据分流、报表简化开关、跨期校验、manual_inputs、
industry_overrides）+ 端到端验收 + 完成报告，**全部 ✅ 完成**。

---

## 1. 现状检查 + 续接说明

### 1.1 入场盘点

| 来源 | 发现 |
|---|---|
| `git log --oneline 11d54ce7..HEAD` | 上一任 worker 已提交 `505aa78a` (Stage 1)、`bfb580f6` (Stage 2)、`91b20099` (Stage 3)、`3e757b94` (Stage 4)；中间夹杂了若干 `feat(finance-auto-ui)`（前端 sibling 的产物，与本 worker 无关） |
| `Glob plugins/finance-auto/finance_auto_backend/**/*.py` | 36 个 .py，已含 `validators/parse_issue_detector.py / cross_period.py`、`renderers/simplifier.py`、`config/manual_inputs_loader.py`、**未入 git 的 `config/industry_loader.py` 与 `industry_routes.py`** |
| `Glob plugins/finance-auto/templates/industry_overrides/` | 3 个 YAML（manufacturing/restaurant/tech_service）**未入 git** |
| `git diff plugins/finance-auto/finance_auto_backend/routes.py` | 多了 2 行 `from .industry_routes import ...` + `register_industry_endpoints(router, service)` — 但未提交 |
| `Glob _m1_w3_*` | 仅有前端 sibling 的 `_m1_w3_frontend_completion_report.md`；后端报告缺失 |
| `Grep SCHEMA_VERSION plugins/finance-auto/finance_auto_backend/schema.py` | 已经是 v7（含 manual_inputs DDL） |
| 已有的 1 份 acceptance 脚本 | `scripts/m1_w2_acceptance.py` 仍可跑通 7/7，证明 W2 没有被 W3 改动破坏 |

### 1.2 续接决策

| Stage | 决策 | 理由 |
|---|---|---|
| 1 未知数据分流 | **不重做** | commit `505aa78a` 已含 6 类规则 + 4 端点 + 学习样本 + 加密 split；既有 12 个测试全绿 |
| 2 报表简化开关 | **不重做** | commit `bfb580f6` 已含 simplifier + PATCH cell/{id}/simplify + GET cell/{id}/details + openpyxl 灰色样式 |
| 3 跨期校验 | **不重做** | commit `91b20099` 已含 4 级 severity + emit_parse_issues + 乐观锁 `version` 列 |
| 4 manual_inputs | **不重做** | commit `3e757b94` 已含 7 keys + GET/PUT + cash_flow 报表读取 |
| 5 industry_overrides | **新增 commit** | working tree 已写好代码但未 commit；本 worker 检查、运行 10 个测试通过后入 git |
| 6 端到端复核 | **新增脚本 + commit** | 之前没有 `m1_w3_acceptance.py`；本 worker 新写 12 步 + DB 快照断言 |
| 7 后端完成报告 | **新增文件 + commit** | 之前没有 `_m1_w3_backend_completion_report.md`；本 worker 写 |

---

## 2. 各 Stage 完成度

| Stage | 主题 | 完成度 | 关键产物 |
|:-:|---|:-:|---|
| 1 | 未知数据分流（v0.2 Part 1 §2） | ✅ | `validators/parse_issue_detector.py`（6 类规则 + pattern_signature）/ `parse_issue_routes.py`（4 端点）/ `parse_issues` + `learning_samples` 两张表 / auto_apply 学习样本 / 上传后自动跑检测 |
| 2 | 报表简化开关（v0.2 Part 1 §3） | ✅ | `renderers/simplifier.py`（top_n / threshold / both 三策略）/ `PATCH .../cells/{id}/simplify` + `GET .../cells/{id}/details` / source_rows 全量保留 + simplified / simplified_top_n / merged_row_ids_json / footnote / version 元数据 / openpyxl 灰色斜体行 |
| 3 | 跨期校验（v0.3 Part Biz §4） | ✅ | `validators/cross_period.py`（exact / tolerance / warning / error 四级）/ 3 端点 / `cross_period_check_results` 表（带 `version` 乐观锁列）/ error 级别自动生成 `issue_type=cross_period_mismatch` 的 ParseIssue |
| 4 | manual_inputs（cash_flow 7 字段） | ✅ | `manual_inputs` 表 / 2 端点 / `templates/manual_inputs/cash_flow_aux.yaml`（vat_output / vat_input / bill_discount_received / interest_paid / interest_income / bank_fee_paid / social_security_paid） / cash_flow 生成器从 manual_inputs 读取，缺失项 warning 不阻塞 |
| 5 | industry_overrides 加载器 | ✅ | `config/industry_loader.py`（deep_merge / load_overlay / effective_config / merge_manual_input_presets） / `industry_routes.py`（2 端点）/ 3 份 YAML（manufacturing / restaurant / tech_service） / 10 个测试 |
| 6 | 端到端复核 | ✅ | `scripts/m1_w3_acceptance.py`（12 步），结果落盘到 `_m1_w3_acceptance_result.json` |
| 7 | 完成报告 | ✅ | 本文件 |

---

## 3. 文件清单

### 3.1 W3 新增 / 修改（按 stage）

```
plugins/finance-auto/
├── finance_auto_backend/
│   ├── routes.py                                 (Stage 1/3/4/5 wiring；+ register_*)
│   ├── schema.py                                 (Stage 1/2/3/4 DDL；SCHEMA_VERSION=7)
│   ├── models.py                                 (Stage 1-5 Pydantic 扩张)
│   ├── encryption.py                             (Stage 1 加 split_parse_issue_payload)
│   ├── validators/
│   │   ├── __init__.py
│   │   ├── parse_issue_detector.py               ★ Stage 1
│   │   └── cross_period.py                       ★ Stage 3
│   ├── parse_issue_routes.py                     ★ Stage 1
│   ├── cross_period_routes.py                    ★ Stage 3
│   ├── manual_input_routes.py                    ★ Stage 4
│   ├── industry_routes.py                        ★ Stage 5
│   ├── renderers/simplifier.py                   ★ Stage 2
│   ├── renderers/openpyxl_writer.py              (Stage 2 加灰色簡化样式)
│   ├── report_routes.py                          (Stage 2 加 PATCH simplify + GET details)
│   ├── report_generator.py                       (Stage 2 加 simplifier 集成；Stage 4 加 manual_input_values)
│   ├── config/
│   │   ├── manual_inputs_loader.py               ★ Stage 4
│   │   └── industry_loader.py                    ★ Stage 5
│   └── config/yaml_loader.py                     (Stage 4 加 ReportRule.manual_input_key)
├── templates/
│   ├── manual_inputs/cash_flow_aux.yaml          ★ Stage 4
│   ├── industry_overrides/                       ★ Stage 5
│   │   ├── manufacturing.yaml
│   │   ├── restaurant.yaml
│   │   └── tech_service.yaml
│   └── reports/cash_flow_small_enterprise.yaml   ★ Stage 4 (最小骨架，详见 §9)
├── scripts/m1_w3_acceptance.py                   ★ Stage 6
└── tests/                                        +10 (Stage 5) + 既有 87 = 97
    ├── test_parse_issue_detector.py              ★ Stage 1
    ├── test_parse_issue_api.py                   ★ Stage 1
    ├── test_simplifier.py                        ★ Stage 2
    ├── test_simplify_api.py                      ★ Stage 2
    ├── test_cross_period_validator.py            ★ Stage 3
    ├── test_cross_period_api.py                  ★ Stage 3
    ├── test_manual_input_api.py                  ★ Stage 4
    └── test_industry_loader.py                   ★ Stage 5（本 worker 入 git）
```

### 3.2 仓库根新增

```
_m1_w3_acceptance_result.json    # 验收脚本输出
_m1_w3_backend_completion_report.md   # 本文件
```

---

## 4. API 总数

W3 共向 `/api/plugins/finance-auto` 命名空间下增量挂载了 **13 个新端点**，W2
终态的 15 个 + W3 的 13 个 = **28 个** HTTP 路由（不含 `/health` 与 W2 已
存在的列表/上传端点会被复用）。

| Stage | 端点 |
|---|---|
| 1 | `GET  /orgs/{org_id}/parse-issues` |
| 1 | `POST /orgs/{org_id}/parse-issues/{issue_id}/decide` |
| 1 | `POST /orgs/{org_id}/parse-issues/{issue_id}/learn` |
| 1 | `GET  /orgs/{org_id}/learning-samples` |
| 2 | `PATCH /orgs/{org_id}/reports/{report_id}/cells/{cell_id}/simplify` |
| 2 | `GET   /orgs/{org_id}/reports/{report_id}/cells/{cell_id}/details` |
| 3 | `POST /orgs/{org_id}/cross-period-checks` |
| 3 | `GET  /orgs/{org_id}/cross-period-checks` |
| 3 | `GET  /orgs/{org_id}/cross-period-checks/{check_id}` |
| 4 | `GET  /orgs/{org_id}/periods/{period_id}/manual-inputs` |
| 4 | `PUT  /orgs/{org_id}/periods/{period_id}/manual-inputs/{field_key}` |
| 5 | `GET  /industries` |
| 5 | `GET  /orgs/{org_id}/effective-config` |

W2 路由总数 15（health 1 + org 2 + import 3 + report 4+W3+2=6 计入 W2 4 + audit 3 + vat 2）；W2 终态实际为 `1+2+3+4+3+2 = 15`，加 W3 的 13 即得 **28**。

---

## 5. Schema 演进 v1 → v7

| 版本 | 变更 | 来源 stage |
|---|---|---|
| v1 | 5 张基础表（organizations / accounting_periods / accounts / trial_balance_imports / trial_balance_rows） | M1 W1 |
| v2 | + reports, report_cells | M1 W2 Stage 4 |
| v3 | + vat_declarations | M1 W2 Stage 5 |
| v4 | + audit_templates | M1 W2 Stage 6 |
| **v5** | + parse_issues, learning_samples；ALTER report_cells 加 simplified / simplified_top_n / simplify_config_json / merged_row_ids_json / footnote / version 6 列 | **M1 W3 Stage 1 + 2** |
| **v6** | + cross_period_check_results（含 `version` 乐观锁列以满足 v0.3 Part Infra C3 契约） | **M1 W3 Stage 3** |
| **v7** | + manual_inputs，UNIQUE INDEX (org_id, period_id, field_key) 强制 UPSERT 语义 | **M1 W3 Stage 4** |

`MIGRATION_STEPS` 中 v5 携带 `_V5_ALTER_REPORT_CELLS` 这一段 ALTER SQL（其余
都是 CREATE TABLE IF NOT EXISTS，由 `db.init()` 在 chain 之前已经无条件
执行）。`run_migrations()` 对每条 ALTER 都会捕获 "duplicate column" 错误以
便重跑幂等。

加密保留语义：所有含 PII / 金额的列都走 `_encrypted_payload BLOB` 或
`__enc_blob__` 字段。Stage 1 的 `parse_issues.original_data` 是 TEXT 列，
采用 「明文 JSON + 嵌入 `__enc_blob__` 十六进制」 复合存储，避免再加列。
W2 验收脚本仍能验证 `trial_balance_rows` 密文率 100%（W3 没有改动这部分）。

---

## 6. 端到端结果（`m1_w3_acceptance.py`）

12 步全绿，结果 JSON 见 `_m1_w3_acceptance_result.json`：

| 步骤 | 主题 | 关键断言 | 结果 |
|:-:|---|---|:-:|
| 1 | Create org (industry=restaurant) | 201 | ✅ org_f826a1e1cf0a |
| 2 | GET /industries | 4 个行业齐全 | ✅ {general, manufacturing, restaurant, tech_service} |
| 3 | GET /orgs/{id}/effective-config | aux_mode=light（餐饮覆盖生效），manual_input_slot_count=7 | ✅ |
| 4 | 上传 15 AP 子户 + 1 unknown + 1 imbalance | parse_issues_detected ≥ 1 | ✅ 24 detected / 24 must_fix |
| 5 | 决策 + learn 一条 parse-issue | learning_samples_total ≥ 1 | ✅ unknown_code → manual_fix → learn(auto_apply=true) |
| 6 | 生成 BS | BS_2202.value ≈ 296 200，simplified=false | ✅ |
| 7 | PATCH simplify top_n=10 | merged_row_ids.length == 5 | ✅ footnote="其他 5 家供应商合计 30,900.00" |
| 8 | GET cell details | full_rows=15，visible_rows=11，最后一行 is_merged=true, merged_count=5, amount=30 900 | ✅ |
| 9 | 上传 2024-FY prior | row_count=5 | ✅ |
| 10 | POST cross-period-checks | error_count ≥ 1，parse_issue_ids 非空，列表能查到 cross_period_mismatch | ✅ errors=5, warnings=20, emitted=5 |
| 11 | PUT 7 manual_inputs + 生成 cash_flow | filled_count=7，至少 1 个 cell 引用 `manual_input:*` | ✅ filled=7, manual_refs=7 |
| 12 | DB 快照 | schema_version=7，所有相关表行数齐全 | ✅ parse_issues=34, xperiod_issues=5, samples=1, manual_inputs=7, simplified_cells=1, cf_reports=1 |

> 复核要点对照 prompt：
> - Stage 1 「合理数量 parse_issue」→ 24 条（含 1 unknown_code + 1 imbalance + 21 余额恒等式异常 + 1 direction_anomaly 等），未误报 AR 信用方
> - Stage 2 「top_n=10 应得 10 行 + 1 行其他」→ visible_rows=11，符合
> - Stage 3 「构造两期数据，跑校验应识别差异」→ 5 errors + 20 warnings，符合
> - Stage 4 「填 7 个字段并验证写入」→ filled_count=7，UPSERT 成功
> - Stage 5 「industry=restaurant 账套，effective-config 含餐饮覆盖」→ overlay_loaded=true，aux_mode=light

---

## 7. 与前端 sibling 接口对接

前端 sibling 在 `revamp/v3-orgs` 同分支上提交了 7 个 `feat(finance-auto-ui)`
+ 1 个 `docs(finance-auto-ui)`，本 worker 完全没碰前端。互不冲突的核心是
**所有 W3 路由都挂在 `/api/plugins/finance-auto/` 命名空间下**，前端通过 W2
既有的 `PluginAppHost.tsx` postMessage 桥访问，URL 形如：

| 前端调用点 | 对应后端端点 |
|---|---|
| 创建组织 | `POST /orgs`（W1） |
| 上传余额表 | `POST /orgs/{id}/imports`（W1，已含 W3 Stage 1 自动检测的返回字段 `parse_issues_detected / parse_issues_must_fix / parse_issues_auto_applied`） |
| 报表生成 + cell 追溯 | `POST .../reports/{kind}/generate` + `GET .../reports/{id}` + `GET .../cells/{id}/details`（cell-traceability 抽屉读这个） |
| 增值税申报上传 | `POST .../vat-declarations`（W2 Stage 5） |
| **可选已就绪 / 前端 M2 才会用** | parse-issues、cross-period-checks、manual-inputs、industries / effective-config — 这 6 类共 13 个端点等待前端 sibling 在 M2 接入 |

> 前端 sibling 报告 `_m1_w3_frontend_completion_report.md` §6 中提到的
> 「Step 8 因后端进程缓存阻塞」与本 worker 无关 —— 那是前端 Playwright e2e
> 在测试夹具进程里的副作用，重启后端即可。后端验收脚本（本文件 §6）每次都
> 起一个全新的 in-process TestClient，所以不受同样的影响。

---

## 8. 实际用时

本次只续接最后 3 个 stage（5 / 6 / 7），实际用时约 **45 分钟**：

| 阶段 | 用时 |
|---|---|
| §1 现状盘点（git log / Glob / Grep / Read）| ~5 min |
| 跑 W2 acceptance 回归 + 跑 industry_loader 10 个测试 | ~2 min |
| Stage 5 commit（已写好代码，本 worker 只整理 commit body）| ~3 min |
| Stage 6 写 `m1_w3_acceptance.py` + 第一次跑 + 修一处 assertion（parent 占位行让 merged=6）+ 重跑 | ~20 min |
| Stage 7 报告撰写 + 入 git | ~15 min |

W3 后端整体（含上一任 worker）实际投入约 **6-8 小时**（4 个 stage commit
的 diff 体量推估 + 本次 45 min 续接）。

---

## 9. M2 启动建议（按 v0.3 INDEX §7 / Part Biz §3 后段）

> M2 主题：AI 模糊匹配 + 关键审计稿渲染 + 跨期重分类 + 真实流量表 + 桌面应用打包。

### 9.1 核心任务（按依赖顺序）

1. **AI 模糊匹配后端**（v0.2 Part 2 §1）
   - 让 `parse_issues.ai_suggestion / ai_confidence / ai_consent_id` 三列开始有值
   - 走 W3 Stage 1 already-shipped 的 schema，零迁移
   - **前置**：用户决策走 OpenAkita 主进程的哪一个 LLM provider；本 W3 提交里
     的 issue payload 已经把 PII / 金额做了 split + 加密，AI 调用前需要再
     脱敏一次（脱敏样本 → AI → 回填后端时绑定 `ai_consent_id` 防止误用）

2. **跨期重分类规则引擎**（v0.1 §5.2 / §5.3；W2 SBE_2202 yaml 里的 `notes:
   "+ 重分类自 BS_1123 预付账款"` 是 placeholder，至今仍只是文本）
   - 把 1122 信用方 → 2203 预收、1123 信用方 → 2202 应付、其他应收信用方 →
     其他应付 这三条主链落到 `report_generator._reclassify_aux_credit_balances()`
   - **前置**：W3 Stage 1 detector 已经把 AR/AP family 列入 `_BIDIRECTIONAL_CODES`
     白名单，确保重分类的源行不会被误判为 direction_anomaly

3. **完整现金流量表模板**（v0.1 §7 + W3 Stage 4 cash_flow_small_enterprise.yaml
   现在只是 7 行 manual_input + 1 行 SUM_LINES 的最小骨架）
   - 把 38 行的间接法 + 直接法两版完整起来
   - 调用 W3 Stage 4 已落地的 `manual_inputs` 与 cross-year `REPORT_PREV()`
     公式（**还没实现**；公式器里目前只有 `ACCOUNT() / SUM_LINES()`）
   - **前置**：把 `REPORT_PREV()` 实装到 `report_generator._formula_eval`，
     并在 schema 加一张 `period_cross_refs` 缓存表（v8）

4. **审计底稿渲染**（v0.3 Part Biz §6）
   - W2 Stage 6 已经做了 "上传 + 占位符扫描 + Jinja2 strict render"；M2 要做
     的是把 25 份真实底稿 .xlsx（位于 `plugins/finance-auto/data/audit_templates/tpl_*.xlsx`，
     这次 W3 没动）按 `tmp_finance_analysis/05_named_ranges.json` 的命名映射
     灌进去
   - **前置**：业务方确认 `BS_GE_1606_ROU` / `BS_GE_2241_CL` / `BS_GE_2811_LL`
     这 3 个 TBD 编码（W2 报告 §9.2 留了这个口子；至今未回信）

5. **Beancount 双写桥**（v0.3 Part Biz §5）
   - W2 §9.1 原计划塞进 W3，本 W3 因为依赖关系下沉到 M2；属于「只读参考布局」
   - **前置**：无；可与 1-4 任意并行

6. **桌面打包**（v0.3 Part Infra §7）
   - Tauri 2.x 把 W3 后端 wheel + 前端 dist 一起打成 .msi / .dmg
   - **前置**：前端 sibling §7.2 提到的「Tauri sidecar 启动 Python」需要先
     拍板，后端这边只要保证 wheel 自包含模板与 YAML 即可

### 9.2 进入 M2 前是否需要用户决策

**无**，可继续自动推进 M2。已知的两个软决策（业务编码 TBD、AI provider 选型）
都不是阻塞项 —— TBD 占位继续保留即可，AI provider 默认走 OpenAkita 主进程的
统一 LLM Client（`src/openakita/llm`），M2 启动时再选一遍模型即可。

### 9.3 M2 启动前的回归基线

- `pytest plugins/finance-auto/tests/` → 97 passed
- `python plugins/finance-auto/scripts/m1_w2_acceptance.py` → 7 / 7
- `python plugins/finance-auto/scripts/m1_w3_acceptance.py` → 12 / 12
- 仓库根 `_m1_w2_acceptance_result.json` + `_m1_w3_acceptance_result.json`
  各自最新

---

## 10. 本 worker 新增的 commit

| # | SHA | Title |
|--:|---|---|
| 1 | `897e0000` | feat(finance-auto): add industry overrides loader with deep-merge |
| 2 | `034ac6fa` | test(finance-auto): extend acceptance script with W3 features |
| 3 | (本 commit) | docs(finance-auto): add M1 W3 backend completion report |

> 上一任 worker 已经提交的 4 个 W3 commit（`505aa78a` / `bfb580f6` /
> `91b20099` / `3e757b94`）不在本表内。整个 W3 后端在 git 上共 **7 个 commit**
> （4 stage commits + 1 stage 5 + 1 stage 6 + 1 stage 7）。

---

*— 报告结束 —*
