"""
CLI configuration wizard

Interactive command-line tool for configuring LLM endpoints.
"""

import asyncio
import os

from ..capabilities import infer_capabilities
from ..config import load_endpoints_config, save_endpoints_config
from ..registries import ProviderInfo, get_registry, list_providers
from ..types import EndpointConfig


def run_cli_wizard():
    """Run the CLI configuration wizard"""
    print("\n[CONFIG] LLM endpoint configuration wizard\n")

    while True:
        # Show current configuration
        endpoints, _compiler_eps, _stt_eps, settings = load_endpoints_config()
        if endpoints:
            print(f"Currently configured {len(endpoints)} endpoint(s):")
            for i, ep in enumerate(endpoints, 1):
                print(f"  [{i}] {ep.name} ({ep.provider}/{ep.model}) - priority {ep.priority}")
            print()

        # Choose action
        print("Choose an action:")
        print("  [1] Add new endpoint")
        print("  [2] Remove endpoint")
        print("  [3] Change priority")
        print("  [4] Test endpoint")
        print("  [5] Save and exit")
        print("  [0] Exit without saving")

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
            save_endpoints_config(
                endpoints,
                settings,
                compiler_endpoints=_compiler_eps,
                stt_endpoints=_stt_eps,
            )
            print("\n[OK] Configuration saved")
            break
        elif choice == "0":
            print("\nCancelled")
            break
        else:
            print("\n[X] Invalid choice, please try again")


def _add_endpoint_interactive(endpoints: list[EndpointConfig]):
    """Interactively add an endpoint"""
    print("\nChoose a provider:")
    providers = list_providers()

    for i, p in enumerate(providers, 1):
        print(f"  [{i}] {p.name}")
    print(f"  [{len(providers) + 1}] Custom (manual entry)")

    choice = input("\n> ").strip()

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(providers):
            provider_info = providers[idx]
            _add_endpoint_from_provider(endpoints, provider_info)
        elif idx == len(providers):
            _add_custom_endpoint(endpoints)
        else:
            print("[X] Invalid choice")
    except ValueError:
        print("[X] Please enter a number")


def _unique_env_key(base: str, used: set[str]) -> str:
    """Return *base* if unused, otherwise append _2, _3, … until unique."""
    if not base or base not in used:
        return base
    for i in range(2, 100):
        candidate = f"{base}_{i}"
        if candidate not in used:
            return candidate
    return f"{base}_{int(__import__('time').time())}"


