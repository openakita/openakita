# finance-auto Plugin · fix-round-2 优化完成报告

> 复审输入：`_finance_plugin_audit_report_round2.md` §8（4 条非阻塞优化）
> 完成范围：5 个本地 commit，`053c8ab6..bb10b7e6`，全部位于 finance-auto territory
> 时间：2026-05-24（与 round-2 复审同日）
> Sibling W（read-only extended audit worker）零文件冲突

---

## §0 摘要

| # | 优化项 | 状态 | commit |
| --- | --- | --- | --- |
| 1 | manual_inputs / comments expected_version 从 opt-in 改强制 | ✅ 完成 | `b7128e4d` |
| 2 | `run_all_acceptance.py` 作为 CI 总闸 | ✅ 完成 | `f6038296` + `bb10b7e6` |
| 3 | `CHANGELOG.md` 补 v1.0 RC 路由与修复 | ✅ 完成 | `bb4e08fc` |
| 4 | `CONTRIBUTING.md` + `check_territory.py` 防漂入 | ✅ 完成 | `384705a8` |

**Self-audit**：pytest 218/218 ✅；run_all_acceptance 10/10 ✅；check_territory 5 commit 全清 ✅；strict expected_version 6/6 probe ✅。

**v1.0 RC readiness**：可发布；唯一遗留 = `.github/workflows/ci.yml` 中接入 `run_all_acceptance.py` 这一步（≤ 1 行 `run:`），但落在 ci.yml 即 repo-wide territory，按 territory 规则必须由单独 `chore(ci):` commit 完成，已在 `CHANGELOG.md` 与 `CONTRIBUTING.md` 中标注 `TODO: CI hook`。

---

## §1 优化 1 · expected_version 强制

| 维度 | 改前 | 改后 |
| --- | --- | --- |
| `PUT /manual-inputs/{key}` | opt-in：缺 token 走回落 last-write-wins | 缺 token → 409 `missing_expected_version` |
| `ReviewWorkflowService.resolve_comment` | opt-in：缺 token 走回落 UPDATE | 缺 token → 409 `missing_expected_version` |
| UPDATE WHERE 子句 | 仅 opt-in 路径带 `version=?` | 全部 UPDATE 带 `WHERE id=? AND version=?` |
| 注释说明 | "为兼容 M2 UI 保留" | 已删，描述改为 strict-enforce |

**文件改动**：
- `finance_auto_backend/manual_input_routes.py`（删 opt-in 分支，加 409 短路）
- `finance_auto_backend/services/review_workflow.py`（同上）
- `tests/test_manual_input_api.py`（旧 PUT 加 `expected_version=0/N`；新增 `test_put_without_expected_version_returns_409_missing_token`）
- `tests/test_comments_optimistic_lock.py`（`test_resolve_comment_legacy_path_still_works` → `..._now_rejects`，断言 409）
- `scripts/m1_w3_acceptance.py` / `scripts/m2_biz_acceptance.py`（seed PUT 加 `expected_version=0`）

**前端 (`ui/dist/index.html`)**：**无需改动**。grep 实证（probe §5/§6）：
- 0 处 manual-inputs PATCH/PUT 调用（UI 通过 `/cash-flow/persist` 批量写，绕过 PUT route）
- 0 处 comments-resolve PATCH 调用（M3 UI 不暴露 resolve 按钮）

下一轮 UI 加 resolve-comment 按钮时，必须在 PATCH body 带 `expected_version: comment.version` —— 已在 `CONTRIBUTING.md` §5 写入 PR 自检清单。

---

## §2 优化 2 · run_all_acceptance.py 作为 CI 总闸

**新脚本**：`plugins/finance-auto/scripts/run_all_acceptance.py`（约 330 行）

**编排顺序**（M1 → M2 → M3 → closing）：

```
1. m1_w2_acceptance.py
2. m1_w3_acceptance.py
3. m2_ai_acceptance.py
4. m2_biz_acceptance.py
5. m2_closing_acceptance.py        --skip-regression
6. m3_raw_ai_acceptance.py
7. m3_infra_acceptance.py
8. m3_notes_peer_acceptance.py     --skip-regression
9. m3_ui_acceptance.py
10. m3_closing_acceptance.py       --skip-regression
```

