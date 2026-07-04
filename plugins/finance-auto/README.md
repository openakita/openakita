# finance-auto · OpenAkita 财务自动化插件

> **版本**：v1.0.0-rc1 (Release Candidate 1)
> **协议**：AGPL-3.0-only（随 OpenAkita 主体）
> **状态**：92 个 REST 路由 + 1 个 WebSocket（双挂 `/ws` + `/v1/ws`），10/10 acceptance suite 全绿
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
- `xltpl>=0.20,<1.0`      — Excel 模板渲染（PyPI 当前 HEAD 0.21）
- `keyring>=24.0,<26.0`   — 操作系统密钥环（Windows Credential Manager / macOS Keychain / Linux Secret Service）
- `pywin32>=306`（Windows-only） — .xls Tier-3 COM fallback
- `cryptography>=42.0,<46.0` — AES-GCM + PBKDF2-HMAC-SHA256

完整清单见 [`requirements.txt`](./requirements.txt)。

### 2.1 插件清单权限 vs 运行时能力

`plugin.json` 的 `permissions` 字段只能声明 **宿主插件系统认得的权限名**
（见 `src/openakita/plugins/manifest.py` 的 `ALL_PERMISSIONS`）。本插件实际
使用的有效权限是：`log` / `routes.register` / `config.read` /
`config.write` / `data.own`。宿主会静默丢弃它不认识的名字，因此清单里
**不再**写入下列条目——它们是「运行时能力」而非宿主权限，由 Python 依赖
或桌面外壳提供，无需也无法通过 `permissions` 申请：

| 能力 | 来源 | 说明 |
| --- | --- | --- |
| 密钥环读写 | `keyring` 依赖 | OS 凭据库存放加密 seed，进程内直接调用 |
| 文件读写 | `data.own` 数据目录 | 插件仅在宿主分配的 `data/` 目录内读写 SQLite / 备份 |
| 原生对话框 / 系统通知 | Tauri 外壳 `finance-native` 能力 | 桌面端通过 bridge 握手协商，Web 预览自动降级为禁用按钮 |
| WebSocket 服务 | `routes.register` 注册的 `/ws` 路由 | WS 端点挂在插件 router 上，不需要单独的服务权限 |

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

无需手动迁移。首次启动时插件会自动执行 `v0 → v1 → … → v14` 全链
schema 升级（idempotent；fix-round-3 引入 v12 extended permissions
seeds + v13 reclassification undo history；v1.0.0-rc1 引入 v14
`org.delete` 权限种子）。

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

# 验证插件已注册（应见 92 /api/plugins/finance-auto/v1/* 路由 + 兼容 308 redirect）
curl http://127.0.0.1:18900/api/plugins/finance-auto/v1/health
# 老路径会 308 跳到 /v1/ 下：
curl -i http://127.0.0.1:18900/api/plugins/finance-auto/health  # → 308 Location: .../v1/health
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

> 完整 92 路由清单见 `routes.build_router_and_service` 入口及
> `_finance_plugin_final_handover.md` §3。所有路由现挂在 `/v1/` 子
> 路径下，老路径自动 308 redirect（详 §6.4）。

---

## §6 安全说明

### 6.1 加密

- **算法**：AES-256-GCM（cipher）+ PBKDF2-HMAC-SHA256（KDF，
  v1.0 RC：**600 000 迭代**，符合 OWASP 2023 推荐；可通过环境变量
  `OPENAKITA_FINANCE_AUTO_KDF_ITERATIONS` 进一步调优，最低 100 000
  保护下限。已发布的旧 200k 备份仍可正常恢复——`restore_backup`
  从 manifest 读取 `kdf_iterations` 字段实现向后兼容。详
  `_finance_plugin_audit_extended_report.md` EX-P1-3 / fix-round-3
  `8a628d1f`）。
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
| `review_workflow` 复核流转（draft → sign-off）| ✅ 7 处 `check_permission`（v9 既有）|
| 9 个写操作模块：admin / reclass / cashflow / xperiod / audit-tpl / manual / consol / parse / notes / peer | ✅ 22 处 `require_permission` 路由依赖（fix-round-3 EX-P1-2 补齐，schema v12 seed 41 行权限）|

