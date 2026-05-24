import { Signal, ScannerStatus } from "@/types";

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetcher<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export const api = {
  signals: (activeOnly = true, direction?: string) => {
    let url = `/api/signals?active_only=${activeOnly}&limit=100`;
    if (direction) url += `&direction=${direction}`;
    return fetcher<Signal[]>(url);
  },
  signalsBySymbol: (symbol: string) =>
    fetcher<Signal[]>(`/api/signals/${symbol}`),
  top: () => fetcher<Signal[]>("/api/top?limit=10"),
  status: () => fetcher<ScannerStatus>("/api/status"),
};
