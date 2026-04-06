"""
表格处理 API 集成
支持：腾讯文档/飞书多维表格/Google Sheets
"""
import httpx
from typing import List, Dict, Any, Optional
import logging

from ..core.base import BaseAPIIntegration, APIConfig, APIResponse
from ..core.exceptions import AuthenticationError, ValidationError, ServiceUnavailableError
from ..core.config import config

logger = logging.getLogger(__name__)


class SpreadsheetAPIConfig(APIConfig):
    """表格 API 配置"""
    provider: str = "feishu"  # feishu, tencent, google
    app_id: Optional[str] = None
    app_secret: Optional[str] = None
    tenant_access_token: Optional[str] = None
    spreadsheet_token: Optional[str] = None
    google_credentials: Optional[Dict] = None


class SpreadsheetAPI(BaseAPIIntegration):
    """表格处理 API"""
    
    def __init__(self, config: SpreadsheetAPIConfig):
        super().__init__(config)
        self.config: SpreadsheetAPIConfig = config
        self.client: Optional[httpx.AsyncClient] = None
        self.access_token: Optional[str] = None
    
    async def initialize(self) -> None:
        """初始化"""
        self._validate_config()
        self.client = httpx.AsyncClient(timeout=self.config.timeout)
        
        if self.config.provider == "feishu":
            await self._get_feishu_token()
    
    async def close(self) -> None:
        """关闭连接"""
        if self.client:
            await self.client.aclose()
    
    def get_required_fields(self) -> list:
        """获取必需配置字段"""
        if self.config.provider == "feishu":
            return ['app_id', 'app_secret']
        elif self.config.provider == "tencent":
            return ['api_key', 'spreadsheet_id']
        elif self.config.provider == "google":
            return ['google_credentials', 'spreadsheet_id']
        return ['api_key']
    
    async def execute(self, action: str, **kwargs) -> APIResponse:
        """
        执行表格操作
        
        Actions:
            - read: 读取数据
                参数：sheet_name, range
            - write: 写入数据
                参数：sheet_name, range, values
            - create: 创建表格
                参数：title
            - delete: 删除行/列
                参数：sheet_name, range
        
        Returns:
            APIResponse
        """
        try:
            if action == "read":
                return await self._read_data(**kwargs)
            elif action == "write":
                return await self._write_data(**kwargs)
            elif action == "create":
                return await self._create_spreadsheet(**kwargs)
            elif action == "delete":
                return await self._delete_range(**kwargs)
            else:
                raise ValidationError(f"不支持的操作：{action}")
        except Exception as e:
            logger.error(f"表格操作失败：{e}")
            return APIResponse(
                success=False,
                error=str(e),
                status_code=getattr(e, 'status_code', None)
            )
    
    async def _get_feishu_token(self) -> None:
        """获取飞书 tenant_access_token"""
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.config.app_id,
            "app_secret": self.config.app_secret
        }
        
        response = await self.client.post(url, json=payload)
        data = response.json()
        
        if data.get('code') != 0:
            raise AuthenticationError(f"飞书认证失败：{data.get('msg')}")
        
        self.access_token = data.get('tenant_access_token')
    
    async def _read_data(
        self,
        sheet_name: str = "Sheet1",
        range: str = "A1:Z100",
        spreadsheet_token: Optional[str] = None
    ) -> APIResponse:
        """读取表格数据"""
        token = spreadsheet_token or self.config.spreadsheet_token
        if not token:
            raise ValidationError("缺少表格 token")
        
        if self.config.provider == "feishu":
            url = f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{token}/values/{sheet_name}!{range}"
            headers = {"Authorization": f"Bearer {self.access_token}"}
            
            response = await self.client.get(url, headers=headers)
            data = response.json()
            
            if data.get('code') != 0:
                raise ServiceUnavailableError(f"飞书表格读取失败：{data.get('msg')}")
            
            return APIResponse(
                success=True,
                data={"values": data.get('data', {}).get('values', [])},
                status_code=200
            )
        
        # TODO: 实现腾讯文档和 Google Sheets
        raise ValidationError(f"暂不支持 {self.config.provider} 的读取操作")
    
    async def _write_data(
        self,
        values: List[List[Any]],
        sheet_name: str = "Sheet1",
        range: str = "A1",
        spreadsheet_token: Optional[str] = None
    ) -> APIResponse:
        """写入表格数据"""
        token = spreadsheet_token or self.config.spreadsheet_token
        if not token:
            raise ValidationError("缺少表格 token")
        
        if self.config.provider == "feishu":
            url = f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{token}/values/{sheet_name}!{range}"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            payload = {
                "valueRange": {
                    "range": f"{sheet_name}!{range}",
                    "values": values
                }
            }
            
            response = await self.client.put(url, headers=headers, json=payload)
            data = response.json()
            
            if data.get('code') != 0:
                raise ServiceUnavailableError(f"飞书表格写入失败：{data.get('msg')}")
            
            return APIResponse(
                success=True,
                data={"updated_cells": data.get('data', {}).get('updatedCells', 0)},
                status_code=200
            )
        
        raise ValidationError(f"暂不支持 {self.config.provider} 的写入操作")
    
    async def _create_spreadsheet(self, title: str) -> APIResponse:
        """创建新表格"""
        if self.config.provider == "feishu":
            url = "https://open.feishu.cn/open-apis/sheets/v2/spreadsheets"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }
            payload = {"title": title}
            
            response = await self.client.post(url, headers=headers, json=payload)
            data = response.json()
            
            if data.get('code') != 0:
                raise ServiceUnavailableError(f"飞书表格创建失败：{data.get('msg')}")
            
            return APIResponse(
                success=True,
                data={"spreadsheet_token": data.get('data', {}).get('spreadsheet', {}).get('token')},
                status_code=200
            )
        
        raise ValidationError(f"暂不支持 {self.config.provider} 的创建操作")
    
    async def _delete_range(self, sheet_name: str, range: str, **kwargs) -> APIResponse:
        """删除指定范围的数据"""
        # 简化实现：写入空值
        return await self._write_data(
            values=[[""] * 10],
            sheet_name=sheet_name,
            range=range
        )


# 工厂函数
def create_spreadsheet_api(provider: str = "feishu") -> SpreadsheetAPI:
    """创建表格 API 实例"""
    config_dict = {"provider": provider}
    
    if provider == "feishu":
        config_dict.update({
            "app_id": config.get("FEISHU_APP_ID"),
            "app_secret": config.get("FEISHU_APP_SECRET"),
            "spreadsheet_token": config.get("FEISHU_SPREADSHEET_TOKEN"),
        })
    elif provider == "tencent":
        config_dict.update({
            "api_key": config.get("TENCENT_DOC_API_KEY"),
            "spreadsheet_id": config.get("TENCENT_SPREADSHEET_ID"),
        })
    elif provider == "google":
        config_dict.update({
            "google_credentials": config.get_dict("GOOGLE"),
            "spreadsheet_id": config.get("GOOGLE_SPREADSHEET_ID"),
        })
    
    return SpreadsheetAPI(SpreadsheetAPIConfig(**config_dict))
