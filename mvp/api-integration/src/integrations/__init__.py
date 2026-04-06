"""
API 集成模块包
"""
from .email_api import EmailAPI
from .database_api import DatabaseAPI
from .storage_api import StorageAPI
from .http_api import HTTPAPI
from .wechat_work_api import WeChatWorkAPI
from .dingtalk_api import DingTalkAPI
from .sms_api import SMSAPI
from .calendar_api import CalendarAPI
from .data_transform_api import DataTransformAPI
from .crm_api import CRMAPI

__all__ = [
    'EmailAPI',
    'DatabaseAPI',
    'StorageAPI',
    'HTTPAPI',
    'WeChatWorkAPI',
    'DingTalkAPI',
    'SMSAPI',
    'CalendarAPI',
    'DataTransformAPI',
    'CRMAPI',
]
