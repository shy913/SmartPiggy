import type { InputHTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/utils";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-md border border-border bg-bg px-2 text-sm text-text",
        "placeholder:text-muted",
        className,
      )}
      {...props}
    />
  );
}

export function Field({
  label,
  children,
  className,
}: {
  label: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("flex flex-col gap-1 text-sm text-muted", className)}>
      <span>{label}</span>
      {children}
    </label>
  );
}

/** 一枚小标签。低调的描边样式，不抢金额的注意力。 */
export function Tag({
  children,
  tone = "muted",
  className,
}: {
  children: ReactNode;
  tone?: "muted" | "in" | "out" | "accent";
  className?: string;
}) {
  const tones = {
    muted: "text-muted border-border",
    in: "text-in border-in/40",
    out: "text-out border-out/40",
    accent: "text-accent border-accent/40",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** 页面上的灰字提示。 */
export function Hint({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("mt-3 text-sm text-muted", className)}>{children}</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-md border border-dashed border-border px-4 py-6 text-center text-muted">{children}</p>;
}

export function ErrorText({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <p className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
      {children}
    </p>
  );
}
