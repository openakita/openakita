# 数据分析 API 示例（Mixpanel/Google Analytics）
# 用于 MVP 用户行为分析

import os
import requests
import hashlib
import base64
import json
from typing import Dict, List, Optional
from datetime import datetime

class AnalyticsClient:
    """数据分析客户端"""
    
    def __init__(self, provider: str = 'mixpanel'):
        self.provider = provider
        
        if provider == 'mixpanel':
            self.token = os.getenv('MIXPANEL_TOKEN', '')
            self.api_secret = os.getenv('MIXPANEL_API_SECRET', '')
            self.base_url = 'https://api.mixpanel.com'
        
        elif provider == 'google':
            self.tracking_id = os.getenv('GA_TRACKING_ID', '')
            self.measurement_id = os.getenv('GA_MEASUREMENT_ID', '')
            self.api_secret = os.getenv('GA_API_SECRET', '')
            self.base_url = 'https://www.google-analytics.com'
    
    def track_event(self, user_id: str, event_name: str, properties: Optional[Dict] = None) -> bool:
        """追踪事件"""
        if self.provider == 'mixpanel':
            return self._track_mixpanel(user_id, event_name, properties)
        elif self.provider == 'google':
            return self._track_google(user_id, event_name, properties)
        return False
    
    def _track_mixpanel(self, user_id: str, event_name: str, properties: Optional[Dict] = None) -> bool:
        props = properties or {}
        props.update({
            'token': self.token,
            'distinct_id': user_id,
            'time': int(datetime.now().timestamp())
        })
        
        data = [{
            'event': event_name,
            'properties': props
        }]
        
        try:
            response = requests.post(
                f'{self.base_url}/track',
                data={'data': json.dumps(data)},
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
            return response.status_code == 200
        except Exception as e:
            print(f"Mixpanel Track Error: {e}")
            return False
    
    def _track_google(self, user_id: str, event_name: str, properties: Optional[Dict] = None) -> bool:
        params = properties or {}
        params.update({
            'client_id': user_id,
            'user_id': user_id
        })
        
        data = {
            'client_id': user_id,
            'events': [{
                'name': event_name,
                'params': params
            }]
        }
        
        try:
            response = requests.post(
                f'{self.base_url}/mp/collect?measurement_id={self.measurement_id}&api_secret={self.api_secret}',
                json=data
            )
            return response.status_code == 204
        except Exception as e:
            print(f"Google Analytics Track Error: {e}")
            return False
    
    def identify_user(self, user_id: str, user_properties: Dict) -> bool:
        """设置用户属性"""
        if self.provider == 'mixpanel':
            return self._identify_mixpanel(user_id, user_properties)
        return False
    
    def _identify_mixpanel(self, user_id: str, user_properties: Dict) -> bool:
        data = [{
            '$token': self.token,
            '$distinct_id': user_id,
            '$set': user_properties
        }]
        
        try:
            response = requests.post(
                f'{self.base_url}/engage',
                data={'data': json.dumps(data)},
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
            return response.status_code == 200
        except Exception as e:
            print(f"Mixpanel Identify Error: {e}")
            return False
    
    def track_page_view(self, user_id: str, page_url: str, page_title: str = '') -> bool:
        """追踪页面浏览"""
        return self.track_event(user_id, 'page_view', {
            'url': page_url,
            'title': page_title
        })
    
    def track_user_signup(self, user_id: str, email: str) -> bool:
        """追踪用户注册"""
        success = self.track_event(user_id, 'user_signup', {'email': email})
        if success:
            self.identify_user(user_id, {'email': email, '$created': datetime.now().isoformat()})
        return success
    
    def track_workflow_action(self, user_id: str, workflow_id: str, action: str) -> bool:
        """追踪工作流操作"""
        return self.track_event(user_id, 'workflow_action', {
            'workflow_id': workflow_id,
            'action': action
        })

if __name__ == '__main__':
    analytics = AnalyticsClient(provider='mixpanel')
    
    # 追踪用户注册
    analytics.track_user_signup('user_123', 'user@example.com')
    
    # 追踪页面浏览
    analytics.track_page_view('user_123', '/dashboard', 'Dashboard')
    
    # 追踪工作流操作
    analytics.track_workflow_action('user_123', 'workflow_001', 'started')
    
    print("Analytics events tracked")