RBAC 模型沿 v0.3 Part Biz §1.1：
- `admin` — 系统级危险操作（备份创建/恢复、密钥轮换）。
- `partner` — 业务最高级；全部 `auditor` + `manager` 权限再 + `notes.edit` / `audit-tpl.delete`。
- `manager` — `auditor` 全权 + `reclassification.apply` / `peer.run` / `audit-tpl.upload`。
- `auditor` — 日常账套作业（preview / generate / compute / decide / learn）。
- 未注册用户 → 整路由 `403 rbac_denied`（`X-OpenAkita-User-Id` header 或 `?user_id=` query 均可标识；缺省 `local` 走单机管理员旁路）。

> 当前部署假设：单机桌面用户走 `local` 旁路；多用户/多审计师 v1.0
> RC 通过 `assignments` 表的项目-角色绑定执行写操作 RBAC。22 处路由
> 依赖 + 7 处 service 校验均经 round-3 矩阵实测（10/10 模块返回
> `rbac_denied`）。

### 6.4 API 路径约定（v1.0 RC + v2 升级路径）

- v1.0.0-rc1：所有 endpoint 挂在 `/api/plugins/finance-auto/v1/`
  下；老的无 `/v1/` 路径自动 308 redirect 到 `/v1/` 等价路径
  （保留 method + body + query string），实现零 breaking change
  迁移。
- WebSocket 双挂：`/ws`（老路径，向后兼容）+ `/v1/ws`（新路径）。
  浏览器 WS 客户端不能跟随 HTTP 308，因此后端两个 mount 共用同一
  `WSManager` 单例。新 UI bundle 已切到 `/v1/ws`；缓存的老 bundle
  继续走 `/ws` 直到用户刷新。
- v1.x → v2 升级策略：未来引入破坏性 schema 时，将通过
  `/api/plugins/finance-auto/v2/` 子路由 + v1 路径继续 308 重定向
  到 v2 的方式滚动升级。详见
  `_finance_plugin_audit_extended_report.md` EX-P2-13 +
  `CHANGELOG.md` v1.0.0-rc1 段落。

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

诚实清单（v1.0 RC，详细 RCA 见 `_finance_plugin_audit_extended_report.md`
+ `_finance_plugin_audit_report_round3.md`）：

1. **`m2_closing_acceptance.py` 偶发 timeout**：scheduler 后台线程
   非 daemon，acceptance 在批量串行第 N 次复用时偶发卡 120s；单独
   subprocess 始终 3.1s natural exit。v1.0 GA 计划 daemonise + 加
   `service.shutdown()` 钩子。
2. **AI 原始（🔴）场景在 CI 中走 mock**：S6/S7/S11 通过 monkey-patch
   `FinanceAIRouter` 注入 stub local endpoint。生产部署需配置真实
   Ollama / OpenAI-compatible endpoint。
3. **Tauri 桌面命令未进 closing harness**：4 个 Rust 命令
   （consent / notification / save-as / system-info）已实现且
   `m3_ui_acceptance.py` 单元覆盖；端到端 IPC 测试在路线图。
4. **附注模板 8 节**：A-share 实际财报常含 ~40 节；v1.x 计划扩展。
5. **同业基准是 JSON 静态数据**：12 行业分位线；v1.x 计划接入
   CSRC / Wind 实时摄取。
6. **WebSocket 无 message replay**：v1.0 RC 已加客户端 cursor +
   `?since=` query 占位 + reconnecting badge 状态机；服务端 replay
   逻辑（按 cursor 重发未消费事件）在 v1.x 路线。
7. **Docker 镜像未本机 build 验证**：`docs/DEPLOY_DOCKER.md` 提供
   compose / k8s 模板，但官方 image push 在 v1.0 GA 完成。
8. **多用户密钥协商**：当前组件密钥按 `key_meta` 全局共享，无
   per-user 子密钥协商；v1.1 计划。
9. **CHANGELOG**：完整变更见 [`CHANGELOG.md`](./CHANGELOG.md)。

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
- **后端 schema 版本**：v14（migration 自动执行；含 v12 extended
  permissions seeds + v13 reclassification undo history + v14
  `org.delete` 权限种子）。
- **REST 路由数**：92（含 `DELETE /orgs/{id}`）+ WebSocket 1
  （双挂 `/ws` + `/v1/ws`）。

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
