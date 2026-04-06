"""
AI 大模型 API 集成示例代码
功能：文本对话、代码生成、文本分析、流式响应
支持：Claude、通义千问、OpenAI
"""

from typing import Optional, List, AsyncGenerator
from pydantic import BaseModel
from dotenv import load_dotenv
import os
import json

load_dotenv()

# Anthropic Claude 配置
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "your-anthropic-key")
ANTHROPIC_MODEL = "claude-3-sonnet-20240229"

# 通义千问配置
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "your-dashscope-key")
DASHSCOPE_MODEL = "qwen-max"

# OpenAI 配置
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "your-openai-key")
OPENAI_MODEL = "gpt-4-turbo-preview"


class ChatMessage(BaseModel):
    """聊天消息"""
    role: str  # system, user, assistant
    content: str


class ChatRequest(BaseModel):
    """聊天请求"""
    messages: List[ChatMessage]
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 2048
    stream: bool = False


class ChatResponse(BaseModel):
    """聊天响应"""
    success: bool
    content: str
    model: str
    usage: Optional[dict] = None
    message: str


# ============ Anthropic Claude ============

class ClaudeClient:
    """Anthropic Claude 客户端"""
    
    def __init__(self):
        self.api_key = ANTHROPIC_API_KEY
        self.model = ANTHROPIC_MODEL
        self.base_url = "https://api.anthropic.com/v1/messages"
    
    def chat(self, request: ChatRequest) -> ChatResponse:
        """
        聊天对话
        
        Args:
            request: 聊天请求
        
        Returns:
            聊天响应
        """
        import httpx
        
        # 构建请求体
        payload = {
            "model": request.model or self.model,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": msg.role, "content": msg.content}
                for msg in request.messages if msg.role != "system"
            ]
        }
        
        # 添加系统消息
        system_messages = [msg for msg in request.messages if msg.role == "system"]
        if system_messages:
            payload["system"] = system_messages[0].content
        
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        
        print(f"Claude 请求:")
        print(f"  模型：{payload['model']}")
        print(f"  消息数：{len(payload['messages'])}")
        print(f"  系统消息：{'system' in payload}")
        print()
        
        # 模拟响应
        return ChatResponse(
            success=True,
            content="这是一个模拟的 Claude 响应。实际调用需要发送 HTTP 请求到 Anthropic API。",
            model=payload["model"],
            usage={"input_tokens": 100, "output_tokens": 200},
            message="成功"
        )
    
    def chat_stream(
        self,
        request: ChatRequest
    ) -> AsyncGenerator[str, None]:
        """
        流式聊天
        
        Args:
            request: 聊天请求
        
        Yields:
            响应文本片段
        """
        # 实际实现需要处理 SSE 流
        yield "这是"
        yield "流式"
        yield "响应"
        yield "示例"


# ============ 通义千问 ============

class DashScopeClient:
    """通义千问客户端"""
    
    def __init__(self):
        self.api_key = DASHSCOPE_API_KEY
        self.model = DASHSCOPE_MODEL
    
    def chat(self, request: ChatRequest) -> ChatResponse:
        """
        聊天对话
        
        Args:
            request: 聊天请求
        
        Returns:
            聊天响应
        """
        # 构建消息格式
        messages = []
        for msg in request.messages:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        print(f"通义千问请求:")
        print(f"  模型：{request.model or self.model}")
        print(f"  消息数：{len(messages)}")
        print()
        
        # 模拟响应
        return ChatResponse(
            success=True,
            content="这是一个模拟的通义千问响应。实际调用需要使用 dashscope SDK。",
            model=request.model or self.model,
            usage={"input_tokens": 100, "output_tokens": 200},
            message="成功"
        )
    
    def generate_code(
        self,
        prompt: str,
        language: str = "python"
    ) -> ChatResponse:
        """
        代码生成
        
        Args:
            prompt: 代码生成提示
            language: 编程语言
        
        Returns:
            聊天响应
        """
        system_message = ChatMessage(
            role="system",
            content=f"你是一个专业的{language}程序员。请生成高质量、可运行的代码。"
        )
        
        user_message = ChatMessage(
            role="user",
            content=prompt
        )
        
        request = ChatRequest(
            messages=[system_message, user_message],
            temperature=0.3,  # 代码生成需要更确定
            max_tokens=4096
        )
        
        return self.chat(request)
    
    def analyze_text(
        self,
        text: str,
        task: str = "summary"
    ) -> ChatResponse:
        """
        文本分析
        
        Args:
            text: 待分析文本
            task: 分析任务（summary/sentiment/keywords）
        
        Returns:
            聊天响应
        """
        task_prompts = {
            "summary": "请总结以下内容：",
            "sentiment": "请分析以下内容的情感倾向：",
            "keywords": "请提取以下内容的关键词："
        }
        
        user_message = ChatMessage(
            role="user",
            content=f"{task_prompts.get(task, '请分析：')}\n\n{text}"
        )
        
        request = ChatRequest(
            messages=[user_message],
            temperature=0.5
        )
        
        return self.chat(request)


# ============ OpenAI ============

