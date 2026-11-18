import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { headlineChange, textEdit } from "./headline";

describe("headlineChange: texten i bilden skickas bara när den har ändrats", () => {
  it("oförändrad text skickas inte", () => {
    expect(headlineChange("Han tappar allt", "Han tappar allt")).toBeUndefined();
    expect(headlineChange("  Han tappar allt ", "Han tappar allt")).toBeUndefined();
  });

  it("tomt fält på ett gammalt klipp utan text är oförändrat", () => {
    expect(headlineChange("", null)).toBeUndefined();
    expect(headlineChange("  ", undefined)).toBeUndefined();
    expect(headlineChange("", "")).toBeUndefined();
  });

  it("ny text skickas utan blanksteg runt", () => {
    expect(headlineChange("  Ny text ", "Gammal")).toBe("Ny text");
    expect(headlineChange("Ny text", null)).toBe("Ny text");
  });

  it("tömt fält skickas som tom text, så att titeln står i bilden", () => {
    expect(headlineChange("", "Gammal")).toBe("");
  });
});

describe("textEdit: bodyn för POST /edit i Redigera och Ändra rubrik", () => {
  it("inget ändrat ger tomt objekt", () => {
    expect(textEdit(" Titel ", "Titel", "", null)).toEqual({});
  });

  it("bara titeln ändrad skickar inte headline", () => {
    expect(textEdit("Ny titel", "Titel", "Bild", "Bild")).toEqual({ title: "Ny titel" });
  });

  it("bara texten i bilden ändrad skickar inte titeln", () => {
    expect(textEdit("Titel", "Titel", "Ny bild ", "")).toEqual({ headline: "Ny bild" });
  });

  it("båda ändrade, och tömd text i bilden skickas som tom", () => {
    expect(textEdit("Ny titel", "Titel", "", "Bild")).toEqual({ title: "Ny titel", headline: "" });
  });
});

describe("api skickar headline och hook_style", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stubFetch() {
    const fetch = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    return fetch;
  }
  const sent = (fetch: ReturnType<typeof stubFetch>) => {
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    return { url, method: init.method, body: JSON.parse(init.body as string) };
  };

  it("saveEdit skickar headline bredvid titeln", async () => {
    const fetch = stubFetch();
    await api.campaign.clips.saveEdit(44, { title: "Titel", headline: "" });
    expect(sent(fetch)).toEqual({ url: "/api/campaign/clips/44/edit", method: "POST", body: { title: "Titel", headline: "" } });
  });

  it("saveEdit utan headline lämnar den utanför", async () => {
    const fetch = stubFetch();
    await api.campaign.clips.saveEdit(44, { title: "Titel" });
    expect(sent(fetch).body).toEqual({ title: "Titel" });
  });

  it("saveSettings skickar hook_style", async () => {
    const fetch = stubFetch();
    await api.campaign.saveSettings("wow", { text_position: "top", hook_style: "serif" });
    expect(sent(fetch)).toEqual({
      url: "/api/campaign/profiles/wow/settings",
      method: "PUT",
      body: { text_position: "top", hook_style: "serif" },
    });
  });
});
