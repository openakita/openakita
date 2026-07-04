import { describe, expect, it } from "vitest";

import { orderTasksForGantt } from "../OrgProjectBoard";

type T = {
  id: string;
  status: string;
  created_at: string;
  parent_task_id?: string | null;
};

const mk = (id: string, parent: string | null, created: string, status = "in_progress"): T => ({
  id,
  status,
  created_at: created,
  parent_task_id: parent,
});

describe("orderTasksForGantt (P4 阶段B 甘特层级)", () => {
  it("orders a tree as parent -> children with depth, children sorted by created_at", () => {
    const tasks: T[] = [
      mk("c2", "root", "2026-01-01T00:02:00Z"),
      mk("root", null, "2026-01-01T00:00:00Z"),
      mk("c1", "root", "2026-01-01T00:01:00Z"),
      mk("gc1", "c1", "2026-01-01T00:03:00Z"),
    ];
    const out = orderTasksForGantt(tasks);
    // root → c1 (then its child gc1) → c2; c1/c2 are both children of root (depth 1).
    expect(out.map(t => t.id)).toEqual(["root", "c1", "gc1", "c2"]);
    expect(out.map(t => t._depth)).toEqual([0, 1, 2, 1]);
  });

  it("falls back to status-then-created order when there are no parent links", () => {
    const tasks: T[] = [
      mk("a", null, "2026-01-01T00:00:00Z", "delivered"),
      mk("b", null, "2026-01-01T00:01:00Z", "todo"),
      mk("c", null, "2026-01-01T00:02:00Z", "in_progress"),
    ];
    const out = orderTasksForGantt(tasks);
    // todo(order0) -> in_progress(order1) -> delivered(order2)
    expect(out.map(t => t.id)).toEqual(["b", "c", "a"]);
    expect(out.every(t => t._depth === 0)).toBe(true);
  });

  it("treats a parent id that is not present as a root (no orphan dropped)", () => {
    const tasks: T[] = [
      mk("orphan", "missing-parent", "2026-01-01T00:00:00Z"),
      mk("p", null, "2026-01-01T00:01:00Z"),
      mk("ch", "p", "2026-01-01T00:02:00Z"),
    ];
    const out = orderTasksForGantt(tasks);
    expect(new Set(out.map(t => t.id))).toEqual(new Set(["orphan", "p", "ch"]));
    expect(out.find(t => t.id === "ch")!._depth).toBe(1);
    expect(out.find(t => t.id === "orphan")!._depth).toBe(0);
  });

  it("does not drop tasks even with a cyclic parent link", () => {
    const tasks: T[] = [
      mk("x", "y", "2026-01-01T00:00:00Z"),
      mk("y", "x", "2026-01-01T00:01:00Z"),
    ];
    const out = orderTasksForGantt(tasks);
    expect(new Set(out.map(t => t.id))).toEqual(new Set(["x", "y"]));
  });
});
