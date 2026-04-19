"""
Windows desktop automation - Vision analyzer

UI visual recognition powered by DashScope Qwen-VL
"""

import json
import logging
import re
import sys

from PIL import Image

from ..capture import ScreenCapture, get_capture
from ..types import (
    BoundingBox,
    ElementLocation,
    VisionResult,
)
from .prompts import PromptTemplates

# Platform check
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. Current platform: {sys.platform}"
    )

logger = logging.getLogger(__name__)


class VisionAnalyzer:
    """
    Vision analyzer

    Analyzes screenshots using DashScope Qwen-VL to identify UI elements
    """

    def __init__(
        self,
        capture: ScreenCapture | None = None,
    ):
        """
        Args:
            capture: Screen capture instance; None uses the global instance
        """
        self._capture = capture or get_capture()
        self._llm_client = None

    @property
    def llm_client(self):
        """Lazy-load the LLM client."""
        if self._llm_client is None:
            from openakita.llm.client import get_default_client

            self._llm_client = get_default_client()
        return self._llm_client

    async def _call_vision_model(
        self,
        prompt: str,
        image: Image.Image,
    ) -> str:
        """
        Call the vision model (model determined by LLM endpoint configuration).

        Args:
            prompt: Prompt text
            image: Image

        Returns:
            Model response text
        """
        from openakita.llm.types import ImageBlock, ImageContent, Message, TextBlock

        b64_data = self._capture.to_base64(image, resize_for_api=True)

        messages = [
            Message(
                role="user",
                content=[
                    ImageBlock(image=ImageContent.from_base64(b64_data, "image/jpeg")),
                    TextBlock(text=prompt),
                ],
            )
        ]

        response = await self.llm_client.chat(
            messages=messages,
            max_tokens=4096,
            temperature=1.0,
        )

        if response.content:
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text

        return ""

    def _parse_json_response(self, text: str) -> dict | None:
        """
        Parse JSON returned by the model.

        Args:
            text: Model response text

        Returns:
            Parsed JSON object
        """
        # Try to extract a JSON block
        json_match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to parse directly
            json_str = text.strip()
            # Try to locate a JSON object
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
        image: Image.Image | None = None,
    ) -> ElementLocation | None:
        """
        Find a UI element by description.

        Args:
            description: Element description (e.g. "save button", "red icon")
            image: Screenshot; if None, captures the current screen automatically

        Returns:
            Found element location, or None if not found
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
        image: Image.Image | None = None,
    ) -> list[ElementLocation]:
        """
        Find all clickable elements.

        Args:
            image: Screenshot

        Returns:
            List of clickable elements
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

                elements.append(
                    ElementLocation(
                        description=elem_data.get("description", "unknown"),
                        bbox=BoundingBox(
                            left=int(bbox_data[0]),
                            top=int(bbox_data[1]),
                            right=int(bbox_data[2]),
                            bottom=int(bbox_data[3]),
                        ),
                        confidence=0.8,
                    )
                )

            return elements

        except Exception as e:
            logger.error(f"Failed to find clickable elements: {e}")
            return []

    async def find_all_input(
        self,
        image: Image.Image | None = None,
    ) -> list[ElementLocation]:
        """
        Find all input elements.

        Args:
            image: Screenshot

        Returns:
            List of input elements
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

                elements.append(
                    ElementLocation(
                        description=elem_data.get("description", "unknown"),
                        bbox=BoundingBox(
                            left=int(bbox_data[0]),
                            top=int(bbox_data[1]),
                            right=int(bbox_data[2]),
                            bottom=int(bbox_data[3]),
                        ),
                        confidence=0.8,
                    )
                )

            return elements

        except Exception as e:
            logger.error(f"Failed to find input elements: {e}")
            return []

    async def analyze_page(
        self,
        image: Image.Image | None = None,
    ) -> VisionResult:
        """
        Analyze page content.

        Args:
            image: Screenshot

        Returns:
            Analysis result
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

            # Extract regions as elements
            elements = []
            for region in result.get("regions", []):
                bbox_data = region.get("bbox")
                if bbox_data and len(bbox_data) == 4:
                    elements.append(
                        ElementLocation(
                            description=region.get("name", "region"),
                            bbox=BoundingBox(
                                left=int(bbox_data[0]),
                                top=int(bbox_data[1]),
                                right=int(bbox_data[2]),
                                bottom=int(bbox_data[3]),
                            ),
                        )
                    )

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
        image: Image.Image | None = None,
    ) -> VisionResult:
        """
        Answer a question about a screenshot.

        Args:
            question: Question
            image: Screenshot

        Returns:
            Answer result
        """
        if image is None:
            image = self._capture.capture(use_cache=False)

        prompt = PromptTemplates.get_answer_question_prompt(question)

        try:
            response = await self._call_vision_model(prompt, image)
            result = self._parse_json_response(response)

            if not result:
                # If JSON parsing fails, return the raw response directly
                return VisionResult(
                    success=True,
                    query=question,
                    answer=response,
                    raw_response=response,
                )

            # Extract relevant elements
            elements = []
            for elem_data in result.get("relevant_elements", []):
                bbox_data = elem_data.get("bbox")
                if bbox_data and len(bbox_data) == 4:
                    elements.append(
                        ElementLocation(
                            description=elem_data.get("description", ""),
                            bbox=BoundingBox(
                                left=int(bbox_data[0]),
                                top=int(bbox_data[1]),
                                right=int(bbox_data[2]),
                                bottom=int(bbox_data[3]),
                            ),
                        )
                    )

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
        image: Image.Image | None = None,
    ) -> VisionResult:
        """
        Extract text from a screenshot (OCR).

        Uses a dedicated OCR model (qwen-vl-ocr) for text extraction.

        Args:
            image: Screenshot

        Returns:
            Extraction result
        """
        if image is None:
            image = self._capture.capture(use_cache=False)

        prompt = PromptTemplates.EXTRACT_TEXT

        try:
            response = await self._call_vision_model(prompt, image)
            result = self._parse_json_response(response)

            if not result:
                return VisionResult(
                    success=False,
                    query="extract_text",
                    error="Failed to parse response",
                    raw_response=response,
                )

            # Extract text elements
            elements = []
            for text_data in result.get("texts", []):
                bbox_data = text_data.get("bbox")
                if bbox_data and len(bbox_data) == 4:
                    elements.append(
                        ElementLocation(
                            description=text_data.get("content", ""),
                            bbox=BoundingBox(
                                left=int(bbox_data[0]),
                                top=int(bbox_data[1]),
                                right=int(bbox_data[2]),
                                bottom=int(bbox_data[3]),
                            ),
                        )
                    )

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
        Verify whether an action succeeded.

        Compares before and after screenshots to determine if the action was successful.

        Args:
            before_image: Screenshot before the action
            after_image: Screenshot after the action
            action_description: Action description
            expected_result: Expected result

        Returns:
            Verification result
        """
        # Merge the two images side by side
        total_width = before_image.width + after_image.width
        max_height = max(before_image.height, after_image.height)
        combined = Image.new("RGB", (total_width, max_height))
        combined.paste(before_image, (0, 0))
        combined.paste(after_image, (before_image.width, 0))

        prompt = PromptTemplates.get_verify_action_prompt(action_description, expected_result)

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


# Global instance
_analyzer: VisionAnalyzer | None = None


def get_vision_analyzer() -> VisionAnalyzer:
    """Get the global vision analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = VisionAnalyzer()
    return _analyzer
