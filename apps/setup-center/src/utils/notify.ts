import { toast } from "sonner";
import { copyToClipboard } from "./clipboard";

export function notifySuccess(msg: string) {
  toast.success(msg, { duration: 4000 });
}

export function notifyError(msg: string) {
  toast.error(msg, {
    duration: 8000,
    action: {
      label: "复制",
      onClick: async () => {
        const ok = await copyToClipboard(msg);
        if (ok) toast.success("已复制到剪贴板", { duration: 2000 });
        else toast.error("复制失败，请手动选择文本复制", { duration: 3000 });
      },
    },
  });
}

export function notifyLoading(msg: string): string | number {
  return toast.loading(msg);
}

export function dismissLoading(id: string | number) {
  toast.dismiss(id);
}
