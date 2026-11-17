import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { CampaignStatus, Platform } from "@/lib/types";

export const STATUS_LABEL: Record<CampaignStatus, string> = {
  renderad: "Att granska",
  underkand: "Underkänd",
  godkand: "Godkänd",
  nekad: "Nekad",
  postad: "Postad",
  inskickad: "Inskickad",
  analytics_inskickad: "Analytics inskickad",
  betald: "Betald",
};

export const STATUS_ORDER: CampaignStatus[] = [
  "renderad",
  "underkand",
  "godkand",
  "postad",
  "inskickad",
  "analytics_inskickad",
  "betald",
  "nekad",
];

export const PLATFORM_LABEL: Record<Platform, string> = {
  tiktok: "TikTok",
  youtube_shorts: "YouTube Shorts",
  instagram_reels: "Instagram Reels",
};

const STATUS_CLASS: Record<CampaignStatus, string> = {
  renderad: "bg-amber-100 text-amber-900 dark:bg-amber-500/20 dark:text-amber-200",
  underkand: "bg-destructive/10 text-destructive",
  godkand: "bg-sky-100 text-sky-900 dark:bg-sky-500/20 dark:text-sky-200",
  nekad: "bg-muted text-muted-foreground",
  postad: "bg-violet-100 text-violet-900 dark:bg-violet-500/20 dark:text-violet-200",
  inskickad: "bg-indigo-100 text-indigo-900 dark:bg-indigo-500/20 dark:text-indigo-200",
  analytics_inskickad: "bg-teal-100 text-teal-900 dark:bg-teal-500/20 dark:text-teal-200",
  betald: "bg-emerald-100 text-emerald-900 dark:bg-emerald-500/20 dark:text-emerald-200",
};

export function CampaignStatusBadge({ status }: { status: CampaignStatus }) {
  return (
    <Badge variant="secondary" className={cn(STATUS_CLASS[status])}>
      {STATUS_LABEL[status] ?? status}
    </Badge>
  );
}
