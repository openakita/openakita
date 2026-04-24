"""
API 集成测试框架
"""

import pytest
import asyncio
from typing import Any, Dict, Optional
import os
import json

# 导入所有适配器
from src.openakita.integrations.adapters.mail import create_mail_adapter
from src.openakita.integrations.adapters.sheets import create_sheets_adapter
from src.openakita.integrations.adapters.crm import create_crm_adapter
from src.openakita.integrations.adapters.im import create_im_adapter
from src.openakita.integrations.adapters.storage import create_storage_adapter
from src.openakita.integrations.adapters.sms import create_sms_adapter
from src.openakita.integrations.adapters.payment import create_payment_adapter
from src.openakita.integrations.adapters.map import create_map_adapter
from src.openakita.integrations.adapters.weather import create_weather_adapter
from src.openakita.integrations.adapters.news import create_news_adapter


class APITestBase:
    """API 测试基类"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """设置测试环境"""
        self.config = self.load_test_config()
    
    def load_test_config(self) -> Dict[str, Any]:
        """加载测试配置"""
        config_file = 'tests/integration/config.test.json'
        if os.path.exists(config_file):
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    def skip_if_no_config(self, provider: str):
        """如果没有配置则跳过测试"""
        if provider not in self.config:
            pytest.skip(f"缺少 {provider} 测试配置")


class TestMailAPI(APITestBase):
    """邮件 API 测试"""
    
    @pytest.mark.asyncio
    async def test_sendgrid_auth(self):
        """测试 SendGrid 认证"""
        self.skip_if_no_config('sendgrid')
        adapter = create_mail_adapter('sendgrid', self.config['sendgrid'])
        result = await adapter.authenticate()
        assert result is True
        await adapter.close()
    
    @pytest.mark.asyncio
    async def test_sendgrid_send_email(self):
        """测试 SendGrid 发送邮件"""
        self.skip_if_no_config('sendgrid')
        adapter = create_mail_adapter('sendgrid', self.config['sendgrid'])
        await adapter.authenticate()
        result = await adapter.send_email(
            to_emails=['test@example.com'],
            subject='测试邮件',
            content='这是一封测试邮件'
        )
        assert result is not None
        await adapter.close()


class TestSheetsAPI(APITestBase):
    """表格 API 测试"""
    
    @pytest.mark.asyncio
    async def test_google_sheets_auth(self):
        """测试 Google Sheets 认证"""
        self.skip_if_no_config('google_sheets')
        adapter = create_sheets_adapter('google', self.config['google_sheets'])
        result = await adapter.authenticate()
        assert result is True
        await adapter.close()


class TestCRMAPI(APITestBase):
    """CRM API 测试"""
    
    @pytest.mark.asyncio
    async def test_salesforce_auth(self):
        """测试 Salesforce 认证"""
        self.skip_if_no_config('salesforce')
        adapter = create_crm_adapter('salesforce', self.config['salesforce'])
        result = await adapter.authenticate()
        assert result is True
        await adapter.close()


class TestIMAPI(APITestBase):
    """即时通讯 API 测试"""
    
    @pytest.mark.asyncio
    async def test_dingtalk_send_message(self):
        """测试钉钉发送消息"""
        self.skip_if_no_config('dingtalk')
        adapter = create_im_adapter('dingtalk', self.config['dingtalk'])
        result = await adapter.send_text('测试消息')
        assert result is not None


class TestStorageAPI(APITestBase):
    """云存储 API 测试"""
    
    @pytest.mark.asyncio
    async def test_aliyun_oss_auth(self):
        """测试阿里云 OSS 认证"""
        self.skip_if_no_config('aliyun_oss')
        adapter = create_storage_adapter('aliyun', self.config['aliyun_oss'])
        result = await adapter.authenticate()
        assert result is True
        await adapter.close()


class TestSMSAPI(APITestBase):
    """短信 API 测试"""
    
    @pytest.mark.asyncio
    async def test_aliyun_sms_send(self):
        """测试阿里云短信发送"""
        self.skip_if_no_config('aliyun_sms')
        adapter = create_sms_adapter('aliyun', self.config['aliyun_sms'])
        result = await adapter.send_sms(
            phone_numbers='13800138000',
            template_code='SMS_123456789',
            template_param={'code': '123456'}
        )
        assert result is not None


class TestPaymentAPI(APITestBase):
    """支付 API 测试"""
    
    @pytest.mark.asyncio
    async def test_alipay_create_order(self):
        """测试支付宝创建订单"""
        self.skip_if_no_config('alipay')
        adapter = create_payment_adapter('alipay', self.config['alipay'])
        result = await adapter.create_order(
            out_trade_no='TEST20260311001',
            total_amount='0.01',
            subject='测试订单'
        )
        assert result is not None


class TestMapAPI(APITestBase):
    """地图 API 测试"""
    
    @pytest.mark.asyncio
    async def test_amap_geocode(self):
        """测试高德地图地理编码"""
        self.skip_if_no_config('amap')
        adapter = create_map_adapter('amap', self.config['amap'])
        result = await adapter.geocode('北京市天安门')
        assert result is not None
        assert 'geocodes' in result
    
    @pytest.mark.asyncio
    async def test_amap_weather(self):
        """测试高德地图天气查询"""
        self.skip_if_no_config('amap')
        adapter = create_map_adapter('amap', self.config['amap'])
        result = await adapter.weather('北京')
        assert result is not None


class TestWeatherAPI(APITestBase):
    """天气 API 测试"""
    
    @pytest.mark.asyncio
    async def test_qweather_get_weather(self):
        """测试和风天气"""
        self.skip_if_no_config('qweather')
        adapter = create_weather_adapter('qweather', self.config['qweather'])
        result = await adapter.get_weather('101010100')  # 北京
        assert result is not None


class TestNewsAPI(APITestBase):
    """新闻 API 测试"""
    
    @pytest.mark.asyncio
    async def test_juhe_top_news(self):
        """测试聚合数据头条新闻"""
        self.skip_if_no_config('juhe_news')
        adapter = create_news_adapter('juhe', self.config['juhe_news'])
        result = await adapter.get_top_news()
        assert result is not None
        assert 'result' in result


# 运行所有测试
if __name__ == '__main__':
    pytest.main(['-v', 'tests/integration/test_api_adapters.py'])

