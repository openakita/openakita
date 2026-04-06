# 大模型 API 示例（Claude/OpenAI）
# 用于 MVP AI 功能集成

import os
import requests
from typing import Optional

class LLMClient:
    """大模型 API 客户端"""
    
    def __init__(self, api_key: str, provider: str = 'claude'):
        self.api_key = api_key
        self.provider = provider
        
        if provider == 'claude':
            self.base_url = 'https://api.anthropic.com/v1'
            self.headers = {
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            }
        elif provider == 'openai':
            self.base_url = 'https://api.openai.com/v1'
            self.headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            }
    
    def chat(self, message: str, system_prompt: Optional[str] = None) -> str:
        """发送聊天请求"""
        if self.provider == 'claude':
            return self._chat_claude(message, system_prompt)
        elif self.provider == 'openai':
            return self._chat_openai(message, system_prompt)
    
    def _chat_claude(self, message: str, system_prompt: Optional[str] = None) -> str:
        """Claude API 调用"""
        payload = {
            'model': 'claude-3-sonnet-20240229',
            'max_tokens': 1024,
            'messages': [{'role': 'user', 'content': message}]
        }
        
        if system_prompt:
            payload['system'] = system_prompt
        
        response = requests.post(
            f'{self.base_url}/messages',
            headers=self.headers,
            json=payload
        )
        
        if response.status_code == 200:
            return response.json()['content'][0]['text']
        else:
            raise Exception(f"Claude API Error: {response.text}")
    
    def _chat_openai(self, message: str, system_prompt: Optional[str] = None) -> str:
        """OpenAI API 调用"""
        messages = []
        
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        
        messages.append({'role': 'user', 'content': message})
        
        payload = {
            'model': 'gpt-4-turbo-preview',
            'messages': messages,
            'max_tokens': 1024
        }
        
        response = requests.post(
            f'{self.base_url}/chat/completions',
            headers=self.headers,
            json=payload
        )
        
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            raise Exception(f"OpenAI API Error: {response.text}")

# 使用示例
if __name__ == '__main__':
    # 从环境变量获取 API Key
    api_key = os.getenv('LLM_API_KEY', 'your-api-key-here')
    
    # 初始化客户端
    client = LLMClient(api_key, provider='claude')
    
    # 发送请求
    response = client.chat(
        message="请总结一下 MVP 开发的最佳实践",
        system_prompt="你是一个专业的技术顾问"
    )
    
    print(response)
