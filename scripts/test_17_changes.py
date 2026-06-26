"""
综合测试：验证 17 项改动的逻辑正确性
"""

import json
import sys

passed = 0
failed = 0
total = 0


def test(name, condition, detail=""):
    global passed, failed, total
    total += 1
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}: {detail}")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ========================================
print("=== A1: llm_endpoints.json.example has compiler_endpoints ===")
example_data = json.loads(read("data/llm_endpoints.json.example"))
test("compiler_endpoints key exists", "compiler_endpoints" in example_data)
test("compiler_endpoints is a list", isinstance(example_data.get("compiler_endpoints"), list))
test("has 2 compiler endpoints", len(example_data.get("compiler_endpoints", [])) == 2)
ce = example_data["compiler_endpoints"]
test("primary has priority 1", ce[0].get("priority") == 1)
test("backup has priority 2", ce[1].get("priority") == 2)
test("primary capabilities is [text]", ce[0].get("capabilities") == ["text"])
test("primary timeout is 30", ce[0].get("timeout") == 30)
test("primary max_tokens is 2048", ce[0].get("max_tokens") == 2048)
test("endpoints still exists", "endpoints" in example_data)
test("settings still exists", "settings" in example_data)

print()
print("=== A2: config.py load_endpoints_config returns 3-tuple ===")
import inspect

sys.path.insert(0, "src")
from openakita.llm.config import load_endpoints_config, save_endpoints_config, validate_config

sig = inspect.signature(load_endpoints_config)
ret_str = str(sig.return_annotation)
# After import, EndpointConfig resolves to full module path; just check it has 3 elements in the tuple
test(
    "return annotation is a 3-element tuple",
    "tuple[" in ret_str and ret_str.count("list[") >= 2 and "dict" in ret_str,
    ret_str,
)

sig_save = inspect.signature(save_endpoints_config)
test("save has compiler_endpoints param", "compiler_endpoints" in sig_save.parameters)

print()
print("=== A2b: client.py and others use 3-tuple ===")
client_src = read("src/openakita/llm/client.py")
test(
    "client.py uses _ for compiler_endpoints",
    "self._endpoints, _, self._settings = load_endpoints_config" in client_src,
)

cli_src = read("src/openakita/llm/setup/cli.py")
test(
    "setup/cli.py 3-tuple (1st call)",
    "_compiler_eps, settings = load_endpoints_config()" in cli_src,
)
test(
    "setup/cli.py 3-tuple (2nd call)", "compiler_eps, settings = load_endpoints_config()" in cli_src
)

diag_src = read("scripts/llm_diag.py")
test("llm_diag.py uses 3-tuple", "_compiler_eps, _settings = load_endpoints_config()" in diag_src)

print()
print("=== A3: brain.py has compiler_think and _compiler_client ===")
brain_src = read("src/openakita/core/brain.py")
test("_compiler_client declaration", "_compiler_client: LLMClient | None = None" in brain_src)
test("_init_compiler_client method", "def _init_compiler_client(self)" in brain_src)
test("compiler_think method", "async def compiler_think(self" in brain_src)
test("compiler_think calls enable_thinking=False", "enable_thinking=False" in brain_src)
test("compiler_think has fallback logic", "falling back to main model" in brain_src)
test("_llm_response_to_response method", "def _llm_response_to_response(self" in brain_src)
test(
    "imports load_endpoints_config",
    "from ..llm.config import get_default_config_path, load_endpoints_config" in brain_src,
)

print()
print("=== A4: agent.py _compile_prompt uses compiler_think ===")
agent_src = read("src/openakita/core/agent.py")
test("_compile_prompt calls brain.compiler_think", "await self.brain.compiler_think(" in agent_src)

# Check brain.think is NOT in _compile_prompt
compile_method = (
    agent_src.split("async def _compile_prompt")[1]
    .split("\n    async def ")[0]
    .split("\n    def ")[0]
)
test(
    "_compile_prompt does NOT call brain.think()",
    "brain.think(" not in compile_method,
    "still uses brain.think",
)

