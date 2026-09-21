import { useState } from 'react';
import { CAPTURE_KINDS, type CaptureItem, type CaptureKind } from '../types';
import { createCapture } from '../api/captures';
import { ApiError } from '../api/client';

/**
 * Universal capture (Plan.md §11).
 *
 * Classification is manual — you pick the kind. AI classification is explicitly
 * deferred, and the UI is built so adding it later means preselecting a value
 * rather than restructuring anything.
 *
 * Speed is the whole point: a capture box that takes three clicks does not get
 * used. Cmd/Ctrl+Enter submits, focus stays in the field, kind persists between
 * captures because consecutive thoughts tend to be the same sort of thing.
 */
export function CaptureBox({ onCaptured }: { onCaptured: (item: CaptureItem) => void }) {
  const [body, setBody] = useState('');
  const [kind, setKind] = useState<CaptureKind>('note');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(false);

  // The kind chips and the button only appear once you are actually capturing.
  // Collapsed, this is one line; expanded it is the full form it always was.
  //
  // It sits inside the Inbox tile, where at full height it took most of the tile
  // and left room for a single inbox item — a panel called Inbox that mostly was
  // not one. Nothing is removed, only deferred until it is relevant.
  const expanded = active || body.trim().length > 0;

  async function submit() {
    const trimmed = body.trim();
    if (!trimmed || saving) return;

    setSaving(true);
    setError(null);
    try {
      const created = await createCapture(trimmed, kind);
      setBody('');
      onCaptured(created);
    } catch (err) {
      // Keep the text on failure. Losing a thought because the API was down is
      // the one unforgivable bug in a capture tool.
      setError(err instanceof ApiError ? err.message : 'Could not save.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="capture"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <textarea
        className="capture-input"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        onFocus={() => setActive(true)}
        onBlur={() => setActive(false)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            void submit();
          }
        }}
        placeholder="Capture a thought…"
        rows={expanded ? 2 : 1}
      />

      {expanded && (
        // onMouseDown, not onClick: blur fires first on click and would collapse
        // the form out from under the button before it registered.
        <div className="capture-actions" onMouseDown={(e) => e.preventDefault()}>
          <div className="capture-kinds">
            {CAPTURE_KINDS.map((k) => (
              <button
                key={k}
                type="button"
                className={k === kind ? 'chip is-selected' : 'chip'}
                onClick={() => setKind(k)}
              >
                {k}
              </button>
            ))}
          </div>

          <button type="submit" className="button" disabled={!body.trim() || saving}>
            {saving ? 'Saving…' : 'Capture'}
          </button>
        </div>
      )}

      {error && <p className="capture-error">{error}</p>}
    </form>
  );
}
