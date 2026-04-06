/**
 * 工作流编排器类型定义
 */

// 节点类型枚举
export enum NodeType {
  TRIGGER = 'trigger',      // 触发器 - 蓝色
  ACTION = 'action',        // 动作 - 绿色
  CONDITION = 'condition',  // 条件 - 橙色
  LOOP = 'loop',            // 循环 - 紫色
  TOOL = 'tool',            // 工具 - 青色
}

// 节点颜色配置
export const NODE_COLORS: Record<NodeType, { bg: string; border: string; text: string }> = {
  [NodeType.TRIGGER]: { bg: '#e6f7ff', border: '#1890ff', text: '#0050b3' },
  [NodeType.ACTION]: { bg: '#f6ffed', border: '#52c41a', text: '#237804' },
  [NodeType.CONDITION]: { bg: '#fff7e6', border: '#fa8c16', text: '#ad6800' },
  [NodeType.LOOP]: { bg: '#f9f0ff', border: '#722ed1', text: '#391085' },
  [NodeType.TOOL]: { bg: '#e6fffb', border: '#13c2c2', text: '#006d75' },
};

// 节点位置
export interface Position {
  x: number;
  y: number;
}

// 节点数据配置
export interface NodeData {
  label: string;
  nodeType: NodeType;
  description?: string;
  config: Record<string, any>;
  inputs?: string[];
  outputs?: string[];
  status?: 'idle' | 'running' | 'completed' | 'error';
}

// 工作流节点
export interface WorkflowNode {
  id: string;
  type: NodeType;
  position: Position;
  data: NodeData;
  selected?: boolean;
}

// 工作流边（连接线）
export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  targetHandle?: string;
  label?: string;
  type?: string;
  animated?: boolean;
  style?: React.CSSProperties;
}

// 工作流定义
export interface WorkflowDefinition {
  id: string;
  name: string;
  description?: string;
  version: string;
  status: 'draft' | 'published' | 'archived';
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

// 节点工具箱分类
export interface NodeCategory {
  id: string;
  label: string;
  icon: React.ReactNode;
  nodes: NodeTemplate[];
}

// 节点模板
export interface NodeTemplate {
  id: string;
  type: NodeType;
  label: string;
  description: string;
  icon: React.ReactNode;
  defaultConfig: Record<string, any>;
}

// 节点配置表单字段
export interface ConfigField {
  key: string;
  label: string;
  type: 'text' | 'number' | 'select' | 'textarea' | 'checkbox' | 'json' | 'cron';
  required?: boolean;
  defaultValue?: any;
  options?: Array<{ label: string; value: string }>;
  placeholder?: string;
  help?: string;
  validation?: {
    pattern?: string;
    min?: number;
    max?: number;
    custom?: (value: any) => string | null;
  };
}

// 节点类型配置
export interface NodeTypeConfig {
  type: NodeType;
  label: string;
  description: string;
  color: string;
  icon: React.ReactNode;
  inputs: string[];
  outputs: string[];
  configFields: ConfigField[];
}

// 工作流模板
export interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  industry?: string;
  workflow: WorkflowDefinition;
  usageCount: number;
  rating: number;
}

// 画布配置
export interface CanvasConfig {
  snapToGrid: boolean;
  gridSize: number;
  showMiniMap: boolean;
  showControls: boolean;
  zoomOnScroll: boolean;
  panOnScroll: boolean;
  selectionKeyCode: 'Shift' | 'Ctrl' | 'Meta' | null;
  multiSelectionKeyCode: 'Shift' | 'Ctrl' | 'Meta' | null;
}

// 工具栏按钮
export interface ToolbarButton {
  id: string;
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  tooltip?: string;
}
