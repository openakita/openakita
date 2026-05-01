from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ui_contains_six_tabs_and_core_widgets() -> None:
    html = (ROOT / "ui" / "dist" / "index.html").read_text(encoding="utf-8")

    for tab in ["Create", "Projects", "Materials", "Templates", "Exports", "Settings"]:
        assert tab in html
    assert '"Sources"' not in html
    assert '"Tables"' not in html
    assert "SourcesTab" not in html
    assert "TablesTab" not in html
    for marker in ["FileUploadZone", "CostBreakdown", "ErrorPanel", "ProgressPanel"]:
        assert marker in html
    assert "Python 可选依赖" in html
    assert "/system/python-deps" in html
    assert "table_to_deck" in html
    assert "template_deck" in html
    assert "brand_tokens" in html
    assert "图表方案" in html
    assert "MaterialsTab" in html
    assert "素材集名称（必填）" in html
    assert "上传后同步处理素材" in html
    assert "同步处理中，请稍候..." in html
    assert "结果请到右侧素材管理卡片查看" in html
    assert "整组处理" in html
    assert "素材管理" in html
    assert "section-title" in html
    assert "collection-accordion" in html
    assert "material-type-tag" in html
    assert "material-badge" in html
    assert "参考资料" in html
    assert "表格数据" in html
    assert "/sources/${item.id}/parse" in html or "sources/${item.id}/parse" in html
    assert "/datasets/${item.id}/profile" in html or "datasets/${item.id}/profile" in html
    assert "确认上传" in html
    assert "处理选中素材" in html
    assert "TemplateDiagnosticSummary" in html
    assert "TemplateDiagnosisModal" in html
    assert "/templates/${item.id}/diagnosis" in html or "templates/${item.id}/diagnosis" in html
    assert "模板管理" in html
    assert "模板名称（必填）" in html
    assert "上传后同步诊断模板" in html
    assert "同步诊断中，请稍候..." in html
    assert "页面类型匹配情况" in html
    assert "品牌色" in html
    assert "查看原始诊断 JSON" in html
    assert "使用前检查" in html
    assert "这些字段可能缺值较多或表格过宽" in html
    assert "手动登记路径" not in html
    assert "确认删除该素材？" in html
    assert "delete-modal__icon" in html
    assert "同时删除插件存储目录里的文件和分析结果" in html
    assert "删除文件夹" in html
    assert "按用途自动归档" in html
    assert "/storage/stats" in html
    assert "/storage/open-folder" in html
    assert "/storage/list-dir" in html
    assert "/storage/mkdir" in html
    assert "FolderPickerModal" in html
    assert "上传文件命名规则" in html
    assert "template-card-list" in html
    assert "centered-tab" in html
    assert "/api/plugins/ppt-maker" in html
    assert "split-layout" in html
    assert "oa-preview-area" in html
    assert "ak-logo" in html
    assert "需求梳理 · 表格洞察 · 模板适配 · PPTX 导出" in html
    assert "m2.859 2.878l12.57-1.796" in html


def test_ui_assets_are_self_contained_for_host_bridge() -> None:
    html = (ROOT / "ui" / "dist" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "ui" / "dist" / "_assets" / "styles.css").read_text(encoding="utf-8")

    assert "./_assets/bootstrap.js" in html
    assert "/api/plugins/_sdk" not in html
    assert "OpenAkita" in html
    assert "#e84d2a" in css.lower()
    assert "#7c3aed" not in css.lower()
    assert "width: 23px" in css