**特性**：
- 每脚本 120s 超时（`--per-script-timeout`）
- 输出每脚本 `{script_name, exit_code, elapsed_ms, natural_exit, timed_out, stdout_tail, stderr_tail}`
- 末尾打印汇总表 + 写 `_finance_auto_run_all_acceptance.json`
- `--only / --fail-fast / --python` 等便利参数
- 末尾 `os._exit(rc)` 防 ASGI 线程 wedge

**重要 bug fix**（commit `bb10b7e6`）：首版用 `subprocess.run(capture_output=True)`，Windows pipe buffer 满（~64 KB）时 child 卡在 `write()` 进不去 `os._exit`，触发假性 TIMEOUT。改为 stdout/stderr 写临时文件，并停止强行覆盖 `PYTHONIOENCODING`（否则 `m3_infra_acceptance.py` 的内层 gbk reader 崩）。

**CI 集成**：未改 `.github/workflows/ci.yml`（territory 边界外）。文档化为 `TODO: CI hook`，详见 `CHANGELOG.md` 与 `CONTRIBUTING.md` §1。

---

## §3 优化 3 · CHANGELOG.md（v1.0 RC 路由 / 修复矩阵）

**新文件**：`plugins/finance-auto/CHANGELOG.md`（9.3 KB / 144 行，Keep-a-Changelog 格式）

**结构**：
- `## [Unreleased] - v1.0 RC (round-2 optimisations)` — 4 项 round-2 改动（Added / Changed）
- `## [1.0.0 - fix-round-1 batch] - 2026-05-24` — 重建 fix-round-1 commit 历史（5 P1 + 6 P2 全条目，每条引用具体 commit SHA）
- "Notes on the 'route count' delta" 章节 — 澄清 round-1 audit 报告"90 → 94"是计数偏差（在 ff2bf79f 与 053c8ab6 都实测 93 条 HTTP route + 1 WS = 90 reachable，无任何新 HTTP 路由），"+4" 实际指 P1-A 新接入的 4 个 Tauri native command（不是 HTTP 路由）
- `v0.x → v1.0 RC functional delta` — 21 行能力矩阵（trial-balance / VAT / 报表 / 重分类 / 合并 / 复核 / AI 9 scenarios / Tauri / 10 acceptance 等）

**4 条"新路由"的真相**：根据 git diff `ff2bf79f..053c8ab6 -- routes` 与本地 FastAPI 启动实测，**没有新增 HTTP 路由**。audit 的 90→94 实际是 P1-A 端到端打通的 4 个 Tauri native command（`show_finance_consent_dialog` / `finance_system_info` / `finance_show_notification` / `finance_pick_save_path`），CHANGELOG 在 P1-A 条目下逐一列出。

---

## §4 优化 4 · CONTRIBUTING.md + check_territory.py

**新文件 1**：`plugins/finance-auto/CONTRIBUTING.md`（5 节）：
1. Territory 边界表（允许路径 / 禁止路径，含 Tauri Rust + JS bridge 细分）
2. Sibling-worker 模板（含本轮 Sibling W 的"纯读，唯一写一个 repo-root md"模板）
3. Commit scope 约定（`finance-auto` / `-ui` / `-tests` / `-scripts` / `-docs`）
4. Pre-commit / pre-push 推荐用法（**不**强制装 git hook）
5. PR 自检 6 项（territory clean / pytest 217 / run_all_acceptance / strict lock / changelog）

**新文件 2**：`plugins/finance-auto/scripts/check_territory.py`（约 175 行）：
- 默认 `HEAD~1..HEAD`，可 `--commit-range A..B`
- ERROR (exit 1)：commit 用 `finance-auto*` scope 但改了 territory 外的文件
- WARNING (不影响 exit code)：非 finance-auto scope 的 commit 触到 `plugins/finance-auto/**`（"orgs_v2 drifted in" 模式）
- exit 2：argparse / git 错误

