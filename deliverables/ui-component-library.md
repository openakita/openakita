# UI 组件库清单

**版本**: V1.0  
**更新时间**: 2026-03-11  
**状态**: ✅ 技术评审会材料  
**技术栈**: React + React Flow + Tailwind CSS

---

## 一、组件库总览

**组件总数**: 30+  
**分类**: 基础组件 (8) + 工作流专用组件 (12) + 节点模板组件 (10)  
**复用率**: 85%+  
**打包体积**: <100KB (gzipped)

---

## 二、基础组件（8 个）

基于 Tailwind CSS 构建，遵循 UI 2.0 设计规范

### 2.1 Button 按钮

**用途**: 所有交互按钮  
**变体**:
- `primary` - 主按钮（品牌蓝 #2563EB）
- `secondary` - 次按钮（灰色边框）
- `danger` - 危险按钮（红色 #EF4444）
- `ghost` - 幽灵按钮（无边框）

**尺寸**: `sm`, `md`, `lg`  
**状态**: `disabled`, `loading`

```tsx
// 使用示例
<Button variant="primary" size="md" onClick={handleSave}>
  保存
</Button>
```

**文件路径**: `src/components/ui/Button.tsx`

---

### 2.2 Input 文本输入框

**用途**: 文本输入  
**类型**: `text`, `password`, `email`, `number`, `textarea`  
**特性**:
- 必填标记（红色*）
- 错误提示（红色文字）
- 字符计数
- 自动聚焦

```tsx
<Input
  label="节点名称"
  value={name}
  onChange={setName}
  required
  maxLength={50}
/>
```

**文件路径**: `src/components/ui/Input.tsx`

---

### 2.3 Select 下拉选择框

**用途**: 单选/多选下拉  
**特性**:
- 搜索过滤
- 分组选项
- 自定义渲染
- 多选标签

```tsx
<Select
  label="触发类型"
  options={[
    { value: 'cron', label: '定时触发' },
    { value: 'event', label: '事件触发' }
  ]}
  value={type}
  onChange={setType}
/>
```

**文件路径**: `src/components/ui/Select.tsx`

---

### 2.4 Switch 开关切换

**用途**: 布尔值切换  
**特性**:
- 自定义颜色
- 加载状态
- 禁用状态
- 尺寸调整

```tsx
<Switch
  label="启用重试"
  checked={enableRetry}
  onChange={setEnableRetry}
/>
```

**文件路径**: `src/components/ui/Switch.tsx`

---

### 2.5 Modal 模态对话框

**用途**: 弹窗对话框  
**类型**:
- `confirm` - 确认对话框
- `form` - 表单对话框
- `custom` - 自定义内容

**特性**:
- 点击遮罩关闭
- ESC 关闭
- 动画过渡
- 滚动锁定

```tsx
<Modal
  title="新建工作流"
  open={isOpen}
  onClose={handleClose}
  onConfirm={handleConfirm}
>
  {/* 自定义内容 */}
</Modal>
```

**文件路径**: `src/components/ui/Modal.tsx`

---

### 2.6 Tooltip 提示气泡

**用途**: 悬停提示  
**位置**: `top`, `bottom`, `left`, `right`  
**特性**:
- 延迟显示（200ms）
- 自动定位
- 富文本支持

```tsx
<Tooltip content="点击配置节点参数" position="top">
  <Button>配置</Button>
</Tooltip>
```

**文件路径**: `src/components/ui/Tooltip.tsx`

---

### 2.7 Badge 状态标签

**用途**: 状态标记  
**颜色**:
- `green` - 成功/完成
- `yellow` - 警告/进行中
- `red` - 错误/失败
- `blue` - 信息/默认
- `gray` - 未开始

```tsx
<Badge color="green">已完成</Badge>
```

**文件路径**: `src/components/ui/Badge.tsx`

---

### 2.8 Avatar 头像/图标容器

**用途**: 节点图标、用户头像  
**尺寸**: `sm` (24px), `md` (32px), `lg` (40px)  
**形状**: `circle`, `square`, `rounded`

```tsx
<Avatar src="/icons/email.svg" size="md" />
```

**文件路径**: `src/components/ui/Avatar.tsx`

---

## 三、工作流专用组件（12 个）

基于 React Flow 构建，专用于工作流编辑器

### 3.1 WorkflowCanvas 无限画布容器

**用途**: 主画布容器  
**特性**:
- 无限画布
- 网格背景
- 缩放和平移
- 节点选择框

**Props**:
```tsx
interface WorkflowCanvasProps {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (connection: Connection) => void;
}
```

**文件路径**: `src/components/workflow/WorkflowCanvas.tsx`

---

### 3.2 NodeCard 节点卡片容器

**用途**: 所有节点的基础容器  
**特性**:
- 圆角边框
- 阴影效果
- 悬停上浮
- 选中高亮

