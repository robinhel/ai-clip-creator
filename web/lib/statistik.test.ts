import { describe, expect, it } from "vitest";
import {
  buildPatch,
  changed,
  draftFrom,
  lengthGroups,
  matches,
  parseFilter,
  POSTED,
  rowsOf,
  sortRows,
  todoItems,
  undoStatus,
  watchPctOfLength,
} from "./statistik";
import type { CampaignClip, ClipPost, PostStatus } from "./types";

const NOW = Date.parse("2026-11-17T12:00:00Z");
const daysAgo = (days: number) => new Date(NOW - days * 86_400_000).toISOString();

function clip(over: Partial<CampaignClip> = {}): CampaignClip {
  return {
    id: 9, vod_id: "2925991945", profile: "skiimasktimmy", start_s: 0, end_s: 30, title: "Kul", format: "funny",
    signals: [], filename: "a.mp4", caption: "", status: "godkand", problems: [], warnings: [], review: null,
    platform: null, url: null, views: null, likes: null, comments: null, shares: null, note: null,
    created: daysAgo(10), updated: daysAgo(10), engagement: null, engagement_min: null, min_views: 2000,
    video_url: "", score: null, reason: "", posts: [], ...over,
  };
}

let nextPost = 1;
function post(over: Partial<ClipPost> = {}): ClipPost {
  return {
    id: nextPost++, clip_id: 9, platform: "tiktok", url: "https://www.tiktok.com/@me/video/1", status: "postad",
    views: null, likes: null, comments: null, shares: null, paid_usd: null, created: daysAgo(1),
    updated: daysAgo(1), engagement: null, estimate_usd: 2.5, ...over,
  };
}

describe("buildPatch", () => {
  it("Betald sparar beloppet som är skrivet men inte sparat, i samma anrop", () => {
    const saved = post({ status: "analytics_inskickad", views: 2500, paid_usd: null });
    const draft = { ...draftFrom(saved), paid: "14,20" };
    expect(buildPatch(draft, saved, "betald")).toEqual({ body: { status: "betald", paid_usd: 14.2 } });
  });

  it("skickar bara statusen när inget är ändrat", () => {
    const saved = post({ status: "inskickad", views: 300, paid_usd: 3 });
    expect(buildPatch(draftFrom(saved), saved, "betald")).toEqual({ body: { status: "betald" } });
  });

  it("skickar visningarna med steget, så att Analytics inskickad går med det nya talet", () => {
    const saved = post({ status: "inskickad", views: 1500 });
    const draft = { ...draftFrom(saved), views: "2500" };
    expect(buildPatch(draft, saved, "analytics_inskickad")).toEqual({
      body: { status: "analytics_inskickad", views: 2500 },
    });
  });

  it("Spara utan steg skickar de ändrade fälten, och ett tömt fält tar bort talet", () => {
    const saved = post({ status: "inskickad", views: 100, likes: 4, paid_usd: 1.5 });
    const draft = { ...draftFrom(saved), views: "", likes: "7", paid: "" };
    expect(buildPatch(draft, saved)).toEqual({ body: { views: null, likes: 7, paid_usd: null } });
  });

  it("ett fel i ett fält stoppar steget och säger vilket fält", () => {
    const saved = post({ status: "analytics_inskickad" });
    const badPaid = buildPatch({ ...draftFrom(saved), paid: "12,5,0" }, saved, "betald");
    expect(badPaid).not.toHaveProperty("body");
    expect("error" in badPaid && badPaid.error).toMatch(/^Betalt/);
    const badViews = buildPatch({ ...draftFrom(saved), views: "1,5" }, saved, "betald");
    expect("error" in badViews && badViews.error).toMatch(/^Visningar/);
  });

  it("visar sparat belopp med komma och skickar inget belopp för en rad utan inlägg", () => {
    expect(draftFrom(post({ paid_usd: 14.2 })).paid).toBe("14,20");
    const old = clip({ status: "inskickad", views: 10 });
    expect(buildPatch({ ...draftFrom(old), paid: "5" }, old, "betald")).toEqual({ body: { status: "betald" } });
  });
});

