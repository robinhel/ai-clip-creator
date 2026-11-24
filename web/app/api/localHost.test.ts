import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import { isLocalHost, isSameOrigin } from "./localHost";
import { GET, POST } from "./[...path]/route";

const ctx = { params: Promise.resolve({ path: ["campaign", "tiktok", "disconnect"] }) };

function request(method: string, host: string): NextRequest {
  return new NextRequest(`http://localhost:3000/api/campaign/tiktok/disconnect`, {
    method,
    headers: { host },
  });
}

describe("isLocalHost", () => {
  it.each(["localhost", "localhost:3000", "127.0.0.1", "127.0.0.1:3000", "[::1]", "[::1]:3000", "LocalHost:3000"])(
    "accepts %s",
    (host) => expect(isLocalHost(host)).toBe(true),
  );

  it.each([
    "evil.example",
    "evil.example:3000",
    "localhost.evil.example",
    "localhost.evil.example:3000",
    "127.0.0.1.evil.example",
    "[::2]:3000",
    "",
    null,
  ])("refuses %s", (host) => expect(isLocalHost(host)).toBe(false));
});

describe("the API proxy", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("answers 403 and never calls the API when the Host is not local (DNS rebinding)", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    for (const host of ["evil.example:3000", "localhost.evil.example:3000"]) {
      const answer = await POST(request("POST", host), ctx);
      expect(answer.status).toBe(403);
      expect((await GET(request("GET", host), ctx)).status).toBe(403);
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it("forwards a request from the local dashboard, also on [::1]", async () => {
    const fetch = vi.fn(async () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    for (const host of ["localhost:3000", "127.0.0.1:3000", "[::1]:3000"]) {
      expect((await POST(request("POST", host), ctx)).status).toBe(200);
    }
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});

describe("the API proxy and the Origin header", () => {
  afterEach(() => vi.unstubAllGlobals());

  function withOrigin(method: string, host: string, origin?: string): NextRequest {
    const headers: Record<string, string> = { host, "content-type": "application/json" };
    if (origin !== undefined) headers.origin = origin;
    return new NextRequest(`http://${host}/api/runs`, { method, headers, body: method === "GET" ? undefined : "{}" });
  }

  function okFetch() {
    const fetch = vi.fn(
      async (_url: string, _init: RequestInit) =>
        new Response("{}", { status: 200, headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetch);
    return fetch;
  }

  it.each([
    ["localhost:3000", "http://localhost:3000"],
    ["localhost:3106", "http://localhost:3106"],
    ["127.0.0.1:3001", "http://127.0.0.1:3001"],
    ["[::1]:3001", "http://[::1]:3001"],
    ["[::1]:3000", "https://[::1]:3000"],
  ])("forwards a write from the dashboard on %s, without the Origin header", async (host, origin) => {
    const fetch = okFetch();
    expect((await POST(withOrigin("POST", host, origin), ctx)).status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(1);
    const sent = fetch.mock.calls[0][1].headers as Record<string, string>;
    expect(Object.keys(sent).map((k) => k.toLowerCase())).not.toContain("origin");
  });

  it.each([
    ["localhost:3106", "https://evil.example"],
    ["localhost:3106", "http://localhost:3000"],
    ["localhost:3000", "http://localhost.evil.example:3000"],
    ["[::1]:3001", "http://[::1]:3000"],
    ["localhost:3000", "null"],
  ])("answers 403 on %s to Origin %s and never calls the API", async (host, origin) => {
    const fetch = okFetch();
    expect((await POST(withOrigin("POST", host, origin), ctx)).status).toBe(403);
    expect((await GET(withOrigin("GET", host, origin), ctx)).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("forwards a GET without Origin", async () => {
    const fetch = okFetch();
    expect((await GET(withOrigin("GET", "localhost:3106"), ctx)).status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe("isSameOrigin", () => {
  it("accepts no Origin and the same host on http or https", () => {
    expect(isSameOrigin(null, "localhost:3106")).toBe(true);
    expect(isSameOrigin("http://localhost:3106", "localhost:3106")).toBe(true);
    expect(isSameOrigin("https://[::1]:3001", "[::1]:3001")).toBe(true);
  });

  it("refuses another host, another port, null and a missing Host", () => {
    expect(isSameOrigin("http://localhost:3000", "localhost:3106")).toBe(false);
    expect(isSameOrigin("https://evil.example", "localhost:3106")).toBe(false);
    expect(isSameOrigin("null", "localhost:3106")).toBe(false);
    expect(isSameOrigin("http://localhost:3106", null)).toBe(false);
  });
});
