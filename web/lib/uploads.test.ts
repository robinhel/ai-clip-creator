import { describe, expect, it } from "vitest";
import {
  START_CHOICES,
  choicesForm,
  clipLength,
  copyChoices,
  frameQuery,
  lengthCheck,
  loadLastChoices,
  musicFileProblem,
  previewOutdated,
  previewKey,
  rejectText,
  saveLastChoices,
  splitVideoFiles,
  volumeWarning,
  withKnownMusic,
  type Choices,
} from "./uploads";

const choices = (change: Partial<Choices> = {}): Choices => ({ ...START_CHOICES, ...change });

/** Som localStorage, men i minnet. */
function memoryStorage(start: Record<string, string> = {}) {
  const data = new Map(Object.entries(start));
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
  };
}

describe("längden på klippet", () => {
  it("är (slut − start) / hastighet, och slut 0 betyder videons slut", () => {
    expect(clipLength(choices({ startS: 2, endS: 22, speed: 1.1 }), 60)).toBeCloseTo(18.18, 2);
    expect(clipLength(choices({ startS: 10, endS: 0, speed: 1 }), 30)).toBe(20);
    expect(clipLength(choices({ startS: 0, endS: 0, speed: 1.2 }), 24)).toBeCloseTo(20, 5);
  });

  it("inom gränserna säger längden och kampanjens gränser, med decimalkomma", () => {
    expect(lengthCheck(18.1818, 7, 45)).toEqual({ ok: true, text: "Klippet blir 18,2 s. Kampanjen tillåter 7–45 s." });
    expect(lengthCheck(44.9, 7, 45)).toEqual({ ok: true, text: "Klippet blir 44,9 s. Kampanjen tillåter 7–45 s." });
    expect(lengthCheck(7, 7, 45).ok).toBe(true);
    expect(lengthCheck(45, 7, 45).ok).toBe(true);
    expect(lengthCheck(12.5, 7.5, 59.5).text).toBe("Klippet blir 12,5 s. Kampanjen tillåter 7,5–59,5 s.");
  });

  it("över taket går inte, och texten säger vad Robin ska göra", () => {
    const check = lengthCheck(45.1, 7, 45);
    expect(check.ok).toBe(false);
    expect(check.text).toMatch(/^Klippet blir 45,1 s\. Kampanjen tillåter högst 45 s\. /);
    expect(check.text).toContain("högre hastighet");
    // Två decimaler när det är för långt, så att 45,04 s inte visas som 45,0 s.
    expect(lengthCheck(45.04, 7, 45).text).toMatch(/^Klippet blir 45,04 s\./);
  });

  it("under golvet går inte, och texten säger vad Robin ska göra", () => {
    const check = lengthCheck(6.9, 7, 45);
    expect(check.ok).toBe(false);
    expect(check.text).toMatch(/^Klippet blir 6,9 s\. Kampanjen kräver minst 7 s\. /);
    expect(check.text).toContain("lägre hastighet");
  });

  it("utan tak från servern nämns bara golvet", () => {
    expect(lengthCheck(80, 7, null)).toEqual({ ok: true, text: "Klippet blir 80 s. Kampanjen kräver minst 7 s." });
  });
});

