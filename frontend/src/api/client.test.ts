import { describe, it, expect, beforeEach, vi } from "vitest";
import { _authMiddleware } from "./client";
import { setToken } from "@/auth/token";

describe("auth middleware", () => {
  beforeEach(() => window.localStorage.clear());
  it("attaches Bearer when a token exists", async () => {
    setToken("tok123");
    const request = new Request("http://x/figures");
    const out = await _authMiddleware.onRequest!({ request } as any);
    expect((out as Request).headers.get("Authorization")).toBe("Bearer tok123");
  });
  it("clears token + redirects on 401", async () => {
    setToken("tok123");
    const assign = vi.fn();
    Object.defineProperty(window, "location", { value: { assign }, writable: true });
    await _authMiddleware.onResponse!({ response: new Response("", { status: 401 }) } as any);
    expect(window.localStorage.getItem("bw_token")).toBeNull();
    expect(assign).toHaveBeenCalledWith("/login");
  });
});
