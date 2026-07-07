"use client";
import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";
import { getToken, clearToken } from "@/auth/token";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = getToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  async onResponse({ response }) {
    if (response.status === 401) {
      clearToken();
      if (typeof window !== "undefined") window.location.assign("/login");
    }
    return response;
  },
};

export const _authMiddleware = authMiddleware;

export const client = createClient<paths>({ baseUrl: API_BASE });
client.use(authMiddleware);
