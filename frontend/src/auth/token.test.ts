import { describe, it, expect, beforeEach } from "vitest";
import { getToken, setToken, clearToken } from "./token";
describe("token store", () => {
  beforeEach(() => window.localStorage.clear());
  it("round-trips", () => {
    expect(getToken()).toBeNull();
    setToken("abc");
    expect(getToken()).toBe("abc");
    clearToken();
    expect(getToken()).toBeNull();
  });
});
