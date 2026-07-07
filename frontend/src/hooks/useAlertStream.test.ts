import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useAlertStream } from "./useAlertStream";
import { setToken } from "@/auth/token";

class MockES {
  listeners: Record<string, (e: any) => void> = {};
  onopen: any; onerror: any;
  constructor(public url: string) { MockES.last = this; }
  addEventListener(t: string, cb: (e: any) => void) { this.listeners[t] = cb; }
  close() {}
  static last: MockES;
}

describe("useAlertStream", () => {
  beforeEach(() => { window.localStorage.clear(); vi.stubGlobal("EventSource", MockES as any); });
  it("prepends parsed alert events", () => {
    setToken("T");
    const { result } = renderHook(() => useAlertStream());
    act(() => { MockES.last.listeners["alert"]({ data: JSON.stringify({ figure: "Fed", direction: "up" }) }); });
    expect(result.current.alerts[0].figure).toBe("Fed");
    expect(MockES.last.url).toContain("token=T");
  });
});
