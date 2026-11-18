import { describe, expect, it } from "vitest";
import { channelLogin, channelStateText, freeChannels, newestFirst, timeText, withChannel } from "./live";
import type { LiveWatch, Peak } from "./liveApi";

function peak(over: Partial<Peak> = {}): Peak {
  return { n: 1, at: "2026-11-18T19:05:00Z", vod_s: 3600, status: "vantar", run_id: null, reason: null, ...over };
}

function watch(over: Partial<LiveWatch> = {}): LiveWatch {
  return { profile: null, channel: "ishowspeed", watching: true, live: false, vod_id: null, error: null, peaks: [], ...over };
}

describe("channelLogin", () => {
  it("gör om namnet eller kanallänken till ett login med små bokstäver", () => {
    for (const text of ["ishowspeed", "IShowSpeed", " ishowspeed ", "https://www.twitch.tv/ishowspeed", "twitch.tv/ishowspeed/", "https://m.twitch.tv/IShowSpeed?sr=a"]) {
      expect(channelLogin(text)).toEqual({ login: "ishowspeed" });
    }
  });

  it("ger fel för ett tomt fält", () => {
    expect(channelLogin("  ")).toEqual({ error: "Skriv en Twitch-kanal, till exempel ishowspeed." });
  });

  it("ger fel för något som inte är en kanal", () => {
    for (const text of ["ishow speed", "ishow-speed", "https://www.youtube.com/@ishowspeed", "a".repeat(26)]) {
      expect(channelLogin(text)).toEqual({ error: "Det är ingen Twitch-kanal. Skriv namnet, till exempel ishowspeed, eller länken till kanalen." });
    }
  });

  it("säger till när länken går till en video och inte en kanal", () => {
    for (const text of ["https://www.twitch.tv/videos/2925991945", "twitch.tv/ishowspeed/v/2925991945"]) {
      expect(channelLogin(text)).toEqual({ error: "Det är en länk till en video. Skriv kanalens namn eller länken till kanalen." });
    }
  });
});

describe("channelStateText", () => {
  it("ger rätt text per läge för en fri kanal", () => {
    expect(channelStateText(watch({ watching: true, live: false }))).toBe("Väntar på att kanalen går live");
    expect(channelStateText(watch({ watching: true, live: true }))).toBe("Live nu. Appen spelar in strömmen.");
    expect(channelStateText(watch({ watching: false, live: true }))).toBe("Bevakas inte");
    expect(channelStateText(watch({ watching: false, live: false }))).toBe("Bevakas inte");
  });
});

describe("freeChannels", () => {
  it("tar bara kanaler utan kampanj", () => {
    const all = [watch({ profile: "timmy", channel: "skiimasktimmy" }), watch({ channel: "ishowspeed" })];
    expect(freeChannels(all).map((w) => w.channel)).toEqual(["ishowspeed"]);
  });
});

describe("withChannel", () => {
  it("byter ut den fria kanalen, och lämnar en kampanj med samma kanal", () => {
    const campaign = watch({ profile: "speed", channel: "ishowspeed" });
    const all = [campaign, watch({ watching: true })];
    const next = withChannel(all, watch({ watching: false }));
    expect(next).toEqual([campaign, watch({ watching: false })]);
    expect(all[1].watching).toBe(true);
  });

  it("lägger till en ny kanal sist", () => {
    const all = [watch({ channel: "kaicenat" })];
    expect(withChannel(all, watch()).map((w) => w.channel)).toEqual(["kaicenat", "ishowspeed"]);
  });
});

describe("timeText", () => {
  it("visar tiden som HH:MM i svensk tid", () => {
    // Vintertid: UTC+1.
    expect(timeText("2026-11-18T19:05:00Z")).toBe("20:05");
    // Sommartid: UTC+2.
    expect(timeText("2026-07-01T08:30:00Z")).toBe("10:30");
  });

  it("ger tom text för en tid som inte går att läsa", () => {
    expect(timeText("inte en tid")).toBe("");
  });
});

describe("newestFirst", () => {
  it("lägger den senaste toppen först utan att ändra listan", () => {
    const peaks = [peak({ n: 1 }), peak({ n: 3 }), peak({ n: 2 })];
    expect(newestFirst(peaks).map((p) => p.n)).toEqual([3, 2, 1]);
    expect(peaks.map((p) => p.n)).toEqual([1, 3, 2]);
  });
});
