import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import { search } from '../api/search';
import type { SearchResult } from '../types';
import { SourceBadge } from './SourceBadge';

interface Source {
  id: string;
  title: string;
  source: string;
}

interface AskResponse {
  answer: string;
  sources: Source[];
}

type Mode = 'search' | 'ask';

/**
 * Words that start a question. Not an attempt at grammar — just the handful that
 * actually begin one in practice, which is enough to guess right most of the time
 * and cheap to override when it does not.
 */
const QUESTION_WORDS = [
  'why',
  'what',
  'how',
  'when',
  'where',
  'who',
  'which',
  'did',
  'do',
  'does',
  'is',
  'are',
  'can',
  'should',
];

/**
 * Guess whether this is a lookup or a question.
 *
 * "resume builder" wants a list. "why did I pick fish audio?" wants an answer.
 * Nobody decides which mode they are in before typing, so the box decides and
 * says so, and the label flips it when the guess is wrong.
 */
function inferMode(text: string): Mode {
  const trimmed = text.trim().toLowerCase();
  if (!trimmed) return 'search';
  if (trimmed.endsWith('?')) return 'ask';

  const [first] = trimmed.split(/\s+/);
  // A question word alone is not a question — "what" is a search for the word.
  // Two or more words starting with one almost always is.
  const wordCount = trimmed.split(/\s+/).length;
  return first && QUESTION_WORDS.includes(first) && wordCount > 2 ? 'ask' : 'search';
}

export function AskBox() {
  const [text, setText] = useState('');
  const [override, setOverride] = useState<Mode | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  // The override is a correction of one guess, not a sticky preference — editing
  // the text means the guess is being made again on different input.
  const mode: Mode = override ?? inferMode(text);

  const latest = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  // Which result the arrow keys are on. -1 means none — Enter then submits
  // rather than opening something, which is what you want before you have
  // started moving through the list.
  const [cursor, setCursor] = useState(-1);

  /**
   * Reach the box from anywhere.
   *
   * Cmd/Ctrl+K is the near-universal convention; "/" is the other one, and it
   * costs nothing to support both. Both are ignored while you are typing
   * somewhere else — otherwise "/" becomes impossible to type into the capture
   * box, and a shortcut that eats a character is worse than no shortcut.
   */
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target?.isContentEditable;

      const shortcut =
        ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') ||
        (event.key === '/' && !typing);

      if (!shortcut) return;
      event.preventDefault();
      inputRef.current?.focus();
      inputRef.current?.select();
    }

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Search runs as you type; asking does not. A 30-second model call per keystroke
  // would be absurd, so ask waits for Enter. That asymmetry is the honest one:
  // the two modes have costs three orders of magnitude apart.
  useEffect(() => {
    if (mode !== 'search') return;

    const query = text.trim();
    if (!query) {
      setResults([]);
      return;
    }

    const id = ++latest.current;
    const timer = setTimeout(() => {
      search(query)
        .then((hits) => {
          if (id !== latest.current) return;
          setResults(hits);
          setCursor(-1);
        })
        .catch(() => id === latest.current && setResults([]));
    }, 180);

    return () => clearTimeout(timer);
  }, [text, mode]);

  // A spinner for 30 seconds looks broken. A counter looks like work.
  useEffect(() => {
    if (!busy) return;
    setElapsed(0);
    const tick = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(tick);
  }, [busy]);

  async function submit() {
    const question = text.trim();
    if (!question || busy) return;
    if (mode === 'search') return; // already live

    setBusy(true);
    setError(null);
    setAnswer(null);

    try {
      setAnswer(await api.post<AskResponse>('/ask', { question }));
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 503
          ? // 503 is the model being down, which is fixable in five seconds — it
            // must not read as a generic failure.
            'The local model is not running. Start it with `ollama serve`.'
          : 'Could not answer that.',
      );
    } finally {
      setBusy(false);
    }
  }

  /**
   * Arrows move, Enter acts, Escape backs out.
   *
   * What Enter does depends on where the cursor is, which is the point: before
   * you have moved it there is nothing selected, so Enter means "ask" — and once
   * you have, it means "open that". Mixing those would make the common case
   * (type, Enter, get an answer) require an extra keystroke to avoid opening
   * whatever happened to be first.
   */
  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    const open = results.slice(0, 8);

    if (event.key === 'ArrowDown' && open.length) {
      event.preventDefault();
      setCursor((c) => (c + 1) % open.length);
      return;
    }

    if (event.key === 'ArrowUp' && open.length) {
      event.preventDefault();
      setCursor((c) => (c <= 0 ? open.length - 1 : c - 1));
      return;
    }

    if (event.key === 'Escape') {
      // First press drops the selection, second clears the box. Escaping
      // straight to empty would throw away a query you were still refining.
      if (cursor >= 0) setCursor(-1);
      else clear();
      return;
    }

    if (event.key === 'Enter') {
      const chosen = cursor >= 0 ? open[cursor] : undefined;
      if (chosen && chosen.source === 'obsidian') {
        navigate(`/notes/${chosen.id}`);
        return;
      }
      void submit();
    }
  }

  function clear() {
    setText('');
    setResults([]);
    setAnswer(null);
    setError(null);
    setOverride(null);
  }

  return (
    <section className="tile area-ask askbox">
      <div className="askbox-input-row">
        <input
          className="askbox-input"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setOverride(null);
            // The previous answer stays. Clearing it on every keystroke meant
            // refining a question destroyed the thing you were refining against
            // — and comparing two phrasings is exactly what you do when
            // retrieval is keyword-only and the first attempt found nothing.
            // It is replaced when a new ask completes, or by the clear button.
          }}
          onKeyDown={onKeyDown}
          ref={inputRef}
          placeholder="Search everything, or ask a question…"
          aria-label="Search or ask"
          autoFocus
        />

        <button
          type="button"
          className="askbox-mode"
          data-mode={mode}
          onClick={() => setOverride(mode === 'ask' ? 'search' : 'ask')}
          title="Switch mode"
        >
          {mode === 'ask' ? 'ask ⏎' : 'search'}
        </button>

        {/* Shown until the box has focus, at which point you have clearly found
            it and the hint is just clutter. */}
        {!text && <span className="askbox-kbd">⌘K</span>}

        {(text || answer) && (
          <button type="button" className="askbox-clear" onClick={clear} aria-label="Clear">
            ×
          </button>
        )}
      </div>

      {busy && (
        <p className="askbox-status">
          Reading your notes… {elapsed}s
          <span className="askbox-hint"> · a local model is slow, this is normal</span>
        </p>
      )}

      {error && <p className="banner is-error">{error}</p>}

      {answer && !busy && (
        <div className="answer">
          <p className="answer-text">{answer.answer}</p>

          {answer.sources.length > 0 && (
            <div className="answer-sources">
              <span className="answer-sources-label">based on</span>
              {answer.sources.map((s) => (
                <span key={s.id} className="answer-source">
                  <SourceBadge source={s.source as never} />
                  {s.title}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {mode === 'search' && results.length > 0 && (
        <ul className="rows askbox-results">
          {results.slice(0, 8).map((result, index) => (
            <li
              key={result.id}
              className={index === cursor ? 'row is-cursor' : 'row'}
              data-source={result.source}
            >
              {result.source === 'obsidian' ? (
                <Link to={`/notes/${result.id}`} className="row-main row-link">
                  {result.title}
                </Link>
              ) : (
                <span className="row-main">{result.title}</span>
              )}
              <span className="row-trail">{result.source.replace('google_', '')}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
