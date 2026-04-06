"""
10. CRM API - Salesforce
支持查询、创建、更新、删除 Salesforce 记录
"""

import json
from typing import Dict, Any, Optional, List
from adapters.base import BaseAPIAdapter, APIResponse, APIStatus

try:
    from simple_salesforce import Salesforce
except ImportError:
    Salesforce = None


class SalesforceAdapter(BaseAPIAdapter):
    """Salesforce CRM 适配器"""
    
    def __init__(self, config: dict):
        """
        配置参数:
        - username: Salesforce 用户名
        - password: 密码
        - security_token: 安全令牌
        - domain: 域 (test 或 None)
        或使用 token 方式:
        - instance: 实例 URL
        - session_id: 会话 ID
        """
        super().__init__(config)
        self.sf = None
    
    def connect(self) -> bool:
        try:
            if Salesforce is None:
                print("提示：请安装 simple-salesforce (pip install simple-salesforce)")
                return False
            
            # 方式 1: 用户名密码登录
            if self.config.get('username') and self.config.get('password'):
                self.sf = Salesforce(
                    username=self.config['username'],
                    password=self.config['password'],
                    security_token=self.config.get('security_token', ''),
                    domain=self.config.get('domain')
                )
            # 方式 2: token 登录
            elif self.config.get('instance') and self.config.get('session_id'):
                self.sf = Salesforce(
                    instance=self.config['instance'],
                    session_id=self.config['session_id']
                )
            else:
                print("错误：请提供用户名密码或实例+session_id")
                return False
            
            self._initialized = True
            return True
        except Exception as e:
            print(f"连接失败：{e}")
            return False
    
    def disconnect(self) -> None:
        self.sf = None
        self._initialized = False
    
    def execute(self, action: str, params: dict) -> APIResponse:
        actions = {
            "query": self.query,
            "create": self.create,
            "update": self.update,
            "delete": self.delete,
            "get": self.get
        }
        if action in actions:
            return actions[action](params)
        return APIResponse(status=APIStatus.FAILED, error=f"未知操作：{action}")
    
    def query(self, params: dict) -> APIResponse:
        """
        执行 SOQL 查询
        
        参数:
        - soql: SOQL 查询语句
        """
        try:
            result = self.sf.query(params['soql'])
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={
                    'records': result.get('records', []),
                    'total_size': result.get('totalSize', 0),
                    'done': result.get('done', False)
                },
                status_code=200
            )
        except Exception as e:
            return APIResponse(status=APIStatus.FAILED, error=str(e))
    
    def create(self, params: dict) -> APIResponse:
        """
        创建记录
        
        参数:
        - object_type: 对象类型 (如 Contact, Account)
        - data: 数据字典
        """
        try:
            result = getattr(self.sf, params['object_type']).create(params['data'])
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={
                    'id': result.get('id'),
                    'success': result.get('success')
                },
                status_code=200
            )
        except Exception as e:
            return APIResponse(status=APIStatus.FAILED, error=str(e))
    
    def update(self, params: dict) -> APIResponse:
        """
        更新记录
        
        参数:
        - object_type: 对象类型
        - record_id: 记录 ID
        - data: 更新数据
        """
        try:
            result = getattr(self.sf, params['object_type']).update(
                params['record_id'],
                params['data']
            )
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={'success': result == []},  # 成功返回空列表
                status_code=200
            )
        except Exception as e:
            return APIResponse(status=APIStatus.FAILED, error=str(e))
    
    def delete(self, params: dict) -> APIResponse:
        """
        删除记录
        
        参数:
        - object_type: 对象类型
        - record_id: 记录 ID
        """
        try:
            result = getattr(self.sf, params['object_type']).delete(params['record_id'])
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={'deleted': params['record_id']},
                status_code=200
            )
        except Exception as e:
            return APIResponse(status=APIStatus.FAILED, error=str(e))
    
    def get(self, params: dict) -> APIResponse:
        """
        获取记录详情
        
        参数:
        - object_type: 对象类型
        - record_id: 记录 ID
        """
        try:
            record = getattr(self.sf, params['object_type']).get(params['record_id'])
            return APIResponse(
                status=APIStatus.SUCCESS,
                data=record,
                status_code=200
            )
        except Exception as e:
            return APIResponse(status=APIStatus.FAILED, error=str(e))


# 使用示例
if __name__ == "__main__":
    config = {
        'username': 'user@example.com',
        'password': 'your_password',
        'security_token': 'your_token'
    }
    
    sf = SalesforceAdapter(config)
    if sf.connect():
        print("✅ Salesforce 连接成功")
        
        # 查询示例
        response = sf.execute('query', {
            'soql': "SELECT Id, Name, Email FROM Contact LIMIT 10"
        })
        
        if response.is_success():
            print(f"✅ 查询成功：{response.data['total_size']} 条记录")
