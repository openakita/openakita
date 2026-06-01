import { useMemo, useState } from "react";

/**
 * One progress-ledger entry, mirroring the payload shape the v2
 * supervisor emits on the ``progress_ledger`` channel (ADR-0006
 * + ADR-0004 §dual-ledger).
 *
 * Backend ``ProgressLedger`` carries five user-facing fields:
 *   is_request_satisfied, is_in_loop, is_progress_being_made,
 *   next_speaker, instruction_or_question.
 * They land in the ``payload`` of a :class:`StreamEvent`; the
 * timeline component renders that payload directly.
 */
export interface ProgressLedgerEvent {
  /** Backend ``StreamEvent.event_id`` -- used as React key. */
  id: string;
  /** Emitted-at timestamp string (ISO-8601). */
  ts: string;
  /** Whether the supervisor judged the user's request satisfied. */
  is_request_satisfied: boolean;
  /** Whether the supervisor detected a loop in this turn. */
  is_in_loop: boolean;
  /** Whether forward progress was made this turn. */
  is_progress_being_made: boolean;
  /** Next-speaker hint from the supervisor (node role / id / name). */
  next_speaker: string;
  /** Verbatim instruction the supervisor will hand to next_speaker. */
  instruction_or_question: string;
  /**
   * 图3 convergence: stable node id this event belongs to. When present,
   * the timeline groups ALL events of the same node into ONE segment (even
   * across interleaving / multiple dispatch rounds) instead of spawning a
   * fresh "进行中" segment each time, so a node never shows multiple parallel
   * running flows. Absent for raw supervisor ledger turns (those keep the
   * legacy consecutive-by-speaker grouping).
   */
  nodeId?: string;
  /**
   * 图3 convergence: the node lifecycle phase this event represents. Drives
   * the segment's terminal status: ``done`` / ``incomplete`` / ``failed`` are
   * terminal and win over later non-terminal events, while a new ``start``
   * after a terminal opens a fresh round within the same segment.
   */
  phase?: "start" | "active" | "done" | "incomplete" | "failed";
}

export interface ProgressLedgerTimelineProps {
  /** Newest-last sequence of ledger entries. */
  events: ProgressLedgerEvent[];
  /** Resolve a raw node id/role to a human (Chinese) display name. */
  nodeNameOf?: (id: string) => string;
  /** Whether a command is still running (drives the live pulse). */
  running?: boolean;
  /** Optional ``data-testid`` for the outer container. */
  "data-testid"?: string;
}

type SegStatus = "running" | "done" | "loop" | "stall" | "incomplete" | "failed";

interface Segment {
  key: string;
  node: string;
  lines: string[];
  status: SegStatus;
  satisfied: boolean;
  ts: string;
  /** Number of dispatch rounds folded into this node segment (图3). */
  rounds: number;
  /** Monotonic order index of the most recent update (drives "active"). */
  lastSeq: number;
}

const TERMINAL: ReadonlySet<SegStatus> = new Set<SegStatus>(["done", "incomplete", "failed"]);

// UI issue #3: the old component rendered English status pills
// (DONE/LOOP/PROGRESS/STALL). The whole product runs in Chinese, so the
// process log must be Chinese too. These are the user-facing labels.
const STATUS_LABEL: Record<SegStatus, string> = {
  running: "进行中",
  done: "已完成",
  loop: "检测到循环",
  stall: "停滞",
  incomplete: "未通过校验",
  failed: "失败",
};

const STATUS_CLASS: Record<SegStatus, string> = {
  running: "plt-pill plt-pill-running",
  done: "plt-pill plt-pill-done",
  loop: "plt-pill plt-pill-loop",
  stall: "plt-pill plt-pill-stall",
  incomplete: "plt-pill plt-pill-stall",
  failed: "plt-pill plt-pill-loop",
};

