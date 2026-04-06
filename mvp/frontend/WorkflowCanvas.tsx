/**
 * 工作流画布编辑器 - 基于 React Flow
 * 支持拖拽节点、连线、配置属性
 */

import React, { useState, useCallback } from 'react';
import ReactFlow, {
  Node,
  Edge,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  NodeTypes,
} from 'reactflow';
import 'reactflow/dist/style.css';

// 节点类型定义
export type NodeType = 'http' | 'condition' | 'loop' | 'transform' | 'notification';

// 初始节点类型配置
const nodeTypeConfig: Record<NodeType, { label: string; color: string; icon: string }> = {
  http: { label: 'HTTP 请求', color: '#3b82f6', icon: '🌐' },
  condition: { label: '条件判断', color: '#f59e0b', icon: '⚡' },
  loop: { label: '循环', color: '#8b5cf6', icon: '🔄' },
  transform: { label: '数据转换', color: '#10b981', icon: '🔧' },
  notification: { label: '通知', color: '#ef4444', icon: '📧' },
};

// 自定义节点组件
const CustomNode = ({ data }: any) => {
  const config = nodeTypeConfig[data.type as NodeType] || nodeTypeConfig.http;
  
  return (
    <div style={{
      padding: '10px',
      borderRadius: '8px',
      border: `2px solid ${config.color}`,
      background: 'white',
      minWidth: '150px',
      boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
    }}>
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        gap: '8px',
        fontWeight: 'bold',
        marginBottom: '8px'
      }}>
        <span>{config.icon}</span>
        <span>{data.label || config.label}</span>
      </div>
      {data.description && (
        <div style={{ fontSize: '12px', color: '#666' }}>
          {data.description}
        </div>
      )}
    </div>
  );
};

const nodeTypes: NodeTypes = {
  custom: CustomNode,
};

// 工作流画布组件
interface WorkflowCanvasProps {
  onSave?: (nodes: Node[], edges: Edge[]) => void;
  readOnly?: boolean;
}

export const WorkflowCanvas: React.FC<WorkflowCanvasProps> = ({ onSave, readOnly = false }) => {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);

  // 处理连线
  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge({ ...params, animated: true }, eds)),
    [setEdges]
  );

  // 节点被选中
  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (!readOnly) {
        setSelectedNode(node);
      }
    },
    [readOnly]
  );

  // 添加节点
  const addNode = useCallback((type: NodeType) => {
    if (readOnly) return;
    
    const config = nodeTypeConfig[type];
    const newNode: Node = {
      id: `node_${Date.now()}`,
      type: 'custom',
      position: { x: Math.random() * 400, y: Math.random() * 300 },
      data: {
        label: config.label,
        type,
        description: `${config.label}节点`,
      },
    };
    setNodes((nds) => [...nds, newNode]);
  }, [readOnly, setNodes]);

  // 保存工作流
  const handleSave = useCallback(() => {
    if (onSave) {
      onSave(nodes, edges);
    }
  }, [nodes, edges, onSave]);

  return (
    <div style={{ 
      display: 'flex', 
      height: '600px', 
      border: '1px solid #e5e7eb',
      borderRadius: '8px'
    }}>
      {/* 节点库面板 */}
      {!readOnly && (
        <div style={{ 
          width: '200px', 
          padding: '16px', 
          background: '#f9fafb',
          borderRight: '1px solid #e5e7eb'
        }}>
          <h3 style={{ marginBottom: '16px', fontSize: '16px' }}>节点库</h3>
          {Object.entries(nodeTypeConfig).map(([type, config]) => (
            <button
              key={type}
              onClick={() => addNode(type as NodeType)}
              style={{
                width: '100%',
                padding: '10px',
                marginBottom: '8px',
                background: 'white',
                border: `2px solid ${config.color}`,
                borderRadius: '6px',
                cursor: 'pointer',
                textAlign: 'left',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
              }}
            >
              <span>{config.icon}</span>
              <span>{config.label}</span>
            </button>
          ))}
        </div>
      )}

      {/* 画布区域 */}
      <div style={{ flex: 1 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={onNodeClick}
          nodeTypes={nodeTypes}
          fitView
          snapToGrid
          snapGrid={[15, 15]}
          defaultEdgeOptions={{
            type: 'smoothstep',
            animated: true,
          }}
        >
          <Controls />
          <Background color="#e5e7eb" gap={16} />
        </ReactFlow>
      </div>

      {/* 属性配置面板 */}
      {!readOnly && selectedNode && (
        <div style={{ 
          width: '280px', 
          padding: '16px', 
          background: '#f9fafb',
          borderLeft: '1px solid #e5e7eb',
          overflowY: 'auto'
        }}>
          <h3 style={{ marginBottom: '16px', fontSize: '16px' }}>属性配置</h3>
          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', marginBottom: '4px', fontSize: '14px' }}>
              节点名称
            </label>
            <input
              type="text"
              value={selectedNode.data.label || ''}
              onChange={(e) => {
                setNodes((nds) =>
                  nds.map((node) =>
                    node.id === selectedNode.id
                      ? { ...node, data: { ...node.data, label: e.target.value } }
                      : node
                  )
                );
                setSelectedNode({
                  ...selectedNode,
                  data: { ...selectedNode.data, label: e.target.value },
                });
              }}
              style={{
                width: '100%',
                padding: '8px',
                border: '1px solid #d1d5db',
                borderRadius: '4px',
              }}
            />
          </div>
          
          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', marginBottom: '4px', fontSize: '14px' }}>
              节点描述
            </label>
            <textarea
              value={selectedNode.data.description || ''}
              onChange={(e) => {
                setNodes((nds) =>
                  nds.map((node) =>
                    node.id === selectedNode.id
                      ? { ...node, data: { ...node.data, description: e.target.value } }
                      : node
                  )
                );
                setSelectedNode({
                  ...selectedNode,
                  data: { ...selectedNode.data, description: e.target.value },
                });
              }}
              rows={4}
              style={{
                width: '100%',
                padding: '8px',
                border: '1px solid #d1d5db',
                borderRadius: '4px',
                resize: 'vertical',
              }}
            />
          </div>

          <button
            onClick={() => {
              setNodes((nds) => nds.filter((n) => n.id !== selectedNode.id));
              setSelectedNode(null);
            }}
            style={{
              width: '100%',
              padding: '8px',
              background: '#ef4444',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
            }}
          >
            删除节点
          </button>
        </div>
      )}

      {/* 保存按钮 */}
      {!readOnly && onSave && (
        <div style={{
          position: 'absolute',
          top: '16px',
          right: '16px',
          zIndex: 1000,
        }}>
          <button
            onClick={handleSave}
            style={{
              padding: '10px 20px',
              background: '#3b82f6',
              color: 'white',
              border: 'none',
              borderRadius: '6px',
              cursor: 'pointer',
              fontWeight: 'bold',
              boxShadow: '0 2px 4px rgba(0,0,0,0.2)',
            }}
          >
            💾 保存工作流
          </button>
        </div>
      )}
    </div>
  );
};

export default WorkflowCanvas;
