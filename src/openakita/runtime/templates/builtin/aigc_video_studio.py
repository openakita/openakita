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
1) 首轮同时声明完整派单 DAG：screenwriter 步骤使用 step_id=storyboard；
   art_director 步骤使用 step_id=visual_production、depends_on=[storyboard]。
   runtime 会先执行编剧，再把真实剧本 + segments JSON 自动注入美术指导派单，
   不要等下一轮才派 visual_production。
2) 要求 screenwriter 返回剧本 + segments JSON（含 prompt / duration /
   transition_to_next 等字段）；要求 art_director 按注入的分镜分发到内容
   工作台，并在需要时调度长视频后期工作台拼接成片。
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
   每个分镜必须保留稳定且唯一的 segment_id，后续图像和视频工具调用
   都要原样传入该 segment_id，运行时据此绑定同一镜头的资产。
3) 用 org_submit_deliverable 返回：summary 写剧本摘要与 segments 概览；
   artifacts 分别登记完整剧本和 segments JSON，填写 kind、status 和真实
   paths。讨论/问询不要凭空调工具或资产。
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
  R4. runtime 会在派单正文中注入【结构化上游交付账本】。只读取该 JSON
      中的 segments / asset_ids / task_ids 声明局部 DAG；禁止 glob、read_file、
      run_powershell 或扫描工作区寻找上游文件。账本已交付 asset_id 必须落到
      下一条派单的「上游 asset_ids」。
  R5. 每个图像/视频步骤都必须在 org_delegate_task.media_spec 中声明 kind、
      output_group、aspect_ratio、resolution、width、height、duration_s。同一最终
      成片的所有视频段使用同一 output_group 和像素规格；只有用户明确要求多条
      成片时才能使用不同 output_group。未指定清晰度时统一使用 720P/1280x720。
      duration_s 写分镜目标时长，runtime 会在付费提交前按模型最小时长规范化。

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
org_submit_deliverable 的 summary 说明镜头号、中文画面和提示词摘要；
artifacts 登记 kind=image、status=ready、segment_id，以及工具真实返回的
asset_ids/task_ids/paths，不要只在正文里写 asset_id。
每次生成必须传入派单中的 segment_id，且同一镜头重做时不得改号。
hh_image_create / hh_image_edit 必须把派单画幅传为 output_ratio，并明确
size（默认 2K）；插件会换算为像素规格并在下载后校验，不合格结果不得交付。
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
重复生成。每次调用必须传入派单中的 segment_id，runtime 会自动绑定
同 segment 的上游关键帧；aspect_ratio 和 resolution 必须与派单一致。
派单中的 media_spec 是强制规格合同；不得自行切换 720P/1080P。若目标时长
低于模型最小值，runtime 会在调用前提升到模型最小值并记录规范化事件。
插件会在付费生成前校验首帧画幅，并在下载后用 ffprobe 校验成片尺寸。
org_submit_deliverable 的 artifacts 必须按段登记 kind=video、status=ready、
segment_id，以及工具真实返回的 video task_ids/asset_ids/paths，供下游拼接。
"""

_WB_HUMAN_PERSONA = """\
HappyHorse 数字人工作台。
  - 一张照片 + 文本/音频 ⇒ hh_photo_speak（说话头像）。
  - 替换现有视频口型：hh_video_relip（需 source_video_url + 新音频）。
  - 替换现有视频的脸：hh_video_reface（需 source_video_url + ref 人脸图）。
  - 用驱动视频姿态控制目标人物：hh_pose_drive。
  - 多图合成口播：hh_avatar_compose。
派单只给 text 而无 voice_id 时使用默认音色；上级要求特定音色应
显式写入 prompt。org_submit_deliverable 的 artifacts 必须登记 kind=video、
status=ready 以及真实 task_ids/asset_ids/paths。
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
org_submit_deliverable 的 artifacts 必须登记最终成片 kind=video、status=ready
以及真实 task_ids/asset_ids/paths；summary 写成片时长、段数和转场方式。
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
                department="制作部",
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
                department="创意",
            ),
            NodeSpec(
                id="art_director",
                type=NodeType.LLM,
                role="art_director",
                label="美术指导",
                persona_prompt=_ART_DIRECTOR_PERSONA,
                tool_subset=("research", "planning", "filesystem", "memory"),
                department="美术",
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
                department="图像生成",
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
                department="视频生成",
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
                department="数字人",
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
                department="后期",
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
                kind=EdgeKind.ARTIFACT,
                binding={
                    "source_port": "storyboard",
                    "target_port": "segments",
                    "target_tools": ["hh_long_video_create"],
                    "target_param": "segments",
                    "value_field": "segments",
                    "required": False,
                    "cardinality": "many",
                    "selection": "command_scoped",
                },
            ),
            EdgeSpec(src="art_director", dst="wb_image", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="art_director", dst="wb_video", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="art_director", dst="wb_human", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="art_director", dst="wb_long", kind=EdgeKind.HIERARCHY),
            EdgeSpec(
                src="wb_image",
                dst="wb_video",
                kind=EdgeKind.ARTIFACT,
                binding={
                    "source_port": "keyframes",
                    "target_port": "source_frames",
                    "target_tools": ["hh_i2v", "hh_r2v"],
                    "target_param": "from_asset_ids",
                    "value_field": "asset_ids",
                    "accepts": ["image"],
                    "join_key": "segment_id",
                    "required": True,
                    "cardinality": "one",
                    "selection": "matching_or_latest",
                    "activation": "when_ready",
                    "dispatch_mode": "join_all",
                    "join_scope": {
                        "source": "screenwriter",
                        "value_field": "segments",
                        "key_field": "segment_id",
                    },
                    "max_attempts": 1,
                },
            ),
            EdgeSpec(
                src="wb_image",
                dst="wb_human",
                kind=EdgeKind.ARTIFACT,
                binding={
                    "source_port": "portraits",
                    "target_port": "source_images",
                    "target_tools": [
                        "hh_photo_speak",
                        "hh_video_reface",
                        "hh_pose_drive",
                        "hh_avatar_compose",
                    ],
                    "target_param": "from_asset_ids",
                    "value_field": "asset_ids",
                    "accepts": ["image"],
                    "join_key": "segment_id",
                    "required": False,
                    "cardinality": "one",
                    "selection": "matching_or_latest",
                },
            ),
            EdgeSpec(
                src="wb_video",
                dst="wb_long",
                kind=EdgeKind.ARTIFACT,
                binding={
                    "source_port": "video_tasks",
                    "target_port": "segments",
                    "target_tools": ["hh_video_concat"],
                    "target_param": "task_ids",
                    "value_field": "task_ids",
                    "accepts": ["video"],
                    "required": True,
                    "cardinality": "many",
                    "selection": "command_scoped",
                    "activation": "when_ready",
                    "dispatch_mode": "join_all",
                    "join_scope": {
                        "source": "screenwriter",
                        "value_field": "segments",
                        "key_field": "segment_id",
                    },
                    "max_attempts": 1,
                },
            ),
            EdgeSpec(
                src="wb_human",
                dst="wb_long",
                kind=EdgeKind.ARTIFACT,
                binding={
                    "source_port": "video_tasks",
                    "target_port": "segments",
                    "target_tools": ["hh_video_concat"],
                    "target_param": "task_ids",
                    "value_field": "task_ids",
                    "accepts": ["video"],
                    "required": False,
                    "cardinality": "many",
                    "selection": "command_scoped",
                },
            ),
        ),
    )
