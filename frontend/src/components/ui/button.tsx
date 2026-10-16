import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes, AnchorHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

const button = cva(
  "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors " +
    "disabled:pointer-events-none disabled:opacity-50 cursor-pointer whitespace-nowrap",
  {
    variants: {
      variant: {
        // 主按钮：底色是强调色，字色跟着主题走（浅色主题的强调色很深，得配白字；
        // 深色的偏亮，得配深字）——所以 on-accent 是个变量，不是写死的 white。
        default: "bg-accent text-on-accent hover:opacity-90",
        outline: "border border-border bg-panel hover:bg-panel-hover",
        ghost: "hover:bg-panel-hover",
        danger: "border border-danger/40 text-danger hover:bg-danger/10",
      },
      size: {
        default: "h-9 px-3 text-sm",
        sm: "h-8 px-2.5 text-xs",
        lg: "h-11 px-5 text-base",
        // 手机上点得准的最小尺寸
        touch: "h-11 px-4 text-sm",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof button>;

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(button({ variant, size }), className)} {...props} />;
}

type LinkButtonProps = AnchorHTMLAttributes<HTMLAnchorElement> &
  VariantProps<typeof button>;

/** 长得像按钮的链接。用 `<a>` 而不是 `<button onClick=navigate>`：
 *  浏览器右键、新标签页打开、中键这些行为都还在。 */
export function LinkButton({ className, variant, size, ...props }: LinkButtonProps) {
  return <a className={cn(button({ variant, size }), className)} {...props} />;
}

export { button as buttonStyles };
