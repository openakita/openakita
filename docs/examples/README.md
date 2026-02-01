# API 转换示例

本目录包含 OpenAI 和 Anthropic API 格式转换的示例代码。

## 文件说明

- `api_converter_example.py`: 完整的转换示例，演示了各种格式转换场景

## 运行示例

```bash
python docs/examples/api_converter_example.py
```

## 示例内容

1. **基本消息格式转换**: 演示如何转换基本的消息格式（包括 system prompt）
2. **工具定义转换**: 演示如何转换工具定义格式
3. **多模态消息转换**: 演示如何转换包含图片的消息
4. **工具调用响应转换**: 演示如何转换工具调用的响应格式

## 相关文档

详细的 API 差异对比文档请参考: [api-comparison-openai-anthropic.md](../api-comparison-openai-anthropic.md)
