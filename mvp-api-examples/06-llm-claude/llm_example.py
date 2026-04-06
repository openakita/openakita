# 大模型 API 集成示例（Claude / OpenAI）
# 适用于 MVP AI 对话、内容生成、智能分析

import os
from typing import Optional, List, Dict
import anthropic
from openai import OpenAI


class LLMClient:
    """大模型客户端封装（支持 Claude 和 OpenAI）"""
    
    def __init__(self, provider: str = "claude"):
        """
        初始化客户端
        
        Args:
            provider: 提供商（claude 或 openai）
        """
        self.provider = provider
        
        if provider == "claude":
            self.api_key = os.getenv("ANTHROPIC_API_KEY", "your-api-key")
            self.client = anthropic.Anthropic(api_key=self.api_key)
            self.model = os.getenv("ANTHROPIC_MODEL", "claude-3-sonnet-20240229")
        else:
            self.api_key = os.getenv("OPENAI_API_KEY", "your-api-key")
            self.client = OpenAI(api_key=self.api_key)
            self.model = os.getenv("OPENAI_MODEL", "gpt-4-turbo-preview")
    
    def chat(
        self,
        messages: List[Dict],
        system_prompt: str = None,
        max_tokens: int = 1024,
        temperature: float = 0.7
    ) -> dict:
        """
        聊天对话
        
        Args:
            messages: 消息列表 [{"role": "user/assistant", "content": "..."}]
            system_prompt: 系统提示词
            max_tokens: 最大生成 token 数
            temperature: 温度（0-1）
        
        Returns:
            响应结果
        """
        try:
            if self.provider == "claude":
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system_prompt or "You are a helpful assistant.",
                    messages=messages
                )
                return {
                    "success": True,
                    "content": response.content[0].text,
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens
                    }
                }
            else:
                api_messages = []
                if system_prompt:
                    api_messages.append({"role": "system", "content": system_prompt})
                api_messages.extend(messages)
                
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=api_messages,
                    max_tokens=max_tokens,
                    temperature=temperature
                )
                return {
                    "success": True,
                    "content": response.choices[0].message.content,
                    "usage": {
                        "input_tokens": response.usage.prompt_tokens,
                        "output_tokens": response.usage.completion_tokens
                    }
                }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def generate_text(self, prompt: str, system_prompt: str = None, **kwargs) -> dict:
        """
        文本生成（简单接口）
        
        Args:
            prompt: 用户提示
            system_prompt: 系统提示词
            **kwargs: 其他参数
        
        Returns:
            生成结果
        """
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages, system_prompt, **kwargs)
    
    def summarize(self, text: str, max_length: int = 200) -> dict:
        """
        文本摘要
        
        Args:
            text: 待摘要文本
            max_length: 最大长度
        
        Returns:
            摘要结果
        """
        prompt = f"请用简洁的语言总结以下内容，不超过{max_length}字：\n\n{text}"
        system_prompt = "你是一个专业的摘要助手，擅长提取关键信息。"
        return self.generate_text(prompt, system_prompt, max_tokens=500)
    
    def translate(self, text: str, target_language: str = "中文") -> dict:
        """
        文本翻译
        
        Args:
            text: 待翻译文本
            target_language: 目标语言
        
        Returns:
            翻译结果
        """
        prompt = f"将以下文本翻译成{target_language}，保持原意和语气：\n\n{text}"
        system_prompt = "你是一个专业的翻译助手，精通多种语言。"
        return self.generate_text(prompt, system_prompt, max_tokens=1024)
    
    def extract_entities(self, text: str) -> dict:
        """
        实体抽取
        
        Args:
            text: 待分析文本
        
        Returns:
            实体列表
        """
        prompt = f"从以下文本中提取所有命名实体（人名、地名、组织名、时间等），以 JSON 格式返回：\n\n{text}"
        system_prompt = "你是一个信息抽取专家，输出格式为 JSON。"
        return self.generate_text(prompt, system_prompt, max_tokens=500)
    
    def sentiment_analysis(self, text: str) -> dict:
        """
        情感分析
        
        Args:
            text: 待分析文本
        
        Returns:
            情感分析结果
        """
        prompt = f"分析以下文本的情感倾向（正面/负面/中性），并给出置信度：\n\n{text}"
        system_prompt = "你是一个情感分析专家。"
        return self.generate_text(prompt, system_prompt, max_tokens=200)
    
    def generate_code(self, description: str, language: str = "python") -> dict:
        """
        代码生成
        
        Args:
            description: 功能描述
            language: 编程语言
        
        Returns:
            生成的代码
        """
        prompt = f"请用{language}编写实现以下功能的代码：\n\n{description}"
        system_prompt = f"你是一个专业的{language}程序员，输出完整可运行的代码。"
        return self.generate_text(prompt, system_prompt, max_tokens=2048)
    
    def chat_with_context(
        self,
        user_message: str,
        context: List[Dict],
        system_prompt: str = None
    ) -> dict:
        """
        带上下文的对话
        
        Args:
            user_message: 用户消息
            context: 对话历史
            system_prompt: 系统提示词
        
        Returns:
            响应结果
        """
        messages = context + [{"role": "user", "content": user_message}]
        return self.chat(messages, system_prompt)


# 使用示例
if __name__ == "__main__":
    # 使用 Claude
    client = LLMClient(provider="claude")
    
    # 1. 简单对话
    result = client.generate_text(
        prompt="请介绍一下人工智能的发展历程",
        max_tokens=500
    )
    print(f"对话结果：{result}")
    
    # 2. 文本摘要
    text = "人工智能（AI）是计算机科学的一个分支，旨在创建能够执行需要人类智能的任务的系统。..."
    result = client.summarize(text, max_length=100)
    print(f"摘要结果：{result}")
    
    # 3. 代码生成
    result = client.generate_code(
        description="实现一个快速排序算法",
        language="python"
    )
    print(f"生成代码：{result}")
    
    # 4. 多轮对话
    context = []
    for i in range(3):
        result = client.chat_with_context(
            user_message=f"问题{i+1}",
            context=context
        )
        if result["success"]:
            context.append({"role": "user", "content": f"问题{i+1}"})
            context.append({"role": "assistant", "content": result["content"]})
