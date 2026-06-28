import type { LucideIcon } from "lucide-react";
import { FlaskConical, ScrollText, SlidersHorizontal, Wrench } from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end: boolean;
}

export const NAV: NavItem[] = [
  { to: "/", label: "Rules", icon: ScrollText, end: true },
  { to: "/simulator", label: "Simulator", icon: FlaskConical, end: false },
  { to: "/tools", label: "Tools", icon: Wrench, end: false },
  { to: "/status", label: "Status", icon: SlidersHorizontal, end: false },
];
