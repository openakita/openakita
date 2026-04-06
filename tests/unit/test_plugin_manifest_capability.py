from openakita.plugins.manifest import PluginManifest


def test_plugin_manifest_exposes_capability_metadata():
    manifest = PluginManifest.model_validate(
        {
            "id": "demo-plugin",
            "name": "Demo Plugin",
            "version": "1.0.0",
            "type": "python",
            "permissions": ["tools.register", "memory.read"],
            "display_name_zh": "演示插件",
        }
    )

    descriptor = manifest.to_capability_descriptor()
    assert manifest.namespace == "plugin:demo-plugin"
    assert manifest.capability_id == "plugin:demo-plugin/plugin:demo-plugin"
    assert descriptor.permission_profile == "advanced"
    assert descriptor.i18n["name"]["zh"] == "演示插件"
