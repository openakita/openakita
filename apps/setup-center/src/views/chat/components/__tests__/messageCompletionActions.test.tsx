import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../../utils/chatTypes";
import { MessageCompletionActions } from "../MessageCompletionActions";

const message: ChatMessage = {
  id: "assistant-1",
  role: "assistant",
  content: "diagnosis",
  timestamp: 1,
  completionActions: [{ type: "submit_feedback", style: "prominent" }],
};

describe("MessageCompletionActions", () => {
  it("renders a persisted action and dispatches it with the source message", () => {
    const onAction = vi.fn();
    render(<MessageCompletionActions msg={message} onAction={onAction} />);

    fireEvent.click(screen.getByRole("button", { name: "提交反馈日志" }));

    expect(onAction).toHaveBeenCalledWith(
      message,
      { type: "submit_feedback", style: "prominent" },
    );
  });

  it("does not render before the assistant turn completes", () => {
    const { container } = render(
      <MessageCompletionActions msg={{ ...message, streaming: true }} onAction={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
