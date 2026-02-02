"""
Windows 桌面自动化 - 视觉分析提示词

定义用于 Qwen-VL 的各种提示词模板
"""


class PromptTemplates:
    """提示词模板集合"""
    
    # 查找元素
    FIND_ELEMENT = """你是一个 Windows 桌面 UI 分析专家。请分析这张截图，找到用户描述的元素。

用户描述: {description}

请仔细查看截图，找到与描述最匹配的 UI 元素，并返回其位置信息。

要求：
1. 仔细分析截图中的所有可见元素
2. 找到与用户描述最匹配的元素
3. 返回元素的边界框坐标（像素）

请以 JSON 格式返回结果:
```json
{{
    "found": true或false,
    "element": {{
        "description": "元素的简短描述",
        "bbox": [左上角x, 左上角y, 右下角x, 右下角y],
        "center": [中心点x, 中心点y],
        "confidence": 0.0到1.0的置信度
    }},
    "reasoning": "找到该元素的原因，或未找到的原因"
}}
```

注意：
- bbox 坐标是相对于截图左上角的像素坐标
- 如果找不到匹配的元素，设置 found 为 false
- confidence 表示匹配的确信程度"""

    # 列出所有可点击元素
    LIST_CLICKABLE = """你是一个 Windows 桌面 UI 分析专家。请分析这张截图，找出所有可以点击的 UI 元素。

可点击元素包括：
- 按钮（普通按钮、图标按钮）
- 链接
- 菜单项
- 标签页
- 列表项
- 复选框
- 单选按钮
- 下拉框
- 可点击的图标

请以 JSON 格式返回结果:
```json
{{
    "elements": [
        {{
            "description": "元素描述",
            "type": "元素类型（button/link/menuitem/tab/listitem/checkbox/icon等）",
            "bbox": [左上角x, 左上角y, 右下角x, 右下角y],
            "center": [中心点x, 中心点y]
        }}
    ],
    "total_count": 元素总数
}}
```

注意：
- 只返回可见且可交互的元素
- bbox 是相对于截图左上角的像素坐标
- 按照从上到下、从左到右的顺序排列"""

    # 列出所有输入元素
    LIST_INPUT = """你是一个 Windows 桌面 UI 分析专家。请分析这张截图，找出所有可以输入的 UI 元素。

输入元素包括：
- 文本框
- 密码框
- 搜索框
- 多行文本区域
- 下拉选择框
- 数字输入框

请以 JSON 格式返回结果:
```json
{{
    "elements": [
        {{
            "description": "元素描述（如'用户名输入框'）",
            "type": "元素类型（textbox/password/search/textarea/combobox等）",
            "bbox": [左上角x, 左上角y, 右下角x, 右下角y],
            "center": [中心点x, 中心点y],
            "current_value": "当前显示的值（如果可见）"
        }}
    ],
    "total_count": 元素总数
}}
```"""

    # 分析页面内容
    ANALYZE_PAGE = """你是一个 Windows 桌面 UI 分析专家。请分析这张截图，描述当前页面的内容和布局。

请回答以下问题：
1. 这是什么应用程序或窗口？
2. 当前页面的主要内容是什么？
3. 页面有哪些主要区域？
4. 有哪些重要的按钮或操作入口？
5. 是否有任何弹窗、对话框或错误提示？

请以 JSON 格式返回结果:
```json
{{
    "application": "应用程序名称",
    "window_title": "窗口标题",
    "page_type": "页面类型（如主页、设置页、对话框等）",
    "main_content": "主要内容描述",
    "regions": [
        {{
            "name": "区域名称",
            "description": "区域描述",
            "bbox": [左, 上, 右, 下]
        }}
    ],
    "key_actions": ["主要操作1", "主要操作2"],
    "alerts": ["弹窗或提示信息"],
    "summary": "页面整体摘要"
}}
```"""

    # 回答关于截图的问题
    ANSWER_QUESTION = """你是一个 Windows 桌面 UI 分析专家。请根据这张截图回答用户的问题。

用户问题: {question}

请仔细观察截图，给出准确的回答。如果问题涉及到元素位置，请提供坐标信息。

回答格式：
```json
{{
    "answer": "你的回答",
    "relevant_elements": [
        {{
            "description": "相关元素描述",
            "bbox": [左, 上, 右, 下],
            "center": [x, y]
        }}
    ],
    "confidence": 0.0到1.0的置信度
}}
```"""

    # 验证操作结果
    VERIFY_ACTION = """你是一个 Windows 桌面 UI 分析专家。请对比这两张截图，验证操作是否成功执行。

操作描述: {action_description}
预期结果: {expected_result}

第一张是操作前的截图，第二张是操作后的截图。

请分析：
1. 界面发生了什么变化？
2. 操作是否成功执行？
3. 结果是否符合预期？

请以 JSON 格式返回结果:
```json
{{
    "success": true或false,
    "changes": ["变化1", "变化2"],
    "matches_expectation": true或false,
    "reasoning": "判断依据",
    "current_state": "当前状态描述"
}}
```"""

    # OCR 文本提取
    EXTRACT_TEXT = """你是一个 Windows 桌面 UI 分析专家。请从这张截图中提取所有可见的文本内容。

请以 JSON 格式返回结果:
```json
{{
    "texts": [
        {{
            "content": "文本内容",
            "bbox": [左, 上, 右, 下],
            "type": "文本类型（title/label/button/input/content等）"
        }}
    ],
    "main_text": "主要文本内容摘要"
}}
```

注意：
- 按照从上到下、从左到右的顺序
- 区分标题、标签、按钮文字、输入内容等"""

    # 比较两张截图
    COMPARE_SCREENSHOTS = """你是一个 Windows 桌面 UI 分析专家。请对比这两张截图，找出它们之间的差异。

第一张是之前的截图，第二张是当前的截图。

请分析：
1. 有哪些元素新增了？
2. 有哪些元素消失了？
3. 有哪些元素发生了变化？
4. 整体布局有什么变化？

请以 JSON 格式返回结果:
```json
{{
    "added": ["新增的元素"],
    "removed": ["消失的元素"],
    "changed": [
        {{
            "element": "变化的元素",
            "before": "之前状态",
            "after": "之后状态"
        }}
    ],
    "layout_changes": "布局变化描述",
    "summary": "变化摘要"
}}
```"""

    @classmethod
    def get_find_element_prompt(cls, description: str) -> str:
        """获取查找元素的提示词"""
        return cls.FIND_ELEMENT.format(description=description)
    
    @classmethod
    def get_answer_question_prompt(cls, question: str) -> str:
        """获取回答问题的提示词"""
        return cls.ANSWER_QUESTION.format(question=question)
    
    @classmethod
    def get_verify_action_prompt(
        cls,
        action_description: str,
        expected_result: str,
    ) -> str:
        """获取验证操作的提示词"""
        return cls.VERIFY_ACTION.format(
            action_description=action_description,
            expected_result=expected_result,
        )
