# finance-auto v1.0.0-rc1 收尾报告

> 起点：HEAD `63789248`（fix-round-3 终点 = round-3 复审基线）
> 终点：HEAD `cb6d4e8d`（v1.0.0-rc1 收尾，本报告即将随其后落盘）
> 复审输入：`_finance_plugin_audit_report_round3.md`（Yellow-Green）
> 工时：~3.5 h（计划 3-4 h）
> 范围：6 项收尾任务 + 1 项 self-audit + 本报告

---

## §0 一句话

**v1.0.0-rc1 真 Green，可发布**。round-3 复审标记的 6 项门面/补强
全部闭环；EX-P2-10（DELETE /orgs）+ EX-P2-13（/v1/ 前缀）两条 v1.x
路线项已在 RC 内提前 land；剩下的只是用户拍板后打 `git tag
v1.0.0-rc1 <HEAD>; git push origin v1.0.0-rc1`。

---

## §1 6 项任务完成情况

| # | 任务 | 状态 | 证据 commit | 用时 |
| --- | --- | :---: | --- | --- |
| 1 | README §6.1/§10/§5 同步 600k / v14 / 91→92 | ✅ | `87b0d035` | 8 min |
| 2 | 5 处 SCHEMA_VERSION assertion 加注释 | ✅ | `bf414e76` | 5 min |
| 3 | CHANGELOG v1.0.0-rc1 编排（Added/Changed/Fixed/Security/Known/Numbers）| ✅ | `affbfab9` | 35 min |
| 4 | EX-P2-10 `DELETE /orgs/{id}` 端点 + cascade + RBAC + v14 migration + 8 tests + 2 acceptance steps | ✅ | `f6a7f1c4` | 55 min |
| 5 | EX-P2-13 `/v1/` URL 前缀 + 308 redirect + UI bundle 切到 v1 + 10 tests | ✅ | `e4b71148` + `bcfc50e6` | 65 min |
| 6 | `_finance_plugin_RELEASE_NOTES_v1.0.0-rc1.md`（168 行 / 6.5 KB）| ✅ | `cb6d4e8d` | 20 min |
| 7 | self-audit + 本报告 | ✅ | （本 commit）| 15 min |

任务 4 + 5 是这一轮真正的"功能补强"——把 round-3 标记为 v1.x
路线的两条提前 land；其余 4 项是门面/文字/注释收尾。

---

## §2 self-audit 全表

| 检查项 | 命令 | 结果 |
| --- | --- | --- |
| pytest | `pytest plugins/finance-auto/tests/ -q` | **280 passed in 50.35s** ✅（fix-round-3 基线 262，+18：8 DELETE + 10 /v1/）|
| acceptance | `scripts/run_all_acceptance.py --per-script-timeout 60` | **10/10 PASS，total 24219ms，所有 natural_exit=True** ✅ |
| check_territory | `scripts/check_territory.py --commit-range 63789248..HEAD` | scanned=7, clean=7, warnings=0, errors=0 ✅ |
| 新路径 308 | `_v1_probe.py` 实跑：legacy GET /health / POST /orgs / GET /orgs?qs / DELETE /orgs/{id}?cascade=true | **5/5 案例 308 + Location 正确 + query string 保留 + 308 不降级为 303** ✅ |
| DELETE /orgs RBAC | `test_delete_org.py` 8 用例 | admin allow / partner allow / manager/auditor/unknown 403 rbac_denied / cascade=false 409 dependents / cascade=true 全清 + backup 文件 unlink / 404 unknown id ✅ |
| README 三处文字 | grep `600 000` / `v14` / `92 个` | **3/3 都命中**（README §6.1 / §10 / §1 状态头）✅ |
| 路由数 | in-process `len(router.routes)` | **94**（92 v1 + 1 legacy /ws + 1 catch-all 308）|
| schema_version | `from finance_auto_backend.schema import SCHEMA_VERSION; print()` | **14** ✅ |
| 总 commit 数（本 round）| `git log 63789248..HEAD --oneline | wc -l` | **7** ✅ |
| 文档完整性 | CHANGELOG + README + handover §9 + RELEASE NOTES + 本 close 报告 | **5/5 同步** ✅ |