class OpenAIClient:
    """OpenAI 客户端"""
    
    def __init__(self):
        self.api_key = OPENAI_API_KEY
        self.model = OPENAI_MODEL
        self.base_url = "https://api.openai.com/v1"
    
    def chat(self, request: ChatRequest) -> ChatResponse:
        """
        聊天对话
        
        Args:
            request: 聊天请求
        
        Returns:
            聊天响应
        """
        # 构建消息格式
        messages = []
        for msg in request.messages:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        print(f"OpenAI 请求:")
        print(f"  模型：{request.model or self.model}")
        print(f"  消息数：{len(messages)}")
        print(f"  温度：{request.temperature}")
        print()
        
        # 模拟响应
        return ChatResponse(
            success=True,
            content="这是一个模拟的 OpenAI 响应。实际调用需要使用 openai SDK。",
            model=request.model or self.model,
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 200,
                "total_tokens": 300
            },
            message="成功"
        )
    
    def chat_stream(
        self,
        request: ChatRequest
    ) -> AsyncGenerator[str, None]:
        """
        流式聊天
        
        Args:
            request: 聊天请求
        
        Yields:
            响应文本片段
        """
        # 实际实现需要处理 SSE 流
        yield "这是"
        yield "OpenAI"
        yield "流式"
        yield "响应"


# ============ 统一 AI 服务 ============

class AIService:
    """统一 AI 服务"""
    
    def __init__(self, provider: str = "dashscope"):
        """
        初始化 AI 服务
        
        Args:
            provider: 服务提供商（claude/dashscope/openai）
        """
        self.provider = provider
        if provider == "claude":
            self.client = ClaudeClient()
        elif provider == "dashscope":
            self.client = DashScopeClient()
        elif provider == "openai":
            self.client = OpenAIClient()
        else:
            raise ValueError(f"不支持的服务商：{provider}")
    
    def chat(
        self,
        messages: List[ChatMessage],
        temperature: float = 0.7
    ) -> ChatResponse:
        """聊天对话"""
        request = ChatRequest(
            messages=messages,
            temperature=temperature
        )
        return self.client.chat(request)
    
    def ask(self, question: str) -> ChatResponse:
        """
        简单问答
        
        Args:
            question: 问题
        
        Returns:
            响应
        """
        message = ChatMessage(role="user", content=question)
        return self.chat([message])
    
    def generate_code(self, prompt: str, language: str = "python") -> ChatResponse:
        """代码生成"""
        if isinstance(self.client, DashScopeClient):
            return self.client.generate_code(prompt, language)
        else:
            system_message = ChatMessage(
                role="system",
                content=f"你是一个专业的{language}程序员。"
            )
            user_message = ChatMessage(role="user", content=prompt)
            return self.chat([system_message, user_message], temperature=0.3)
    
    def summarize(self, text: str) -> ChatResponse:
        """文本摘要"""
        if isinstance(self.client, DashScopeClient):
            return self.client.analyze_text(text, "summary")
        else:
            message = ChatMessage(
                role="user",
                content=f"请总结以下内容：\n\n{text}"
            )
            return self.chat([message])


# ============ 使用示例 ============

def example_ai():
    """AI 大模型示例"""
    print("=== AI 大模型 API 示例 ===\n")
    
    # 1. Claude 对话
    print("1. Claude 对话:")
    claude_client = ClaudeClient()
    messages = [
        ChatMessage(role="system", content="你是一个有帮助的助手。"),
        ChatMessage(role="user", content="你好，请介绍一下自己。")
    ]
    request = ChatRequest(messages=messages)
    response = claude_client.chat(request)
    print(f"   模型：{response.model}")
    print(f"   响应：{response.content[:50]}...")
    print()
    
    # 2. 通义千问代码生成
    print("2. 通义千问代码生成:")
    dashscope_client = DashScopeClient()
    code_response = dashscope_client.generate_code(
        prompt="写一个快速排序函数",
        language="python"
    )
    print(f"   模型：{code_response.model}")
    print(f"   响应：{code_response.content[:50]}...")
    print()
    
    # 3. 通义千问文本分析
    print("3. 通义千问文本分析:")
    text = "今天天气很好，心情也很愉快。阳光明媚，适合外出游玩。"
    analysis = dashscope_client.analyze_text(text, "sentiment")
    print(f"   文本：{text}")
    print(f"   分析结果：{analysis.content[:50]}...")
    print()
    
    # 4. OpenAI 对话
    print("4. OpenAI 对话:")
    openai_client = OpenAIClient()
    messages = [
        ChatMessage(role="user", content="什么是人工智能？")
    ]
    request = ChatRequest(messages=messages, temperature=0.5)
    response = openai_client.chat(request)
    print(f"   模型：{response.model}")
    print(f"   响应：{response.content[:50]}...")
    print()
    
    # 5. 统一 AI 服务
    print("5. 统一 AI 服务:")
    ai_service = AIService(provider="dashscope")
    response = ai_service.ask("中国的首都是哪里？")
    print(f"   服务商：{ai_service.provider}")
    print(f"   问题：中国的首都是哪里？")
    print(f"   响应：{response.content[:50]}...")
    print()
    
    # 6. 统一 AI 服务 - 摘要
    print("6. 统一 AI 服务 - 摘要:")
    long_text = "人工智能（Artificial Intelligence，简称 AI）是计算机科学的一个分支，它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。该领域的研究包括机器人、语言识别、图像识别、自然语言处理和专家系统等。"
    summary = ai_service.summarize(long_text)
    print(f"   原文长度：{len(long_text)} 字")
    print(f"   摘要：{summary.content[:50]}...")


if __name__ == "__main__":
    example_ai()