describe("valen till servern", () => {
  it("blir formuläret som render och save tar emot, med start_s och end_s", () => {
    const form = choicesForm(choices({ startS: 2.5, endS: 20, mirror: true, speed: 1.05 }), "timmy", "Rubriken");
    expect(Object.fromEntries(form.entries())).toEqual({
      profile: "timmy",
      title: "Rubriken",
      mirror: "true",
      speed: "1.05",
      filter: "fresh",
      brightness: "0",
      saturation: "0",
      framing: "fit",
      font: "",
      overlay: "",
      start_s: "2.5",
      end_s: "20",
      audio: "keep",
    });
  });

  it("Behåll och Tyst skickar ljudvalet utan musikens fält", () => {
    for (const audio of ["keep", "mute"] as const) {
      const form = choicesForm(choices({ audio, music: "wow.mp3", musicStart: "4", originalVolume: 50 }), "timmy", "");
      expect(form.get("audio")).toBe(audio);
      expect(form.has("music")).toBe(false);
      expect(form.has("music_start_s")).toBe(false);
      expect(form.has("original_volume")).toBe(false);
    }
  });

  it("Musik skickar alla fyra fälten, originalljudet som andel och starten med komma", () => {
    const form = choicesForm(choices({ audio: "music", music: "wow.mp3", musicStart: "12,5", originalVolume: 30 }), "timmy", "");
    expect(form.get("audio")).toBe("music");
    expect(form.get("music")).toBe("wow.mp3");
    expect(form.get("music_start_s")).toBe("12.5");
    expect(form.get("original_volume")).toBe("0.3");
    expect(choicesForm(choices({ audio: "music", music: "a.mp3", originalVolume: 0 }), "timmy", "").get("original_volume")).toBe("0");
    expect(choicesForm(choices({ audio: "music", music: "a.mp3", originalVolume: 100 }), "timmy", "").get("original_volume")).toBe("1");
  });

  it("startar med Behåll ljudet och originalljudet 30 %", () => {
    expect(START_CHOICES.audio).toBe("keep");
    expect(START_CHOICES.originalVolume).toBe(30);
  });

  it("tar med klistermärket bara när det är valt, och läser komma i sekunderna", () => {
    expect(choicesForm(choices({ position: "bottom_left" }), "timmy", "").has("sticker_position")).toBe(false);
    const form = choicesForm(choices({ sticker: "pil.png", stickerStart: "1,5", stickerLength: "3" }), "timmy", "");
    expect(form.get("sticker")).toBe("pil.png");
    expect(form.get("sticker_position")).toBe("top_right");
    expect(form.get("sticker_start_s")).toBe("1.5");
    expect(form.get("sticker_duration_s")).toBe("3");
  });

  it("blir query för bilden, med tiden i det färdiga klippet", () => {
    const query = new URLSearchParams(frameQuery(choices({ startS: 4, endS: 0 }), "timmy", "Hej", 3.5));
    expect(query.get("t")).toBe("3.5");
    expect(query.get("profile")).toBe("timmy");
    expect(query.get("title")).toBe("Hej");
    expect(query.get("start_s")).toBe("4");
    expect(query.get("end_s")).toBe("0");
    expect(query.has("sticker")).toBe(false);
    const withSticker = new URLSearchParams(frameQuery(choices({ sticker: "pil.png" }), "timmy", "", 0));
    expect(withSticker.get("sticker")).toBe("pil.png");
    expect(withSticker.get("sticker_duration_s")).toBe("3");
  });

  it("bildens query har inga ljudval, för bilden har inget ljud", () => {
    const music = choices({ audio: "music", music: "wow.mp3", musicStart: "2", originalVolume: 0 });
    const query = new URLSearchParams(frameQuery(music, "timmy", "", 1));
    for (const key of ["audio", "music", "music_start_s", "original_volume"]) expect(query.has(key)).toBe(false);
    expect(frameQuery(music, "timmy", "", 1)).toBe(frameQuery(choices(), "timmy", "", 1));
  });
});

describe("filtyper", () => {
  const file = (name: string) => ({ name });

  it("godtar videofilerna, också med stora bokstäver", () => {
    const names = ["a.mp4", "b.MOV", "c.m4v", "d.WebM", "e.mkv"];
    const { videos, rejected } = splitVideoFiles(names.map(file));
    expect(videos.map((f) => f.name)).toEqual(names);
    expect(rejected).toEqual([]);
    expect(rejectText(rejected)).toBeNull();
  });

  it("nekar andra filer med en text", () => {
    const { videos, rejected } = splitVideoFiles([file("klipp.mp4"), file("anteckning.txt"), file("bild.png"), file("mp4")]);
    expect(videos.map((f) => f.name)).toEqual(["klipp.mp4"]);
    expect(rejected).toEqual(["anteckning.txt", "bild.png", "mp4"]);
    expect(rejectText(rejected)).toBe(
      "anteckning.txt, bild.png och mp4 är ingen video och togs inte med. Välj mp4, mov, m4v, webm eller mkv."
    );
    expect(rejectText(["a.txt"])).toBe("a.txt är ingen video och togs inte med. Välj mp4, mov, m4v, webm eller mkv.");
  });
});

