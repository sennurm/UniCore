const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "unicore_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token === null) window.localStorage.removeItem(TOKEN_KEY);
  else window.localStorage.setItem(TOKEN_KEY, token);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** Set once a redirect is in flight so concurrent 401s don't stack navigations. */
let redirecting = false;

/**
 * An invalid/expired/revoked session must always land the user on the sign-in
 * page — never a raw error or a half-rendered screen. Every request path
 * (json, upload, download) funnels through here.
 */
function handleUnauthorized(): never {
  setToken(null);
  if (typeof window !== "undefined" && !redirecting) {
    redirecting = true;
    window.location.replace("/login");
  }
  throw new ApiError(401, "Session expired — please sign in again.");
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers = { ...extra };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

async function assertOk(response: Response, hadToken: boolean): Promise<Response> {
  if (response.ok) return response;
  // 401 with a token attached = the session died. 401 without one is a
  // credential failure on a public endpoint (login/OTP) and renders inline.
  if (response.status === 401 && hadToken) handleUnauthorized();
  let detail = response.statusText;
  try {
    const data = await response.json();
    detail = data.detail ?? data.error?.message ?? detail;
  } catch {
    /* keep statusText */
  }
  throw new ApiError(response.status, detail);
}

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const token = getToken();
  const response = await fetch(`${BASE}${path}`, {
    method: options.method ?? "GET",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  await assertOk(response, Boolean(token));
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function upload<T>(path: string, form: FormData): Promise<T> {
  const token = getToken();
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  await assertOk(response, Boolean(token));
  return (await response.json()) as T;
}

export function downloadUrl(path: string): string {
  return `${BASE}${path}`;
}

/**
 * Download a file from an authenticated endpoint.
 * A plain <a href> cannot carry the Authorization header, so every protected
 * download must go through fetch + blob (project security rule: no endpoint is
 * public just to make a link work).
 */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const token = getToken();
  const response = await fetch(`${BASE}${path}`, { headers: authHeaders() });
  await assertOk(response, Boolean(token));
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Resolves true only if the stored token is accepted by the server. */
export async function validateSession(): Promise<boolean> {
  if (!getToken()) return false;
  try {
    await api("/auth/me");
    return true;
  } catch {
    return false; // api() has already cleared the token and redirected on 401
  }
}
