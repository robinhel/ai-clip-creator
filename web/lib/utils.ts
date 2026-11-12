import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Serverns tider saknar ibland tidszon (SQLite) men är alltid UTC. */
export function serverTime(iso: string): number {
  return new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`).getTime()
}
