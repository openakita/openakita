import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDownIcon, CheckIcon } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export function ProviderSearchSelect({
  value,
  onChange,
  options,
  placeholder,
  disabled,
  extraOptions,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
  disabled?: boolean;
  extraOptions?: { value: string; label: string }[];
}) {
  const [open, setOpen] = useState(false);
  const [hoverIdx, setHoverIdx] = useState(0);
  const [search, setSearch] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0, width: 0 });
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);

  const allOptions = useMemo(() => {
    return extraOptions ? [...extraOptions, ...options] : options.slice();
  }, [options, extraOptions]);

  const selectedLabel = useMemo(
    () => allOptions.find((o) => o.value === value)?.label ?? "",
    [allOptions, value],
  );

  const displayValue = isFocused ? search : selectedLabel;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = q
      ? allOptions.filter((o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q))
      : allOptions;
    return list.slice(0, 200);
  }, [allOptions, search]);

  const showDrop = open && !disabled;

  useEffect(() => {
    if (hoverIdx >= filtered.length) setHoverIdx(0);
  }, [filtered.length, hoverIdx]);

  useEffect(() => {
    if (showDrop && dropRef.current && hoverIdx >= 0) {
      const el = dropRef.current.children[hoverIdx] as HTMLElement | undefined;
      el?.scrollIntoView?.({ block: "nearest" });
    }
  }, [hoverIdx, showDrop]);

  const updatePos = useCallback(() => {
    if (!rootRef.current) return;
    const r = rootRef.current.getBoundingClientRect();
    setPos({ top: r.bottom + 4, left: r.left, width: r.width });
  }, []);

  useLayoutEffect(() => {
    if (!showDrop) return;
    updatePos();
    window.addEventListener("scroll", updatePos, true);
    window.addEventListener("resize", updatePos);
    return () => {
      window.removeEventListener("scroll", updatePos, true);
      window.removeEventListener("resize", updatePos);
    };
  }, [showDrop, updatePos]);

  useEffect(() => {
    if (!showDrop) return;
    const el = dropRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();
      el.scrollTop += e.deltaY;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [showDrop, filtered]);

  useEffect(() => {
    if (!showDrop) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (rootRef.current?.contains(t)) return;
      if (dropRef.current?.parentElement?.contains(t)) return;
      setOpen(false);
      setSearch("");
      setIsFocused(false);
    };
    document.addEventListener("mousedown", handler, true);
    return () => document.removeEventListener("mousedown", handler, true);
  }, [showDrop]);

  const selectItem = (opt: { value: string; label: string }) => {
    onChange(opt.value);
    setSearch("");
    setOpen(false);
    setIsFocused(false);
  };

  return (
    <div ref={rootRef} data-slot="provider-select" className="relative">
      <div className="relative">
        <Input
          ref={inputRef}
          value={displayValue}
          onChange={(e) => {
            setSearch(e.target.value);
            setOpen(true);
          }}
          placeholder={placeholder || "搜索服务商..."}
          onClick={() => {
            if (!open) { setIsFocused(true); setSearch(""); setOpen(true); }
          }}
          onFocus={() => { setIsFocused(true); setSearch(""); setOpen(true); }}
          onBlur={() => {
            setIsFocused(false);
            setTimeout(() => { setOpen(false); setSearch(""); }, 150);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault(); setOpen(true);
              setHoverIdx((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setHoverIdx((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter") {
              if (open && filtered[hoverIdx]) { e.preventDefault(); selectItem(filtered[hoverIdx]); }
            } else if (e.key === "Escape") {
              setSearch(""); setOpen(false); setIsFocused(false);
            }
          }}
          disabled={disabled}
          className="pr-9"
        />
        <button
          type="button"
          data-slot="provider-select-btn"
          className={cn(
            "absolute right-1.5 top-1/2 -translate-y-1/2 inline-flex items-center justify-center size-6 rounded-sm text-muted-foreground/50 transition-colors cursor-pointer",
            !disabled && "hover:text-muted-foreground"
          )}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            if (!open) { setIsFocused(true); setSearch(""); }
            setOpen((v) => !v);
            inputRef.current?.focus();
          }}
          disabled={disabled}
        >
          <ChevronDownIcon className={cn("size-4 transition-transform", open && "rotate-180")} />
        </button>
      </div>

      {showDrop && createPortal(
        <div
          style={{
            position: "fixed",
            top: pos.top,
            left: pos.left,
            width: pos.width,
            zIndex: 2147483647,
            pointerEvents: "all",
          }}
          className="rounded-md border bg-popover text-popover-foreground shadow-md animate-in fade-in-0 zoom-in-95"
        >
          <div
            ref={dropRef}
            style={{
              maxHeight: 280,
              overflowY: "scroll",
              pointerEvents: "all",
              scrollbarWidth: "thin" as any,
              scrollbarColor: "var(--border) transparent",
            }}
            className="p-1"
          >
            {filtered.length === 0 ? (
              <div className="py-6 text-center text-sm text-muted-foreground">没有匹配项</div>
            ) : (
              filtered.map((opt, idx) => (
                <div
                  key={opt.value}
                  onMouseEnter={() => setHoverIdx(idx)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => selectItem(opt)}
                  className={cn(
                    "relative flex w-full items-center rounded-sm py-1.5 pl-2 pr-8 text-sm cursor-pointer select-none outline-hidden",
                    idx === hoverIdx ? "bg-accent text-accent-foreground" : "text-popover-foreground",
                  )}
                >
                  <span className="truncate">{opt.label}</span>
                  <span className="absolute right-2 flex size-3.5 items-center justify-center">
                    {opt.value === value && <CheckIcon className="size-4" />}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
