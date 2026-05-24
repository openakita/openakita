# finance-auto · OpenAkita 财务自动化插件

> **版本**：v1.0.0-rc1 (Release Candidate 1)
> **协议**：AGPL-3.0-only（随 OpenAkita 主体）
> **状态**：90 个 REST 路由 + 1 个 WebSocket，10/10 acceptance suite 全绿
> **设计参考**：`_finance_plugin_design_v0.3_INDEX.md` / `_finance_plugin_final_handover.md`

---

## §1 功能简介

**finance-auto** 是 OpenAkita 平台的"小企业 + 一般纳税人 + 多审计师协作"
财务自动化插件。它把三件传统上靠 Excel + 经验完成的工作整合进同一个
OpenAkita 桌面/服务实例：

1. **试算余额表 → 法定报表**：自动解析 .xls / .xlsx 余额表，按
   小企业准则或企业会计准则映射出资产负债表 / 利润表 / 现金流量表。
2. **多审计师工作流**：项目经理 → 复核员 → 合伙人三级 RBAC，复核
   留痕、签字、合并报表与重分类规则全部可溯源。
3. **AI 协作**：6 个聚合敏感度场景 + 3 个原始数据场景（默认走本地
   LLM），所有调用都有用户授权弹窗 + 审计日志 + WebSocket 实时推送。

