"""
日历 API 集成 - Google Calendar
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from .base_client import BaseAPIClient, APIError


class GoogleCalendarClient(BaseAPIClient):
    """Google Calendar API 客户端"""
    
    def __init__(self, api_key: str, calendar_id: str = "primary"):
        super().__init__(
            base_url="https://www.googleapis.com/calendar/v3",
            api_key=api_key
        )
        self.calendar_id = calendar_id
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    async def create_event(
        self,
        summary: str,
        start_time: datetime,
        end_time: datetime,
        description: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        location: Optional[str] = None
    ) -> Dict[str, Any]:
        """创建日历事件"""
        event = {
            "summary": summary,
            "start": {"dateTime": start_time.isoformat(), "timeZone": "Asia/Shanghai"},
            "end": {"dateTime": end_time.isoformat(), "timeZone": "Asia/Shanghai"},
        }
        
        if description:
            event["description"] = description
        if location:
            event["location"] = location
        if attendees:
            event["attendees"] = [{"email": email} for email in attendees]
        
        return await self.post(f"/calendars/{self.calendar_id}/events", json=event)
    
    async def list_events(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """列出日历事件"""
        params = {"maxResults": max_results}
        
        if start_time:
            params["timeMin"] = start_time.isoformat()
        if end_time:
            params["timeMax"] = end_time.isoformat()
        
        response = await self.get(f"/calendars/{self.calendar_id}/events", params=params)
        return response.get("items", [])
    
    async def delete_event(self, event_id: str) -> Dict[str, Any]:
        """删除事件"""
        return await self.delete(f"/calendars/{self.calendar_id}/events/{event_id}")
    
    async def test_connection(self) -> bool:
        """测试连接"""
        try:
            await self.get(f"/calendars/{self.calendar_id}")
            return True
        except APIError:
            return False


# 使用示例
async def example_google_calendar():
    """Google Calendar 使用示例"""
    from config import APIConfig
    
    async with GoogleCalendarClient(
        APIConfig.GOOGLE_CALENDAR_API_KEY,
        APIConfig.GOOGLE_CALENDAR_ID
    ) as client:
        # 创建会议
        await client.create_event(
            summary="项目评审会议",
            start_time=datetime(2026, 3, 15, 14, 0),
            end_time=datetime(2026, 3, 15, 15, 0),
            description="讨论 MVP 功能范围",
            attendees=["team@example.com"],
            location="会议室 A"
        )
        
        # 获取本周事件
        events = await client.list_events(
            start_time=datetime.now(),
            max_results=10
        )
