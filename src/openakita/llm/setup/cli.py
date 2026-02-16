"""
CLI 配置向导

交互式命令行工具，用于配置 LLM 端点。
"""

import asyncio
import os

from ..capabilities import infer_capabilities
from ..config import load_endpoints_config, save_endpoints_config
from ..registries import ProviderInfo, get_registry, list_providers
from ..types import EndpointConfig


def run_cli_wizard():
    """运行 CLI 配置向导"""
    print("\n[CONFIG] LLM 端点配置向导\n")

    while True:
        # 显示当前配置
        endpoints, _compiler_eps, settings = load_endpoints_config()
        if endpoints:
            print(f"当前已配置 {len(endpoints)} 个端点:")
            for i, ep in enumerate(endpoints, 1):
                print(f"  [{i}] {ep.name} ({ep.provider}/{ep.model}) - 优先级 {ep.priority}")
            print()

        # 选择操作
        print("选择操作:")
        print("  [1] 添加新端点")
        print("  [2] 删除端点")
        print("  [3] 修改优先级")
        print("  [4] 测试端点")
        print("  [5] 保存并退出")
        print("  [0] 退出不保存")

        choice = input("\n> ").strip()

        if choice == "1":
            _add_endpoint_interactive(endpoints)
        elif choice == "2":
            _remove_endpoint_interactive(endpoints)
        elif choice == "3":
            _change_priority_interactive(endpoints)
        elif choice == "4":
            _test_endpoint_interactive(endpoints)
        elif choice == "5":
            save_endpoints_config(endpoints, settings)
            print("\n[OK] 配置已保存")
            break
        elif choice == "0":
            print("\n已取消")
            break
        else:
            print("\n[X] 无效选择，请重试")


def _add_endpoint_interactive(endpoints: list[EndpointConfig]):
    """交互式添加端点"""
    print("\n选择服务商:")
    providers = list_providers()

    for i, p in enumerate(providers, 1):
        print(f"  [{i}] {p.name}")
    print(f"  [{len(providers) + 1}] 自定义 (手动输入)")

    choice = input("\n> ").strip()

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(providers):
            provider_info = providers[idx]
            _add_endpoint_from_provider(endpoints, provider_info)
        elif idx == len(providers):
            _add_custom_endpoint(endpoints)
        else:
            print("[X] 无效选择")
    except ValueError:
        print("[X] 请输入数字")


def _add_endpoint_from_provider(endpoints: list[EndpointConfig], provider_info: ProviderInfo):
    """从服务商添加端点"""
    print(f"\n已选择: {provider_info.name}")

    # 获取 API Key
    env_key = provider_info.api_key_env_suggestion
    existing_key = os.environ.get(env_key)

    if existing_key:
        print(f"检测到环境变量 {env_key} 已设置")
        use_env = input("使用此环境变量? [Y/n]: ").strip().lower()
        api_key = existing_key if use_env in ("", "y", "yes") else input("请输入 API Key: ").strip()
    else:
        api_key = input(f"请输入 API Key (或按 Enter 跳过，稍后设置环境变量 {env_key}): ").strip()

    if not api_key and not existing_key:
        print(f"\n[!] 请确保稍后设置环境变量: export {env_key}=your_api_key")
        api_key = "placeholder"  # 仅用于测试获取模型列表

    # 获取模型列表
    if provider_info.supports_model_list and api_key != "placeholder":
        print("\n正在获取模型列表...")
        try:
            registry = get_registry(provider_info.slug)
            models = asyncio.run(registry.list_models(api_key))

            if models:
                print("\n可用模型:")
                for i, m in enumerate(models[:20], 1):  # 最多显示 20 个
                    print(f"  [{i}] {m.id}")
                if len(models) > 20:
                    print(f"  ... 还有 {len(models) - 20} 个模型")

                model_choice = input("\n选择模型 (输入编号或模型名): ").strip()
                try:
                    model_idx = int(model_choice) - 1
                    if 0 <= model_idx < len(models):
                        model_id = models[model_idx].id
                    else:
                        model_id = model_choice
                except ValueError:
                    model_id = model_choice
            else:
                model_id = input("请输入模型名称: ").strip()
        except Exception as e:
            print(f"[!] 获取模型列表失败: {e}")
            model_id = input("请输入模型名称: ").strip()
    else:
        model_id = input("请输入模型名称: ").strip()

    if not model_id:
        print("[X] 模型名称不能为空")
        return

    # 设置优先级
    priority = input(f"设置优先级 (数字越小优先级越高, 默认 {len(endpoints) + 1}): ").strip()
    try:
        priority = int(priority) if priority else len(endpoints) + 1
    except ValueError:
        priority = len(endpoints) + 1

    # 设置端点名称
    default_name = f"{provider_info.slug}-{model_id.split('/')[-1]}"
    name = input(f"端点名称 (默认 {default_name}): ").strip() or default_name

    # 自定义 Base URL (可选)
    print(f"\nAPI Base URL (默认 {provider_info.default_base_url}):")
    custom_url = input("> ").strip()
    base_url = custom_url if custom_url else provider_info.default_base_url

    # 获取能力（自动推断 + 用户确认）
    caps = infer_capabilities(model_id, provider_slug=provider_info.slug)
    auto_capabilities = [k for k, v in caps.items() if v and k != "thinking_only"]

    print(f"\n自动检测到的能力: {', '.join(auto_capabilities) if auto_capabilities else '无'}")
    print("可用能力: text, vision, video, tools")
    print("是否修改? 输入新的能力列表 (用逗号分隔) 或直接回车保留:")
    caps_input = input("> ").strip()

    if caps_input:
        capabilities = [c.strip() for c in caps_input.split(",") if c.strip()]
    else:
        capabilities = auto_capabilities if auto_capabilities else ["text"]

    # 创建端点配置
    endpoint = EndpointConfig(
        name=name,
        provider=provider_info.slug,
        api_type=provider_info.api_type,
        base_url=base_url,
        api_key_env=env_key,
        model=model_id,
        priority=priority,
        capabilities=capabilities,
    )

    endpoints.append(endpoint)
    endpoints.sort(key=lambda x: x.priority)

    print(f"\n[OK] 已添加端点: {name}")


