"""Built-in template: AIGC Video Studio.

A 7-node creative-agency layout that mirrors the legacy
``aigc-video-studio`` template (``orgs/templates.py`` ~lines 741-1160)
but expressed in the v2 schema:

::

    producer (LLM)
       ├── screenwriter (LLM)         persona: storyboard decomposer
       └── art_director (LLM)         persona: routing brain
              ├── wb_image  (workbench, mode=image_artist)
              ├── wb_video  (workbench, mode=video_animator)
              ├── wb_human  (workbench, mode=portrait_actor)
              └── wb_long   (workbench, mode=art_director, the
                             stitching-mode of happyhorse-video that
                             owns hh_long_video_create / hh_video_concat)

    Collaborate edges:
      screenwriter -> art_director       (shares storyboard JSON)
      screenwriter -> wb_long            (segments straight to concat)
      wb_image     -> wb_video           (asset_ids as first frames)
      wb_image     -> wb_human           (portrait stills as actor refs)
      wb_video     -> wb_long            (per-shot mp4 task_ids)
      wb_human     -> wb_long            (digital-human task_ids)

This is not a literal port. The legacy schema had ~80 free-form keys
per node (``avatar``, ``position``, ``custom_prompt``, ``department``,
…) that were never enforced and frequently bit-rotted. The v2 schema
keeps only what the supervisor actually consumes: ``role`` + ``label``
+ ``persona_prompt`` + ``tool_subset`` + ``workbench`` + closed
runtime overrides + declarative ``guardrails``.

Personas remain in Chinese to match the user-facing character of the
legacy template. We summarise the 800-line legacy ``custom_prompt``
strings down to the routing rules that *actually matter at runtime*;
fine-tuning a persona stays a single-file diff because of the
one-template-per-module layout.
"""

from __future__ import annotations

from ...models import EdgeKind, NodeType
from ..registry import template
from ..schema import (
    DefaultsSpec,
    EdgeSpec,
    NodeSpec,
    TemplateSpec,
    WorkbenchBindingSpec,
)

PLUGIN_ID = "happyhorse-video"

# ---------------------------------------------------------------------------
# Persona prompts. Kept short on purpose — the supervisor injects the
# manifest-derived tool whitelist and the WorkbenchNode mode prompt
# anyway, so there is no need to re-spell every tool name here.
# ---------------------------------------------------------------------------

_PRODUCER_PERSONA = """\
你是【AIGC 视频工作室·制片人】节点。直属下级两人：
  - screenwriter  负责剧本与结构化分镜（hh_storyboard_decompose）。
  - art_director  统一调度图像/短视频/数字人/长视频后期四个工作台。

工作流：
1) 把出品方选题派给 screenwriter，要求返回剧本 + segments JSON
   (含 prompt / duration / transition_to_next 等字段)。
2) 把剧本 + segments JSON + 转场偏好派给 art_director，让其按分镜
   分发到三个内容工作台并最终调度长视频后期工作台拼接成片。
3) 收到 art_director 的最终交付（成片 task_id + 各段 task_id）后向
   出品方交付。一镜一资产，严禁让任何工作台用同一段总主题 prompt
   重复生成多个视频；多镜头任务必须按 segments[] 逐镜头拆派。
"""

_SCREENWRITER_PERSONA = """\
你是【编剧】节点。
1) 先输出可读剧本（场景、人物、对白）。
2) 调用 hh_storyboard_decompose 把剧情拆为结构化 segments：
   story=剧情正文；total_duration=成片秒数；segment_duration=每段秒数；
   aspect_ratio=画幅；style=视觉风格。返回的每段会带
   transition_to_next（cut / crossfade / ai_extend），下游据此决定
   衔接首尾帧或拼接转场。
3) 用一次 deliverable 返回剧本摘要 + segments JSON 概览，并把完整
   剧本与完整 segments 各自落盘为附件。讨论/问询不要凭空调工具。
"""

