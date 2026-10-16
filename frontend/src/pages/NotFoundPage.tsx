import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="py-16 text-center">
      <p className="text-muted">这个地址没有对应的页面。</p>
      <Link className="mt-3 inline-block text-accent underline" to="/">
        回汇总 →
      </Link>
    </div>
  );
}
