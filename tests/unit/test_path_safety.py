from openakita.tools.path_safety import resolve_within_root


def test_resolve_within_root_allows_workspace_child(tmp_path):
    child = tmp_path / "src" / "a.py"

    result = resolve_within_root(str(child), [tmp_path])

    assert result.ok
    assert result.safe_ref.replace("\\", "/") == "src/a.py"


def test_resolve_within_root_denies_parent_escape(tmp_path):
    outside = tmp_path.parent / "outside.txt"

    result = resolve_within_root(str(outside), [tmp_path])

    assert not result.ok
    assert result.reason == "outside_allowed_roots"


def test_resolve_within_root_denies_control_chars(tmp_path):
    result = resolve_within_root("bad\x00path", [tmp_path])

    assert not result.ok
    assert result.reason == "control_char"
