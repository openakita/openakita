"""
API 集成示例 6: Claude AI (Anthropic)
"""
import requests
import json

class ClaudeClient:
    def __init__(self, api_key, model="claude-3-sonnet-20240229"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.anthropic.com/v1"
        self.headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
    
    def chat(self, messages, max_tokens=1024, system_prompt=None):
        """对话"""
        data = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages
        }
        
        if system_prompt:
            data["system"] = system_prompt
        
        response = requests.post(
            f"{self.base_url}/messages",
            headers=self.headers,
            json=data
        )
        
        if response.status_code == 200:
            return response.json()["content"][0]["text"]
        else:
            raise Exception(f"API Error: {response.text}")
    
    def complete(self, prompt, max_tokens=1024):
        """文本补全"""
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages, max_tokens)
    
    def analyze_image(self, image_base64, prompt):
        """图像分析"""
        messages = [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_base64}},
                {"type": "text", "text": prompt}
            ]
        }]
        return self.chat(messages)

# 使用示例
if __name__ == "__main__":
    claude = ClaudeClient("your_api_key")
    # response = claude.complete("你好，请介绍一下自己")
    # print(response)
    print("Claude AI 示例已就绪")
