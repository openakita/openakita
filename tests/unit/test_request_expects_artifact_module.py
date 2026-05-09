"""Module-level ``request_expects_artifact`` semantics + 兼容 ResponseHandler 静态方法。

补充 ``test_summary_bypass.py`` 覆盖：
1. 新关键词识别（导出/海报/写一份等"交付意图"短语）；
2. 模块级 helper 与 ResponseHandler._request_expects_artifact 行为完全一致；
3. None / 空字符串 / 系统前缀仍正确拒识。
"""

from openakita.core.response_handler import (
    ResponseHandler,
    request_expects_artifact,
)


class TestRequestExpectsArtifactModuleLevel:
    def test_module_level_function_exists(self):
        assert callable(request_expects_artifact)

    def test_static_method_delegates_to_module_function(self):
        for sample in (
            "请生成一份海报",
            "导出一个 csv",
            "你好",
            "[用户指令最终汇总] 请输出文件清单",
            None,
            "",
        ):
            assert (
                ResponseHandler._request_expects_artifact(sample)
                == request_expects_artifact(sample)
            )

    def test_new_chinese_delivery_intent_keywords(self):
        # 新加入的「交付意图」短语应判 True
        assert request_expects_artifact("帮我生成一份周报") is True
        assert request_expects_artifact("写一份分析") is True
        assert request_expects_artifact("把结果导出成 excel") is True
        assert request_expects_artifact("做一份海报") is True

    def test_existing_artifact_keywords_still_work(self):
        # 原有覆盖不能退化
        assert request_expects_artifact("帮我下载文件") is True
        assert request_expects_artifact("发我一张图片") is True
        assert request_expects_artifact("please attach the file") is True

    def test_input_attachment_reference_is_not_delivery_intent(self):
        # 读取/分析用户已上传的附件是输入对象，不代表需要再交付一个附件。
        assert (
            request_expects_artifact(
                "Read the attached text file and answer only the project code it contains."
            )
            is False
        )
        assert request_expects_artifact("请读取我上传的附件，并总结里面的项目代号") is False
        assert request_expects_artifact("分析刚发来的图片里有什么") is False

    def test_input_attachment_with_explicit_export_still_expects_delivery(self):
        assert request_expects_artifact("Read the attached file and export a summary") is True
        assert request_expects_artifact("读取附件内容，然后导出成 excel") is True

    def test_pure_chat_returns_false(self):
        # 真正只是闲聊/问答，不含"交付意图"信号
        assert request_expects_artifact("你好今天天气怎么样") is False
        assert request_expects_artifact("帮我算一下 1+1") is False

    def test_system_prefixes_excluded(self):
        # 后端合成的元指令前缀必须直接 False，避免命中其中的"文件"
        assert request_expects_artifact("[用户指令最终汇总] 请汇总文件") is False
        assert request_expects_artifact("[系统] 请输出文件") is False
        assert request_expects_artifact("[组织] 文件已就绪") is False

    def test_none_and_empty(self):
        assert request_expects_artifact(None) is False
        assert request_expects_artifact("") is False
        assert request_expects_artifact("   ") is False

