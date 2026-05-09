import { ChevronRight } from "lucide-react";

export function Section({ title, subtitle, children, toggle, className }: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
  toggle?: React.ReactNode;
  className?: string;
}) {
  return (
    <details className={`group rounded-lg border border-border ${className ?? ""}`}>
      <summary className="flex cursor-pointer items-center justify-between px-3 py-2 text-sm font-medium select-none list-none transition-colors hover:bg-accent/50 [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-1.5">
          {children ? (
            <ChevronRight className="size-4 shrink-0 transition-transform group-open:rotate-90 text-muted-foreground" />
          ) : (
            <span className="size-4 shrink-0" />
          )}
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span>{title}</span>
            {subtitle && <span className="text-xs font-normal text-muted-foreground">{subtitle}</span>}
          </span>
        </span>
        {toggle}
      </summary>
      {children && (
        <div className="flex flex-col gap-2.5 border-t border-border px-3 py-2.5">
          {children}
        </div>
      )}
    </details>
  );
}

