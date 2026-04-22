# AI 配音员 / Avatar Speaker

文字 → 自然好听的配音。零配置即用 (Edge TTS 免费)。

## 给小白用户

1. 写一段文字
2. 选一个声音 (默认晓晓)
3. 点【生成配音】
4. 立即在浏览器里播放 / 下载 mp3

## 引擎

- **Edge TTS** (默认, 免费)
- **CosyVoice** (DashScope, 付费, 支持声音克隆)
- **OpenAI TTS** (付费, 6 voice)
- **Stub** (零音频, 开发/演示用)

## 数字人形象

骨架已预留 (`providers.DigitalHumanAvatar`), 实际合成在 P3 实现 (HeyGen / SadTalker / D-ID 集成)。

## 测试

```bash
pytest plugins/avatar-speaker/tests
```
