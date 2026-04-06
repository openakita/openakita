import React, { useCallback, useState, useRef } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useNodesState,
  useEdgesState,
  Controls,
  Background,
  MiniMap,
  Connection,
  Edge,
  Node,
  Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './WorkflowCanvas.css';

// 初始节点数据（从左侧面板拖拽创建）
const initialNodes: Node[] = [
  {
    id: '1',
    type: 'trigger',
    position: { x: 250, y: 100 },
    data: { 
      label: '定时触发',
      description: '每天 09:00 执行',
      icon: '🕐'
    },
  },
];

// 初始连接线
const initialEdges: Edge[] = [];

interface WorkflowCanvasProps {
  onNodeSelect?: (node: Node) => void;
  onNodesChange?: (nodes: Node[]) => void;
}

function Flow() {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const reactFlowWrapper = useRef<HTMLDivElement>(null);

  // 处理连接线
  const onConnect = useCallback(
    (params: Connection) => {
      setEdges((eds) => addEdge({
        ...params,
        type: 'smoothstep',
        animated: false,
        style: { stroke: '#9CA3AF', strokeWidth: 2 },
      }, eds));
    },
    [setEdges],
  );

  // 节点点击事件
  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      setSelectedNode(node);
      console.log('Node clicked:', node);
    },
    [],
  );

  // 画布点击事件（取消选中）
  const onPaneClick = useCallback(() => {
    setSelectedNode(null);
    console.log('Pane clicked - cleared selection');
  }, []);

  // 节点拖拽创建（从左侧面板）
  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      const type = event.dataTransfer.getData('application/reactflow');
      if (!type || !reactFlowWrapper.current) {
        return;
      }

      const reactFlowBounds = reactFlowWrapper.current.getBoundingClientRect();
      const position = {
        x: event.clientX - reactFlowBounds.left - 120, // 节点宽度的一半
        y: event.clientY - reactFlowBounds.top - 40,
      };

      const newNode: Node = {
        id: `${type}-${Date.now()}`,
        type,
        position,
        data: { 
          label: getNodeTypeLabel(type),
          description: getNodeTypeDescription(type),
          icon: getNodeTypeIcon(type)
        },
      };

      setNodes((nds) => nds.concat(newNode));
    },
    [setNodes],
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  return (
    <div className="workflow-canvas-wrapper" ref={reactFlowWrapper}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        onDrop={onDrop}
        onDragOver={onDragOver}
        fitView
        snapToGrid
        snapGrid={[20, 20]}
        defaultEdgeOptions={{
          type: 'smoothstep',
          style: { stroke: '#9CA3AF', strokeWidth: 2 },
        }}
      >
        <Background color="#E5E7EB" gap={20} />
        <Controls position="bottom-right" />
        <MiniMap 
          position="bottom-right"
          nodeColor={(node) => {
            switch (node.type) {
              case 'trigger': return '#10B981';
              case 'action': return '#2563EB';
              case 'condition': return '#F59E0B';
              case 'loop': return '#7C3AED';
              default: return '#EF4444';
            }
          }}
          maskColor="rgb(249, 250, 251, 0.8)"
        />
        
        {/* 空状态提示 */}
        {nodes.length === 0 && (
          <Panel position="top-center" className="empty-state-panel">
            <div className="empty-state">
              <div className="empty-state-icon">📋</div>
              <div className="empty-state-title">从左侧面板拖拽节点到画布</div>
              <div className="empty-state-desc">开始构建你的工作流</div>
            </div>
          </Panel>
        )}
      </ReactFlow>
    </div>
  );
}

// 辅助函数：获取节点类型标签
function getNodeTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    trigger: '触发器',
    action: '动作',
    condition: '条件',
    loop: '循环',
    transform: '数据转换',
  };
  return labels[type] || '节点';
}

// 辅助函数：获取节点类型描述
function getNodeTypeDescription(type: string): string {
  const descriptions: Record<string, string> = {
    trigger: '工作流的起点',
    action: '执行具体操作',
    condition: '条件判断分支',
    loop: '循环执行',
    transform: '数据格式转换',
  };
  return descriptions[type] || '';
}

// 辅助函数：获取节点类型图标
function getNodeTypeIcon(type: string): string {
  const icons: Record<string, string> = {
    trigger: '🕐',
    action: '⚡',
    condition: '❓',
    loop: '🔄',
    transform: '🔀',
  };
  return icons[type] || '📦';
}

// 导出带 Provider 的组件
export default function WorkflowCanvas(props: WorkflowCanvasProps) {
  return (
    <ReactFlowProvider>
      <Flow />
    </ReactFlowProvider>
  );
}
