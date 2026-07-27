const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("unicore_token");
}

export function setToken(token: string | null): void {
  if (token === null) window.localStorage.removeItem("unicore_token");
  else window.localStorage.setItem("unicore_token", token);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const response = await fetch(`${BASE}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (response.status === 401 && token && typeof window !== "undefined") {
    // Session expired/revoked. Credential failures (no token attached) fall
    // through to the normal error path and render inline.
    setToken(null);
    window.location.href = "/login";
    throw new ApiError(401, "Session expired — please sign in again.");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = data.detail ?? data.error?.message ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