_ART_DIRECTOR_PERSONA = """\
你是【美术指导】节点，是 wb_image / wb_video / wb_human / wb_long
四个 happyhorse-video 工作台的直属上级。

路由规则（违反就是错误派单）：
  R1. wb_human 仅用于：在已有人物图/视频上做说话头像、唇形对齐、
      换脸、姿态驱动、多图合成。任何「跳舞 / 全身动作 / 大幅运镜 /
      武打 / 风景空镜」镜头一律走 wb_image → wb_video（hh_i2v 配合
      from_asset_ids）。
  R2. 「唱歌 / 唱词 / 歌词」不等于「口播」。判断是否走数字人的
      唯一标准是：是否需要让一张照片里的脸做唇形或表情驱动。
  R3. 资产复用强制：第二次给同一镜头派单时，必须先在派单里列出
      前一次的 task_id / asset_id 并写「请复用为 from_asset_ids /
      image_url，禁止重新生成」。先用 hh_image_edit 调整，不要再
      hh_image_create。
  R4. 派单前先看 BLACKBOARD / TASK_DELIVERED 里是否已经有可复用
      资产；已交付 asset_id 必须落到下一条派单的「上游 asset_ids」。

最终把每段视频 task_id 按出场顺序整理为列表，连同 transition /
fade_duration（cut→none、crossfade→crossfade、ai_extend→
hh_long_video_create 衔接），派给 wb_long 做 hh_long_video_create
+ hh_video_concat 拼接成片，再 deliverable 回给制片人。
"""

_WB_IMAGE_PERSONA = """\
HappyHorse 图像工作台。只在收到 org_delegate_task 时启动。按派单
prompt + 上游 asset_ids 选择合适的 hh_image_* 工具：
  - 文生图首帧/海报：hh_image_create
  - 局部编辑/多图融合：hh_image_edit（需 images 上游）
  - 风格迁移：hh_image_style_repaint
  - 替换背景：hh_image_background
  - 画幅扩展：hh_image_outpaint
  - 涂鸦/线稿成图：hh_image_sketch
  - 电商场景：hh_image_ecommerce
deliverable 文本里说明镜头号 + 中文画面 + 提示词摘要 + 生成的
asset_id；不要重复声明 file_attachments。
"""

_WB_VIDEO_PERSONA = """\
HappyHorse 短视频工作台。
  - 无首帧素材时直接 hh_t2v；
  - 派单里有上游 image asset_ids 时用 hh_i2v；
  - 多张参考图走 hh_r2v；
  - 基于现有视频改写走 hh_video_edit。
多镜头必须按镜头逐个调用，每次只消费当前镜头的 from_asset_ids /
first_frame_url，并按分镜时长设置 duration。每次 prompt 必须写清
当前镜头独有的中文画面/镜头运动/风格，绝不要用同一段总主题 prompt
重复生成。deliverable 必须列出每段 video task_id 给下游拼接。
"""

_WB_HUMAN_PERSONA = """\
HappyHorse 数字人工作台。
  - 一张照片 + 文本/音频 ⇒ hh_photo_speak（说话头像）。
  - 替换现有视频口型：hh_video_relip（需 source_video_url + 新音频）。
  - 替换现有视频的脸：hh_video_reface（需 source_video_url + ref 人脸图）。
  - 用驱动视频姿态控制目标人物：hh_pose_drive。
  - 多图合成口播：hh_avatar_compose。
派单只给 text 而无 voice_id 时使用默认音色；上级要求特定音色应
显式写入 prompt。deliverable 必须列出 video task_id。
"""

_WB_LONG_PERSONA = """\
HappyHorse 长视频后期工作台（happyhorse-video 的 art_director 模式
负责拼接：拥有 hh_long_video_create / hh_video_concat / hh_storyboard
_decompose / hh_cost_preview / hh_status / hh_list）。

标准流程：
1) (可选) 重新衔接首尾帧 + 并发生成 i2v：调 hh_long_video_create——
   传入完整 segments[]、model_id（默认 happyhorse-1.0-i2v）、
   aspect_ratio、resolution、mode（serial / parallel / cloud_extend）。
   返回 chain_group_id。
2) 拼接：hh_video_concat 传 task_ids（按出场顺序），
   transition（'none' 硬切；'crossfade' / 'fade' / 'xfade' / 'dissolve'
   触发 ffmpeg xfade；'ai_extend'/'cut' 归一化为 'none'），
   fade_duration（crossfade 时长，秒），output_name（可空）。
3) 用 hh_status / hh_list 跟踪进度，hh_cost_preview 评估批量成本。
deliverable 文本里写：成片时长 / 段数 / 转场方式 / 最终 asset_id。
"""


