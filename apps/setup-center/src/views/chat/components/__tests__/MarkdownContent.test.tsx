import { act, fireEvent, render, screen } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import { afterEach, describe, expect, it, vi } from "vitest";
import { copyToClipboard } from "../../../../utils/clipboard";
import type { MdModules } from "../../utils/chatTypes";
import { MarkdownContent } from "../MarkdownContent";

vi.mock("../../../../utils/clipboard", () => ({
  copyToClipboard: vi.fn(),
}));

const mdModules: MdModules = {
  ReactMarkdown,
  remarkPlugins: [],
  rehypePlugins: [],
};

describe("MarkdownContent code block copy", () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("copies fenced code and briefly shows success feedback", async () => {
    vi.mocked(copyToClipboard).mockResolvedValue(true);
    vi.useFakeTimers();

    render(
      <MarkdownContent
        content={'```js\nconsole.log("hello");\n```'}
        mdModules={mdModules}
        className="chatMdContent"
      />,
    );

    const copyButton = screen.getByRole("button", { name: "Copy" });
    await act(async () => {
      fireEvent.click(copyButton);
      await Promise.resolve();
    });
    expect(copyToClipboard).toHaveBeenCalledWith('console.log("hello");\n');
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
  });

  it("does not add a copy button to inline code", () => {
    render(
      <MarkdownContent
        content="Run `openakita serve` to start."
        mdModules={mdModules}
        className="chatMdContent"
      />,
    );

    expect(screen.queryByRole("button", { name: "Copy" })).not.toBeInTheDocument();
  });
});
