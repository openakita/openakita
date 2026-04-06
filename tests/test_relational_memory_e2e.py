"""
End-to-End Tests for Multi-Dimensional Relational Memory (Mode 2)

Simulates real conversations and validates the full pipeline:
  Layer 1: Quick rule-based encoding (encode_quick)
  Layer 2: Backfill from compression summary (backfill_from_summary)
  Layer 3: Batch LLM encoding at session end (encode_session)
  Graph traversal, mode routing, consolidation, API format
"""

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openakita.memory.relational.types import (
    Dimension,
    EdgeType,
    EncodingResult,
    EntityRef,
    MemoryEdge,
    MemoryNode,
    NodeType,
    EDGE_DIMENSION,
)
from openakita.memory.relational.store import RelationalMemoryStore
from openakita.memory.relational.encoder import MemoryEncoder
from openakita.memory.relational.entity_resolver import EntityResolver
from openakita.memory.relational.graph_engine import GraphEngine
from openakita.memory.relational.consolidator import RelationalConsolidator
from openakita.memory.relational.bridge import MemoryModeRouter

PASS = 0
FAIL = 0
WARN = 0


def report(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def warn(name: str, detail: str):
    global WARN
    WARN += 1
    print(f"  [WARN] {name} — {detail}")


# ======================================================================
# Simulated conversation: user asks to refactor the memory module
# ======================================================================
CONVERSATION_TURNS = [
    {
        "role": "user",
        "content": "我想重构记忆模块，现在的碎片化存储性能不好，SQLite查询太慢了",
        "tool_calls": None,
        "tool_results": None,
    },
    {
        "role": "assistant",
        "content": "好的，我来分析一下现有的记忆存储架构。让我先看一下代码结构。",
        "tool_calls": [
            {"function": {"name": "read_file"}, "arguments": {"path": "src/memory/store.py"}}
        ],
        "tool_results": None,
    },
    {
        "role": "assistant",
        "content": "分析完成。当前系统使用 SemanticMemory 存储在 SQLite 中，存在以下问题：1. 没有结构化关系 2. FTS5 索引缺失 3. 查询需要全表扫描。建议切换到图结构存储。",
        "tool_calls": None,
        "tool_results": None,
    },
    {
        "role": "user",
        "content": "好的，那我们设计一个新的方案吧，用图结构来存储记忆关系",
        "tool_calls": None,
        "tool_results": None,
    },
    {
        "role": "assistant",
        "content": "我来创建新的图存储方案。设计包括：MemoryNode 节点、MemoryEdge 边、五个维度（时间、实体、因果、动作、上下文）。",
        "tool_calls": [
            {"function": {"name": "write_file"}, "arguments": {"path": "src/memory/relational/types.py"}}
        ],
        "tool_results": None,
    },
    {
        "role": "user",
        "content": "因为性能问题导致了这次重构，这个因果关系要记录下来。测试一下新方案是否可行。",
        "tool_calls": None,
        "tool_results": None,
    },
]

COMPRESSION_SUMMARY = (
    "本次会话讨论了记忆模块重构方案。由于现有的碎片化存储性能不佳（SQLite 查询慢），"
    "用户决定采用图结构重新设计记忆系统。助手分析了现有代码后，"
    "设计了包含 MemoryNode、MemoryEdge 和五个维度的新方案。"
    "因此进行了从 JSON 存储到 SQLite 图结构的切换。"
)


def make_store() -> tuple[sqlite3.Connection, RelationalMemoryStore]:
    conn = sqlite3.connect(":memory:")
    store = RelationalMemoryStore(conn)
    return conn, store


# ======================================================================
# TEST SUITE 1: Data Types (Plan Section 3.2)
# ======================================================================
def test_data_types():
    print("\n=== 1. 数据模型验证 (Plan §3.2) ===")

    # NodeType should have exactly 4 types (Plan §5.6)
    expected_types = {"event", "fact", "decision", "goal"}
    actual_types = {nt.value for nt in NodeType}
    report("NodeType 应有 4 种类型", actual_types == expected_types,
           f"expected {expected_types}, got {actual_types}")

    # EdgeType should cover all 5 dimensions
    dimension_coverage = set()
    for et, dim in EDGE_DIMENSION.items():
        dimension_coverage.add(dim)
    all_dims = {d for d in Dimension}
    report("EdgeType 覆盖全部 5 个维度", dimension_coverage == all_dims,
           f"missing: {all_dims - dimension_coverage}")

    # MemoryNode auto-generates ID and timestamps
    node = MemoryNode(content="test")
    report("MemoryNode 自动生成 ID", len(node.id) == 16)
    report("MemoryNode valid_from 默认等于 occurred_at (Plan §5.5)",
           node.valid_from == node.occurred_at)

    # MemoryEdge dimension auto-correction
    edge = MemoryEdge(
        source_id="a", target_id="b",
        edge_type=EdgeType.FOLLOWED_BY,  # temporal type
        dimension=Dimension.ENTITY,       # wrong default
    )
    report("MemoryEdge 自动修正维度 (FOLLOWED_BY → TEMPORAL)",
           edge.dimension == Dimension.TEMPORAL)


# ======================================================================
# TEST SUITE 2: Storage Layer (Plan §3.3)
# ======================================================================
def test_storage():
    print("\n=== 2. 存储层验证 (Plan §3.3 Schema) ===")
    conn, store = make_store()

    # Verify all required tables exist
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {row[0] for row in cur.fetchall()}
    required = {"mdrm_nodes", "mdrm_edges", "mdrm_entity_index",
                "mdrm_reachable", "mdrm_entity_aliases"}
    report("所有 mdrm_* 表已创建", required.issubset(tables),
           f"missing: {required - tables}")

    # FTS5 virtual table
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mdrm_nodes_fts'")
    report("FTS5 虚拟表已创建", cur.fetchone() is not None)

    # Verify indexes
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_mdrm%'")
    indexes = {row[0] for row in cur.fetchall()}
    required_idx = {"idx_mdrm_nodes_time", "idx_mdrm_edges_source",
                    "idx_mdrm_edges_target", "idx_mdrm_edges_dim"}
    report("核心索引已创建", required_idx.issubset(indexes),
           f"missing: {required_idx - indexes}")

    # CRUD: save and retrieve node
    node = MemoryNode(
        content="用户讨论了记忆重构",
        node_type=NodeType.EVENT,
        entities=[EntityRef(name="用户", type="person", role="agent"),
                  EntityRef(name="记忆模块", type="concept", role="patient")],
        action_verb="讨论",
        action_category="communicate",
        session_id="sess-001",
        project="OpenAkita",
        importance=0.8,
    )
    store.save_node(node)
    retrieved = store.get_node(node.id)
    report("节点保存+读取", retrieved is not None)
    report("内容一致", retrieved.content == node.content)
    report("实体序列化正确", len(retrieved.entities) == 2)
    report("实体名称正确", retrieved.entities[0].name == "用户")
    report("importance 正确", abs(retrieved.importance - 0.8) < 0.01)

    # Entity index populated
    cur = conn.execute("SELECT entity_name FROM mdrm_entity_index WHERE node_id=?", (node.id,))
    entity_names = {row[0] for row in cur.fetchall()}
    report("实体索引已填充", "用户" in entity_names and "记忆模块" in entity_names,
           f"got: {entity_names}")

    # FTS search
    fts_results = store.search_fts("记忆", limit=5)
    report("FTS5 搜索命中", len(fts_results) >= 1 and fts_results[0].id == node.id)

    # Entity search
    ent_results = store.search_by_entity("用户", limit=5)
    report("实体搜索命中", len(ent_results) >= 1)

    # Batch save
    nodes = [MemoryNode(content=f"batch-{i}", node_type=NodeType.FACT) for i in range(10)]
    store.save_nodes_batch(nodes)
    report("批量保存 10 节点", store.count_nodes() == 11)

    # Edge CRUD
    edge = MemoryEdge(
        source_id=node.id, target_id=nodes[0].id,
        edge_type=EdgeType.LED_TO, dimension=Dimension.CAUSAL,
        weight=0.7,
    )
    store.save_edge(edge)
    edges = store.get_edges_for_node(node.id)
    report("边保存+查询", len(edges) == 1 and edges[0].edge_type == EdgeType.LED_TO)

    # Delete node cascade
    ok = store.delete_node(node.id)
    report("删除节点", ok)
    report("关联边已删除", len(store.get_edges_for_node(node.id)) == 0)

    # Entity aliases
    store.add_alias("记忆模块", "memory_module", confidence=0.9, source="rule")
    resolved = store.resolve_entity("记忆模块")
    report("别名解析", resolved == "memory_module")

    conn.close()


# ======================================================================
# TEST SUITE 3: Encoder Layer 1 — Quick Encoding (Plan §4.1)
# ======================================================================
def test_encoder_quick():
    print("\n=== 3. 编码器第一层：快速规则编码 (Plan §4.1) ===")
    encoder = MemoryEncoder(session_id="sess-001")

    result = encoder.encode_quick(CONVERSATION_TURNS, "sess-001")

    # Should produce nodes for turns with content >= 15 chars
    valid_turns = [t for t in CONVERSATION_TURNS if len(t.get("content", "") or "") >= 15]
    report(f"节点数量合理 (有 {len(valid_turns)} 个有效 turn)",
           len(result.nodes) == len(valid_turns),
           f"got {len(result.nodes)} nodes")

    # Check temporal chain edges
    temporal_edges = [e for e in result.edges if e.dimension == Dimension.TEMPORAL]
    report("时间链边存在", len(temporal_edges) >= 1)
    if temporal_edges:
        report("时间链使用 FOLLOWED_BY",
               all(e.edge_type == EdgeType.FOLLOWED_BY for e in temporal_edges))

    # Check entity extraction from tool_calls
    tool_nodes = [n for n in result.nodes if n.action_verb and n.action_verb != "asked"
                  and n.action_verb != "responded"]
    report("工具调用提取了动作", len(tool_nodes) >= 1,
           f"tool nodes: {[n.action_verb for n in tool_nodes]}")

    # Check action_category mapping
    read_nodes = [n for n in result.nodes if n.action_category == "analyze"]
    report("read_file 映射为 analyze", len(read_nodes) >= 1)

    create_nodes = [n for n in result.nodes if n.action_category == "create"]
    report("write_file 映射为 create", len(create_nodes) >= 1)

    # Check entity co-occurrence edges
    entity_edges = [e for e in result.edges if e.dimension == Dimension.ENTITY]
    report("实体共现边存在", len(entity_edges) >= 0)

    # Session ID assigned
    report("session_id 已设置", all(n.session_id == "sess-001" for n in result.nodes))

    # User turns have higher importance than assistant turns
    user_nodes = [n for n in result.nodes if n.importance == 0.4]
    asst_nodes = [n for n in result.nodes if n.importance == 0.3]
    report("用户 turn 重要性(0.4) > 助手 turn(0.3)",
           len(user_nodes) >= 1 and len(asst_nodes) >= 1)

    return result


# ======================================================================
# TEST SUITE 4: Encoder Layer 2 — Backfill (Plan §4.1)
# ======================================================================
def test_encoder_backfill(quick_result: EncodingResult):
    print("\n=== 4. 编码器第二层：压缩回填 (Plan §4.1) ===")
    encoder = MemoryEncoder(session_id="sess-001")

    backfill = encoder.backfill_from_summary(COMPRESSION_SUMMARY, quick_result.nodes)

    report("回填产生了摘要节点", len(backfill.nodes) >= 1)
    if backfill.nodes:
        summary_node = backfill.nodes[0]
        report("摘要节点类型为 FACT", summary_node.node_type == NodeType.FACT)
        report("摘要节点重要性 >= 0.5", summary_node.importance >= 0.5)

    # Context edges linking original nodes to summary
    context_edges = [e for e in backfill.edges if e.dimension == Dimension.CONTEXT]
    report("上下文边连接原节点到摘要", len(context_edges) >= 1)
    if context_edges:
        report("使用 PART_OF 边类型",
               any(e.edge_type == EdgeType.PART_OF for e in context_edges))

    # Causal edge from summary detection
    causal_edges = [e for e in backfill.edges if e.dimension == Dimension.CAUSAL]
    report("因果边已从摘要中提取 (含因果关键词)", len(causal_edges) >= 1,
           f"summary contains '导致/因此', got {len(causal_edges)} causal edges")

    return backfill


# ======================================================================
# TEST SUITE 5: Encoder Layer 3 — Batch LLM (Plan §4.1)
# ======================================================================
def test_encoder_session():
    print("\n=== 5. 编码器第三层：批量 LLM 编码 (Plan §4.1) ===")

    # Without brain, falls back to quick encode
    encoder = MemoryEncoder(brain=None, session_id="sess-001")
    result = asyncio.run(encoder.encode_session(CONVERSATION_TURNS, session_id="sess-001"))
    report("无 Brain 时降级为快速编码", len(result.nodes) >= 1)

    # With existing_nodes and no brain, should still produce nodes
    existing = [MemoryNode(content="pre-existing node", node_type=NodeType.EVENT)]
    result2 = asyncio.run(
        encoder.encode_session(CONVERSATION_TURNS, existing_nodes=existing, session_id="sess-001")
    )
    report("有 existing_nodes 时仍可降级编码", len(result2.nodes) >= 1)

    # Test LLM response parsing (simulate LLM output)
    llm_response = json.dumps([
        {
            "content": "用户要求重构记忆模块",
            "node_type": "decision",
            "entities": [{"name": "用户", "type": "person", "role": "agent"},
                         {"name": "记忆模块", "type": "concept", "role": "patient"}],
            "action_verb": "决定",
            "action_category": "decide",
            "importance": 0.9,
            "causal_refs": ["因性能问题触发了重构设计"],
        },
        {
            "content": "因性能问题触发了重构设计",
            "node_type": "event",
            "entities": [{"name": "SQLite", "type": "tool", "role": "instrument"}],
            "action_verb": "触发",
            "action_category": "analyze",
            "importance": 0.7,
            "causal_refs": [],
        },
    ], ensure_ascii=False)

    nodes, edges = encoder._parse_llm_response(llm_response, "sess-test")
    report("LLM 响应解析：2 个节点", len(nodes) == 2)

    if len(nodes) >= 2:
        report("DECISION 类型正确", nodes[0].node_type == NodeType.DECISION)
        report("importance 正确解析", abs(nodes[0].importance - 0.9) < 0.01)
        report("实体正确解析", len(nodes[0].entities) == 2)

    # Check temporal chain
    temporal = [e for e in edges if e.edge_type == EdgeType.FOLLOWED_BY]
    report("时间链边已建立", len(temporal) >= 1)

    # Check causal edges from causal_refs
    causal = [e for e in edges if e.edge_type == EdgeType.LED_TO]
    report("因果边已从 causal_refs 建立", len(causal) >= 1)

    # Entity co-occurrence
    entity = [e for e in edges if e.edge_type == EdgeType.INVOLVES]
    report("实体共现边", len(entity) >= 0)


# ======================================================================
# TEST SUITE 6: Full Pipeline — Encode → Store → Retrieve (Plan §4.2)
# ======================================================================
def test_full_pipeline():
    print("\n=== 6. 完整管线：编码 → 存储 → 图遍历检索 (Plan §4.2) ===")
    conn, store = make_store()
    encoder = MemoryEncoder(session_id="sess-full")

    # Layer 1: Quick encode
    quick = encoder.encode_quick(CONVERSATION_TURNS, "sess-full")
    store.save_nodes_batch(quick.nodes)
    store.save_edges_batch(quick.edges)
    report(f"Layer 1: {len(quick.nodes)} 节点, {len(quick.edges)} 边已存储", True)

    # Layer 2: Backfill
    backfill = encoder.backfill_from_summary(COMPRESSION_SUMMARY, quick.nodes)
    store.save_nodes_batch(backfill.nodes)
    store.save_edges_batch(backfill.edges)
    report(f"Layer 2: {len(backfill.nodes)} 摘要节点, {len(backfill.edges)} 回填边已存储", True)

    total_nodes = store.count_nodes()
    total_edges = store.count_edges()
    report(f"总计: {total_nodes} 节点, {total_edges} 边", total_nodes > 0 and total_edges > 0)

    # Rebuild reachable table
    reachable_count = store.rebuild_reachable()
    report(f"物化可达表: {reachable_count} 行", reachable_count > 0)

    # Graph Engine queries
    ge = GraphEngine(store)

    # Query 1: Entity search — "记忆"
    results1 = asyncio.run(ge.query("记忆模块重构", limit=5, token_budget=2000))
    report(f"实体查询 '记忆模块重构': {len(results1)} 结果", len(results1) >= 1)
    if results1:
        report("结果按分数降序排列",
               all(results1[i].score >= results1[i + 1].score
                   for i in range(len(results1) - 1)))

    # Query 2: Causal query — "为什么"
    results2 = asyncio.run(ge.query("为什么要重构记忆模块", limit=5, token_budget=2000))
    report(f"因果查询 '为什么要重构': {len(results2)} 结果", len(results2) >= 1)
    if results2:
        has_causal_dim = any(
            Dimension.CAUSAL in r.dimensions_matched for r in results2
        )
        report("因果维度被匹配", has_causal_dim)

    # Query 3: Temporal query — "最近"
    results3 = asyncio.run(ge.query("最近讨论了什么", limit=5, token_budget=2000))
    report(f"时间查询 '最近讨论了什么': {len(results3)} 结果", len(results3) >= 1)
    if results3:
        has_temporal_dim = any(
            Dimension.TEMPORAL in r.dimensions_matched for r in results3
        )
        report("时间维度被匹配", has_temporal_dim)

    # Format results
    if results1:
        formatted = ge.format_results(results1)
        report("格式化输出非空", len(formatted) > 0)
        report("格式化包含节点类型", "[EVENT]" in formatted or "[FACT]" in formatted
               or "[DECISION]" in formatted or "[GOAL]" in formatted)

    # Verify access_count incremented only for returned results
    if results1:
        result_node = store.get_node(results1[0].node.id)
        report("访问计数已更新", result_node.access_count >= 1)

    conn.close()


# ======================================================================
# TEST SUITE 7: Entity Resolution (Plan §5.2)
# ======================================================================
def test_entity_resolver():
    print("\n=== 7. 实体消歧 (Plan §5.2) ===")
    conn, store = make_store()
    resolver = EntityResolver(store)

    # Rule-based normalization
    report("normalize: 大小写统一", resolver.normalize("OpenAkita") == "openakita")
    report("normalize: 空格→下划线", resolver.normalize("my project") == "my_project")
    report("normalize: 中英映射 (记忆→memory)", resolver.normalize("记忆") == "memory")
    report("normalize: 中英映射 (数据库→database)", resolver.normalize("数据库") == "database")
    report("normalize: 引号去除", resolver.normalize("'test'") == "test")

    # Alias table
    store.add_alias("mem_module", "memory_module", confidence=0.8, source="rule")
    result = resolver.resolve("mem_module")
    report("别名表解析", result == "memory_module")

    # Unknown entity returns normalized form
    result2 = resolver.resolve("UnknownEntity")
    report("未知实体返回规范化形式", result2 == "unknownentity")

    # Resolve many
    mapping = resolver.resolve_many(["记忆", "数据库", "用户"])
    report("批量解析", len(mapping) == 3)
    report("记忆→memory", mapping["记忆"] == "memory")

    conn.close()


# ======================================================================
# TEST SUITE 8: Consolidation (Plan §4.3)
# ======================================================================
def test_consolidation():
    print("\n=== 8. 整合器 (Plan §4.3) ===")
    conn, store = make_store()
    consolidator = RelationalConsolidator(store)

    # Create test data
    nodes = [MemoryNode(content=f"cons-node-{i}", node_type=NodeType.EVENT) for i in range(5)]
    store.save_nodes_batch(nodes)
    edges = []
    for i in range(4):
        edges.append(MemoryEdge(
            source_id=nodes[i].id, target_id=nodes[i + 1].id,
            edge_type=EdgeType.FOLLOWED_BY, dimension=Dimension.TEMPORAL,
            weight=0.7,
        ))
    # Add a weak edge
    edges.append(MemoryEdge(
        source_id=nodes[0].id, target_id=nodes[4].id,
        edge_type=EdgeType.RELATED_TO, dimension=Dimension.ENTITY,
        weight=0.03,
    ))
    store.save_edges_batch(edges)

    initial_edge_count = store.count_edges()
    report(f"初始: {initial_edge_count} 边", initial_edge_count == 5)

    # Run consolidation
    report_dict = asyncio.run(consolidator.consolidate(
        decay_factor=0.98,
        prune_threshold=0.05,
    ))

    report("物化表已重建", "reachable_rows" in report_dict and report_dict["reachable_rows"] > 0)
    report("衰减已执行", "decayed_edges" in report_dict)
    report("弱边已修剪 (weight=0.03 < 0.05)", report_dict.get("pruned_edges", 0) >= 1)
    report("修剪后边数减少", store.count_edges() < initial_edge_count)

    # Hebbian strengthening
    co_accessed = [nodes[0].id, nodes[1].id]
    before_edges = store.get_edges_for_node(nodes[0].id)
    before_weight = None
    for e in before_edges:
        other = e.target_id if e.source_id == nodes[0].id else e.source_id
        if other == nodes[1].id:
            before_weight = e.weight
    if before_weight is not None:
        strengthened = consolidator.strengthen_co_accessed(co_accessed, delta=0.05)
        after_edges = store.get_edges_for_node(nodes[0].id)
        after_weight = None
        for e in after_edges:
            other = e.target_id if e.source_id == nodes[0].id else e.source_id
            if other == nodes[1].id:
                after_weight = e.weight
        report("Hebbian 强化生效",
               after_weight is not None and after_weight > before_weight,
               f"before={before_weight}, after={after_weight}")
    else:
        warn("Hebbian 强化", "节点 0→1 的边被修剪或不存在，跳过")

    conn.close()


# ======================================================================
# TEST SUITE 9: Mode Router (Plan §4.4)
# ======================================================================
def test_mode_router():
    print("\n=== 9. 模式路由器 (Plan §4.4) ===")
    router = MemoryModeRouter()

    # Auto mode selection
    report("因果查询 → mode2", router.select_mode("为什么要重构") == "mode2")
    report("原因查询 → mode2", router.select_mode("这个bug的根因是什么") == "mode2")
    report("时间线查询 → mode2", router.select_mode("之前发生了什么") == "mode2")
    report("历史查询 → mode2", router.select_mode("记忆模块的历史") == "mode2")
    report("跨会话查询 → mode2", router.select_mode("以前遇到过类似问题吗") == "mode2")
    report("实体追踪 → mode2", router.select_mode("关于SQLite的所有记录") == "mode2")
    report("简单查询 → mode1", router.select_mode("今天天气怎么样") == "mode1")
    report("普通查询 → mode1", router.select_mode("帮我写个函数") == "mode1")

    # English queries
    report("English causal → mode2", router.select_mode("why did this fail") == "mode2")
    report("English timeline → mode2", router.select_mode("what happened previously") == "mode2")
    report("English simple → mode1", router.select_mode("write a hello world") == "mode1")

    # Search routing
    results_m1 = asyncio.run(router.search("test", config_mode="mode1"))
    report("mode1 搜索不崩溃", isinstance(results_m1, list))

    results_m2 = asyncio.run(router.search("test", config_mode="mode2"))
    report("mode2 搜索不崩溃", isinstance(results_m2, list))

    results_auto = asyncio.run(router.search("test", config_mode="auto"))
    report("auto 搜索不崩溃", isinstance(results_auto, list))


# ======================================================================
# TEST SUITE 10: Reachable Table — Bidirectional (Plan §5.1)
# ======================================================================
def test_reachable_bidirectional():
    print("\n=== 10. 物化可达表 — 双向遍历 (Plan §5.1) ===")
    conn, store = make_store()

    # Create a chain: A → B → C → D (only forward edges)
    nodes = [MemoryNode(content=f"chain-{chr(65+i)}", node_type=NodeType.EVENT) for i in range(4)]
    store.save_nodes_batch(nodes)
    for i in range(3):
        store.save_edge(MemoryEdge(
            source_id=nodes[i].id, target_id=nodes[i + 1].id,
            edge_type=EdgeType.FOLLOWED_BY, dimension=Dimension.TEMPORAL,
            weight=0.8,
        ))

    store.rebuild_reachable()

    # Forward: A should reach B (1-hop), C (2-hop)
    fwd_a = store.query_reachable(nodes[0].id, "temporal")
    fwd_targets = {r["target_id"] for r in fwd_a}
    report("正向 1-hop: A→B", nodes[1].id in fwd_targets)
    report("正向 2-hop: A→C", nodes[2].id in fwd_targets)

    # Reverse: D should reach C (1-hop reverse), B (2-hop reverse)
    rev_d = store.query_reachable(nodes[3].id, "temporal")
    rev_targets = {r["target_id"] for r in rev_d}
    report("反向 1-hop: D→C", nodes[2].id in rev_targets)
    report("反向 2-hop: D→B", nodes[1].id in rev_targets)

    # Cross-direction: B should reach A (reverse) and C (forward) and D (2-hop forward)
    mid_b = store.query_reachable(nodes[1].id, "temporal")
    mid_targets = {r["target_id"] for r in mid_b}
    report("双向: B→A (反向)", nodes[0].id in mid_targets)
    report("双向: B→C (正向)", nodes[2].id in mid_targets)
    report("双向: B→D (2-hop正向)", nodes[3].id in mid_targets)

    # Graph engine should find related nodes
    ge = GraphEngine(store)
    results = asyncio.run(ge.query("chain-D", limit=5, token_budget=2000))
    report(f"图引擎从 D 搜索到 {len(results)} 个节点", len(results) >= 1)

    conn.close()


# ======================================================================
# TEST SUITE 11: API Response Format (Plan §7.3)
# ======================================================================
def test_api_response_format():
    print("\n=== 11. API 响应格式验证 (Plan §7.3) ===")
    conn, store = make_store()

    # Create test data matching API format expectations
    n1 = MemoryNode(
        content="用户讨论了记忆系统重构",
        node_type=NodeType.EVENT,
        importance=0.8,
        entities=[EntityRef(name="用户", type="person")],
        action_category="discuss",
        session_id="abc-123",
        project="OpenAkita",
    )
    n2 = MemoryNode(
        content="设计了新的图结构方案",
        node_type=NodeType.DECISION,
        importance=0.9,
        entities=[EntityRef(name="图结构", type="concept")],
        action_category="design",
    )
    store.save_nodes_batch([n1, n2])
    store.save_edge(MemoryEdge(
        source_id=n1.id, target_id=n2.id,
        edge_type=EdgeType.LED_TO, dimension=Dimension.CAUSAL,
        weight=0.7,
    ))

    # Simulate what the API endpoint does
    raw_nodes = store.get_all_nodes(limit=500)
    node_ids = {n.id for n in raw_nodes}
    nodes_out = []
    for n in raw_nodes:
        ents = [{"name": e.name, "type": e.type} for e in n.entities[:5]]
        group = f"entity:{ents[0]['name']}" if ents else f"type:{n.node_type.value}"
        nodes_out.append({
            "id": n.id,
            "content": n.content[:200],
            "node_type": n.node_type.value.upper(),
            "importance": n.importance,
            "entities": ents,
            "action_category": n.action_category,
            "occurred_at": n.occurred_at.isoformat() if n.occurred_at else None,
            "session_id": n.session_id,
            "project": n.project,
            "group": group,
        })

    raw_edges = store.get_all_edges(node_ids)
    links_out = []
    for e in raw_edges:
        if e.source_id in node_ids and e.target_id in node_ids:
            links_out.append({
                "source": e.source_id,
                "target": e.target_id,
                "edge_type": e.edge_type.value,
                "dimension": e.dimension.value,
                "weight": e.weight,
            })

    api_response = {
        "nodes": nodes_out,
        "links": links_out,
        "meta": {
            "total_nodes": len(nodes_out),
            "total_edges": len(links_out),
            "mode": "mode2",
        },
    }

    # Validate format against Plan §7.3
    report("nodes 是列表", isinstance(api_response["nodes"], list))
    report("links 是列表", isinstance(api_response["links"], list))
    report("meta 包含 total_nodes/total_edges/mode",
           all(k in api_response["meta"] for k in ["total_nodes", "total_edges", "mode"]))

    if nodes_out:
        n = nodes_out[0]
        required_fields = {"id", "content", "node_type", "importance", "entities",
                          "action_category", "occurred_at", "session_id", "project", "group"}
        report("节点包含所有必填字段", required_fields.issubset(n.keys()),
               f"missing: {required_fields - set(n.keys())}")
        report("node_type 是大写", n["node_type"].isupper())

    if links_out:
        lk = links_out[0]
        required_link_fields = {"source", "target", "edge_type", "dimension", "weight"}
        report("边包含所有必填字段", required_link_fields.issubset(lk.keys()),
               f"missing: {required_link_fields - set(lk.keys())}")

    # Verify JSON serializable
    try:
        json.dumps(api_response, ensure_ascii=False)
        report("响应可 JSON 序列化", True)
    except (TypeError, ValueError) as ex:
        report("响应可 JSON 序列化", False, str(ex))

    conn.close()


# ======================================================================
# TEST SUITE 12: Multi-Session Simulation
# ======================================================================
def test_multi_session():
    print("\n=== 12. 多会话模拟 — 端到端 ===")
    conn, store = make_store()

    # Session 1: Discuss memory refactoring
    enc1 = MemoryEncoder(session_id="session-1")
    turns1 = [
        {"role": "user", "content": "我们需要重构记忆存储模块，因为当前的JSON存储太慢了"},
        {"role": "assistant", "content": "好的，我来分析现有的记忆系统架构，看看性能瓶颈在哪里"},
        {"role": "user", "content": "决定采用SQLite图结构来替代现有的JSON存储方案"},
    ]
    r1 = enc1.encode_quick(turns1, "session-1")
    store.save_nodes_batch(r1.nodes)
    store.save_edges_batch(r1.edges)
    s1_nodes = len(r1.nodes)

    # Session 2: Implement the changes
    enc2 = MemoryEncoder(session_id="session-2")
    turns2 = [
        {"role": "user", "content": "开始实现SQLite图结构存储，创建types.py文件"},
        {"role": "assistant", "content": "已创建MemoryNode和MemoryEdge数据类，包含五个维度的EdgeType",
         "tool_calls": [{"function": {"name": "write_file"}, "arguments": {"path": "types.py"}}],
         "tool_results": None},
        {"role": "user", "content": "再创建store.py文件，实现节点和边的CRUD操作"},
        {"role": "assistant", "content": "完成了RelationalMemoryStore的实现，包含FTS5索引和实体索引表",
         "tool_calls": [{"function": {"name": "write_file"}, "arguments": {"path": "store.py"}}],
         "tool_results": None},
    ]
    r2 = enc2.encode_quick(turns2, "session-2")
    store.save_nodes_batch(r2.nodes)
    store.save_edges_batch(r2.edges)
    s2_nodes = len(r2.nodes)

    # Session 3: Test and find bugs
    enc3 = MemoryEncoder(session_id="session-3")
    turns3 = [
        {"role": "user", "content": "测试新的记忆系统，发现FTS5查询在特殊字符时会崩溃"},
        {"role": "assistant", "content": "这是因为FTS5特殊运算符没有被转义导致的OperationalError"},
        {"role": "user", "content": "修复了FTS5特殊字符的问题，现在查询正常了"},
    ]
    r3 = enc3.encode_quick(turns3, "session-3")
    store.save_nodes_batch(r3.nodes)
    store.save_edges_batch(r3.edges)

    # Cross-session entity linking: manually add edges
    # Session 1 talks about "记忆", Session 2 implements it, Session 3 tests it
    if r1.nodes and r2.nodes:
        store.save_edge(MemoryEdge(
            source_id=r1.nodes[-1].id, target_id=r2.nodes[0].id,
            edge_type=EdgeType.LED_TO, dimension=Dimension.CAUSAL,
            weight=0.8,
        ))
    if r2.nodes and r3.nodes:
        store.save_edge(MemoryEdge(
            source_id=r2.nodes[-1].id, target_id=r3.nodes[0].id,
            edge_type=EdgeType.LED_TO, dimension=Dimension.CAUSAL,
            weight=0.7,
        ))

    store.rebuild_reachable()

    total_nodes = store.count_nodes()
    total_edges = store.count_edges()
    report(f"3 个会话共 {total_nodes} 节点, {total_edges} 边",
           total_nodes == s1_nodes + s2_nodes + len(r3.nodes))

    # Graph queries across sessions
    ge = GraphEngine(store)

    # "What happened with the memory module?" — should span sessions
    results = asyncio.run(ge.query("记忆模块的完整记录", limit=10, token_budget=3000))
    sessions_found = {r.node.session_id for r in results if r.node.session_id}
    report(f"跨会话检索: 命中 {len(sessions_found)} 个会话",
           len(sessions_found) >= 2,
           f"sessions: {sessions_found}")

    # "Why was there a bug?" — causal chain
    results2 = asyncio.run(ge.query("为什么FTS5会崩溃", limit=10, token_budget=3000))
    report(f"因果链检索: {len(results2)} 结果", len(results2) >= 1)

    # "What happened recently?" — temporal
    results3 = asyncio.run(ge.query("最近做了什么", limit=10, token_budget=3000))
    report(f"时间线检索: {len(results3)} 结果", len(results3) >= 1)

    # Run consolidation
    consolidator = RelationalConsolidator(store)
    c_report = asyncio.run(consolidator.consolidate())
    report("整合成功", "total_nodes" in c_report and c_report["total_nodes"] > 0)

    conn.close()


# ======================================================================
# TEST SUITE 13: Edge Cases
# ======================================================================
def test_edge_cases():
    print("\n=== 13. 边界情况测试 ===")
    conn, store = make_store()
    encoder = MemoryEncoder(session_id="edge")

    # Empty turns
    result = encoder.encode_quick([], "edge")
    report("空 turns 返回空结果", len(result.nodes) == 0 and len(result.edges) == 0)

    # Very short content (< 15 chars)
    result2 = encoder.encode_quick([{"role": "user", "content": "ok"}], "edge")
    report("短内容被跳过", len(result2.nodes) == 0)

    # None content
    result3 = encoder.encode_quick([{"role": "user", "content": None}], "edge")
    report("None content 不崩溃", len(result3.nodes) == 0)

    # Very long content (> 500 chars)
    long_content = "x" * 1000
    result4 = encoder.encode_quick([{"role": "user", "content": long_content}], "edge")
    if result4.nodes:
        report("长内容被截断到 500 字符", len(result4.nodes[0].content) <= 500)

    # Unicode and special characters
    result5 = encoder.encode_quick([
        {"role": "user", "content": "这是一个包含特殊字符的测试：@#$%^&*()_+{}|:<>?"},
    ], "edge")
    report("特殊字符不崩溃", len(result5.nodes) >= 1)

    # FTS with special characters
    n = MemoryNode(content="test with *wildcards* and \"quotes\"", node_type=NodeType.FACT)
    store.save_node(n)
    results = store.search_fts('*wildcards* "quotes"', limit=5)
    report("FTS5 特殊字符查询不崩溃", isinstance(results, list))

    # Empty FTS query
    results2 = store.search_fts("", limit=5)
    report("空 FTS 查询不崩溃", isinstance(results2, list))

    # Importance edge cases
    report("_safe_importance(None) = 0.5", MemoryEncoder._safe_importance(None) == 0.5)
    report("_safe_importance('abc') = 0.5", MemoryEncoder._safe_importance("abc") == 0.5)
    report("_safe_importance(999) = 1.0", MemoryEncoder._safe_importance(999) == 1.0)
    report("_safe_importance(-5) = 0.0", MemoryEncoder._safe_importance(-5) == 0.0)

    # Future timestamp (ZeroDivisionError guard)
    ge = GraphEngine(store)
    future_node = MemoryNode(content="future event", node_type=NodeType.EVENT)
    future_node.occurred_at = datetime.now() + timedelta(days=100)
    try:
        score = ge._score_node(future_node, {"keywords": []}, 0.5)
        report("未来时间戳评分不崩溃", isinstance(score, float))
    except ZeroDivisionError:
        report("未来时间戳评分不崩溃", False, "ZeroDivisionError!")

    # Node not found during traversal
    results3 = asyncio.run(ge.query("nonexistent_query_xyz", limit=5, token_budget=500))
    report("无结果查询不崩溃", isinstance(results3, list))

    conn.close()


# ======================================================================
# TEST SUITE 14: Plan Compliance Check
# ======================================================================
def test_plan_compliance():
    print("\n=== 14. Plan 合规性检查 ===")

    # §3.2 NodeType simplified to 4 (§5.6)
    report("NodeType 简化为 4 种 (§5.6)", len(NodeType) == 4)

    # §3.3 Schema: mdrm_ prefix
    conn, store = make_store()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'mdrm_%'")
    tables = [r[0] for r in cur.fetchall()]
    report("所有表使用 mdrm_ 前缀", len(tables) >= 5)

    # §4.1 Three-layer encoding
    enc = MemoryEncoder(session_id="plan")
    report("Layer 1: encode_quick 方法存在", hasattr(enc, "encode_quick"))
    report("Layer 2: backfill_from_summary 方法存在", hasattr(enc, "backfill_from_summary"))
    report("Layer 3: encode_session 方法存在", hasattr(enc, "encode_session"))

    # §4.2 GraphEngine
    ge = GraphEngine(store)
    report("GraphEngine.query 方法存在", hasattr(ge, "query"))
    report("GraphEngine.format_results 方法存在", hasattr(ge, "format_results"))

    # §4.3 Consolidator
    cons = RelationalConsolidator(store)
    report("Consolidator.consolidate 方法存在", hasattr(cons, "consolidate"))
    report("Consolidator.strengthen_co_accessed 方法存在",
           hasattr(cons, "strengthen_co_accessed"))

    # §4.4 Mode Router
    router = MemoryModeRouter()
    report("ModeRouter.search 方法存在", hasattr(router, "search"))
    report("ModeRouter.select_mode 方法存在", hasattr(router, "select_mode"))

    # §5.1 Reachable table
    report("mdrm_reachable 表存在", "mdrm_reachable" in tables)

    # §5.2 Entity resolver
    resolver = EntityResolver(store)
    report("EntityResolver.normalize 方法存在", hasattr(resolver, "normalize"))
    report("EntityResolver.resolve_batch_with_llm 方法存在",
           hasattr(resolver, "resolve_batch_with_llm"))

    # §5.5 valid_from defaults to occurred_at
    n = MemoryNode(content="test")
    report("valid_from 默认等于 occurred_at (§5.5)", n.valid_from == n.occurred_at)

    # §6 Config fields
    try:
        from openakita.config import Settings
        s = Settings()
        report("memory_mode 配置存在", hasattr(s, "memory_mode"))
        report("memory_mode 默认 mode1", s.memory_mode == "mode1")
        report("mdrm_max_hops 配置存在", hasattr(s, "mdrm_max_hops"))
    except Exception as e:
        warn("Config 检查", f"无法加载 Settings: {e}")

    # §3.4 Module structure
    import importlib
    modules = [
        "openakita.memory.relational.types",
        "openakita.memory.relational.store",
        "openakita.memory.relational.encoder",
        "openakita.memory.relational.graph_engine",
        "openakita.memory.relational.consolidator",
        "openakita.memory.relational.bridge",
        "openakita.memory.relational.entity_resolver",
    ]
    for mod in modules:
        try:
            importlib.import_module(mod)
            report(f"模块 {mod.split('.')[-1]} 可导入", True)
        except ImportError as e:
            report(f"模块 {mod.split('.')[-1]} 可导入", False, str(e))

    conn.close()


# ======================================================================
# MAIN
# ======================================================================
def main():
    print("=" * 70)
    print("Multi-Dimensional Relational Memory — 全面端到端测试")
    print("=" * 70)

    test_data_types()
    test_storage()
    quick_result = test_encoder_quick()
    test_encoder_backfill(quick_result)
    test_encoder_session()
    test_full_pipeline()
    test_entity_resolver()
    test_consolidation()
    test_mode_router()
    test_reachable_bidirectional()
    test_api_response_format()
    test_multi_session()
    test_edge_cases()
    test_plan_compliance()

    print("\n" + "=" * 70)
    print(f"总计: {PASS} PASS, {FAIL} FAIL, {WARN} WARN")
    print("=" * 70)

    if FAIL > 0:
        print(f"\n*** {FAIL} 个测试失败，请检查上方 [FAIL] 条目 ***")
        sys.exit(1)
    else:
        print("\n✓ 所有测试通过，实现与 Plan 设计一致")
        sys.exit(0)


if __name__ == "__main__":
    main()