describe("filtyper för musik", () => {
  it("godtar ljudfiler och videor, också med stora bokstäver", () => {
    for (const name of ["a.mp3", "b.M4A", "c.aac", "d.WAV", "e.ogg", "f.flac", "g.MP4", "h.mov", "i.m4v", "j.webm", "k.mkv"]) {
      expect(musicFileProblem(name)).toBeNull();
    }
  });

  it("nekar andra filer med en text innan uppladdningen", () => {
    expect(musicFileProblem("låtar.txt")).toBe(
      "låtar.txt är ingen ljudfil eller video. Välj mp3, m4a, aac, wav, ogg, flac, mp4, mov, m4v, webm eller mkv."
    );
    expect(musicFileProblem("mp3")).not.toBeNull();
  });
});

describe("varningen när originalljudet är 0 %", () => {
  it("säger att originalljudet inte hörs, och kampanjens krav när den har regler om ljud", () => {
    expect(volumeWarning(0, [])).toEqual(["Originalljudet hörs inte i klippet."]);
    expect(volumeWarning(0, ["Streamerns röst ska höras."])).toEqual([
      "Originalljudet hörs inte i klippet.",
      "Kampanjen kräver att det hörs tydligt.",
    ]);
    expect(volumeWarning(0, undefined)).toEqual(["Originalljudet hörs inte i klippet."]);
  });

  it("ingen varning när originalljudet hörs", () => {
    expect(volumeWarning(1, ["Streamerns röst ska höras."])).toEqual([]);
    expect(volumeWarning(30, [])).toEqual([]);
  });
});

describe("senaste valen per kampanj", () => {
  it("sparar ljudvalet", () => {
    const storage = memoryStorage();
    const mine = choices({ audio: "music", music: "wow.mp3", musicStart: "3,5", originalVolume: 0 });
    saveLastChoices(storage, "timmy", mine);
    expect(loadLastChoices(storage, "timmy")).toEqual(mine);
    expect(loadLastChoices(storage, "timmy", ["wow.mp3", "annan.mp3"])).toEqual(mine);
    saveLastChoices(storage, "annan", choices({ audio: "mute" }));
    expect(loadLastChoices(storage, "annan").audio).toBe("mute");
  });

  it("en musikfil som inte finns i kampanjens lista längre ger Behåll", () => {
    const storage = memoryStorage();
    saveLastChoices(storage, "timmy", choices({ audio: "music", music: "borta.mp3", originalVolume: 0 }));
    const loaded = loadLastChoices(storage, "timmy", ["wow.mp3"]);
    expect(loaded.audio).toBe("keep");
    expect(loaded.music).toBe("");
    expect(loadLastChoices(storage, "timmy", []).audio).toBe("keep");
  });

  it("ett okänt ljudval eller en volym utanför 0–100 ger startvärdet", () => {
    const storage = memoryStorage();
    saveLastChoices(storage, "timmy", START_CHOICES);
    const key = [...storage.data.keys()][0];
    const loaded = loadLastChoices(memoryStorage({ [key]: JSON.stringify({ audio: "högt", originalVolume: 250 }) }), "timmy");
    expect(loaded).toEqual(START_CHOICES);
  });

  it("sparas och läses per kampanj", () => {
    const storage = memoryStorage();
    const mine = choices({ mirror: true, speed: 1.2, filter: "warm", sticker: "pil.png", stickerStart: "2" });
    saveLastChoices(storage, "timmy", mine);
    expect(loadLastChoices(storage, "timmy")).toEqual(mine);
    expect(loadLastChoices(storage, "annan")).toEqual(START_CHOICES);
  });

  it("sparar inte rubriken, och inte start och slut som hör till videon", () => {
    const storage = memoryStorage();
    saveLastChoices(storage, "timmy", { ...choices({ startS: 5, endS: 12 }), title: "Hemlig rubrik" } as Choices);
    const stored = [...storage.data.values()].join("");
    expect(stored).not.toContain("Hemlig rubrik");
    expect(stored).not.toContain("title");
    expect(loadLastChoices(storage, "timmy")).toEqual(START_CHOICES);
  });

  it("ett trasigt värde ger startvalen", () => {
    const key = (() => {
      const storage = memoryStorage();
      saveLastChoices(storage, "timmy", START_CHOICES);
      return [...storage.data.keys()][0];
    })();
    expect(loadLastChoices(memoryStorage({ [key]: "{inte json" }), "timmy")).toEqual(START_CHOICES);
    expect(loadLastChoices(memoryStorage({ [key]: "null" }), "timmy")).toEqual(START_CHOICES);
    // Ett fält med fel typ blir startvärdet, de andra behålls.
    const mixed = loadLastChoices(memoryStorage({ [key]: JSON.stringify({ speed: "snabb", filter: "warm" }) }), "timmy");
    expect(mixed).toEqual(choices({ filter: "warm" }));
  });

  it("utan localStorage blir det startvalen", () => {
    const broken = {
      getItem: () => {
        throw new Error("blockerad");
      },
      setItem: () => {
        throw new Error("blockerad");
      },
    };
    expect(loadLastChoices(broken, "timmy")).toEqual(START_CHOICES);
    expect(() => saveLastChoices(broken, "timmy", START_CHOICES)).not.toThrow();
  });
});

