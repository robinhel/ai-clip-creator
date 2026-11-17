import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { TIKTOK_APP, TIKTOK_VERIFIED_PREFIX, YOUTUBE_REDIRECT_URI } from "./appLinks";

// Serverns standardadresser. Skiljer de sig från det inställningssidan visar, misslyckas inloggningen.
const config = readFileSync(path.join(__dirname, "../../campaign/config.py"), "utf-8");
const serverDefault = (name: string) => config.match(new RegExp(`${name}: str = "([^"]+)"`))?.[1];

describe("adresserna i TikTok- och Google-apparna", () => {
  it("redirect-adresserna är serverns standard", () => {
    expect(TIKTOK_APP.find((l) => l.id === "redirect")?.url).toBe(serverDefault("tiktok_redirect_uri"));
    expect(YOUTUBE_REDIRECT_URI).toBe(serverDefault("youtube_redirect_uri"));
  });

  it("webbsidan och villkoren ligger under prefixet som TikTok har verifierat", () => {
    for (const id of ["web", "terms", "privacy"]) {
      expect(TIKTOK_APP.find((l) => l.id === id)?.url.startsWith(TIKTOK_VERIFIED_PREFIX)).toBe(true);
    }
  });
});
