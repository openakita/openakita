"""
Mock 引擎 - 模拟 API 响应
用于开发和测试阶段，无需真实 API 凭据
"""
import asyncio
import random
from typing import Any, Dict, Optional
from pydantic import BaseModel
import logging

from .base import APIResponse

logger = logging.getLogger(__name__)


class MockConfig(BaseModel):
    """Mock 配置"""
    enabled: bool = True
    delay: float = 0.5  # 模拟网络延迟 (秒)
    success_rate: float = 0.98  # 成功率
    custom_responses: Optional[Dict[str, Any]] = None


class MockEngine:
    """Mock 引擎"""
    
    def __init__(self, config: MockConfig):
        self.config = config
    
    async def execute(self, action: str, api_name: str, **kwargs) -> APIResponse:
        """
        执行 Mock 调用
        
        Args:
            action: 操作类型
            api_name: API 名称
            **kwargs: 操作参数
        
        Returns:
            APIResponse
        """
        # 模拟网络延迟
        await asyncio.sleep(self.config.delay)
        
        # 模拟失败
        if random.random() > self.config.success_rate:
            return APIResponse(
                success=False,
                error=f"Mock: 模拟随机失败 ({api_name}.{action})",
                status_code=500
            )
        
        # 获取自定义响应
        response_data = self._get_mock_response(api_name, action, **kwargs)
        
        return APIResponse(
            success=True,
            data=response_data,
            status_code=200,
            request_id=f"mock-{random.randint(10000, 99999)}"
        )
    
    def _get_mock_response(self, api_name: str, action: str, **kwargs) -> Dict[str, Any]:
        """获取 Mock 响应数据"""
        
        # 自定义响应优先
        if self.config.custom_responses:
            key = f"{api_name}.{action}"
            if key in self.config.custom_responses:
                return self.config.custom_responses[key]
        
        # 默认 Mock 响应
        return self._get_default_response(api_name, action, **kwargs)
    
    def _get_default_response(self, api_name: str, action: str, **kwargs) -> Dict[str, Any]:
        """默认 Mock 响应"""
        
        responses = {
            "email": {
                "send": {"message_id": f"mock-email-{random.randint(1000, 9999)}", "status": "sent"}
            },
            "wecom": {
                "send_message": {"errcode": 0, "errmsg": "ok", "message_id": f"mock-wecom-{random.randint(1000, 9999)}"},
                "send_robot": {"errcode": 0, "errmsg": "ok"}
            },
            "dingtalk": {
                "send_message": {"errcode": 0, "errmsg": "ok", "message_id": f"mock-ding-{random.randint(1000, 9999)}"},
                "send_robot": {"errcode": 0, "errmsg": "ok"}
            },
            "crm": {
                "create_lead": {"lead_id": f"mock-lead-{random.randint(1000, 9999)}", "status": "created"},
                "query_lead": {"leads": []},
                "create_customer": {"customer_id": f"mock-customer-{random.randint(1000, 9999)}"},
                "query_customer": {"customers": []}
            },
            "spreadsheet": {
                "read": {"rows": []},
                "write": {"row_id": f"mock-row-{random.randint(1000, 9999)}"},
                "update": {"updated": True}
            },
            "database": {
                "query": {"rows": [], "count": 0},
                "execute": {"affected_rows": 0},
                "insert": {"id": random.randint(1000, 9999)}
            },
            "oss": {
                "upload": {"url": f"https://mock.oss.com/file-{random.randint(1000, 9999)}", "etag": "mock-etag"},
                "download": {"content": b"mock content"},
                "delete": {"deleted": True}
            },
            "sms": {
                "send": {"message_id": f"mock-sms-{random.randint(1000, 9999)}", "status": "sent"}
            },
            "webhook": {
                "post": {"status": "success", "response_code": 200},
                "get": {"status": "success", "response_code": 200}
            },
            "calendar": {
                "create_event": {"event_id": f"mock-event-{random.randint(1000, 9999)}", "status": "confirmed"},
                "query_events": {"events": []},
                "update_event": {"updated": True}
            },
            "transform": {
                "json_to_csv": {"rows": 0, "columns": 0},
                "csv_to_json": {"records": []},
                "xml_to_json": {"data": {}}
            }
        }
        
        api_responses = responses.get(api_name, {})
        return api_responses.get(action, {"status": "mocked", "action": action})


# 全局 Mock 配置
DEFAULT_MOCK_CONFIG = MockConfig(
    enabled=True,
    delay=0.5,
    success_rate=0.98,
    custom_responses=None
)
