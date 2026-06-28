import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, Navigate, RouterProvider, createBrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { AppShell } from "@/components/layout/AppShell";
import { buttonClasses } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ThemeProvider, useTheme } from "@/hooks/useTheme";
import { DashboardPage } from "@/pages/DashboardPage";
import { RulesPage } from "@/pages/RulesPage";
import { StatusPage } from "@/pages/StatusPage";
import { ToolsPage } from "@/pages/ToolsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 },
    mutations: { retry: 0 },
  },
});

function NotFound() {
  return (
    <div className="flex flex-col items-center gap-4 py-24 text-center">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <Link to="/" className={buttonClasses("primary", "md")}>
        Back to dashboard
      </Link>
    </div>
  );
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "rules", element: <RulesPage /> },
      { path: "simulator", element: <Navigate to="/rules" replace /> },
      { path: "tools", element: <ToolsPage /> },
      { path: "status", element: <StatusPage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

function AppToaster() {
  const { theme } = useTheme();
  return <Toaster theme={theme} position="top-right" richColors closeButton />;
}

export default function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider delayDuration={200}>
          <RouterProvider router={router} />
          <AppToaster />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
