"""Declarative feature definitions for all 19 sub-features across 4 modules.

Each feature is a dict that will be inflated into FeatureDefinition + FeatureParam + FeatureExample.
Organized by module: video | image | detail | poster.
"""

from __future__ import annotations

from ecom_models import VIDEO_MODELS
from ecom_prompt_optimizer import VIDEO_OPTIMIZE_SYSTEM_PROMPT

# Common param builders
def _p_prompt(required=True, placeholder="描述你想要的效果..."):
    return {"id": "prompt", "type": "textarea", "label": "描述", "label_en": "Description",
            "required": required, "placeholder": placeholder, "group": "basic", "order": 1}

def _p_ref_image(label="参考图", required=False):
    return {"id": "ref_image", "type": "image_upload", "label": label, "label_en": "Reference Image",
            "required": required, "group": "basic", "order": 2}

def _p_product_name():
    return {"id": "product_name", "type": "text", "label": "产品名称", "label_en": "Product Name",
            "placeholder": "如: 智能手表 Pro Max", "group": "basic", "order": 0}

def _p_selling_points():
    return {"id": "selling_points", "type": "textarea", "label": "产品卖点", "label_en": "Selling Points",
            "placeholder": "如: 防水、续航72小时、血氧监测", "group": "basic", "order": 3}

def _p_size(default="2K"):
    return {"id": "size", "type": "select", "label": "清晰度", "label_en": "Resolution", "default": default,
            "options": [
                {"value": "1K", "label": "1K (标清)"},
                {"value": "2K", "label": "2K (高清)"},
                {"value": "4K", "label": "4K (超清·仅纯文生图)"},
            ], "group": "advanced", "order": 10}

def _p_ratio(default="1:1"):
    return {"id": "ratio", "type": "select", "label": "尺寸比例", "label_en": "Aspect Ratio", "default": default,
            "options": [
                {"value": "1:1", "label": "1:1 正方形"},
                {"value": "16:9", "label": "16:9 横版"},
                {"value": "9:16", "label": "9:16 竖版"},
                {"value": "4:3", "label": "4:3 横版"},
                {"value": "3:4", "label": "3:4 竖版"},
                {"value": "auto", "label": "自动 (跟随输入图)"},
            ], "group": "advanced", "order": 9}

def _p_quantity(max_n=4):
    return {"id": "quantity", "type": "number", "label": "生成数量", "label_en": "Quantity",
            "default": 1, "group": "advanced", "order": 11}

_ALL_IMAGE_MODEL_OPTIONS = [
    {"value": "wan2.7-image-pro", "label": "万相 2.7 Pro (wan2.7-image-pro)"},
    {"value": "wan2.7-image", "label": "万相 2.7 (wan2.7-image)"},
    {"value": "wan2.6-image", "label": "万相 2.6 (wan2.6-image) · 需上传图片"},
    {"value": "qwen-image-2.0-pro", "label": "千问 2.0 Pro (qwen-image-2.0-pro) · 仅文生图"},
    {"value": "qwen-image-2.0", "label": "千问 2.0 (qwen-image-2.0) · 仅文生图"},
]

_TEXT2IMG_MODEL_OPTIONS = [o for o in _ALL_IMAGE_MODEL_OPTIONS if "wan2.6" not in o["value"]]

_EDIT_MODEL_OPTIONS = [o for o in _ALL_IMAGE_MODEL_OPTIONS if "qwen" not in o["value"]]


def _p_model_image(default="wan2.7-image-pro", edit_only=False):
    options = _EDIT_MODEL_OPTIONS if edit_only else _TEXT2IMG_MODEL_OPTIONS
    if not any(o["value"] == default for o in options):
        options = _ALL_IMAGE_MODEL_OPTIONS
    return {"id": "model", "type": "select", "label": "模型", "label_en": "Model", "default": default,
            "options": options, "group": "advanced", "order": 12}

def _p_style():
    return {"id": "style", "type": "select", "label": "风格", "label_en": "Style", "default": "realistic",
            "options": [
                {"value": "realistic", "label": "写实"},
                {"value": "anime", "label": "动漫"},
                {"value": "3d_render", "label": "3D渲染"},
                {"value": "flat", "label": "扁平"},
                {"value": "watercolor", "label": "水彩"},
                {"value": "minimalist", "label": "极简"},
            ], "group": "advanced", "order": 13}

def _p_ratio_video(default="16:9"):
    return {"id": "ratio", "type": "select", "label": "比例", "label_en": "Aspect Ratio", "default": default,
            "options": [
                {"value": "16:9", "label": "16:9 横版"},
                {"value": "9:16", "label": "9:16 竖版"},
                {"value": "1:1", "label": "1:1 方形"},
                {"value": "4:3", "label": "4:3"},
                {"value": "3:4", "label": "3:4"},
                {"value": "21:9", "label": "21:9 电影宽屏"},
            ], "group": "advanced", "order": 10}

def _p_duration(default=5):
    return {"id": "duration", "type": "select", "label": "时长(秒)", "label_en": "Duration",
            "default": default,
            "options": [
                {"value": 4, "label": "4秒"},
                {"value": 5, "label": "5秒"},
                {"value": 8, "label": "8秒"},
                {"value": 10, "label": "10秒"},
                {"value": 12, "label": "12秒"},
                {"value": 15, "label": "15秒 (仅 2.0)"},
            ], "group": "advanced", "order": 11}

def _p_model_video(default="seedance-2-0"):
    """Model dropdown auto-synced with VIDEO_MODELS in ecom_models.py."""
    options = [{"value": m["id"], "label": m["name"]} for m in VIDEO_MODELS]
    return {"id": "model", "type": "select", "label": "模型", "label_en": "Model",
            "default": default, "options": options,
            "group": "advanced", "order": 12}

def _p_resolution(default="720p"):
    return {"id": "resolution", "type": "select", "label": "分辨率", "label_en": "Resolution",
            "default": default,
            "options": [
                {"value": "480p", "label": "480p (流畅)"},
                {"value": "720p", "label": "720p (高清)"},
                {"value": "1080p", "label": "1080p (仅 1.x Pro)"},
            ], "group": "advanced", "order": 13}

def _p_generate_audio(default=True):
    return {"id": "generate_audio", "type": "boolean", "label": "生成原声",
            "label_en": "Generate Audio", "default": default,
            "group": "advanced", "order": 14}

def _p_camera_fixed(default=False):
    return {"id": "camera_fixed", "type": "boolean", "label": "镜头固定",
            "label_en": "Camera Fixed", "default": default,
            "group": "advanced", "order": 15}

def _p_seed(default=-1):
    return {"id": "seed", "type": "number", "label": "随机种子 (-1=随机)",
            "label_en": "Seed", "default": default,
            "group": "advanced", "order": 16}

