# AI 改图 / Image Edit

用一句话改一张图。涂出要改的部分，AI 帮你重绘。

## 给小白用户

1. 上传图片
2. 用鼠标涂出要改的部分（可选）
3. 写一句话描述想改成什么
4. 点【立即改图】

## 引擎

- **OpenAI gpt-image-1**（首选，质量最稳）—— 配置 `OPENAI_API_KEY`
- **通义万相**（国内备用，便宜）—— 配置 `DASHSCOPE_API_KEY`
- **Stub** —— 没配 key 也能跑通流程（不会真改图）

## 测试

```bash
pytest plugins/image-edit/tests
```
