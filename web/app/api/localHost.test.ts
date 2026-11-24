import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import { isLocalHost } from "./localHost";
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
