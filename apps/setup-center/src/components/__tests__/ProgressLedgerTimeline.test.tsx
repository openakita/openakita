import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import {
  ProgressLedgerTimeline,
  type ProgressLedgerEvent,
} from "../ProgressLedgerTimeline";

interface EvOpts {
  is_progress_being_made?: boolean;
  is_in_loop?: boolean;
  is_request_satisfied?: boolean;
  next_speaker?: string;
  instruction?: string;
}

function ev(id: string, opts: EvOpts = {}): ProgressLedgerEvent {
  return {
    id,
    ts: "2026-05-18T01:23:45Z",
    is_request_satisfied: opts.is_request_satisfied ?? false,
    is_in_loop: opts.is_in_loop ?? false,
    is_progress_being_made: opts.is_progress_being_made ?? false,
    next_speaker: opts.next_speaker ?? "writer",
    instruction_or_question: opts.instruction ?? "draft the synopsis",
  };
}

describe("ProgressLedgerTimeline", () => {
  it("renders nothing when no events are present (no permanent banner)", () => {
    const { container } = render(<ProgressLedgerTimeline events={[]} />);
    expect(container.querySelector('[data-testid="progress-ledger-timeline"]')).toBeNull();
  });

  it("groups consecutive same-node events into one segment, chronological order", () => {
    const events: ProgressLedgerEvent[] = [
      ev("1", { next_speaker: "alpha", is_progress_being_made: true }),
      ev("2", { next_speaker: "beta", is_progress_being_made: true }),
      ev("3", { next_speaker: "beta", is_progress_being_made: true, instruction: "more" }),
      ev("4", { next_speaker: "gamma", is_progress_being_made: true }),
    ];
    render(<ProgressLedgerTimeline events={events} />);
    const entries = screen.getAllByTestId("progress-ledger-entry");
    // beta's two consecutive events collapse into one segment -> 3 segments.
    expect(entries).toHaveLength(3);
    expect(entries[0]).toHaveTextContent("alpha");
    expect(entries[1]).toHaveTextContent("beta");
    expect(entries[2]).toHaveTextContent("gamma");
  });

  it("renders Chinese status labels, not English badges", () => {
    const events: ProgressLedgerEvent[] = [
      ev("a", { next_speaker: "n1", is_request_satisfied: true }),
      ev("b", { next_speaker: "n2", is_in_loop: true }),
      ev("c", { next_speaker: "n3", is_progress_being_made: true }),
      ev("d", { next_speaker: "n4" }),
    ];
    // running=true so the still-"进行中" segment is NOT converged to 已完成 by
    // the idle-settle pass (which otherwise resolves dangling running rows);
    // this keeps exactly one segment per status label for the assertions.
    render(<ProgressLedgerTimeline events={events} running={true} />);
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("疑似重复")).toBeInTheDocument();
    expect(screen.getByText("进行中")).toBeInTheDocument();
    expect(screen.getByText("进展缓慢")).toBeInTheDocument();
    expect(screen.queryByText("DONE")).toBeNull();
  });

  it("maps raw node ids to display names via nodeNameOf", () => {
    const events: ProgressLedgerEvent[] = [ev("1", { next_speaker: "writer-a" })];
    render(
      <ProgressLedgerTimeline
        events={events}
        nodeNameOf={(id) => (id === "writer-a" ? "文案写手A" : id)}
      />,
    );
    expect(screen.getByText("文案写手A")).toBeInTheDocument();
  });

  it("collapses a completed segment to a summary and expands on click", () => {
    const events: ProgressLedgerEvent[] = [
      ev("1", { next_speaker: "alpha", instruction: "线索A", is_progress_being_made: true }),
    ];
    // running=false -> the segment is not the active one, so it starts collapsed.
    render(<ProgressLedgerTimeline events={events} running={false} />);
    const head = screen.getByText("alpha");
    fireEvent.click(head);
    // After expanding, the content line is shown in the lines area.
    expect(screen.getAllByText("线索A").length).toBeGreaterThan(0);
  });

  // Item 3 (2026-06): a multi-run org's rebuilt /activity mixes node segments
  // from many commands; the timeline must show only the CURRENT command.
  it("shows only the latest command's segments when commandId is set", () => {
    const events: ProgressLedgerEvent[] = [
      { ...ev("old1", { next_speaker: "oldNode", is_progress_being_made: true }), commandId: "cmd_1", ts: "1000" },
      { ...ev("new1", { next_speaker: "newNode", is_progress_being_made: true }), commandId: "cmd_2", ts: "2000" },
    ];
    // No explicit activeCommandId -> auto-select the latest command (cmd_2).
    render(<ProgressLedgerTimeline events={events} />);
    expect(screen.getByText("newNode")).toBeInTheDocument();
    expect(screen.queryByText("oldNode")).toBeNull();
  });

  it("honours an explicit activeCommandId over the latest", () => {
    const events: ProgressLedgerEvent[] = [
      { ...ev("old1", { next_speaker: "oldNode", is_progress_being_made: true }), commandId: "cmd_1", ts: "1000" },
      { ...ev("new1", { next_speaker: "newNode", is_progress_being_made: true }), commandId: "cmd_2", ts: "2000" },
    ];
    render(<ProgressLedgerTimeline events={events} activeCommandId="cmd_1" />);
    expect(screen.getByText("oldNode")).toBeInTheDocument();
    expect(screen.queryByText("newNode")).toBeNull();
  });

  // v21 time-ordered flow: a node that activates twice (with a terminal in
  // between) must render as TWO separate steps in chronological order, NOT be
  // folded back into one pinned segment.
  it("renders each node activation as its own time-ordered step", () => {
    const mk = (
      id: string,
      node: string,
      phase: ProgressLedgerEvent["phase"],
      ts: string,
      instruction: string,
    ): ProgressLedgerEvent => ({
      id,
      ts,
      is_request_satisfied: false,
      is_in_loop: false,
      is_progress_being_made: true,
      next_speaker: node,
      instruction_or_question: instruction,
      nodeId: node,
      phase,
      commandId: "cmd_1",
    });
    const events: ProgressLedgerEvent[] = [
      mk("1", "editor", "start", "1000", "主编启动并派单"),
      mk("2", "editor", "done", "1100", "主编完成首轮派发"),
      mk("3", "planner", "start", "1200", "策划编辑开工"),
      mk("4", "planner", "done", "1300", "策划编辑产出"),
      // editor acts AGAIN later -> must be a NEW step at the bottom.
      mk("5", "editor", "start", "1400", "主编再次整合"),
      mk("6", "editor", "done", "1500", "主编最终汇报"),
    ];
    render(<ProgressLedgerTimeline events={events} running={false} />);
    const entries = screen.getAllByTestId("progress-ledger-entry");
    // 3 activations: editor(1) -> planner -> editor(2) == 3 steps, NOT 2.
    expect(entries).toHaveLength(3);
    expect(entries[0]).toHaveTextContent("editor");
    expect(entries[1]).toHaveTextContent("planner");
    expect(entries[2]).toHaveTextContent("editor");
    // The last editor step carries its later content, proving it is a distinct
    // bottom step rather than the first editor segment.
    fireEvent.click(screen.getAllByText("editor")[1]);
    expect(screen.getAllByText("主编最终汇报").length).toBeGreaterThan(0);
  });

  // Timeline aggregation (2026-06): consecutive same-node activations (no
  // other node in between) merge into ONE step with a round badge, instead
  // of stacking redundant back-to-back rows ("主编 × 8"). Non-consecutive
  // turns still stay separate (covered by the test above).
  it("merges consecutive same-node activations into one step with a round badge", () => {
    const mk = (
      id: string,
      node: string,
      phase: ProgressLedgerEvent["phase"],
      ts: string,
      instruction: string,
    ): ProgressLedgerEvent => ({
      id,
      ts,
      is_request_satisfied: false,
      is_in_loop: false,
      is_progress_being_made: true,
      next_speaker: node,
      instruction_or_question: instruction,
      nodeId: node,
      phase,
      commandId: "cmd_1",
    });
    const events: ProgressLedgerEvent[] = [
      mk("1", "editor", "start", "1000", "主编第一轮"),
      mk("2", "editor", "done", "1100", "主编第一轮完成"),
      // editor re-activates immediately (no other node between) x2 more.
      mk("3", "editor", "start", "1200", "主编第二轮"),
      mk("4", "editor", "done", "1300", "主编第二轮完成"),
      mk("5", "editor", "start", "1400", "主编第三轮"),
      mk("6", "editor", "done", "1500", "主编第三轮完成"),
    ];
    render(<ProgressLedgerTimeline events={events} running={false} />);
    const entries = screen.getAllByTestId("progress-ledger-entry");
    // All three consecutive editor activations collapse into ONE step.
    expect(entries).toHaveLength(1);
    expect(entries[0]).toHaveTextContent("editor");
    // The round badge surfaces the merged count.
    expect(screen.getByText("× 3 轮")).toBeInTheDocument();
  });

  it("does NOT merge same-node steps separated by another node", () => {
    const mk = (
      id: string,
      node: string,
      phase: ProgressLedgerEvent["phase"],
      ts: string,
    ): ProgressLedgerEvent => ({
      id,
      ts,
      is_request_satisfied: false,
      is_in_loop: false,
      is_progress_being_made: true,
      next_speaker: node,
      instruction_or_question: `${node}-${phase}`,
      nodeId: node,
      phase,
      commandId: "cmd_1",
    });
    const events: ProgressLedgerEvent[] = [
      mk("1", "editor", "start", "1000"),
      mk("2", "editor", "done", "1100"),
      mk("3", "planner", "start", "1200"),
      mk("4", "planner", "done", "1300"),
      mk("5", "editor", "start", "1400"),
      mk("6", "editor", "done", "1500"),
    ];
    render(<ProgressLedgerTimeline events={events} running={false} />);
    const entries = screen.getAllByTestId("progress-ledger-entry");
    // editor | planner | editor stay as 3 distinct time-ordered steps.
    expect(entries).toHaveLength(3);
    expect(screen.queryByText(/× \d+ 轮/)).toBeNull();
  });

  it("always keeps command-less (global) entries regardless of scoping", () => {
    const events: ProgressLedgerEvent[] = [
      { ...ev("g", { next_speaker: "globalNode", is_progress_being_made: true }), ts: "500" },
      { ...ev("c", { next_speaker: "cmdNode", is_progress_being_made: true }), commandId: "cmd_2", ts: "2000" },
    ];
    render(<ProgressLedgerTimeline events={events} />);
    expect(screen.getByText("globalNode")).toBeInTheDocument();
    expect(screen.getByText("cmdNode")).toBeInTheDocument();
  });

  // test17 图2: a running node that is actively calling tools stays 进行中 AND
  // stays expanded -- its process lines must NOT collapse to a single line just
  // because a tool call happened. Only real stalls/terminal steps collapse.
  it("keeps a running tool-active node expanded and 进行中 (no premature collapse)", () => {
    const mk = (
      id: string,
      phase: ProgressLedgerEvent["phase"],
      instruction: string,
    ): ProgressLedgerEvent => ({
      id,
      ts: `${1000 + Number(id)}`,
      is_request_satisfied: false,
      is_in_loop: false,
      is_progress_being_made: true,
      next_speaker: "data-analyst",
      instruction_or_question: instruction,
      nodeId: "data-analyst",
      phase,
      commandId: "cmd_1",
    });
    const events: ProgressLedgerEvent[] = [
      mk("1", "start", "开始执行：调研 AI 沙龙"),
      mk("2", "active", "🛠 调用工具 `read_file`：report.md"),
      mk("3", "active", "⚠ 工具 `web_fetch` 失败（network timeout），节点将重试或改用其他方式"),
    ];
    render(<ProgressLedgerTimeline events={events} running={true} />);
    // The node reads 进行中, never 进展缓慢.
    expect(screen.getByText("进行中")).toBeInTheDocument();
    expect(screen.queryByText("进展缓慢")).toBeNull();
    // Expanded: EVERY process line is visible (not collapsed to a one-line
    // summary), so a mid-run tool call does not hide the ongoing work.
    expect(screen.getByText(/调用工具/)).toBeInTheDocument();
    expect(screen.getByText(/开始执行/)).toBeInTheDocument();
    expect(screen.getByText(/web_fetch/)).toBeInTheDocument();
  });
});
