import { describe, expect, it } from "vitest";
import { filterDeliverables } from "../OrgChatPanel";

const f = (file_path: string) => ({ filename: file_path.split(/[\\/]/).pop() || file_path, file_path });

describe("filterDeliverables (test17 item 3)", () => {
  it("keeps the final package + PDF and drops process files when a package exists", () => {
    const files = [
      f("D:/o/cmd/artifacts/projects/esp32/project_brief.md"),
      f("D:/o/cmd/artifacts/视觉需求_实验室.md"),
      f("D:/o/cmd/artifacts/projects/esp32/promotional_copy_draft.md"),
      f("D:/o/cmd/artifacts/SEO_优化建议_writer-a_初稿.md"),
      f("D:/o/cmd/artifacts/ESP32_Development_Sharing_Session_Full_Package/00_Cover_Executive_Summary.md"),
      f("D:/o/cmd/artifacts/ESP32_Development_Sharing_Session_Full_Package/04_Budget_Breakdown.md"),
      f("D:/o/cmd/artifacts/最终报告_主编.pdf"),
    ];
    const out = filterDeliverables(files).map(x => x.file_path);
    // PDF + the two package files survive; drafts/briefs/notes are dropped.
    expect(out).toContain("D:/o/cmd/artifacts/最终报告_主编.pdf");
    expect(out).toContain("D:/o/cmd/artifacts/ESP32_Development_Sharing_Session_Full_Package/00_Cover_Executive_Summary.md");
    expect(out).toContain("D:/o/cmd/artifacts/ESP32_Development_Sharing_Session_Full_Package/04_Budget_Breakdown.md");
    expect(out).not.toContain("D:/o/cmd/artifacts/projects/esp32/project_brief.md");
    expect(out).not.toContain("D:/o/cmd/artifacts/projects/esp32/promotional_copy_draft.md");
    expect(out).not.toContain("D:/o/cmd/artifacts/SEO_优化建议_writer-a_初稿.md");
  });

  it("without a package, keeps non-process outputs + drops kickoff/draft", () => {
    const files = [
      f("D:/o/cmd/artifacts/kickoff_planner.md"),
      f("D:/o/cmd/artifacts/方案V1_draft.md"),
      f("D:/o/cmd/artifacts/最终方案.md"),
      f("D:/o/cmd/artifacts/报告.pdf"),
    ];
    const out = filterDeliverables(files).map(x => x.file_path);
    expect(out).toContain("D:/o/cmd/artifacts/最终方案.md");
    expect(out).toContain("D:/o/cmd/artifacts/报告.pdf");
    expect(out).not.toContain("D:/o/cmd/artifacts/kickoff_planner.md");
    expect(out).not.toContain("D:/o/cmd/artifacts/方案V1_draft.md");
  });

  it("never hides everything: all-process input falls back to showing all", () => {
    const files = [f("D:/o/cmd/artifacts/kickoff.md"), f("D:/o/cmd/artifacts/draft.md")];
    expect(filterDeliverables(files).length).toBe(2);
  });

  it("always keeps the PDF even if its name looks process-y", () => {
    const files = [f("D:/o/cmd/artifacts/draft_报告.pdf")];
    expect(filterDeliverables(files).map(x => x.file_path)).toContain("D:/o/cmd/artifacts/draft_报告.pdf");
  });
});