def _add_custom_endpoint(endpoints: list[EndpointConfig]):
    """添加自定义端点"""
    print("\n" + "=" * 50)
    print("  添加自定义 LLM 端点")
    print("=" * 50)

    # 基本信息
    name = input("\n端点名称 (如 my-gpt4): ").strip()
    if not name:
        print("[X] 名称不能为空")
        return

    base_url = input("API Base URL (如 https://api.openai.com/v1): ").strip()
    if not base_url:
        print("[X] URL 不能为空")
        return

    print("\nAPI Key 配置方式:")
    print("  [1] 使用环境变量 (推荐)")
    print("  [2] 直接输入 Key (会保存到配置文件)")
    key_choice = input("> ").strip()

    if key_choice == "2":
        api_key = input("API Key: ").strip()
        api_key_env = None
    else:
        api_key_env = input("环境变量名 (如 MY_API_KEY): ").strip()
        api_key = None
        if api_key_env:
            existing = os.environ.get(api_key_env)
            if existing:
                print(f"  [OK] 已检测到环境变量 {api_key_env}")
            else:
                print(f"  [!] 请稍后设置: export {api_key_env}=your_key")

    model = input("模型名称 (如 gpt-4, qwen-max): ").strip()
    if not model:
        print("[X] 模型名称不能为空")
        return

    # API 类型
    print("\nAPI 类型:")
    print("  [1] OpenAI 兼容 (适用于大多数服务商)")
    print("  [2] Anthropic 原生")
    api_type_choice = input("> ").strip()
    api_type = "anthropic" if api_type_choice == "2" else "openai"

    # 优先级
    priority = input(f"\n优先级 (数字越小越优先, 默认 {len(endpoints) + 1}): ").strip()
    try:
        priority = int(priority) if priority else len(endpoints) + 1
    except ValueError:
        priority = len(endpoints) + 1

    # 能力配置
    print("\n" + "-" * 50)
    print("  配置端点能力")
    print("-" * 50)
    print("可用能力:")
    print("  text   - 文本对话 (基础能力)")
    print("  vision - 图片理解")
    print("  video  - 视频理解")
    print("  tools  - 工具调用 (Function Calling)")
    print()
    print("请选择支持的能力 (用逗号分隔, 默认 text,tools):")
    caps_input = input("> ").strip()

    if caps_input:
        capabilities = [c.strip() for c in caps_input.split(",") if c.strip()]
    else:
        capabilities = ["text", "tools"]

    # 创建端点配置
    endpoint = EndpointConfig(
        name=name,
        provider="custom",
        api_type=api_type,
        base_url=base_url,
        api_key_env=api_key_env,
        api_key=api_key,
        model=model,
        priority=priority,
        capabilities=capabilities,
    )

    endpoints.append(endpoint)
    endpoints.sort(key=lambda x: x.priority)

    print(f"\n[OK] 已添加端点: {name}")
    print(f"     URL: {base_url}")
    print(f"     模型: {model}")
    print(f"     能力: {', '.join(capabilities)}")


