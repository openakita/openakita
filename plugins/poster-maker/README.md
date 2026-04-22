# AI 海报生成 / Poster Maker

零设计基础也能用：选模板 → 填字 → 上传配图 → 出 PNG。

- 全本地渲染（Pillow），无外部依赖
- 支持中英文自动换行 / 字体自适应
- 可选 AI 背景润色（复用 `image-edit` 的 provider）

## 测试

```bash
pytest plugins/poster-maker/tests
```