**样式**:
```tsx
className="bg-white rounded-lg shadow-md hover:shadow-lg border-2 transition-all"
```

**文件路径**: `src/components/workflow/NodeCard.tsx`

---

### 3.3 NodeHeader 节点头部

**用途**: 节点标题区域  
**结构**:
```tsx
<div className="flex items-center gap-2 p-3 border-b">
  <Avatar icon={icon} />
  <span className="font-medium">{title}</span>
  <Badge color={status} />
</div>
```

**文件路径**: `src/components/workflow/NodeHeader.tsx`

---

### 3.4 NodeBody 节点内容区域

**用途**: 节点内容展示  
**特性**:
- 可折叠
- 自定义渲染
- 数据预览

**文件路径**: `src/components/workflow/NodeBody.tsx`

---

### 3.5 NodePort 连接端口

**用途**: 节点输入/输出连接点  
**类型**: `source` (输出), `target` (输入)  
**特性**:
- 悬停高亮
- 连接状态指示
- 类型校验

```tsx
<NodePort
  type="source"
  position="right"
  id="output-1"
  dataType="order"
/>
```

**文件路径**: `src/components/workflow/NodePort.tsx`

---

### 3.6 ConnectionLine 贝塞尔曲线连接线

**用途**: 节点间连接线  
**特性**:
- 贝塞尔曲线
- 动画流动效果
- 点击选择
- 删除按钮

**样式**:
```tsx
// 默认状态
stroke="#9CA3AF" strokeWidth={2} fill="none"

// 选中状态
stroke="#2563EB" strokeWidth={3}
```

**文件路径**: `src/components/workflow/ConnectionLine.tsx`

---

### 3.7 ComponentPanel 左侧组件库面板

**用途**: 节点模板库  
**结构**:
```tsx
<div className="w-70 border-r bg-white">
  <PanelHeader title="组件库" />
  <NodeCategory title="触发器" nodes={triggerNodes} />
  <NodeCategory title="动作" nodes={actionNodes} />
  <NodeCategory title="条件" nodes={conditionNodes} />
</div>
```

**特性**:
- 分类折叠
- 拖拽开始
- 搜索过滤

**文件路径**: `src/components/workflow/ComponentPanel.tsx`

---

### 3.8 ConfigPanel 右侧配置面板

**用途**: 节点配置表单  
**特性**:
- 自动展开/收起
- 表单验证
- 实时预览
- 自动保存

**文件路径**: `src/components/workflow/ConfigPanel.tsx`

---

### 3.9 MiniMap 画布小地图

**用途**: 画布导航  
**特性**:
- 实时缩略图
- 可视区域标记
- 点击跳转
- 缩放控制

**文件路径**: `src/components/workflow/MiniMap.tsx`

---

### 3.10 Toolbar 顶部工具栏

**用途**: 全局操作按钮  
**按钮**:
- 新建工作流
- 保存
- 测试
- 撤销/重做
- 适应屏幕
- 帮助

**文件路径**: `src/components/workflow/Toolbar.tsx`

---

### 3.11 StatusBar 底部状态栏

**用途**: 状态信息和缩放控制  
**内容**:
- 节点数量
- 最后保存时间
- 缩放比例
- 执行状态

**文件路径**: `src/components/workflow/StatusBar.tsx`

---

### 3.12 ExpressionEditor 表达式编辑器

**用途**: 自定义表达式输入  
**特性**:
- 语法高亮
- 自动补全
- 错误提示
- 字段引用

**文件路径**: `src/components/workflow/ExpressionEditor.tsx`

---

## 四、节点模板组件（10 个）

### 4.1 TriggerNode 触发器节点

**用途**: 所有触发器节点模板  
**子类型**: 定时/事件/Webhook/手动

**文件路径**: `src/components/workflow/nodes/TriggerNode.tsx`

---

### 4.2 ActionNode 动作节点

**用途**: 所有动作节点模板  
**子类型**: 邮件/报表/数据/API

**文件路径**: `src/components/workflow/nodes/ActionNode.tsx`

---

### 4.3 ConditionNode 条件分支节点

**用途**: 条件判断节点  
**特性**: 菱形形状，True/False 双输出

**文件路径**: `src/components/workflow/nodes/ConditionNode.tsx`

---

### 4.4 LoopNode 循环节点

**用途**: 循环遍历节点  
**特性**: 紫色边框，循环箭头图标

**文件路径**: `src/components/workflow/nodes/LoopNode.tsx`

---

### 4.5 TransformNode 数据转换节点

**用途**: 字段映射和转换  
**特性**: 橙色边框，数据转换图标

**文件路径**: `src/components/workflow/nodes/TransformNode.tsx`

---

### 4.6 EmailNode 邮件节点

