/**
 * The frame every page is drawn inside.
 *
 * It is a route layout rather than a wrapper each page imports: a page that
 * has to remember to include the chrome is a page that will one day forget.
 */

import { Outlet } from "react-router-dom";

import { ToastStack } from "@/presentation/components/ui/ToastStack";

import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";

export function AppShell() {
  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <aside className="w-56 shrink-0 border-r border-line bg-surface">
          <SideNav />
        </aside>
        <main className="min-w-0 flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
      <ToastStack />
    </div>
  );
}