def _p_web_search(default=False):
    return {"id": "web_search", "type": "boolean", "label": "联网搜索热点",
            "label_en": "Web Search", "default": default,
            "group": "advanced", "order": 17}

def _p_draft_mode(default=False):
    return {"id": "draft", "type": "boolean", "label": "草稿模式 (省费快出)",
            "label_en": "Draft Mode", "default": default,
            "group": "advanced", "order": 18}

def _p_return_last_frame(default=False):
    return {"id": "return_last_frame", "type": "boolean", "label": "返回尾帧 (用于续接)",
            "label_en": "Return Last Frame", "default": default,
            "group": "advanced", "order": 19}

def _p_negative():
    return {"id": "negative_prompt", "type": "textarea", "label": "反向提示词", "label_en": "Negative Prompt",
            "placeholder": "不想出现的元素...", "group": "advanced", "order": 14}

def _p_color_scheme():
    return {"id": "color_scheme", "type": "select", "label": "配色方案", "label_en": "Color Scheme",
            "default": "auto",
            "options": [
                {"value": "auto", "label": "自动"},
                {"value": "warm", "label": "暖色调"},
                {"value": "cool", "label": "冷色调"},
                {"value": "vibrant", "label": "鲜艳"},
                {"value": "pastel", "label": "柔和"},
                {"value": "dark", "label": "暗色"},
            ], "group": "style", "order": 20}


# ===========================================================================
# Module 1: VIDEO (视频生成) — 4 features
# ===========================================================================

VIDEO_FEATURES = [
    {
        "id": "video_hot_replicate",
        "name": "爆款复刻",
        "name_en": "Viral Video Replicate",
        "module": "video",
        "description": "参考爆款视频风格，快速生成同类型短视频",
        "icon": "flame",
        "output_type": "video",
        "execution_mode": "agent",
        "execution_config": {
            "agent_system_prompt": (
                "你是电商短视频爆款分析师。用户将提供爆款视频的风格描述或参考，"
                "你需要生成一条可直接用于 Seedance 视频模型的提示词。\n\n"
                "## 爆款视频提示词结构\n"
                "格式：[风格基调]，[时长]秒，[画幅比例]\n"
                "0-{t1}秒：[开场钩子] — 3秒内抓住注意力\n"
                "{t1}-{t2}秒：[核心内容] — 产品/卖点展示\n"
                "{t2}-{duration}秒：[转化收尾] — 行动指令或利益点\n"
                "【镜头】具体运镜：推/拉/环绕/跟/手持晃动\n"
                "【氛围】光影+色调+质感关键词\n\n"
                "## 爆款要素（必须包含）\n"
                "- 开场3秒必须有视觉钩子（悬念/冲突/美感冲击）\n"
                "- 中段展示1-2个核心卖点的使用场景\n"
                "- 结尾留转化引导空间\n"
                "- 整体节奏紧凑，信息密度高\n\n"
                "## 约束\n"
                "- 提示词80-250字，不超过300字\n"
                "- 直接输出最终提示词，不要解释\n"
                "- 不要输出JSON，只输出纯文本提示词"
            ),
            "fallback_to_template": True,
        },
        "api_provider": "ark",
        "default_model": "seedance-2-0",
        "prompt_template": (
            "电商爆款短视频，产品：{product_name}，{ratio}画幅，{duration}秒。"
            "开场：产品从画面外滑入，{style}风格，背景简洁有质感。"
            "中段：环绕镜头展示产品全貌，推镜头特写材质细节。"
            "{prompt}。"
            "结尾：产品居中定格，氛围光晕收束。"
            "整体节奏紧凑，商业广告级画质"
        ),
        "params": [
            _p_product_name(),
            _p_prompt(placeholder="描述爆款视频风格，如：美妆开箱、科技感展示、Vlog真实感..."),
            _p_ref_image("参考截图"),
            _p_ratio_video(),
            _p_duration(),
            _p_style(),
            _p_model_video(),
            _p_resolution(),
            _p_generate_audio(),
            _p_camera_fixed(),
            _p_web_search(),
            _p_seed(),
        ],
        "examples": [
            {"id": "vhr_1", "title": "美妆开箱", "description": "高转化美妆产品开箱视频风格",
             "preset_params": {"product_name": "精华液套装", "prompt": "美妆博主开箱视频风格，柔和灯光，近景特写产品质地，配合手部展示使用方法", "ratio": "9:16", "duration": 5}},
            {"id": "vhr_2", "title": "数码产品展示", "description": "科技感产品展示视频",
             "preset_params": {"product_name": "无线耳机", "prompt": "科技感产品展示，深色背景，产品360度旋转，光线流动特效，突出金属质感", "ratio": "16:9", "duration": 5}},
        ],
    },
    {
        "id": "video_storyboard",
        "name": "视频分镜生成器",
        "name_en": "Video Storyboard",
        "module": "video",
        "description": "输入故事脚本，自动拆解为多段视频分镜并逐段生成",
        "icon": "clapperboard",
        "output_type": "video",
        "execution_mode": "pipeline",
        "execution_config": {
            "steps": [
                {"action": "decompose_storyboard", "config": {"segment_duration": 5}},
                {"action": "generate_video", "config": {"model": "seedance-2-0"}},
            ],
            "on_step_error": "abort",
        },
        "api_provider": "ark",
        "default_model": "seedance-2-0",
        "params": [
            _p_product_name(),
            {"id": "storyboard_script", "type": "textarea", "label": "故事脚本", "label_en": "Script",
             "required": True, "placeholder": "输入完整故事脚本，系统会自动拆分为多个分镜...",
             "group": "basic", "order": 1},
            {"id": "total_duration", "type": "number", "label": "总时长(秒)", "label_en": "Total Duration",
             "default": 30, "group": "advanced", "order": 10},
            _p_ratio_video(),
            _p_duration(),
            _p_model_video(),
            _p_resolution(),
            _p_generate_audio(),
            _p_camera_fixed(),
            _p_seed(),
        ],
        "examples": [
            {"id": "vsb_1", "title": "美食制作", "description": "3段分镜美食制作短视频",
             "preset_params": {"product_name": "空气炸锅", "storyboard_script": "第一幕：在明亮的现代厨房中，展示新鲜食材摆放在桌面上。\n第二幕：空气炸锅开始工作，金黄色的鸡翅在篮中翻滚，热气腾腾。\n第三幕：成品装盘特写，撒上葱花点缀，家人围坐享用。", "total_duration": 15}},
        ],
    },
    {
        "id": "video_ad_oneclick",
        "name": "商品广告一键成片",
        "name_en": "Product Ad Video",
        "module": "video",
        "description": "上传商品图+卖点文案，一键生成商品广告短视频",
        "icon": "camera",
        "output_type": "video",
        "execution_mode": "agent",
        "execution_config": {
            "agent_system_prompt": (
                "你是电商视频广告导演。用户提供产品信息和卖点，"
                "你需要生成一条适合 Seedance 视频模型的广告脚本提示词。\n\n"
                "## 广告视频提示词结构\n"
                "格式：商业广告风格，[时长]秒，[画幅]，[色调氛围]\n"
                "0-{t1}秒：[产品亮相] — 产品从视觉焦点出现，建立品牌感\n"
                "{t1}-{t2}秒：[卖点演示] — 通过场景/特效展示核心功能\n"
                "{t2}-{duration}秒：[行动召唤] — 产品定格+价值感收束\n"
                "【镜头】每段指定运镜（推/拉/环绕/升降）\n"
                "【光影】影棚级布光描述\n\n"
                "## 广告片核心法则\n"
                "- 产品是绝对主角，每帧都服务于产品展示\n"
                "- 卖点用「视觉语言」呈现，不要纯文字描述\n"
                "- 光影质感要达到TVC广告级别\n"
                "- 动作流畅连贯，避免跳切\n\n"
                "## 约束\n"
                "- 提示词80-250字\n"
                "- 直接输出最终提示词，不要解释\n"
                "- 不要输出JSON，只输出纯文本提示词"
            ),
            "fallback_to_template": True,
        },
        "api_provider": "ark",
        "default_model": "seedance-2-0",
        "prompt_template": (
            "电商商品广告视频，产品：{product_name}，{ratio}画幅，{duration}秒。"
            "卖点：{selling_points}。"
            "开场：产品在简洁背景中优雅出现，影棚级柔光，建立品质感。"
            "中段：推镜头特写产品关键细节，环绕展示立体造型。"
            "{prompt}。"
            "结尾：拉镜头，产品居中完整呈现，光影收束。"
            "商业广告级画质，色彩饱满，光影专业"
        ),
        "params": [
            _p_product_name(),
            _p_selling_points(),
            _p_prompt(required=False, placeholder="补充视频风格要求，如：科技感、温馨、高级感..."),
            _p_ref_image("商品图"),
            _p_ratio_video("9:16"),
            _p_duration(),
            _p_style(),
            _p_model_video(),
            _p_resolution(),
            _p_generate_audio(),
            _p_camera_fixed(),
            _p_web_search(),
            _p_draft_mode(),
            _p_seed(),
        ],
        "examples": [
            {"id": "vao_1", "title": "智能手表广告", "description": "运动场景手表广告",
             "preset_params": {"product_name": "智能运动手表", "selling_points": "50米防水、GPS轨迹、心率监测、7天续航", "prompt": "运动员佩戴手表跑步，汗水飞溅，动态数据界面叠加", "ratio": "9:16", "duration": 5}},
        ],
    },
    {
        "id": "video_character_replace",
        "name": "角色替换",
        "name_en": "Character Replace",
        "module": "video",
        "description": "替换视频中的角色形象，保持动作和场景不变",
        "icon": "masks",
        "output_type": "video",
        "execution_mode": "agent",
        "execution_config": {
            "agent_system_prompt": (
                "你是视频角色替换专家。用户提供原始场景描述和目标角色信息，"
                "你需要生成一条适合 Seedance 视频模型的角色替换提示词。\n\n"
                "## 角色替换提示词要素\n"
                "1. 原始场景完整保留：背景、灯光、构图、动作轨迹\n"
                "2. 目标角色精确描述：外貌、服装、肤色、表情、体型\n"
                "3. 动作映射：原角色动作 → 新角色自然执行\n"
                "4. 一致性：光影方向、色温、景深与原场景匹配\n\n"
                "## 提示词格式\n"
                "[场景保持]：保留原始场景的所有环境元素\n"
                "[角色替换]：新角色的详细外观描述\n"
                "[动作延续]：角色执行原场景中的动作\n"
                "[画面要求]：一致的光影和色调\n\n"
                "## 约束\n"
                "- 直接输出最终提示词，不要解释\n"
                "- 不要输出JSON"
            ),
            "fallback_to_template": True,
        },
        "api_provider": "ark",
        "default_model": "doubao-seedance-1-0-lite-i2v",
        "prompt_template": (
            "视频角色替换，保持场景和动作不变。"
            "原始场景：{prompt}。"
            "目标角色：{target_character}。"
            "要求：场景环境完全保留，只替换人物形象。"
            "新角色自然融入原场景的光影和氛围，"
            "动作流畅，表情自然，与场景融为一体"
        ),
        "params": [
            _p_prompt(placeholder="描述原始视频场景，如：时尚女性在咖啡厅品尝饮品..."),
            {"id": "target_character", "type": "textarea", "label": "目标角色描述",
             "label_en": "Target Character", "required": True,
             "placeholder": "描述替换后的角色外观...", "group": "basic", "order": 2},
            _p_ref_image("角色参考图"),
            _p_ratio_video(),
            _p_duration(),
            _p_model_video("doubao-seedance-1-0-lite-i2v"),
            _p_resolution(),
            _p_camera_fixed(),
            _p_seed(),
        ],
        "examples": [
            {"id": "vcr_1", "title": "模特替换", "description": "替换广告视频中的模特形象",
             "preset_params": {"prompt": "时尚女性在咖啡厅品尝饮品，温暖光线", "target_character": "亚洲男性，商务休闲风格，短发，微笑表情"}},
        ],
    },
]


