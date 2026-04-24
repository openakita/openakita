Seedance 2.0 系列模型（包括 Seedance 2.0 和 Seedance 2.0 fast ）支持图像、视频、音频、文本等多种模态内容输入，具备视频生成、视频编辑、视频延长等能力，可高精度还原物品细节、音色、效果、风格、运镜等，保持稳定角色特征，赋予使用者如同导演般的掌控权。本文介绍 Seedance 2.0 系列模型的专属能力，帮助您快速实现 [Video Generation API](https://www.volcengine.com/docs/82379/1520758) 调用。
:::tip
Seedance 2.0 及 Seedance 2.0 fast API 现已面向**企业用户**开启公测。参与公测请 [点击此处](https://www.volcengine.com/contact/seedance2-0public) 提交申请，审核结果将以短信形式另行通知，请您耐心等候。
:::
<span id="e000144b"></span>
# 新手入门
本入门教程专为 **API 新手用户** 设计，帮助您一键搭建 Python 开发环境、完成虚拟环境创建和方舟 SDK 安装，并提供直接可运行的 seedance 2.0 示例代码，您只需修改对应的输入素材，即可开始您的视频生成创作。
**1. 准备工作**
在开始之前，请确保您已经完成以下准备：

1. **注册账号**：确保您拥有火山引擎账号并已[登录](https://console.volcengine.com/)。
2. **获取 API Key**：访问 [API Key 管理页面](https://console.volcengine.com/ark/region:ark+cn-beijing/apikey)，点击 **创建 API Key**，并复制保存您的 API Key。注意请妥善保管您的 API Key，不要泄露给他人。
3. **开通模型（可选）** ：所有新用户可使用免费额度体验模型服务。如额度使用完毕需要[开通模型](https://console.volcengine.com/ark/region:ark+cn-beijing/openManagement)，以继续使用方舟模型服务。
4. **下载并解压文件**：点击下载下方附件，将其解压到您的本地目录（如桌面或“下载”文件夹）。
   <Attachment link="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/1c5fc49ecf2d40b89ef7dd12765e23e7~tplv-goo7wpa0wc-image.image" name="ark_seedance2.0_quickstart_package.zip"></Attachment>

**2.操作步骤**

```mixin-react
return (<Tabs>
<Tabs.TabPane title="Windows 用户" key="X0cvXR3YXb"><RenderMd content={`1. 进入 \`scripts/init_dev_env\` 目录。
2. 双击运行 \`setup_windows.bat\`。
3. 脚本会自动执行以下操作：
   * 下载 uv 工具。
   * 自动下载 Python 3.12（如果不干扰您的系统 Python）。
   * 创建虚拟环境 .\`venv\`。
   * 安装方舟 SDK。
4. 完成后，在项目根目录会生成一个 \`run_demo.bat\`。
5. 双击 \`run_demo.bat\`，即可运行 Python SDK 示例代码(python/demo_standard.py)。
`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="macOS 用户" key="Sh7ZRW82Oz"><RenderMd content={`1. 打开终端，进入 \`scripts/init_dev_env\` 目录。
2. 运行构建脚本：

\`\`\`Plain
./setup_mac.sh
\`\`\`


3. 脚本会自动配置好所有环境。
4. 完成后，在项目根目录会生成一个 \`run_demo.sh\`。
5. 运行 \`./run_demo.sh\` 即可运行 Python SDK 示例代码(python/demo_standard.py)。
`}></RenderMd></Tabs.TabPane></Tabs>);
```

**3.运行说明**
运行脚本后，您将看到如下流程：

1. **API Key 校验**：脚本会自动检测您本地是否配置了`ARK_API_KEY`环境变量。如果没有，会提示您手动输入。
2. **素材预览**：脚本会自动在您的默认浏览器中弹出一个本地生成的 HTML 页面，直观地展示本次任务的文本提示词、待替换的参考图片以及原始参考视频。
3. **任务创建与轮询**：脚本向火山方舟服务器发起异步请求。由于视频生成需要一定时间，控制台会每隔 30 秒打印一次任务状态（如 `running`等）。
4. **获取结果**：任务成功后，控制台会输出一段最终生成的视频 URL。您可以复制该链接到浏览器下载或在线播放。

**4.下一步**
在成功跑通本示例后，您可以尝试修改 `python/demo_standard.py`，来打造您专属的视频生成任务：

1. 修改文本提示词

找到代码中的 `user_content` 变量，更改为您想要的画面描述。

2. 替换输入素材 (图片、视频、音频)

您可以将 `reference_image_url`、`reference_video_url` 和 `reference_audio_url` 替换为您自己的素材链接。
**注意**：请确保 URL 是公网可公开访问的链接（建议存放在 TOS 对象存储服务中，并配置为公共读）。

3. 继续学习下文中丰富的使用示例。

<span id="fd30cc1a"></span>
# 模型能力
Seedance 2.0 fast 和 Seedance 2.0 的模型能力相同。追求最高生成品质，推荐使用 Seedance 2.0；更注重成本与生成速度，不要求极限品质，推荐使用 Seedance 2.0 fast。

<span aceTableMode="list" aceTableWidth="3,3,4,4"></span>
|模型名称 | |[Seedance 2.0](https://console.volcengine.com/ark/region:ark+cn-beijing/model/detail?Id=doubao-seedance-2-0&projectName=default) |[Seedance 2.0 fast](https://console.volcengine.com/ark/region:ark+cn-beijing/model/detail?Id=doubao-seedance-2-0-fast&projectName=default) |
|---|---|---|---|
|Model ID | |doubao\-seedance\-2\-0\-260128 |doubao\-seedance\-2\-0\-fast\-260128 |
|文生视频 | |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |
|图生视频\-首帧 | |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |
|图生视频\-首尾帧 | |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |
|多模态参考【New】 |图片参考 |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |
|^^|视频参考 |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |
|^^|组合参考|<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |\
| || | |\
| |* 图片 + 音频| | |\
| |* 图片 + 视频| | |\
| |* 视频 + 音频| | |\
| |* 图片 + 视频 + 音频 | | |
|编辑视频【New】 | |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |
|延长视频【New】 | |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |
|生成有声视频 | |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |
|联网搜索增强【New】 | |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |
|样片模式 | |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/f359753773c94d97885008ca1223c9bc~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/f359753773c94d97885008ca1223c9bc~tplv-goo7wpa0wc-image.image =20x) </span> |
|返回视频尾帧 | |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/ee51ce32c1914aed81ff95080bb7db1d~tplv-goo7wpa0wc-image.image =20x) </span> |
|输出视频规格 |输出分辨率 |480p, 720p |480p, 720p |
| |输出宽高比 |21:9, 16:9, 4:3, 1:1, 3:4, 9:16 ||
| |输出时长 |4~15 秒 |4~15 秒 |
| |输出视频格式 |mp4 |mp4 |
|离线推理 | |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/f359753773c94d97885008ca1223c9bc~tplv-goo7wpa0wc-image.image =20x) </span> |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/f359753773c94d97885008ca1223c9bc~tplv-goo7wpa0wc-image.image =20x) </span> |
|在线推理限流 |RPM |600 |600 |
| |并发数 |10 |10 |
|离线推理限流 |TPD |\- |\- |

<span id="dcb767c3"></span>
# 基础使用
<span id="50e1b4ea"></span>
## 多模态参考
输入文本、参考图、视频（可带音轨）和音频等内容，来生成一段新视频。可继承参考图片的角色形象、视觉风格、画面构图；参考视频的主体内容、运镜方式、动作表现、整体风格；以及参考音频的音色、音乐旋律、对话内容等核心信息。
效果预览如下（访问[模型卡片](https://console.volcengine.com/ark/region:ark+cn-beijing/model/detail?Id=doubao-seedance-2-0)查看更多示例）：

<span aceTableMode="list" aceTableWidth="4,5,5"></span>
|输入：文本 |输入：图片、视频、音频 |输出 |
|---|---|---|
|全程使用**视频1**的第一视角构图，全程使用**音频1**作为背景音乐。第一人称视角果茶宣传广告，seedance牌「苹苹安安」苹果果茶限定款；首帧为**图片1**，你的手摘下一颗带晨露的阿克苏红苹果，轻脆的苹果碰撞声；2\-4 秒：快速切镜，你的手将苹果块投入雪克杯，加入冰块与茶底，用力摇晃，冰块碰撞声与摇晃声卡点轻快鼓点，背景音：「鲜切现摇」；4\-6 秒：第一人称成品特写，分层果茶倒入透明杯，你的手轻挤奶盖在顶部铺展，在杯身贴上粉红包标，镜头拉近看奶盖与果茶的分层纹理；6\-8 秒：第一人称手持举杯，你将**图片2**中的果茶举到镜头前（模拟递到观众面前的视角），杯身标签清晰可见，背景音「来一口鲜爽」，尾帧定格为**图片2**。背景声音统一为女生音色。 |<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/0ba05cd435f543c5bc65c378d94d094a~tplv-goo7wpa0wc-image.image" controls></video>|<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/dab46ce2289a4a8ead76711bb02f2e1d~tplv-goo7wpa0wc-image.image" controls></video>|\
| || |\
| |<Attachment link="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/8bbbacecfd7d48dfa7ec6ec74125eb04~tplv-goo7wpa0wc-image.image" name="r2v_tea_audio1.mp3"></Attachment> | |


```mixin-react
return (<Tabs>
<Tabs.TabPane title="Python" key="AJQ5JD4id4"><RenderMd content={`\`\`\`Python
import os
import time
# Install SDK:  pip install 'volcengine-python-sdk[ark]'
from volcenginesdkarkruntime import Ark 

client = Ark(
    # The base URL for model invocation
    base_url='https://ark.cn-beijing.volces.com/api/v3',
    # Get API Key：https://console.volcengine.com/ark/region:ark+cn-beijing/apikey
    api_key=os.environ.get("ARK_API_KEY"),
)

if __name__ == "__main__":
    print("----- create request -----")
    create_result = client.content_generation.tasks.create(
        model="doubao-seedance-2-0-260128", # Replace with Model ID 
        content=[
            {
                "type": "text",
                "text": "全程使用视频1的第一视角构图，全程使用音频1作为背景音乐。第一人称视角果茶宣传广告，seedance牌「苹苹安安」苹果果茶限定款；首帧为图片1，你的手摘下一颗带晨露的阿克苏红苹果，轻脆的苹果碰撞声；2-4 秒：快速切镜，你的手将苹果块投入雪克杯，加入冰块与茶底，用力摇晃，冰块碰撞声与摇晃声卡点轻快鼓点，背景音：「鲜切现摇」；4-6 秒：第一人称成品特写，分层果茶倒入透明杯，你的手轻挤奶盖在顶部铺展，在杯身贴上粉红包标，镜头拉近看奶盖与果茶的分层纹理；6-8 秒：第一人称手持举杯，你将图片2中的果茶举到镜头前（模拟递到观众面前的视角），杯身标签清晰可见，背景音「来一口鲜爽」，尾帧定格为图片2。背景声音统一为女生音色。",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
                },
                "role": "reference_image",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg"
                },
                "role": "reference_image",
            },
            {
                "type": "video_url",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_tea_video1.mp4"
                },
                "role": "reference_video",
            },
            {
                "type": "audio_url",
                "audio_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_audio/r2v_tea_audio1.mp3"
                },
                "role": "reference_audio",
            },
        ],
        generate_audio=True,
        ratio="16:9",
        duration=11,
        watermark=True,
    )
    print(create_result)


    # Polling query section
    print("----- polling task status -----")
    task_id = create_result.id
    while True:
        get_result = client.content_generation.tasks.get(task_id=task_id)
        status = get_result.status
        if status == "succeeded":
            print("----- task succeeded -----")
            print(get_result)
            break
        elif status == "failed":
            print("----- task failed -----")
            print(f"Error: {get_result.error}")
            break
        else:
            print(f"Current status: {status}, Retrying after 30 seconds...")
            time.sleep(30)
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Java" key="reSluY5cEE"><RenderMd content={`\`\`\`Java
package com.ark.sample;

import com.volcengine.ark.runtime.model.content.generation.*;
import com.volcengine.ark.runtime.model.content.generation.CreateContentGenerationTaskRequest.Content;
import com.volcengine.ark.runtime.service.ArkService;
import okhttp3.ConnectionPool;
import okhttp3.Dispatcher;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

public class ContentGenerationTaskExample {

    // Client initialization
    static String apiKey = System.getenv("ARK_API_KEY");
    static ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);
    static Dispatcher dispatcher = new Dispatcher();
    static ArkService service = ArkService.builder()
           .baseUrl("https://ark.cn-beijing.volces.com/api/v3") // The base URL for model invocation
           .dispatcher(dispatcher)
           .connectionPool(connectionPool)
           .apiKey(apiKey)
           .build();
           
    public static void main(String[] args) {
        
        // Model ID
        final String modelId = "doubao-seedance-2-0-260128";
        // Text prompt
        final String prompt = "全程使用视频1的第一视角构图，全程使用音频1作为背景音乐。第一人称视角果茶宣传广告，seedance牌「苹苹安安」苹果果茶限定款；" +
                "首帧为图片1，你的手摘下一颗带晨露的阿克苏红苹果，轻脆的苹果碰撞声；" +
                "2-4 秒：快速切镜，你的手将苹果块投入雪克杯，加入冰块与茶底，用力摇晃，冰块碰撞声与摇晃声卡点轻快鼓点，背景音：「鲜切现摇」；" +
                "4-6 秒：第一人称成品特写，分层果茶倒入透明杯，你的手轻挤奶盖在顶部铺展，在杯身贴上粉红包标，镜头拉近看奶盖与果茶的分层纹理；" +
                "6-8 秒：第一人称手持举杯，你将图片2中的果茶举到镜头前（模拟递到观众面前的视角），杯身标签清晰可见，背景音「来一口鲜爽」，尾帧定格为图片2。" +
                "背景声音统一为女生音色。";
        
        // Example resource URLs
        final String refImage1 = "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg";
        final String refImage2 = "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg";
        final String refVideo = "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_tea_video1.mp4";
        final String refAudio = "https://ark-project.tos-cn-beijing.volces.com/doc_audio/r2v_tea_audio1.mp3";

        // Output video parameters
        final boolean generateAudio = true;
        final String videoRatio = "16:9";      
        final long videoDuration = 11L;          
        final boolean showWatermark = true;

        System.out.println("----- create request -----");
        // Build request content
        List<Content> contents = new ArrayList<>();
        
        // 1. Text prompt
        contents.add(Content.builder()
                .type("text")
                .text(prompt)
                .build());
                
        // 2. Reference image 1
        contents.add(Content.builder()
                .type("image_url")
                .imageUrl(CreateContentGenerationTaskRequest.ImageUrl.builder()
                        .url(refImage1)
                        .build())
                .role("reference_image")
                .build());

        // 3. Reference image 2
        contents.add(Content.builder()
                .type("image_url")
                .imageUrl(CreateContentGenerationTaskRequest.ImageUrl.builder()
                        .url(refImage2)
                        .build())
                .role("reference_image")
                .build());

        // 4. Reference video
        contents.add(Content.builder()
                .type("video_url")
                .videoUrl(CreateContentGenerationTaskRequest.VideoUrl.builder()
                        .url(refVideo)  
                        .build())
                .role("reference_video")
                .build());

        // 5. Reference audio
        contents.add(Content.builder()
                .type("audio_url")
                .audioUrl(CreateContentGenerationTaskRequest.AudioUrl.builder()
                        .url(refAudio)
                        .build())
                .role("reference_audio")
                .build());

        // Create video generation task
        CreateContentGenerationTaskRequest createRequest = CreateContentGenerationTaskRequest.builder()
                .generateAudio(generateAudio)
                .model(modelId)
                .content(contents)
                .ratio(videoRatio)
                .duration(videoDuration)
                .watermark(showWatermark)
                .build();

        CreateContentGenerationTaskResult createResult = service.createContentGenerationTask(createRequest);
        System.out.println("Task Created: " + createResult);

        // Get task details and poll status
        String taskId = createResult.getId();
        pollTaskStatus(taskId);
    }

    /**
     * Poll task status
     * @param taskId Task ID
     */

    private static void pollTaskStatus(String taskId) {
        GetContentGenerationTaskRequest getRequest = GetContentGenerationTaskRequest.builder()
                .taskId(taskId)
                .build();

        System.out.println("----- polling task status -----");
        try {
            while (true) {
                GetContentGenerationTaskResponse getResponse = service.getContentGenerationTask(getRequest);
                String status = getResponse.getStatus();

                if ("succeeded".equalsIgnoreCase(status)) {
                    System.out.println("----- task succeeded -----");
                    System.out.println(getResponse);
                    break;
                } else if ("failed".equalsIgnoreCase(status)) {
                    System.out.println("----- task failed -----");
                    if (getResponse.getError() != null) {
                        System.out.println("Error: " + getResponse.getError().getMessage());
                    }
                    break;
                } else {
                    System.out.printf("Current status: %s, Retrying in 10 seconds...%n", status);
                    TimeUnit.SECONDS.sleep(10);
                }
            }
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            System.err.println("Polling interrupted");
        } catch (Exception e) {
            System.err.println("Error occurred: " + e.getMessage());
        } finally {
            service.shutdownExecutor();
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Go" key="dZDK7RAw2Y"><RenderMd content={`\`\`\`Go
package main

import (
    "context"
    "fmt"
    "os"
    "time"

    "github.com/volcengine/volcengine-go-sdk/service/arkruntime"
    "github.com/volcengine/volcengine-go-sdk/service/arkruntime/model"
    "github.com/volcengine/volcengine-go-sdk/volcengine"
)

func main() {
    // Initialize Ark client
    client := arkruntime.NewClientWithApiKey(
        os.Getenv("ARK_API_KEY"),
        // The base URL for model invocation
        arkruntime.WithBaseUrl("https://ark.cn-beijing.volces.com/api/v3"),
    )
    ctx := context.Background()

    // Model ID
    modelID := "doubao-seedance-2-0-260128"
    // Text prompt
    prompt := "全程使用视频1的第一视角构图，全程使用音频1作为背景音乐。第一人称视角果茶宣传广告，seedance牌「苹苹安安」苹果果茶限定款；" +
        "首帧为图片1，你的手摘下一颗带晨露的阿克苏红苹果，轻脆的苹果碰撞声；" +
        "2-4 秒：快速切镜，你的手将苹果块投入雪克杯，加入冰块与茶底，用力摇晃，冰块碰撞声与摇晃声卡点轻快鼓点，背景音：「鲜切现摇」；" +
        "4-6 秒：第一人称成品特写，分层果茶倒入透明杯，你的手轻挤奶盖在顶部铺展，在杯身贴上粉红包标，镜头拉近看奶盖与果茶的分层纹理；" +
        "6-8 秒：第一人称手持举杯，你将图片2中的果茶举到镜头前（模拟递到观众面前的视角），杯身标签清晰可见，背景音「来一口鲜爽」，尾帧定格为图片2。" +
        "背景声音统一为女生音色。"

    // Example resource URLs
    refImage1 := "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg"
    refImage2 := "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg"
    refVideo := "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_tea_video1.mp4"
    refAudio := "https://ark-project.tos-cn-beijing.volces.com/doc_audio/r2v_tea_audio1.mp3"

    // Output video parameters
    generateAudio := true
    videoRatio := "16:9"
    videoDuration := int64(11)
    showWatermark := true

    // 1. Create video generation task
    fmt.Println("----- create request -----")
    createReq := model.CreateContentGenerationTaskRequest{
        Model:         modelID,
        GenerateAudio: volcengine.Bool(generateAudio),
        Ratio:         volcengine.String(videoRatio),
        Duration:      volcengine.Int64(videoDuration),
        Watermark:     volcengine.Bool(showWatermark),
        Content: []*model.CreateContentGenerationContentItem{
            {
                Type: model.ContentGenerationContentItemTypeText,
                Text: volcengine.String(prompt),
            },
            {
                Type: model.ContentGenerationContentItemType("image_url"),
                ImageURL: &model.ImageURL{
                    URL: refImage1,
                },
                Role: volcengine.String("reference_image"),
            },
            {
                Type: model.ContentGenerationContentItemType("image_url"),
                ImageURL: &model.ImageURL{
                    URL: refImage2,
                },
                Role: volcengine.String("reference_image"),
            },
            {
                Type: model.ContentGenerationContentItemType("video_url"),
                VideoURL: &model.VideoUrl{
                    Url: refVideo,
                },
                Role: volcengine.String("reference_video"),
            },
            {
                Type: model.ContentGenerationContentItemType("audio_url"),
                AudioURL: &model.AudioUrl{
                    Url: refAudio,
                },
                Role: volcengine.String("reference_audio"),
            },
        },
    }

    createResp, err := client.CreateContentGenerationTask(ctx, createReq)
    if err != nil {
        fmt.Printf("create content generation error: %v\\n", err)
        return
    }

    taskID := createResp.ID
    fmt.Printf("Task Created with ID: %s\\n", taskID)

    // 2. Poll task status
    pollTaskStatus(ctx, client, taskID)
}

// poll task status
func pollTaskStatus(ctx context.Context, client *arkruntime.Client, taskID string) {
    fmt.Println("----- polling task status -----")
    for {
        getReq := model.GetContentGenerationTaskRequest{ID: taskID}
        getResp, err := client.GetContentGenerationTask(ctx, getReq)
        if err != nil {
            fmt.Printf("get content generation task error: %v\\n", err)
            return
        }

        status := getResp.Status
        if status == "succeeded" {
            fmt.Println("----- task succeeded -----")
            fmt.Printf("Task ID: %s \\n", getResp.ID)
            fmt.Printf("Model: %s \\n", getResp.Model)
            fmt.Printf("Video URL: %s \\n", getResp.Content.VideoURL)
            fmt.Printf("Completion Tokens: %d \\n", getResp.Usage.CompletionTokens)
            fmt.Printf("Created At: %d, Updated At: %d\\n", getResp.CreatedAt, getResp.UpdatedAt)
            return
        } else if status == "failed" {
            fmt.Println("----- task failed -----")
            if getResp.Error != nil {
                fmt.Printf("Error Code: %s, Message: %s\\n", getResp.Error.Code, getResp.Error.Message)
            }
            return
        } else {
            fmt.Printf("Current status: %s, Retrying in 10 seconds... \\n", status)
            time.Sleep(10 * time.Second)
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane></Tabs>);
```

:::tip

* 您可任意组合以下模态内容，注意不支持“文本+音频”、“纯音频” 输入。
   * 文本
   * 图片：0~9 张
   * 视频：0~3 个
   * 音频：0~3 个
* **进阶用法**：多模态生视频可通过提示词指定参考图片作为首帧/尾帧，间接实现“首尾帧+多模态参考”效果。若需严格保障首尾帧和指定图片一致，**优先使用图生视频\-首尾帧**（配置 role 为 first_frame/last_frame）。
* 各个模态信息输入要求参见[多模态输入](/docs/82379/1366799#63a97f09)。

:::
<span id="75a28782"></span>
## 编辑视频
您可以提供待编辑的视频、参考图片或音频，并结合使用提示词，完成多种视频编辑任务，例如：替换视频主体、视频中对象增删改、局部画面重绘/修复等。
效果预览如下（访问[模型卡片](https://console.volcengine.com/ark/region:ark+cn-beijing/model/detail?Id=doubao-seedance-2-0)查看更多示例）：

<span aceTableMode="list" aceTableWidth="4,5,5"></span>
|输入：文本 |输入：视频&图片 |输出 |
|---|---|---|
|将**视频1**礼盒中的香水替换成**图像1**中的面霜，运镜不变 |<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/0a1afd3250d84b8995e9c0aa61b57d38~tplv-goo7wpa0wc-image.image" controls></video>|<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/fd7bcf4eaf504f50aeeebd48ce35c06a~tplv-goo7wpa0wc-image.image" controls></video>|\
| || |\
| |<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/791b783fc6cd4394b13f41b66b5ff461~tplv-goo7wpa0wc-image.image =280x) </span> | |


```mixin-react
return (<Tabs>
<Tabs.TabPane title="Python" key="MBBLnOwElh"><RenderMd content={`\`\`\`Python
import os
import time
# Install SDK:  pip install 'volcengine-python-sdk[ark]'
from volcenginesdkarkruntime import Ark 

client = Ark(
    # The base URL for model invocation
    base_url='https://ark.cn-beijing.volces.com/api/v3',
    # Get API Key：https://console.volcengine.com/ark/region:ark+cn-beijing/apikey
    api_key=os.environ.get("ARK_API_KEY"),
)

if __name__ == "__main__":
    print("----- create request -----")
    create_result = client.content_generation.tasks.create(
        model="doubao-seedance-2-0-260128", # Replace with Model ID 
        content=[
            {
                "type": "text",
                "text": "将视频1礼盒中的香水替换成图片1中的面霜，运镜不变",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_edit_pic1.jpg"
                },
                "role": "reference_image",
            },
            {
                "type": "video_url",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_edit_video1.mp4"
                },
                "role": "reference_video",
            },
        ],
        generate_audio=True,
        ratio="16:9",
        duration=5,
        watermark=True,
    )
    print(create_result)


    # Polling query section
    print("----- polling task status -----")
    task_id = create_result.id
    while True:
        get_result = client.content_generation.tasks.get(task_id=task_id)
        status = get_result.status
        if status == "succeeded":
            print("----- task succeeded -----")
            print(get_result)
            break
        elif status == "failed":
            print("----- task failed -----")
            print(f"Error: {get_result.error}")
            break
        else:
            print(f"Current status: {status}, Retrying after 30 seconds...")
            time.sleep(30)
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Java" key="LinIErs760"><RenderMd content={`\`\`\`Java
package com.ark.sample;

import com.volcengine.ark.runtime.model.content.generation.*;
import com.volcengine.ark.runtime.model.content.generation.CreateContentGenerationTaskRequest.Content;
import com.volcengine.ark.runtime.service.ArkService;
import okhttp3.ConnectionPool;
import okhttp3.Dispatcher;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

public class ContentGenerationTaskExample {

    // Client initialization
    static String apiKey = System.getenv("ARK_API_KEY");
    static ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);
    static Dispatcher dispatcher = new Dispatcher();
    static ArkService service = ArkService.builder()
           .baseUrl("https://ark.cn-beijing.volces.com/api/v3") // The base URL for model invocation
           .dispatcher(dispatcher)
           .connectionPool(connectionPool)
           .apiKey(apiKey)
           .build();
           
    public static void main(String[] args) {
        
        // Model ID
        final String modelId = "doubao-seedance-2-0-260128"; 
        // Text prompt
        final String prompt = "将视频1礼盒中的香水替换成图片1中的面霜，运镜不变";
        
        // Example resource URLs
        final String refImage1 = "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_edit_pic1.jpg";
        final String refVideo = "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_edit_video1.mp4";

        // Output video parameters
        final boolean generateAudio = true;
        final String videoRatio = "16:9";      
        final long videoDuration = 5L;          
        final boolean showWatermark = true;

        System.out.println("----- create request -----");
        // Build request content
        List<Content> contents = new ArrayList<>();
        
        // 1. Text prompt
        contents.add(Content.builder()
                .type("text")
                .text(prompt)
                .build());
                
        // 2. Reference image 1
        contents.add(Content.builder()
                .type("image_url")
                .imageUrl(CreateContentGenerationTaskRequest.ImageUrl.builder()
                        .url(refImage1)
                        .build())
                .role("reference_image")
                .build());

        // 3. Reference video
        contents.add(Content.builder()
                .type("video_url")
                .videoUrl(CreateContentGenerationTaskRequest.VideoUrl.builder()
                        .url(refVideo)  
                        .build())
                .role("reference_video")
                .build());

        // Create video generation task
        CreateContentGenerationTaskRequest createRequest = CreateContentGenerationTaskRequest.builder()
                .generateAudio(generateAudio)
                .model(modelId)
                .content(contents)
                .ratio(videoRatio)
                .duration(videoDuration)
                .watermark(showWatermark)
                .build();

        CreateContentGenerationTaskResult createResult = service.createContentGenerationTask(createRequest);
        System.out.println("Task Created: " + createResult);

        // Get task details and poll status
        String taskId = createResult.getId();
        pollTaskStatus(taskId);
    }

    /**
     * Poll task status
     * @param taskId Task ID
     */

    private static void pollTaskStatus(String taskId) {
        GetContentGenerationTaskRequest getRequest = GetContentGenerationTaskRequest.builder()
                .taskId(taskId)
                .build();

        System.out.println("----- polling task status -----");
        try {
            while (true) {
                GetContentGenerationTaskResponse getResponse = service.getContentGenerationTask(getRequest);
                String status = getResponse.getStatus();

                if ("succeeded".equalsIgnoreCase(status)) {
                    System.out.println("----- task succeeded -----");
                    System.out.println(getResponse);
                    break;
                } else if ("failed".equalsIgnoreCase(status)) {
                    System.out.println("----- task failed -----");
                    if (getResponse.getError() != null) {
                        System.out.println("Error: " + getResponse.getError().getMessage());
                    }
                    break;
                } else {
                    System.out.printf("Current status: %s, Retrying in 10 seconds...%n", status);
                    TimeUnit.SECONDS.sleep(10);
                }
            }
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            System.err.println("Polling interrupted");
        } catch (Exception e) {
            System.err.println("Error occurred: " + e.getMessage());
        } finally {
            service.shutdownExecutor();
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Go" key="DveWMHzyTg"><RenderMd content={`\`\`\`Go
package main

import (
    "context"
    "fmt"
    "os"
    "time"

    "github.com/volcengine/volcengine-go-sdk/service/arkruntime"
    "github.com/volcengine/volcengine-go-sdk/service/arkruntime/model"
    "github.com/volcengine/volcengine-go-sdk/volcengine"
)

func main() {
    // Initialize Ark client
    client := arkruntime.NewClientWithApiKey(
        os.Getenv("ARK_API_KEY"),
        // The base URL for model invocation
        arkruntime.WithBaseUrl("https://ark.cn-beijing.volces.com/api/v3"),
    )
    ctx := context.Background()

    // Model ID
    modelID := "doubao-seedance-2-0-260128"
    // Text prompt
    prompt := "将视频1礼盒中的香水替换成图片1中的面霜，运镜不变"

    // Example resource URLs
    refImage1 := "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_edit_pic1.jpg"
    refVideo1 := "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_edit_video1.mp4"

    // Output video parameters
    generateAudio := true
    videoRatio := "16:9"
    videoDuration := int64(5)
    showWatermark := true

    // 1. Create video generation task
    fmt.Println("----- create request -----")
    createReq := model.CreateContentGenerationTaskRequest{
        Model:         modelID,
        GenerateAudio: volcengine.Bool(generateAudio),
        Ratio:         volcengine.String(videoRatio),
        Duration:      volcengine.Int64(videoDuration),
        Watermark:     volcengine.Bool(showWatermark),
        Content: []*model.CreateContentGenerationContentItem{
            {
                Type: model.ContentGenerationContentItemTypeText,
                Text: volcengine.String(prompt),
            },
            {
                Type: model.ContentGenerationContentItemType("image_url"),
                ImageURL: &model.ImageURL{
                    URL: refImage1,
                },
                Role: volcengine.String("reference_image"),
            },
            {
                Type: model.ContentGenerationContentItemType("video_url"),
                VideoURL: &model.VideoUrl{
                    Url: refVideo1,
                },
                Role: volcengine.String("reference_video"),
            },
        },
    }

    createResp, err := client.CreateContentGenerationTask(ctx, createReq)
    if err != nil {
        fmt.Printf("create content generation error: %v\\n", err)
        return
    }

    taskID := createResp.ID
    fmt.Printf("Task Created with ID: %s\\n", taskID)

    // 2. Poll task status
    pollTaskStatus(ctx, client, taskID)
}

// poll task status
func pollTaskStatus(ctx context.Context, client *arkruntime.Client, taskID string) {
    fmt.Println("----- polling task status -----")
    for {
        getReq := model.GetContentGenerationTaskRequest{ID: taskID}
        getResp, err := client.GetContentGenerationTask(ctx, getReq)
        if err != nil {
            fmt.Printf("get content generation task error: %v\\n", err)
            return
        }

        status := getResp.Status
        if status == "succeeded" {
            fmt.Println("----- task succeeded -----")
            fmt.Printf("Task ID: %s \\n", getResp.ID)
            fmt.Printf("Model: %s \\n", getResp.Model)
            fmt.Printf("Video URL: %s \\n", getResp.Content.VideoURL)
            fmt.Printf("Completion Tokens: %d \\n", getResp.Usage.CompletionTokens)
            fmt.Printf("Created At: %d, Updated At: %d\\n", getResp.CreatedAt, getResp.UpdatedAt)
            return
        } else if status == "failed" {
            fmt.Println("----- task failed -----")
            if getResp.Error != nil {
                fmt.Printf("Error Code: %s, Message: %s\\n", getResp.Error.Code, getResp.Error.Message)
            }
            return
        } else {
            fmt.Printf("Current status: %s, Retrying in 10 seconds... \\n", status)
            time.Sleep(10 * time.Second)
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane></Tabs>);
```

<span id="46d77653"></span>
## 延长视频
在原有视频基础上，向前或者向后延长视频，或多个视频片段（最多 3 个视频片段）串联成一个连贯视频。
效果预览如下（访问[模型卡片](https://console.volcengine.com/ark/region:ark+cn-beijing/model/detail?Id=doubao-seedance-2-0)查看更多示例）：

<span aceTableMode="list" aceTableWidth="4,5,5"></span>
|输入：文本 |输入：待延长视频 |输出 |
|---|---|---|
|**视频1**中的拱形窗户打开，进入美术馆室内，接**视频2**，之后镜头进入画内，接**视频3** |<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/54519ff7266d4f1caa12b8cc95e2dd1d~tplv-goo7wpa0wc-image.image" controls></video>|<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/849b3f86f609495ca09d559aa14c79ed~tplv-goo7wpa0wc-image.image" controls></video>|\
| || |\
| |<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/b15d56c80c884faa8526beb6ca540b98~tplv-goo7wpa0wc-image.image" controls></video>| |\
| || |\
| |<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/f5d327311e094361b15dca0a37b14ab4~tplv-goo7wpa0wc-image.image" controls></video>| |\
| | | |


```mixin-react
return (<Tabs>
<Tabs.TabPane title="Python" key="FZUYSi22bC"><RenderMd content={`\`\`\`Python
import os
import time
# Install SDK:  pip install 'volcengine-python-sdk[ark]'
from volcenginesdkarkruntime import Ark 

client = Ark(
    # The base URL for model invocation
    base_url='https://ark.cn-beijing.volces.com/api/v3',
    # Get API Key：https://console.volcengine.com/ark/region:ark+cn-beijing/apikey
    api_key=os.environ.get("ARK_API_KEY"),
)

if __name__ == "__main__":
    print("----- create request -----")
    create_result = client.content_generation.tasks.create(
        model="doubao-seedance-2-0-260128", # Replace with Model ID 
        content=[
            {
                "type": "text",
                "text": "视频1中的拱形窗户打开，进入美术馆室内，接视频2，之后镜头进入画内，接视频3",
                
            },
            {
                "type": "video_url",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video1.mp4"
                },
                "role": "reference_video",
            },
            {
                "type": "video_url",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video2.mp4"
                },
                "role": "reference_video",
            },
            {
                "type": "video_url",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video3.mp4"
                },
                "role": "reference_video",
            },
        ],
        generate_audio=True,
        ratio="16:9",
        duration=8,
        watermark=True,
    )
    print(create_result)


    # Polling query section
    print("----- polling task status -----")
    task_id = create_result.id
    while True:
        get_result = client.content_generation.tasks.get(task_id=task_id)
        status = get_result.status
        if status == "succeeded":
            print("----- task succeeded -----")
            print(get_result)
            break
        elif status == "failed":
            print("----- task failed -----")
            print(f"Error: {get_result.error}")
            break
        else:
            print(f"Current status: {status}, Retrying after 30 seconds...")
            time.sleep(30)
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Java" key="nWDbi2p6B6"><RenderMd content={`\`\`\`Java
package com.ark.sample;

import com.volcengine.ark.runtime.model.content.generation.*;
import com.volcengine.ark.runtime.model.content.generation.CreateContentGenerationTaskRequest.Content;
import com.volcengine.ark.runtime.service.ArkService;
import okhttp3.ConnectionPool;
import okhttp3.Dispatcher;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

public class ContentGenerationTaskExample {

    // Client initialization
    static String apiKey = System.getenv("ARK_API_KEY");
    static ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);
    static Dispatcher dispatcher = new Dispatcher();
    static ArkService service = ArkService.builder()
           .baseUrl("https://ark.cn-beijing.volces.com/api/v3") // The base URL for model invocation
           .dispatcher(dispatcher)
           .connectionPool(connectionPool)
           .apiKey(apiKey)
           .build();
           
    public static void main(String[] args) {
        
        // Model ID
        final String modelId = "doubao-seedance-2-0-260128";
        // Text prompt
        final String prompt = "视频1中的拱形窗户打开，进入美术馆室内，接视频2，之后镜头进入画内，接视频3";
        
        // Example resource URLs
        final String refVideo1 = "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video1.mp4";
        final String refVideo2 = "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video2.mp4";
        final String refVideo3 = "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video3.mp4";

        // Output video parameters
        final boolean generateAudio = true;
        final String videoRatio = "16:9";      
        final long videoDuration = 8L;          
        final boolean showWatermark = true;

        System.out.println("----- create request -----");
        // Build request content
        List<Content> contents = new ArrayList<>();
        
        // 1. Text prompt
        contents.add(Content.builder()
                .type("text")
                .text(prompt)
                .build());
                
        // 2. Reference video 1
        contents.add(Content.builder()
                .type("video_url")
                .videoUrl(CreateContentGenerationTaskRequest.VideoUrl.builder()
                        .url(refVideo1)  
                        .build())
                .role("reference_video")
                .build());

        // 3. Reference video 2
        contents.add(Content.builder()
                .type("video_url")
                .videoUrl(CreateContentGenerationTaskRequest.VideoUrl.builder()
                        .url(refVideo2)  
                        .build())
                .role("reference_video")
                .build());

        // 4. Reference video 3
        contents.add(Content.builder()
                .type("video_url")
                .videoUrl(CreateContentGenerationTaskRequest.VideoUrl.builder()
                        .url(refVideo3)  
                        .build())
                .role("reference_video")
                .build());

        // Create video generation task
        CreateContentGenerationTaskRequest createRequest = CreateContentGenerationTaskRequest.builder()
                .generateAudio(generateAudio)
                .model(modelId)
                .content(contents)
                .ratio(videoRatio)
                .duration(videoDuration)
                .watermark(showWatermark)
                .build();

        CreateContentGenerationTaskResult createResult = service.createContentGenerationTask(createRequest);
        System.out.println("Task Created: " + createResult);

        // Get task details and poll status
        String taskId = createResult.getId();
        pollTaskStatus(taskId);
    }

    /**
     * Poll task status
     * @param taskId Task ID
     */

    private static void pollTaskStatus(String taskId) {
        GetContentGenerationTaskRequest getRequest = GetContentGenerationTaskRequest.builder()
                .taskId(taskId)
                .build();

        System.out.println("----- polling task status -----");
        try {
            while (true) {
                GetContentGenerationTaskResponse getResponse = service.getContentGenerationTask(getRequest);
                String status = getResponse.getStatus();

                if ("succeeded".equalsIgnoreCase(status)) {
                    System.out.println("----- task succeeded -----");
                    System.out.println(getResponse);
                    break;
                } else if ("failed".equalsIgnoreCase(status)) {
                    System.out.println("----- task failed -----");
                    if (getResponse.getError() != null) {
                        System.out.println("Error: " + getResponse.getError().getMessage());
                    }
                    break;
                } else {
                    System.out.printf("Current status: %s, Retrying in 10 seconds...%n", status);
                    TimeUnit.SECONDS.sleep(10);
                }
            }
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            System.err.println("Polling interrupted");
        } catch (Exception e) {
            System.err.println("Error occurred: " + e.getMessage());
        } finally {
            service.shutdownExecutor();
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Go" key="jGtbK0Sv0v"><RenderMd content={`\`\`\`Go
package main

import (
    "context"
    "fmt"
    "os"
    "time"

    "github.com/volcengine/volcengine-go-sdk/service/arkruntime"
    "github.com/volcengine/volcengine-go-sdk/service/arkruntime/model"
    "github.com/volcengine/volcengine-go-sdk/volcengine"
)

func main() {
    // Initialize Ark client
    client := arkruntime.NewClientWithApiKey(
        os.Getenv("ARK_API_KEY"),
        // The base URL for model invocation
        arkruntime.WithBaseUrl("https://ark.cn-beijing.volces.com/api/v3"),
    )
    ctx := context.Background()

    // Model ID
    modelID := "doubao-seedance-2-0-260128"
    // Text prompt
    prompt := "视频1中的拱形窗户打开，进入美术馆室内，接视频2，之后镜头进入画内，接视频3"

    // Example resource URLs
    refVideo1 := "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video1.mp4"
    refVideo2 := "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video2.mp4"
    refVideo3 := "https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video3.mp4"

    // Output video parameters
    generateAudio := true
    videoRatio := "16:9"
    videoDuration := int64(8)
    showWatermark := true

    // 1. Create video generation task
    fmt.Println("----- create request -----")
    createReq := model.CreateContentGenerationTaskRequest{
        Model:         modelID,
        GenerateAudio: volcengine.Bool(generateAudio),
        Ratio:         volcengine.String(videoRatio),
        Duration:      volcengine.Int64(videoDuration),
        Watermark:     volcengine.Bool(showWatermark),
        Content: []*model.CreateContentGenerationContentItem{
            {
                Type: model.ContentGenerationContentItemTypeText,
                Text: volcengine.String(prompt),
            },
            {
                Type: model.ContentGenerationContentItemType("video_url"),
                VideoURL: &model.VideoUrl{
                    Url: refVideo1,
                },
                Role: volcengine.String("reference_video"),
            },
            {
                Type: model.ContentGenerationContentItemType("video_url"),
                VideoURL: &model.VideoUrl{
                    Url: refVideo2,
                },
                Role: volcengine.String("reference_video"),
            },
            {
                Type: model.ContentGenerationContentItemType("video_url"),
                VideoURL: &model.VideoUrl{
                    Url: refVideo3,
                },
                Role: volcengine.String("reference_video"),
            },
        },
    }

    createResp, err := client.CreateContentGenerationTask(ctx, createReq)
    if err != nil {
        fmt.Printf("create content generation error: %v\\n", err)
        return
    }

    taskID := createResp.ID
    fmt.Printf("Task Created with ID: %s\\n", taskID)

    // 2. Poll task status
    pollTaskStatus(ctx, client, taskID)
}

// poll task status
func pollTaskStatus(ctx context.Context, client *arkruntime.Client, taskID string) {
    fmt.Println("----- polling task status -----")
    for {
        getReq := model.GetContentGenerationTaskRequest{ID: taskID}
        getResp, err := client.GetContentGenerationTask(ctx, getReq)
        if err != nil {
            fmt.Printf("get content generation task error: %v\\n", err)
            return
        }

        status := getResp.Status
        if status == "succeeded" {
            fmt.Println("----- task succeeded -----")
            fmt.Printf("Task ID: %s \\n", getResp.ID)
            fmt.Printf("Model: %s \\n", getResp.Model)
            fmt.Printf("Video URL: %s \\n", getResp.Content.VideoURL)
            fmt.Printf("Completion Tokens: %d \\n", getResp.Usage.CompletionTokens)
            fmt.Printf("Created At: %d, Updated At: %d\\n", getResp.CreatedAt, getResp.UpdatedAt)
            return
        } else if status == "failed" {
            fmt.Println("----- task failed -----")
            if getResp.Error != nil {
                fmt.Printf("Error Code: %s, Message: %s\\n", getResp.Error.Code, getResp.Error.Message)
            }
            return
        } else {
            fmt.Printf("Current status: %s, Retrying in 10 seconds... \\n", status)
            time.Sleep(10 * time.Second)
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane></Tabs>);
```

:::tip

* 向前或向后延长 1 段视频，生成的视频一般只包含原视频的尾部画面。但您也可以通过提示词灵活控制，使其包含原视频内容。 例如：向前延长视频1，[延长内容描述...]，**最后接视频1**。
* 传入 2~3 段视频，补全中间过渡部分，生成的视频会包含原视频内容和新生成的视频内容。

:::
<span id="c40ed3ef"></span>
## 使用联网搜索
> 联网搜索能力仅适用于纯文本输入

Seedance 2.0 新增支持调用联网搜索工具，通过配置 tools.**type** 参数为 web_search 即可开启联网搜索。

* 开启联网搜索后，模型会根据用户的提示词自主判断是否搜索互联网内容（如商品、天气等）。可提升生成视频的时效性，但也会增加一定的时延。
* 实际搜索次数可通过 [查询视频生成任务 API](https://www.volcengine.com/docs/82379/1521309?lang=zh) 返回的 usage.tool_usage.**web_search** 字段获取，如果为 0 表示未搜索。


<span aceTableMode="list" aceTableWidth="5,5"></span>
|输入：文本 |输出 |
|---|---|
|微距镜头对准叶片上翠绿的玻璃蛙。焦点逐渐从它光滑的皮肤，转移到它完全透明的腹部，一颗鲜红的心脏正在有力地、规律地收缩扩张。|<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/afad79fc76a34d1fbe7b2c809d1e19f1~tplv-goo7wpa0wc-image.image" controls></video>|\
|:::tip| |\
|联网搜索玻璃蛙的容貌特征。| |\
|| |\
|:::| |


```mixin-react
return (<Tabs>
<Tabs.TabPane title="Python" key="rdFGllcEyb"><RenderMd content={`\`\`\`Python
import os
import time  
# Install SDK:  pip install 'volcengine-python-sdk[ark]'
from volcenginesdkarkruntime import Ark 
# Make sure that you have stored the API Key in the environment variable ARK_API_KEY
# Initialize the Ark client to read your API Key from an environment variable
client = Ark(
    # This is the default path. You can configure it based on the service location
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    # Get API Key：https://console.volcengine.com/ark/region:ark+cn-beijing/apikey
    api_key=os.environ.get("ARK_API_KEY"),
)
if __name__ == "__main__":
    print("----- create request -----")
    create_result = client.content_generation.tasks.create(
        model="doubao-seedance-2-0-260128", # Replace with Model ID 
        content=[
            {
                # text prompt
                "type": "text",
                "text": "微距镜头对准叶片上翠绿的玻璃蛙。焦点逐渐从它光滑的皮肤，转移到它完全透明的腹部，一颗鲜红的心脏正在有力地、规律地收缩扩张。"
            }
        ],
        ratio="16:9",
        duration=11,
        watermark=False,
        tools=[{"type": "web_search"}],
    )
    print(create_result)
    
    # Polling query section
    print("----- polling task status -----")
    task_id = create_result.id
    while True:
        get_result = client.content_generation.tasks.get(task_id=task_id)
        status = get_result.status
        if status == "succeeded":
            print("----- task succeeded -----")
            print(get_result)
            break
        elif status == "failed":
            print("----- task failed -----")
            print(f"Error: {get_result.error}")
            break
        else:
            print(f"Current status: {status}, Retrying after 10 seconds...")
            time.sleep(10)
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Java" key="Lcj0NJmXQq"><RenderMd content={`\`\`\`Java
package com.ark.sample;

import com.volcengine.ark.runtime.model.content.generation.*;
import com.volcengine.ark.runtime.model.content.generation.CreateContentGenerationTaskRequest.Content;
import com.volcengine.ark.runtime.service.ArkService;
import okhttp3.ConnectionPool;
import okhttp3.Dispatcher;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.Collections;

public class ContentGenerationTaskExample {
    // Make sure that you have stored the API Key in the environment variable ARK_API_KEY
    // Initialize the Ark client to read your API Key from an environment variable
    static String apiKey = System.getenv("ARK_API_KEY");
    static ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);
    static Dispatcher dispatcher = new Dispatcher();
    static ArkService service = ArkService.builder()
           .baseUrl("https://ark.cn-beijing.volces.com/api/v3") // The base URL for model invocation
           .dispatcher(dispatcher)
           .connectionPool(connectionPool)
           .apiKey(apiKey)
           .build();
           
    public static void main(String[] args) {
        String model = "doubao-seedance-2-0-260128"; // Replace with Model ID
        String prompt = "微距镜头对准叶片上翠绿的玻璃蛙。焦点逐渐从它光滑的皮肤，转移到它完全透明的腹部，一颗鲜红的心脏正在有力地、规律地收缩扩张。";
        
        Boolean generateAudio = true;
        String videoRatio = "16:9";
        Long videoDuration = 11L;
        Boolean showWatermark = true;
        
        // Create ContentGenerationTool
        CreateContentGenerationTaskRequest.ContentGenerationTool webSearchTool = new CreateContentGenerationTaskRequest.ContentGenerationTool();
        webSearchTool.setType("web_search");
        
        System.out.println("----- create request -----");
        List<Content> contents = new ArrayList<>();
        
        // text prompt
        contents.add(Content.builder()
                .type("text")
                .text(prompt)
                .build());
         
        // Create a video generation task
        CreateContentGenerationTaskRequest createRequest = CreateContentGenerationTaskRequest.builder()
                .model(modelId)
                .content(contents)
                .generateAudio(generateAudio)
                .ratio(videoRatio)
                .duration(videoDuration)
                .watermark(showWatermark)
                .tools(Collections.singletonList(webSearchTool))
                .build();
        CreateContentGenerationTaskResult createResult = service.createContentGenerationTask(createRequest);
        System.out.println(createResult);
        // Get the details of the task
        String taskId = createResult.getId();
        GetContentGenerationTaskRequest getRequest = GetContentGenerationTaskRequest.builder()
                .taskId(taskId)
                .build();
        
        // Polling query section
        System.out.println("----- polling task status -----");
        while (true) {
            try {
                GetContentGenerationTaskResponse getResponse = service.getContentGenerationTask(getRequest);
                String status = getResponse.getStatus();
                if ("succeeded".equalsIgnoreCase(status)) {
                    System.out.println("----- task succeeded -----");
                    System.out.println(getResponse);
                    break;
                } else if ("failed".equalsIgnoreCase(status)) {
                    System.out.println("----- task failed -----");
                    System.out.println("Error: " + getResponse.getStatus());
                    break;
                } else {
                    System.out.printf("Current status: %s, Retrying in 10 seconds...", status);
                    TimeUnit.SECONDS.sleep(10);
                }
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                System.err.println("Polling interrupted");
                break;
            }
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Go" key="ArWzSbc51r"><RenderMd content={`\`\`\`Go
package main

import (
    "context"
    "fmt"
    "os"
    "time"

    "github.com/volcengine/volcengine-go-sdk/service/arkruntime"
    "github.com/volcengine/volcengine-go-sdk/service/arkruntime/model"
    "github.com/volcengine/volcengine-go-sdk/volcengine"
)

func main() {
    // Make sure that you have stored the API Key in the environment variable ARK_API_KEY
    // Initialize the Ark client to read your API Key from an environment variable
    client := arkruntime.NewClientWithApiKey(
        // Get your API Key from the environment variable. This is the default mode and you can modify it as required
        os.Getenv("ARK_API_KEY"),
        // The base URL for model invocation
        arkruntime.WithBaseUrl("https://ark.cn-beijing.volces.com/api/v3"),
    )
    ctx := context.Background()
    
    // Model ID
    modelID := "doubao-seedance-2-0-260128"
    // Text prompt
    prompt := "微距镜头对准叶片上翠绿的玻璃蛙。焦点逐渐从它光滑的皮肤，转移到它完全透明的腹部，一颗鲜红的心脏正在有力地、规律地收缩扩张。"

    // Output video parameters
    generateAudio := true
    videoRatio := "adaptive"
    videoDuration := int64(11)
    showWatermark := true

    // Create ContentGenerationTool
    tools := []*model.ContentGenerationTool{
        {Type: model.ToolTypeWebSearch},
    }

    // Generate a task
    fmt.Println("----- create request -----")
    createReq := model.CreateContentGenerationTaskRequest{
        Model:     modelID,
        GenerateAudio: volcengine.Bool(generateAudio),
        Ratio:     volcengine.String(videoRatio),
        Duration:  volcengine.Int64(videoDuration),
        Watermark: volcengine.Bool(showWatermark),
        Tools:     tools,
        Content: []*model.CreateContentGenerationContentItem{
            {
                // Combination of text prompt and parameters
                Type: model.ContentGenerationContentItemTypeText,
                Text: volcengine.String(prompt),
            },
        },
    }
    createResp, err := client.CreateContentGenerationTask(ctx, createReq)
    if err != nil {
        fmt.Printf("create content generation error: %v\\n", err)
        return
    }

    taskID := createResp.ID
    fmt.Printf("Task Created with ID: %s\\n", taskID)

    // 2. Poll task status
    pollTaskStatus(ctx, client, taskID)
}

    // poll task status
func pollTaskStatus(ctx context.Context, client *arkruntime.Client, taskID string) {
    fmt.Println("----- polling task status -----")
    for {
        getReq := model.GetContentGenerationTaskRequest{ID: taskID}
        getResp, err := client.GetContentGenerationTask(ctx, getReq)
        if err != nil {
            fmt.Printf("get content generation task error: %v\\n", err)
            return
        }

        status := getResp.Status
        if status == "succeeded" {
            fmt.Println("----- task succeeded -----")
            fmt.Printf("Task ID: %s \\n", getResp.ID)
            fmt.Printf("Model: %s \\n", getResp.Model)
            fmt.Printf("Video URL: %s \\n", getResp.Content.VideoURL)
            fmt.Printf("Completion Tokens: %d \\n", getResp.Usage.CompletionTokens)
            fmt.Printf("Created At: %d, Updated At: %d\\n", getResp.CreatedAt, getResp.UpdatedAt)
            return
        } else if status == "failed" {
            fmt.Println("----- task failed -----")
            if getResp.Error != nil {
                fmt.Printf("Error Code: %s, Message: %s\\n", getResp.Error.Code, getResp.Error.Message)
            }
            return
        } else {
            fmt.Printf("Current status: %s, Retrying in 10 seconds... \\n", status)
            time.Sleep(10 * time.Second)
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane></Tabs>);
```

<span id="17c64b2e"></span>
## 更多能力
Seedance 2.0 系列模型也支持文生视频、首帧图生视频、首尾帧图生视频、设置视频输出规格等通用基础能力，详情请参见 [视频生成（Ark for TT）](/docs/82379/1366799)。
<span id="5c67c9a1"></span>
# 便利创作
Seedance 2.0 系列模型不支持直接上传含有真人人脸的参考图/视频。为便利创作者使用肖像，平台推出了以下解决方案。

<span aceTableMode="list" aceTableWidth="2,4"></span>
|方案 |介绍 |
|---|---|
|[使用虚拟人像](/docs/82379/2291680#2bf01416) |平台预置虚拟人像库，为创作者提供免费、合规、丰富多样的肖像素材。适用于需真人风格人脸但无需指定具体人物，追求零合规风险、快速创作的场景。 |
|[使用模型产物进行二创](/docs/82379/2291680#86c3831f) |本账号下 Seedance 2.0/2.0 fast 生成的视频可直接用于编辑 / 延长，人脸正常参与生成，不触发审核拦截。 |

<span id="2bf01416"></span>
## 使用虚拟人像
对写实风格视频，可通过虚拟人像库预置人像来控制角色样貌。每个素材对应一个独立素材 ID (asset ID)， 在 **content.<模态\>_url.url** 字段中传入 `asset://<asset ID>` 即可生成视频。浏览及检索虚拟人像请参见[虚拟人像库](/docs/82379/2223965)。

<span aceTableMode="list" aceTableWidth="3,3,4"></span>
|输入：文本 |输入：虚拟人像、图片 |输出 |
|---|---|---|
|固定机位，近景镜头，清新自然风格。在室内自然光下，**图片1**中美妆博主面带笑容，向镜头介绍**图片2**中的面霜。博主将手里的面霜展示给镜头，开心地说“挖到本命面霜了！”；接着她一边用手指轻轻蘸取面霜展示那种软糯感，一边说“质地像云朵一样软糯，一抹就吸收”；最后她把面霜涂抹在脸颊上，展示着水润透亮的皮肤，同时自信地说“熬夜急救、补水保湿全搞定”。要求画面中人物居中，完整展示人物的整个脑袋和上半身，始终对焦人脸，人脸始终清晰，纯净无任何字幕。|<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/946509d1f37f476c9ff29e0adaf187eb~tplv-goo7wpa0wc-image.image =200x) </span>|<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/0bd96f702bdf48bab1a9505710d9e1f9~tplv-goo7wpa0wc-image.image" controls></video>|\
|:::warning|> 虚拟人像| |\
|\*\*Asset ID 仅用来向模型传入素材，不支持使用 Asset ID 指代素材。\*\*提示词中仍需使用"**素材类型+序号**”格式引用素材，序号为请求体中该素材在同类素材中的排序。|| |\
|正确用法：**图片1**中美妆博主|<span>![图片](https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/791b783fc6cd4394b13f41b66b5ff461~tplv-goo7wpa0wc-image.image =200x) </span>| |\
|错误用法：asset\-2026\*\*\*\*是美妆博主|> 产品图像 | |\
|| | |\
|:::| | |


```mixin-react
return (<Tabs>
<Tabs.TabPane title="Python" key="Q1hUr39le2"><RenderMd content={`\`\`\`Python
import os
import time
# Install SDK:  pip install 'volcengine-python-sdk[ark]'
from volcenginesdkarkruntime import Ark 

client = Ark(
    # The base URL for model invocation
    base_url='https://ark.cn-beijing.volces.com/api/v3',
    # Get API Key：https://console.volcengine.com/ark/region:ark+cn-beijing/apikey
    api_key=os.environ.get("ARK_API_KEY"),
)

if __name__ == "__main__":
    print("----- create request -----")
    create_result = client.content_generation.tasks.create(
        model="doubao-seedance-2-0-260128", # Replace with Model ID 
        content=[
            {
                "type": "text",
                "text": "固定机位，近景镜头，清新自然风格。在室内自然光下，图片1中美妆博主面带笑容，向镜头介绍图片2中的面霜。博主将手里的面霜展示给镜头，开心地说“挖到本命面霜了！”；接着她一边用手指轻轻蘸取面霜展示那种软糯感，一边说“质地像云朵一样软糯，一抹就吸收”；最后她把面霜涂抹在脸颊上，展示着水润透亮的皮肤，同时自信地说“熬夜急救、补水保湿全搞定”。要求画面中人物居中，完整展示人物的整个脑袋和上半身，始终对焦人脸，人脸始终清晰，纯净无任何字幕。"
            },        
            {
                "type": "image_url",
                "image_url": {
                    "url": "asset://asset-20260401123823-6d4x2"
                },
                "role": "reference_image"
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_edit_pic1.jpg"
                },
                "role": "reference_image"
            },
        ],
        generate_audio=True,
        ratio="adaptive",
        duration=11,
        watermark=True,
    )
    print(create_result)

    print("----- polling task status -----")
    task_id = create_result.id
    while True:
        get_result = client.content_generation.tasks.get(task_id=task_id)
        status = get_result.status
        if status == "succeeded":
            print("----- task succeeded -----")
            print(get_result)
            break
        elif status == "failed":
            print("----- task failed -----")
            print(f"Error: {get_result.error}")
            break
        else:
            print(f"Current status: {status}, Retrying after 30 seconds...")
            time.sleep(30)
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Java" key="mRjJrtFyjT"><RenderMd content={`\`\`\`Java
package com.ark.sample;

import com.volcengine.ark.runtime.model.content.generation.*;
import com.volcengine.ark.runtime.model.content.generation.CreateContentGenerationTaskRequest.Content;
import com.volcengine.ark.runtime.service.ArkService;
import okhttp3.ConnectionPool;
import okhttp3.Dispatcher;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

public class ContentGenerationTaskExample {

    // Client initialization
    static String apiKey = System.getenv("ARK_API_KEY");
    static ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);
    static Dispatcher dispatcher = new Dispatcher();
    static ArkService service = ArkService.builder()
           .baseUrl("https://ark.cn-beijing.volces.com/api/v3") // The base URL for model invocation
           .dispatcher(dispatcher)
           .connectionPool(connectionPool)
           .apiKey(apiKey)
           .build();
           
    public static void main(String[] args) {
        
        // Model ID
        final String modelId = "doubao-seedance-2-0-260128";
        // Text prompt
        final String prompt = "固定机位，近景镜头，清新自然风格。在室内自然光下，图片1中美妆博主面带笑容，向镜头介绍图片2中的面霜。博主将手里的面霜展示给镜头，开心地说“挖到本命面霜了！”；接着她一边用手指轻轻蘸取面霜展示那种软糯感，一边说“质地像云朵一样软糯，一抹就吸收”；最后她把面霜涂抹在脸颊上，展示着水润透亮的皮肤，同时自信地说“熬夜急救、补水保湿全搞定”。要求画面中人物居中，完整展示人物的整个脑袋和上半身，始终对焦人脸，人脸始终清晰，纯净无任何字幕。";
        
        // Example resource URLs
        final String refImage1 = "asset://asset-20260401123823-6d4x2";
        final String refImage2 = "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_edit_pic1.jpg";

        // Output video parameters
        final boolean generateAudio = true;
        final String videoRatio = "adaptive";      
        final long videoDuration = 11L;          
        final boolean showWatermark = true;

        System.out.println("----- create request -----");
        // Build request content
        List<Content> contents = new ArrayList<>();
        
        // 1. Text prompt
        contents.add(Content.builder()
                .type("text")
                .text(prompt)
                .build());
                
        // 2. Reference image 1
        contents.add(Content.builder()
                .type("image_url")
                .imageUrl(CreateContentGenerationTaskRequest.ImageUrl.builder()
                        .url(refImage1)
                        .build())
                .role("reference_image")
                .build());

        // 3. Reference image 2
        contents.add(Content.builder()
                .type("image_url")
                .imageUrl(CreateContentGenerationTaskRequest.ImageUrl.builder()
                        .url(refImage2)
                        .build())
                .role("reference_image")
                .build());

        // Create video generation task
        CreateContentGenerationTaskRequest createRequest = CreateContentGenerationTaskRequest.builder()
                .generateAudio(generateAudio)
                .model(modelId)
                .content(contents)
                .ratio(videoRatio)
                .duration(videoDuration)
                .watermark(showWatermark)
                .build();

        CreateContentGenerationTaskResult createResult = service.createContentGenerationTask(createRequest);
        System.out.println("Task Created: " + createResult);

        // Get task details and poll status
        String taskId = createResult.getId();
        pollTaskStatus(taskId);
    }

    /**
     * Poll task status
     * @param taskId Task ID
     */

    private static void pollTaskStatus(String taskId) {
        GetContentGenerationTaskRequest getRequest = GetContentGenerationTaskRequest.builder()
                .taskId(taskId)
                .build();

        System.out.println("----- polling task status -----");
        try {
            while (true) {
                GetContentGenerationTaskResponse getResponse = service.getContentGenerationTask(getRequest);
                String status = getResponse.getStatus();

                if ("succeeded".equalsIgnoreCase(status)) {
                    System.out.println("----- task succeeded -----");
                    System.out.println(getResponse);
                    break;
                } else if ("failed".equalsIgnoreCase(status)) {
                    System.out.println("----- task failed -----");
                    if (getResponse.getError() != null) {
                        System.out.println("Error: " + getResponse.getError().getMessage());
                    }
                    break;
                } else {
                    System.out.printf("Current status: %s, Retrying in 10 seconds...%n", status);
                    TimeUnit.SECONDS.sleep(10);
                }
            }
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            System.err.println("Polling interrupted");
        } catch (Exception e) {
            System.err.println("Error occurred: " + e.getMessage());
        } finally {
            service.shutdownExecutor();
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Go" key="EMim4C1KLm"><RenderMd content={`\`\`\`Go
package main

import (
    "context"
    "fmt"
    "os"
    "time"

    "github.com/volcengine/volcengine-go-sdk/service/arkruntime"
    "github.com/volcengine/volcengine-go-sdk/service/arkruntime/model"
    "github.com/volcengine/volcengine-go-sdk/volcengine"
)

func main() {
    // Initialize Ark client
    client := arkruntime.NewClientWithApiKey(
        os.Getenv("ARK_API_KEY"),
        // The base URL for model invocation
        arkruntime.WithBaseUrl("https://ark.cn-beijing.volces.com/api/v3"),
    )
    ctx := context.Background()

    // Model ID
    modelID := "doubao-seedance-2-0-260128"
    // Text prompt
    prompt := "固定机位，近景镜头，清新自然风格。在室内自然光下，图片1中美妆博主面带笑容，向镜头介绍图片2中的面霜。博主将手里的面霜展示给镜头，开心地说“挖到本命面霜了！”；接着她一边用手指轻轻蘸取面霜展示那种软糯感，一边说“质地像云朵一样软糯，一抹就吸收”；最后她把面霜涂抹在脸颊上，展示着水润透亮的皮肤，同时自信地说“熬夜急救、补水保湿全搞定”。要求画面中人物居中，完整展示人物的整个脑袋和上半身，始终对焦人脸，人脸始终清晰，纯净无任何字幕。"

    // Example resource URLs
    refImage1 := "asset://asset-20260401123823-6d4x2"
    refImage2 := "https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_edit_pic1.jpg"

    // Output video parameters
    generateAudio := true
    videoRatio := "adaptive"
    videoDuration := int64(11)
    showWatermark := true

    // 1. Create video generation task
    fmt.Println("----- create request -----")
    createReq := model.CreateContentGenerationTaskRequest{
        Model:         modelID,
        GenerateAudio: volcengine.Bool(generateAudio),
        Ratio:         volcengine.String(videoRatio),
        Duration:      volcengine.Int64(videoDuration),
        Watermark:     volcengine.Bool(showWatermark),
        Content: []*model.CreateContentGenerationContentItem{
            {
                Type: model.ContentGenerationContentItemTypeText,
                Text: volcengine.String(prompt),
            },
            {
                Type: model.ContentGenerationContentItemType("image_url"),
                ImageURL: &model.ImageURL{
                    URL: refImage1,
                },
                Role: volcengine.String("reference_image"),
            },
            {
                Type: model.ContentGenerationContentItemType("image_url"),
                ImageURL: &model.ImageURL{
                    URL: refImage2,
                },
                Role: volcengine.String("reference_image"),
            },
        },
    }

    createResp, err := client.CreateContentGenerationTask(ctx, createReq)
    if err != nil {
        fmt.Printf("create content generation error: %v\\n", err)
        return
    }

    taskID := createResp.ID
    fmt.Printf("Task Created with ID: %s\\n", taskID)

    // 2. Poll task status
    pollTaskStatus(ctx, client, taskID)
}

// poll task status
func pollTaskStatus(ctx context.Context, client *arkruntime.Client, taskID string) {
    fmt.Println("----- polling task status -----")
    for {
        getReq := model.GetContentGenerationTaskRequest{ID: taskID}
        getResp, err := client.GetContentGenerationTask(ctx, getReq)
        if err != nil {
            fmt.Printf("get content generation task error: %v\\n", err)
            return
        }

        status := getResp.Status
        if status == "succeeded" {
            fmt.Println("----- task succeeded -----")
            fmt.Printf("Task ID: %s \\n", getResp.ID)
            fmt.Printf("Model: %s \\n", getResp.Model)
            fmt.Printf("Video URL: %s \\n", getResp.Content.VideoURL)
            fmt.Printf("Completion Tokens: %d \\n", getResp.Usage.CompletionTokens)
            fmt.Printf("Created At: %d, Updated At: %d\\n", getResp.CreatedAt, getResp.UpdatedAt)
            return
        } else if status == "failed" {
            fmt.Println("----- task failed -----")
            if getResp.Error != nil {
                fmt.Printf("Error Code: %s, Message: %s\\n", getResp.Error.Code, getResp.Error.Message)
            }
            return
        } else {
            fmt.Printf("Current status: %s, Retrying in 10 seconds... \\n", status)
            time.Sleep(10 * time.Second)
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane></Tabs>);
```

&nbsp;
<span id="86c3831f"></span>
## 使用模型产物进行二创
Seedance 2.0 及 2.0 fast 模型生成的视频为受信素材。您可使用**本账号下**由上述模型生成的视频，进行视频编辑、视频延长等二次创作，素材中的人脸可正常参与生成，不会触发审核拦截。
:::tip
2026年3月11日起，使用 Seedance 2.0 及 2.0 fast 模型生成的视频，支持二次创作。

:::
<span aceTableMode="list" aceTableWidth="1,2"></span>
|第一次输出视频 |二次编辑后视频 |
|---|---|
|<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/24e27818aeb644b6942c2cbc949ddc86~tplv-goo7wpa0wc-image.image" controls></video>|<video src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/44d52b9f0768460c8c86b81d2df40350~tplv-goo7wpa0wc-image.image" controls></video>|\
|||\
|> 输入：固定机位，近景镜头，清新自然风格。在室内自然光下，**图片1**中美妆博主面带笑容，向镜头介绍**图片2**中的面霜。博主将手里的面霜展示给镜头，开心地说“挖到本命面霜了！”；接着她一边用手指轻轻蘸取面霜展示那种软糯感，一边说“质地像云朵一样软糯，一抹就吸收”；最后她把面霜涂抹在脸颊上，展示着水润透亮的皮肤，同时自信地说“熬夜急救、补水保湿全搞定”。要求画面中人物居中，完整展示人物的整个脑袋和上半身，始终对焦人脸，人脸始终清晰，纯净无任何字幕。 |> 输入：将面霜的颜色修改为白色。|\
| |> ratio 修改为16:9 |


```mixin-react
return (<Tabs>
<Tabs.TabPane title="Python" key="Rk19QG2A2l"><RenderMd content={`1. 首次生视频，并获取视频 URL。此处直接用[使用虚拟人像](/docs/82379/2277377#2bf01416)的例子。
2. 对 Seedance 2.0 生成的视频进行再次编辑。视频原始 URL 的有效期仅 24 小时，本示例将原始视频转存至 TOS 使用。

:::tip
视频原始 URL 的有效期仅 24 小时，实际使用时，建议您提前转存视频文件。推荐配置火山引擎 TOS 提供的数据订阅功能，将您的视频产物自动转存到自己的 TOS 桶中，便于长期备份或二次加工。详细介绍请参见 [TOS 数据订阅](https://www.volcengine.com/docs/6349/2280949?lang=zh)。
:::
\`\`\`Python
import os
import time
# Install SDK:  pip install 'volcengine-python-sdk[ark]'
from volcenginesdkarkruntime import Ark 

client = Ark(
    # The base URL for model invocation
    base_url='https://ark.cn-beijing.volces.com/api/v3',
    # Get API Key：https://console.volcengine.com/ark/region:ark+cn-beijing/apikey
    api_key=os.environ.get("ARK_API_KEY"),
)

if __name__ == "__main__":
    print("----- create request -----")
    create_result = client.content_generation.tasks.create(
        model="doubao-seedance-2-0-260128", # Replace with Model ID 
        content=[
            {
                "type": "text",
                "text": "将面霜的颜色修改为白色。"
            },                
            {
                "type": "video_url",
                "video_url": {
                    "url": "https://ark-project.tos-cn-beijing.volces.com/doc_video/video_by_sd2.mp4"
                },
                "role": "reference_video"
            },
        ],
        generate_audio=True,
        ratio="16:9",
        duration=11,
        watermark=True,
    )
    print(create_result)
    print("----- polling task status -----")
    task_id = create_result.id
    while True:
        get_result = client.content_generation.tasks.get(task_id=task_id)
        status = get_result.status
        if status == "succeeded":
            print("----- task succeeded -----")
            print(get_result)
            break
        elif status == "failed":
            print("----- task failed -----")
            print(f"Error: {get_result.error}")
            break
        else:
            print(f"Current status: {status}, Retrying after 30 seconds...")
            time.sleep(30)
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Java" key="uqDEtb77VD"><RenderMd content={`1. 首次生视频，并获取视频 URL。此处直接用[使用虚拟人像](/docs/82379/2277377#2bf01416)的例子。
2. 对 Seedance 2.0 生成的视频进行再次编辑。视频原始 URL 的有效期仅 24 小时，本示例将原始视频转存至 TOS 使用。

:::tip
视频原始 URL 的有效期仅 24 小时，实际使用时，建议您提前转存视频文件。推荐配置火山引擎 TOS 提供的数据订阅功能，将您的视频产物自动转存到自己的 TOS 桶中，便于长期备份或二次加工。详细介绍请参见 [TOS 数据订阅](https://www.volcengine.com/docs/6349/2280949?lang=zh)。
:::
\`\`\`Java
package com.ark.sample;

import com.volcengine.ark.runtime.model.content.generation.*;
import com.volcengine.ark.runtime.model.content.generation.CreateContentGenerationTaskRequest.Content;
import com.volcengine.ark.runtime.service.ArkService;
import okhttp3.ConnectionPool;
import okhttp3.Dispatcher;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

public class ContentGenerationTaskExample {

    // Client initialization
    static String apiKey = System.getenv("ARK_API_KEY");
    static ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);
    static Dispatcher dispatcher = new Dispatcher();
    static ArkService service = ArkService.builder()
           .baseUrl("https://ark.cn-beijing.volces.com/api/v3") // The base URL for model invocation
           .dispatcher(dispatcher)
           .connectionPool(connectionPool)
           .apiKey(apiKey)
           .build();
           
    public static void main(String[] args) {
        
        // Model ID
        final String modelId = "doubao-seedance-2-0-260128";
        // Text prompt
        final String prompt = "将面霜的颜色修改为白色。";
        
        // Example resource URLs
        final String refVideo = "https://ark-project.tos-cn-beijing.volces.com/doc_video/video_by_sd2.mp4";

        // Output video parameters
        final boolean generateAudio = true;
        final String videoRatio = "16:9";      
        final long videoDuration = 11L;          
        final boolean showWatermark = true;

        System.out.println("----- create request -----");
        // Build request content
        List<Content> contents = new ArrayList<>();
        
        // 1. Text prompt
        contents.add(Content.builder()
                .type("text")
                .text(prompt)
                .build());
                
        // 2. Reference video
        contents.add(Content.builder()
                .type("video_url")
                .videoUrl(CreateContentGenerationTaskRequest.VideoUrl.builder()
                        .url(refVideo)
                        .build())
                .role("reference_video")
                .build());

        // Create video generation task
        CreateContentGenerationTaskRequest createRequest = CreateContentGenerationTaskRequest.builder()
                .generateAudio(generateAudio)
                .model(modelId)
                .content(contents)
                .ratio(videoRatio)
                .duration(videoDuration)
                .watermark(showWatermark)
                .build();

        CreateContentGenerationTaskResult createResult = service.createContentGenerationTask(createRequest);
        System.out.println("Task Created: " + createResult);

        // Get task details and poll status
        String taskId = createResult.getId();
        pollTaskStatus(taskId);
    }

    /**
     * Poll task status
     * @param taskId Task ID
     */

    private static void pollTaskStatus(String taskId) {
        GetContentGenerationTaskRequest getRequest = GetContentGenerationTaskRequest.builder()
                .taskId(taskId)
                .build();

        System.out.println("----- polling task status -----");
        try {
            while (true) {
                GetContentGenerationTaskResponse getResponse = service.getContentGenerationTask(getRequest);
                String status = getResponse.getStatus();

                if ("succeeded".equalsIgnoreCase(status)) {
                    System.out.println("----- task succeeded -----");
                    System.out.println(getResponse);
                    break;
                } else if ("failed".equalsIgnoreCase(status)) {
                    System.out.println("----- task failed -----");
                    if (getResponse.getError() != null) {
                        System.out.println("Error: " + getResponse.getError().getMessage());
                    }
                    break;
                } else {
                    System.out.printf("Current status: %s, Retrying in 10 seconds...%n", status);
                    TimeUnit.SECONDS.sleep(10);
                }
            }
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            System.err.println("Polling interrupted");
        } catch (Exception e) {
            System.err.println("Error occurred: " + e.getMessage());
        } finally {
            service.shutdownExecutor();
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane>
<Tabs.TabPane title="Go" key="pFrtZIwwbT"><RenderMd content={`1. 首次生视频，并获取视频 URL。此处直接用[使用虚拟人像](/docs/82379/2277377#2bf01416)的例子。
2. 对 Seedance 2.0 生成的视频进行再次编辑。视频原始 URL 的有效期仅 24 小时，本示例将原始视频转存至 TOS 使用。

:::tip
视频原始 URL 的有效期仅 24 小时，实际使用时，建议您提前转存视频文件。推荐配置火山引擎 TOS 提供的数据订阅功能，将您的视频产物自动转存到自己的 TOS 桶中，便于长期备份或二次加工。详细介绍请参见 [TOS 数据订阅](https://www.volcengine.com/docs/6349/2280949?lang=zh)。
:::
\`\`\`Go
package main

import (
    "context"
    "fmt"
    "os"
    "time"

    "github.com/volcengine/volcengine-go-sdk/service/arkruntime"
    "github.com/volcengine/volcengine-go-sdk/service/arkruntime/model"
    "github.com/volcengine/volcengine-go-sdk/volcengine"
)

func main() {
    // Initialize Ark client
    client := arkruntime.NewClientWithApiKey(
        os.Getenv("ARK_API_KEY"),
        // The base URL for model invocation
        arkruntime.WithBaseUrl("https://ark.cn-beijing.volces.com/api/v3"),
    )
    ctx := context.Background()

    // Model ID
    modelID := "doubao-seedance-2-0-260128"
    // Text prompt
    prompt := "将面霜的颜色修改为白色。"

    // Example resource URLs
    refVideo1 := "https://ark-project.tos-cn-beijing.volces.com/doc_video/video_by_sd2.mp4"

    // Output video parameters
    generateAudio := true
    videoRatio := "16:9"
    videoDuration := int64(11)
    showWatermark := true

    // 1. Create video generation task
    fmt.Println("----- create request -----")
    createReq := model.CreateContentGenerationTaskRequest{
        Model:         modelID,
        GenerateAudio: volcengine.Bool(generateAudio),
        Ratio:         volcengine.String(videoRatio),
        Duration:      volcengine.Int64(videoDuration),
        Watermark:     volcengine.Bool(showWatermark),
        Content: []*model.CreateContentGenerationContentItem{
            {
                Type: model.ContentGenerationContentItemTypeText,
                Text: volcengine.String(prompt),
            },
            {
                Type: model.ContentGenerationContentItemType("video_url"),
                VideoURL: &model.VideoUrl{
                    Url: refVideo1,
                },
                Role: volcengine.String("reference_video"),
            },
        },
    }

    createResp, err := client.CreateContentGenerationTask(ctx, createReq)
    if err != nil {
        fmt.Printf("create content generation error: %v\\n", err)
        return
    }

    taskID := createResp.ID
    fmt.Printf("Task Created with ID: %s\\n", taskID)

    // 2. Poll task status
    pollTaskStatus(ctx, client, taskID)
}

// poll task status
func pollTaskStatus(ctx context.Context, client *arkruntime.Client, taskID string) {
    fmt.Println("----- polling task status -----")
    for {
        getReq := model.GetContentGenerationTaskRequest{ID: taskID}
        getResp, err := client.GetContentGenerationTask(ctx, getReq)
        if err != nil {
            fmt.Printf("get content generation task error: %v\\n", err)
            return
        }

        status := getResp.Status
        if status == "succeeded" {
            fmt.Println("----- task succeeded -----")
            fmt.Printf("Task ID: %s \\n", getResp.ID)
            fmt.Printf("Model: %s \\n", getResp.Model)
            fmt.Printf("Video URL: %s \\n", getResp.Content.VideoURL)
            fmt.Printf("Completion Tokens: %d \\n", getResp.Usage.CompletionTokens)
            fmt.Printf("Created At: %d, Updated At: %d\\n", getResp.CreatedAt, getResp.UpdatedAt)
            return
        } else if status == "failed" {
            fmt.Println("----- task failed -----")
            if getResp.Error != nil {
                fmt.Printf("Error Code: %s, Message: %s\\n", getResp.Error.Code, getResp.Error.Message)
            }
            return
        } else {
            fmt.Printf("Current status: %s, Retrying in 10 seconds... \\n", status)
            time.Sleep(10 * time.Second)
        }
    }
}
\`\`\`

`}></RenderMd></Tabs.TabPane></Tabs>);
```

<span id="7f69bcbf"></span>
# 提示词技巧
提示词中必须使用"**素材类型+序号**”格式引用素材，序号为请求体中该素材在同类素材中的排序。例如 「图片 n」指代`content`数组中第 n 个`type="image_url"`的参考图片（按数组顺序从1开始计数）。**注意不支持使用 Asset ID 指代素材。** 
下文介绍多模态参考、编辑视频、延长视频的提示词典型公式，更多详细内容请参见[Seedance 2.0 系列提示词指南](/docs/82379/2222480)。
:::tip
平台提供 **Seedance 2.0 提示词优化技能**，方便您对提示词进行调优。

* 配置方式：可将技能文件配置到 Code Agent / AI Agent 中使用。以 OpenClaw 为例，下载该 SKILL.md 文件，复制完整内容至对话输入框中，并发送”请帮我安装这个技能”，等待工具自动完成安装。
* 使用方式：在 AI 对话框输入 `/sd2-pe + 你的提示词内容`，开始调试提示词。

<Attachment link="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/3ae6b89184254218a83b9e65ac4a6422~tplv-goo7wpa0wc-image.image" name="SKILL.md"></Attachment>
:::
**多模态参考**

* 图片参考：参考 / 提取 / 结合 +「图片 n」中的「主体 / 被参考元素描述」，生成「画面描述」，保持「主体 / 被参考元素描述」特征一致。
* 视频参考：参考「视频 n」的「动作描述 / 运镜描述 / 特效描述」，生成「画面描述」，保持动作细节 / 运镜 / 特效一致。
* 音频参考：
   * 音色参考：「角色」说：“「台词」，音色参考「音频 n」。
   * 音频内容参考：理想出现时机 +「音频 n」。

**编辑视频**

* 增加元素：清晰描述「元素特征」+「出现时机」+「出现位置」
* 删除元素：点明需要删除的元素，对于保持不变的元素，在提示词中加以强调，表现更佳
* 修改元素：清晰描述更换元素即可

**延长视频**

* 延长视频：向前/向后延长「视频n」+「需延长的视频描述」
* 轨道补全：「视频1」+「过渡画面描述」+接「视频2」+「过渡画面描述」+接「视频3」

<span id="66cb028f"></span>
# 使用限制
参见[使用限制](/docs/82379/1366799#66cb028f)。



