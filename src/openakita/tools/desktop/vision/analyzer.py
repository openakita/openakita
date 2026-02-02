"""
Windows 桌面自动化 - 视觉分析器

基于 DashScope Qwen-VL 实现 UI 视觉识别
"""

import sys
import json
import re
import logging
from typing import Optional, List, Any
from PIL import Image

from ..config import get_config
from ..types import (
    BoundingBox,
    ElementLocation,
    VisionResult,
    UIElement,
)
from ..capture import ScreenCapture, get_capture
from .prompts import PromptTemplates

# 平台检查
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. "
        f"Current platform: {sys.platform}"
    )

logger = logging.getLogger(__name__)


class VisionAnalyzer:
    """
    视觉分析器
    
    使用 DashScope Qwen-VL 分析截图，识别 UI 元素
    """
    
    def __init__(
        self,
        model: Optional[str] = None,
        capture: Optional[ScreenCapture] = None,
    ):
        """
        Args:
            model: 视觉模型名称，None 使用配置
            capture: 截图实例，None 使用全局实例
        """
        config = get_config().vision
        self._model = model or config.model
        self._fallback_model = config.fallback_model
        self._capture = capture or get_capture()
        self._llm_client = None
    
    @property
    def llm_client(self):
        """懒加载 LLM 客户端"""
        if self._llm_client is None:
            from openakita.llm.client import get_default_client
            self._llm_client = get_default_client()
        return self._llm_client
    
    async def _call_vision_model(
        self,
        prompt: str,
        image: Image.Image,
        model: Optional[str] = None,
    ) -> str:
        """
        调用视觉模型
        
        Args:
            prompt: 提示词
            image: 图片
            model: 模型名称
            
        Returns:
            模型响应文本
        """
        from openakita.llm.types import Message, ImageBlock, TextBlock, ImageContent
        
        # 将图片转换为 base64
        b64_data = self._capture.to_base64(image, resize_for_api=True)
        
        # 构建消息
        messages = [
            Message(
                role="user",
                content=[
                    ImageBlock(
                        image=ImageContent.from_base64(b64_data, "image/jpeg")
                    ),
                    TextBlock(text=prompt),
                ],
            )
        ]
        
        # 调用 LLM
        config = get_config().vision
        use_model = model or self._model
        
        try:
            response = await self.llm_client.chat(
                messages=messages,
                max_tokens=4096,
                temperature=1.0,  # 使用默认温度，避免某些模型限制
            )
            
            # 提取文本响应
            if response.content:
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
            
            return ""
            
        except Exception as e:
            logger.error(f"Vision model call failed: {e}")
            
            # 尝试备用模型
            if use_model != self._fallback_model:
                logger.info(f"Trying fallback model: {self._fallback_model}")
                return await self._call_vision_model(
                    prompt, image, model=self._fallback_model
                )
            
            raise
    
    def _parse_json_response(self, text: str) -> Optional[dict]:
        """
        解析模型返回的 JSON
        
        Args:
            text: 模型响应文本
            
        Returns:
            解析后的 JSON 对象
        """
        # 尝试提取 JSON 块
        json_match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接解析
            json_str = text.strip()
            # 尝试找到 JSON 对象
            start = json_str.find("{")
            end = json_str.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = json_str[start:end]
        
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON response: {e}")
            logger.debug(f"Raw response: {text}")
            return None
    
    async def find_element(
        self,
        description: str,
        image: Optional[Image.Image] = None,
    ) -> Optional[ElementLocation]:
        """
        根据描述查找 UI 元素
        
        Args:
            description: 元素描述（如"保存按钮"、"红色图标"）
            image: 截图，None 则自动截取当前屏幕
            
        Returns:
            找到的元素位置，未找到返回 None
        """
        if image is None:
            image = self._capture.capture(use_cache=False)
        
        prompt = PromptTemplates.get_find_element_prompt(description)
        
        try:
            response = await self._call_vision_model(prompt, image)
            result = self._parse_json_response(response)
            
            if not result:
                return None
            
            if not result.get("found", False):
                logger.debug(f"Element not found: {result.get('reasoning', 'unknown')}")
                return None
            
            element = result.get("element", {})
            bbox_data = element.get("bbox")
            
            if not bbox_data or len(bbox_data) != 4:
                logger.warning(f"Invalid bbox in response: {bbox_data}")
                return None
            
            return ElementLocation(
                description=element.get("description", description),
                bbox=BoundingBox(
                    left=int(bbox_data[0]),
                    top=int(bbox_data[1]),
                    right=int(bbox_data[2]),
                    bottom=int(bbox_data[3]),
                ),
                confidence=float(element.get("confidence", 0.8)),
                reasoning=result.get("reasoning", ""),
            )
            
        except Exception as e:
            logger.error(f"Failed to find element: {e}")
            return None
    
    async def find_all_clickable(
        self,
        image: Optional[Image.Image] = None,
    ) -> List[ElementLocation]:
        """
        查找所有可点击元素
        
        Args:
            image: 截图
            
        Returns:
            可点击元素列表
        """
        if image is None:
            image = self._capture.capture(use_cache=False)
        
        prompt = PromptTemplates.LIST_CLICKABLE
        
        try:
            response = await self._call_vision_model(prompt, image)
            result = self._parse_json_response(response)
            
            if not result:
                return []
            
            elements = []
            for elem_data in result.get("elements", []):
                bbox_data = elem_data.get("bbox")
                if not bbox_data or len(bbox_data) != 4:
                    continue
                
                elements.append(ElementLocation(
                    description=elem_data.get("description", "unknown"),
                    bbox=BoundingBox(
                        left=int(bbox_data[0]),
                        top=int(bbox_data[1]),
                        right=int(bbox_data[2]),
                        bottom=int(bbox_data[3]),
                    ),
                    confidence=0.8,
                ))
            
            return elements
            
        except Exception as e:
            logger.error(f"Failed to find clickable elements: {e}")
            return []
    
    async def find_all_input(
        self,
        image: Optional[Image.Image] = None,
    ) -> List[ElementLocation]:
        """
        查找所有输入元素
        
        Args:
            image: 截图
            
        Returns:
            输入元素列表
        """
        if image is None:
            image = self._capture.capture(use_cache=False)
        
        prompt = PromptTemplates.LIST_INPUT
        
        try:
            response = await self._call_vision_model(prompt, image)
            result = self._parse_json_response(response)
            
            if not result:
                return []
            
            elements = []
            for elem_data in result.get("elements", []):
                bbox_data = elem_data.get("bbox")
                if not bbox_data or len(bbox_data) != 4:
                    continue
                
                elements.append(ElementLocation(
                    description=elem_data.get("description", "unknown"),
                    bbox=BoundingBox(
                        left=int(bbox_data[0]),
                        top=int(bbox_data[1]),
                        right=int(bbox_data[2]),
                        bottom=int(bbox_data[3]),
                    ),
                    confidence=0.8,
                ))
            
            return elements
            
        except Exception as e:
            logger.error(f"Failed to find input elements: {e}")
            return []
    
    async def analyze_page(
        self,
        image: Optional[Image.Image] = None,
    ) -> VisionResult:
        """
        分析页面内容
        
        Args:
            image: 截图
            
        Returns:
            分析结果
        """
        if image is None:
            image = self._capture.capture(use_cache=False)
        
        prompt = PromptTemplates.ANALYZE_PAGE
        
        try:
            response = await self._call_vision_model(prompt, image)
            result = self._parse_json_response(response)
            
            if not result:
                return VisionResult(
                    success=False,
                    query="analyze_page",
                    error="Failed to parse response",
                    raw_response=response,
                )
            
            # 提取区域作为元素
            elements = []
            for region in result.get("regions", []):
                bbox_data = region.get("bbox")
                if bbox_data and len(bbox_data) == 4:
                    elements.append(ElementLocation(
                        description=region.get("name", "region"),
                        bbox=BoundingBox(
                            left=int(bbox_data[0]),
                            top=int(bbox_data[1]),
                            right=int(bbox_data[2]),
                            bottom=int(bbox_data[3]),
                        ),
                    ))
            
            return VisionResult(
                success=True,
                query="analyze_page",
                answer=result.get("summary", ""),
                elements=elements,
                raw_response=response,
            )
            
        except Exception as e:
            logger.error(f"Failed to analyze page: {e}")
            return VisionResult(
                success=False,
                query="analyze_page",
                error=str(e),
            )
    
    async def answer_question(
        self,
        question: str,
        image: Optional[Image.Image] = None,
    ) -> VisionResult:
        """
        回答关于截图的问题
        
        Args:
            question: 问题
            image: 截图
            
        Returns:
            回答结果
        """
        if image is None:
            image = self._capture.capture(use_cache=False)
        
        prompt = PromptTemplates.get_answer_question_prompt(question)
        
        try:
            response = await self._call_vision_model(prompt, image)
            result = self._parse_json_response(response)
            
            if not result:
                # 如果 JSON 解析失败，直接返回原始响应
                return VisionResult(
                    success=True,
                    query=question,
                    answer=response,
                    raw_response=response,
                )
            
            # 提取相关元素
            elements = []
            for elem_data in result.get("relevant_elements", []):
                bbox_data = elem_data.get("bbox")
                if bbox_data and len(bbox_data) == 4:
                    elements.append(ElementLocation(
                        description=elem_data.get("description", ""),
                        bbox=BoundingBox(
                            left=int(bbox_data[0]),
                            top=int(bbox_data[1]),
                            right=int(bbox_data[2]),
                            bottom=int(bbox_data[3]),
                        ),
                    ))
            
            return VisionResult(
                success=True,
                query=question,
                answer=result.get("answer", ""),
                elements=elements,
                raw_response=response,
            )
            
        except Exception as e:
            logger.error(f"Failed to answer question: {e}")
            return VisionResult(
                success=False,
                query=question,
                error=str(e),
            )
    
    async def extract_text(
        self,
        image: Optional[Image.Image] = None,
    ) -> VisionResult:
        """
        从截图中提取文本（OCR）
        
        使用专用 OCR 模型 (qwen-vl-ocr) 进行文本提取
        
        Args:
            image: 截图
            
        Returns:
            提取结果
        """
        if image is None:
            image = self._capture.capture(use_cache=False)
        
        prompt = PromptTemplates.EXTRACT_TEXT
        
        # 使用 OCR 专用模型
        ocr_model = get_config().vision.ocr_model
        
        try:
            response = await self._call_vision_model(prompt, image, model=ocr_model)
            result = self._parse_json_response(response)
            
            if not result:
                return VisionResult(
                    success=False,
                    query="extract_text",
                    error="Failed to parse response",
                    raw_response=response,
                )
            
            # 提取文本元素
            elements = []
            for text_data in result.get("texts", []):
                bbox_data = text_data.get("bbox")
                if bbox_data and len(bbox_data) == 4:
                    elements.append(ElementLocation(
                        description=text_data.get("content", ""),
                        bbox=BoundingBox(
                            left=int(bbox_data[0]),
                            top=int(bbox_data[1]),
                            right=int(bbox_data[2]),
                            bottom=int(bbox_data[3]),
                        ),
                    ))
            
            return VisionResult(
                success=True,
                query="extract_text",
                answer=result.get("main_text", ""),
                elements=elements,
                raw_response=response,
            )
            
        except Exception as e:
            logger.error(f"Failed to extract text: {e}")
            return VisionResult(
                success=False,
                query="extract_text",
                error=str(e),
            )
    
    async def verify_action(
        self,
        before_image: Image.Image,
        after_image: Image.Image,
        action_description: str,
        expected_result: str,
    ) -> VisionResult:
        """
        验证操作是否成功
        
        对比操作前后的截图，判断操作是否成功
        
        Args:
            before_image: 操作前截图
            after_image: 操作后截图
            action_description: 操作描述
            expected_result: 预期结果
            
        Returns:
            验证结果
        """
        # 将两张图片合并（左右排列）
        total_width = before_image.width + after_image.width
        max_height = max(before_image.height, after_image.height)
        combined = Image.new("RGB", (total_width, max_height))
        combined.paste(before_image, (0, 0))
        combined.paste(after_image, (before_image.width, 0))
        
        prompt = PromptTemplates.get_verify_action_prompt(
            action_description, expected_result
        )
        
        try:
            response = await self._call_vision_model(prompt, combined)
            result = self._parse_json_response(response)
            
            if not result:
                return VisionResult(
                    success=False,
                    query=f"verify: {action_description}",
                    error="Failed to parse response",
                    raw_response=response,
                )
            
            return VisionResult(
                success=result.get("success", False),
                query=f"verify: {action_description}",
                answer=result.get("reasoning", ""),
                raw_response=response,
            )
            
        except Exception as e:
            logger.error(f"Failed to verify action: {e}")
            return VisionResult(
                success=False,
                query=f"verify: {action_description}",
                error=str(e),
            )


# 全局实例
_analyzer: Optional[VisionAnalyzer] = None


def get_vision_analyzer() -> VisionAnalyzer:
    """获取全局视觉分析器"""
    global _analyzer
    if _analyzer is None:
        _analyzer = VisionAnalyzer()
    return _analyzer
