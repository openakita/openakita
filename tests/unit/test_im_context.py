"""L1 Unit Tests: IM context variable management."""

import pytest

from openakita.core.im_context import (
    get_im_session,
    get_im_gateway,
    set_im_context,
    reset_im_context,
)


class TestIMContext:
    def test_default_none(self):
        assert get_im_session() is None
        assert get_im_gateway() is None

    def test_set_and_get(self):
        mock_session = {"id": "test-session"}
        mock_gateway = {"name": "telegram"}
        tokens = set_im_context(session=mock_session, gateway=mock_gateway)
        try:
            assert get_im_session() == mock_session
            assert get_im_gateway() == mock_gateway
        finally:
            reset_im_context(tokens)

    def test_reset_restores_previous(self):
        tokens = set_im_context(session="s1", gateway="g1")
        reset_im_context(tokens)
        session = get_im_session()
        assert session is None or session != "s1"

    def test_nested_contexts(self):
        t1 = set_im_context(session="outer", gateway="outer_gw")
        assert get_im_session() == "outer"

        t2 = set_im_context(session="inner", gateway="inner_gw")
        assert get_im_session() == "inner"

        reset_im_context(t2)
        assert get_im_session() == "outer"
        reset_im_context(t1)
