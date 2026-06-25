import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export function Section({
  title,
  subtitle,
  children,
  toggle,
  className,
  icon,
  panelRef,
  panelId,
  contentClassName,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
  toggle?: React.ReactNode;
  className?: string;
  icon?: React.ReactNode;
  panelRef?: React.Ref<HTMLDetailsElement>;
  panelId?: string;
  contentClassName?: string;
}) {
  return (
    <details
      ref={panelRef}
      data-panel-id={panelId}
      className={cn(
        "group rounded-xl border border-border/80 bg-card/60 transition-colors open:border-primary/35 open:bg-card",
        className,
      )}
    >
      <summary className="flex cursor-pointer items-center justify-between gap-3 px-3 py-2.5 text-sm select-none list-none transition-colors hover:bg-accent/40 [&::-webkit-details-marker]:hidden">
        <span className="flex min-w-0 items-center gap-2">
          {children ? (
            <ChevronRight className="size-4 shrink-0 transition-transform group-open:rotate-90 text-muted-foreground" />
          ) : (
            <span className="size-4 shrink-0" />
          )}
          {icon && (
            <span className="inline-flex size-5 shrink-0 items-center justify-center rounded-md bg-muted/70 text-muted-foreground">
              {icon}
            </span>
          )}
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="font-semibold text-foreground">{title}</span>
            {subtitle && <span className="text-xs font-normal text-muted-foreground">{subtitle}</span>}
          </span>
        </span>
        {toggle}
      </summary>
      {children && (
        <div className={cn("flex flex-col gap-2.5 border-t border-border/70 px-3 py-2.5", contentClassName)}>
          {children}
        </div>
      )}
    </details>
  );
}

