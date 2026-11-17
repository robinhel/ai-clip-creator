// Adresserna som är inlagda i TikTok-appen robin-klipp-verktyg och i Google-klienten för YouTube.
// Inställningssidan visar dem. Ändras något i utvecklarportalen, ändra det här.

/** Prefixet som TikTok har verifierat. Webbsidan och villkoren måste ligga under det. */
export const TIKTOK_VERIFIED_PREFIX = "https://robinhel.github.io/";

export interface AppLink {
  id: string;
  /** Fältets namn i utvecklarportalen. */
  label: string;
  url: string;
  /** false för adresser på den egna datorn, som inte går att öppna som en sida. */
  page: boolean;
}

/** TikTok-appen robin-klipp-verktyg (Production). Sidorna ligger i repot robinhel/robinhel.github.io. */
export const TIKTOK_APP: AppLink[] = [
  { id: "web", label: "Web/Desktop URL", url: TIKTOK_VERIFIED_PREFIX, page: true },
  { id: "terms", label: "Terms of Service URL", url: `${TIKTOK_VERIFIED_PREFIX}terms/`, page: true },
  { id: "privacy", label: "Privacy Policy URL", url: `${TIKTOK_VERIFIED_PREFIX}privacy/`, page: true },
  { id: "redirect", label: "Redirect URI (Login Kit, Desktop)", url: "http://127.0.0.1:8000/campaign/tiktok/callback/", page: false },
];

export const TIKTOK_REDIRECT_URI = TIKTOK_APP.find((l) => l.id === "redirect")!.url;

export const YOUTUBE_REDIRECT_URI = "http://127.0.0.1:8000/campaign/youtube/callback/";
