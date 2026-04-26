from openakita.api.server import create_app


def test_openapi_schema_generates():
    app = create_app(agent=None)
    schema = app.openapi()

    assert schema["openapi"].startswith("3.")
    assert "/api/plugins/{plugin_id}/_admin/readme" in schema["paths"]
    assert "/api/plugins/{plugin_id}/_admin/logs" in schema["paths"]
