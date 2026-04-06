"""
API 集成层 - 统一 API 适配器
支持 Mock/真实环境切换，对接全栈 A 的 API 集成层
"""

import json
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum
import time

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class Environment(Enum):
    """运行环境"""
    MOCK = "mock"
    REAL = "real"


@dataclass
class APIResponse:
    """API 响应"""
    success: bool
    status_code: int
    data: Any
    message: str
    latency_ms: float


class APIAdapter:
    """
    API 适配器 - 统一接口层
    支持 Mock/真实环境无缝切换
    """
    
    def __init__(self, environment: Environment = Environment.MOCK):
        self.environment = environment
        self.base_url = self._get_base_url()
        self.api_key = self._get_api_key()
        logger.info(f"API 适配器初始化完成 - 环境：{environment.value}")
    
    def _get_base_url(self) -> str:
        """获取基础 URL"""
        if self.environment == Environment.MOCK:
            return "http://localhost:8000/mock"
        else:
            # TODO: 从配置文件读取真实 API 地址
            return "http://api.openakita.com/v1"
    
    def _get_api_key(self) -> Optional[str]:
        """获取 API Key"""
        if self.environment == Environment.MOCK:
            return "mock-key-12345"
        else:
            # TODO: 从环境变量读取
            import os
            return os.getenv("OPENAKITA_API_KEY")
    
    def _mock_response(self, endpoint: str, params: Dict = None) -> APIResponse:
        """生成 Mock 响应"""
        logger.info(f"[MOCK] 请求端点：{endpoint}, 参数：{params}")
        
        # 模拟网络延迟
        time.sleep(0.1)
        
        # 根据端点返回不同的 Mock 数据
        mock_data = {
            "/workflow/templates": {
                "templates": [
                    {"id": "WF-01", "name": "电商订单处理", "category": "电商零售"},
                    {"id": "WF-02", "name": "客户咨询自动回复", "category": "客服"},
                    {"id": "WF-03", "name": "会议纪要生成与分发", "category": "办公"},
                ]
            },
            "/workflow/execute": {
                "instance_id": "wf_mock_12345",
                "status": "running",
                "message": "工作流已启动"
            },
            "/workflow/status": {
                "instance_id": "wf_mock_12345",
                "status": "completed",
                "progress": 100,
                "result": {"success": True}
            },
            "/integrations/list": {
                "integrations": [
                    {"id": "wechat", "name": "企业微信", "status": "active"},
                    {"id": "dingtalk", "name": "钉钉", "status": "active"},
                    {"id": "taobao", "name": "淘宝", "status": "inactive"},
                ]
            },
        }
        
        return APIResponse(
            success=True,
            status_code=200,
            data=mock_data.get(endpoint, {"message": "Mock response"}),
            message="Mock 响应",
            latency_ms=100.0
        )
    
    def _real_request(self, method: str, endpoint: str, params: Dict = None) -> APIResponse:
        """发送真实 API 请求"""
        import requests
        
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        start_time = time.time()
        
        try:
            if method == "GET":
                response = requests.get(url, params=params, headers=headers, timeout=30)
            elif method == "POST":
                response = requests.post(url, json=params, headers=headers, timeout=30)
            elif method == "PUT":
                response = requests.put(url, json=params, headers=headers, timeout=30)
            elif method == "DELETE":
                response = requests.delete(url, headers=headers, timeout=30)
            else:
                raise ValueError(f"不支持的 HTTP 方法：{method}")
            
            latency_ms = (time.time() - start_time) * 1000
            
            return APIResponse(
                success=response.status_code < 400,
                status_code=response.status_code,
                data=response.json() if response.content else None,
                message=response.reason,
                latency_ms=latency_ms
            )
            
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            logger.error(f"API 请求失败：{e}")
            
            return APIResponse(
                success=False,
                status_code=500,
                data=None,
                message=str(e),
                latency_ms=latency_ms
            )
    
    def request(self, method: str, endpoint: str, params: Dict = None) -> APIResponse:
        """统一请求入口"""
        if self.environment == Environment.MOCK:
            return self._mock_response(endpoint, params)
        else:
            return self._real_request(method, endpoint, params)
    
    # ========== 工作流相关 API ==========
    
    def list_workflow_templates(self) -> APIResponse:
        """获取工作流模板列表"""
        return self.request("GET", "/workflow/templates")
    
    def get_workflow_template(self, template_id: str) -> APIResponse:
        """获取工作流模板详情"""
        return self.request("GET", f"/workflow/templates/{template_id}")
    
    def execute_workflow(self, template_id: str, parameters: Dict = None) -> APIResponse:
        """执行工作流"""
        return self.request("POST", "/workflow/execute", {
            "template_id": template_id,
            "parameters": parameters or {}
        })
    
    def get_workflow_status(self, instance_id: str) -> APIResponse:
        """获取工作流实例状态"""
        return self.request("GET", f"/workflow/status/{instance_id}")
    
    def cancel_workflow(self, instance_id: str) -> APIResponse:
        """取消工作流"""
        return self.request("POST", f"/workflow/cancel/{instance_id}")
    
    # ========== 集成系统相关 API ==========
    
    def list_integrations(self) -> APIResponse:
        """获取所有集成系统"""
        return self.request("GET", "/integrations/list")
    
    def get_integration(self, integration_id: str) -> APIResponse:
        """获取集成系统详情"""
        return self.request("GET", f"/integrations/{integration_id}")
    
    def test_integration(self, integration_id: str) -> APIResponse:
        """测试集成连接"""
        return self.request("POST", f"/integrations/{integration_id}/test")
    
    # ========== 节点相关 API ==========
    
    def execute_node(self, node_type: str, node_config: Dict) -> APIResponse:
        """执行单个节点"""
        return self.request("POST", "/node/execute", {
            "node_type": node_type,
            "config": node_config
        })
    
    def validate_node(self, node_type: str, node_config: Dict) -> APIResponse:
        """验证节点配置"""
        return self.request("POST", "/node/validate", {
            "node_type": node_type,
            "config": node_config
        })


