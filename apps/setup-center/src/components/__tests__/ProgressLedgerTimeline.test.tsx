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
  it("renders an empty-state hint when no events are present", () => {
    render(<ProgressLedgerTimeline events={[]} />);
    expect(screen.getByText(/暂无进度记录/)).toBeInTheDocument();
  });

  it("renders one card per event, newest first", () => {
    const events: ProgressLedgerEvent[] = [
      ev("1", { next_speaker: "alpha", is_progress_being_made: true }),
      ev("2", { next_speaker: "beta", is_progress_being_made: true }),
      ev("3", { next_speaker: "gamma", is_progress_being_made: true }),
    ];
    render(<ProgressLedgerTimeline events={events} />);
    const entries = screen.getAllByTestId("progress-ledger-entry");
    expect(entries).toHaveLength(3);
    // First card should reflect the newest entry (id=3, gamma).
    expect(entries[0]).toHaveTextContent("gamma");
    expect(entries[2]).toHaveTextContent("alpha");
  });

  it("derives the badge label from the boolean fields", () => {
    const events: ProgressLedgerEvent[] = [
      ev("a", { is_request_satisfied: true }),
      ev("b", { is_in_loop: true }),
      ev("c", { is_progress_being_made: true }),
      ev("d"),
    ];
    render(<ProgressLedgerTimeline events={events} />);
    expect(screen.getByTestId("progress-ledger-badge-done")).toBeInTheDocument();
    expect(screen.getByTestId("progress-ledger-badge-loop")).toBeInTheDocument();
    expect(screen.getByTestId("progress-ledger-badge-progress")).toBeInTheDocument();
    expect(screen.getByTestId("progress-ledger-badge-stall")).toBeInTheDocument();
  });

  it("collapses old entries past initialVisible and toggles open", () => {
    const events: ProgressLedgerEvent[] = Array.from({ length: 12 }).map((_, i) =>
      ev(`e${i}`, { next_speaker: `n${i}` }),
    );
    render(<ProgressLedgerTimeline events={events} initialVisible={5} />);

    expect(screen.getAllByTestId("progress-ledger-entry")).toHaveLength(5);
    const toggle = screen.getByTestId("progress-ledger-toggle");
    expect(toggle).toHaveTextContent("展开剩余 7 条");

    fireEvent.click(toggle);
    expect(screen.getAllByTestId("progress-ledger-entry")).toHaveLength(12);
    expect(screen.getByTestId("progress-ledger-toggle")).toHaveTextContent("收起");
  });
});