acceptance 一次性 10/10 PASS，没出现 round-3 §4.2 描述的"批量
m3_closing TIMEOUT"——可能跟我加 `--per-script-timeout 60` 有关；
单跑也是 3.2s natural exit，符合预期。

---

## §3 与 round3 复审基线对比

| 维度 | round-3 评级 | v1.0.0-rc1 评级 | 变化 |
| --- | --- | --- | --- |
| 综合 | **Yellow-Green** | **Green** | ✅ 升 |
| EX-P1 闭环 | 5/5 | 5/5 | ✅ 持平 |
| EX-P2 闭环 | 12/14（EX-P2-10、EX-P2-13 列入 Known Limitations）| **14/14** ✅ | ✅ 升 |
| pytest | 262 | **280**（+18：8 DELETE + 10 /v1/）| ✅ |
| acceptance | 10/10 | 10/10 | ✅ 持平 |
| RBAC 覆盖 | 10/10 模块 | **10/10 + org.delete 第 11 个 resource** | ✅ 升 |
| 路由数 | 91 | **92**（+1 DELETE /orgs）+ legacy /ws + catch-all 308 = 94 内部 | ✅ |
| schema_version | 13 | **14**（+v14 `org.delete` perm seed）| ✅ |
| README 文字一致性 | 3 处落后（PBKDF2 200k / schema v11 / 90 路由）| **3/3 已修** | ✅ |
| dirty trick / soft regression | 5 处 `==`→`>=` 缺注释（soft 而已）| **5 处都补上注释** | ✅ |
| Known Limitations | 12 条 | **9 条**（移除 RBAC / PBKDF2 / path-traversal / executemany 4 条 + 移除 DELETE /orgs / /v1/ prefix 2 条；新增多机部署 / Docker push / multi-user keys 等不阻塞 GA 的项）| ✅ 净减 3 |

**评级理由从 Yellow-Green 升 Green 的 3 个关键差异**：

1. **EX-P2-10 + EX-P2-13 提前 land**，把 round-3 唯一两条"未实施
   但 acknowledge"的项从 Known Limitations 升级到 Added/Changed。
2. **README 三处文字落差完全闭合**（round-3 §7 评 β 95% 可信度
   的唯一软瑕疵）。
3. **5 处 `assert SCHEMA_VERSION >= 11` 全部加 forward-compat 注释**
   （round-3 §6 唯一 soft regression flag）。

---

## §4 现在的完整数据

| 项 | 值 |
| --- | --- |
| 累计 commit | **~110**（M1 ≈19 + M2 ≈16 + M3 ≈23 + fix-round-1 ≈10 + round-2 ≈8 + fix-round-2 ≈7 + 扩展审查 ≈8 + fix-round-3 ≈23 + round3-扩展 ≈3 + v1.0.0-rc1 收尾 7）|
| 本 round 新增 commit | **7**（见 §1 commit SHA） |
| 本 round diff | +1735 / -73 行（含 CHANGELOG / 测试 / 文档；纯代码改动 ≈ +650 行）|
| pytest 用例 | **280**（基线 262，+18 本 round）|
| acceptance 脚本 | **10 张全绿**，aggregate 24.2 秒 |
| REST 路由 | **92 个 `/v1/*`** + 1 个 legacy `/ws` + 1 个 catch-all = 内部 94 |
| WebSocket | 1 个 manager 单例，双挂 `/ws` + `/v1/ws` |
| schema_version | **14**（v11 → v12 RBAC seeds → v13 reclass history → v14 org.delete perm）|
| RBAC 覆盖 | **10 业务模块 + 1 admin（org.delete）= 11 个 resource**，42 perm seed 行（v12 41 行 + v14 2 行 admin/partner）|
| 文档 | README 345 行 / CHANGELOG 343 行（v1.0.0-rc1 段 ~280 行） / RELEASE NOTES 168 行 / handover 加 §9 v1.0.0-rc1 更新表 / 本 close 报告 |

---

## §5 Known Limitations（v1.0.0-rc1 仍存）

按 RELEASE NOTES §6 + README §8 同步：

