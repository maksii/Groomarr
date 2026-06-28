import { Outlet } from "react-router-dom";
import { MobileNav } from "./MobileNav";
import { Sidebar } from "./Sidebar";
import { StatusPill } from "./StatusPill";
import { ThemeToggle } from "./ThemeToggle";

export function AppShell() {
  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-3 border-b border-border bg-background/80 px-4 backdrop-blur md:px-8">
          <div className="text-base font-semibold md:hidden">Groomarr</div>
          <div className="hidden md:block" aria-hidden />
          <div className="flex items-center gap-2">
            <StatusPill />
            <ThemeToggle />
          </div>
        </header>
        <MobileNav />
        <main className="flex-1 overflow-y-auto scrollbar-thin">
          <div className="mx-auto w-full max-w-5xl px-4 py-6 md:px-8 md:py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
