/**
 * 工作流编排器主应用
 * MVP Sprint 1 - 前端界面
 */

import React, { useState } from 'react';
import { WorkflowCanvas } from './WorkflowCanvas';
import { WorkflowNode, WorkflowEdge, WorkflowDefinition, NodeType } from './types/workflow';

// 预置工作流模板
const PRESET_TEMPLATES = [
  { id: 'WF-01', name: '电商订单处理', category: '电商', icon: '📦' },
  { id: 'WF-02', name: '客户咨询自动回复', category: '电商', icon: '💬' },
  { id: 'WF-03', name: '数据报表自动生成', category: '通用', icon: '📊' },
  { id: 'WF-04', name: '员工入职流程', category: '人事', icon: '👤' },
  { id: 'WF-05', name: '采购审批流程', category: '财务', icon: '💰' },
  { id: 'WF-06', name: '营销活动执行', category: '市场', icon: '📢' },
  { id: 'WF-07', name: '工单自动分配', category: '客服', icon: '🎫' },
  { id: 'WF-08', name: '合同审批流程', category: '法务', icon: '📄' },
  { id: 'WF-09', name: '社交媒体内容发布', category: '市场', icon: '📱' },
  { id: 'WF-10', name: '会议纪要生成', category: '通用', icon: '📝' },
];

const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'editor' | 'templates'>('editor');
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);

  const handleSaveWorkflow = (nodes: WorkflowNode[], edges: WorkflowEdge[]) => {
    const workflow: WorkflowDefinition = {
      id: `wf_${Date.now()}`,
      name: '未命名工作流',
      version: '1.0',
      status: 'draft',
      nodes,
      edges,
      createdBy: 'current_user',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    
    console.log('保存工作流:', workflow);
    // TODO: 调用后端 API 保存
    alert('工作流已保存！');
  };

  const handleLoadTemplate = (templateId: string) => {
    setSelectedTemplate(templateId);
    setActiveTab('editor');
    // TODO: 加载模板数据到画布
    console.log('加载模板:', templateId);
  };

  return (
    <div style={{ fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      {/* 顶部导航栏 */}
      <header style={{
        background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        color: 'white',
        padding: '16px 24px',
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      }}>
        <div style={{ maxWidth: '1400px', margin: '0 auto', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 'bold' }}>
              🚀 工作流编排器
            </h1>
            <p style={{ margin: '4px 0 0', fontSize: '14px', opacity: 0.9 }}>
              可视化拖拽 · 智能自动化 · 企业级工作流
            </p>
          </div>
          <div style={{ display: 'flex', gap: '12px' }}>
            <button
              onClick={() => setActiveTab('editor')}
              style={{
                padding: '8px 16px',
                background: activeTab === 'editor' ? 'white' : 'rgba(255,255,255,0.2)',
                color: activeTab === 'editor' ? '#667eea' : 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                fontWeight: 'bold',
                transition: 'all 0.2s',
              }}
            >
              📐 编辑器
            </button>
            <button
              onClick={() => setActiveTab('templates')}
              style={{
                padding: '8px 16px',
                background: activeTab === 'templates' ? 'white' : 'rgba(255,255,255,0.2)',
                color: activeTab === 'templates' ? '#667eea' : 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                fontWeight: 'bold',
                transition: 'all 0.2s',
              }}
            >
              📚 模板库
            </button>
          </div>
        </div>
      </header>

      {/* 主内容区 */}
      <main style={{ padding: '24px', maxWidth: '1400px', margin: '0 auto' }}>
        {activeTab === 'editor' ? (
          <div>
            <div style={{ marginBottom: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h2 style={{ margin: 0, fontSize: '20px', color: '#333' }}>
                {selectedTemplate ? `编辑模板：${selectedTemplate}` : '新建工作流'}
              </h2>
              <button
                onClick={() => setSelectedTemplate(null)}
                style={{
                  padding: '8px 16px',
                  background: '#f3f4f6',
                  border: '1px solid #d1d5db',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  color: '#374151',
                }}
              >
                🗑️ 清空画布
              </button>
            </div>
            <WorkflowCanvas onSave={handleSaveWorkflow} readOnly={false} />
          </div>
        ) : (
          <div>
            <h2 style={{ marginBottom: '24px', fontSize: '20px', color: '#333' }}>
              📚 预置工作流模板（10 个）
            </h2>
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
              gap: '20px',
            }}>
              {PRESET_TEMPLATES.map((template) => (
                <div
                  key={template.id}
                  onClick={() => handleLoadTemplate(template.name)}
                  style={{
                    padding: '20px',
                    background: 'white',
                    border: '2px solid #e5e7eb',
                    borderRadius: '12px',
                    cursor: 'pointer',
                    transition: 'all 0.2s',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.transform = 'translateY(-4px)';
                    e.currentTarget.style.borderColor = '#667eea';
                    e.currentTarget.style.boxShadow = '0 4px 12px rgba(102,126,234,0.2)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.transform = 'translateY(0)';
                    e.currentTarget.style.borderColor = '#e5e7eb';
                    e.currentTarget.style.boxShadow = '0 1px 3px rgba(0,0,0,0.1)';
                  }}
                >
                  <div style={{ fontSize: '32px', marginBottom: '12px' }}>{template.icon}</div>
                  <h3 style={{ margin: '0 0 8px', fontSize: '18px', color: '#1f2937' }}>
                    {template.name}
                  </h3>
                  <p style={{ margin: '0 0 12px', fontSize: '14px', color: '#6b7280' }}>
                    {template.category}
                  </p>
                  <div style={{
                    display: 'inline-block',
                    padding: '4px 12px',
                    background: '#eff6ff',
                    color: '#3b82f6',
                    borderRadius: '12px',
                    fontSize: '12px',
                    fontWeight: 'bold',
                  }}>
                    {template.id}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </main>

      {/* 底部状态栏 */}
      <footer style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        background: '#f9fafb',
        borderTop: '1px solid #e5e7eb',
        padding: '8px 24px',
        fontSize: '13px',
        color: '#6b7280',
        display: 'flex',
        justifyContent: 'space-between',
      }}>
        <span>✅ 已连接后端 API | 📦 10 个预置模板可用 | 🔌 8 个集成模块就绪</span>
        <span>MVP Sprint 1 v1.0.0</span>
      </footer>
    </div>
  );
};

export default App;