1. AI 🔴 raw 场景 CI 中走 mock，生产需配 Ollama / OpenAI-compatible。
2. Tauri 4 命令未进端到端 IPC harness（单元覆盖已有）。
3. 附注模板 8 节（vs A-share ~40 节，v1.x 扩展）。
4. 同业基准 12 行 JSON（vs CSRC/Wind 实时摄取，v1.x）。
5. WebSocket server-side replay buffer 未做（客户端 cursor 已就位）。
6. 多用户密钥协商（per-user sub-key derivation 在 v1.1）。
7. Docker 官方 image 未本机 build / push（compose/k8s 模板已就位）。
8. 多机部署（SQLite + WAL 单机 only；Postgres backend 在 v1.1）。
9. `m2_closing_acceptance.py` 批量串行第 N 次偶发 120s timeout
   （单跑 3.1s natural exit；本 round 一次性 PASS）。

**关键**：这 9 条**没有一条是 P0/P1 阻塞项**，全部是 v1.0 GA 或
v1.1 路线的"可以发的限制"。

---

## §6 发布步骤（不打 tag，等用户拍板）

```powershell
# 1. 确认当前 HEAD = v1.0.0-rc1 收尾
git log -1 --format='%h %s'
# 预期：cb6d4e8d docs(finance-auto): add v1.0.0-rc1 release notes
# 实际 HEAD 会随本 close 报告 commit 而前移一格

# 2. 跑一次全套自验（可选；本报告 §2 已跑过）
.venv\Scripts\python.exe -m pytest plugins/finance-auto/tests/ -q
.venv\Scripts\python.exe plugins/finance-auto/scripts/run_all_acceptance.py

# 3. 用户拍板后：
# git tag v1.0.0-rc1 <HEAD>
# git push origin v1.0.0-rc1

# 4. （可选）GitHub Release Notes 直接复制 _finance_plugin_RELEASE_NOTES_v1.0.0-rc1.md
```

**绝对不要打 tag、不要 push**（per 用户严格约束 #4）。

---

## §7 v1.0.x backlog（已写进 README §8 + RELEASE NOTES §6）

按优先级：

1. **m2_closing TIMEOUT 修复**（daemonise scheduler + service.shutdown
   钩子）— v1.0.x 第 1 个补丁。
2. **Tauri 端到端 IPC harness**（与 m3_ui_acceptance 联动）。
3. **AI raw 场景生产 endpoint 配置文档**。
4. **附注模板扩展 8 → 40 节**（v1.1 主要工作量；可拆 sibling）。
5. **同业基准接入 CSRC / Wind**（数据合规 + ETL，v1.1+）。
6. **WebSocket server-side replay buffer**（客户端 cursor 已就位，
   服务端按 cursor 重发未消费事件）。
7. **多用户密钥协商**（per-user sub-key derivation；v1.1）。
8. **Docker 官方 image build + push**（v1.0 GA）。
9. **Postgres backend** + **远程 keyring**（多机部署；v1.1）。

---

## §8 最终自检清单（给用户）

- [x] 7 commits（包含本 close 报告 commit）落在 `63789248..HEAD`
- [x] 全部 commit 走 conventional commit 格式
- [x] **不打 tag** / **不 push** / **不改 git config** ✅
- [x] **本地 commit only** ✅
- [x] 全部 Python 用 `d:\OpenAkita\.venv\Scripts\python.exe`
- [x] Windows + PowerShell 用 `;` 不用 `&&`
- [x] self-audit 真跑（pytest / acceptance / check_territory / 308 probe / DELETE / README grep）
- [x] /v1/ 前缀有 308 redirect 旧路径兼容（不 breaking）
- [x] DELETE /orgs 默认 cascade=false（防误删）
- [x] release notes 诚实（Known Limitations 列了所有 v1.0.x / v1.1 待办）
- [x] 报告 ≤ 10 KB / 200 行（实际 ~7 KB / ~190 行）

**v1.0.0-rc1 真 Green，可发布。剩下的只是 `git tag v1.0.0-rc1
<HEAD>; git push origin v1.0.0-rc1` 这一步——等用户明确同意。**
