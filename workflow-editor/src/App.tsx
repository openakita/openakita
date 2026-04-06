import { useState } from 'react'
import { Node, Edge } from '@xyflow/react'
import WorkflowCanvas from './components/WorkflowCanvas'
import NodePanel from './components/NodePanel'
import ConfigPanel from './components/ConfigPanel'
import './App.css'

function App() {
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<Edge[]>([])

  const handleNodeSelect = (node: Node) => {
    setSelectedNode(node)
  }

  const handleNodesChange = (newNodes: Node[]) => {
    setNodes(newNodes)
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-left">
          <h1 className="header-title">🔄 工作流编辑器</h1>
          <span className="header-subtitle">MVP 原型 - React Flow</span>
        </div>
        <div className="header-right">
          <div className="stats">
            <span className="stat-item">
              <span className="stat-label">节点:</span>
              <span className="stat-value">{nodes.length}</span>
            </span>
            <span className="stat-item">
              <span className="stat-label">连接:</span>
              <span className="stat-value">{edges.length}</span>
            </span>
          </div>
          <button className="btn btn-primary">▶️ 运行</button>
          <button className="btn btn-secondary">💾 保存</button>
        </div>
      </header>
      
      <div className="app-content">
        {/* 左侧组件库 */}
        <aside className="sidebar-left">
          <NodePanel />
        </aside>
        
        {/* 中间画布 */}
        <main className="canvas-area">
          <WorkflowCanvas 
            onNodeSelect={handleNodeSelect}
            onNodesChange={handleNodesChange}
          />
        </main>
        
        {/* 右侧配置面板 */}
        <aside className="sidebar-right">
          <ConfigPanel node={selectedNode} />
        </aside>
      </div>
    </div>
  )
}

export default App
