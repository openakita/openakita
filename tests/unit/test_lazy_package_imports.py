import importlib
import sys


def _clear_modules(prefix: str) -> None:
    for name in list(sys.modules):
        if name == prefix or name.startswith(f"{prefix}."):
            sys.modules.pop(name, None)


class TestCorePackageImports:
    def test_importing_core_errors_does_not_load_agent_stack(self):
        _clear_modules("openakita.core")

        errors = importlib.import_module("openakita.core.errors")

        assert hasattr(errors, "UserCancelledError")
        assert "openakita.core.agent" not in sys.modules
        assert "openakita.core.brain" not in sys.modules

    def test_package_level_core_exports_are_lazy(self):
        _clear_modules("openakita.core")

        core = importlib.import_module("openakita.core")
        assert "openakita.core.agent" not in sys.modules

        AgentState = core.AgentState

        assert AgentState.__name__ == "AgentState"
        assert "openakita.core.agent_state" in sys.modules
        assert "openakita.core.agent" not in sys.modules


class TestLlmPackageImports:
    def test_importing_llm_types_does_not_load_client_stack(self):
        _clear_modules("openakita.llm")
        _clear_modules("openakita.core")

        types_mod = importlib.import_module("openakita.llm.types")

        assert hasattr(types_mod, "LLMRequest")
        assert "openakita.llm.client" not in sys.modules
        assert "openakita.llm.adapter" not in sys.modules
        assert "openakita.core.agent" not in sys.modules

    def test_package_level_llm_type_exports_are_lazy(self):
        _clear_modules("openakita.llm")
        _clear_modules("openakita.core")

        llm = importlib.import_module("openakita.llm")
        assert "openakita.llm.client" not in sys.modules

        EndpointConfig = llm.EndpointConfig

        assert EndpointConfig.__name__ == "EndpointConfig"
        assert "openakita.llm.types" in sys.modules
        assert "openakita.llm.client" not in sys.modules
