import Link from "next/link";
import { ExternalLink } from "lucide-react";
import { PLATFORM_LABEL } from "@/components/CampaignStatusBadge";
import type { CampaignProfileDetail } from "@/lib/types";

const num = new Intl.NumberFormat("en-US");
const pct = (n: number) => `${String(n).replace(".", ",")} %`;

/** Engagemangskravet i ord, till exempel "minst 0,9 % upp till 25 000 visningar, sedan 0,5 %". */
export function engagementText(steps: { views_up_to: number | null; min: number }[]): string | null {
  if (steps.length === 0) return null;
  const parts = steps.map((s) => (s.views_up_to == null ? `${pct(s.min)} över det` : `${pct(s.min)} upp till ${num.format(s.views_up_to)} visningar`));
  if (steps.length === 1 && steps[0].views_up_to == null) return `Minst ${pct(steps[0].min)} engagemang.`;
  return `Minst ${parts.join(", ")}.`;
}

/** Kampanjens regler: samma som på Att posta, plus material, plattformar, konton och gränser. */
export function CampaignRules({ profile: p }: { profile: CampaignProfileDetail }) {
  const accounts = (p.accounts?.tiktok ?? []).concat(p.accounts?.youtube_shorts ?? []);
  const platforms = (p.platforms ?? []).map((x) => PLATFORM_LABEL[x] ?? x).join(", ");
  const engagement = engagementText(p.engagement ?? []);
  const link = "inline-flex items-center gap-1 font-medium text-foreground underline underline-offset-4";
  return (
    <section aria-label="Regler" className="space-y-3 rounded-2xl border border-line bg-panel p-5 text-sm">
      <h2 className="font-semibold">Regler</h2>
      {p.material?.allowed && (
        <div>
          <h3 className="text-[13px] text-muted-foreground">Tillåtet material</h3>
          <p>{p.material.allowed}</p>
          {p.material.banned.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
              {p.material.banned.map((b) => (
                <li key={b}>Inte: {b}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {p.rules.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
          {p.rules.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
      )}
      {p.caption.tag_line && <p className="text-muted-foreground">Captionen har raden {p.caption.tag_line} efter texten.</p>}
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        {platforms && (
          <>
            <dt className="text-muted-foreground">Plattformar</dt>
            <dd>{platforms}</dd>
          </>
        )}
        <dt className="text-muted-foreground">Konton</dt>
        <dd>{accounts.length ? accounts.map((a) => a.name ?? "ett konto som inte är kopplat").join(", ") : "alla kopplade"}</dd>
        {p.posting && (
          <>
            <dt className="text-muted-foreground">Inlägg</dt>
            <dd>
              högst {p.posting.max_per_day} per konto och dag, minst {p.posting.min_gap_minutes} minuter mellan
            </dd>
          </>
        )}
        {engagement && (
          <>
            <dt className="text-muted-foreground">Engagemang</dt>
            <dd>{engagement}</dd>
          </>
        )}
      </dl>
      {p.approval && <p className="text-muted-foreground">{p.approval}</p>}
      <p className="flex flex-wrap gap-x-5 gap-y-1">
        {p.links.content_rewards && (
          <a href={p.links.content_rewards} target="_blank" rel="noreferrer" className={link}>
            Öppna Content Rewards
            <ExternalLink className="size-3.5" aria-hidden />
          </a>
        )}
        {p.links.analytics_form && (
          <a href={p.links.analytics_form} target="_blank" rel="noreferrer" className={link}>
            Analytics-formuläret
            <ExternalLink className="size-3.5" aria-hidden />
          </a>
        )}
      </p>
      <p className="text-xs text-muted-foreground">
        Ändra reglerna under{" "}
        <Link href={`/campaigns/${p.name}?tab=installningar&del=profil`} className="underline underline-offset-4 hover:text-foreground">
          Avancerat (YAML)
        </Link>
        .
      </p>
    </section>
  );
}
