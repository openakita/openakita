# 第三方登录 API 示例（OAuth2 - Google/GitHub）
# 用于 MVP 第三方登录功能

import os
import requests
from typing import Optional, Dict
from urllib.parse import urlencode

class OAuthClient:
    """OAuth2 客户端"""
    
    def __init__(self, provider: str = 'google'):
        self.provider = provider
        
        if provider == 'google':
            self.client_id = os.getenv('GOOGLE_CLIENT_ID', '')
            self.client_secret = os.getenv('GOOGLE_CLIENT_SECRET', '')
            self.redirect_uri = os.getenv('GOOGLE_REDIRECT_URI', '')
            self.auth_url = 'https://accounts.google.com/o/oauth2/v2/auth'
            self.token_url = 'https://oauth2.googleapis.com/token'
            self.userinfo_url = 'https://www.googleapis.com/oauth2/v2/userinfo'
            self.scope = 'openid email profile'
        
        elif provider == 'github':
            self.client_id = os.getenv('GITHUB_CLIENT_ID', '')
            self.client_secret = os.getenv('GITHUB_CLIENT_SECRET', '')
            self.redirect_uri = os.getenv('GITHUB_REDIRECT_URI', '')
            self.auth_url = 'https://github.com/login/oauth/authorize'
            self.token_url = 'https://github.com/login/oauth/access_token'
            self.userinfo_url = 'https://api.github.com/user'
            self.scope = 'user:email'
    
    def get_authorization_url(self, state: str) -> str:
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': self.scope,
            'state': state
        }
        if self.provider == 'google':
            params['access_type'] = 'offline'
            params['prompt'] = 'consent'
        return f"{self.auth_url}?{urlencode(params)}"
    
    def exchange_code(self, code: str) -> Optional[Dict]:
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
            'code': code,
            'grant_type': 'authorization_code'
        }
        try:
            if self.provider == 'github':
                headers = {'Accept': 'application/json'}
                response = requests.post(self.token_url, data=data, headers=headers)
            else:
                response = requests.post(self.token_url, data=data)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Token Exchange Error: {e}")
            return None
    
    def get_user_info(self, access_token: str) -> Optional[Dict]:
        try:
            if self.provider == 'github':
                headers = {'Authorization': f'token {access_token}'}
                response = requests.get(self.userinfo_url, headers=headers)
            else:
                headers = {'Authorization': f'Bearer {access_token}'}
                response = requests.get(self.userinfo_url, headers=headers)
            if response.status_code == 200:
                user_data = response.json()
                return {
                    'provider': self.provider,
                    'id': user_data.get('id') or user_data.get('sub'),
                    'email': user_data.get('email'),
                    'name': user_data.get('name') or user_data.get('login'),
                    'avatar': user_data.get('avatar_url') or user_data.get('picture')
                }
            return None
        except Exception as e:
            print(f"User Info Error: {e}")
            return None
    
    def login(self, code: str) -> Optional[Dict]:
        token_data = self.exchange_code(code)
        if not token_data:
            return None
        return self.get_user_info(token_data.get('access_token'))

if __name__ == '__main__':
    oauth = OAuthClient(provider='google')
    auth_url = oauth.get_authorization_url('random_state_123')
    print(f"Authorization URL: {auth_url}")