# ===========================================================================
# Module 2: IMAGE (图像生成) — 7 features
# ===========================================================================

IMAGE_FEATURES = [
    {
        "id": "image_main_replicate",
        "name": "主图复刻",
        "name_en": "Main Image Replicate",
        "module": "image",
        "description": "参考竞品主图风格，生成同类型高质量主图",
        "icon": "copy",
        "output_type": "image",
        "execution_mode": "agent",
        "execution_config": {
            "agent_system_prompt": (
                "你是电商主图复刻专家。用户将提供一张参考主图和产品信息。\n\n"
                "## 你的任务\n"
                "分析参考图的以下要素并生成可复刻同风格的提示词：\n"
                "- 构图方式：产品居中/偏左/偏右、占比大小、角度（正面/45度/俯拍）\n"
                "- 背景处理：纯色/渐变/场景、背景与产品的层次关系\n"
                "- 光影风格：硬光/柔光、光源方向、阴影浓度、高光位置\n"
                "- 色彩方案：主色调、辅助色、色温冷暖\n"
                "- 点缀元素：水花/光斑/烟雾/粒子等装饰元素\n\n"
                "## 强制约束\n"
                "- 产品主体必须占画面中心85%以上面积\n"
                "- 产品边缘必须锐利清晰，不可模糊\n"
                "- 背景不可喧宾夺主\n"
                "- 输出纯提示词，不要解释"
            ),
            "fallback_to_template": True,
        },
        "api_provider": "dashscope",
        "default_model": "wan2.7-image-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "电商商品主图复刻，产品：{product_name}。"
            "参考原图的构图和风格，{prompt}。"
            "风格：{style}专业商业摄影，产品居中占画面85%以上，"
            "背景简洁不喧宾夺主，光影自然，产品边缘锐利清晰，"
            "超高清商业品质，适合电商平台800x800主图"
        ),
        "params": [
            _p_product_name(),
            _p_ref_image("参考主图", required=True),
            _p_prompt(required=False, placeholder="补充说明希望保留或修改的元素..."),
            _p_ratio(),
            _p_size(),
            _p_style(),
            _p_model_image("wan2.7-image-pro"),
            _p_quantity(),
        ],
        "examples": [
            {"id": "imr_1", "title": "护肤品主图", "description": "参考竞品风格生成护肤品主图",
             "preset_params": {"product_name": "玻尿酸精华液", "prompt": "保持同款简约白色背景风格，产品居中放大，添加水滴元素体现保湿", "style": "realistic"}},
        ],
    },
    {
        "id": "image_batch_edit",
        "name": "批量改图",
        "name_en": "Batch Image Edit",
        "module": "image",
        "description": "批量修改多张图片的风格、色调、背景等",
        "icon": "edit",
        "output_type": "images",
        "execution_mode": "batch",
        "execution_config": {
            "base_strategy": "prompt_template",
            "variation_source": "images_list",
            "max_concurrent": 4,
        },
        "api_provider": "dashscope",
        "default_model": "wan2.6-image",
        "api_capability": "multimodal",
        "prompt_template": (
            "图片编辑：{prompt}，编辑风格：{style}。"
            "【强制约束】仅按描述修改指定的视觉属性，"
            "严格保持原图中产品主体的形状、位置、大小、细节完全不变，"
            "保持原图的构图布局不变，修改区域与原图自然融合无痕迹"
        ),
        "batch_capable": True,
        "params": [
            _p_prompt(placeholder="描述需要的修改效果..."),
            {"id": "images_list", "type": "multi_image", "label": "上传图片(多张)",
             "label_en": "Upload Images", "required": True, "group": "basic", "order": 2},
            {"id": "edit_type", "type": "select", "label": "编辑类型", "label_en": "Edit Type",
             "default": "style", "options": [
                {"value": "style", "label": "风格变换"},
                {"value": "color", "label": "色调调整"},
                {"value": "background", "label": "背景替换"},
                {"value": "enhance", "label": "画质增强"},
             ], "group": "basic", "order": 3},
            _p_style(),
            _p_model_image("wan2.6-image", edit_only=True),
        ],
        "examples": [
            {"id": "ibe_1", "title": "批量风格统一", "description": "将多张产品图统一为日系清新风格",
             "preset_params": {"prompt": "日系清新风格，柔和光线，淡雅色调", "edit_type": "style", "style": "minimalist"}},
        ],
    },
    {
        "id": "image_batch_replace",
        "name": "批量替换",
        "name_en": "Batch Replace",
        "module": "image",
        "description": "批量替换图片中的指定元素（背景、文字、物体）",
        "icon": "refresh",
        "output_type": "images",
        "execution_mode": "batch",
        "execution_config": {
            "base_strategy": "prompt_template",
            "variation_source": "images_list",
            "max_concurrent": 4,
        },
        "api_provider": "dashscope",
        "default_model": "wan2.6-image",
        "api_capability": "multimodal",
        "prompt_template": (
            "图片元素替换：将图中的「{source_element}」替换为「{target_element}」。"
            "{prompt}。"
            "【强制约束】仅替换指定元素，严格保持产品主体的位置、大小、角度、"
            "光影、色温完全不变，替换后的新元素与原图环境自然融合，"
            "光照方向一致，透视关系正确，无PS合成痕迹"
        ),
        "batch_capable": True,
        "params": [
            {"id": "images_list", "type": "multi_image", "label": "上传图片(多张)",
             "label_en": "Upload Images", "required": True, "group": "basic", "order": 0},
            {"id": "source_element", "type": "text", "label": "原始元素", "label_en": "Source Element",
             "placeholder": "如: 白色背景", "required": True, "group": "basic", "order": 1},
            {"id": "target_element", "type": "text", "label": "目标元素", "label_en": "Target Element",
             "placeholder": "如: 渐变蓝色背景", "required": True, "group": "basic", "order": 2},
            _p_prompt(required=False, placeholder="补充替换细节..."),
            _p_model_image("wan2.6-image", edit_only=True),
        ],
        "examples": [
            {"id": "ibr_1", "title": "背景批量替换", "description": "白底图批量替换为场景图",
             "preset_params": {"source_element": "白色纯色背景", "target_element": "温馨客厅场景，柔和自然光", "prompt": "保持产品位置和大小不变"}},
        ],
    },
    {
        "id": "image_batch_gen",
        "name": "批量生图",
        "name_en": "Batch Generate",
        "module": "image",
        "description": "一次生成多张不同变体的商品图",
        "icon": "package",
        "output_type": "images",
        "execution_mode": "prompt_template",
        "api_provider": "dashscope",
        "default_model": "wan2.7-image-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "电商商品展示图，产品：{product_name}。"
            "{prompt}。"
            "风格：{style}专业摄影，{color_scheme}配色方案。"
            "产品为画面绝对主体，占比85%以上，边缘锐利，"
            "背景简洁衬托产品，光影层次丰富，商业级画质"
        ),
        "batch_capable": True,
        "params": [
            _p_product_name(),
            _p_prompt(placeholder="描述商品图效果..."),
            _p_ratio(),
            _p_size(),
            _p_style(),
            _p_color_scheme(),
            _p_quantity(max_n=4),
            _p_model_image(),
            _p_negative(),
        ],
        "examples": [
            {"id": "ibg_1", "title": "家居产品多图", "description": "一次生成4张不同角度家居产品图",
             "preset_params": {"product_name": "北欧实木餐桌", "prompt": "自然光拍摄，温馨家居环境，不同角度展示", "style": "realistic", "quantity": 4}},
        ],
    },
    {
        "id": "image_main_suite",
        "name": "主图套图",
        "name_en": "Main Image Suite",
        "module": "image",
        "description": "一键生成一组风格统一的商品主图（多角度/多场景）",
        "icon": "images",
        "output_type": "images",
        "execution_mode": "agent",
        "execution_config": {
            "agent_system_prompt": (
                "你是电商视觉设计总监。用户需要一组风格统一的商品主图套图。\n\n"
                "## 电商主图套图标准（5张一套）\n"
                "1. 第1张-正面主图：产品正面居中，白色/浅色纯净背景，全貌展示，这是最重要的图\n"
                "2. 第2张-角度图：45度侧面角度，展示产品立体感和层次\n"
                "3. 第3张-细节特写：材质/工艺/核心部件微距特写\n"
                "4. 第4张-场景图：产品在真实使用场景中的效果\n"
                "5. 第5张-卖点图：产品+核心卖点的信息图展示\n\n"
                "## 风格一致性约束（极其重要）\n"
                "每条 prompt 都必须在开头包含完全相同的「风格锚定段」，格式如下：\n"
                "「[统一风格] 产品：<产品完整外观描述>，拍摄风格：<具体摄影风格>，"
                "光影：<光源类型与方向>，色调：<主色调+辅色调>，背景基调：<背景类型>。」\n"
                "这段文字在每条 prompt 中必须一字不差地重复，确保 AI 生图模型对产品外观和视觉"
                "风格的理解完全一致。\n\n"
                "## 其他约束\n"
                "- 每张图产品主体清晰完整可辨认\n"
                "- 适合淘宝/天猫800x800主图规格\n\n"
                "输出 JSON: {\"prompts\": [\"...\", ...]}，每条对应一张主图。"
            ),
            "fallback_to_template": True,
        },
        "api_provider": "dashscope",
        "default_model": "wan2.7-image-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "[统一风格] 产品：{product_name}，风格：{style}商业摄影，色调统一，光影一致。"
            "电商主图套图第{scene_index}张。"
            "卖点：{selling_points}。{prompt}。"
            "要求：产品居中占画面85%以上，边缘锐利清晰，"
            "色调与整套主图保持完全一致，专业商业摄影品质，"
            "适合电商平台800x800主图展示"
        ),
        "batch_capable": True,
        "params": [
            _p_product_name(),
            _p_selling_points(),
            _p_prompt(required=False, placeholder="整体风格描述..."),
            _p_ref_image(),
            {"id": "suite_count", "type": "number", "label": "套图数量", "label_en": "Suite Count",
             "default": 5, "group": "basic", "order": 5},
            _p_ratio(),
            _p_size(),
            _p_style(),
            _p_model_image(),
        ],
        "examples": [
            {"id": "ims_1", "title": "手机壳套图", "description": "5张不同场景的手机壳展示套图",
             "preset_params": {"product_name": "创意手机壳", "selling_points": "防摔、超薄、磨砂触感", "prompt": "简约时尚风格，统一的莫兰迪色调", "suite_count": 5, "style": "minimalist"}},
        ],
    },
    {
        "id": "image_translate",
        "name": "图片翻译",
        "name_en": "Image Translation",
        "module": "image",
        "description": "翻译图片中的文字为目标语言，保持设计不变",
        "icon": "globe",
        "output_type": "image",
        "execution_mode": "pipeline",
        "execution_config": {
            "steps": [
                {"action": "llm_translate", "config": {"target_language": "en"}},
                {"action": "generate_image", "config": {"capability": "multimodal"}},
            ],
            "on_step_error": "abort",
        },
        "api_provider": "dashscope",
        "default_model": "wan2.7-image-pro",
        "api_capability": "multimodal",
        "params": [
            _p_ref_image("原图", required=True),
            {"id": "target_language", "type": "select", "label": "目标语言", "label_en": "Target Language",
             "default": "en", "options": [
                {"value": "zh", "label": "中文"},
                {"value": "en", "label": "English"},
                {"value": "ja", "label": "日本語"},
                {"value": "ko", "label": "한국어"},
                {"value": "es", "label": "Español"},
                {"value": "fr", "label": "Français"},
                {"value": "ar", "label": "العربية"},
             ], "group": "basic", "order": 1},
            _p_prompt(required=False, placeholder="补充翻译要求..."),
            _p_model_image("wan2.7-image-pro"),
        ],
        "examples": [
            {"id": "itr_1", "title": "中→英翻译", "description": "将中文电商图翻译为英文版",
             "preset_params": {"target_language": "en", "prompt": "保持原始设计风格，替换所有中文为英文"}},
        ],
    },
    {
        "id": "image_main_gen",
        "name": "主图生成",
        "name_en": "Main Image Generate",
        "module": "image",
        "description": "根据文字描述直接生成商品主图",
        "icon": "sparkles",
        "output_type": "image",
        "execution_mode": "prompt_template",
        "api_provider": "dashscope",
        "default_model": "wan2.7-image-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "电商商品主图，产品：{product_name}。"
            "场景与构图：{prompt}。"
            "风格：{style}专业商业摄影。"
            "【电商主图标准】"
            "产品为画面绝对焦点，居中占画面85%以上，"
            "背景简洁干净（白色/浅灰/单色渐变），不喧宾夺主，"
            "光影：专业影棚布光，主光+补光+轮廓光，产品立体感强，"
            "产品边缘锐利清晰，材质质感真实，"
            "超高清商业品质，适合电商平台展示，"
            "不含任何文字、水印、Logo叠加"
        ),
        "params": [
            _p_product_name(),
            _p_prompt(placeholder="描述场景、氛围、光影效果..."),
            _p_ref_image(),
            _p_ratio(),
            _p_size(),
            _p_style(),
            _p_color_scheme(),
            _p_model_image(),
            _p_quantity(),
            _p_negative(),
        ],
        "examples": [
            {"id": "img_1", "title": "咖啡机主图", "description": "高端咖啡机产品主图",
             "preset_params": {"product_name": "全自动意式咖啡机", "prompt": "现代厨房台面上，咖啡正在萃取，热气氤氲，温暖晨光透过窗户", "style": "realistic", "size": "1K"}},
            {"id": "img_2", "title": "运动鞋主图", "description": "动感运动鞋展示",
             "preset_params": {"product_name": "超轻跑步鞋", "prompt": "悬浮在空中的跑步鞋，彩色粒子爆发效果，深色背景突出产品", "style": "3d_render", "size": "1K"}},
        ],
    },
]


