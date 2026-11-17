import type { AccountPlatform, CampaignProfileDetail, ConnectedAccounts } from "./types";

export interface ChecklistItem {
  id: "lank" | "overlay" | "facecam" | "konton";
  label: string;
  done: boolean;
  /** Vad som saknas, när punkten inte är klar. */
  todo: string;
  href: string;
}

const ACCOUNT_LABEL: Record<AccountPlatform, string> = { tiktok: "ett TikTok-konto", youtube_shorts: "en YouTube-kanal" };

/** Punkterna som gör kampanjen klar. Facecam bara när kampanjen har kamera, overlay bara när den krävs. */
export function checklist(p: CampaignProfileDetail, accounts: ConnectedAccounts | null): ChecklistItem[] {
  const base = `/campaigns/${p.name}?tab=installningar`;
  const items: ChecklistItem[] = [
    {
      id: "lank",
      label: "Länken till kampanjen i Content Rewards",
      done: !!p.links.content_rewards,
      todo: "Klistra in länken från kampanjsidan. Exempelklipp och inskicket använder den.",
      href: `${base}&del=oversikt#lankar`,
    },
  ];
  if (p.overlay.required) {
    items.push({
      id: "overlay",
      label: "Overlay-bilden",
      done: p.overlay.exists,
      todo: "Ladda upp bilden från kampanjsidan. Utan den blir klippen underkända.",
      href: `${base}&del=overlay`,
    });
  }
  if (p.facecam?.camera ?? true) {
    items.push({
      id: "facecam",
      label: "Facecam- och spelrutan",
      done: p.facecam?.rects_set ?? !p.problems.some((x) => x.startsWith("Facecam- och spelrutan")),
      todo: "Dra rutorna över kameran och spelet i en bild från streamen.",
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
      label: "Kontona som postar",
      done: accounts !== null && missing.length === 0,
      todo: accounts
        ? `Koppla eller välj ${missing.map((m) => ACCOUNT_LABEL[m]).join(" och ")}.`
        : "Läser kontona…",
      href: `${base}&del=oversikt#konton`,
    });
  }
  return items;
}
