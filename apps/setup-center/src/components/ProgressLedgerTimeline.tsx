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
  /**
   * Owning command id. When set, the timeline shows only the segments of the
   * CURRENT command (the latest command id among the events, or an explicitly
   * pinned ``activeCommandId``) so a single org that has run many commands no
   * longer cross-renders stale node segments / hanging statuses from older
   * commands' ``/activity`` history (2026-06 item 3). Command-level / global
   * entries with no ``commandId`` are always kept.
   */
  commandId?: string;
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
  /**
   * Item 3 (2026-06): only render segments belonging to this command id. When
   * omitted, the timeline auto-selects the LATEST command id present among the
   * events. Entries with no ``commandId`` (command-level/global) are always
   * shown regardless.
   */
  activeCommandId?: string;
}

type SegStatus = "running" | "done" | "loop" | "stall" | "incomplete" | "failed";

interface Segment {
  key: string;
  node: string;
  lines: string[];
  status: SegStatus;
  satisfied: boolean;
  ts: string;
  /** Monotonic order index of the most recent update (drives "active"). */
  lastSeq: number;
  /**
   * How many CONSECUTIVE activations of the same node this step merges.
   * The v21 flow opened a brand-new step on every re-activation, so a root
   * coordinator that re-ran many times in a row showed up as "主编 × 8"
   * back-to-back rows (redundant). We now merge consecutive same-node
   * activations into ONE step and surface the count here — without folding
   * NON-consecutive turns (a 主编 turn after a child turn stays its own
   * step, preserving the time-ordered flow).
   */
  rounds: number;
}

const TERMINAL: ReadonlySet<SegStatus> = new Set<SegStatus>(["done", "incomplete", "failed"]);

// UI issue #3: the old component rendered English status pills
// (DONE/LOOP/PROGRESS/STALL). The whole product runs in Chinese, so the
// process log must be Chinese too. These are the user-facing labels.
const STATUS_LABEL: Record<SegStatus, string> = {
  running: "进行中",
  done: "已完成",
  loop: "疑似重复",
  stall: "进展缓慢",
  incomplete: "校验未通过",
  failed: "失败",
};