对照 v0.3 设计文档的 14 大功能矩阵参见 [§5 功能矩阵](#§5-功能矩阵)。

---

## §2 系统要求

| 项 | 最低 | 推荐 |
| --- | --- | --- |
| Python | 3.11 | 3.12 |
| 操作系统 | Windows 10 / macOS 12 / Linux (glibc 2.28+) | Windows 11 / macOS 14 / Ubuntu 22.04 |
| 内存 | 2 GB 可用 | 4 GB+ |
| 磁盘 | 500 MB（不含数据 + 备份） | 5 GB+（含审计模板 + 历史备份） |
| 网络 | 离线可用；AI 场景按需联网 | 同左 |
| 桌面端 GUI | OpenAkita Setup Center (Tauri 2.x) | 同左 |

**插件级 Python 依赖**（不随 OpenAkita 主体安装，需单独 `pip install`）：

- `openpyxl>=3.1.5,<4.0`  — .xlsx 主路径
- `xlrd==1.2.0`           — .xls 兼容（必须 pin，2.x 已移除 .xls）
- `xltpl>=0.30,<1.0`      — Excel 模板渲染
- `keyring>=24.0,<26.0`   — 操作系统密钥环（Windows Credential Manager / macOS Keychain / Linux Secret Service）
- `pywin32>=306`（Windows-only） — .xls Tier-3 COM fallback
- `cryptography>=42.0,<46.0` — AES-GCM + PBKDF2-HMAC-SHA256

完整清单见 [`requirements.txt`](./requirements.txt)。

---

## §3 安装

### 3.1 从源码（开发者）

```powershell
# 1. 克隆 OpenAkita 仓库
git clone https://github.com/openakita/openakita.git
cd openakita

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\Activate.ps1   # macOS/Linux: source .venv/bin/activate

# 3. 安装 OpenAkita 主体 + dev 工具链
pip install -e ".[dev]"

# 4. 安装 finance-auto 插件依赖
pip install -r plugins/finance-auto/requirements.txt

# 5. （可选，未来支持）通过 extra 一键装：
#    pip install -e ".[finance-auto]"
```

### 3.2 数据库初始化

无需手动迁移。首次启动时插件会自动执行 `v0 → v1 → … → v11` 全链
schema 升级（idempotent）。

### 3.3 加密密钥种子

| 部署形态 | 推荐方式 |
| --- | --- |
| 桌面单用户（Windows / macOS / 大多数 Linux 桌面） | 默认走系统 keyring，无需操作 |
| Headless / Docker / 远程服务器 | **必须**设置 `OPENAKITA_FINANCE_AUTO_PASSPHRASE` 环境变量（32+ 字节高熵字符串）|
| CI / Acceptance | 同 Headless，使用临时环境变量 |

详见 [§7 部署模式](#§7-部署模式) 与
[`docs/DEPLOY_DOCKER.md`](./docs/DEPLOY_DOCKER.md)。

### 3.4 首次运行

```powershell
# 启动 OpenAkita 后端（finance-auto 自动加载）
openakita serve

# 验证插件已注册（应见 90+ /api/plugins/finance-auto/* 路由）
curl http://127.0.0.1:18900/api/plugins/finance-auto/health
```

桌面端打开 OpenAkita Setup Center → 侧边栏 **应用**（apps）分组下点
**财务自动化**。

---

## §4 快速上手（5 步 happy path）

1. **创建账套**：财务 → 账套管理 → 新建。填写组织名称、行业、报表
   口径（小企业 / 企业准则）、辅助核算模式（full / light / top_n）。
2. **导入余额表**：账套详情 → 上传 → 选 .xls/.xlsx 试算余额表。
   解析器三段降级（openpyxl → xlrd → pywin32 COM）兜底；解析失败的
   行进入 **ParseIssue 队列**，可在 UI 点 AI 协助 / 手动修正。
3. **生成报表**：报表 → 选期 → 一键生成资产负债表 / 利润表 /
   现金流量表（自动按 51-cell 模板，含 GAAP+CAS 双映射）。
4. **简化（可选）**：Top-N 合并次要科目 → 其余汇总到"其他"行；
   或切换"小企业简化"开关跳过部分披露行。
5. **跨期校验 + 审计报告**：跨期 → 触发期初对账（自动 emit
   ParseIssue 异常）；审计 → 上传审计模板（.xlsx with placeholder
   tags）→ 渲染填充值的最终交付件。

完整 11 步深度演示（含 AI / Notes / Peer / 密钥轮换）见
`_finance_plugin_final_handover.md` §4.2。

---

## §5 功能矩阵

按 v0.3 设计文档 14 大功能维度核对：

| # | 功能 | 状态 | UI 入口 | 主路由前缀 |
| --- | --- | :---: | --- | --- |
| 1 | 多账套（org/period CRUD）| ✅ | 账套管理 | `/orgs`, `/orgs/{id}/periods` |
| 2 | 试算余额导入 + 三段解析 | ✅ | 账套 → 上传 | `/orgs/{id}/imports` |
| 3 | 资产负债 / 利润 / 现金流报表 | ✅ | 报表 | `/orgs/{id}/reports/*` |
| 4 | 报表简化（Top-N + 其他）| ✅ | 报表 → 简化开关 | `/orgs/{id}/reports?simplified=1` |
| 5 | 跨期连续性校验 | ✅ | 跨期 | `/orgs/{id}/cross-period-checks` |
| 6 | 增值税申报表解析 | ✅ | 账套 → VAT | `/orgs/{id}/vat/*` |
| 7 | 行业覆盖（5 行业 override）| ✅ | 账套设置 → 行业 | `/orgs/{id}/industry-overrides` |
| 8 | 重分类规则（preview / apply / undo）| ✅ | 重分类 | `/orgs/{id}/reclassification-*` |
| 9 | AI 三档敏感度 + consent + 审计 | ✅ | 顶部 AI 弹窗 + AdvancedAI | `/ai/*` |
| 10 | 多审计师 RBAC + 复核工作流 | ✅ | 复核 | `/orgs/{id}/reviews/*` |
| 11 | 合并报表 group + pipeline | ✅ | 合并 | `/orgs/{id}/consolidation/*` |
| 12 | 附注自动生成（8 节）| ✅ | 附注 | `/orgs/{id}/notes/*` |
| 13 | 同业 Peer 对比（12 行业基准）| ✅ | Peer 对比 | `/orgs/{id}/peer/*` |
| 14 | 密钥轮换 + 加密备份/恢复 | ✅ | 密钥管理 | `/admin/key-*`, `/backups/*` |

> 完整 90 路由清单见 `routes.build_router_and_service` 入口及
> `_finance_plugin_final_handover.md` §3。

---

## §6 安全说明

### 6.1 加密

- **算法**：AES-256-GCM（cipher）+ PBKDF2-HMAC-SHA256（KDF，
  v1.0 RC：200 000 迭代，**v1.0 正式 GA 前计划提到 600 000**，
  详 `_finance_plugin_audit_extended_report.md` EX-P1-3）。
- **AAD**：固定 `openakita-finance-v1`，防止跨场景密文复用。
- **Nonce**：每次加密 `os.urandom(12)`，杜绝重用风险。
- **加密范围**：trial_balance / consent_records / ai_audit_log
  等敏感表的 `_encrypted_payload` 列；账套元数据明文以支持索引。

### 6.2 密钥管理

| 层 | 实现 | 备注 |
| --- | --- | --- |
| seed | 32 字节随机 | 存 OS keyring 或 `OPENAKITA_FINANCE_AUTO_PASSPHRASE` |
| component key | PBKDF2(seed, salt, iters) → 32 字节 | 写 `key_meta` 表，含 salt + iters + version |
| 轮换 | `POST /admin/key-rotate` | v1 → v2，仅 component；密文继续解，新写入用新版 |

### 6.3 RBAC（v1.0 RC 现状）

| 模块 | 应用层 RBAC |
| --- | --- |
| `review_workflow` 复核流转（draft → sign-off）| ✅ 8 处 `check_permission` |
| 其余 admin / reclass / cashflow / notes / peer 等 9 个模块 | ⚠️ 仅 host bearer token；**v1.0 正式 GA 计划补齐**（EX-P1-2）|

> 当前部署假设：单机桌面用户即 admin；多用户/多审计师 v1.0 RC
> 通过 `assignments` 表的项目-角色绑定提供**协作记录**，但写操作
> 的角色校验仍在 v1.x 路线图中。

### 6.4 API 路径约定（v1.0 RC + v2 升级路径）

- v1.0 RC：所有 endpoint 直接挂在 `/api/plugins/finance-auto/`
  下（无 `/v1/` 前缀）。
- v1.x → v2 升级策略：未来引入破坏性 schema 时，将通过
  `/api/plugins/finance-auto/v2/` 子路由 + v1 路径保留兼容 308
  重定向的方式滚动升级（参考 host 的 `orgs_v2_legacy_redirects.py`
  pattern）。详见 `_finance_plugin_audit_extended_report.md`
  EX-P2-13。

---

## §7 部署模式

### 7.1 单机桌面（默认）

OpenAkita Setup Center 启动后，finance-auto 自动加载。密钥种子走系统
keyring（Windows Credential Manager / macOS Keychain / Linux Secret
Service），用户无感知。

### 7.2 容器化 / Headless

Headless Linux 容器**没有 D-Bus → keyring 不可用**。**必须**设置
环境变量：

```bash
docker run -d --name openakita \
  -e OPENAKITA_FINANCE_AUTO_PASSPHRASE="$(openssl rand -hex 32)" \
  -v ./data:/app/data \
  -p 18900:18900 \
  openakita:1.0.0-rc1
```

完整指南：[`docs/DEPLOY_DOCKER.md`](./docs/DEPLOY_DOCKER.md)。

### 7.3 多用户协作（v1.0 RC）

后端 `assignments` 表支持项目-用户-角色三维绑定；前端 UserCtx 切换
当前身份。审计、复核、签字记录均带 user_id 留痕。**真正的鉴权
（拦截越权写入）属 v1.0 正式 GA 范围**（EX-P1-2）。

---

## §8 已知限制 (Known Limitations)

诚实清单（v1.0 RC，详细 RCA 见 `_finance_plugin_audit_extended_report.md`）：

1. **应用层 RBAC 不一致**（EX-P1-2）：除 review_workflow 外，admin /
   reclass / cashflow / xperiod / audit-tpl / manual / consol / notes /
   peer 9 个模块的写操作目前不在应用层校验角色，仅依赖 host bearer
   token。计划 v1.0 正式 GA 前补齐。
2. **PBKDF2 迭代数 200 k**（EX-P1-3）：低于 OWASP 2023 推荐的 600 k。
   `BACKUP_KDF_ITERATIONS` 与 `PBKDF2_ITERATIONS` 计划在 v1.0 GA
   提升到 600 k+（一次性 ~3s desktop unlock 延迟可接受）。
3. **路径遍历**（EX-P1-1）：备份/恢复 admin endpoint 接受用户传入
   `dest_dir` / `target_db_path`，沙盒校验在 v1.0 GA 加入。当前
   通过单机部署假设缓解。
4. **`m2_closing_acceptance.py` 不能干净退出**：scheduler 后台线程
   非 daemon。v1.0 GA 计划 daemonise + 加 `service.shutdown()` 钩子。
5. **AI 原始（🔴）场景在 CI 中走 mock**：S6/S7/S11 通过 monkey-patch
   `FinanceAIRouter` 注入 stub local endpoint。生产部署需配置真实
   Ollama / OpenAI-compatible endpoint。
6. **Tauri 桌面命令未进 closing harness**：4 个 Rust 命令
   （consent / notification / save-as / system-info）已实现且
   `m3_ui_acceptance.py` 单元覆盖；端到端测试在路线图。
7. **附注模板 8 节**：A-share 实际财报常含 ~40 节；v1.x 计划扩展。
8. **同业基准是 JSON 静态数据**：12 行业分位线；v1.x 计划接入
   CSRC / Wind 实时摄取。
9. **WebSocket 无 message replay**：客户端断线重连不补发漏掉的事件
   （v1.0 RC 已加客户端 cursor 占位，等后端 `?since=` 支持）。
10. **`reclassification.apply` 单条 INSERT 循环**：100 规则 ≈ 100 次
    round-trip。v1.1 改 `executemany`。
11. **无 `DELETE /orgs/{id}` endpoint**：账套清理需通过手动 SQL。
12. **CHANGELOG**：完整变更见 [`CHANGELOG.md`](./CHANGELOG.md)。

---

## §9 故障排查 FAQ

### Q1：`KeyringUnavailable / no recommended backend was available`

**症状**：headless Linux / Docker / WSL 启动时报错；插件加密功能
启动失败。

**原因**：D-Bus 未运行或未安装 `secretstorage` 后端。

**修复**：设置环境变量 `OPENAKITA_FINANCE_AUTO_PASSPHRASE`
（32+ 字节）。详见 `docs/DEPLOY_DOCKER.md`。

### Q2：备份文件解不开（restore 报 `wrong passphrase`）

**症状**：`POST /backups/{id}/restore` 返回
`{"ok": false, "verified": false, "error": "wrong passphrase"}`。

**原因**：备份用的 passphrase 与当前 keyring 中的 seed 不匹配，
或备份在另一台机器创建。

**修复**：使用备份创建时记录的 passphrase；如果丢失，仍可重新
解析 trial balance + 重生成报表（备份不是唯一数据源）。

### Q3：AI 调用 30s 超时 / `local endpoint unavailable`

**症状**：raw 场景（S6/S7/S11）返回 timeout 或 404。

**原因**：未配置本地 LLM endpoint（默认 Ollama `http://127.0.0.1:11434`）。

**修复**：启动 Ollama 并 `ollama pull llama3:8b`；或在
`config/ai_endpoints.yaml` 配置 OpenAI-compatible endpoint 并
打 `is_local_endpoint=true` 标记（仅当 endpoint 确在私网时）。

### Q4：上传 .xls 文件解析失败（Tier 1/2 均失败）

**症状**：上传 .xls 后状态 `failed`，ParseIssue 队列出现一条
`stage=parse, reason=tier3_pywin32_not_available`。

**修复**：Windows 仅：`pip install pywin32>=306` + 确认 Excel 已安装
（用于 COM fallback）。macOS/Linux 当前不支持 .xls 第三层兜底，
建议用户预先在 Excel 中"另存为 .xlsx"。

### Q5：路由 `/api/plugins/finance-auto/*` 全返回 404

**症状**：所有 finance-auto endpoint 都 404；其他插件正常。

**原因**：插件未被 PluginManager 加载（依赖未装 / plugin.json 解析
失败 / Python 异常）。

**修复**：
1. 查启动日志找 `finance-auto` 行；
2. `pip install -r plugins/finance-auto/requirements.txt`；
3. `python -c "import openpyxl, xlrd, xltpl, keyring, cryptography"`
   逐个 import 找缺包。

---

## §10 License + 版本信息

- **代码 License**：AGPL-3.0-only（随 OpenAkita 主体；见仓库根
  [`LICENSE`](../../LICENSE)）。
- **商标**：`OpenAkita` 名称与 logo 受
  [`TRADEMARK.md`](../../TRADEMARK.md) 限制；fork 必须保留 NOTICE。
- **审计模板版权**：仓库内 67 个 .xlsx 审计模板属用户自带样本，
  不构成原创作品；用户上传的会计师事务所内部模板版权归原作者。
- **插件版本**：`v1.0.0-rc1`（见 [`plugin.json`](./plugin.json)
  + [`CHANGELOG.md`](./CHANGELOG.md)）。
- **后端 schema 版本**：v11（migration 自动执行）。
- **REST 路由数**：90 + WebSocket 1。

### 10.1 前端开发者补充

如果你要修改前端 bundle（`ui/dist/index.html`），可通过
[`ui/scripts/gen-types.mjs`](./ui/scripts/gen-types.mjs) 拉取
当前后端 `/openapi.json` 并生成 TypeScript 类型定义供 IDE 提示：

```powershell
node plugins/finance-auto/ui/scripts/gen-types.mjs
```

输出在 `plugins/finance-auto/ui/dist/types/finance-auto-api.d.ts`。

---

**最后更新**：2026-05-24 · 维护：OpenAkita team · 反馈：
GitHub Issues / OpenAkita 仓库根 `_finance_plugin_*` 审查报告。
