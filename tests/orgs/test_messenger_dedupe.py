"""OrgMessenger 内存级 send 去重测试

覆盖 5s 窗口内同 (chain_id, to_node, msg_type, content_hash) 的重复 send 应该被
直接 drop（返回 False），但允许：
  * 不同 chain_id 不被影响
  * 不同 to_node 不被影响
  * 不同内容不被影响
  * 窗口外（time.time 被 mock）允许再次发送
  * 无 chain_id 的对话性消息不启用去重

这条 dedupe 是 LLM 在同一 ReAct iter emit 多个相同 tool_use 时的兜底防线，
不能误伤合法多轮对话。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.orgs.messenger import OrgMessenger
from openakita.orgs.models import MsgType, OrgMessage


@pytest.fixture()
def messenger(org_dir: Path, persisted_org) -> OrgMessenger:
    return OrgMessenger(persisted_org, org_dir)


def _make_msg(
    to_node: str = "node_cto",
    chain_id: str | None = "chain-1",
    content: str = "hello",
    msg_type: MsgType = MsgType.TASK_ASSIGN,
) -> OrgMessage:
    metadata: dict = {}
    if chain_id is not None:
        metadata["task_chain_id"] = chain_id
    return OrgMessage(
        org_id="org_test",
        from_node="node_ceo",
        to_node=to_node,
        msg_type=msg_type,
        content=content,
        metadata=metadata,
    )


class TestMessengerSendDedupe:
    async def test_duplicate_send_within_window_drops(self, messenger: OrgMessenger):
        msg1 = _make_msg()
        msg2 = _make_msg()
        ok1 = await messenger.send(msg1)
        ok2 = await messenger.send(msg2)
        assert ok1 is True
        assert ok2 is False
        mb = messenger.get_mailbox("node_cto")
        assert mb is not None
        assert mb.pending_count == 1

    async def test_different_chain_not_deduped(self, messenger: OrgMessenger):
        ok1 = await messenger.send(_make_msg(chain_id="chain-A"))
        ok2 = await messenger.send(_make_msg(chain_id="chain-B"))
        assert ok1 is True
        assert ok2 is True
        mb = messenger.get_mailbox("node_cto")
        assert mb.pending_count == 2

    async def test_different_to_node_not_deduped(self, messenger: OrgMessenger):
        ok1 = await messenger.send(_make_msg(to_node="node_cto"))
        ok2 = await messenger.send(_make_msg(to_node="node_dev"))
        assert ok1 is True
        assert ok2 is True

    async def test_different_content_not_deduped(self, messenger: OrgMessenger):
        ok1 = await messenger.send(_make_msg(content="A"))
        ok2 = await messenger.send(_make_msg(content="B"))
        assert ok1 is True
        assert ok2 is True

    async def test_no_chain_id_not_deduped(self, messenger: OrgMessenger):
        # 无 chain_id 的对话性消息（LLM 主动 question）应该走原行为，
        # 不应被新 dedupe 误伤。
        ok1 = await messenger.send(_make_msg(chain_id=None))
        ok2 = await messenger.send(_make_msg(chain_id=None))
        assert ok1 is True
        assert ok2 is True

    async def test_window_expiry_allows_resend(self, messenger: OrgMessenger):
        # 第一次正常发送
        ok1 = await messenger.send(_make_msg())
        assert ok1 is True
        # 把 _recent_send_keys 内的时间戳手动倒退到窗口外
        for k in list(messenger._recent_send_keys.keys()):
            messenger._recent_send_keys[k] = messenger._recent_send_keys[k] - 100.0
        ok2 = await messenger.send(_make_msg())
        assert ok2 is True
        mb = messenger.get_mailbox("node_cto")
        assert mb is not None and mb.pending_count == 2