// test11 P1: the old pills ("检测到循环"/"停滞") read like scary errors with no
// explanation of WHAT happened, WHY, or the NEXT step — users couldn't tell if
// the org was broken. These tooltips spell out the meaning + that the
// supervisor automatically intervenes (it is NOT a dead end).
const STATUS_TOOLTIP: Record<SegStatus, string> = {
  running: "该节点正在执行任务。",
  done: "该节点已完成并通过校验。",
  loop:
    "调度大脑判断最近几轮在重复相似动作、进展不明显，已自动调整分工/换人继续推进——不是报错，无需手动干预。",
  stall:
    "本轮相比上一轮没有取得明显进展，调度大脑会换思路或补充信息后继续，必要时换节点处理。",
  incomplete:
    "该节点本轮产出未通过完成度校验（如内容过短/仍是中间思考），已被退回重做或交由上级补全，不会作为最终交付。",
  failed: "该节点本轮执行失败，已记录异常并交由调度大脑重试或换人接手。",
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
 * Render the v2 live-process feed as a TIME-ORDERED task flow.
 *
 * Redesign (exploratory testing v21): the previous version grouped ALL of a
 * node's rounds into ONE stable segment keyed on ``node:<id>`` — so when the
 * root主编 acted, was coordinated away, then acted again, its second turn was
 * folded back into the SAME top segment. The user lost the sense of flow (the
 * 主编 row stayed pinned at the top; to see a later 主编 output you had to
 * scroll back up). This version makes each node ACTIVATION a distinct,
 * time-ordered step appended in chronological order, so the feed reads as
 * 主编 → 协调 → 下级 → 产出 → 再协调 → 上级 → 再主编 …:
 *
 *  * each ``phase==="start"`` (a node activation / re-dispatch / rework
 *    re-run) opens a NEW step at its chronological position — a node that
 *    acts twice shows up as two steps, the later one further down,
 *  * incremental updates WITHIN one activation (tool calls, deltas, review
 *    trace lines) refresh in place on that step (no hundreds of rows),
 *  * the running step stays expanded with a live pulse; a completed step
 *    collapses to a one-line summary BUT stays at its original time position,
 *  * coordinator / supervisor ledger turns (no ``nodeId``) keep the legacy
 *    consecutive-by-speaker merge so they don't spam one row per token,
 *  * is fully Chinese, and renders NOTHING when there are no meaningful events.
 *
 * It lives INSIDE the message scroll column so the command center reads as a
 * single conversation that scrolls as one (the parent auto-scrolls to bottom
 * on new events).
 */
export function ProgressLedgerTimeline({
  events,
  nodeNameOf,
  running = false,
  activeCommandId,
  ...rest
}: ProgressLedgerTimelineProps) {
  const [openKeys, setOpenKeys] = useState<Record<string, boolean>>({});

  const segments = useMemo<Segment[]>(() => {
    const resolve = (id: string) => (nodeNameOf ? nodeNameOf(id) : id) || id;
    // Item 3 (2026-06): a single org that has run many commands accumulates
    // node segments from ALL of them in the rebuilt /activity feed. Show ONLY
    // the CURRENT command's segments so old commands' stale "进行中"/"失败"
    // rows never cross-render. The current command = the explicit
    // ``activeCommandId`` when pinned, else the LATEST command id present
    // (by event order/timestamp). Entries with no ``commandId`` (command-level
    // / global / legacy supervisor turns) are always kept.
    const tnum = (s: string) => (/^\d+$/.test(s) ? Number(s) : Date.parse(s) || 0);
    let currentCmd = (activeCommandId || "").trim();
    if (!currentCmd) {
      let bestTs = -1;
      for (const e of events) {
        const cid = (e.commandId || "").trim();
        if (!cid) continue;
        const t = tnum(e.ts || "");
        if (t >= bestTs) {
          bestTs = t;
          currentCmd = cid;
        }
      }
    }
    const scoped = currentCmd
      ? events.filter((e) => {
          const cid = (e.commandId || "").trim();
          return !cid || cid === currentCmd;
        })
      : events;
    // Drop empty-shell entries (no speaker AND no instruction AND not a
    // terminal "satisfied" marker) — those were the bulk of the "大白块".
    const meaningful = scoped.filter(
      (e) =>
        (e.nodeId && e.nodeId.trim()) ||
        (e.next_speaker && e.next_speaker.trim()) ||
        (e.instruction_or_question && e.instruction_or_question.trim()) ||
        e.is_request_satisfied,
    );
    // v21 TIME-ORDERED FLOW: each node ACTIVATION is its own step, appended in
    // chronological order. We no longer key on a stable ``node:<id>`` (which
    // folded every round of a node into one pinned segment). Instead we track
    // the node's CURRENTLY-OPEN step in ``openByNode``; a ``start`` phase, or
    // activity arriving after that step already reached a terminal status,
    // opens a BRAND-NEW step (so 主编's 2nd turn lands below, not back on top).
    // ``order`` is first-seen order = chronological (events are ts-sorted), so
    // steps render in the exact sequence they happened. Coordinator / ledger
    // turns (no nodeId) keep the legacy consecutive-by-speaker merge.
    const byKey = new Map<string, Segment>();
    const order: string[] = [];
    const openByNode = new Map<string, string>();
    let seq = 0;
    let consecutiveKey = "";
    let consecutiveNode = "";
    // rawId of the step most recently APPENDED to ``order`` ("" for a
    // coordinator/ledger turn). Drives the consecutive-same-node merge:
    // a coordinator or different-node step in between breaks the run so
    // only genuinely back-to-back same-node activations collapse.
    let lastOrderRawId = "";
    for (const e of meaningful) {
      seq += 1;
      const rawId = (e.nodeId || "").trim();
      const node = resolve((rawId || e.next_speaker || "").trim()) || "协调";
      const line = (e.instruction_or_question || "").trim();
      const phase = e.phase;

      // Resolve the grouping key.
      let groupKey: string;
      if (rawId) {
        const openKey = openByNode.get(rawId);
        const openSeg = openKey ? byKey.get(openKey) : undefined;
        // Open a new step on activation, when no step is open, or when the
        // node's current step already finished (a fresh round = a fresh step).
        const needNew = phase === "start" || !openSeg || TERMINAL.has(openSeg.status);
        if (needNew) {
          // Consecutive same-node merge: if the immediately-preceding step
          // in the flow is THIS node, fold the re-activation into it as
          // another round instead of stacking a redundant new row. A step
          // belonging to any other node (or a coordinator turn) sits in
          // between -> we keep them separate so the time-ordered flow and
          // interleaving stay legible.
          const prevKey = order.length ? order[order.length - 1] : "";
          const prevSeg = prevKey ? byKey.get(prevKey) : undefined;
          if (lastOrderRawId === rawId && prevSeg) {
            groupKey = prevKey;
            prevSeg.rounds += 1;
            // Re-activation reopens a previously-terminal step so its live
            // status reflects that the node is working again.
            if (TERMINAL.has(prevSeg.status)) prevSeg.status = "running";
            openByNode.set(rawId, groupKey);
          } else {
            groupKey = `node:${rawId}#${seq}`;
            openByNode.set(rawId, groupKey);
          }
        } else {
          groupKey = openKey!;
        }
        consecutiveKey = "";
        consecutiveNode = "";
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

      let seg = byKey.get(groupKey);
      if (!seg) {
        seg = {
          key: groupKey,
          node,
          lines: [],
          status: "running",
          satisfied: false,
          ts: e.ts,
          lastSeq: seq,
          rounds: 1,
        };
        byKey.set(groupKey, seg);
        order.push(groupKey);
        // Remember what kind of step this newly-appended row is, so the
        // NEXT activation can decide whether to merge (same node, no gap).
        lastOrderRawId = rawId;
      }
      seg.lastSeq = seq;
      seg.ts = e.ts || seg.ts;

      if (line && !seg.lines.includes(line)) seg.lines.push(line);

      // Status convergence. Terminal phases win and latch; non-terminal events
      // never downgrade a terminal status. A terminal phase also closes the
      // node's open step so the NEXT activity for that node opens a new step.
      if (phase === "done") {
        seg.status = "done";
        seg.satisfied = true;
        if (rawId) openByNode.delete(rawId);
      } else if (phase === "incomplete") {
        seg.status = "incomplete";
        if (rawId) openByNode.delete(rawId);
      } else if (phase === "failed") {
        seg.status = "failed";
        if (rawId) openByNode.delete(rawId);
      } else if (e.is_request_satisfied) {
        seg.status = "done";
        seg.satisfied = true;
        if (rawId) openByNode.delete(rawId);
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
  }, [events, nodeNameOf, running, activeCommandId]);

  if (segments.length === 0) return null;

  // v21 time-ordered flow: EVERY still-running step stays expanded (parallel
  // dispatch means several steps can be live at once), and the most-recent
  // running step additionally carries the pulse. Completed steps collapse to a
  // one-line summary at their original chronological position.
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
        const isRunning = running && seg.status === "running";
        const isActive = isRunning && seg.key === activeKey;
        // Any running step stays open; completed steps collapse to one line
        // unless the user explicitly expanded them.
        const open = openKeys[seg.key] ?? isRunning;
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
                {seg.rounds > 1 && (
                  <span
                    className="plt-rounds"
                    title={`该节点连续执行了 ${seg.rounds} 次（已合并为一个步骤，避免同节点多段刷屏）`}
                  >
                    × {seg.rounds} 轮
                  </span>
                )}
                <span className={STATUS_CLASS[seg.status]} title={STATUS_TOOLTIP[seg.status]}>{STATUS_LABEL[seg.status]}</span>
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
