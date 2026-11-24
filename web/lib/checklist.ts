import type { AccountPlatform, CampaignProfileDetail, ConnectedAccounts } from "./types";

export interface ChecklistItem {
  id: "lank" | "overlay" | "facecam" | "konton";
  label: string;
  done: boolean;
  /** Vad som saknas, när punkten inte är klar. */
  todo: string;
  href: string;
}

const ACCOUNT_LABEL: Record<AccountPlatform, string> = { tiktok: "a TikTok account", youtube_shorts: "a YouTube channel" };

/** Punkterna som gör kampanjen klar. Facecam bara när kampanjen har kamera, overlay bara när den krävs. */
export function checklist(p: CampaignProfileDetail, accounts: ConnectedAccounts | null): ChecklistItem[] {
  const base = `/campaigns/${p.name}?tab=installningar`;
  const items: ChecklistItem[] = [
    {
      id: "lank",
      label: "The link to the campaign in Content Rewards",
      done: !!p.links.content_rewards,
      todo: "Paste the link from the campaign page. Example clips and submitting use it.",
      href: `${base}&del=oversikt#lankar`,
    },
  ];
  if (p.overlay.required) {
    items.push({
      id: "overlay",
      label: "The overlay image",
      done: p.overlay.exists,
      todo: "Upload the image from the campaign page. Without it the clips fail the check.",
      href: `${base}&del=overlay`,
    });
  }
  if (p.facecam?.camera ?? true) {
    items.push({
      id: "facecam",
      label: "Facecam and game boxes",
      done: p.facecam?.rects_set ?? !p.problems.some((x) => x.startsWith("Facecam- och spelrutan")),
      todo: "Drag the boxes over the camera and the game in a frame from the stream.",
      href: `${base}&del=facecam`,
    });
  }
  // Konton: på varje plattform där appen kan posta måste ett konto vara kopplat, och de valda måste vara inloggade.
  const platforms = (p.platforms ?? ["tiktok", "youtube_shorts"]).filter(
    (x): x is AccountPlatform => x === "tiktok" || x === "youtube_shorts"
  );
  if (platforms.length > 0) {
    const missing = platforms.filter((platform) => {
      const connected = (accounts?.[platform] ?? []).filter((a) => a.connected);
      const chosen = p.accounts?.[platform] ?? [];
      return chosen.length ? !chosen.some((c) => connected.some((a) => a.id === c.id)) : connected.length === 0;
    });
    items.push({
      id: "konton",
      label: "The accounts that post",
      done: accounts !== null && missing.length === 0,
      todo: accounts
        ? `Connect or choose ${missing.map((m) => ACCOUNT_LABEL[m]).join(" and ")}.`
        : "Loading the accounts…",
      href: `${base}&del=oversikt#konton`,
    });
  }
  return items;
}
