"""
工作流引擎 - MVP 版本
支持预置模板的导入、执行和监控
"""

import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from enum import Enum

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WorkflowStatus(Enum):
    """工作流状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class WorkflowInstance:
    """工作流实例"""
    instance_id: str
    template_id: str
    status: WorkflowStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    current_step: int = 0
    result: Any = None
    error: Optional[str] = None


class WorkflowEngine:
    """工作流引擎"""
    
    def __init__(self, templates_dir: str = "workflow-templates"):
        self.templates_dir = Path(templates_dir)
        self.templates: Dict[str, Dict] = {}
        self.instances: Dict[str, WorkflowInstance] = {}
        self._load_templates()
    
    def _load_templates(self):
        """加载所有模板"""
        logger.info(f"从 {self.templates_dir} 加载模板...")
        
        if not self.templates_dir.exists():
            logger.warning(f"模板目录不存在：{self.templates_dir}")
            return
        
        for template_file in self.templates_dir.glob("*.md"):
            if template_file.name.startswith("README") or template_file.name.startswith("实施"):
                continue
            
            try:
                template = self._parse_template(template_file)
                if template:
                    template_id = template.get("template_id", template_file.stem)
                    self.templates[template_id] = template
                    logger.info(f"✓ 加载模板：{template_id} - {template.get('name', 'Unknown')}")
            except Exception as e:
                logger.error(f"✗ 加载模板失败 {template_file}: {e}")
        
        logger.info(f"模板加载完成，共 {len(self.templates)} 个模板")
    
    def _parse_template(self, file_path: Path) -> Optional[Dict]:
        """解析模板文件"""
        content = file_path.read_text(encoding='utf-8')
        
        # 简单解析 Markdown 模板
        template = {
            "file_path": str(file_path),
            "template_id": file_path.stem,
            "name": file_path.stem,
        }
        
        # 提取模板 ID
        if "**模板 ID**:" in content:
            for line in content.split('\n'):
                if "**模板 ID**:" in line:
                    template_id = line.split(":")[1].strip()
                    template["template_id"] = template_id
                    break
        
        # 提取名称
        if content.startswith("# "):
            name = content.split('\n')[0].replace("# ", "").replace("工作流模板：", "")
            template["name"] = name.strip()
        
        # 提取分类
        if "**分类**:" in content:
            for line in content.split('\n'):
                if "**分类**:" in line:
                    category = line.split(":")[1].strip()
                    template["category"] = category
                    break
        
        # 提取执行步骤
        template["steps"] = self._extract_steps(content)
        
        # 提取触发条件
        template["triggers"] = self._extract_triggers(content)
        
        # 提取集成系统
        template["integrations"] = self._extract_integrations(content)
        
        return template
    
    def _extract_steps(self, content: str) -> List[Dict]:
        """提取执行步骤"""
        steps = []
        in_steps = False
        
        for line in content.split('\n'):
            if "## 三、执行步骤" in line or "### 执行步骤" in line:
                in_steps = True
                continue
            
            if in_steps:
                if line.startswith("## "):
                    break
                
                # 解析表格行：| 1 | 验证订单信息 | 订单系统 | 自动 |
                if "|" in line and line.count("|") >= 4:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 5 and parts[1].isdigit():
                        steps.append({
                            "step_number": int(parts[1]),
                            "action": parts[2],
                            "system": parts[3],
                            "duration": parts[4]
                        })
        
        return steps
    
    def _extract_triggers(self, content: str) -> List[str]:
        """提取触发条件"""
        triggers = []
        in_triggers = False
        
        for line in content.split('\n'):
            if "## 二、触发条件" in line or "### 触发条件" in line:
                in_triggers = True
                continue
            
            if in_triggers:
                if line.startswith("## "):
                    break
                
                if "✅" in line:
                    trigger = line.replace("✅", "").strip()
                    triggers.append(trigger)
        
        return triggers
    
    def _extract_integrations(self, content: str) -> List[str]:
        """提取集成系统"""
        integrations = []
        in_integrations = False
        
        for line in content.split('\n'):
            if "## 四、系统集成" in line or "### 系统集成" in line:
                in_integrations = True
                continue
            
            if in_integrations:
                if line.startswith("## "):
                    break
                
                if "🔌" in line:
                    integration = line.replace("🔌", "").strip()
                    integrations.append(integration)
        
        return integrations
    
    def list_templates(self) -> List[Dict]:
        """列出所有可用模板"""
        return [
            {
                "template_id": t["template_id"],
                "name": t["name"],
                "category": t.get("category", "Unknown"),
                "steps_count": len(t.get("steps", [])),
                "triggers": t.get("triggers", []),
            }
            for t in self.templates.values()
        ]
    
    def get_template(self, template_id: str) -> Optional[Dict]:
        """获取模板详情"""
        return self.templates.get(template_id)
    
    def execute(self, template_id: str, parameters: Dict = None) -> WorkflowInstance:
        """执行工作流"""
        if template_id not in self.templates:
            raise ValueError(f"模板不存在：{template_id}")
        
        template = self.templates[template_id]
        instance_id = f"wf_{template_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        instance = WorkflowInstance(
            instance_id=instance_id,
            template_id=template_id,
            status=WorkflowStatus.PENDING,
            created_at=datetime.now(),
        )
        
        self.instances[instance_id] = instance
        logger.info(f"创建工作流实例：{instance_id}")
        
        # 异步执行（简化版本：同步执行）
        return self._run_instance(instance, template, parameters or {})
    
    def _run_instance(self, instance: WorkflowInstance, template: Dict, parameters: Dict) -> WorkflowInstance:
        """运行工作流实例"""
        instance.status = WorkflowStatus.RUNNING
        instance.started_at = datetime.now()
        
        logger.info(f"开始执行工作流：{instance.instance_id} - {template['name']}")
        
        try:
            steps = template.get("steps", [])
            
            for i, step in enumerate(steps):
                instance.current_step = i + 1
                logger.info(f"执行步骤 {i+1}/{len(steps)}: {step['action']}")
                
                # 模拟执行（MVP 版本）
                self._execute_step(step, parameters)
            
            instance.status = WorkflowStatus.COMPLETED
            instance.completed_at = datetime.now()
            instance.result = {
                "message": f"工作流执行完成：{template['name']}",
                "steps_executed": len(steps),
                "duration": (instance.completed_at - instance.started_at).total_seconds()
            }
            
            logger.info(f"✓ 工作流执行完成：{instance.instance_id}")
            
        except Exception as e:
            instance.status = WorkflowStatus.FAILED
            instance.error = str(e)
            logger.error(f"✗ 工作流执行失败：{instance.instance_id} - {e}")
        
        return instance
    
    def _execute_step(self, step: Dict, parameters: Dict):
        """执行单个工作流步骤（MVP 版本：模拟执行）"""
        # TODO: 集成真实 API
        # 目前仅模拟执行
        import time
        time.sleep(0.1)  # 模拟执行时间
    
    def get_instance(self, instance_id: str) -> Optional[WorkflowInstance]:
        """获取工作流实例状态"""
        return self.instances.get(instance_id)
    
    def export_template(self, template_id: str, output_path: str):
        """导出模板为 JSON"""
        if template_id not in self.templates:
            raise ValueError(f"模板不存在：{template_id}")
        
        template = self.templates[template_id]
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(template, f, ensure_ascii=False, indent=2, default=str)
        
        logger.info(f"模板已导出：{output_path}")


def main():
    """主函数 - 演示用法"""
    print("=" * 60)
    print("工作流引擎 - MVP 版本")
    print("=" * 60)
    
    # 初始化引擎
    engine = WorkflowEngine(templates_dir="workflow-templates")
    
    # 列出所有模板
    print("\n📋 可用模板列表:")
    print("-" * 60)
    templates = engine.list_templates()
    for t in templates:
        print(f"  {t['template_id']}: {t['name']} ({t['category']}) - {t['steps_count']} 步骤")
    
    print(f"\n共 {len(templates)} 个模板")
    
    # 执行示例
    if templates:
        print("\n🚀 执行示例工作流...")
        print("-" * 60)
        first_template = templates[0]['template_id']
        instance = engine.execute(first_template)
        
        print(f"实例 ID: {instance.instance_id}")
        print(f"状态：{instance.status.value}")
        print(f"创建时间：{instance.created_at}")
        if instance.completed_at:
            print(f"完成时间：{instance.completed_at}")
            print(f"执行结果：{instance.result}")
        if instance.error:
            print(f"错误：{instance.error}")
    
    print("\n" + "=" * 60)
    print("演示完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