describe("musik som inte finns längre", () => {
  it("ger Behåll, och annat ljud ändras inte", () => {
    expect(withKnownMusic(choices({ audio: "music", music: "borta.mp3" }), ["wow.mp3"])).toEqual(choices());
    const kept = choices({ audio: "music", music: "wow.mp3" });
    expect(withKnownMusic(kept, ["wow.mp3"])).toBe(kept);
    const mute = choices({ audio: "mute" });
    expect(withKnownMusic(mute, [])).toBe(mute);
  });
});

describe("Använd på alla", () => {
  it("kopierar ljudvalet", () => {
    const from = choices({ audio: "music", music: "wow.mp3", musicStart: "2", originalVolume: 10, startS: 3 });
    expect(copyChoices(from, choices({ startS: 7 }))).toEqual({ ...from, startS: 7 });
  });

  it("kopierar valen men inte start och slut, som hör till varje video", () => {
    const from = choices({ mirror: true, speed: 1.2, sticker: "pil.png", startS: 3, endS: 40 });
    const to = choices({ startS: 7, endS: 25 });
    expect(copyChoices(from, to)).toEqual({ ...from, startS: 7, endS: 25 });
    expect(copyChoices(from, choices())).toEqual({ ...from, startS: 0, endS: 0 });
  });
});

describe("ändrade val sedan förhandsvisningen", () => {
  it("jämför valen, inte objektet", () => {
    const key = previewKey(choices({ speed: 1.2 }), "timmy", "Rubrik");
    expect(previewOutdated(key, choices({ speed: 1.2 }), "timmy", "Rubrik")).toBe(false);
    expect(previewOutdated(key, choices({ speed: 1.15 }), "timmy", "Rubrik")).toBe(true);
    expect(previewOutdated(key, choices({ speed: 1.2 }), "timmy", "Ny rubrik")).toBe(true);
    expect(previewOutdated(key, choices({ speed: 1.2 }), "annan", "Rubrik")).toBe(true);
  });

  it("klistermärkets fält räknas bara när ett klistermärke är valt", () => {
    const key = previewKey(choices(), "timmy", "");
    expect(previewOutdated(key, choices({ position: "bottom_left" }), "timmy", "")).toBe(false);
    expect(previewOutdated(key, choices({ sticker: "pil.png" }), "timmy", "")).toBe(true);
  });

  it("ändrat ljud räknas, men musikens fält bara när Musik är valt", () => {
    const key = previewKey(choices(), "timmy", "");
    expect(previewOutdated(key, choices({ audio: "mute" }), "timmy", "")).toBe(true);
    expect(previewOutdated(key, choices({ originalVolume: 0 }), "timmy", "")).toBe(false);
    const music = choices({ audio: "music", music: "wow.mp3" });
    const musicKey = previewKey(music, "timmy", "");
    expect(previewOutdated(musicKey, { ...music, originalVolume: 40 }, "timmy", "")).toBe(true);
    expect(previewOutdated(musicKey, { ...music, musicStart: "5" }, "timmy", "")).toBe(true);
    expect(previewOutdated(musicKey, { ...music, music: "annan.mp3" }, "timmy", "")).toBe(true);
  });

  it("utan förhandsvisning har inget ändrats", () => {
    expect(previewOutdated(null, choices(), "timmy", "")).toBe(false);
  });
});
