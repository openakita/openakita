import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AttachmentPreview } from "../AttachmentPreview";

describe("AttachmentPreview image lightbox trigger", () => {
  it("opens the image preview when an image thumbnail is clicked", () => {
    const onImagePreview = vi.fn();
    const dataUrl = "data:image/png;base64,queued";

    render(
      <AttachmentPreview
        att={{ type: "image", name: "queued.png", previewUrl: dataUrl, url: dataUrl }}
        onImagePreview={onImagePreview}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "queued.png" }));

    expect(onImagePreview).toHaveBeenCalledTimes(1);
    expect(onImagePreview).toHaveBeenCalledWith(dataUrl, dataUrl, "queued.png");
  });

  it("does not open the image preview when the remove button is clicked", () => {
    const onImagePreview = vi.fn();
    const onRemove = vi.fn();
    const dataUrl = "data:image/png;base64,pending";
    const { container } = render(
      <AttachmentPreview
        att={{ type: "image", name: "pending.png", previewUrl: dataUrl, url: dataUrl }}
        onImagePreview={onImagePreview}
        onRemove={onRemove}
      />,
    );

    const removeButton = container.querySelector("button");
    expect(removeButton).not.toBeNull();
    fireEvent.click(removeButton as HTMLButtonElement);

    expect(onRemove).toHaveBeenCalledTimes(1);
    expect(onImagePreview).not.toHaveBeenCalled();
  });
});