print()
print("=== B1: _should_compile_prompt simplified ===")
method_start = agent_src.find("def _should_compile_prompt(self, message: str)")
method_end = agent_src.find("\n    def ", method_start + 10)
method_body = agent_src[method_start:method_end]
test("no simple_patterns list", "simple_patterns" not in method_body)
test("no regex match", "re.match" not in method_body)
test("uses len < 20 threshold", "len(message.strip()) < 20" in method_body)
test(
    "method is concise (< 15 lines)",
    method_body.count("\n") < 15,
    f"{method_body.count(chr(10))} lines",
)

print()
print("=== B2: vector_store async_search + async prompt build ===")
vs_src = read("src/openakita/memory/vector_store.py")
test("imports asyncio", "import asyncio" in vs_src)
test("async_search method exists", "async def async_search(" in vs_src)
test("uses asyncio.to_thread", "asyncio.to_thread" in vs_src)

ret_src = read("src/openakita/prompt/retriever.py")
test("async_search_related_memories exists", "async def async_search_related_memories(" in ret_src)
test("retriever uses vector_store.async_search", "vector_store.async_search(" in ret_src)

test(
    "_build_system_prompt_compiled is async",
    "async def _build_system_prompt_compiled(self" in agent_src,
)
test(
    "_build_system_prompt_compiled_sync exists",
    "def _build_system_prompt_compiled_sync(self" in agent_src,
)
test(
    "async version passes precomputed_memory", "precomputed_memory=precomputed_memory" in agent_src
)

print()
print("=== C1+C3: system msg convention + msg typing rules ===")
builder_src = read("src/openakita/prompt/builder.py")
test("system msg convention section", "## 系统消息约定" in builder_src)
test("mentions [系统] prefix", "[系统]" in builder_src)
test("mentions [系统提示] prefix", "[系统提示]" in builder_src)
test("msg typing rules section", "## 消息分型原则" in builder_src)
test("mentions 闲聊/问候", "闲聊/问候" in builder_src)
test("mentions 简单问答", "简单问答" in builder_src)
test("mentions 任务请求", "任务请求" in builder_src)
test("IM specific note about short messages", "IM 特殊注意" in builder_src)
test("common rules shared by both modes", builder_src.count("## 系统消息约定") >= 1)

print()
print("=== C2: context boundary marker ===")
test("boundary marker in agent", "[上下文结束，以下是用户的最新消息]" in agent_src)
test("assistant ack after boundary", "好的，我已了解之前的对话上下文" in agent_src)
# Verify it's only inserted when there are history messages
test(
    "only inserted when messages exist",
    'if messages:\n                messages.append({\n                    "role": "user",\n                    "content": "[上下文结束'
    in agent_src,
)

print()
print("=== C4: TaskVerify handles non-task messages ===")
rh_src = read("src/openakita/core/response_handler.py")
_vs = rh_src.find("async def verify_task_completion")
_ve = rh_src.find("\n    async def do_task_retrospect", _vs)
verify_section = rh_src[_vs:_ve] if _vs >= 0 and _ve > _vs else rh_src[_vs : _vs + 16000]
test("verify_task_completion lives in response_handler", _vs >= 0)
test("verify prompt handles greetings", "闲聊/问候" in verify_section)
test("verify prompt handles confirmations", "简单确认/反馈" in verify_section)
test(
    "non-task section references COMPLETED",
    "非任务类" in verify_section and "COMPLETED" in verify_section,
)
test("simple Q&A in verify prompt", "简单问答" in verify_section)

print()
print("=== C5: soften INCOMPLETE message ===")
re_src = read("src/openakita/core/reasoning_engine.py")
_core = agent_src + re_src
test("old harsh message removed", "任务尚未完成。请继续执行" not in _core)
test("new soft message in reasoning_engine", "根据复核判断，用户请求可能还有未完成的部分" in re_src)
test("verify retry offers finish or continue", "如果已全部完成" in re_src)

