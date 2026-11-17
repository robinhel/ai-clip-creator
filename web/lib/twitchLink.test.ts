import { describe, expect, it } from "vitest";
import { parseTwitchLink } from "./twitchLink";

describe("parseTwitchLink", () => {
  it("läser VOD-id ur båda Twitchs länkar och bara siffror", () => {
    expect(parseTwitchLink("https://www.twitch.tv/videos/2925991945").vodId).toBe("2925991945");
    expect(parseTwitchLink("twitch.tv/skiimasktimmy/v/2925991945?sr=a").vodId).toBe("2925991945");
    expect(parseTwitchLink(" 2925991945 ").vodId).toBe("2925991945");
    // En kanal utan /v/ är ingen VOD.
    expect(parseTwitchLink("https://www.twitch.tv/skiimasktimmy").vodId).toBeNull();
  });

  it("läser starten ur ?t= och &t=", () => {
    expect(parseTwitchLink("https://www.twitch.tv/videos/2925991945?t=1h2m3s").startS).toBe(3723);
    expect(parseTwitchLink("https://www.twitch.tv/skiimasktimmy/v/2925991945?sr=a&t=05m00s").startS).toBe(300);
    expect(parseTwitchLink("https://www.twitch.tv/videos/2925991945?t=90").startS).toBe(90);
    expect(parseTwitchLink("https://www.twitch.tv/videos/2925991945").startS).toBeNull();
    expect(parseTwitchLink("https://www.twitch.tv/videos/2925991945?t=hms").startS).toBeNull();
    // Siffror i en annan parameter är ingen start.
    expect(parseTwitchLink("https://www.twitch.tv/videos/2925991945?st=12").startS).toBeNull();
  });
});