# ===========================================================================
# Module 3: DETAIL (电商详情图) — 4 features
# ===========================================================================

DETAIL_FEATURES = [
    {
        "id": "detail_replicate",
        "name": "详情图复刻",
        "name_en": "Detail Image Replicate",
        "module": "detail",
        "description": "参考竞品详情图风格，生成同款详情图",
        "icon": "copy",
        "output_type": "image",
        "execution_mode": "agent",
        "execution_config": {
            "agent_system_prompt": (
                "你是电商详情页设计专家。用户将提供竞品详情图参考。\n\n"
                "## 你的分析维度\n"
                "- 版式布局：信息区块的排列方式和间距\n"
                "- 卖点呈现：图标+文字/对比图/数据图表\n"
                "- 色彩方案：主色/辅助色/点缀色\n"
                "- 字体层次：标题/副标题/正文的大小和粗细关系\n"
                "- 视觉动线：用户阅读浏览的路径引导\n\n"
                "## 详情图内容结构标准\n"
                "1. 首屏：产品全貌+核心卖点标题（3秒抓住注意力）\n"
                "2. 卖点展示：每个卖点一个区块，图文并茂\n"
                "3. 细节特写：材质/工艺/包装细节\n"
                "4. 使用场景：真实生活场景中的产品使用效果\n"
                "5. 参数规格：尺寸/重量/材质等规格表\n\n"
                "输出可直接生成图片的提示词文本。"
            ),
            "fallback_to_template": True,
        },
        "api_provider": "dashscope",
        "default_model": "wan2.7-image-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "电商详情图复刻，产品：{product_name}。"
            "{prompt}。"
            "信息层次清晰，首屏产品全貌+核心卖点，"
            "配色与参考图统一，适合750px宽度详情页展示"
        ),
        "params": [
            _p_product_name(),
            _p_ref_image("参考详情图", required=True),
            _p_prompt(required=False, placeholder="补充产品信息或修改要求..."),
            _p_selling_points(),
            _p_ratio(),
            _p_size("2K"),
            _p_model_image("wan2.7-image-pro"),
        ],
        "examples": [
            {"id": "dr_1", "title": "护肤详情图", "description": "参考竞品护肤产品详情页风格",
             "preset_params": {"product_name": "烟酰胺美白面霜", "selling_points": "烟酰胺5%、玻尿酸保湿、28天焕白", "prompt": "保留简约清新风格，增加成分图解", "size": "2K"}},
        ],
    },
    {
        "id": "detail_suite",
        "name": "详情图套图",
        "name_en": "Detail Image Suite",
        "module": "detail",
        "description": "一键生成一套完整的商品详情页图片",
        "icon": "layers",
        "output_type": "images",
        "execution_mode": "agent",
        "execution_config": {
            "agent_system_prompt": (
                "你是电商详情页策划师。根据产品信息，生成一套完整详情图的提示词。\n\n"
                "## 详情图套图结构（每张图对应一个信息区块）\n"
                "1. 首图-产品展示：产品全貌+品名+核心卖点大标题，白色或品牌色背景\n"
                "2. 卖点图1：第一核心卖点图解，左图右文或上图下文排版\n"
                "3. 卖点图2：第二核心卖点图解，与上一张排版交替变化\n"
                "4. 细节/材质图：产品材质、工艺微距特写\n"
                "5. 场景/使用图：产品在真实场景中的使用效果\n"
                "6. (可选)参数图：产品规格参数表格式展示\n"
                "7. (可选)品牌/售后图：品牌故事或售后保障\n\n"
                "## 风格一致性约束（极其重要）\n"
                "每条 prompt 都必须在开头包含完全相同的「风格锚定段」，格式如下：\n"
                "「[统一风格] 产品：<产品完整外观描述>，设计语言：<具体设计风格>，"
                "主色调：<精确颜色描述>，辅助色：<辅助颜色>，字体风格：<字体类型>，"
                "整体氛围：<简洁/高端/活力等>。」\n"
                "这段文字在每条 prompt 中必须一字不差地重复，确保 AI 生图模型对产品外观和"
                "设计语言的理解完全一致。\n\n"
                "## 其他约束\n"
                "- 宽度标准750px（淘宝）/790px（天猫），竖版排列\n"
                "- 每张图信息清晰，文字可读\n"
                "- 视觉层次明确：标题 > 产品 > 说明文字\n\n"
                "输出 JSON: {\"prompts\": [\"...\", ...]}，每条对应一张详情图。"
            ),
            "fallback_to_template": True,
        },
        "api_provider": "dashscope",
        "default_model": "wan2.7-image-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "[统一风格] 产品：{product_name}，设计风格：{style}电商详情页，配色统一，排版一致。"
            "电商详情图套图第{scene_index}张。"
            "卖点：{selling_points}。{prompt}。"
            "要求：信息层次清晰，标题醒目，产品展示精致，"
            "配色与整套详情图保持完全一致，适合750px宽度详情页展示"
        ),
        "batch_capable": True,
        "params": [
            _p_product_name(),
            _p_selling_points(),
            _p_prompt(required=False, placeholder="整体风格/色调要求..."),
            _p_ref_image(),
            {"id": "detail_count", "type": "number", "label": "图片数量", "label_en": "Count",
             "default": 5, "group": "basic", "order": 5},
            _p_ratio(),
            _p_size("2K"),
            _p_style(),
            _p_model_image(),
        ],
        "examples": [
            {"id": "ds_1", "title": "蓝牙耳机详情套图", "description": "5张完整详情页",
             "preset_params": {"product_name": "降噪蓝牙耳机", "selling_points": "40dB主动降噪、30小时续航、IPX5防水", "prompt": "科技深色风格，蓝色渐变点缀", "detail_count": 5, "style": "minimalist"}},
        ],
    },
    {
        "id": "detail_long",
        "name": "详情图长图",
        "name_en": "Detail Long Image",
        "module": "detail",
        "description": "将多张详情图拼接为一张可直接上传的长图",
        "icon": "ruler",
        "output_type": "image",
        "execution_mode": "pipeline",
        "execution_config": {
            "steps": [
                {"action": "generate_image", "config": {
                    "n": 4, "capability": "multimodal", "force_ratio": "3:4",
                }},
                {"action": "stitch_images", "config": {"direction": "vertical"}},
            ],
            "on_step_error": "abort",
        },
        "api_provider": "dashscope",
        "default_model": "wan2.7-image-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "电商详情页竖版分段内容图，产品：{product_name}，"
            "卖点：{selling_points}。"
            "{prompt}。"
            "本图为详情页长图的一个独立区块，竖版3:4比例，"
            "宽度750px标准，内容聚焦一个核心卖点或场景，"
            "包含产品展示+文字说明的图文混排设计，"
            "色调风格统一，适合手机端纵向滚动浏览，"
            "信息密度适中，排版清晰，留有呼吸感"
        ),
        "params": [
            _p_product_name(),
            _p_selling_points(),
            _p_prompt(placeholder="详情长图的整体风格..."),
            _p_ref_image(),
            {"id": "section_count", "type": "number", "label": "分段数量", "label_en": "Sections",
             "default": 4, "group": "basic", "order": 5},
            _p_ratio(),
            _p_size("2K"),
            _p_model_image(),
        ],
        "examples": [
            {"id": "dl_1", "title": "家电详情长图", "description": "4段拼接的家电详情长图",
             "preset_params": {"product_name": "智能扫地机器人", "selling_points": "激光导航、自动集尘、App远程控制", "prompt": "简约科技风格，白色背景，功能图解清晰", "section_count": 4}},
        ],
    },
    {
        "id": "detail_new_product",
        "name": "新品发布",
        "name_en": "New Product Launch",
        "module": "detail",
        "description": "为新品发布生成一系列宣传素材（主图+详情图+海报）",
        "icon": "rocket",
        "output_type": "images",
        "execution_mode": "agent",
        "execution_config": {
            "agent_system_prompt": (
                "你是新品发布策划专家。根据新品信息，生成一套完整的发布素材提示词。\n\n"
                "## 新品发布素材清单\n"
                "1. 主图：新品正面全貌展示，品牌感+科技感，突出「新」\n"
                "2. 卖点详情图x3：分别突出3个核心卖点，图文并茂\n"
                "3. 发布海报：新品发布主题海报，有发布主题标语，视觉冲击力强\n\n"
                "## 风格一致性约束（极其重要）\n"
                "每条 prompt 都必须在开头包含完全相同的「风格锚定段」，格式如下：\n"
                "「[统一风格] 产品：<产品完整外观描述>，视觉风格：<具体风格>，"
                "主色调：<精确颜色>，光影：<光源设计>，整体调性：<品牌调性描述>。」\n"
                "这段文字在每条 prompt 中必须一字不差地重复，确保 AI 生图模型对产品外观和"
                "视觉风格的理解完全一致。\n\n"
                "## 其他约束\n"
                "- 产品在每张图中都保持一致的外观表现\n"
                "- 发布海报需要预留文字排版空间\n\n"
                "输出 JSON: {\"prompts\": [{\"type\": \"main\", \"prompt\": \"...\"}, "
                "{\"type\": \"detail\", \"prompt\": \"...\"}, ...]}"
            ),
            "fallback_to_template": True,
        },
        "api_provider": "dashscope",
        "default_model": "wan2.7-image-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "[统一风格] 产品：{product_name}，视觉风格：{style}品牌感科技感，色调统一，光影一致。"
            "新品发布素材第{scene_index}张。"
            "卖点：{selling_points}。{prompt}。"
            "整套素材视觉风格保持完全一致，品牌感与科技感并存，"
            "产品在每张图中清晰完整可辨认"
        ),
        "batch_capable": True,
        "params": [
            _p_product_name(),
            _p_selling_points(),
            _p_prompt(placeholder="新品特色和发布主题..."),
            _p_ref_image("产品图"),
            {"id": "launch_theme", "type": "text", "label": "发布主题", "label_en": "Launch Theme",
             "placeholder": "如: 科技重新定义生活", "group": "basic", "order": 4},
            {"id": "suite_count", "type": "number", "label": "素材数量", "label_en": "Material Count",
             "default": 5, "group": "basic", "order": 5},
            _p_ratio(),
            _p_size(),
            _p_style(),
            _p_color_scheme(),
            _p_model_image("wan2.7-image-pro"),
        ],
        "examples": [
            {"id": "dnp_1", "title": "智能手表新品", "description": "新品发布全套素材",
             "preset_params": {"product_name": "Galaxy Watch Ultra", "selling_points": "钛合金外壳、双频GPS、10ATM防水", "prompt": "高端科技质感，太空主题", "launch_theme": "探索无界", "suite_count": 5, "style": "3d_render"}},
        ],
    },
]


