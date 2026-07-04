# P4 设计方案：项目页甘特图式拆解 + 用户验收

> 状态：阶段A/B/C 全部落地。项目页 `OrgProjectBoard` 已有甘特时间轴 + 看板 +
> 任务详情；本轮补齐「派发层级缩进 + started_at 时间条起点」(阶段B) 并确认
> 「验收/打回」交互 + 后端 ACCEPTED→completed_at 自动落时间 (阶段C)。

## 1. 目标
收到用户指令时即把指令拆解为子任务/阶段，并在项目页以**甘特图/时间线**展示：
- 按节点清晰标识（谁负责）
- 各任务执行状态（进行中/已交付/被退回/已验收）
- 起止时间形成时间条
- 用户可查看并**主动验收**任务/子任务（图3 紫色进度条语义）

## 2. 数据来源盘点（现状）
编排过程已经把派单/交付落成 `ProjectStore` 的 `ProjectTask`（见
`_contract_event_tap`），`ProjectTask` 模型**已具备**甘特所需的绝大多数字段：

| 字段 | 现状 | 甘特用途 |
|------|------|----------|
| `assignee_node_id` | ✅ 派单时写入 | 行（按节点/泳道） |
| `delegated_by` | ✅ | 连线（上级→下级） |
| `parent_task_id` / `chain_id` / `depth` | ✅ | 任务树/层级缩进 |
| `status`（todo/in_progress/delivered/rejected/**accepted**） | ✅ 有 ACCEPTED 态 | 颜色/状态 |
| `progress_pct` | ✅ 交付时置 100 | 进度条填充 |
| `title` / `description` | ✅ 派单摘要 | 条目标签/悬浮 |
| `started_at` / `delivered_at` / `completed_at` | ⚠️ 字段在但此前**未写时间** | 时间条起止 |

## 3. 缺口
1. **时间戳未采集**：契约 tap 建任务时 `started_at=None`，交付时也没写
   `delivered_at/completed_at` → 时间条画不出来。（本轮已修，见 §5）
2. **验收交互缺失**：有 `TaskStatus.ACCEPTED` 枚举，但没有「用户把任务标记
   为已验收」的 API/按钮。
3. **甘特渲染组件缺失**：项目页目前是看板/列表，没有时间线/甘特视图组件。

## 4. 推荐实现路径（分阶段）
- **阶段A（已落地）**：契约 tap 采集 `started_at`（派单/开始时）与
  `delivered_at`+`completed_at`（交付时），让任务自带真实起止时间。
- **阶段B（剩余·中）**：项目页新增「时间线/甘特」视图：以 `assignee_node_id`
  为泳道、`started_at→completed_at` 为时间条、`status` 决定颜色、
  `parent_task_id` 缩进表达层级；数据直接读现有 `/api/v2/orgs/{id}/projects`
  任务列表，无需新数据管线。
- **阶段C（剩余·中）**：验收交互：新增
  `POST /api/v2/orgs/{id}/tasks/{task_id}/accept` 将 `status→accepted` 并写
  `completed_at`；甘特条目加「验收」按钮，已验收显示紫色（对齐图3）。

## 5. 落地记录
- **阶段A**：契约 tap 采集 `started_at`（派单时）；交付走 DELIVERED 自动落
  `delivered_at`，`completed_at` 留给验收。
- **阶段B（本轮）**：`OrgProjectBoard` 已内置甘特时间轴（`GanttView`）。本轮
  把任务排序改为**按父子树排列 + 按 `depth` 缩进**（`orderTasksForGantt`，
  附 vitest 覆盖树序/回退/孤儿/环），让派发层级/阶段一目了然；时间条起点
  改为优先 `started_at`（回退 `created_at`），终点用 `delivered_at`/
  `completed_at`；子任务行加 `└` 连接符 + 悬浮说明。
- **阶段C（本轮确认）**：`GanttView`/`KanbanView`/任务详情在 `delivered`
  状态下已提供「验收 / 打回」按钮，经 `PUT .../tasks/{id}` 写 `status`；
  `update_task` 在 ACCEPTED 时自动落 `completed_at`、DELIVERED 时自动落
  `delivered_at`（新增 `test_accept_and_deliver_stamp_timestamps` 双后端覆盖）。
  「打回」置 `rejected`，随后「重新派发」按钮触发重做，与现有状态机一致。

## 6. 剩余/可选增强
- 甘特顶部时间刻度轴（日期标尺）目前用相对时间条，未画绝对刻度；可选增强。
- 「打回」目前是「置 rejected + 手动重新派发」两步；若要一键「打回即自动重做」
  可在 accept/reject 端点加可选 `redispatch=true`，列为可选。