def _add_endpoint_from_provider(endpoints: list[EndpointConfig], provider_info: ProviderInfo):
    """Add endpoint from a provider"""
    print(f"\nSelected: {provider_info.name}")

    # Get API Key — deduplicate env var name against existing endpoints
    used_env_keys = {ep.api_key_env for ep in endpoints if ep.api_key_env}
    env_key = _unique_env_key(provider_info.api_key_env_suggestion, used_env_keys)
    existing_key = os.environ.get(env_key)

    if existing_key:
        print(f"Detected environment variable {env_key} is already set")
        use_env = input("Use this environment variable? [Y/n]: ").strip().lower()
        api_key = existing_key if use_env in ("", "y", "yes") else input("Enter API Key: ").strip()
    else:
        api_key = input(f"Enter API Key (or press Enter to skip and set the {env_key} env var later): ").strip()

    if not api_key and not existing_key:
        print(f"\n[!] Make sure to set the environment variable later: export {env_key}=your_api_key")
        api_key = "placeholder"  # Only used for fetching the model list

    # Fetch model list
    if provider_info.supports_model_list and api_key != "placeholder":
        print("\nFetching model list...")
        try:
            registry = get_registry(provider_info.slug)
            models = asyncio.run(registry.list_models(api_key))

            if models:
                print("\nAvailable models:")
                for i, m in enumerate(models[:20], 1):  # Show at most 20
                    print(f"  [{i}] {m.id}")
                if len(models) > 20:
                    print(f"  ... and {len(models) - 20} more model(s)")

                model_choice = input("\nChoose a model (enter number or model name): ").strip()
                try:
                    model_idx = int(model_choice) - 1
                    if 0 <= model_idx < len(models):
                        model_id = models[model_idx].id
                    else:
                        model_id = model_choice
                except ValueError:
                    model_id = model_choice
            else:
                model_id = input("Enter model name: ").strip()
        except Exception as e:
            print(f"[!] Failed to fetch model list: {e}")
            model_id = input("Enter model name: ").strip()
    else:
        model_id = input("Enter model name: ").strip()

    if not model_id:
        print("[X] Model name cannot be empty")
        return

    # Set priority
    priority = input(f"Set priority (lower number = higher priority, default {len(endpoints) + 1}): ").strip()
    try:
        priority = int(priority) if priority else len(endpoints) + 1
    except ValueError:
        priority = len(endpoints) + 1

    # Set endpoint name
    default_name = f"{provider_info.slug}-{model_id.split('/')[-1]}"
    name = input(f"Endpoint name (default {default_name}): ").strip() or default_name

    # Custom Base URL (optional)
    print(f"\nAPI Base URL (default {provider_info.default_base_url}):")
    custom_url = input("> ").strip()
    base_url = custom_url if custom_url else provider_info.default_base_url

    # Get capabilities (auto-infer + user confirmation)
    caps = infer_capabilities(model_id, provider_slug=provider_info.slug)
    auto_capabilities = [k for k, v in caps.items() if v and k != "thinking_only"]

    print(f"\nAuto-detected capabilities: {', '.join(auto_capabilities) if auto_capabilities else 'none'}")
    print("Available capabilities: text, vision, video, tools")
    print("Modify? Enter a new comma-separated capability list or press Enter to keep:")
    caps_input = input("> ").strip()

    if caps_input:
        capabilities = [c.strip() for c in caps_input.split(",") if c.strip()]
    else:
        capabilities = auto_capabilities if auto_capabilities else ["text"]

    # Create endpoint config
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

    print(f"\n[OK] Endpoint added: {name}")


def _add_custom_endpoint(endpoints: list[EndpointConfig]):
    """Add a custom endpoint"""
    print("\n" + "=" * 50)
    print("  Add custom LLM endpoint")
    print("=" * 50)

    # Basic info
    name = input("\nEndpoint name (e.g. my-gpt4): ").strip()
    if not name:
        print("[X] Name cannot be empty")
        return

    base_url = input("API Base URL (e.g. https://api.openai.com/v1): ").strip()
    if not base_url:
        print("[X] URL cannot be empty")
        return

    print("\nAPI Key configuration method:")
    print("  [1] Use environment variable (recommended)")
    print("  [2] Enter key directly (will be saved to config file)")
    key_choice = input("> ").strip()

    if key_choice == "2":
        api_key = input("API Key: ").strip()
        api_key_env = None
    else:
        api_key_env = input("Environment variable name (e.g. MY_API_KEY): ").strip()
        api_key = None
        if api_key_env:
            existing = os.environ.get(api_key_env)
            if existing:
                print(f"  [OK] Environment variable {api_key_env} detected")
            else:
                print(f"  [!] Please set it later: export {api_key_env}=your_key")

    model = input("Model name (e.g. gpt-4, qwen-max): ").strip()
    if not model:
        print("[X] Model name cannot be empty")
        return

    # API type
    print("\nAPI type:")
    print("  [1] OpenAI-compatible (works for most providers)")
    print("  [2] Anthropic native")
    api_type_choice = input("> ").strip()
    api_type = "anthropic" if api_type_choice == "2" else "openai"

    # Priority
    priority = input(f"\nPriority (lower number = higher priority, default {len(endpoints) + 1}): ").strip()
    try:
        priority = int(priority) if priority else len(endpoints) + 1
    except ValueError:
        priority = len(endpoints) + 1

    # Capability configuration
    print("\n" + "-" * 50)
    print("  Configure endpoint capabilities")
    print("-" * 50)
    print("Available capabilities:")
    print("  text   - Text chat (base capability)")
    print("  vision - Image understanding")
    print("  video  - Video understanding")
    print("  tools  - Tool calling (Function Calling)")
    print()
    print("Select supported capabilities (comma-separated, default text,tools):")
    caps_input = input("> ").strip()

    if caps_input:
        capabilities = [c.strip() for c in caps_input.split(",") if c.strip()]
    else:
        capabilities = ["text", "tools"]

    # Create endpoint config
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

    print(f"\n[OK] Endpoint added: {name}")
    print(f"     URL: {base_url}")
    print(f"     Model: {model}")
    print(f"     Capabilities: {', '.join(capabilities)}")


