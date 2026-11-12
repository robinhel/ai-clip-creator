"use client";

import Link from "next/link";
import { Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { serverTime } from "@/lib/utils";
import type { CampaignRunInfo, Run, RunStatus } from "@/lib/types";

const STATUS_VARIANT: Record<RunStatus, "default" | "secondary" | "destructive" | "outline"> = {
  pending: "outline",
  running: "default",
  done: "secondary",
  failed: "destructive",
};

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  pending: "Väntar",
  running: "Kör",
  done: "Klar",
  failed: "Misslyckades",
};

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400_000],
  ["hour", 3_600_000],
  ["minute", 60_000],
];

export function relativeTime(iso: string): string {
  const diff = Date.now() - serverTime(iso);
  const format = new Intl.RelativeTimeFormat("sv", { numeric: "auto" });
  for (const [unit, ms] of UNITS) {
    if (diff >= ms) return format.format(-Math.floor(diff / ms), unit);
  }
  return "nyss";
}

interface RunCardProps {
  run: Run;
  campaign?: CampaignRunInfo;
  onDelete?: (id: number) => void;
}

export function RunCard({ run, campaign, onDelete }: RunCardProps) {
  async function handleDelete(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    try {
      await api.runs.delete(run.id);
      onDelete?.(run.id);
    } catch (err) {
      console.error("Kunde inte ta bort körningen:", err);
    }
  }

  return (
    <Link href={`/runs/${run.id}`} className="block min-w-0 hover:no-underline">
      <Card className="h-full hover:shadow-md transition-shadow cursor-pointer">
        <CardHeader className="grid-cols-[minmax(0,1fr)_auto] gap-x-2">
          <div className="min-w-0">
            <p className="truncate text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {campaign ? campaign.profile : "Utan kampanj"}
              {campaign?.game ? ` · ${campaign.game}` : ""}
            </p>
            <CardTitle className="font-mono break-all">{run.vod_id}</CardTitle>
            {run.channel && (
              <p className="truncate text-sm text-muted-foreground" title={`@${run.channel}`}>
                @{run.channel}
              </p>
            )}
          </div>
          <div className="flex items-center gap-1">
            <Badge variant={STATUS_VARIANT[run.status]}>{RUN_STATUS_LABEL[run.status]}</Badge>
            {run.status === "failed" && onDelete && (
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6 text-muted-foreground hover:text-destructive"
                onClick={handleDelete}
                aria-label="Ta bort körningen"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="mt-auto space-y-1">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span>{run.clip_count} klipp</span>
            <span>{relativeTime(run.created_at)}</span>
          </div>
          {run.stage && run.status === "running" && (
            <p className="text-xs text-blue-500">{run.stage}…</p>
          )}
          {run.error && run.status === "failed" && (
            <p className="text-xs text-destructive line-clamp-2" title={run.error}>
              {run.error}
            </p>
          )}
        </CardContent>
      </Card>
    </Link>
  );
}