@template
def aigc_video_studio() -> TemplateSpec:
    """Return a fresh :class:`TemplateSpec` for the AIGC Video Studio."""
    return TemplateSpec(
        id="aigc_video_studio",
        name="AIGC Video Studio",
        category="aigc",
        description=(
            "Seven-node creative agency wired to the happyhorse-video plugin: "
            "producer drives a screenwriter (storyboard decomposition) and an "
            "art director who routes shots across image / video / digital-human "
            "workbenches and finally stitches the master cut. Mirrors the "
            "legacy aigc-video-studio template but in the v2 schema."
        ),
        version=1,
        defaults=DefaultsSpec(max_turns=60, max_stalls=4, suspect_secs=120),
        nodes=(
            NodeSpec(
                id="producer",
                type=NodeType.LLM,
                role="producer",
                label="制片人",
                persona_prompt=_PRODUCER_PERSONA,
                tool_subset=("research", "planning", "filesystem", "memory"),
            ),
            NodeSpec(
                id="screenwriter",
                type=NodeType.LLM,
                role="screenwriter",
                label="编剧",
                persona_prompt=_SCREENWRITER_PERSONA,
                tool_subset=(
                    "research",
                    "planning",
                    "filesystem",
                    "memory",
                    "hh_storyboard_decompose",
                ),
            ),
            NodeSpec(
                id="art_director",
                type=NodeType.LLM,
                role="art_director",
                label="美术指导",
                persona_prompt=_ART_DIRECTOR_PERSONA,
                tool_subset=("research", "planning", "filesystem", "memory"),
            ),
            NodeSpec(
                id="wb_image",
                type=NodeType.WORKBENCH,
                role="image_artist",
                label="图像工作台",
                persona_prompt=_WB_IMAGE_PERSONA,
                workbench=WorkbenchBindingSpec(
                    plugin_id=PLUGIN_ID,
                    mode="image_artist",
                ),
            ),
            NodeSpec(
                id="wb_video",
                type=NodeType.WORKBENCH,
                role="video_animator",
                label="短视频工作台",
                persona_prompt=_WB_VIDEO_PERSONA,
                workbench=WorkbenchBindingSpec(
                    plugin_id=PLUGIN_ID,
                    mode="video_animator",
                ),
            ),
            NodeSpec(
                id="wb_human",
                type=NodeType.WORKBENCH,
                role="portrait_actor",
                label="数字人工作台",
                persona_prompt=_WB_HUMAN_PERSONA,
                workbench=WorkbenchBindingSpec(
                    plugin_id=PLUGIN_ID,
                    mode="portrait_actor",
                ),
            ),
            NodeSpec(
                id="wb_long",
                type=NodeType.WORKBENCH,
                role="long_video_director",
                label="长视频后期工作台",
                persona_prompt=_WB_LONG_PERSONA,
                # The stitching role lives in the art_director mode of the
                # happyhorse-video manifest because that is where
                # hh_long_video_create / hh_video_concat / hh_storyboard_
                # decompose / hh_cost_preview / hh_status / hh_list are
                # whitelisted. We narrow the manifest with capabilities so
                # the WorkbenchNode does not also expose director-level
                # planning tools the persona doesn't need.
                workbench=WorkbenchBindingSpec(
                    plugin_id=PLUGIN_ID,
                    mode="art_director",
                    capabilities=("storyboard", "long_video", "video_concat"),
                ),
            ),
        ),
        edges=(
            EdgeSpec(src="producer", dst="screenwriter", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="producer", dst="art_director", kind=EdgeKind.HIERARCHY),
            EdgeSpec(
                src="screenwriter",
                dst="art_director",
                kind=EdgeKind.COLLABORATE,
            ),
            EdgeSpec(
                src="screenwriter",
                dst="wb_long",
                kind=EdgeKind.COLLABORATE,
            ),
            EdgeSpec(src="art_director", dst="wb_image", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="art_director", dst="wb_video", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="art_director", dst="wb_human", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="art_director", dst="wb_long", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="wb_image", dst="wb_video", kind=EdgeKind.COLLABORATE),
            EdgeSpec(src="wb_image", dst="wb_human", kind=EdgeKind.COLLABORATE),
            EdgeSpec(src="wb_video", dst="wb_long", kind=EdgeKind.COLLABORATE),
            EdgeSpec(src="wb_human", dst="wb_long", kind=EdgeKind.COLLABORATE),
        ),
    )
