import { Link } from 'react-router-dom';
import { useRootPref } from '../lib/prefs';
import { Segmented, icons, type SegmentedOption } from './Segmented';

type Theme = 'system' | 'light' | 'dark';
type Density = 'cozy' | 'compact';

const THEMES: readonly SegmentedOption<Theme>[] = [
  { value: 'system', label: 'Match system theme', icon: icons.auto },
  { value: 'light', label: 'Light theme', icon: icons.sun },
  { value: 'dark', label: 'Dark theme', icon: icons.moon },
];

const DENSITIES: readonly SegmentedOption<Density>[] = [
  { value: 'cozy', label: 'Comfortable spacing', icon: icons.cozy },
  { value: 'compact', label: 'Compact spacing', icon: icons.compact },
];

/**
 * Brand, and the two controls that belong to the whole app.
 *
 * It replaced a 208px sidebar of seven links: once every section is a tile on the
 * grid, navigation stops being a list of destinations, because the destinations are
 * on screen. Horizontal rather than vertical because the grid needs the width — a
 * bento with a sidebar is a bento with one fewer column.
 *
 * What is left over is the argument for what goes here now. Not links: settings that
 * are true of the entire surface rather than of any one tile.
 *
 * Theme has three states, and the third is the one that was missing — the app only
 * ever followed the OS, and a tool used at 9am and at 11pm needs to be overridable.
 * Density is the answer to a question the app cannot answer for you: comfortable is
 * right when you are reading your day, compact is right when there are forty open
 * tasks and you want to see all of them.
 *
 * Neither writes to a store or re-renders the tree — they set one attribute on
 * <html> and CSS does the rest.
 */
export function TopBar() {
  const [theme, setTheme] = useRootPref<Theme>(
    'theme',
    ['system', 'light', 'dark'],
    'system',
  );
  const [density, setDensity] = useRootPref<Density>(
    'density',
    ['cozy', 'compact'],
    'cozy',
  );

  return (
    <header className="topbar">
      <Link to="/" className="topbar-brand">
        Personal OS
      </Link>

      <div className="topbar-controls">
        <Segmented label="Density" options={DENSITIES} value={density} onChange={setDensity} />
        <Segmented label="Theme" options={THEMES} value={theme} onChange={setTheme} />

        <Link to="/settings" className="topbar-link">
          Settings
        </Link>
      </div>
    </header>
  );
}
