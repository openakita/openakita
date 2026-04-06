"""
CRM 系统 API - 销售易/纷享销客
支持客户管理、销售机会跟踪
"""

from typing import Dict, Any, List
import logging
from .base import BaseAPI, APIResponse, APIMode
import time

logger = logging.getLogger(__name__)


class CRMAPI(BaseAPI):
    """CRM 系统 API"""
    
    def __init__(self, mode: APIMode = APIMode.MOCK):
        super().__init__(mode)
        self.mock_customers = {
            "customers": [
                {"id": "cust001", "name": "某某科技公司", "industry": "互联网", "contact": "张总", "phone": "138****1234", "status": "active"},
                {"id": "cust002", "name": "某某制造公司", "industry": "制造业", "contact": "李总", "phone": "139****5678", "status": "active"},
                {"id": "cust003", "name": "某某贸易公司", "industry": "贸易", "contact": "王总", "phone": "136****9012", "status": "potential"},
            ]
        }
    
    def _call_mock(self, **kwargs) -> APIResponse:
        """Mock 模式：模拟 CRM 操作"""
        action = kwargs.get('action', 'list')
        
        try:
            if action == 'list':
                return APIResponse(
                    success=True,
                    data={
                        'customers': self.mock_customers['customers'],
                        'total': len(self.mock_customers['customers'])
                    }
                )
            
            elif action == 'create':
                customer_data = kwargs.get('customer_data', {})
                new_customer = {
                    'id': f'cust{int(time.time())}',
                    **customer_data
                }
                self.mock_customers['customers'].append(new_customer)
                return APIResponse(success=True, data=new_customer, status_code=201)
            
            elif action == 'update':
                customer_id = kwargs.get('customer_id')
                update_data = kwargs.get('update_data', {})
                for customer in self.mock_customers['customers']:
                    if customer['id'] == customer_id:
                        customer.update(update_data)
                        return APIResponse(success=True, data=customer)
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"客户不存在：{customer_id}",
                    status_code=404
                )
            
            elif action == 'search':
                keyword = kwargs.get('keyword', '')
                results = [c for c in self.mock_customers['customers'] 
                          if keyword.lower() in c.get('name', '').lower() or keyword in c.get('contact', '')]
                return APIResponse(
                    success=True,
                    data={'customers': results, 'total': len(results)}
                )
            
            else:
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"未知操作：{action}",
                    status_code=400
                )
                
        except Exception as e:
            return APIResponse(success=False, data=None, error=str(e), status_code=500)
    
    def _call_real(self, **kwargs) -> APIResponse:
        """真实 API 调用"""
        try:
            import requests
            
            action = kwargs.get('action', 'list')
            base_url = self._config.get('CRM_API_BASE_URL')
            access_token = self._config.get('CRM_ACCESS_TOKEN')
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            if action == 'list':
                url = f"{base_url}/customers"
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    return APIResponse(success=True, data=response.json())
                else:
                    return APIResponse(success=False, data=None, error=f"API 错误：{response.text}", status_code=response.status_code)
            
            elif action == 'create':
                url = f"{base_url}/customers"
                data = kwargs.get('customer_data', {})
                response = requests.post(url, json=data, headers=headers, timeout=10)
                if response.status_code == 201:
                    return APIResponse(success=True, data=response.json())
                else:
                    return APIResponse(success=False, data=None, error=f"API 错误：{response.text}", status_code=response.status_code)
            
            else:
                return APIResponse(success=False, data=None, error=f"不支持的操作：{action}", status_code=400)
                
        except Exception as e:
            return APIResponse(success=False, data=None, error=str(e), status_code=500)
    
    def list_customers(self) -> APIResponse:
        """获取客户列表"""
        return self.call(action='list')
    
    def create_customer(self, customer_data: Dict[str, Any]) -> APIResponse:
        """创建新客户"""
        return self.call(action='create', customer_data=customer_data)
    
    def search_customer(self, keyword: str) -> APIResponse:
        """搜索客户"""
        return self.call(action='search', keyword=keyword)


def test_crm_api():
    """CRM API 测试"""
    print("=" * 50)
    print("CRM API 测试")
    print("=" * 50)
    
    api = CRMAPI(mode=APIMode.MOCK)
    
    print("\n[测试 1] 获取客户列表")
    result = api.list_customers()
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    if result.success:
        print(f"客户数：{result.data.get('total', 0)}")
    
    print("\n[测试 2] 创建新客户")
    result = api.create_customer({
        "name": "某某咨询公司",
        "industry": "咨询",
        "contact": "陈总",
        "phone": "137****3456",
        "status": "potential"
    })
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    
    print("\n[测试 3] 搜索客户")
    result = api.search_customer("科技")
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    if result.success:
        print(f"匹配客户数：{result.data.get('total', 0)}")
    
    print("\n" + "=" * 50)


if __name__ == "__main__":
    test_crm_api()
