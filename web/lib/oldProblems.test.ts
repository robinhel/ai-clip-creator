import { describe, expect, it } from "vitest";
import { englishProblem } from "./oldProblems";
import { NO_OWN } from "./versionOwn";
import { fixHint, rejectedFix } from "./steps";
import type { CampaignClip } from "./types";

const failed = (vod_id: string, problems: string[]) => ({ status: "underkand", vod_id, problems }) as CampaignClip;

describe("old Swedish and new English rule check texts", () => {
  // Klipp från före översättningen har svensk text i klipp.db. Nya klipp har engelsk.
  const old = {
    caption: "Captionen saknar hashtaggen #ad.",
    short: "Klippet är 4,0 s. Kampanjen kräver minst 7 s.",
    long: "Klippet är 72,4 s. Profilens tak är 60 s.",
  };
  const current = {
    caption: "Caption is missing the hashtag #ad.",
    short: "The clip is 4.0 s. The campaign requires at least 7 s.",
    long: "The clip is 72.4 s. The campaign allows at most 60 s.",
  };

  it.each([
    ["old Swedish", old],
    ["new English", current],
  ])("recognizes %s texts in the fix for a rejected clip", (_, texts) => {
    expect(rejectedFix(failed("v123", [texts.caption]))).toEqual({ kind: "caption" });
    expect(rejectedFix(failed("v123", [texts.short]))).toEqual({ kind: "kort" });
    expect(rejectedFix(failed("v123", [texts.long, texts.caption]))).toEqual({ kind: "lang", max: "60", caption: true });
    expect(fixHint(rejectedFix(failed("v123", [texts.long]))!)).toContain("at most 60 s.");
  });

  it("shows an old Swedish problem in English when it is known", () => {
    expect(englishProblem(old.caption)).toBe(current.caption);
    expect(englishProblem(old.short)).toBe(current.short);
    expect(englishProblem(old.long)).toBe(current.long);
    expect(englishProblem("Klippet är 1,7 s. Kampanjen kräver minst 7 s. Ladda upp en längre video eller tryck Nej.")).toBe(
      "The clip is 1.7 s. The campaign requires at least 7 s. Upload a longer video or press No."
    );
    expect(englishProblem("Captionen saknar 'twitch.tv/skiimasktimmy' på första raden.")).toBe(
      "Caption is missing 'twitch.tv/skiimasktimmy' on the first line."
    );
    expect(englishProblem("Klippet saknar ljud. Streamens ljud måste höras.")).toBe(
      "The clip has no sound. The stream's sound must be heard."
    );
  });

  it("keeps new English and unknown texts as they are", () => {
    expect(englishProblem(current.long)).toBe(current.long);
    expect(englishProblem("Något helt annat.")).toBe("Något helt annat.");
  });
});

describe("every old Swedish text has its English text", () => {
  it.each([
    ["Captionen har kvar platshållaren {title}. Skriv en egen text.", "Caption still has the placeholder {title}. Write your own text."],
    ["Captionen saknar '#ad #gaming' på en egen rad efter texten.", "Caption is missing '#ad #gaming' on its own line after the text."],
    ["Captionen har hashtags på första raden. De ska stå efter '#ad'.", "Caption has hashtags on the first line. They belong after '#ad'."],
    [
      "Captionen har 5 hashtags utöver de obligatoriska. Kampanjen tillåter högst 3.",
      "Caption has 5 hashtags besides the required ones. The campaign allows at most 3.",
    ],
    ["Captionen har 5 hashtags utöver #ad. Kampanjen tillåter högst 3.", "Caption has 5 hashtags besides #ad. The campaign allows at most 3."],
    ["Captionen saknar hashtaggen #ad.", "Caption is missing the hashtag #ad."],
    ["Captionen saknar 'twitch.tv/x' på första raden.", "Caption is missing 'twitch.tv/x' on the first line."],
    ["Captionen saknar 'twitch.tv/x'.", "Caption is missing 'twitch.tv/x'."],
    ["Klippet är 4,6 s. Kampanjen kräver minst 7 s.", "The clip is 4.6 s. The campaign requires at least 7 s."],
    ["Klippet är 72,4 s. Profilens tak är 60 s.", "The clip is 72.4 s. The campaign allows at most 60 s."],
    [
      "Klippet är 1920×1080, inte 1080×1920. Sätt reframe.mode: stacked (eller center) i config.",
      "The clip is 1920×1080, not 1080×1920. Set reframe.mode: stacked (or center) in the config.",
    ],
    [
      "Originalljudet hörs inte. Kampanjen har en regel om ljud: Keep the sound.",
      "The original sound cannot be heard. The campaign has a rule about sound: Keep the sound.",
    ],
    ["Formatet clutch finns inte i kampanjens lista.", "The format clutch is not in the campaign's list."],
    [
      "Klippet har ett inlägg utan konto på TikTok. Det kan redan ligga på det här kontot. Kontrollera innan du postar.",
      "The clip has a post without an account on TikTok. It may already be on this account. Check before you post.",
    ],
    [
      "Musiken wow.mp3 finns inte längre. Lägg tillbaka den i musik/timmy/, eller gör om klippet på Egna videor.",
      "The music wow.mp3 no longer exists. Put it back in musik/timmy/, or redo the clip on Own videos.",
    ],
  ])("a pattern: %s", (old, english) => {
    expect(englishProblem(old)).toBe(english);
  });

  it.each([
    ["Klippet har inget format ännu. Välj ett när du godkänner.", "The clip has no format yet. Choose one when you approve."],
    ["Klippet har ingen kampanj. Välj kampanj först.", "The clip has no campaign. Choose a campaign first."],
    ["Klippet saknar ljud. Streamens ljud måste höras.", "The clip has no sound. The stream's sound must be heard."],
    ["Texten på skärmen saknas.", "The on-screen text is missing."],
    ["Overlayen saknas. Lägg bilden i overlays/ och kör finish igen.", "The overlay is missing. Put the image in overlays/ and run finish again."],
    ["Ingen bildgranskning gjord. Kontrollera själv att facecam syns.", "No image review was done. Check yourself that the facecam is visible."],
    ["Bildgranskningen hittade ingen facecam i klippet.", "The image review found no facecam in the clip."],
    ["Bildgranskningen kunde inte läsa texten på skärmen.", "The image review could not read the on-screen text."],
    ["Bildgranskningen såg text över ansiktet.", "The image review saw text over the face."],
    [
      "Versionen har ingen ny hook och ingen ny startpunkt. Bara spegling, hastighet, filter eller font kan ge strike på YouTube och stoppa klippet från För dig på TikTok.",
      "The version has no new hook and no new start point. Only mirroring, speed, filter or font can get a strike on YouTube and keep the clip out of For You on TikTok.",
    ],
  ])("a fixed text: %s", (old, english) => {
    expect(englishProblem(old)).toBe(english);
  });

  it("the old NO_OWN becomes the same text as the version dialog shows", () => {
    expect(englishProblem("Versionen har ingen ny hook och ingen ny startpunkt. Bara spegling, hastighet, filter eller font kan ge strike på YouTube och stoppa klippet från För dig på TikTok.")).toBe(NO_OWN);
  });
});
