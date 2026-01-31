"""
Brain 模块 - 与 Claude API 交互

负责:
- 发送请求到 Claude API
- 管理对话历史
- 工具调用
- API 故障切换
"""

import logging
import asyncio
import time
from typing import Any, Optional
from dataclasses import dataclass, field

from anthropic import Anthropic
from anthropic.types import Message, MessageParam, ToolParam
from openai import OpenAI

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class Response:
    """LLM 响应"""
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)


@dataclass
class Context:
    """对话上下文"""
    messages: list[MessageParam] = field(default_factory=list)
    system: str = ""
    tools: list[ToolParam] = field(default_factory=list)


@dataclass
class LLMEndpoint:
    """LLM API 端点配置"""
    name: str
    api_key: str
    base_url: str
    model: str
    client_type: str = "anthropic"  # "anthropic" 或 "openai"
    priority: int = 0  # 优先级，数字越小优先级越高
    healthy: bool = True
    last_check: float = 0
    fail_count: int = 0


class Brain:
    """Agent 大脑 - LLM 交互层（支持故障切换）"""
    
    # 健康检查间隔（秒）
    HEALTH_CHECK_INTERVAL = 60
    # 失败阈值，超过则标记为不健康
    FAIL_THRESHOLD = 3
    # 请求超时（秒）
    REQUEST_TIMEOUT = 30
    # 主端点恢复检查间隔（秒）
    RECOVERY_CHECK_INTERVAL = 60
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ):
        self.max_tokens = max_tokens or settings.max_tokens
        
        # 配置多个 API 端点
        self._endpoints: list[LLMEndpoint] = []
        
        # 主端点（云雾 AI）
        primary_key = api_key or settings.anthropic_api_key
        primary_url = base_url or settings.anthropic_base_url
        primary_model = model or settings.default_model
        
        if primary_key:
            self._endpoints.append(LLMEndpoint(
                name="primary",
                api_key=primary_key,
                base_url=primary_url,
                model=primary_model,
                priority=0,
            ))
        
        # 第二备用端点（阿里 DashScope）
        dashscope_key = "DASHSCOPE_KEY_REMOVED"
        dashscope_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        dashscope_model = "qwen3-max-2026-01-23"
        
        if dashscope_key:
            self._endpoints.append(LLMEndpoint(
                name="backup-1 (Aliyun DashScope)",
                api_key=dashscope_key,
                base_url=dashscope_url,
                model=dashscope_model,
                client_type="openai",
                priority=1,
            ))
        
        # 第三备用端点（MiniMax）
        minimax_key = "MINIMAX_KEY_REMOVED"
        minimax_url = "https://api.minimaxi.com/anthropic"
        minimax_model = "MiniMax-M2.1"
        
        if minimax_key:
            self._endpoints.append(LLMEndpoint(
                name="backup-2 (MiniMax)",
                api_key=minimax_key,
                base_url=minimax_url,
                model=minimax_model,
                client_type="anthropic",
                priority=2,
            ))
        
        if not self._endpoints:
            raise ValueError(
                "At least one API key is required. "
                "Set ANTHROPIC_API_KEY environment variable or pass api_key parameter."
            )
        
        # 按优先级排序
        self._endpoints.sort(key=lambda x: x.priority)
        
        # 当前使用的端点索引
        self._current_endpoint_idx = 0
        
        # 创建客户端（设置超时和禁用自动重试）
        self._anthropic_clients: dict[str, Anthropic] = {}
        self._openai_clients: dict[str, OpenAI] = {}
        
        for ep in self._endpoints:
            if ep.client_type == "openai":
                self._openai_clients[ep.name] = OpenAI(
                    api_key=ep.api_key,
                    base_url=ep.base_url,
                    timeout=self.REQUEST_TIMEOUT,
                    max_retries=0,
                )
            else:
                self._anthropic_clients[ep.name] = Anthropic(
                    api_key=ep.api_key,
                    base_url=ep.base_url,
                    timeout=self.REQUEST_TIMEOUT,
                    max_retries=0,
                )
        
        # 兼容旧代码的 _clients 属性
        self._clients = self._anthropic_clients
        
        # 公开属性（兼容旧代码）
        self._update_public_attrs()
        
        logger.info(f"Brain initialized with {len(self._endpoints)} endpoints")
        for ep in self._endpoints:
            logger.info(f"  - {ep.name}: {ep.model} @ {ep.base_url}")
        
        # 启动时检测端点健康度
        self._startup_health_check()
    
    def _startup_health_check(self) -> None:
        """
        启动时并行检测所有端点健康度
        
        同时测试所有端点，选择优先级最高的可用端点
        """
        import concurrent.futures
        
        logger.info("Performing startup health check (parallel)...")
        
        results = {}  # endpoint_idx -> (success, error)
        
        def test_one(idx: int, endpoint: LLMEndpoint) -> tuple:
            logger.info(f"  Testing {endpoint.name}...")
            try:
                self._test_endpoint_with_timeout(endpoint, timeout=15)
                return (idx, True, None)
            except Exception as e:
                return (idx, False, str(e))
        
        # 并行测试所有端点
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self._endpoints)) as executor:
            futures = [
                executor.submit(test_one, i, ep) 
                for i, ep in enumerate(self._endpoints)
            ]
            for future in concurrent.futures.as_completed(futures):
                idx, success, error = future.result()
                results[idx] = (success, error)
        
        # 处理结果，按优先级选择第一个可用的
        for i, endpoint in enumerate(self._endpoints):
            success, error = results.get(i, (False, "No result"))
            if success:
                endpoint.healthy = True
                endpoint.fail_count = 0
                endpoint.last_check = time.time()
                logger.info(f"  ✓ {endpoint.name} is healthy")
            else:
                endpoint.healthy = False
                endpoint.fail_count = self.FAIL_THRESHOLD
                logger.warning(f"  ✗ {endpoint.name} failed: {error}")
        
        # 选择优先级最高的可用端点
        for i, endpoint in enumerate(self._endpoints):
            if endpoint.healthy:
                self._current_endpoint_idx = i
                self._update_public_attrs()
                logger.info(f"  → Using {endpoint.name} as current endpoint")
                logger.info(f"")
                logger.info(f"  ╔══════════════════════════════════════════╗")
                logger.info(f"  ║  当前 LLM: {endpoint.model:<30}║")
                logger.info(f"  ╚══════════════════════════════════════════╝")
                return
        
        # 所有端点都失败
        logger.error("All endpoints failed health check! Will retry on first request.")
        self._current_endpoint_idx = 0
        self._update_public_attrs()
        ep = self._endpoints[0]
        logger.info(f"  当前 LLM (fallback): {ep.model}")
    
    def _update_public_attrs(self) -> None:
        """更新公开属性"""
        ep = self._endpoints[self._current_endpoint_idx]
        self.api_key = ep.api_key
        self.base_url = ep.base_url
        self.model = ep.model
        # client 属性保持 Anthropic 客户端兼容（如果当前端点是 OpenAI，则使用第一个 Anthropic 客户端）
        if ep.client_type == "anthropic" and ep.name in self._anthropic_clients:
            self.client = self._anthropic_clients[ep.name]
        elif self._anthropic_clients:
            self.client = list(self._anthropic_clients.values())[0]
        else:
            self.client = None
    
    def _get_healthy_endpoint(self) -> Optional[LLMEndpoint]:
        """获取健康的端点"""
        for ep in self._endpoints:
            if ep.healthy:
                return ep
        # 如果所有都不健康，重置并返回第一个
        for ep in self._endpoints:
            ep.healthy = True
            ep.fail_count = 0
        return self._endpoints[0] if self._endpoints else None
    
    def _mark_endpoint_failed(self, endpoint: LLMEndpoint) -> None:
        """标记端点失败"""
        endpoint.fail_count += 1
        if endpoint.fail_count >= self.FAIL_THRESHOLD:
            endpoint.healthy = False
            logger.warning(f"Endpoint {endpoint.name} marked as unhealthy after {endpoint.fail_count} failures")
    
    def _mark_endpoint_success(self, endpoint: LLMEndpoint) -> None:
        """标记端点成功"""
        endpoint.fail_count = 0
        endpoint.healthy = True
        endpoint.last_check = time.time()
    
    async def health_check(self, endpoint: Optional[LLMEndpoint] = None) -> bool:
        """
        健康检查
        
        Args:
            endpoint: 要检查的端点，None 则检查当前端点
        
        Returns:
            是否健康
        """
        if endpoint is None:
            endpoint = self._endpoints[self._current_endpoint_idx]
        
        try:
            # 发送简单测试请求
            await asyncio.wait_for(
                asyncio.to_thread(self._test_endpoint, endpoint),
                timeout=10,
            )
            self._mark_endpoint_success(endpoint)
            logger.info(f"Health check passed for {endpoint.name}")
            return True
        except Exception as e:
            self._mark_endpoint_failed(endpoint)
            logger.warning(f"Health check failed for {endpoint.name}: {e}")
            return False
    
    def switch_to_backup(self) -> bool:
        """
        切换到备用端点
        
        Returns:
            是否成功切换
        """
        old_ep = self._endpoints[self._current_endpoint_idx]
        
        # 查找下一个健康的端点
        for i, ep in enumerate(self._endpoints):
            if i != self._current_endpoint_idx and ep.healthy:
                self._current_endpoint_idx = i
                self._update_public_attrs()
                logger.info(f"Switched from {old_ep.name} to {ep.name}")
                return True
        
        logger.warning("No healthy backup endpoint available")
        return False
    
    def get_current_endpoint_info(self) -> dict:
        """获取当前端点信息"""
        ep = self._endpoints[self._current_endpoint_idx]
        return {
            "name": ep.name,
            "model": ep.model,
            "base_url": ep.base_url,
            "healthy": ep.healthy,
        }
    
    def messages_create(self, **kwargs) -> Message:
        """
        同步调用 LLM API（带故障切换）
        
        这是对 client.messages.create 的包装，自动处理故障切换。
        Agent 中应使用此方法而不是直接调用 client.messages.create。
        支持 Anthropic 和 OpenAI 客户端。
        
        故障切换逻辑:
        1. 使用当前端点（_current_endpoint_idx）
        2. 失败则切到下一个端点，并"粘住"
        3. 定期检测主端点是否恢复（每 RECOVERY_CHECK_INTERVAL 秒）
        4. 主端点恢复后才切回
        
        Args:
            **kwargs: 传递给 messages.create 的参数（Anthropic 格式）
        
        Returns:
            LLM 响应（Anthropic Message 格式）
        """
        last_error = None
        tried_indices = set()
        
        # 检查是否应该尝试恢复主端点
        self._maybe_recover_primary()
        
        # 从当前端点开始尝试
        start_idx = self._current_endpoint_idx
        
        while len(tried_indices) < len(self._endpoints):
            idx = (start_idx + len(tried_indices)) % len(self._endpoints)
            if idx in tried_indices:
                break
            tried_indices.add(idx)
            
            endpoint = self._endpoints[idx]
            
            try:
                logger.info(f"Sending request to {endpoint.name} ({endpoint.model})")
                
                if endpoint.client_type == "openai":
                    response = self._call_openai_endpoint(endpoint, kwargs)
                else:
                    response = self._call_anthropic_endpoint(endpoint, kwargs)
                
                # 成功，更新当前端点
                self._mark_endpoint_success(endpoint)
                self._current_endpoint_idx = idx
                self._update_public_attrs()
                
                if idx > 0:
                    logger.info(f"")
                    logger.info(f"  ╔══════════════════════════════════════════╗")
                    logger.info(f"  ║  故障切换 → {endpoint.model:<28}║")
                    logger.info(f"  ╚══════════════════════════════════════════╝")
                
                return response
                
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Request failed for {endpoint.name}: {e}")
                self._mark_endpoint_failed(endpoint)
                
                # 继续尝试下一个端点
                logger.info(f"Trying next endpoint...")
        
        raise RuntimeError(f"All LLM endpoints failed: {last_error}")
    
    def _call_anthropic_endpoint(self, endpoint: LLMEndpoint, kwargs: dict) -> Message:
        """调用 Anthropic 格式的端点"""
        client = self._anthropic_clients[endpoint.name]
        request_kwargs = kwargs.copy()
        request_kwargs["model"] = endpoint.model
        return client.messages.create(**request_kwargs)
    
    def _call_openai_endpoint(self, endpoint: LLMEndpoint, kwargs: dict) -> Message:
        """
        调用 OpenAI 格式的端点（如阿里 DashScope）
        
        将 Anthropic 格式转换为 OpenAI 格式，然后将响应转回 Anthropic Message 格式
        """
        client = self._openai_clients[endpoint.name]
        
        # 转换消息格式: Anthropic -> OpenAI
        openai_messages = []
        
        # 系统消息
        if "system" in kwargs and kwargs["system"]:
            openai_messages.append({
                "role": "system",
                "content": kwargs["system"]
            })
        
        # 用户/助手消息
        for msg in kwargs.get("messages", []):
            role = msg["role"]
            content = msg["content"]
            
            # 处理复杂内容（如工具调用结果）
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif part.get("type") == "tool_result":
                            # 工具结果作为普通文本处理
                            tool_content = part.get("content", "")
                            if isinstance(tool_content, list):
                                for tc in tool_content:
                                    if isinstance(tc, dict) and tc.get("type") == "text":
                                        text_parts.append(tc.get("text", ""))
                            else:
                                text_parts.append(str(tool_content))
                    else:
                        text_parts.append(str(part))
                content = "\n".join(text_parts)
            
            openai_messages.append({
                "role": role,
                "content": content
            })
        
        # 调用 OpenAI API（启用 thinking 模式）
        max_tokens = kwargs.get("max_tokens", 4096)
        
        response = client.chat.completions.create(
            model=endpoint.model,
            messages=openai_messages,
            max_tokens=max_tokens,
            # Qwen3 支持 enable_thinking 参数
            extra_body={"enable_thinking": True}
        )
        
        # 转换响应: OpenAI -> Anthropic Message 格式
        choice = response.choices[0]
        content_text = choice.message.content or ""
        
        # 构造 Anthropic Message 格式的响应
        from anthropic.types import Message as AnthropicMessage, TextBlock, Usage
        
        # 创建模拟的 Anthropic Message 对象
        return AnthropicMessage(
            id=response.id or "msg_openai",
            type="message",
            role="assistant",
            content=[TextBlock(type="text", text=content_text)],
            model=endpoint.model,
            stop_reason="end_turn" if choice.finish_reason == "stop" else choice.finish_reason,
            stop_sequence=None,
            usage=Usage(
                input_tokens=response.usage.prompt_tokens if response.usage else 0,
                output_tokens=response.usage.completion_tokens if response.usage else 0,
            )
        )
    
    def _maybe_recover_primary(self) -> None:
        """
        检查是否应该尝试恢复到主端点（非阻塞）
        
        启动后台线程检测，不阻塞正常请求
        """
        if self._current_endpoint_idx == 0:
            return  # 已经是主端点
        
        primary = self._endpoints[0]
        now = time.time()
        
        # 每 60 秒检查一次主端点是否恢复
        if now - primary.last_check < self.RECOVERY_CHECK_INTERVAL:
            return
        
        # 避免重复检测
        if getattr(self, '_recovery_check_in_progress', False):
            return
        
        primary.last_check = now
        self._recovery_check_in_progress = True
        
        # 启动后台线程检测，不阻塞当前请求
        import threading
        def check_primary():
            try:
                logger.info(f"[Background] Checking if primary endpoint has recovered...")
                self._test_endpoint_with_timeout(primary, timeout=10)
                
                # 主端点恢复了
                logger.info(f"")
                logger.info(f"  ╔══════════════════════════════════════════╗")
                logger.info(f"  ║  主端点恢复 → {primary.model:<26}║")
                logger.info(f"  ╚══════════════════════════════════════════╝")
                primary.healthy = True
                primary.fail_count = 0
                self._current_endpoint_idx = 0
                self._update_public_attrs()
                
            except Exception as e:
                logger.debug(f"[Background] Primary endpoint still down: {e}")
            finally:
                self._recovery_check_in_progress = False
        
        thread = threading.Thread(target=check_primary, daemon=True)
        thread.start()
    
    def _test_endpoint_with_timeout(self, endpoint: LLMEndpoint, timeout: float = 10) -> None:
        """测试端点（带短超时）"""
        if endpoint.client_type == "openai":
            # 创建临时客户端（短超时）
            client = OpenAI(
                api_key=endpoint.api_key,
                base_url=endpoint.base_url,
                timeout=timeout,
                max_retries=0,
            )
            client.chat.completions.create(
                model=endpoint.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
        else:
            client = Anthropic(
                api_key=endpoint.api_key,
                base_url=endpoint.base_url,
                timeout=timeout,
                max_retries=0,
            )
            client.messages.create(
                model=endpoint.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
    
    def _test_endpoint(self, endpoint: LLMEndpoint) -> None:
        """测试端点是否可用"""
        if endpoint.client_type == "openai":
            client = self._openai_clients[endpoint.name]
            client.chat.completions.create(
                model=endpoint.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
        else:
            client = self._anthropic_clients[endpoint.name]
            client.messages.create(
                model=endpoint.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}],
            )
    
    async def think(
        self,
        prompt: str,
        context: Optional[Context] = None,
        system: Optional[str] = None,
        tools: Optional[list[ToolParam]] = None,
    ) -> Response:
        """
        发送思考请求到 LLM（带自动故障切换）
        
        Args:
            prompt: 用户输入
            context: 对话上下文
            system: 系统提示词
            tools: 可用工具列表
        
        Returns:
            LLM 响应
        """
        messages: list[MessageParam] = []
        
        # 添加上下文中的历史消息
        if context and context.messages:
            messages.extend(context.messages)
        
        # 添加当前用户消息
        messages.append({"role": "user", "content": prompt})
        
        # 确定系统提示词
        sys_prompt = system or (context.system if context else "")
        
        # 确定工具列表
        tool_list = tools or (context.tools if context else [])
        
        # 尝试所有端点
        last_error = None
        tried_endpoints = set()
        
        while len(tried_endpoints) < len(self._endpoints):
            endpoint = self._get_healthy_endpoint()
            if endpoint is None or endpoint.name in tried_endpoints:
                # 没有更多端点可尝试
                break
            
            tried_endpoints.add(endpoint.name)
            client = self._clients[endpoint.name]
            
            try:
                # 构建请求参数
                request_params: dict[str, Any] = {
                    "model": endpoint.model,
                    "max_tokens": self.max_tokens,
                    "messages": messages,
                }
                
                if sys_prompt:
                    request_params["system"] = sys_prompt
                
                if tool_list:
                    request_params["tools"] = tool_list
                
                # 发送请求（带超时）
                logger.info(f"Sending request to {endpoint.name} ({endpoint.model})")
                
                response: Message = await asyncio.wait_for(
                    asyncio.to_thread(client.messages.create, **request_params),
                    timeout=self.REQUEST_TIMEOUT,
                )
                
                # 成功，标记端点健康
                self._mark_endpoint_success(endpoint)
                
                # 解析响应
                content = ""
                tool_calls = []
                
                for block in response.content:
                    if block.type == "text":
                        content += block.text
                    elif block.type == "tool_use":
                        tool_calls.append({
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                
                return Response(
                    content=content,
                    tool_calls=tool_calls,
                    stop_reason=response.stop_reason or "",
                    usage={
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                    },
                )
                
            except asyncio.TimeoutError:
                last_error = f"Request timeout ({self.REQUEST_TIMEOUT}s) for {endpoint.name}"
                logger.warning(last_error)
                self._mark_endpoint_failed(endpoint)
                # 尝试切换到备用
                self.switch_to_backup()
                
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Request failed for {endpoint.name}: {e}")
                self._mark_endpoint_failed(endpoint)
                # 尝试切换到备用
                self.switch_to_backup()
        
        # 所有端点都失败
        logger.error(f"All endpoints failed. Last error: {last_error}")
        raise RuntimeError(f"All LLM endpoints failed: {last_error}")
    
    async def plan(self, task: str, context: Optional[Context] = None) -> str:
        """
        为任务生成执行计划
        
        Args:
            task: 任务描述
            context: 上下文
        
        Returns:
            执行计划文本
        """
        prompt = f"""请为以下任务制定详细的执行计划:

任务: {task}

要求:
1. 分解为具体的步骤
2. 识别需要的工具和技能
3. 考虑可能的失败情况和备选方案
4. 估计每个步骤的复杂度

请以 Markdown 格式输出计划。"""

        response = await self.think(prompt, context)
        return response.content
    
    async def generate_code(
        self,
        description: str,
        language: str = "python",
        context: Optional[Context] = None,
    ) -> str:
        """
        生成代码
        
        Args:
            description: 代码功能描述
            language: 编程语言
            context: 上下文
        
        Returns:
            生成的代码
        """
        prompt = f"""请生成以下功能的 {language} 代码:

{description}

要求:
1. 代码应该完整、可运行
2. 包含必要的导入语句
3. 添加适当的注释和 docstring
4. 遵循 {language} 的最佳实践
5. 如果是类，包含类型提示

只输出代码，不要解释。"""

        response = await self.think(prompt, context)
        
        # 提取代码块
        code = response.content
        if f"```{language}" in code:
            start = code.find(f"```{language}") + len(f"```{language}")
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()
        elif "```" in code:
            start = code.find("```") + 3
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()
        
        return code
    
    async def analyze_error(
        self,
        error: str,
        context: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        分析错误并提供解决方案
        
        Args:
            error: 错误信息
            context: 错误上下文
        
        Returns:
            分析结果和建议
        """
        prompt = f"""请分析以下错误并提供解决方案:

错误信息:
{error}

{"上下文:" + context if context else ""}

请提供:
1. 错误原因分析
2. 可能的解决方案（按优先级排序）
3. 如何避免类似错误

以 JSON 格式输出:
{{
    "cause": "错误原因",
    "solutions": ["解决方案1", "解决方案2"],
    "prevention": "预防措施"
}}"""

        response = await self.think(prompt)
        
        # 尝试解析 JSON
        import json
        try:
            content = response.content
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end].strip()
            
            return json.loads(content)
        except json.JSONDecodeError:
            return {
                "cause": "Unable to parse error analysis",
                "solutions": [response.content],
                "prevention": "",
            }
