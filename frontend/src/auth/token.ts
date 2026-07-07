const KEY = "bw_token";
export function getToken(): string | null {
  return typeof window === "undefined" ? null : window.localStorage.getItem(KEY);
}
export function setToken(token: string): void {
  window.localStorage.setItem(KEY, token);
}
export function clearToken(): void {
  window.localStorage.removeItem(KEY);
}
