import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  description?: string;
  /** Pulled to the right of the title: one action, usually. */
  aside?: ReactNode;
  children?: ReactNode;
}

export function Card({ title, description, aside, children }: CardProps) {
  return (
    <section className="rounded-xl border border-line bg-surface">
      {title ? (
        <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            {description ? (
              <p className="mt-0.5 text-sm text-ink-soft">{description}</p>
            ) : null}
          </div>
          {aside}
        </header>
      ) : null}
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}
