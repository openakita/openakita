import { describe, expect, it, vi } from "vitest";

import {
  ORG_STRUCTURE_CHANGED_EVENT,
  dispatchOrgStructureChanged,
  normalizeOrgStructureChange,
} from "../orgStructureEvents";

describe("orgStructureEvents", () => {
  it("normalizes backend snake_case payloads", () => {
    expect(normalizeOrgStructureChange({
      action: "created",
      org_id: "org_123",
      org_name: "测试组织",
      node_count: 3,
      edge_count: 2,
      status: "dormant",
      tool_use_id: "call_1",
    })).toEqual({
      action: "created",
      orgId: "org_123",
      orgName: "测试组织",
      templateId: undefined,
      nodeCount: 3,
      edgeCount: 2,
      status: "dormant",
      toolUseId: "call_1",
    });
  });

  it("dispatches a browser event only when org id is present", () => {
    const listener = vi.fn();
    window.addEventListener(ORG_STRUCTURE_CHANGED_EVENT, listener);
    try {
      expect(dispatchOrgStructureChanged({ action: "updated" })).toBe(false);
      expect(dispatchOrgStructureChanged({
        action: "updated",
        org_id: "org_abc",
        status: "dormant",
      })).toBe(true);
      expect(listener).toHaveBeenCalledTimes(1);
      expect(listener.mock.calls[0][0].detail).toMatchObject({
        action: "updated",
        orgId: "org_abc",
        status: "dormant",
      });
    } finally {
      window.removeEventListener(ORG_STRUCTURE_CHANGED_EVENT, listener);
    }
  });
});
