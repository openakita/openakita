"""
CLI 配置向导

交互式命令行工具，用于配置 LLM 端点。
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

from ..config import load_endpoints_config, save_endpoints_config, get_default_config_path
from ..types import EndpointConfig
from ..registries import get_registry, list_providers, ProviderInfo, ModelInfo
from ..capabilities import infer_capabilities


def run_cli_wizard():
    """运行 CLI 配置向导"""
    print("\n🔧 LLM 端点配置向导\n")
    
    while True:
        # 显示当前配置
        endpoints, settings = load_endpoints_config()
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
            print("\n✅ 配置已保存")
            break
        elif choice == "0":
            print("\n已取消")
            break
        else:
            print("\n❌ 无效选择，请重试")


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
            print("❌ 无效选择")
    except ValueError:
        print("❌ 请输入数字")


def _add_endpoint_from_provider(endpoints: list[EndpointConfig], provider_info: ProviderInfo):
    """从服务商添加端点"""
    print(f"\n已选择: {provider_info.name}")
    
    # 获取 API Key
    env_key = provider_info.api_key_env_suggestion
    existing_key = os.environ.get(env_key)
    
    if existing_key:
        print(f"检测到环境变量 {env_key} 已设置")
        use_env = input("使用此环境变量? [Y/n]: ").strip().lower()
        if use_env in ("", "y", "yes"):
            api_key = existing_key
        else:
            api_key = input(f"请输入 API Key: ").strip()
    else:
        api_key = input(f"请输入 API Key (或按 Enter 跳过，稍后设置环境变量 {env_key}): ").strip()
    
    if not api_key and not existing_key:
        print(f"\n⚠️ 请确保稍后设置环境变量: export {env_key}=your_api_key")
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
            print(f"⚠️ 获取模型列表失败: {e}")
            model_id = input("请输入模型名称: ").strip()
    else:
        model_id = input("请输入模型名称: ").strip()
    
    if not model_id:
        print("❌ 模型名称不能为空")
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
    
    # 获取能力
    caps = infer_capabilities(model_id, provider_slug=provider_info.slug)
    capabilities = [k for k, v in caps.items() if v and k != "thinking_only"]
    
    # 创建端点配置
    endpoint = EndpointConfig(
        name=name,
        provider=provider_info.slug,
        api_type=provider_info.api_type,
        base_url=provider_info.default_base_url,
        api_key_env=env_key,
        model=model_id,
        priority=priority,
        capabilities=capabilities,
    )
    
    endpoints.append(endpoint)
    endpoints.sort(key=lambda x: x.priority)
    
    print(f"\n✅ 已添加端点: {name}")


def _add_custom_endpoint(endpoints: list[EndpointConfig]):
    """添加自定义端点"""
    print("\n添加自定义端点")
    
    name = input("端点名称: ").strip()
    base_url = input("API Base URL: ").strip()
    api_key_env = input("API Key 环境变量名: ").strip()
    model = input("模型名称: ").strip()
    
    print("\nAPI 类型:")
    print("  [1] OpenAI 兼容")
    print("  [2] Anthropic")
    api_type_choice = input("> ").strip()
    api_type = "anthropic" if api_type_choice == "2" else "openai"
    
    priority = input(f"优先级 (默认 {len(endpoints) + 1}): ").strip()
    try:
        priority = int(priority) if priority else len(endpoints) + 1
    except ValueError:
        priority = len(endpoints) + 1
    
    # 能力配置
    print("\n选择支持的能力 (用逗号分隔):")
    print("  text, vision, video, tools, thinking")
    caps_input = input("> ").strip()
    capabilities = [c.strip() for c in caps_input.split(",") if c.strip()]
    if not capabilities:
        capabilities = ["text"]
    
    endpoint = EndpointConfig(
        name=name,
        provider="custom",
        api_type=api_type,
        base_url=base_url,
        api_key_env=api_key_env,
        model=model,
        priority=priority,
        capabilities=capabilities,
    )
    
    endpoints.append(endpoint)
    endpoints.sort(key=lambda x: x.priority)
    
    print(f"\n✅ 已添加端点: {name}")


def _remove_endpoint_interactive(endpoints: list[EndpointConfig]):
    """交互式删除端点"""
    if not endpoints:
        print("\n⚠️ 没有可删除的端点")
        return
    
    print("\n选择要删除的端点:")
    for i, ep in enumerate(endpoints, 1):
        print(f"  [{i}] {ep.name}")
    
    choice = input("\n> ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(endpoints):
            removed = endpoints.pop(idx)
            print(f"\n✅ 已删除端点: {removed.name}")
        else:
            print("❌ 无效选择")
    except ValueError:
        print("❌ 请输入数字")


def _change_priority_interactive(endpoints: list[EndpointConfig]):
    """交互式修改优先级"""
    if not endpoints:
        print("\n⚠️ 没有可修改的端点")
        return
    
    print("\n选择要修改的端点:")
    for i, ep in enumerate(endpoints, 1):
        print(f"  [{i}] {ep.name} - 当前优先级 {ep.priority}")
    
    choice = input("\n> ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(endpoints):
            new_priority = input(f"新优先级: ").strip()
            endpoints[idx].priority = int(new_priority)
            endpoints.sort(key=lambda x: x.priority)
            print(f"\n✅ 已修改优先级")
        else:
            print("❌ 无效选择")
    except ValueError:
        print("❌ 请输入数字")


def _test_endpoint_interactive(endpoints: list[EndpointConfig]):
    """交互式测试端点"""
    if not endpoints:
        print("\n⚠️ 没有可测试的端点")
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
                        messages=[Message(role="user", content="Hi, just testing. Reply with 'OK'.")],
                        max_tokens=10,
                    )
                    return True, response.text
                except Exception as e:
                    return False, str(e)
            
            success, result = asyncio.run(test())
            
            if success:
                print(f"\n✅ 测试成功: {result[:50]}...")
            else:
                print(f"\n❌ 测试失败: {result}")
        else:
            print("❌ 无效选择")
    except ValueError:
        print("❌ 请输入数字")


def quick_add_endpoint(
    provider: str,
    model: str,
    priority: int = 1,
    name: Optional[str] = None,
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
    
    endpoints, settings = load_endpoints_config()
    endpoints.append(endpoint)
    endpoints.sort(key=lambda x: x.priority)
    save_endpoints_config(endpoints, settings)
    
    print(f"✅ 已添加端点: {name}")


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
