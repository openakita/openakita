import React from 'react';
import './NodePanel.css';

interface NodeItem {
  type: string;
  label: string;
  icon: string;
  description: string;
  category: 'trigger' | 'action' | 'condition' | 'utility';
}

const nodeItems: NodeItem[] = [
  // 触发器节点
  {
    type: 'trigger',
    label: '定时触发',
    icon: '🕐',
    description: 'Cron 表达式定时执行',
    category: 'trigger',
  },
  {
    type: 'trigger',
    label: '事件触发',
    icon: '🔔',
    description: '系统事件监听',
    category: 'trigger',
  },
  {
    type: 'trigger',
    label: 'Webhook',
    icon: '🌐',
    description: 'API 端点接收',
    category: 'trigger',
  },
  {
    type: 'trigger',
    label: '手动触发',
    icon: '👆',
    description: '点击执行',
    category: 'trigger',
  },
  // 动作节点
  {
    type: 'action',
    label: '发送邮件',
    icon: '📧',
    description: '发送电子邮件',
    category: 'action',
  },
  {
    type: 'action',
    label: '生成报表',
    icon: '📊',
    description: '数据汇总和图表',
    category: 'action',
  },
  {
    type: 'action',
    label: '数据整理',
    icon: '🧹',
    description: '数据清洗和转换',
    category: 'action',
  },
  {
    type: 'action',
    label: 'API 调用',
    icon: '🔌',
    description: 'HTTP 请求',
    category: 'action',
  },
  // 条件节点
  {
    type: 'condition',
    label: '条件分支',
    icon: '❓',
    description: 'True/False 判断',
    category: 'condition',
  },
  {
    type: 'condition',
    label: '多重条件',
    icon: '🔀',
    description: '多分支判断',
    category: 'condition',
  },
  // 工具节点
  {
    type: 'loop',
    label: '循环',
    icon: '🔄',
    description: '遍历列表执行',
    category: 'utility',
  },
  {
    type: 'transform',
    label: '字段映射',
    icon: '🔧',
    description: '数据字段转换',
    category: 'utility',
  },
];

interface NodePanelProps {
  onNodeDragStart?: (nodeType: string) => void;
}

export default function NodePanel({ onNodeDragStart }: NodePanelProps) {
  const onDragStart = (event: React.DragEvent, nodeType: string) => {
    event.dataTransfer.setData('application/reactflow', nodeType);
    event.dataTransfer.effectAllowed = 'move';
    onNodeDragStart?.(nodeType);
  };

  const categories = [
    { id: 'trigger', name: '触发器', color: '#10B981' },
    { id: 'action', name: '动作', color: '#2563EB' },
    { id: 'condition', name: '条件', color: '#F59E0B' },
    { id: 'utility', name: '工具', color: '#7C3AED' },
  ];

  return (
    <div className="node-panel">
      <div className="node-panel-header">
        <h3>组件库</h3>
        <span className="node-count">{nodeItems.length} 个组件</span>
      </div>
      
      <div className="node-panel-content">
        {categories.map((category) => {
          const categoryNodes = nodeItems.filter(
            (item) => item.category === category.id
          );
          
          if (categoryNodes.length === 0) return null;

          return (
            <div key={category.id} className="node-category">
              <div 
                className="category-title"
                style={{ color: category.color }}
              >
                {category.name}
              </div>
              
              {categoryNodes.map((node, index) => (
                <div
                  key={`${node.type}-${index}`}
                  className="node-item"
                  draggable
                  onDragStart={(e) => onDragStart(e, node.type)}
                >
                  <div 
                    className="node-icon"
                    style={{ 
                      background: getCategoryColor(category.id),
                      color: getIconColor(category.id)
                    }}
                  >
                    {node.icon}
                  </div>
                  <div className="node-info">
                    <div className="node-name">{node.label}</div>
                    <div className="node-desc">{node.description}</div>
                  </div>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 辅助函数：获取分类背景色
function getCategoryColor(categoryId: string): string {
  const colors: Record<string, string> = {
    trigger: '#D1FAE5',
    action: '#DBEAFE',
    condition: '#FEF3C7',
    utility: '#E9D5FF',
  };
  return colors[categoryId] || '#F3F4F6';
}

// 辅助函数：获取图标颜色
function getIconColor(categoryId: string): string {
  const colors: Record<string, string> = {
    trigger: '#059669',
    action: '#1D4ED8',
    condition: '#D97706',
    utility: '#7C3AED',
  };
  return colors[categoryId] || '#6B7280';
}
