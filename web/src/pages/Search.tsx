/**
 * Unified search. The input is deliberately disabled — real search arrives at step 9,
 * after Postgres full-text search is understood (step 8).
 *
 * Showing the intended result shape now is useful: every result names its source,
 * because Plan.md §12 requires it and the requirement is easier to honour when the
 * layout assumes it from the start.
 */
export function Search() {
  return (
    <>
      <header className="page-header">
        <h1>Search</h1>
        <p className="page-subtitle">Across every connected source</p>
      </header>

      <input
        className="search-input"
        type="search"
        placeholder="Search is wired up at step 9"
        disabled
        aria-label="Search"
      />

      <div className="empty">
        <p className="empty-title">Not built yet</p>
        <p className="empty-detail">
          Step 8 covers Postgres full-text search; step 9 indexes notes and captures.
          Keyword search first — semantic search only once the keyword baseline's
          limits are demonstrable.
        </p>
      </div>
    </>
  );
}