**用途**: 邮件发送专用  
**配置项**: 收件人/主题/内容/附件

**文件路径**: `src/components/workflow/nodes/EmailNode.tsx`

---

### 4.7 ReportNode 报表节点

**用途**: 报表生成专用  
**配置项**: 数据源/图表类型/导出格式

**文件路径**: `src/components/workflow/nodes/ReportNode.tsx`

---

### 4.8 APINode API 调用节点

**用途**: HTTP API 调用  
**配置项**: URL/方法/Headers/Body

**文件路径**: `src/components/workflow/nodes/APINode.tsx`

---

### 4.9 DataNode 数据处理节点

**用途**: 数据清洗/转换  
**配置项**: 输入字段/转换规则/输出字段

**文件路径**: `src/components/workflow/nodes/DataNode.tsx`

---

### 4.10 WebhookNode Webhook 节点

**用途**: Webhook 接收/发送  
**配置项**: 端点 URL/认证/事件类型

**文件路径**: `src/components/workflow/nodes/WebhookNode.tsx`

---

## 五、组件依赖关系

```
WorkflowCanvas (核心容器)
├── NodeCard (节点容器)
│   ├── NodeHeader
│   │   ├── Avatar
│   │   └── Badge
│   ├── NodeBody
│   └── NodePort
├── ConnectionLine
├── ComponentPanel
│   └── NodeCategory (复用 NodeCard)
├── ConfigPanel
│   ├── Input
│   ├── Select
│   ├── Switch
│   └── ExpressionEditor
├── MiniMap
├── Toolbar
│   └── Button
└── StatusBar
```

---

## 六、技术栈建议

### 6.1 核心依赖

```json
{
  "react": "^18.2.0",
  "react-flow-renderer": "^11.10.0",
  "tailwindcss": "^3.4.0",
  "@headlessui/react": "^1.7.0",
  "@heroicons/react": "^2.1.0"
}
```

### 6.2 推荐原因

**React Flow**:
- ✅ 专为流程图/工作流设计
- ✅ 内置节点/连线/画布管理
- ✅ 高度可定制
- ✅ 活跃社区，文档完善
- ✅ 支持 TypeScript

**Tailwind CSS**:
- ✅ 原子化 CSS，开发效率高
- ✅ 内置响应式支持
- ✅ 主题定制方便
- ✅ 打包体积小
- ✅ 与设计系统完美配合

### 6.3 项目结构建议

```
src/
├── components/
│   ├── ui/              # 基础组件
│   │   ├── Button.tsx
│   │   ├── Input.tsx
│   │   └── ...
│   └── workflow/        # 工作流专用组件
│       ├── WorkflowCanvas.tsx
│       ├── NodeCard.tsx
│       ├── nodes/       # 节点模板
│       │   ├── TriggerNode.tsx
│       │   └── ...
│       └── ...
├── hooks/               # 自定义 Hooks
├── utils/               # 工具函数
├── types/               # TypeScript 类型定义
└── styles/              # 全局样式
```

---

## 七、开发优先级

### P0 - Sprint 1-2 (03-12 ~ 03-25)

| 组件 | 预计工时 | 负责人 |
|------|----------|--------|
| WorkflowCanvas | 2 天 | 全栈 A |
| NodeCard | 1 天 | 全栈 B |
| ComponentPanel | 1.5 天 | 全栈 A |
| ConfigPanel | 2 天 | 全栈 B |
| TriggerNode | 1 天 | 全栈 A |
| ActionNode | 1 天 | 全栈 B |
| ConnectionLine | 0.5 天 | 全栈 A |

**小计**: 9 天

### P1 - Sprint 3-4 (03-26 ~ 04-10)

| 组件 | 预计工时 |
|------|----------|
| ConditionNode | 1 天 |
| LoopNode | 1 天 |
| TransformNode | 1 天 |
| 专用节点模板 (5 个) | 3 天 |
| MiniMap | 1 天 |
| Toolbar | 0.5 天 |
| StatusBar | 0.5 天 |

**小计**: 8 天

### P2 - Sprint 5-6 (04-11 ~ 04-20)

| 组件 | 预计工时 |
|------|----------|
| ExpressionEditor | 2 天 |
| 基础组件优化 | 2 天 |
| 响应式适配 | 2 天 |
| 性能优化 | 2 天 |

**小计**: 8 天

---

## 八、验收标准

| 指标 | 目标值 |
|------|--------|
| 组件复用率 | >85% |
| 打包体积 | <100KB (gzipped) |
| 首屏加载 | <2 秒 |
| TypeScript 覆盖率 | 100% |
| 单元测试覆盖率 | >80% |

---

**文档状态**: ✅ 完成  
**适用会议**: 03-12 技术评审会  
**配合开发**: Sprint 1-3
