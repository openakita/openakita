# 系统指令手册

本文档记录 OpenAkita 的系统级命令。这些命令由系统直接处理，不经过大模型，确保在任何情况下都能执行。

---

## 模型切换命令

用于管理和切换 LLM 模型。即使当前模型不可用，也能通过这些命令切换到其他模型。

### `/model`

显示当前模型状态和所有可用模型列表。

**示例输出**:
```
📋 模型状态

1. ✅ minimax (MiniMax-M2.1) ⬅️ 当前
2. ✅ dashscope (qwen3-max)
3. ✅ kimi (kimi-k2.5)
4. ✅ claude-primary (claude-opus)

💡 命令: /switch 切换 | /priority 调整优先级 | /restore 恢复默认
```

---

### `/switch`

进入交互式模型切换模式（临时切换，12小时有效）。

**流程**:
1. 系统显示可用模型列表
2. 用户输入数字或模型名称选择
3. 系统要求确认
4. 用户输入 `yes` 确认切换

**示例**:
```
用户: /switch
系统: 📋 可用模型

      1. ✅ minimax (MiniMax-M2.1) ⬅️ 当前
      2. ✅ dashscope (qwen3-max)
      3. ✅ kimi (kimi-k2.5)
      4. ✅ claude-primary (claude-opus)

      请输入数字或模型名称选择，/cancel 取消

用户: 4
系统: ⚠️ 确认切换到 claude-primary (claude-opus)?

      临时切换有效期: 12小时
      输入 yes 确认，其他任意内容取消

用户: yes
系统: ✅ 已切换到模型: claude-opus
      有效期至: 2026-02-02 15:45:00

      发送 /model 查看状态
```

---

### `/switch <模型名>`

直接切换到指定模型（仍需确认）。

**示例**:
```
用户: /switch claude
系统: ⚠️ 确认切换到 claude-primary (claude-opus)?

      临时切换有效期: 12小时
      输入 yes 确认，其他任意内容取消

用户: yes
系统: ✅ 已切换到模型: claude-opus
```

---

### `/priority`

进入交互式优先级调整模式（永久生效）。

调整模型的优先级顺序，优先级高的模型会优先使用。

**流程**:
1. 系统显示当前优先级
2. 用户按顺序输入所有模型名称
3. 系统要求确认
4. 用户输入 `yes` 确认

**示例**:
```
用户: /priority
系统: 📋 当前优先级 (数字越小越优先)

      0. minimax
      1. dashscope
      2. kimi
      3. claude-primary

      请按顺序输入模型名称，用空格分隔
      例如: claude kimi dashscope minimax
      /cancel 取消

用户: claude kimi dashscope minimax
系统: ⚠️ 确认调整优先级为:

      0. claude-primary
      1. kimi
      2. dashscope
      3. minimax

      这是永久更改！输入 yes 确认

用户: yes
系统: ✅ 优先级已更新并保存: claude-primary > kimi > dashscope > minimax
```

---

### `/restore`

恢复到默认模型（清除临时切换）。

**示例**:
```
用户: /restore
系统: ⚠️ 确认恢复默认模型?

      当前临时使用: claude-primary
      剩余时间: 10.5 小时

      输入 yes 确认，其他任意内容取消

用户: yes
系统: ✅ 已恢复默认模型: minimax
```

---

### `/cancel`

取消当前进行中的操作，退出交互模式。

在任何交互过程中都可以使用此命令退出。

**示例**:
```
用户: /switch
系统: 📋 可用模型...

用户: /cancel
系统: ✅ 操作已取消
```

---

## 交互超时

所有交互操作（切换、优先级调整）有 **5分钟** 超时限制。超时后会自动取消操作。

```
系统: ⏰ 操作超时（5分钟），已自动取消
```

---

## 命令速查表

| 命令 | 说明 | 生效范围 |
|------|------|----------|
| `/model` | 查看当前模型和可用列表 | - |
| `/switch` | 交互式临时切换 | 12小时 |
| `/switch <名称>` | 直接临时切换 | 12小时 |
| `/priority` | 交互式优先级调整 | 永久 |
| `/restore` | 恢复默认模型 | - |
| `/cancel` | 取消当前操作 | - |

---

## 注意事项

1. **系统级命令**: 这些命令不经过大模型处理，即使模型崩溃也能执行
2. **确认机制**: 所有切换操作都需要二次确认，防止误操作
3. **临时切换**: `/switch` 的切换是临时的，12小时后自动恢复
4. **永久更改**: `/priority` 会修改配置文件，永久生效
5. **大小写不敏感**: 命令支持大小写混用，如 `/MODEL`、`/Switch` 都有效

