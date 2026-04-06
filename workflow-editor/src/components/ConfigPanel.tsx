import React, { useState, useEffect } from 'react';
import { Node } from '@xyflow/react';
import './ConfigPanel.css';

interface ConfigPanelProps {
  node: Node | null;
  onClose: () => void;
  onSave: (nodeId: string, data: any) => void;
}

export default function ConfigPanel({ node, onClose, onSave }: ConfigPanelProps) {
  const [config, setConfig] = useState<Record<string, any>>({});
  const [hasChanges, setHasChanges] = useState(false);

  useEffect(() => {
    if (node) {
      setConfig(node.data.config || {});
      setHasChanges(false);
    }
  }, [node]);

  if (!node) {
    return (
      <div className="config-panel">
        <div className="config-empty">
          <div className="empty-icon">📝</div>
          <div className="empty-text">点击节点进行配置</div>
        </div>
      </div>
    );
  }

  const handleConfigChange = (key: string, value: any) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
    setHasChanges(true);
  };

  const handleSave = () => {
    onSave(node.id, config);
    setHasChanges(false);
  };

  const renderConfigFields = () => {
    const nodeType = node.type;
    
    switch (nodeType) {
      case 'trigger':
        return (
          <>
            <div className="form-group">
              <label className="form-label">触发类型</label>
              <select
                className="form-select"
                value={config.triggerType || 'cron'}
                onChange={(e) => handleConfigChange('triggerType', e.target.value)}
              >
                <option value="cron">定时触发</option>
                <option value="event">事件触发</option>
                <option value="webhook">Webhook</option>
                <option value="manual">手动触发</option>
              </select>
            </div>
            
            {(config.triggerType === 'cron' || !config.triggerType) && (
              <div className="form-group">
                <label className="form-label">Cron 表达式</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="0 9 * * *"
                  value={config.cronExpression || ''}
                  onChange={(e) => handleConfigChange('cronExpression', e.target.value)}
                />
                <div className="form-hint">每天 09:00 执行：0 9 * * *</div>
              </div>
            )}
          </>
        );

      case 'action':
        return (
          <>
            <div className="form-group">
              <label className="form-label">动作类型</label>
              <select
                className="form-select"
                value={config.actionType || 'email'}
                onChange={(e) => handleConfigChange('actionType', e.target.value)}
              >
                <option value="email">发送邮件</option>
                <option value="report">生成报表</option>
                <option value="data">数据整理</option>
                <option value="api">API 调用</option>
              </select>
            </div>

            {config.actionType === 'email' && (
              <>
                <div className="form-group">
                  <label className="form-label">收件人</label>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="{{customer_email}}"
                    value={config.recipient || ''}
                    onChange={(e) => handleConfigChange('recipient', e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">邮件主题</label>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="订单确认"
                    value={config.subject || ''}
                    onChange={(e) => handleConfigChange('subject', e.target.value)}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">邮件内容</label>
                  <textarea
                    className="form-textarea"
                    rows={4}
                    placeholder="邮件正文内容..."
                    value={config.content || ''}
                    onChange={(e) => handleConfigChange('content', e.target.value)}
                  />
                </div>
              </>
            )}
          </>
        );

      case 'condition':
        return (
          <>
            <div className="form-group">
              <label className="form-label">条件字段</label>
              <input
                type="text"
                className="form-input"
                placeholder="amount"
                value={config.field || ''}
                onChange={(e) => handleConfigChange('field', e.target.value)}
              />
            </div>
            <div className="form-group">
              <label className="form-label">操作符</label>
              <select
                className="form-select"
                value={config.operator || '>'}
                onChange={(e) => handleConfigChange('operator', e.target.value)}
              >
                <option value=">">大于</option>
                <option value=">=">大于等于</option>
                <option value="<">小于</option>
                <option value="<=">小于等于</option>
                <option value="==">等于</option>
                <option value="!=">不等于</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">阈值</label>
              <input
                type="text"
                className="form-input"
                placeholder="1000"
                value={config.threshold || ''}
                onChange={(e) => handleConfigChange('threshold', e.target.value)}
              />
            </div>
          </>
        );

      case 'loop':
        return (
          <>
            <div className="form-group">
              <label className="form-label">循环类型</label>
              <select
                className="form-select"
                value={config.loopType || 'foreach'}
                onChange={(e) => handleConfigChange('loopType', e.target.value)}
              >
                <option value="foreach">遍历列表</option>
                <option value="while">条件循环</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">列表变量</label>
              <input
                type="text"
                className="form-input"
                placeholder="{{order_list}}"
                value={config.listVariable || ''}
                onChange={(e) => handleConfigChange('listVariable', e.target.value)}
              />
            </div>
          </>
        );

      case 'transform':
        return (
          <>
            <div className="form-group">
              <label className="form-label">字段映射</label>
              <textarea
                className="form-textarea"
                rows={5}
                placeholder={'order_id → 订单编号\namount → 金额'}
                value={config.fieldMapping || ''}
                onChange={(e) => handleConfigChange('fieldMapping', e.target.value)}
              />
            </div>
          </>
        );

      default:
        return <div className="form-hint">暂无配置项</div>;
    }
  };

  return (
    <div className="config-panel">
      <div className="config-panel-header">
        <div className="config-title">
          <span className="node-icon">{node.data.icon || '📦'}</span>
          <span>{node.data.label || '节点配置'}</span>
        </div>
        <button className="close-btn" onClick={onClose}>✕</button>
      </div>

      <div className="config-panel-content">
        {/* 基础配置 */}
        <div className="config-section">
          <div className="section-title">基础配置</div>
          <div className="form-group">
            <label className="form-label">节点名称</label>
            <input
              type="text"
              className="form-input"
              value={node.data.label || ''}
              onChange={(e) => {
                onSave(node.id, { ...node.data, label: e.target.value });
              }}
            />
          </div>
          <div className="form-group">
            <label className="form-label">描述</label>
            <textarea
              className="form-textarea"
              rows={2}
              value={node.data.description || ''}
              onChange={(e) => {
                onSave(node.id, { ...node.data, description: e.target.value });
              }}
            />
          </div>
        </div>

        {/* 高级配置 */}
        <div className="config-section">
          <div className="section-title">高级配置</div>
          {renderConfigFields()}
          
          <div className="form-group">
            <label className="form-label">
              <input
                type="checkbox"
                checked={config.enableRetry || false}
                onChange={(e) => handleConfigChange('enableRetry', e.target.checked)}
              />
              {' '}启用重试
            </label>
          </div>
          
          {config.enableRetry && (
            <>
              <div className="form-group">
                <label className="form-label">重试次数</label>
                <input
                  type="number"
                  className="form-input"
                  value={config.retryCount || 3}
                  onChange={(e) => handleConfigChange('retryCount', parseInt(e.target.value))}
                />
              </div>
              <div className="form-group">
                <label className="form-label">超时时间 (秒)</label>
                <input
                  type="number"
                  className="form-input"
                  value={config.timeout || 30}
                  onChange={(e) => handleConfigChange('timeout', parseInt(e.target.value))}
                />
              </div>
            </>
          )}
        </div>
      </div>

      <div className="config-panel-footer">
        <button className="btn btn-secondary" onClick={onClose}>取消</button>
        <button 
          className={`btn btn-primary ${hasChanges ? 'has-changes' : ''}`}
          onClick={handleSave}
          disabled={!hasChanges}
        >
          保存
        </button>
      </div>
    </div>
  );
}
