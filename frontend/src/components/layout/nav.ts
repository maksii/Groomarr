import type { LucideIcon } from "lucide-react";
import { LayoutDashboard, ScrollText, SlidersHorizontal, Wrench } from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end: boolean;
}

export const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/rules", label: "Rules", icon: ScrollText, end: false },
  { to: "/tools", label: "Tools", icon: Wrench, end: false },
  { to: "/status", label: "Status", icon: SlidersHorizontal, end: false },
];
