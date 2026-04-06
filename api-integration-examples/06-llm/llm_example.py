"""
API 集成示例 06: 大模型 API (OpenAI/Claude/国产)
=============================================
功能：文本生成、对话、Embedding
依赖：pip install openai anthropic dashscope
"""

import os
from typing import List, Optional, Dict, Generator
from pydantic import BaseModel

# ==================== OpenAI 集成 ====================

class OpenAIConfig:
    """OpenAI 配置"""
    API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    BASE_URL = "https://api.openai.com/v1"
    MODEL = "gpt-4"
    MAX_TOKENS = 2048
    TEMPERATURE = 0.7

class OpenAIClient:
    """OpenAI API 客户端"""
    
    def __init__(self):
        self.config = OpenAIConfig()
        # 实际使用需要初始化客户端
        # from openai import OpenAI
        # self.client = OpenAI(
        #     api_key=self.config.API_KEY,
        #     base_url=self.config.BASE_URL
        # )
    
    def chat_completion(self, messages: List[Dict], 
                       model: Optional[str] = None,
                       temperature: float = 0.7,
                       max_tokens: int = 2048) -> Dict:
        """
        聊天补全
        
        Args:
            messages: 消息列表 [{"role": "user", "content": "你好"}]
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大 token 数
            
        Returns:
            响应结果
        """
        # 实际调用
        # response = self.client.chat.completions.create(
        #     model=model or self.config.MODEL,
        #     messages=messages,
        #     temperature=temperature,
        #     max_tokens=max_tokens
        # )
        # return {
        #     "content": response.choices[0].message.content,
        #     "usage": response.usage.model_dump()
        # }
        
        return {
            "success": True,
            "content": "这是模拟的 AI 回复内容",
            "model": model or self.config.MODEL,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30
            }
        }
    
    def stream_chat(self, messages: List[Dict]) -> Generator[str, None, None]:
        """
        流式聊天
        
        Args:
            messages: 消息列表
            
        Yields:
            流式响应内容
        """
        # 实际调用
        # stream = self.client.chat.completions.create(
        #     model=self.config.MODEL,
        #     messages=messages,
        #     stream=True
        # )
        # for chunk in stream:
        #     if chunk.choices[0].delta.content:
        #         yield chunk.choices[0].delta.content
        
        yield "这是"
        yield "流式"
        yield "响应"
    
    def embeddings(self, input: str, model: str = "text-embedding-ada-002") -> Dict:
        """
        生成 Embedding
        
        Args:
            input: 输入文本
            model: Embedding 模型
            
        Returns:
            Embedding 向量
        """
        # 实际调用
        # response = self.client.embeddings.create(
        #     model=model,
        #     input=input
        # )
        # return {
        #     "embedding": response.data[0].embedding,
        #     "usage": response.usage.model_dump()
        # }
        
        return {
            "success": True,
            "embedding": [0.1, 0.2, 0.3, 0.4, 0.5],  # 模拟向量
            "dimensions": 1536,
            "model": model
        }
    
    def generate_image(self, prompt: str, size: str = "1024x1024",
                      n: int = 1) -> Dict:
        """
        生成图片
        
        Args:
            prompt: 提示词
            size: 图片尺寸
            n: 生成数量
            
        Returns:
            图片 URL
        """
        # 实际调用
        # response = self.client.images.generate(
        #     model="dall-e-3",
        #     prompt=prompt,
        #     size=size,
        #     n=n
        # )
        # return {
        #     "url": response.data[0].url
        # }
        
        return {
            "success": True,
            "url": "https://example.com/generated-image.png",
            "prompt": prompt,
            "size": size
        }

# ==================== Claude (Anthropic) 集成 ====================

class ClaudeConfig:
    """Claude 配置"""
    API_KEY = "sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    MODEL = "claude-3-sonnet-20240229"
    MAX_TOKENS = 4096

class ClaudeClient:
    """Claude API 客户端"""
    
    def __init__(self):
        self.config = ClaudeConfig()
        # 实际使用需要初始化客户端
        # import anthropic
        # self.client = anthropic.Anthropic(api_key=self.config.API_KEY)
    
    def create_message(self, messages: List[Dict],
                      max_tokens: int = 4096,
                      temperature: float = 0.7) -> Dict:
        """
        创建消息
        
        Args:
            messages: 消息列表
            max_tokens: 最大 token 数
            temperature: 温度参数
            
        Returns:
            响应结果
        """
        # 实际调用
        # response = self.client.messages.create(
        #     model=self.config.MODEL,
        #     max_tokens=max_tokens,
        #     messages=messages
        # )
        # return {
        #     "content": response.content[0].text,
        #     "usage": {
        #         "input_tokens": response.usage.input_tokens,
        #         "output_tokens": response.usage.output_tokens
        #     }
        # }
        
        return {
            "success": True,
            "content": "这是 Claude 的模拟回复",
            "model": self.config.MODEL,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 200
            }
        }
    
    def stream_message(self, messages: List[Dict]) -> Generator[str, None, None]:
        """
        流式消息
        
        Args:
            messages: 消息列表
            
        Yields:
            流式响应内容
        """
        # 实际调用
        # with self.client.messages.stream(
        #     model=self.config.MODEL,
        #     max_tokens=4096,
        #     messages=messages
        # ) as stream:
        #     for text in stream.text_stream:
        #         yield text
        
        yield "这是"
        yield "Claude"
        yield "流式响应"

