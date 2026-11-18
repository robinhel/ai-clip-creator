import { describe, expect, it } from "vitest";
import { textEdit } from "./headline";

describe("textEdit: bodyn för POST /edit i Redigera och Ändra rubrik", () => {
  it("inget ändrat ger tomt objekt", () => {
    expect(textEdit(" Titel ", "Titel", "", null)).toEqual({});
    expect(textEdit("Titel", "Titel", "  Han tappar allt ", "Han tappar allt")).toEqual({});
  });

  it("äldre server utan headline: tomt fält är oförändrat", () => {
    expect(textEdit("Titel", "Titel", "", undefined)).toEqual({});
    expect(textEdit("Titel", "Titel", "Ny bild", undefined)).toEqual({ headline: "Ny bild" });
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
