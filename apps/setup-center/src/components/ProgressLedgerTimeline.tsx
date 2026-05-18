import { useMemo, useState } from "react";

import { Badge } from "./ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";

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
  /** Next-speaker hint from the supervisor (node role). */
  next_speaker: string;
  /** Verbatim instruction the supervisor will hand to next_speaker. */
  instruction_or_question: string;
}

export interface ProgressLedgerTimelineProps {
  /** Newest-last sequence of ledger entries. */
  events: ProgressLedgerEvent[];
  /** How many entries to show before the "expand" toggle (default 10). */
  initialVisible?: number;
  /** Optional ``data-testid`` for the outer container. */
  "data-testid"?: string;
}

interface BadgeStyle {
  variant: "default" | "secondary" | "destructive" | "outline";
  label: string;
}

function progressBadge(ev: ProgressLedgerEvent): BadgeStyle {
  if (ev.is_request_satisfied) return { variant: "default", label: "DONE" };
  if (ev.is_in_loop) return { variant: "destructive", label: "LOOP" };
  if (ev.is_progress_being_made) return { variant: "default", label: "PROGRESS" };
  return { variant: "secondary", label: "STALL" };
}

/**
 * Render a v2 ``progress_ledger`` channel as a vertical timeline.
 *
 * Visual: one shadcn Card per entry, newest first; "DONE" / "LOOP" /
 * "PROGRESS" / "STALL" badge derived from the boolean fields. Old
 * entries collapse below ``initialVisible`` (default 10) and a
 * "展开/收起" toggle reveals the full history.
 */
export function ProgressLedgerTimeline({
  events,
  initialVisible = 10,
  ...rest
}: ProgressLedgerTimelineProps) {
  const [expanded, setExpanded] = useState(false);

  const reversed = useMemo(() => [...events].reverse(), [events]);
  const visibleCount = expanded ? reversed.length : initialVisible;
  const visible = reversed.slice(0, visibleCount);
  const hidden = reversed.length - visible.length;

  if (events.length === 0) {
    return (
      <div
        className="text-sm text-muted-foreground"
        data-testid={rest["data-testid"] ?? "progress-ledger-timeline"}
      >
        暂无进度记录。等待 v2 supervisor 发出第一条 ``progress_ledger`` 事件…
      </div>
    );
  }

  return (
    <div
      className="flex flex-col gap-2"
      data-testid={rest["data-testid"] ?? "progress-ledger-timeline"}
    >
      {visible.map((ev) => {
        const badge = progressBadge(ev);
        return (
          <Card key={ev.id} data-testid="progress-ledger-entry">
            <CardHeader className="flex flex-row items-center justify-between gap-2 py-2">
              <CardTitle className="text-sm font-medium">
                {ev.next_speaker || "(尚未指定)"}
              </CardTitle>
              <div className="flex items-center gap-2">
                <Badge
                  variant={badge.variant}
                  data-testid={`progress-ledger-badge-${badge.label.toLowerCase()}`}
                >
                  {badge.label}
                </Badge>
                <CardDescription className="text-xs">
                  {ev.ts.slice(0, 19).replace("T", " ")}
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent className="text-sm py-2">
              {ev.instruction_or_question || (
                <span className="text-muted-foreground">(无指令)</span>
              )}
            </CardContent>
          </Card>
        );
      })}
      {(reversed.length > initialVisible) && (
        <button
          type="button"
          className="text-xs text-primary hover:underline self-start"
          onClick={() => setExpanded((prev) => !prev)}
          data-testid="progress-ledger-toggle"
        >
          {expanded ? "收起" : `展开剩余 ${hidden} 条`}
        </button>
      )}
    </div>
  );
}

export default ProgressLedgerTimeline;