def _remove_endpoint_interactive(endpoints: list[EndpointConfig]):
    """Interactively remove an endpoint"""
    if not endpoints:
        print("\n[!] No endpoints to remove")
        return

    print("\nChoose an endpoint to remove:")
    for i, ep in enumerate(endpoints, 1):
        print(f"  [{i}] {ep.name}")

    choice = input("\n> ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(endpoints):
            removed = endpoints.pop(idx)
            print(f"\n[OK] Endpoint removed: {removed.name}")
        else:
            print("[X] Invalid choice")
    except ValueError:
        print("[X] Please enter a number")


def _change_priority_interactive(endpoints: list[EndpointConfig]):
    """Interactively change priority"""
    if not endpoints:
        print("\n[!] No endpoints to modify")
        return

    print("\nChoose an endpoint to modify:")
    for i, ep in enumerate(endpoints, 1):
        print(f"  [{i}] {ep.name} - current priority {ep.priority}")

    choice = input("\n> ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(endpoints):
            new_priority = input("New priority: ").strip()
            endpoints[idx].priority = int(new_priority)
            endpoints.sort(key=lambda x: x.priority)
            print("\n[OK] Priority updated")
        else:
            print("[X] Invalid choice")
    except ValueError:
        print("[X] Please enter a number")


def _test_endpoint_interactive(endpoints: list[EndpointConfig]):
    """Interactively test an endpoint"""
    if not endpoints:
        print("\n[!] No endpoints to test")
        return

    print("\nChoose an endpoint to test:")
    for i, ep in enumerate(endpoints, 1):
        print(f"  [{i}] {ep.name}")

    choice = input("\n> ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(endpoints):
            ep = endpoints[idx]
            print(f"\nTesting {ep.name}...")

            # Simple test
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
                print(f"\n[OK] Test succeeded: {result}")
            else:
                print(f"\n[FAIL] Test failed: {result}")
        else:
            print("[X] Invalid choice")
    except ValueError:
        print("[X] Please enter a number")


def quick_add_endpoint(
    provider: str,
    model: str,
    priority: int = 1,
    name: str | None = None,
):
    """
    Quickly add an endpoint (for command-line use)

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

    endpoints, compiler_eps, stt_eps, settings = load_endpoints_config()

    used_env_keys = {
        ep.api_key_env for ep in [*endpoints, *compiler_eps, *stt_eps] if ep.api_key_env
    }
    env_key = _unique_env_key(info.api_key_env_suggestion, used_env_keys)

    endpoint = EndpointConfig(
        name=name,
        provider=provider,
        api_type=info.api_type,
        base_url=info.default_base_url,
        api_key_env=env_key,
        model=model,
        priority=priority,
        capabilities=capabilities,
    )

    endpoints.append(endpoint)
    endpoints.sort(key=lambda x: x.priority)
    save_endpoints_config(
        endpoints, settings, compiler_endpoints=compiler_eps, stt_endpoints=stt_eps
    )

    print(f"[OK] Endpoint added: {name}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LLM endpoint configuration wizard")
    subparsers = parser.add_subparsers(dest="command")

    # add command
    add_parser = subparsers.add_parser("add", help="Quickly add an endpoint")
    add_parser.add_argument("--provider", required=True, help="Provider")
    add_parser.add_argument("--model", required=True, help="Model name")
    add_parser.add_argument("--priority", type=int, default=1, help="Priority")
    add_parser.add_argument("--name", help="Endpoint name")

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