describe("Fler mått", () => {
  it("tar decimalkomma och punkt för sekunder och procent", () => {
    const saved = post({ status: "inskickad" });
    const draft = { ...draftFrom(saved), avg_watch_s: "6,4", full_pct: "6.4", fyp_pct: "55", countries_pct: "100", saves: "12" };
    expect(buildPatch(draft, saved)).toEqual({
      body: { avg_watch_s: 6.4, full_pct: 6.4, fyp_pct: 55, countries_pct: 100, saves: 12 },
    });
  });

  it("procent över 100, text som inte är ett tal och sparningar med decimaler ger fel med fältets namn", () => {
    const saved = post({ status: "inskickad" });
    const errorFor = (field: string, value: string) => {
      const result = buildPatch({ ...draftFrom(saved), [field]: value }, saved);
      return "error" in result ? result.error : null;
    };
    expect(errorFor("full_pct", "101")).toMatch(/^Ser klart \(%\)/);
    expect(errorFor("countries_pct", "abc")).toMatch(/^Från kampanjens länder \(%\)/);
    expect(errorFor("avg_watch_s", "abc")).toMatch(/^Tittartid \(s\)/);
    expect(errorFor("fyp_pct", "6,4,1")).toMatch(/^Från För dig \(%\)/);
    expect(errorFor("saves", "2,5")).toMatch(/^Sparningar/);
  });

  it("visar sparade mått med komma och ser bara ändrade fält; ett tömt fält sparas som null", () => {
    const saved = post({ status: "inskickad", avg_watch_s: 6.4, full_pct: 30, fyp_pct: null, countries_pct: 80, saves: 3 });
    expect(draftFrom(saved).avg_watch_s).toBe("6,4");
    expect(changed(draftFrom(saved), saved)).toEqual([]);
    const draft = { ...draftFrom(saved), fyp_pct: "40", saves: "" };
    expect(changed(draft, saved).sort()).toEqual(["fyp_pct", "saves"]);
    expect(buildPatch(draft, saved, "analytics_inskickad")).toEqual({
      body: { status: "analytics_inskickad", fyp_pct: 40, saves: null },
    });
  });

  it("en rad utan inlägg skickar inga mått", () => {
    const old = clip({ status: "inskickad", views: 10 });
    const draft = { ...draftFrom(old), avg_watch_s: "6,4", full_pct: "50", fyp_pct: "50", countries_pct: "50", saves: "3" };
    expect(changed(draft, old)).toEqual([]);
    expect(buildPatch(draft, old)).toEqual({ body: {} });
  });
});

describe("watchPctOfLength", () => {
  it("ger tittartiden i procent av klippets längd", () => {
    expect(watchPctOfLength(6.4, 20)).toBe(32);
  });

  it("ger null när längden eller tittartiden saknas", () => {
    expect(watchPctOfLength(6.4, null)).toBeNull();
    expect(watchPctOfLength(6.4, 0)).toBeNull();
    expect(watchPctOfLength(null, 20)).toBeNull();
  });
});

describe("lengthGroups", () => {
  it("räknar inlägg per längd och snittar bara inlägg med visningar", () => {
    const clips = [
      clip({ id: 1, length_s: 14.9, posts: [post({ views: 500 })] }),
      clip({ id: 2, length_s: 15.0, posts: [post({ views: 1000 }), post({ views: null })] }),
      clip({ id: 3, length_s: 20.0, posts: [post({ views: 3000 })] }),
      clip({ id: 4, length_s: 20.1, posts: [post({ views: 700 })] }),
      clip({ id: 5, length_s: null, posts: [post({ views: 100 })] }),
      // Ett klipp utan inlägg räknas inte.
      clip({ id: 6, length_s: 10, posts: [] }),
    ];
    expect(lengthGroups(rowsOf(clips))).toEqual([
      { label: "Under 15 s", posts: 1, avgViews: 500 },
      { label: "15–20 s", posts: 3, avgViews: 2000 },
      { label: "Över 20 s", posts: 1, avgViews: 700 },
      { label: "Okänd längd", posts: 1, avgViews: 100 },
    ]);
  });

  it("utan visningar är snittet null, och Okänd längd finns bara när den har inlägg", () => {
    const clips = [clip({ length_s: 12, posts: [post({ views: null })] })];
    expect(lengthGroups(rowsOf(clips))).toEqual([
      { label: "Under 15 s", posts: 1, avgViews: null },
      { label: "15–20 s", posts: 0, avgViews: null },
      { label: "Över 20 s", posts: 0, avgViews: null },
    ]);
  });
});

