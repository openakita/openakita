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

  it("formats large attachment sizes in GB", () => {
    render(
      <AttachmentPreview
        att={{
          type: "file",
          name: "large.zip",
          size: 3.14 * 1024 * 1024 * 1024,
          mimeType: "application/zip",
        }}
      />,
    );

    expect(screen.getByText("3.14 GB")).toBeInTheDocument();
  });

  it("shows a progress bar while an attachment is uploading", () => {
    render(
      <AttachmentPreview
        att={{
          type: "image",
          name: "large.png",
          size: 16 * 1024 * 1024,
          mimeType: "image/png",
          uploadStatus: "uploading",
          uploadProgress: 0.42,
        }}
      />,
    );

    const progress = screen.getByLabelText("附件处理进度");
    expect(progress).toBeInTheDocument();
    expect(progress).toHaveAttribute("value", "42");
    expect(screen.getByText("处理中")).toBeInTheDocument();
  });
});
