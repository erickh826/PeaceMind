import { QueryClient, QueryFunction } from "@tanstack/react-query";

/**
 * API_BASE resolution order:
 * 1. VITE_API_URL env var  → points to deployed backend (e.g. https://peacemind-api.vercel.app)
 * 2. __PORT_5000__ token   → injected by deploy_website() for same-origin proxy
 * 3. ""                    → relative paths (local dev via Express proxy)
 */
const VITE_API_URL = import.meta.env.VITE_API_URL as string | undefined;
const PORT_TOKEN   = "__PORT_5000__";

export const API_BASE: string =
  VITE_API_URL
    ? VITE_API_URL.replace(/\/$/, "")          // e.g. https://peacemind-api.vercel.app
    : PORT_TOKEN.startsWith("__")
      ? ""                                      // local dev: relative
      : PORT_TOKEN;                             // deployed same-origin proxy

async function throwIfResNotOk(res: Response) {
  if (!res.ok) {
    const text = (await res.text()) || res.statusText;
    throw new Error(`${res.status}: ${text}`);
  }
}

export async function apiRequest(
  method: string,
  url: string,
  data?: unknown,
): Promise<Response> {
  const headers: Record<string, string> = data
    ? { "Content-Type": "application/json" }
    : {};

  const res = await fetch(`${API_BASE}${url}`, {
    method,
    headers,
    body: data ? JSON.stringify(data) : undefined,
  });

  await throwIfResNotOk(res);
  return res;
}

type UnauthorizedBehavior = "returnNull" | "throw";
export const getQueryFn: <T>(options: {
  on401: UnauthorizedBehavior;
}) => QueryFunction<T> =
  ({ on401: unauthorizedBehavior }) =>
  async ({ queryKey }) => {
    const res = await fetch(`${API_BASE}${queryKey.join("/")}`);
    if (unauthorizedBehavior === "returnNull" && res.status === 401) {
      return null;
    }
    await throwIfResNotOk(res);
    return await res.json();
  };

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      queryFn: getQueryFn({ on401: "throw" }),
      refetchInterval: false,
      refetchOnWindowFocus: false,
      staleTime: Infinity,
      retry: false,
    },
    mutations: { retry: false },
  },
});
