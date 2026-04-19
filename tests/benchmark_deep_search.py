#!/usr/bin/env python3
"""
Deep Search Benchmark Test Suite

Tests the deep_search module against various benchmark scenarios:
1. Single provider (Tavily only)
2. Single provider (Exa only) — requires EXA_API_KEY
3. Multi-provider (Tavily + Exa)
4. High-volume target (400 sources)
5. Content retrieval mode
6. Performance benchmarks

Usage:
  export TAVILY_API_KEY=tvly-...
  export EXA_API_KEY=exa-...   # optional
  python tests/benchmark_deep_search.py
"""

import asyncio
import os
import sys
import time
from dataclasses import dataclass

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openakita.tools.handlers.deep_search import (
    DeepSearchOrchestrator,
    DeepSearchResult,
    format_deep_results,
)


@dataclass
class BenchmarkCase:
    name: str
    query: str
    max_sources: int
    providers: list[str] | None
    include_content: bool
    expect_min_sources: int


BENCHMARK_CASES = [
    BenchmarkCase(
        name="quick_tavily",
        query="Python asyncio best practices",
        max_sources=50,
        providers=["tavily"],
        include_content=False,
        expect_min_sources=10,
    ),
    BenchmarkCase(
        name="quick_exa",
        query="transformer neural network architecture",
        max_sources=50,
        providers=["exa"],
        include_content=False,
        expect_min_sources=10,
    ),
    BenchmarkCase(
        name="multi_provider_100",
        query="large language model scaling laws",
        max_sources=100,
        providers=None,  # all available
        include_content=False,
        expect_min_sources=20,
    ),
    BenchmarkCase(
        name="deep_400_sources",
        query="artificial intelligence safety alignment research",
        max_sources=400,
        providers=None,
        include_content=False,
        expect_min_sources=50,
    ),
    BenchmarkCase(
        name="content_mode",
        query="RAG retrieval augmented generation techniques",
        max_sources=50,
        providers=["tavily"],
        include_content=True,
        expect_min_sources=10,
    ),
    BenchmarkCase(
        name="broad_topic",
        query="climate change renewable energy 2026",
        max_sources=200,
        providers=None,
        include_content=False,
        expect_min_sources=30,
    ),
]


async def run_benchmark(
    case: BenchmarkCase, orchestrator: DeepSearchOrchestrator
) -> dict:
    """Run a single benchmark case and return metrics."""
    print(f"\n{'='*60}")
    print(f"BENCHMARK: {case.name}")
    print(f"  Query: {case.query}")
    print(f"  Target sources: {case.max_sources}")
    print(f"  Providers: {case.providers or 'all available'}")
    print(f"  Include content: {case.include_content}")
    print(f"{'='*60}")

    start = time.time()
    result = await orchestrator.deep_search(
        query=case.query,
        max_sources=case.max_sources,
        providers=case.providers,
        include_content=case.include_content,
    )
    elapsed = time.time() - start

    passed = len(result.sources) >= case.expect_min_sources
    status = "PASS" if passed else "FAIL"

    print(f"\n  Results:")
    print(f"    Status: {status}")
    print(f"    Unique sources: {len(result.sources)} (expected >= {case.expect_min_sources})")
    print(f"    Raw results: {result.total_found}")
    print(f"    Duplicates removed: {result.duplicates_removed}")
    print(f"    Providers used: {result.providers_used}")
    print(f"    Queries used: {len(result.queries_used)}")
    print(f"    Time: {elapsed:.2f}s (internal: {result.elapsed_seconds}s)")

    if result.sources:
        print(f"\n  Top 5 sources:")
        for i, src in enumerate(result.sources[:5], 1):
            print(f"    [{i}] {src.title[:60]}... (score={src.relevance_score:.2f}, via={src.source_provider})")
            print(f"        {src.url[:80]}")

    return {
        "name": case.name,
        "passed": passed,
        "sources_found": len(result.sources),
        "expected_min": case.expect_min_sources,
        "raw_results": result.total_found,
        "duplicates_removed": result.duplicates_removed,
        "elapsed": round(elapsed, 2),
        "providers": result.providers_used,
    }


async def main():
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    exa_key = os.environ.get("EXA_API_KEY", "")

    print("Deep Search Benchmark Suite")
    print("=" * 60)
    print(f"  Tavily API Key: {'SET' if tavily_key else 'NOT SET'}")
    print(f"  Exa API Key: {'SET' if exa_key else 'NOT SET'}")

    if not tavily_key and not exa_key:
        print("\nERROR: At least one API key is required!")
        print("  export TAVILY_API_KEY=tvly-...")
        print("  export EXA_API_KEY=exa-...")
        sys.exit(1)

    orch = DeepSearchOrchestrator(tavily_key=tavily_key, exa_key=exa_key)
    available = orch._available_providers()
    print(f"  Available providers: {available}")

    # Filter benchmarks to only run those with available providers
    cases_to_run = []
    for case in BENCHMARK_CASES:
        needed = case.providers or available
        if all(p in available for p in needed):
            cases_to_run.append(case)
        else:
            print(f"\n  SKIP: {case.name} (missing providers: {set(needed) - set(available)})")

    if not cases_to_run:
        print("\nNo benchmark cases to run!")
        sys.exit(1)

    print(f"\n  Running {len(cases_to_run)} benchmark cases...\n")

    results = []
    for case in cases_to_run:
        result = await run_benchmark(case, orch)
        results.append(result)

    # Summary
    print(f"\n{'='*60}")
    print("BENCHMARK SUMMARY")
    print(f"{'='*60}")

    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    total_time = sum(r["elapsed"] for r in results)
    total_sources = sum(r["sources_found"] for r in results)

    print(f"\n  Cases: {len(results)} total, {passed} passed, {failed} failed")
    print(f"  Total sources discovered: {total_sources}")
    print(f"  Total time: {total_time:.2f}s")

    print(f"\n  {'Name':<25} {'Status':<8} {'Sources':<12} {'Expected':<12} {'Time':<10}")
    print(f"  {'-'*67}")
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(
            f"  {r['name']:<25} {status:<8} {r['sources_found']:<12} "
            f">={r['expected_min']:<10} {r['elapsed']:.2f}s"
        )

    if failed > 0:
        print(f"\n  {failed} case(s) FAILED — see details above")
        sys.exit(1)
    else:
        print(f"\n  All {passed} benchmarks PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
