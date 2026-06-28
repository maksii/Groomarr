import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type Tone = "info" | "warning" | "destructive" | "success";

const tones: Record<Tone, string> = {
  info: "border-primary/30 bg-primary/10 text-foreground",
  warning: "border-warning/40 bg-warning/10 text-foreground",
  destructive: "border-destructive/40 bg-destructive/10 text-foreground",
  success: "border-success/40 bg-success/10 text-foreground",
};

const iconTones: Record<Tone, string> = {
  info: "text-primary",
  warning: "text-warning",
  destructive: "text-destructive",
  success: "text-success",
};

export function Banner({
  tone = "info",
  icon,
  title,
  children,
  action,
  className,
}: {
  tone?: Tone;
  icon?: ReactNode;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start gap-3 rounded-lg border p-4", tones[tone], className)}>
      {icon ? <div className={cn("mt-0.5 shrink-0", iconTones[tone])}>{icon}</div> : null}
      <div className="min-w-0 flex-1 text-sm">
        {title ? <div className="font-medium">{title}</div> : null}
        {children ? <div className="text-muted-foreground">{children}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
