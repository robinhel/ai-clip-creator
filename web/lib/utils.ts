import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import type { CampaignClip, Platform } from "./types"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Serverns tider saknar ibland tidszon (SQLite) men är alltid UTC. */
export function serverTime(iso: string): number {
  return new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`).getTime()
}

/** Sekunder som t:mm:ss, eller m:ss under en timme. */
export function clock(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60).toString().padStart(2, "0")
  return h ? `${h}:${m.toString().padStart(2, "0")}:${s}` : `${m}:${s}`
}

/** "t:mm:ss", "m:ss" eller sekunder till sekunder. null om texten inte är en tid. */
export function parseClock(text: string): number | null {
  const parts = text.trim().split(":")
  if (parts.length > 3 || parts.some((p) => !/^\d+$/.test(p))) return null
  // Minuter och sekunder efter ett kolon måste vara under 60.
  if (parts.slice(1).some((p) => Number(p) >= 60)) return null
  return parts.reduce((total, p) => total * 60 + Number(p), 0)
}

/** Längd i ord: "3 h 52 min", "14 min" eller "40 s". */
export function duration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} s`
  const minutes = Math.round(seconds / 60)
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return h ? `${h} h ${m.toString().padStart(2, "0")} min` : `${m} min`
}

export const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 })

/** Händelse när ett klipp har ändrats, så att sidomenyns siffror läses om. */
export const SUMMARY_CHANGED = "klipp:summary-changed"

/** Kampanjen postar på plattformen. Äldre servrar skickar inte platforms: då alla. */
export const allows = (clip: CampaignClip, platform: Platform) => !clip.platforms || clip.platforms.includes(platform)
