import { api } from './client';
import type { CaptureItem, CaptureKind, CaptureStatus } from '../types';

/**
 * Capture endpoints.
 *
 * The return type is the domain type from types.ts, not a separate "API response"
 * type. The API is built to serve the domain model — camelCase keys, the same field
 * names — so no translation layer is needed. If the two ever diverge, the mapping
 * belongs here, in one place, rather than spread across components.
 */

export function listCaptures(status?: CaptureStatus): Promise<CaptureItem[]> {
  const query = status ? `?status=${status}` : '';
  return api.get<CaptureItem[]>(`/captures${query}`);
}

export function createCapture(body: string, kind: CaptureKind): Promise<CaptureItem> {
  return api.post<CaptureItem>('/captures', { body, kind });
}

export function archiveCapture(id: string): Promise<CaptureItem> {
  return api.post<CaptureItem>(`/captures/${id}/archive`);
}
