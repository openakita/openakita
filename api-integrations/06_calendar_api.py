"""
日历 API 集成示例
支持 Google Calendar
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any


class GoogleCalendarAPI:
    """Google Calendar API 集成"""
    
    def __init__(self, credentials_file: str = 'credentials.json', token_file: str = 'token.json'):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.service = None
    
    def authenticate(self, scopes: List[str] = None):
        """
        身份认证
        
        Args:
            scopes: 权限范围列表
        """
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        import os.path
        
        scopes = scopes or ['https://www.googleapis.com/auth/calendar']
        
        creds = None
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, scopes)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, scopes
                )
                creds = flow.run_local_server(port=0)
            
            with open(self.token_file, 'w') as token:
                token.write(creds.to_json())
        
        self.service = build('calendar', 'v3', credentials=creds)
        print("✓ Google Calendar 认证成功")
    
    def create_event(self, summary: str, start_time: datetime, end_time: datetime, 
                     description: str = "", attendees: List[str] = None) -> Optional[str]:
        """
        创建日历事件
        
        Args:
            summary: 事件标题
            start_time: 开始时间
            end_time: 结束时间
            description: 事件描述
            attendees: 参与者邮箱列表
            
        Returns:
            str: 事件 ID
        """
        if not self.service:
            self.authenticate()
        
        event = {
            'summary': summary,
            'description': description,
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'Asia/Shanghai',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'Asia/Shanghai',
            },
        }
        
        if attendees:
            event['attendees'] = [{'email': email} for email in attendees]
        
        try:
            event = self.service.events().insert(calendarId='primary', body=event).execute()
            print(f"✓ 日历事件已创建：{event.get('htmlLink')}")
            return event.get('id')
            
        except Exception as e:
            print(f"✗ 创建日历事件失败：{e}")
            return None
    
    def get_events(self, start_time: datetime = None, end_time: datetime = None, 
                   max_results: int = 10) -> List[Dict]:
        """
        获取日历事件
        
        Args:
            start_time: 开始时间
            end_time: 结束时间
            max_results: 最大返回数量
            
        Returns:
            list: 事件列表
        """
        if not self.service:
            self.authenticate()
        
        now = datetime.utcnow().isoformat() + 'Z'
        
        try:
            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=start_time.isoformat() if start_time else now,
                timeMax=end_time.isoformat() if end_time else None,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            print(f"✓ 获取到 {len(events)} 个日历事件")
            return events
            
        except Exception as e:
            print(f"✗ 获取日历事件失败：{e}")
            return []
    
    def delete_event(self, event_id: str) -> bool:
        """
        删除日历事件
        
        Args:
            event_id: 事件 ID
            
        Returns:
            bool: 删除是否成功
        """
        if not self.service:
            self.authenticate()
        
        try:
            self.service.events().delete(calendarId='primary', eventId=event_id).execute()
            print(f"✓ 日历事件已删除：{event_id}")
            return True
            
        except Exception as e:
            print(f"✗ 删除日历事件失败：{e}")
            return False
    
    def update_event(self, event_id: str, **kwargs) -> bool:
        """
        更新日历事件
        
        Args:
            event_id: 事件 ID
            **kwargs: 要更新的字段（summary/description/start/end 等）
            
        Returns:
            bool: 更新是否成功
        """
        if not self.service:
            self.authenticate()
        
        try:
            event = self.service.events().get(calendarId='primary', eventId=event_id).execute()
            
            # 更新指定字段
            for key, value in kwargs.items():
                if key in event:
                    event[key] = value
            
            self.service.events().update(
                calendarId='primary', 
                eventId=event_id, 
                body=event
            ).execute()
            
            print(f"✓ 日历事件已更新：{event_id}")
            return True
            
        except Exception as e:
            print(f"✗ 更新日历事件失败：{e}")
            return False


# 使用示例
if __name__ == "__main__":
    calendar = GoogleCalendarAPI()
    calendar.authenticate()
    
    # 创建事件
    event_id = calendar.create_event(
        summary="产品评审会议",
        start_time=datetime(2026, 3, 15, 14, 0),
        end_time=datetime(2026, 3, 15, 15, 30),
        description="讨论 MVP 功能范围和技术方案",
        attendees=["team@example.com"]
    )
    
    # 获取未来 7 天的事件
    events = calendar.get_events(
        start_time=datetime.now(),
        end_time=datetime.now() + timedelta(days=7),
        max_results=10
    )
    
    for event in events:
        print(f"事件：{event['summary']} - {event['start'].get('dateTime', event['start'].get('date'))}")