describe("todoItems", () => {
  it("ger ett steg per inlägg, med raden där knappen för steget finns", () => {
    const submit = post({ status: "postad" });
    const noViews = post({ status: "inskickad", platform: "instagram_reels" });
    const analytics = post({ status: "inskickad", platform: "youtube_shorts", views: 2500 });
    const paid = post({ status: "analytics_inskickad", views: 4000 });
    const done = post({ status: "betald", views: 9000, clip_id: 10 });
    const items = todoItems([
      clip({ status: "postad", posts: [submit, noViews, analytics, paid] }),
      clip({ id: 10, status: "betald", posts: [done] }),
    ], NOW);
    expect(items.map((i) => [i.step, i.rowId])).toEqual([
      ["inskickad", `inlagg-${submit.id}`],
      ["analytics_inskickad", `inlagg-${analytics.id}`],
      ["visningar", `inlagg-${noViews.id}`],
      ["betald", `inlagg-${paid.id}`],
    ]);
  });

  it("använder kampanjens egna minsta visningar", () => {
    const blizzard = post({ status: "inskickad", views: 300, updated: daysAgo(0) });
    const timmy = post({ status: "inskickad", views: 300, updated: daysAgo(0) });
    const items = todoItems([
      clip({ id: 1, profile: "blizzard_nordics", min_views: 250, status: "inskickad", posts: [blizzard] }),
      clip({ id: 2, min_views: 2000, status: "inskickad", posts: [timmy] }),
    ], NOW);
    expect(items.map((i) => [i.step, i.post?.id])).toEqual([["analytics_inskickad", blizzard.id]]);
  });

  it("påminner om visningarna igen när de inte har ändrats på några dagar", () => {
    const old = post({ status: "inskickad", views: 300, updated: daysAgo(4) });
    const fresh = post({ status: "inskickad", views: 300, updated: daysAgo(1) });
    const items = todoItems([clip({ status: "inskickad", posts: [old, fresh] })], NOW);
    expect(items.map((i) => [i.step, i.post?.id])).toEqual([["visningar_igen", old.id]]);
  });

  it("ett godkänt klipp postas i Att posta, och ett äldre klipp utan inlägg följs på klippets rad", () => {
    const items = todoItems([
      clip({ id: 3, status: "godkand" }),
      clip({ id: 4, status: "inskickad", posts: undefined }),
      clip({ id: 5, status: "renderad" }),
    ], NOW);
    expect(items.map((i) => [i.step, i.clip.id, i.rowId])).toEqual([
      ["visningar", 4, "klipp-4"],
      ["posta", 3, null],
    ]);
  });
});

describe("filtret i adressen", () => {
  it("läser en status ur ?status=, som länken från Hem, och visar postade inlägg annars", () => {
    expect(parseFilter("inskickad")).toBe("inskickad");
    expect(parseFilter("alla")).toBe("alla");
    expect(parseFilter("okänd")).toBe("postade");
    expect(parseFilter(undefined)).toBe("postade");
  });

  it("postade inlägg är alla statusar från Postad till Betald", () => {
    expect(POSTED.filter((s) => matches("postade", s))).toEqual(POSTED);
    expect(matches("postade", "godkand")).toBe(false);
    expect(matches("alla", "nekad")).toBe(true);
  });
});

describe("undoStatus", () => {
  const paid = (prev?: PostStatus) => post({ status: "betald", prev_status: prev });

  it("Ångra på Betald går tillbaka till statusen före Betald", () => {
    const c = clip({ status: "betald" });
    expect(undoStatus({ clip: c, post: paid("analytics_inskickad") })).toBe("analytics_inskickad");
    expect(undoStatus({ clip: c, post: paid("inskickad") })).toBe("inskickad");
    // En äldre server skickar inte statusen före: Inskickad, som förut.
    expect(undoStatus({ clip: c, post: paid() })).toBe("inskickad");
  });

  it("de andra stegen går ett steg bakåt, och ett klipp som inte är postat har inget Ångra", () => {
    expect(undoStatus({ clip: clip(), post: post({ status: "analytics_inskickad" }) })).toBe("inskickad");
    expect(undoStatus({ clip: clip({ status: "inskickad" }) })).toBe("postad");
    expect(undoStatus({ clip: clip({ status: "godkand" }) })).toBeNull();
  });
});

describe("sortRows", () => {
  it("lägger det som behöver göras först och betalda och opostade sist", () => {
    const paid = post({ status: "betald", views: 9000 });
    const waiting = post({ status: "inskickad", views: 300, updated: daysAgo(0) });
    const submit = post({ status: "postad" });
    const c = clip({ status: "postad", posts: [paid, waiting, submit] });
    const unposted = clip({ id: 11, status: "godkand" });
    const rows = [
      { clip: unposted, post: undefined },
      ...c.posts!.map((p) => ({ clip: c, post: p })),
    ];
    expect(sortRows(rows, NOW).map((r) => r.post?.id ?? `klipp ${r.clip.id}`)).toEqual([
      submit.id, waiting.id, paid.id, "klipp 11",
    ]);
  });
});
