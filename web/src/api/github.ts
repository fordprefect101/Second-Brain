import { api, ApiError } from './client';

export interface GitHubStatus {
  connected: boolean;
  state: 'connected' | 'disconnected' | 'invalid' | 'error';
  username?: string;
  detail?: string;
}

export interface Repository {
  id: string;
  name: string;
  description: string | null;
  pushedAt: string;
  language: string | null;
  private: boolean;
  url: string | null;
  source: string;
}

export interface Activity {
  id: string;
  kind: string;
  summary: string;
  repository: string;
  occurredAt: string;
  url: string | null;
  source: string;
}

/** 401 means the token was revoked or expired — reconnect, not a bug. */
export const needsToken = (e: unknown) => e instanceof ApiError && e.status === 401;

/** 429 is a rate limit: transient, so the message is "wait", not "you can't". */
export const isRateLimited = (e: unknown) => e instanceof ApiError && e.status === 429;

export const githubStatus = () => api.get<GitHubStatus>('/github/status');
export const listRepos = (limit = 30) => api.get<Repository[]>(`/github/repos?limit=${limit}`);
export const listActivity = (limit = 30) => api.get<Activity[]>(`/github/activity?limit=${limit}`);
export const disconnectGitHub = () => api.post<{ note: string }>('/github/disconnect');

export const connectGitHub = (token: string) =>
  api.post<{ username: string }>('/github/connect', { token });
