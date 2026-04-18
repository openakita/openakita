"""
Windows desktop automation - vision analysis prompts

Defines various prompt templates used with Qwen-VL
"""


class PromptTemplates:
    """Collection of prompt templates"""

    # Find an element
    FIND_ELEMENT = """You are a Windows desktop UI analysis expert. Analyze this screenshot and find the element described by the user.

User description: {description}

Examine the screenshot carefully, locate the UI element that best matches the description, and return its position information.

Requirements:
1. Carefully analyze all visible elements in the screenshot
2. Find the element that best matches the user's description
3. Return the element's bounding box coordinates (in pixels)

Return the result in JSON format:
```json
{{
    "found": true or false,
    "element": {{
        "description": "brief description of the element",
        "bbox": [top-left x, top-left y, bottom-right x, bottom-right y],
        "center": [center x, center y],
        "confidence": confidence between 0.0 and 1.0
    }},
    "reasoning": "why the element was found, or why it was not"
}}
```

Notes:
- bbox coordinates are pixel coordinates relative to the top-left corner of the screenshot
- If no matching element is found, set found to false
- confidence represents the certainty of the match"""

    # List all clickable elements
    LIST_CLICKABLE = """You are a Windows desktop UI analysis expert. Analyze this screenshot and find all clickable UI elements.

Clickable elements include:
- Buttons (regular buttons, icon buttons)
- Links
- Menu items
- Tabs
- List items
- Checkboxes
- Radio buttons
- Dropdowns
- Clickable icons

Return the result in JSON format:
```json
{{
    "elements": [
        {{
            "description": "element description",
            "type": "element type (button/link/menuitem/tab/listitem/checkbox/icon, etc.)",
            "bbox": [top-left x, top-left y, bottom-right x, bottom-right y],
            "center": [center x, center y]
        }}
    ],
    "total_count": total number of elements
}}
```

Notes:
- Only return elements that are visible and interactive
- bbox is in pixel coordinates relative to the top-left corner of the screenshot
- Order elements from top to bottom, left to right"""

    # List all input elements
    LIST_INPUT = """You are a Windows desktop UI analysis expert. Analyze this screenshot and find all UI elements that accept input.

Input elements include:
- Text boxes
- Password fields
- Search boxes
- Multi-line text areas
- Dropdown combo boxes
- Numeric input fields

Return the result in JSON format:
```json
{{
    "elements": [
        {{
            "description": "element description (e.g. 'username input')",
            "type": "element type (textbox/password/search/textarea/combobox, etc.)",
            "bbox": [top-left x, top-left y, bottom-right x, bottom-right y],
            "center": [center x, center y],
            "current_value": "currently displayed value (if visible)"
        }}
    ],
    "total_count": total number of elements
}}
```"""

    # Analyze page content
    ANALYZE_PAGE = """You are a Windows desktop UI analysis expert. Analyze this screenshot and describe the current page's content and layout.

Answer the following questions:
1. What application or window is this?
2. What is the main content of the current page?
3. What are the main regions of the page?
4. What important buttons or action entry points are there?
5. Are there any popups, dialogs, or error messages?

Return the result in JSON format:
```json
{{
    "application": "application name",
    "window_title": "window title",
    "page_type": "page type (e.g. home, settings, dialog)",
    "main_content": "description of the main content",
    "regions": [
        {{
            "name": "region name",
            "description": "region description",
            "bbox": [left, top, right, bottom]
        }}
    ],
    "key_actions": ["main action 1", "main action 2"],
    "alerts": ["popup or notification text"],
    "summary": "overall summary of the page"
}}
```"""

    # Answer a question about the screenshot
    ANSWER_QUESTION = """You are a Windows desktop UI analysis expert. Answer the user's question based on this screenshot.

User question: {question}

Observe the screenshot carefully and give an accurate answer. If the question involves element positions, provide coordinate information.

Answer format:
```json
{{
    "answer": "your answer",
    "relevant_elements": [
        {{
            "description": "description of the relevant element",
            "bbox": [left, top, right, bottom],
            "center": [x, y]
        }}
    ],
    "confidence": confidence between 0.0 and 1.0
}}
```"""

    # Verify an action result
    VERIFY_ACTION = """You are a Windows desktop UI analysis expert. Compare these two screenshots and verify whether the action was executed successfully.

Action description: {action_description}
Expected result: {expected_result}

The first image is the screenshot before the action; the second is after the action.

Analyze:
1. What changes happened in the UI?
2. Was the action executed successfully?
3. Does the result match the expectation?

Return the result in JSON format:
```json
{{
    "success": true or false,
    "changes": ["change 1", "change 2"],
    "matches_expectation": true or false,
    "reasoning": "basis for the judgment",
    "current_state": "description of the current state"
}}
```"""

    # OCR text extraction
    EXTRACT_TEXT = """You are a Windows desktop UI analysis expert. Extract all visible text content from this screenshot.

Return the result in JSON format:
```json
{{
    "texts": [
        {{
            "content": "text content",
            "bbox": [left, top, right, bottom],
            "type": "text type (title/label/button/input/content, etc.)"
        }}
    ],
    "main_text": "summary of the main text content"
}}
```

Notes:
- Order from top to bottom, left to right
- Distinguish between titles, labels, button text, input content, etc."""

    # Compare two screenshots
    COMPARE_SCREENSHOTS = """You are a Windows desktop UI analysis expert. Compare these two screenshots and identify the differences between them.

The first image is the previous screenshot; the second is the current screenshot.

Analyze:
1. Which elements have been added?
2. Which elements have disappeared?
3. Which elements have changed?
4. How has the overall layout changed?

Return the result in JSON format:
```json
{{
    "added": ["added elements"],
    "removed": ["removed elements"],
    "changed": [
        {{
            "element": "changed element",
            "before": "previous state",
            "after": "current state"
        }}
    ],
    "layout_changes": "description of layout changes",
    "summary": "summary of the changes"
}}
```"""

    @classmethod
    def get_find_element_prompt(cls, description: str) -> str:
        """Get the find-element prompt"""
        return cls.FIND_ELEMENT.format(description=description)

    @classmethod
    def get_answer_question_prompt(cls, question: str) -> str:
        """Get the answer-question prompt"""
        return cls.ANSWER_QUESTION.format(question=question)

    @classmethod
    def get_verify_action_prompt(
        cls,
        action_description: str,
        expected_result: str,
    ) -> str:
        """Get the verify-action prompt"""
        return cls.VERIFY_ACTION.format(
            action_description=action_description,
            expected_result=expected_result,
        )