def _remove_endpoint_interactive(endpoints: list[EndpointConfig]):
    """交互式删除端点"""
    if not endpoints:
        print("\n[!] 没有可删除的端点")
        return

    print("\n选择要删除的端点:")
    for i, ep in enumerate(endpoints, 1):
        print(f"  [{i}] {ep.name}")

    choice = input("\n> ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(endpoints):
            removed = endpoints.pop(idx)
            print(f"\n[OK] 已删除端点: {removed.name}")
        else:
            print("[X] 无效选择")
    except ValueError:
        print("[X] 请输入数字")


def _change_priority_interactive(endpoints: list[EndpointConfig]):
    """交互式修改优先级"""
    if not endpoints:
        print("\n[!] 没有可修改的端点")
        return

    print("\n选择要修改的端点:")
    for i, ep in enumerate(endpoints, 1):
        print(f"  [{i}] {ep.name} - 当前优先级 {ep.priority}")

    choice = input("\n> ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(endpoints):
            new_priority = input("新优先级: ").strip()
            endpoints[idx].priority = int(new_priority)
            endpoints.sort(key=lambda x: x.priority)
            print("\n[OK] 已修改优先级")
        else:
            print("[X] 无效选择")
    except ValueError:
        print("[X] 请输入数字")


def _test_endpoint_interactive(endpoints: list[EndpointConfig]):
    """交互式测试端点"""
    if not endpoints:
        print("\n[!] 没有可测试的端点")
        return

    print("\n选择要测试的端点:")
    for i, ep in enumerate(endpoints, 1):
        print(f"  [{i}] {ep.name}")

    choice = input("\n> ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(endpoints):
            ep = endpoints[idx]
            print(f"\n正在测试 {ep.name}...")

            # 简单测试
            from ..client import LLMClient
            from ..types import Message

            client = LLMClient(endpoints=[ep])

            async def test():
                try:
                    response = await client.chat(
                        messages=[
                            Message(role="user", content="Hi, just testing. Reply with 'OK'.")
                        ],
                        max_tokens=10,
                    )
                    return True, response.text
                except Exception as e:
                    return False, str(e)

            success, result = asyncio.run(test())

            if success:
                print(f"\n[OK] 测试成功: {result}")
            else:
                print(f"\n[FAIL] 测试失败: {result}")
        else:
            print("[X] 无效选择")
    except ValueError:
        print("[X] 请输入数字")


def quick_add_endpoint(
    provider: str,
    model: str,
    priority: int = 1,
    name: str | None = None,
):
    """
    快速添加端点（用于命令行）

    Usage:
        python -m openakita.llm.setup.cli add --provider dashscope --model qwen-max
    """
    from ..registries import get_registry

    registry = get_registry(provider)
    info = registry.info

    if name is None:
        name = f"{provider}-{model.split('/')[-1]}"

    caps = infer_capabilities(model, provider_slug=provider)
    capabilities = [k for k, v in caps.items() if v and k != "thinking_only"]

    endpoint = EndpointConfig(
        name=name,
        provider=provider,
        api_type=info.api_type,
        base_url=info.default_base_url,
        api_key_env=info.api_key_env_suggestion,
        model=model,
        priority=priority,
        capabilities=capabilities,
    )

    endpoints, compiler_eps, settings = load_endpoints_config()
    endpoints.append(endpoint)
    endpoints.sort(key=lambda x: x.priority)
    save_endpoints_config(endpoints, settings, compiler_endpoints=compiler_eps)

    print(f"[OK] 已添加端点: {name}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LLM 端点配置向导")
    subparsers = parser.add_subparsers(dest="command")

    # add 命令
    add_parser = subparsers.add_parser("add", help="快速添加端点")
    add_parser.add_argument("--provider", required=True, help="服务商")
    add_parser.add_argument("--model", required=True, help="模型名称")
    add_parser.add_argument("--priority", type=int, default=1, help="优先级")
    add_parser.add_argument("--name", help="端点名称")

    args = parser.parse_args()

    if args.command == "add":
        quick_add_endpoint(
            provider=args.provider,
            model=args.model,
            priority=args.priority,
            name=args.name,
        )
    else:
        run_cli_wizard()
