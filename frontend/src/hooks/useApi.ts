"use client";
import useSWR from "swr";
import { client } from "@/api/client";

// Generic SWR read bound to the typed client. `path` is a schema path; returns data|undefined.
export function useApiGet<T>(key: string | null, fetcher: () => Promise<T>) {
  return useSWR<T>(key, key ? fetcher : null);
}
export { client };
