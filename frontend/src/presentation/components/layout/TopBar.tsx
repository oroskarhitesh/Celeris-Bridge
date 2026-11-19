import { SignOutButton } from "@/presentation/components/session/SignOutButton";

export function TopBar() {
  return (
    <header className="flex h-14 items-center justify-between border-b border-line bg-surface px-5">
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-semibold tracking-tight text-ink">
          Celeris Bridge
        </span>
        <span className="text-xs text-ink-soft">Agent console</span>
      </div>
      <SignOutButton />
    </header>
  );
}
