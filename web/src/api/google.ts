import { api, ApiError } from './client';

export interface GoogleStatus {
  connected: boolean;
  state: 'connected' | 'expired' | 'disconnected' | 'not_configured' | 'error';
  detail: string | null;
  scopes?: string[];
  expiresAt?: string;
}

export interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end: string;
  allDay: boolean;
  location: string | null;
  calendarName: string | null;
  source: string;
}

export interface Task {
  id: string;
  title: string;
  completed: boolean;
  due: string | null;
  notes: string | null;
  parentId: string | null;
  source: string;
  /** Which list this belongs to. Name to display, id to write against. */
  listName: string | null;
  listId: string | null;
}

export interface TaskList {
  id: string;
  name: string;
}

/**
 * 401 from these endpoints is not a bug — it means the refresh token died and the
 * user has to reconnect. Unverified Google apps get 7-day refresh tokens, so this
 * happens roughly weekly and must be presented as a normal state.
 */
export function needsReconnect(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export const googleStatus = () => api.get<GoogleStatus>('/google/status');
export const disconnectGoogle = () => api.post<{ note: string }>('/google/disconnect');

export const listEvents = (days = 1) => api.get<CalendarEvent[]>(`/events?days=${days}`);
export const listTasks = () => api.get<Task[]>('/tasks');

export function completeTask(id: string): Promise<Task> {
  // id is 'listId/taskId' — a Google task is only addressable with its list.
  return api.post<Task>(`/tasks/${id}/complete`);
}

/**
 * Every list, including empty ones.
 *
 * Separate from listTasks because a list with no tasks cannot be derived from
 * them — and that is exactly when it matters, since a new "To Read" list is
 * empty at the moment you first want to add to it.
 */
export const listTaskLists = () => api.get<TaskList[]>('/tasks/lists');

/**
 * Make a new list.
 *
 * The last gap in owning tasks from here — reading lists, reading, completing
 * and adding tasks all worked, but a list that did not exist yet had to be
 * created in Google Tasks itself. Which is the case that actually comes up: a
 * new "To Read" is empty exactly when you first want to put something in it.
 */
export const createTaskList = (name: string) =>
  api.post<TaskList>('/tasks/lists', { name });

export function createTask(
  listId: string,
  title: string,
  notes?: string,
): Promise<Task> {
  // listId is required and never defaulted: a book landing in a work backlog is
  // wrong in a way that is easy not to notice.
  return api.post<Task>('/tasks', { listId, title, notes });
}

/** Full-page redirect, not fetch: the user must see and interact with Google. */
export function startGoogleConnect(): void {
  const base = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
  window.location.href = `${base}/google/connect`;
}
