"""
9. 日历 API - Google Calendar
支持创建事件、查询事件、更新事件等操作
"""

import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from adapters.base import BaseAPIAdapter, APIResponse, APIStatus

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
except ImportError:
    service_account = None
    build = None


class GoogleCalendarAdapter(BaseAPIAdapter):
    """Google Calendar API 适配器"""
    
    def __init__(self, config: dict):
        """
        配置参数:
        - credentials_file: 服务账号凭证文件路径
        - calendar_id: 日历 ID (默认 primary)
        - scopes: 权限范围
        """
        super().__init__(config)
        self.service = None
        self.calendar_id = config.get('calendar_id', 'primary')
    
    def connect(self) -> bool:
        try:
            if service_account is None or build is None:
                print("提示：请安装 Google API 库 (pip install google-auth google-api-python-client)")
                return False
            
            credentials_file = self.config.get('credentials_file')
            if not credentials_file:
                print("错误：请提供 credentials_file 配置")
                return False
            
            scopes = self.config.get('scopes', ['https://www.googleapis.com/auth/calendar'])
            creds = service_account.Credentials.from_service_account_file(
                credentials_file, scopes=scopes
            )
            
            self.service = build('calendar', 'v3', credentials=creds)
            self._initialized = True
            return True
        except Exception as e:
            print(f"连接失败：{e}")
            return False
    
    def disconnect(self) -> None:
        self.service = None
        self._initialized = False
    
    def execute(self, action: str, params: dict) -> APIResponse:
        actions = {
            "create_event": self.create_event,
            "get_event": self.get_event,
            "list_events": self.list_events,
            "update_event": self.update_event,
            "delete_event": self.delete_event
        }
        if action in actions:
            return actions[action](params)
        return APIResponse(status=APIStatus.FAILED, error=f"未知操作：{action}")
    
    def create_event(self, params: dict) -> APIResponse:
        """创建日历事件"""
        try:
            timezone = params.get('timezone', 'UTC')
            start_dt = params['start_time'] if isinstance(params['start_time'], str) else params['start_time'].isoformat()
            end_dt = params['end_time'] if isinstance(params['end_time'], str) else params['end_time'].isoformat()
            
            event = {
                'summary': params['summary'],
                'location': params.get('location', ''),
                'description': params.get('description', ''),
                'start': {'dateTime': start_dt, 'timeZone': timezone},
                'end': {'dateTime': end_dt, 'timeZone': timezone},
            }
            
            if params.get('attendees'):
                event['attendees'] = [{'email': email} for email in params['attendees']]
            
            event_result = self.service.events().insert(
                calendarId=self.calendar_id, body=event, sendUpdates='all'
            ).execute()
            
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={'event_id': event_result['id'], 'html_link': event_result.get('htmlLink')},
                status_code=200
            )
        except Exception as e:
            return APIResponse(status=APIStatus.FAILED, error=str(e))
    
    def get_event(self, params: dict) -> APIResponse:
        """获取事件详情"""
        try:
            event = self.service.events().get(
                calendarId=self.calendar_id, eventId=params['event_id']
            ).execute()
            return APIResponse(status=APIStatus.SUCCESS, data=event, status_code=200)
        except Exception as e:
            return APIResponse(status=APIStatus.FAILED, error=str(e))
    
    def list_events(self, params: dict) -> APIResponse:
        """列出事件"""
        try:
            time_min = params.get('time_min', datetime.utcnow().isoformat() + 'Z')
            time_max = params.get('time_max', (datetime.utcnow() + timedelta(days=7)).isoformat() + 'Z')
            
            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min, timeMax=time_max,
                maxResults=params.get('max_results', 10),
                singleEvents=params.get('single_events', True),
                orderBy=params.get('order_by', 'startTime')
            ).execute()
            
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={'events': events_result.get('items', [])},
                status_code=200
            )
        except Exception as e:
            return APIResponse(status=APIStatus.FAILED, error=str(e))
    
    def update_event(self, params: dict) -> APIResponse:
        """更新事件"""
        try:
            event = self.service.events().get(
                calendarId=self.calendar_id, eventId=params['event_id']
            ).execute()
            
            for key, value in params.get('updates', {}).items():
                if key in event:
                    event[key] = value
            
            updated_event = self.service.events().update(
                calendarId=self.calendar_id, eventId=params['event_id'],
                body=event, sendUpdates='all'
            ).execute()
            
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={'event_id': updated_event['id']},
                status_code=200
            )
        except Exception as e:
            return APIResponse(status=APIStatus.FAILED, error=str(e))
    
    def delete_event(self, params: dict) -> APIResponse:
        """删除事件"""
        try:
            self.service.events().delete(
                calendarId=self.calendar_id, eventId=params['event_id'], sendUpdates='all'
            ).execute()
            return APIResponse(
                status=APIStatus.SUCCESS,
                data={'deleted': params['event_id']},
                status_code=200
            )
        except Exception as e:
            return APIResponse(status=APIStatus.FAILED, error=str(e))


# 使用示例
if __name__ == "__main__":
    config = {
        'credentials_file': 'credentials.json',
        'calendar_id': 'primary'
    }
    
    calendar = GoogleCalendarAdapter(config)
    if calendar.connect():
        print("✅ Google Calendar 连接成功")
