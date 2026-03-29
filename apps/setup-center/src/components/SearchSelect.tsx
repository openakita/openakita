import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { XIcon, ChevronDownIcon, CheckIcon } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export function SearchSelect({
  value,
  onChange,
  options,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [hoverIdx, setHoverIdx] = useState(0);
  const [search, setSearch] = useState("");
  const [pos, setPos] = useState({ top: 0, left: 0, width: 0 });
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const hasOptions = options.length > 0;
  const showDrop = open && hasOptions && !disabled;

  const displayValue = hasOptions ? (search || value) : value;

  const closeMenu = useCallback(() => {
    setOpen(false);
    setSearch("");
  }, []);

  const filtered = useMemo(() => {
    if (!hasOptions) return [];
    const q = search.trim().toLowerCase();
    return q ? options.filter((x) => x.toLowerCase().includes(q)) : options;
  }, [options, search, hasOptions]);

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

  // Native wheel event listener — bypasses React and any framework event interception
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

  // Close when window loses focus (e.g. click outside Tauri window)
  useEffect(() => {
    if (!showDrop) return;
    const onBlur = () => closeMenu();
    window.addEventListener("blur", onBlur);
    return () => window.removeEventListener("blur", onBlur);
  }, [showDrop, closeMenu]);

  // Close on click outside
  useEffect(() => {
    if (!showDrop) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (rootRef.current?.contains(t)) return;
      if (dropRef.current?.parentElement?.contains(t)) return;
      closeMenu();
    };
    document.addEventListener("mousedown", handler, true);
    return () => document.removeEventListener("mousedown", handler, true);
  }, [showDrop, closeMenu]);

  const selectItem = (v: string) => {
    onChange(v);
    setSearch("");
    setOpen(false);
    inputRef.current?.focus();
  };

  return (
    <div ref={rootRef} data-slot="search-select" className="relative flex-1 min-w-0">
      <div className="relative">
        <Input
          ref={inputRef}
          value={displayValue}
          onChange={(e) => {
            const v = e.target.value;
            if (hasOptions) { setSearch(v); setOpen(true); }
            onChange(v);
          }}
          placeholder={placeholder}
          onClick={() => { if (hasOptions && !open) setOpen(true); }}
          onKeyDown={(e) => {
            if (!hasOptions) return;
            if (e.key === "ArrowDown") {
              e.preventDefault(); setOpen(true);
              setHoverIdx((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setHoverIdx((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter") {
              if (open && filtered[hoverIdx]) { e.preventDefault(); selectItem(filtered[hoverIdx]); }
              else if (hasOptions && search.trim()) { e.preventDefault(); selectItem(search.trim()); }
            } else if (e.key === "Escape") { closeMenu(); }
          }}
          disabled={disabled}
          className={cn(hasOptions && "pr-16")}
        />
        <div className="absolute right-1.5 top-1/2 -translate-y-1/2 flex items-center gap-0.5">
          {hasOptions && (value || search) && !disabled && (
            <button type="button" data-slot="search-select-btn"
              className="inline-flex items-center justify-center size-6 rounded-sm text-muted-foreground/50 hover:text-muted-foreground transition-colors cursor-pointer"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => { setSearch(""); onChange(""); setOpen(true); inputRef.current?.focus(); }}
              title="清空"
            ><XIcon className="size-3.5" /></button>
          )}
          {hasOptions && (
            <button type="button" data-slot="search-select-btn"
              className={cn("inline-flex items-center justify-center size-6 rounded-sm text-muted-foreground/50 transition-colors cursor-pointer", !disabled && "hover:text-muted-foreground")}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => { if (!open) { setSearch(""); setOpen(true); } else { setOpen(false); } inputRef.current?.focus(); }}
              disabled={disabled}
            ><ChevronDownIcon className={cn("size-4 transition-transform", open && "rotate-180")} /></button>
          )}
        </div>
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
                  key={opt}
                  onMouseEnter={() => setHoverIdx(idx)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => selectItem(opt)}
                  className={cn(
                    "relative flex w-full items-center rounded-sm py-1.5 pl-2 pr-8 text-sm cursor-pointer select-none outline-hidden",
                    idx === hoverIdx ? "bg-accent text-accent-foreground" : "text-popover-foreground",
                  )}
                >
                  <span className="truncate">{opt}</span>
                  <span className="absolute right-2 flex size-3.5 items-center justify-center">
                    {opt === value && <CheckIcon className="size-4" />}
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
