import { useCallback, useEffect } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { TopBar } from './components/TopBar';
import { Home } from './pages/Home';

/**
 * Layout shell — the grid, with everything else layered over it.
 *
 * The grid is rendered unconditionally rather than as a route, so every other
 * page becomes an overlay on top of it. That is what makes expanding a tile feel
 * like opening something rather than navigating away, and it means the existing
 * pages (Inbox, Tasks, Projects, NoteDetail) are reused untouched as their own
 * expanded views.
 *
 * Routing still does the work, which is the point: the URL changes, so browser
 * back closes the overlay, refresh reopens it, and any view is linkable. An
 * overlay whose only exit is the right X is the dead end this avoids.
 *
 * The cost is that deep-linking to /inbox also mounts the grid behind it, so its
 * tiles fetch too. On a local single-user tool that is a few milliseconds, bought
 * in exchange for closing being instant instead of a fresh page render.
 */
export function App() {
  const location = useLocation();
  const navigate = useNavigate();

  const isOverlay = location.pathname !== '/';

  /**
   * Close one layer, not all of them.
   *
   * It used to go straight to '/'. That loses your place on the way back out:
   * Notes tile -> /knowledge -> open a note -> Escape, and the list you were
   * browsing is gone. Stepping back returns you to it.
   *
   * The guard matters because history may not contain this app at all — open
   * /notes/abc in a fresh tab and going back leaves the site entirely. React
   * Router marks that entry `key: 'default'`, which is the only reliable signal
   * that nothing preceded it.
   */
  const close = useCallback(() => {
    if (location.key === 'default') navigate('/');
    else navigate(-1);
  }, [location.key, navigate]);

  useEffect(() => {
    if (!isOverlay) return;

    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') close();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOverlay, close]);

  return (
    <div className="app">
      <TopBar />

      <main className="main">
        <Home />
      </main>

      {isOverlay && (
        <div
          className="overlay"
          // Clicking the backdrop closes; clicking inside must not. Without the
          // stopPropagation below, selecting text in a note would close it.
          onClick={close}
          role="presentation"
        >
          <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="overlay-close"
              onClick={close}
              aria-label="Close"
            >
              ×
            </button>
            <Outlet />
          </div>
        </div>
      )}
    </div>
  );
}