function fmtTs(ts: string): string {
  if (!ts) return "";
  // Accept ISO strings and epoch numbers alike.
  const d = /^\d+$/.test(ts) ? new Date(Number(ts)) : new Date(ts);
  if (Number.isNaN(d.getTime())) return ts.slice(11, 19);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/**
 * Render the v2 live-process feed as a connected, conversational timeline.
 *
 * Redesign (exploratory testing v12): the previous version rendered one big
 * shadcn Card per ledger entry with English badges and "(尚未指定)/(无指令)"
 * placeholders, sitting in a detached strip above the chat — the "大白块 +
 * 割裂 + 英文" the user reported. This version:
 *
 *  * groups consecutive events by node into ONE segment (a node's whole turn
 *    is a single bubble, not N cards),
 *  * shows each node's actual content lines (not just an action verb),
 *  * auto-collapses every COMPLETED node to a one-line summary (click to
 *    expand) and keeps only the active node expanded with a live pulse,
 *  * is fully Chinese, and
 *  * renders NOTHING when there are no meaningful events, so the old
 *    "暂无进度记录…" banner never sits permanently above a finished task.
 *
 * It is meant to live INSIDE the message scroll column (not a bounded strip),
 * so the command center reads as a single conversation that scrolls as one.
 */
export function ProgressLedgerTimeline({
  events,
  nodeNameOf,
  running = false,
  ...rest
}: ProgressLedgerTimelineProps) {
  const [openKeys, setOpenKeys] = useState<Record<string, boolean>>({});

  const segments = useMemo<Segment[]>(() => {
    const resolve = (id: string) => (nodeNameOf ? nodeNameOf(id) : id) || id;
    // Drop empty-shell entries (no speaker AND no instruction AND not a
    // terminal "satisfied" marker) — those were the bulk of the "大白块".
    const meaningful = events.filter(
      (e) =>
        (e.nodeId && e.nodeId.trim()) ||
        (e.next_speaker && e.next_speaker.trim()) ||
        (e.instruction_or_question && e.instruction_or_question.trim()) ||
        e.is_request_satisfied,
    );
    // 图3 convergence: group by a STABLE key. When an event carries a
    // ``nodeId`` we key on the node so all its rounds collapse into ONE
    // segment regardless of interleaving (no more N parallel "进行中" rows for
    // the same node). Supervisor ledger turns (no nodeId) fall back to the
    // legacy consecutive-by-speaker behaviour via a per-run synthetic key.
    const byKey = new Map<string, Segment>();
    const order: string[] = [];
    let seq = 0;
    let consecutiveKey = "";
    let consecutiveNode = "";
    for (const e of meaningful) {
      seq += 1;
      const rawId = (e.nodeId || "").trim();
      const node = resolve((rawId || e.next_speaker || "").trim()) || "协调";
      const line = (e.instruction_or_question || "").trim();
      const phase = e.phase;

      // Resolve the grouping key.
      let groupKey: string;
      if (rawId) {
        groupKey = `node:${rawId}`;
      } else {
        // Legacy ledger turn: merge consecutive same-speaker entries.
        if (consecutiveNode === node && consecutiveKey) {
          groupKey = consecutiveKey;
        } else {
          groupKey = `seg:${e.id}`;
          consecutiveKey = groupKey;
          consecutiveNode = node;
        }
      }
      if (!rawId) {
        consecutiveNode = node;
        consecutiveKey = groupKey;
      } else {
        consecutiveKey = "";
        consecutiveNode = "";
      }

      let seg = byKey.get(groupKey);
      if (!seg) {
        seg = {
          key: groupKey,
          node,
          lines: [],
          status: "running",
          satisfied: false,
          ts: e.ts,
          rounds: 1,
          lastSeq: seq,
        };
        byKey.set(groupKey, seg);
        order.push(groupKey);
      }
      seg.lastSeq = seq;
      seg.ts = e.ts || seg.ts;

      // A new dispatch round after a terminal state: open a fresh round in the
      // SAME segment instead of a parallel one, and clear the terminal latch.
      if (phase === "start" && TERMINAL.has(seg.status)) {
        seg.rounds += 1;
        seg.status = "running";
        seg.satisfied = false;
        seg.lines.push(`— 第 ${seg.rounds} 轮 —`);
      }
      if (line && !seg.lines.includes(line)) seg.lines.push(line);

      // Status convergence. Terminal phases win and latch; non-terminal events
      // never downgrade a terminal status (except via the new-round reset).
      if (phase === "done") {
        seg.status = "done";
        seg.satisfied = true;
      } else if (phase === "incomplete") {
        seg.status = "incomplete";
      } else if (phase === "failed") {
        seg.status = "failed";
      } else if (e.is_request_satisfied) {
        seg.status = "done";
        seg.satisfied = true;
      } else if (!TERMINAL.has(seg.status)) {
        if (e.is_in_loop) seg.status = "loop";
        else if (!e.is_progress_being_made) seg.status = "stall";
        else seg.status = "running";
      }
    }
    const built = order.map((k) => byKey.get(k)!).filter(Boolean);
    // 图3 final convergence: once the command is no longer running, NOTHING is
    // truly "进行中". A node whose terminal event fell outside the rebuilt
    // window (e.g. a worker that only ever emitted tool events, or an older
    // command's mid-round row) must not be left spinning forever — resolve any
    // still-"running" segment to "已完成" so the timeline settles deterministically.
    if (!running) {
      for (const s of built) {
        if (s.status === "running") {
          s.status = "done";
          s.satisfied = true;
        }
      }
    }
    return built;
  }, [events, nodeNameOf, running]);

  if (segments.length === 0) return null;

  // 图3: "active" is the still-running segment that was updated most recently
  // (by seq), not merely the last in render order — interleaved nodes mean the
  // newest activity isn't always the last node first seen.
  const activeKey = (() => {
    let best: Segment | null = null;
    for (const s of segments) {
      if (s.status === "running" && (!best || s.lastSeq > best.lastSeq)) best = s;
    }
    return best?.key ?? "";
  })();

  return (
    <div className="plt-feed" data-testid={rest["data-testid"] ?? "progress-ledger-timeline"}>
      {segments.map((seg) => {
        const isActive = running && seg.key === activeKey && seg.status === "running";
        // Active node stays open; completed nodes collapse to one line unless
        // the user explicitly expanded them.
        const open = openKeys[seg.key] ?? isActive;
        const summary = seg.lines[seg.lines.length - 1] || STATUS_LABEL[seg.status];
        return (
          <div
            key={seg.key}
            className={`plt-seg${isActive ? " plt-seg-active" : ""}`}
            data-testid="progress-ledger-entry"
          >
            <div className={`plt-rail${isActive ? " plt-rail-active" : ""}`}>
              <span className={`plt-dot plt-dot-${seg.status}${isActive ? " plt-dot-pulse" : ""}`} />
            </div>
            <div className="plt-body">
              <button
                type="button"
                className="plt-head"
                onClick={() => setOpenKeys((p) => ({ ...p, [seg.key]: !open }))}
              >
                <span className="plt-node">{seg.node}</span>
                <span className={STATUS_CLASS[seg.status]}>{STATUS_LABEL[seg.status]}</span>
                <span className="plt-time">{fmtTs(seg.ts)}</span>
                {seg.lines.length > 0 && (
                  <span className="plt-caret">{open ? "▾" : "▸"}</span>
                )}
              </button>
              {open ? (
                seg.lines.length > 0 && (
                  <div className="plt-lines">
                    {seg.lines.map((ln, i) => (
                      <div className="plt-line" key={i}>{ln}</div>
                    ))}
                  </div>
                )
              ) : (
                <div className="plt-summary" title={summary}>{summary}</div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default ProgressLedgerTimeline;
