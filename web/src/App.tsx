import { useEffect } from 'react';
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

  useEffect(() => {
    if (!isOverlay) return;

    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') navigate('/');
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOverlay, navigate]);

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
          onClick={() => navigate('/')}
          role="presentation"
        >
          <div className="overlay-panel" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="overlay-close"
              onClick={() => navigate('/')}
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
