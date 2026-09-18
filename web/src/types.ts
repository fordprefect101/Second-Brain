/**
 * Domain types.
 *
 * These describe DOMAIN CONCEPTS — not database rows, and not provider payloads.
 * They become the contract that NoteService and friends implement at step 7, so a
 * mistake here propagates into the provider layer rather than staying in the UI.
 *
 * The rule that keeps the layering honest (Plan.md §10):
 *
 *   No type in this file may contain a field that only one provider could supply.
 *
 * The concrete trap is `path`. A path is an Obsidian implementation detail. Put it
 * on `Note` and every component touching a note quietly assumes notes are files —
 * and then Notion pages arrive in Phase 4 with no path, and the assumption is
 * spread across thirty components. That is a layering violation arriving through a
 * type definition instead of an import, which is why it is easy to miss in review.
 */

/** Which service a piece of information came from. */
export type SourceId =
  | 'personal_os' // captures — the only thing this app owns
  | 'obsidian'
  | 'notion'
  | 'google_calendar'
  | 'google_tasks'
  | 'gmail'
  | 'google_drive'
  | 'google_sheets'
  | 'github'
  | 'spotify';

/** Display metadata for a source. Results must always name their origin (§12). */
export interface SourceMeta {
  id: SourceId;
  label: string;
}

export const SOURCES: Record<SourceId, SourceMeta> = {
  personal_os: { id: 'personal_os', label: 'Personal OS' },
  obsidian: { id: 'obsidian', label: 'Obsidian' },
  notion: { id: 'notion', label: 'Notion' },
  google_calendar: { id: 'google_calendar', label: 'Calendar' },
  google_tasks: { id: 'google_tasks', label: 'Tasks' },
  gmail: { id: 'gmail', label: 'Gmail' },
  google_drive: { id: 'google_drive', label: 'Drive' },
  google_sheets: { id: 'google_sheets', label: 'Sheets' },
  github: { id: 'github', label: 'GitHub' },
  spotify: { id: 'spotify', label: 'Spotify' },
};

// ---------------------------------------------------------------------------
// Capture — the one thing the Personal OS owns
// ---------------------------------------------------------------------------

export type CaptureKind = 'note' | 'idea' | 'task' | 'resource' | 'reminder';

export const CAPTURE_KINDS: CaptureKind[] = [
  'note',
  'idea',
  'task',
  'resource',
  'reminder',
];

export type CaptureStatus = 'inbox' | 'routed' | 'archived';

export interface CaptureItem {
  id: string;
  body: string;
  /** Chosen by hand. AI classification is explicitly deferred (Plan.md §11). */
  kind: CaptureKind;
  status: CaptureStatus;
  /** ISO 8601. Strings, not Date objects — this is what crosses the wire. */
  createdAt: string;
  /**
   * Where it went when routed, e.g. 'obsidian:Ideas/AI guitar teacher.md'.
   * A snapshot taken at routing time, never updated — it records history, so it
   * has no staleness obligation. Present only when status is 'routed'.
   */
  routedToRef?: string;
}

// ---------------------------------------------------------------------------
// Note — owned by an external service, never by us
// ---------------------------------------------------------------------------

export interface Note {
  /**
   * Opaque internal id from entity_map. Deliberately NOT a file path: the UI must
   * not be able to tell that an Obsidian note is a file on disk.
   */
  id: string;
  title: string;
  /** Short preview for lists. Never the full body — that is fetched on demand. */
  excerpt: string;
  tags: string[];
  /** ISO 8601 */
  modifiedAt: string;
  source: SourceId;
}

// ---------------------------------------------------------------------------
// Search — shape only; real results arrive at step 9
// ---------------------------------------------------------------------------

export interface SearchResult {
  id: string;
  title: string;
  excerpt: string;
  /** Required, not optional: a result that cannot name its source is a bug (§12). */
  source: SourceId;
  /** ts_rank score. Only meaningful relative to other results in the same query. */
  rank: number;
  /**
   * ISO 8601, and its meaning depends on `source`: for a calendar event this is
   * when the event *starts*, for a task its due date, for a note the last edit,
   * for a repo the last push. Render accordingly — an event wants an absolute
   * date, a note wants "edited 3 days ago".
   */
  modifiedAt: string | null;
}