class WorkflowAPI:
    """
    工作流 API 客户端 - 高级封装
    提供更友好的接口
    """
    
    def __init__(self, environment: Environment = Environment.MOCK):
        self.adapter = APIAdapter(environment)
    
    def import_template(self, template_id: str) -> Dict:
        """导入工作流模板"""
        response = self.adapter.get_workflow_template(template_id)
        
        if response.success:
            logger.info(f"✓ 模板导入成功：{template_id}")
            return response.data
        else:
            logger.error(f"✗ 模板导入失败：{template_id} - {response.message}")
            raise Exception(f"导入失败：{response.message}")
    
    def execute_template(self, template_id: str, parameters: Dict = None) -> Dict:
        """执行工作流模板"""
        response = self.adapter.execute_workflow(template_id, parameters)
        
        if response.success:
            logger.info(f"✓ 工作流执行成功：{template_id}, 实例 ID: {response.data.get('instance_id')}")
            return response.data
        else:
            logger.error(f"✗ 工作流执行失败：{template_id} - {response.message}")
            raise Exception(f"执行失败：{response.message}")
    
    def get_execution_status(self, instance_id: str) -> Dict:
        """获取执行状态"""
        response = self.adapter.get_workflow_status(instance_id)
        
        if response.success:
            return response.data
        else:
            raise Exception(f"查询失败：{response.message}")
    
    def list_available_templates(self) -> List[Dict]:
        """列出可用模板"""
        response = self.adapter.list_workflow_templates()
        
        if response.success:
            return response.data.get("templates", [])
        else:
            logger.error(f"获取模板列表失败：{response.message}")
            return []
    
    def test_all_integrations(self) -> Dict[str, bool]:
        """测试所有集成连接"""
        response = self.adapter.list_integrations()
        
        if not response.success:
            return {}
        
        results = {}
        for integration in response.data.get("integrations", []):
            test_response = self.adapter.test_integration(integration["id"])
            results[integration["id"]] = test_response.success
        
        return results


def main():
    """主函数 - 演示用法"""
    print("=" * 60)
    print("API 集成层 - 演示")
    print("=" * 60)
    
    # 创建 API 客户端（Mock 环境）
    client = WorkflowAPI(environment=Environment.MOCK)
    
    # 列出可用模板
    print("\n📋 可用模板列表:")
    print("-" * 60)
    templates = client.list_available_templates()
    for t in templates:
        print(f"  {t['id']}: {t['name']} ({t['category']})")
    
    # 导入模板
    print("\n📥 导入模板 WF-01:")
    print("-" * 60)
    try:
        template = client.import_template("WF-01")
        print(f"✓ 导入成功：{template}")
    except Exception as e:
        print(f"✗ 导入失败：{e}")
    
    # 执行工作流
    print("\n▶️ 执行工作流 WF-01:")
    print("-" * 60)
    try:
        result = client.execute_template("WF-01", {
            "workflow_name": "测试订单",
            "trigger_type": "event"
        })
        print(f"✓ 执行成功：{result}")
        
        # 查询状态
        instance_id = result.get("instance_id")
        if instance_id:
            print(f"\n📊 查询执行状态:")
            print("-" * 60)
            status = client.get_execution_status(instance_id)
            print(f"状态：{status}")
    except Exception as e:
        print(f"✗ 执行失败：{e}")
    
    # 测试集成连接
    print("\n🔌 测试集成连接:")
    print("-" * 60)
    integration_status = client.test_all_integrations()
    for integration_id, status in integration_status.items():
        icon = "✓" if status else "✗"
        print(f"  {icon} {integration_id}: {'正常' if status else '异常'}")
    
    print("\n" + "=" * 60)
    print("演示完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
