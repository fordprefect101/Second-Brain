/**
 * Minimal fetch wrapper.
 *
 * Deliberately raw fetch, no data-fetching library. This is the baseline for the
 * step 5.5 comparison: build it by hand first, feel what is missing, then decide
 * whether TanStack Query earns its place (Plan.md §4).
 *
 * Watch for what this does NOT do — that list is the actual lesson:
 *   - no caching: every mount refetches from scratch
 *   - no deduplication: two components asking at once make two requests
 *   - no shared state: Home and Inbox each keep their own copy of the same data
 *   - no revalidation: data is stale the moment another tab writes
 *   - no retry, no request cancellation on unmount
 */

const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;

  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch {
    // fetch only rejects on network failure — for a local tool that almost always
    // means the API is not running, so say that rather than "Failed to fetch".
    throw new ApiError('Cannot reach the API. Is it running on :8000?', 0);
  }

  if (!response.ok) {
    // FastAPI puts human-readable errors in `detail`; fall back to the status text.
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      // Body was not JSON. The status text will do.
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
};