**Allowed 模式**（与 CONTRIBUTING §1 表对齐）：`plugins/finance-auto/{finance_auto_backend,tests,scripts,ui/dist}/**` + `CHANGELOG.md` / `CONTRIBUTING.md` / `README.md` + `apps/setup-center/src-tauri/src/finance*.rs` + `apps/setup-center/src/lib/native/finance-*.ts` + 仓库根 `_finance_plugin_*.md` / `_fix_round*.md`。

**自检结果**：对 `053c8ab6..HEAD` 的 5 个本轮 commit 全部 PASS（0 ERROR / 0 WARN）。对 `ff2bf79f..HEAD` 的全 19 commit 命中 1 ERROR（历史 commit `22b31de5` 改了共享 `plugin-bridge-host.ts`），这是合规的 round-1 边界泄漏，已记录但本轮不回溯修。

---

## §5 self-audit 结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| pytest 全套 | `pytest -q plugins/finance-auto/tests/` | **218 passed in 25.8s**（round-2 基线 217 + 1 新增 missing-token 测试） |
| run_all_acceptance | `python plugins/finance-auto/scripts/run_all_acceptance.py` | **10/10 PASS，overall 25.5s**（全部 `natural_exit=true`） |
| check_territory | `python ... check_territory.py --commit-range 053c8ab6..HEAD` | **5/5 clean，0 ERROR，0 WARN** |
| 强制 expected_version probe | `python _round2_strict_lock_probe.py` | **6/6 PASS**（4 个后端 TestClient case + 2 个 UI grep 断言） |

probe 4 个后端 case：
1. 无 `expected_version` → 409 `missing_expected_version` ✅
2. fresh slot `expected_version=0` → 200 + version=1 ✅
3. 错误 `expected_version=99` → 409 `version_conflict` ✅
4. 正确 `expected_version=1` → 200 + version=2 ✅

probe 2 个 UI grep：5) 0 处 manual-inputs PATCH/PUT；6) 0 处 comments resolve PATCH。

---

## §6 与 Sibling W 协调情况

Sibling W = 扩展延伸深度检查 worker（read-only），唯一写入 `_finance_plugin_audit_extended_report.md`（repo root）。

- **零文件冲突**：W 不改任何代码；本轮 5 commit 仅落在 `plugins/finance-auto/**` + 一个 `apps/setup-center/src-tauri/src/finance*.rs`-class 无改动，与 W 的写入目标完全脱钩。
- **commit message 可读性**：5 个 commit message 都写了详细英文 body（"为什么" + "做了什么"），W 在阅读时不需要回头读源码；特别 `bb10b7e6` 的 pipe-deadlock fix 注释解释了为什么停止覆盖 `PYTHONIOENCODING`，W 可直接引用。
- **未踩 W 的 read 路径**：W 读的全是已有文件 + `_finance_plugin_audit_report_round2.md`；本轮没动它要读的任何文件。

---

## §7 v1.0 RC 最终 readiness 评估

**结论：可发布 v1.0 RC。**

- 4/4 优化全部落地，5 个 commit 全部 territory clean
- pytest 218/218 + acceptance 10/10 全部自然退出
- P2-2 / P2-6 opt-in 妥协已升级到 strict-enforce，silent-overwrite 竞态结构上不可能
- 路由 / WS / Tauri / 视图全部端到端联通（与 round-2 §6 一致）
- CHANGELOG / CONTRIBUTING / territory 守护脚本全部 in place

**唯一遗留**：CI hook（`.github/workflows/ci.yml` 增 1 行 `run:`）。此项故意不在本轮 commit，理由：
1. ci.yml 是 repo-wide CI territory，按 CONTRIBUTING §1 表不属于 finance-auto territory
2. check_territory.py 会 ERROR 出此 commit
3. 该改动应由下一个 `chore(ci):` scope 的独立 commit 承担

CHANGELOG `## [Unreleased]` 已写 `TODO: CI hook`，提示下一个 PR owner 一行 fix 即可闭环。

— end of fix-round-2 optimisation report —