print()
print("=== C6: IM ForceToolCall floor ===")
test(
    "IM path sets base_force_retries from im_floor",
    'session_type == "im"' in re_src and "im_floor" in re_src,
)

print()
print("=== C7: loop detection refactored ===")
sup_src = read("src/openakita/core/supervisor.py")
state_src = read("src/openakita/core/agent_state.py")
test("_make_tool_signature with hashlib", "import hashlib" in re_src)
test("param hash in signature", "param_hash = hashlib.md5" in re_src)
test("old soft limit 15 removed", "max_tool_rounds_soft = 15" not in re_src)
test("old hard limit 30 removed", "max_tool_rounds_hard = 30" not in re_src)
test("LLM self-check interval = 10", "llm_self_check_interval: int = 10" in state_src)
test("extreme safety threshold = 50", "extreme_safety_threshold: int = 50" in state_src)
test("supervisor repeat terminate threshold", "SIGNATURE_REPEAT_TERMINATE" in sup_src)
test("dead loop uses most_common_count", "most_common_count >=" in sup_src)
test(
    "tool pattern uses param hash",
    "round_signatures = [_make_tool_signature(tc) for tc in tool_calls]" in re_src,
)

print()
print("=== C8: interrupt mechanism fixed ===")
gw_src = read("src/openakita/channels/gateway.py")
test("gateway detects stop commands", "is_stop_command(user_text)" in gw_src)
test("gateway calls cancel_current_task", "cancel_current_task(" in gw_src)
test("reasoning_engine cancels at iteration start", "Task cancelled at iteration start" in re_src)
test("cancel check comment in agent _chat_with_tools", "C8: 每轮迭代检查取消" in agent_src)
test(
    "cancel check comment in agent execute_task",
    "C8: 每轮迭代开始时检查任务是否被取消" in agent_src,
)

print()
print("=== A5: Setup Center UI (App.tsx) ===")
tsx_src = read("apps/setup-center/src/App.tsx")
test("savedCompilerEndpoints state var", "savedCompilerEndpoints" in tsx_src)
test("compiler_endpoints in JSON parsing", "compiler_endpoints" in tsx_src)
test("doSaveCompilerEndpoint function", "doSaveCompilerEndpoint" in tsx_src)
test("doDeleteCompilerEndpoint function", "doDeleteCompilerEndpoint" in tsx_src)
test("Prompt Compiler UI title", "提示词编译模型" in tsx_src or "Prompt Compiler" in tsx_src)
test(
    "max 2 compiler endpoints",
    tsx_src.count("savedCompilerEndpoints.length < 2") >= 1
    or tsx_src.count("savedCompilerEndpoints.length >= 2") >= 1
    or "最多 2 个" in tsx_src,
)

print()
print("=== A7: wizard.py compiler config ===")
wiz_src = read("src/openakita/setup/wizard.py")
test("_configure_compiler method", "def _configure_compiler(self)" in wiz_src)
test("_write_llm_endpoints method", "def _write_llm_endpoints(self)" in wiz_src)
test("_configure_compiler called in run()", "_configure_compiler()" in wiz_src)
test("_write_llm_endpoints called in _write_env_file flow", "_write_llm_endpoints()" in wiz_src)
test("imports json", "import json" in wiz_src)
test("writes compiler_endpoints to JSON", '"compiler_endpoints"' in wiz_src)
test("provides DashScope as default option", "qwen-turbo-latest" in wiz_src)
test("supports backup endpoint", "_compiler_backup" in wiz_src)

# ========================================
print()
print("=" * 60)
print(f"Results: {passed}/{total} passed, {failed} failed")
if failed:
    sys.exit(1)
else:
    print("\n🎉 All tests passed!")
