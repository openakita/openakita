---
name: openakita/skills@baidu-famou
description: "Baidu FaMou algorithm skills for efficient algorithm self-evolution. Provides experiment management and visualization capabilities to help optimize complex algorithms. Use when user needs algorithm optimization or experiment management."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# 百度伏谋 (FaMou)

轻松高效调用伏谋算法自演化能力，提供实验管理和可视化能力，帮助用户极致调优完成复杂的算法实验。

## 功能

- 算法自演化
- 实验管理
- 可视化分析
- 参数调优

## 预置脚本

### scripts/famou.py
算法实验管理（百度千帆 AppBuilder），需设置 APPBUILDER_TOKEN。

```bash
python3 scripts/famou.py experiment "图像分类模型调优"
python3 scripts/famou.py optimize "超参数搜索"
```
