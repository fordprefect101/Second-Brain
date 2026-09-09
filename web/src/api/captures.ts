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

export interface RouteResult {
  captureId: string;
  entityId: string;
  ref: string;
}

/** Turn a capture into a note in the vault. Writes a real file. */
export function routeCapture(id: string, folder = ''): Promise<RouteResult> {
  return api.post<RouteResult>(`/captures/${id}/route`, { folder });
}

/** Reverse a routing. Refuses if the note was edited in Obsidian since. */
export function undoRoute(id: string): Promise<CaptureItem> {
  return api.post<CaptureItem>(`/captures/${id}/undo-route`);
}
