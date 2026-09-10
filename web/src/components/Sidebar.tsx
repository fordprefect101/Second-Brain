import { NavLink } from 'react-router-dom';

/**
 * The ten sections from Plan.md §6.
 *
 * `built: false` marks sections that are navigable but empty. Showing them greyed
 * rather than hiding them keeps the information architecture visible while it is
 * still cheap to change — which is the actual purpose of this step.
 */
interface Section {
  path: string;
  label: string;
  built: boolean;
}

const SECTIONS: Section[] = [
  { path: '/', label: 'Home', built: true },
  { path: '/inbox', label: 'Inbox', built: true },
  { path: '/tasks', label: 'Tasks', built: true },
  { path: '/knowledge', label: 'Knowledge', built: true },
  { path: '/projects', label: 'Projects', built: true },
  { path: '/areas', label: 'Areas', built: false },
  { path: '/resources', label: 'Resources', built: false },
  { path: '/ideas', label: 'Ideas', built: false },
  { path: '/goals', label: 'Goals', built: false },
  { path: '/search', label: 'Search', built: true },
  { path: '/settings', label: 'Settings', built: true },
];

export function Sidebar() {
  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        Personal OS
        <span className="sidebar-version">v0.1 · mock data</span>
      </div>

      <ul className="sidebar-nav">
        {SECTIONS.map((section) => (
          <li key={section.path}>
            <NavLink
              to={section.path}
              // `end` stops "/" matching every route — without it Home stays
              // highlighted on every page.
              end={section.path === '/'}
              className={({ isActive }) =>
                [
                  'sidebar-link',
                  isActive ? 'is-active' : '',
                  section.built ? '' : 'is-stub',
                ]
                  .filter(Boolean)
                  .join(' ')
              }
            >
              {section.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
