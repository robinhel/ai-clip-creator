/** Det som står i fältet på Hem: VOD-id:t och starten i sekunder från ?t=, eller null. */
export interface TwitchLink {
  vodId: string | null;
  startS: number | null;
}

/** Twitchs tid i ?t=: "1h2m3s", "5m", "90s" eller bara sekunder. null om den inte går att läsa. */
function twitchTime(text: string): number | null {
  if (/^\d+$/.test(text)) return Number(text);
  const m = text.match(/^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/i);
  if (!m?.[0]) return null; // en tom text passar mönstret men är ingen tid
  const [h, min, s] = m.slice(1).map((part) => Number(part ?? 0));
  return h * 3600 + min * 60 + s;
}

/**
 * Siffrorna ur en Twitch-länk, eller texten om den bara är siffror. Förstår twitch.tv/videos/123 och
 * twitch.tv/kanal/v/123, och ?t=1h2m3s som start.
 */
export function parseTwitchLink(text: string): TwitchLink {
  const value = text.trim();
  if (/^\d+$/.test(value)) return { vodId: value, startS: null };
  const vodId = value.match(/twitch\.tv\/(?:videos|[\w-]+\/v(?:ideo)?)\/(\d+)/i)?.[1] ?? null;
  const t = value.match(/[?&#]t=([0-9hms]+)/i)?.[1];
  return { vodId, startS: vodId && t ? twitchTime(t) : null };
}
