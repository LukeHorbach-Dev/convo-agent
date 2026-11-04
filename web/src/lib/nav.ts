import {
  AtSign, BarChart3, Bell, Brain, CalendarDays, Clapperboard, Clock, FileText, FolderKanban, Hand, Handshake, History,
  KeyRound, LayoutDashboard, Library, MessageCircleReply, MessagesSquare, Plug, Rocket, Settings, Sparkles, Sprout, Target,
  Trophy, Users, Workflow,
} from "lucide-react";

export type NavItem = { path: string; label: string; icon: any; badge?: "replies" | "hellos" };
export type NavSection = { title: string; items: NavItem[] };

// The sidebar, exactly as in the brief. Every item opens a working page.
export const NAV: NavSection[] = [
  { title: "Overview", items: [
    { path: "/", label: "Dashboard", icon: LayoutDashboard },
    { path: "/strategist", label: "AI Strategist", icon: Sparkles },
    { path: "/notifications", label: "Notifications", icon: Bell },
  ] },
  { title: "Social accounts", items: [
    { path: "/accounts", label: "Connected Accounts", icon: AtSign },
    { path: "/launch", label: "Launch Network", icon: Rocket },
    { path: "/groups", label: "Account Groups", icon: FolderKanban },
  ] },
  { title: "Content", items: [
    { path: "/studio", label: "Content Studio", icon: Clapperboard },
    { path: "/library", label: "Content Library", icon: Library },
    { path: "/calendar", label: "Publishing Calendar", icon: CalendarDays },
    { path: "/scheduled", label: "Scheduled Posts", icon: Clock },
  ] },
  { title: "Audience", items: [
    { path: "/hello", label: "Hello Message", icon: Hand, badge: "hellos" },
    { path: "/conversations", label: "Conversations", icon: MessagesSquare, badge: "replies" },
    { path: "/contacts", label: "Contacts", icon: Users },
    { path: "/engagement", label: "Engagement Assistant", icon: MessageCircleReply },
    { path: "/collaborations", label: "Collaboration Opportunities", icon: Handshake },
  ] },
  { title: "Growth", items: [
    { path: "/analytics", label: "Analytics", icon: BarChart3 },
    { path: "/missions", label: "Weekly Missions", icon: Trophy },
    { path: "/goals", label: "Growth Goals", icon: Target },
    { path: "/independence", label: "Organic Independence", icon: Sprout },
  ] },
  { title: "Automation", items: [
    { path: "/automations", label: "Automation Centre", icon: Workflow },
    { path: "/automation-history", label: "Automation History", icon: History },
    { path: "/reports", label: "Reports", icon: FileText },
  ] },
  { title: "Settings", items: [
    { path: "/integrations", label: "Integrations", icon: Plug },
    { path: "/api-connections", label: "API Connections", icon: KeyRound },
    { path: "/ai", label: "AI Preferences", icon: Brain },
    { path: "/settings", label: "Account Settings", icon: Settings },
  ] },
];

export const ALL_ITEMS = NAV.flatMap((s) => s.items);
export const titleFor = (path: string) => ALL_ITEMS.find((i) => i.path === path)?.label || "Convo";

// The phone's bottom bar: the things you check on the go.
export const MOBILE_TABS = ["/", "/hello", "/conversations", "/strategist"];
