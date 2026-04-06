"""
表格处理 API - 飞书多维表格
支持表格读取、写入、更新操作
"""

from typing import Dict, Any, List
import logging
from .base import BaseAPI, APIResponse, APIMode
import time

logger = logging.getLogger(__name__)


class TableAPI(BaseAPI):
    """飞书多维表格 API"""
    
    def __init__(self, mode: APIMode = APIMode.MOCK):
        super().__init__(mode)
        self.mock_data = {
            "records": [
                {"id": "rec1", "fields": {"姓名": "张三", "部门": "技术部", "入职日期": "2024-01-15"}},
                {"id": "rec2", "fields": {"姓名": "李四", "部门": "市场部", "入职日期": "2024-02-20"}},
                {"id": "rec3", "fields": {"姓名": "王五", "部门": "销售部", "入职日期": "2024-03-10"}},
            ]
        }
    
    def _call_mock(self, **kwargs) -> APIResponse:
        """Mock 模式：模拟表格操作"""
        action = kwargs.get('action', 'list')
        
        try:
            if action == 'list':
                # 模拟读取表格数据
                page_size = kwargs.get('page_size', 100)
                return APIResponse(
                    success=True,
                    data={
                        'records': self.mock_data['records'][:page_size],
                        'total': len(self.mock_data['records'])
                    }
                )
            
            elif action == 'create':
                # 模拟创建记录
                fields = kwargs.get('fields', {})
                new_record = {
                    'id': f'rec{int(time.time())}',
                    'fields': fields
                }
                self.mock_data['records'].append(new_record)
                return APIResponse(
                    success=True,
                    data=new_record,
                    status_code=201
                )
            
            elif action == 'update':
                # 模拟更新记录
                record_id = kwargs.get('record_id')
                fields = kwargs.get('fields', {})
                for record in self.mock_data['records']:
                    if record['id'] == record_id:
                        record['fields'].update(fields)
                        return APIResponse(success=True, data=record)
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"记录不存在：{record_id}",
                    status_code=404
                )
            
            elif action == 'delete':
                # 模拟删除记录
                record_id = kwargs.get('record_id')
                original_count = len(self.mock_data['records'])
                self.mock_data['records'] = [r for r in self.mock_data['records'] if r['id'] != record_id]
                if len(self.mock_data['records']) < original_count:
                    return APIResponse(success=True, data={'deleted': record_id})
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"记录不存在：{record_id}",
                    status_code=404
                )
            
            else:
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"未知操作：{action}",
                    status_code=400
                )
                
        except Exception as e:
            return APIResponse(
                success=False,
                data=None,
                error=str(e),
                status_code=500
            )
    
    def _call_real(self, **kwargs) -> APIResponse:
        """真实 API 调用 - 飞书开放平台"""
        try:
            import requests
            
            action = kwargs.get('action', 'list')
            app_token = self._config.get('FEISHU_BITABLE_APP_TOKEN')
            
            if action == 'list':
                url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/table_id/records"
                headers = {
                    'Authorization': f'Bearer {self._config.get("FEISHU_ACCESS_TOKEN")}',
                    'Content-Type': 'application/json'
                }
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    return APIResponse(success=True, data=response.json())
                else:
                    return APIResponse(
                        success=False,
                        data=None,
                        error=f"API 错误：{response.text}",
                        status_code=response.status_code
                    )
            
            elif action == 'create':
                url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/table_id/records"
                headers = {
                    'Authorization': f'Bearer {self._config.get("FEISHU_ACCESS_TOKEN")}',
                    'Content-Type': 'application/json'
                }
                data = {'fields': kwargs.get('fields', {})}
                response = requests.post(url, json=data, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    return APIResponse(success=True, data=response.json())
                else:
                    return APIResponse(
                        success=False,
                        data=None,
                        error=f"API 错误：{response.text}",
                        status_code=response.status_code
                    )
            
            else:
                return APIResponse(
                    success=False,
                    data=None,
                    error=f"不支持的操作：{action}",
                    status_code=400
                )
                
        except Exception as e:
            return APIResponse(
                success=False,
                data=None,
                error=str(e),
                status_code=500
            )
    
    def list_records(self, page_size: int = 100) -> APIResponse:
        """读取表格数据"""
        return self.call(action='list', page_size=page_size)
    
    def create_record(self, fields: Dict[str, Any]) -> APIResponse:
        """创建新记录"""
        return self.call(action='create', fields=fields)
    
    def update_record(self, record_id: str, fields: Dict[str, Any]) -> APIResponse:
        """更新记录"""
        return self.call(action='update', record_id=record_id, fields=fields)
    
    def delete_record(self, record_id: str) -> APIResponse:
        """删除记录"""
        return self.call(action='delete', record_id=record_id)


# 测试用例
def test_table_api():
    """表格 API 测试"""
    print("=" * 50)
    print("表格 API 测试")
    print("=" * 50)
    
    api = TableAPI(mode=APIMode.MOCK)
    
    # 测试 1: 读取数据
    print("\n[测试 1] 读取表格数据")
    result = api.list_records()
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    print(f"记录数：{result.data.get('total', 0) if result.data else 0}")
    
    # 测试 2: 创建记录
    print("\n[测试 2] 创建新记录")
    result = api.create_record({
        "姓名": "赵六",
        "部门": "人力资源部",
        "入职日期": "2024-04-01"
    })
    print(f"结果：{'✅ 成功' if result.success else '❌ 失败'}")
    if result.success:
        print(f"新记录 ID: {result.data.get('id')}")
    
    # 测试 3: 更新记录
    print("\n[测试 3] 更新记录")
    if result.success:
        record_id = result.data.get('id')
        update_result = api.update_record(record_id, {"部门": "财务部"})
        print(f"结果：{'✅ 成功' if update_result.success else '❌ 失败'}")
    
    print("\n" + "=" * 50)


if __name__ == "__main__":
    test_table_api()