# ===========================================================================
# Module 4: POSTER (活动海报) — 4 features
# ===========================================================================

POSTER_FEATURES = [
    {
        "id": "poster_private_domain",
        "name": "私域运营",
        "name_en": "Private Domain",
        "module": "poster",
        "description": "生成适用于微信群、朋友圈等私域渠道的运营海报",
        "icon": "message",
        "output_type": "image",
        "execution_mode": "prompt_template",
        "api_provider": "dashscope",
        "default_model": "qwen-image-2.0-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "私域运营海报设计，产品：{product_name}。"
            "活动主题：{prompt}。"
            "风格：{style}设计，适合{channel}场景分享。"
            "【海报设计标准】"
            "构图：上方1/3预留标题文字区，中间产品主视觉，下方行动引导，"
            "产品突出醒目，是视觉焦点，"
            "配色方案明快吸引，适合手机屏幕浏览，"
            "信息层次：主标题 > 产品图 > 卖点 > 行动指引，"
            "适合微信朋友圈/群分享，竖版9:16或正方形1:1"
        ),
        "params": [
            _p_product_name(),
            _p_prompt(placeholder="运营活动主题或卖点..."),
            {"id": "channel", "type": "select", "label": "渠道", "label_en": "Channel",
             "default": "wechat_moments", "options": [
                {"value": "wechat_moments", "label": "朋友圈"},
                {"value": "wechat_group", "label": "微信群"},
                {"value": "mini_program", "label": "小程序"},
                {"value": "community", "label": "社群"},
             ], "group": "basic", "order": 3},
            _p_ref_image(),
            _p_ratio(),
            _p_size("2K"),
            _p_style(),
            _p_color_scheme(),
            _p_model_image("qwen-image-2.0-pro"),
            _p_quantity(),
        ],
        "examples": [
            {"id": "ppd_1", "title": "社群专享优惠", "description": "微信群专享优惠海报",
             "preset_params": {"product_name": "有机坚果礼盒", "prompt": "群友专享8折优惠，限量100份", "channel": "wechat_group", "style": "flat", "color_scheme": "warm"}},
        ],
    },
    {
        "id": "poster_product",
        "name": "产品营销",
        "name_en": "Product Marketing",
        "module": "poster",
        "description": "生成产品营销推广海报",
        "icon": "megaphone",
        "output_type": "image",
        "execution_mode": "agent",
        "execution_config": {
            "agent_system_prompt": (
                "你是电商视觉营销设计师。根据产品信息和营销目标，设计一张高转化率营销海报提示词。\n\n"
                "## 营销海报核心要素\n"
                "1. 视觉钩子：3秒内抓住注意力的核心画面\n"
                "2. 产品展示：产品占据视觉中心，清晰可辨\n"
                "3. 卖点传达：1-2个核心卖点通过视觉方式呈现（非纯文字）\n"
                "4. 行动引导：预留CTA按钮区域（画面下方1/4）\n"
                "5. 品牌调性：配色和风格符合品牌定位\n\n"
                "## 强制约束\n"
                "- 构图预留上方和下方各1/4区域给标题和CTA文字\n"
                "- 中间1/2区域是产品主视觉\n"
                "- 高对比度配色，在手机小屏幕上也能辨识\n"
                "- 不要生成实际文字，只预留文字排版空间\n\n"
                "输出纯提示词文本。"
            ),
            "fallback_to_template": True,
        },
        "api_provider": "dashscope",
        "default_model": "qwen-image-2.0-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "电商产品营销海报，产品：{product_name}。"
            "卖点：{selling_points}。{prompt}。"
            "风格：{style}，{color_scheme}配色。"
            "构图：上方1/4预留标题区，中间产品主视觉占50%，下方预留CTA区。"
            "产品清晰醒目，背景衬托主体，高对比度，适合手机屏幕浏览"
        ),
        "params": [
            _p_product_name(),
            _p_selling_points(),
            _p_prompt(required=False, placeholder="营销目标或活动信息..."),
            _p_ref_image(),
            {"id": "cta_text", "type": "text", "label": "行动按钮文案", "label_en": "CTA Text",
             "placeholder": "如: 立即购买、限时特惠", "group": "basic", "order": 4},
            _p_ratio(),
            _p_size(),
            _p_style(),
            _p_color_scheme(),
            _p_model_image("qwen-image-2.0-pro"),
            _p_quantity(),
        ],
        "examples": [
            {"id": "ppm_1", "title": "新品推广海报", "description": "新款耳机推广营销海报",
             "preset_params": {"product_name": "FreeBuds Pro 3", "selling_points": "空间音频、智能降噪、48小时续航", "prompt": "年轻潮流风格，吸引Z世代", "cta_text": "立即了解", "style": "3d_render", "color_scheme": "vibrant"}},
        ],
    },
    {
        "id": "poster_holiday",
        "name": "节日海报",
        "name_en": "Holiday Poster",
        "module": "poster",
        "description": "根据节日主题生成应景海报",
        "icon": "party",
        "output_type": "image",
        "execution_mode": "prompt_template",
        "api_provider": "dashscope",
        "default_model": "qwen-image-2.0-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "{holiday}节日主题电商海报，产品：{product_name}。"
            "节日氛围：{prompt}。"
            "风格：{style}设计，{color_scheme}配色。"
            "【节日海报标准】"
            "节日氛围浓郁：运用该节日的标志性视觉元素（色彩/图案/符号），"
            "产品融入节日场景，是画面主角而非点缀，"
            "构图：上方1/3预留节日主题标语区域，"
            "品牌感与节日感并存，喜庆但不杂乱，"
            "配色符合节日传统（春节红金/圣诞红绿/618红蓝），"
            "适合电商平台和社交媒体展示"
        ),
        "params": [
            {"id": "holiday", "type": "select", "label": "节日", "label_en": "Holiday",
             "required": True, "default": "spring_festival", "options": [
                {"value": "spring_festival", "label": "春节"},
                {"value": "valentines", "label": "情人节"},
                {"value": "womens_day", "label": "妇女节/女神节"},
                {"value": "618", "label": "618大促"},
                {"value": "mid_autumn", "label": "中秋节"},
                {"value": "national_day", "label": "国庆节"},
                {"value": "double_11", "label": "双十一"},
                {"value": "double_12", "label": "双十二"},
                {"value": "christmas", "label": "圣诞节"},
                {"value": "new_year", "label": "元旦"},
                {"value": "custom", "label": "自定义"},
             ], "group": "basic", "order": 0},
            _p_product_name(),
            _p_prompt(required=False, placeholder="补充节日氛围描述..."),
            _p_ref_image(),
            _p_ratio(),
            _p_size(),
            _p_style(),
            _p_color_scheme(),
            _p_model_image("qwen-image-2.0-pro"),
            _p_quantity(),
        ],
        "examples": [
            {"id": "ph_1", "title": "双十一海报", "description": "双十一电子产品促销海报",
             "preset_params": {"holiday": "double_11", "product_name": "智能音箱", "prompt": "炫酷科技感，霓虹灯效果，大促氛围", "style": "3d_render", "color_scheme": "vibrant"}},
            {"id": "ph_2", "title": "春节礼盒", "description": "春节年货礼盒海报",
             "preset_params": {"holiday": "spring_festival", "product_name": "坚果礼盒", "prompt": "红色喜庆氛围，金色点缀，年味十足", "style": "realistic", "color_scheme": "warm"}},
        ],
    },
    {
        "id": "poster_campaign",
        "name": "活动宣传",
        "name_en": "Campaign Promotion",
        "module": "poster",
        "description": "为促销活动、新品发布等活动生成宣传海报",
        "icon": "confetti",
        "output_type": "image",
        "execution_mode": "agent",
        "execution_config": {
            "agent_system_prompt": (
                "你是促销活动视觉设计师。根据活动信息设计高转化活动海报提示词。\n\n"
                "## 促销海报核心法则\n"
                "1. 紧迫感：通过色彩（红/橙/黄）和构图传达「限时」感\n"
                "2. 利益点：优惠力度必须是视觉焦点之一\n"
                "3. 视觉冲击：高饱和度、强对比度、动感元素\n"
                "4. 信息层级：活动名 > 优惠力度 > 产品 > 活动时间 > 行动指令\n"
                "5. 行动指令：预留CTA区域\n\n"
                "## 强制约束\n"
                "- 上方1/4：活动名称+日期标语区\n"
                "- 中间1/2：产品+优惠力度主视觉\n"
                "- 下方1/4：行动按钮区\n"
                "- 霓虹/渐变/粒子等促销氛围元素烘托\n"
                "- 高对比度配色，热烈活跃\n\n"
                "输出纯提示词文本。"
            ),
            "fallback_to_template": True,
        },
        "api_provider": "dashscope",
        "default_model": "qwen-image-2.0-pro",
        "api_capability": "multimodal",
        "prompt_template": (
            "电商促销活动海报，活动：{campaign_name}，产品：{product_name}。"
            "{prompt}。"
            "风格：{style}，{color_scheme}配色。"
            "构图：上方活动标题区，中间产品+优惠信息主视觉，下方行动引导区。"
            "视觉冲击力强，配色鲜明热烈，传达紧迫感和利益点，"
            "适合电商大促氛围"
        ),
        "params": [
            {"id": "campaign_name", "type": "text", "label": "活动名称", "label_en": "Campaign Name",
             "required": True, "placeholder": "如: 年中大促、清仓特卖", "group": "basic", "order": 0},
            _p_product_name(),
            _p_prompt(required=False, placeholder="活动详情、优惠力度..."),
            {"id": "discount_info", "type": "text", "label": "优惠信息", "label_en": "Discount Info",
             "placeholder": "如: 满300减50、全场5折", "group": "basic", "order": 3},
            {"id": "campaign_date", "type": "text", "label": "活动日期", "label_en": "Campaign Date",
             "placeholder": "如: 6月1日-6月18日", "group": "basic", "order": 4},
            _p_ref_image(),
            _p_ratio(),
            _p_size(),
            _p_style(),
            _p_color_scheme(),
            _p_model_image("qwen-image-2.0-pro"),
            _p_quantity(),
        ],
        "examples": [
            {"id": "pc_1", "title": "618年中大促", "description": "618购物节活动宣传海报",
             "preset_params": {"campaign_name": "618年中狂欢", "product_name": "全品类", "prompt": "限时抢购，全场低至5折", "discount_info": "满300减50，跨店满减", "campaign_date": "6月1日-6月18日", "style": "flat", "color_scheme": "vibrant"}},
        ],
    },
]


# ===========================================================================
# Aggregated list
# ===========================================================================

ALL_FEATURES: list[dict] = VIDEO_FEATURES + IMAGE_FEATURES + DETAIL_FEATURES + POSTER_FEATURES
