"""End-to-end smoke — every primary module imports & is internally consistent."""

from __future__ import annotations


def test_models_module_imports_clean():
    import happyhorse_models  # noqa: F401
    assert len(happyhorse_models.MODES) == 12


def test_registry_module_imports_clean():
    import happyhorse_model_registry  # noqa: F401

    payload = happyhorse_model_registry.RegistryPayload.build()
    assert len(payload.defaults) == len(happyhorse_model_registry.ALL_MODES)


def test_client_module_imports_clean():
    import happyhorse_dashscope_client  # noqa: F401

    settings = happyhorse_dashscope_client.make_default_settings()
    assert "api_key" in settings
    assert settings["base_url"].startswith("https://")


def test_pipeline_module_imports_clean():
    import happyhorse_pipeline  # noqa: F401

    assert happyhorse_pipeline.DEFAULT_POLL.total_timeout_sec >= 600


def test_long_video_module_imports_clean():
    import happyhorse_long_video  # noqa: F401

    assert callable(happyhorse_long_video.decompose_storyboard)
    assert callable(happyhorse_long_video.concat_videos)


def test_prompt_optimizer_imports_clean():
    import happyhorse_prompt_optimizer  # noqa: F401

    assert len(happyhorse_prompt_optimizer.PROMPT_TEMPLATES) >= 12


def test_image_models_import_clean():
    import happyhorse_image_models

    catalog = happyhorse_image_models.build_image_catalog()
    assert len(catalog["modes"]) >= 7
    assert happyhorse_image_models.image_model_for("").id == "wan27-pro"


def test_plugin_module_imports_clean():
    from _plugin_loader import load_happyhorse_plugin

    mod = load_happyhorse_plugin()
    assert mod.PLUGIN_ID == "happyhorse-video"
