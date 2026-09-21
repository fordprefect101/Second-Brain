import { Link } from 'react-router-dom';

/**
 * Brand and settings. That is all the chrome left.
 *
 * Replaces a 208px sidebar that held seven links. Once every section is a tile on
 * the grid, navigation stops being a list of destinations — the destinations are
 * on screen. What remains is the one thing that is not a tile, and the way home.
 *
 * Horizontal rather than vertical because the grid needs the width: a bento with
 * a sidebar is a bento with one fewer column.
 */
export function TopBar() {
  return (
    <header className="topbar">
      <Link to="/" className="topbar-brand">
        Personal OS
      </Link>

      <Link to="/settings" className="topbar-link">
        Settings
      </Link>
    </header>
  );
}
