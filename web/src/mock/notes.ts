import type { Note } from '../types';

/**
 * Mock notes, standing in for an Obsidian vault until step 7.
 *
 * Note what is absent: no file paths. If a component needs one to render, that is
 * the signal that a provider detail has leaked into the domain type.
 */

const daysAgo = (d: number): string =>
  new Date(Date.now() - d * 24 * 60 * 60 * 1000).toISOString();

export const MOCK_NOTES: Note[] = [
  {
    id: 'note-1',
    title: 'Personalization Pipeline',
    excerpt:
      'Redis-backed feature store feeding the ranking model. Connection pooling was the first real bottleneck.',
    tags: ['redis', 'architecture', 'work'],
    modifiedAt: daysAgo(1),
    source: 'obsidian',
  },
  {
    id: 'note-2',
    title: 'Why Demucs over Spleeter',
    excerpt:
      'Separation quality on acoustic sources was the deciding factor. Slower, but the artefacts were unacceptable.',
    tags: ['audio', 'decision', 'music'],
    modifiedAt: daysAgo(3),
    source: 'obsidian',
  },
  {
    id: 'note-3',
    title: 'Postgres full-text search',
    excerpt:
      'tsvector stores lexemes, not words. Stemming throws away information you sometimes need back.',
    tags: ['postgres', 'search', 'learning'],
    modifiedAt: daysAgo(4),
    source: 'obsidian',
  },
  {
    id: 'note-4',
    title: 'Music transcription — open problems',
    excerpt:
      'Polyphonic transcription is still unsolved for dense mixes. Onset detection is the tractable part.',
    tags: ['music', 'research'],
    modifiedAt: daysAgo(9),
    source: 'obsidian',
  },
  {
    id: 'note-5',
    title: 'OAuth refresh token expiry',
    excerpt:
      'Unverified Google apps keep refresh tokens for seven days in testing mode. Plan for reauth.',
    tags: ['oauth', 'google', 'learning'],
    modifiedAt: daysAgo(12),
    source: 'obsidian',
  },
  {
    id: 'note-6',
    title: 'Reading list — retrieval',
    excerpt: 'BM25 original paper, the contextual retrieval writeup, and the RAG survey.',
    tags: ['reading', 'retrieval'],
    modifiedAt: daysAgo(20),
    source: 'obsidian',
  },
];