# ==================== 国产大模型集成（阿里云通义千问） ====================

class QwenConfig:
    """通义千问配置"""
    API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    MODEL = "qwen-max"
    DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

class QwenClient:
    """通义千问 API 客户端"""
    
    def __init__(self):
        self.config = QwenConfig()
        # 实际使用需要初始化
        # import dashscope
        # dashscope.api_key = self.config.API_KEY
    
    def chat(self, messages: List[Dict],
            model: Optional[str] = None) -> Dict:
        """
        聊天
        
        Args:
            messages: 消息列表
            model: 模型名称
            
        Returns:
            响应结果
        """
        # 实际调用
        # from dashscope import Generation
        # response = Generation.call(
        #     model=model or self.config.MODEL,
        #     messages=messages
        # )
        # return {
        #     "content": response.output.choices[0].message.content,
        #     "usage": response.usage
        # }
        
        return {
            "success": True,
            "content": "这是通义千问的模拟回复",
            "model": model or self.config.MODEL,
            "usage": {
                "input_tokens": 50,
                "output_tokens": 100
            }
        }
    
    def text_embedding(self, text: str,
                      model: str = "text-embedding-v2") -> Dict:
        """
        文本 Embedding
        
        Args:
            text: 输入文本
            model: Embedding 模型
            
        Returns:
            Embedding 向量
        """
        # 实际调用
        # from dashscope import TextEmbedding
        # response = TextEmbedding.call(
        #     model=model,
        #     input=text
        # )
        # return {
        #     "embedding": response.output.embeddings[0].embedding
        # }
        
        return {
            "success": True,
            "embedding": [0.1, 0.2, 0.3, 0.4, 0.5],
            "dimensions": 1536
        }

# ==================== 大模型服务封装 ====================

class LLMProvider:
    """大模型服务商枚举"""
    OPENAI = "openai"
    CLAUDE = "claude"
    QWEN = "qwen"

class LLMService:
    """大模型服务（支持多服务商）"""
    
    def __init__(self, provider: str = LLMProvider.OPENAI):
        self.provider = provider
        if provider == LLMProvider.OPENAI:
            self.client = OpenAIClient()
        elif provider == LLMProvider.CLAUDE:
            self.client = ClaudeClient()
        elif provider == LLMProvider.QWEN:
            self.client = QwenClient()
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    def chat(self, messages: List[Dict], **kwargs) -> Dict:
        """聊天"""
        if self.provider == LLMProvider.OPENAI:
            return self.client.chat_completion(messages, **kwargs)
        elif self.provider == LLMProvider.CLAUDE:
            return self.client.create_message(messages, **kwargs)
        elif self.provider == LLMProvider.QWEN:
            return self.client.chat(messages, **kwargs)
    
    def embed(self, text: str) -> Dict:
        """生成 Embedding"""
        if self.provider == LLMProvider.OPENAI:
            return self.client.embeddings(text)
        elif self.provider == LLMProvider.QWEN:
            return self.client.text_embedding(text)
        else:
            raise ValueError(f"Embedding not supported for {self.provider}")

# ==================== 使用示例 ====================

if __name__ == "__main__":
    # OpenAI 示例
    openai_client = OpenAIClient()
    messages = [{"role": "user", "content": "你好，请介绍一下自己"}]
    response = openai_client.chat_completion(messages)
    print(f"OpenAI 回复：{response['content']}")
    
    # Claude 示例
    claude_client = ClaudeClient()
    messages = [{"role": "user", "content": "你好"}]
    response = claude_client.create_message(messages)
    print(f"Claude 回复：{response['content']}")
    
    # 通义千问示例
    qwen_client = QwenClient()
    messages = [{"role": "user", "content": "你好"}]
    response = qwen_client.chat(messages)
    print(f"通义千问回复：{response['content']}")
    
    # 统一服务示例
    llm_service = LLMService(provider=LLMProvider.OPENAI)
    response = llm_service.chat([{"role": "user", "content": "测试"}])
    print(f"统一 LLM 服务：{response['content']}")
