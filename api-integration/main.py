"""
API 集成统一入口
提供统一的 API 适配器工厂和配置管理
"""

from typing import Dict, Any, Optional
from adapters.base import BaseAPIAdapter, APIResponse, APIStatus

# 导入所有适配器
from adapters.email import AliyunEmailAdapter
from adapters.sms import AliyunSMSAdapter
from adapters.wecom import WeComAdapter
from adapters.dingtalk import DingTalkRobotAdapter
from adapters.feishu import FeishuAdapter
from adapters.http import HTTPClientAdapter
from adapters.oss import AliyunOSSAdapter
from adapters.postgresql import PostgreSQLAdapter
from adapters.google_calendar import GoogleCalendarAdapter
from adapters.salesforce import SalesforceAdapter


class APIAdapterFactory:
    """API 适配器工厂"""
    
    _adapters = {
        'email': AliyunEmailAdapter,
        'sms': AliyunSMSAdapter,
        'wecom': WeComAdapter,
        'dingtalk': DingTalkRobotAdapter,
        'feishu': FeishuAdapter,
        'http': HTTPClientAdapter,
        'oss': AliyunOSSAdapter,
        'postgresql': PostgreSQLAdapter,
        'google_calendar': GoogleCalendarAdapter,
        'salesforce': SalesforceAdapter
    }
    
    @classmethod
    def create(cls, adapter_type: str, config: Dict[str, Any]) -> Optional[BaseAPIAdapter]:
        """
        创建适配器实例
        
        参数:
        - adapter_type: 适配器类型 (email/sms/wecom/dingtalk/feishu/http/oss/postgresql/google_calendar/salesforce)
        - config: 配置字典
        """
        adapter_class = cls._adapters.get(adapter_type)
        if not adapter_class:
            raise ValueError(f"不支持的适配器类型：{adapter_type}")
        return adapter_class(config)
    
    @classmethod
    def list_adapters(cls) -> list:
        """列出所有支持的适配器类型"""
        return list(cls._adapters.keys())


class APIIntegrationManager:
    """API 集成管理器"""
    
    def __init__(self):
        self.adapters: Dict[str, BaseAPIAdapter] = {}
    
    def register(self, name: str, adapter_type: str, config: Dict[str, Any]) -> bool:
        """
        注册适配器
        
        参数:
        - name: 适配器名称 (自定义)
        - adapter_type: 适配器类型
        - config: 配置
        """
        try:
            adapter = APIAdapterFactory.create(adapter_type, config)
            if adapter.connect():
                self.adapters[name] = adapter
                return True
            else:
                print(f"适配器 {name} 连接失败")
                return False
        except Exception as e:
            print(f"注册适配器失败：{e}")
            return False
    
    def unregister(self, name: str) -> None:
        """注销适配器"""
        if name in self.adapters:
            self.adapters[name].disconnect()
            del self.adapters[name]
    
    def get(self, name: str) -> Optional[BaseAPIAdapter]:
        """获取适配器"""
        return self.adapters.get(name)
    
    def execute(self, name: str, action: str, params: Dict[str, Any]) -> APIResponse:
        """
        执行 API 调用
        
        参数:
        - name: 适配器名称
        - action: 操作类型
        - params: 参数
        """
        adapter = self.get(name)
        if not adapter:
            return APIResponse(
                status=APIStatus.FAILED,
                error=f"适配器 {name} 未找到"
            )
        return adapter.execute(action, params)
    
    def health_check(self) -> Dict[str, bool]:
        """检查所有适配器健康状态"""
        return {
            name: adapter.health_check()
            for name, adapter in self.adapters.items()
        }
    
    def disconnect_all(self) -> None:
        """断开所有连接"""
        for adapter in self.adapters.values():
            adapter.disconnect()
        self.adapters.clear()


# 使用示例
if __name__ == "__main__":
    # 创建管理器
    manager = APIIntegrationManager()
    
    # 注册适配器
    manager.register('email', 'email', {
        'access_key_id': 'YOUR_KEY',
        'access_key_secret': 'YOUR_SECRET',
        'account_name': 'noreply@example.com',
        'region': 'cn-hangzhou'
    })
    
    manager.register('wecom', 'wecom', {
        'corp_id': 'YOUR_CORP_ID',
        'agent_id': 1000001,
        'secret': 'YOUR_SECRET'
    })
    
    # 执行 API 调用
    response = manager.execute('wecom', 'send_text', {
        'to_user': '@all',
        'content': '测试消息'
    })
    
    if response.is_success():
        print("✅ 消息发送成功")
    else:
        print(f"❌ 发送失败：{response.error}")
    
    # 健康检查
    status = manager.health_check()
    print(f"适配器状态：{status}")
    
    # 清理
    manager.disconnect_all()
