import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * One panel of the grid: a preview, with a way to see all of it.
 *
 * Every tile is this component. That matters more than it looks — the previous UI
 * rendered search results, captures, notes and tasks through one generic list, so
 * five different kinds of thing were structurally identical and the page read as
 * flat. The fix is not a different list; it is that the *container* is uniform and
 * the *contents* are not.
 *
 * `expandTo` is a route, not a callback. The expanded view is a URL — so it is
 * linkable, survives refresh, and closes on browser back. A tile whose "see all"
 * opened local state would lose every one of those.
 */
export function Tile({
  title,
  count,
  expandTo,
  accent,
  className,
  children,
}: {
  title: string;
  /** Shown beside the title. Omitted rather than zero when there is nothing. */
  count?: number;
  /** Route for the expanded view. Omit for tiles with nothing more to show. */
  expandTo?: string;
  /**
   * Which source this tile is about, which colours its header.
   *
   * Carries the row colours up to the tile itself. Without it the grid is five
   * identical grey rectangles and the colour system only exists three levels
   * down, where you have to already be reading to notice it. With it, each tile
   * is identifiable from the corner of your eye — and it creates variation
   * without having to declare one tile more important than the others.
   */
  accent?: 'obsidian' | 'personal_os' | 'google_calendar' | 'google_tasks' | 'github';
  className?: string;
  children: ReactNode;
}) {
  const navigate = useNavigate();

  return (
    <section className={className ? `tile ${className}` : 'tile'} data-accent={accent}>
      <header className="tile-head">
        <h2 className="tile-title">{title}</h2>

        <div className="tile-actions">
          {count !== undefined && count > 0 && (
            <span className="tile-count">{count}</span>
          )}
          {expandTo && (
            <button
              type="button"
              className="tile-expand"
              onClick={() => navigate(expandTo)}
              aria-label={`Open ${title}`}
              title={`Open ${title}`}
            >
              {/* Two corner brackets — the conventional "expand" mark, and legible
                  at 14px in a way most glyphs are not. */}
              <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
                <path
                  d="M6 1H1v5M10 15h5v-5M15 6V1h-5M1 10v5h5"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          )}
        </div>
      </header>

      <div className="tile-body">{children}</div>
    </section>
  );
}

/**
 * What a tile shows when it has nothing.
 *
 * A separate component because "empty" is a state worth designing rather than a
 * blank area: an empty tile and a broken tile look identical otherwise, and one
 * of those needs fixing.
 */
export function TileEmpty({ children }: { children: ReactNode }) {
  return <p className="tile-empty">{children}</p>;
}

/**
 * What a tile shows while it is still loading.
 *
 * Not a spinner, and not nothing. State starts as an empty array, so without
 * this every tile rendered its empty message for the first second — the app
 * announced "Nothing scheduled" and "No repositories" and then corrected itself.
 * That is not a missing flourish; it is the interface stating something false.
 *
 * Grey bars are the honest answer: they say "rows are coming" and occupy roughly
 * the space the rows will, so nothing jumps when the data lands.
 */
export function TileSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        // Varying widths, because four identical bars read as a graphic rather
        // than as pending content.
        <div key={i} className="skeleton-row" style={{ width: `${92 - i * 11}%` }} />
      ))}
    </div>
  );
}

/**
 * The same idea one level up: placeholder cards for an expanded view.
 *
 * The dashboard solved loading properly and the pages it expands into did not —
 * they printed the word "Loading…" into a subtitle and rendered nothing else, so
 * opening Tasks gave you a panel two lines tall that then jumped to full height
 * when the data landed. Same failure the tiles had before TileSkeleton, in the
 * place you look at *after* clicking something, which is when you are least
 * willing to wait.
 *
 * Card-shaped rather than bar-shaped because that is what these lists hold — a
 * skeleton is only useful if it occupies roughly the space the real thing will.
 */
export function ListSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <ul className="list" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <li key={i} className="list-item">
          <div className="skeleton">
            {/* A short bar for the meta line, a long one for the title. */}
            <div className="skeleton-row" style={{ width: '22%' }} />
            <div className="skeleton-row" style={{ width: `${78 - i * 9}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

/**
 * What a tile shows when its source failed.
 *
 * The case this exists for: allSettled swallows rejections, so a GitHub token
 * expiring rendered as "No repositories" — a failure presented as a fact, and
 * indistinguishable from the truth. A source that breaks has to say so.
 */
export function TileError({
  children,
  onAction,
  actionLabel = 'retry',
}: {
  children: ReactNode;
  onAction?: () => void;
  /** "retry" for a transient failure, "reconnect" for a dead token — different
      problems, and offering the wrong one wastes a click. */
  actionLabel?: string;
}) {
  return (
    <p className="tile-error">
      {children}
      {onAction && (
        <>
          {' '}
          <button type="button" className="link-button" onClick={onAction}>
            {actionLabel}
          </button>
        </>
      )}
    </p>
  );
}
