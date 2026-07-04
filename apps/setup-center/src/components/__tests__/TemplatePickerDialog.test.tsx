import { describe, expect, it, vi } from "vitest";
import { render, screen, act, fireEvent } from "@testing-library/react";

// Mock the API module so the dialog never hits the network. The
// mocked listTemplates returns 2 templates; instantiateTemplate
// returns a fake OrgWire that the dialog hands to onCreated.
vi.mock("../../api/orgs", async () => {
  const tpls = [
    {
      id: "tpl_a",
      name: "Newsroom",
      description: "A two-node v2 newsroom",
      node_count: 2,
      preset_id: "newsroom",
    },
    {
      id: "tpl_b",
      name: "Solo Writer",
      description: "single-node",
      node_count: 1,
      preset_id: "solo",
    },
  ];
  return {
    __esModule: true,
    listTemplates: vi.fn(() => Promise.resolve(tpls)),
    instantiateTemplate: vi.fn((_b: string, id: string, body: { name: string }) =>
      Promise.resolve({
        id: "org_new",
        name: body.name,
        template_id: id,
        description: null,
        status: "draft",
        nodes: [],
        edges: [],
        created_at: "",
        updated_at: "",
      }),
    ),
  };
});

import * as orgsApi from "../../api/orgs";
import { TemplatePickerDialog } from "../TemplatePickerDialog";

describe("TemplatePickerDialog", () => {
  it("opens, lists templates, and POSTs on create", async () => {
    const onCreated = vi.fn();
    render(
      <TemplatePickerDialog apiBase="http://test" onCreated={onCreated}>
        <button data-testid="trigger">新建 v2 组织（从模板）</button>
      </TemplatePickerDialog>,
    );

    // 1. Modal is closed initially — no template list.
    expect(screen.queryByText(/选择 v2 组织模板/)).toBeNull();

    // 2. Click trigger → modal opens, listTemplates called.
    await act(async () => {
      fireEvent.click(screen.getByTestId("trigger"));
    });
    expect(orgsApi.listTemplates).toHaveBeenCalledWith("http://test");
    // The mocked promise needs a tick to flush.
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText("Newsroom")).toBeInTheDocument();
    expect(screen.getByText("Solo Writer")).toBeInTheDocument();

    // 3. First template auto-selected — visual selected state.
    const cardA = screen.getByTestId("v2-template-card-tpl_a");
    expect(cardA.getAttribute("data-selected")).toBe("true");

    // 4. Click second template → selection switches.
    await act(async () => {
      fireEvent.click(screen.getByTestId("v2-template-card-tpl_b"));
    });
    expect(screen.getByTestId("v2-template-card-tpl_b").getAttribute("data-selected")).toBe("true");
    expect(screen.getByTestId("v2-template-card-tpl_a").getAttribute("data-selected")).toBe("false");

    // 5. Switch back to tpl_a, then type name and click “创建组织”.
    await act(async () => {
      fireEvent.click(screen.getByTestId("v2-template-card-tpl_a"));
    });
    const input = screen.getByLabelText(/新组织名称/) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { value: "试验编辑部" } });
    });
    const createBtn = screen.getByTestId("v2-template-dialog-create");
    expect((createBtn as HTMLButtonElement).disabled).toBe(false);
    await act(async () => {
      fireEvent.click(createBtn);
      await Promise.resolve();
      await Promise.resolve();
    });

    // 6. instantiateTemplate must have fired with tpl_a + typed name.
    expect(orgsApi.instantiateTemplate).toHaveBeenCalledWith(
      "http://test",
      "tpl_a",
      { name: "试验编辑部" },
    );
    expect(onCreated).toHaveBeenCalledTimes(1);
    expect(onCreated.mock.calls[0][0]).toMatchObject({
      id: "org_new",
      name: "试验编辑部",
      template_id: "tpl_a",
    });
  });

  it("disables the create button when name is empty", async () => {
    render(
      <TemplatePickerDialog apiBase="http://test" onCreated={() => {}}>
        <button data-testid="trigger2">open</button>
      </TemplatePickerDialog>,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("trigger2"));
      await Promise.resolve();
    });
    const createBtn = screen.getByTestId("v2-template-dialog-create") as HTMLButtonElement;
    // Auto-selected first template, but name empty → still disabled.
    expect(createBtn.disabled).toBe(true);
  });
});
