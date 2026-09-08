import type { CaptureItem } from '../types';

/**
 * Mock captures. Replaced by the real API at step 5.
 *
 * Timestamps are computed relative to now so the Inbox always looks plausible
 * rather than showing "8 months ago" the week after this was written.
 */

const hoursAgo = (h: number): string =>
  new Date(Date.now() - h * 60 * 60 * 1000).toISOString();

export const MOCK_CAPTURES: CaptureItem[] = [
  {
    id: 'cap-1',
    body: 'Maybe build an AI guitar teacher.',
    kind: 'idea',
    status: 'inbox',
    createdAt: hoursAgo(2),
  },
  {
    id: 'cap-2',
    body: 'Remember to call the studio about the mixing session.',
    kind: 'reminder',
    status: 'inbox',
    createdAt: hoursAgo(5),
  },
  {
    id: 'cap-3',
    body: 'Interesting paper about polyphonic music transcription — check the eval methodology.',
    kind: 'resource',
    status: 'inbox',
    createdAt: hoursAgo(26),
  },
  {
    id: 'cap-4',
    body: 'Postgres FTS ranks short documents higher. Worth understanding ts_rank normalisation before step 9.',
    kind: 'note',
    status: 'inbox',
    createdAt: hoursAgo(30),
  },
  {
    id: 'cap-5',
    body: 'Write the ADR for why Obsidian is read-only in V1.',
    kind: 'task',
    status: 'routed',
    createdAt: hoursAgo(50),
    routedToRef: 'obsidian:Projects/Personal OS.md',
  },
];
