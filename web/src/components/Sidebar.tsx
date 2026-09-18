import { NavLink } from 'react-router-dom';

/**
 * Every section here is real. There are no stubs.
 *
 * There used to be four — Areas, Resources, Ideas, Goals — shown greyed so the
 * information architecture stayed visible while it was cheap to change. That was
 * right while the app was mock data and the shape was being tested by clicking it.
 * It stopped being right once this became a tool used daily: a dead link is a dead
 * link, and three of the four had gone stale anyway (Ideas promised routed captures
 * would land there; they route to Obsidian instead. Resources promised Notion,
 * which was ruled out).
 *
 * Ideas and Resources need no home of their own: a capture carries a `kind`, so
 * both are already visible in Inbox and findable in search. Areas and Goals had
 * no owner and no data.
 *
 * Tasks and Knowledge are still destinations, and stop being so once Today absorbs
 * them — removing them before that would orphan working features.
 */
interface Section {
  path: string;
  label: string;
}

const SECTIONS: Section[] = [
  { path: '/', label: 'Today' },
  { path: '/inbox', label: 'Inbox' },
  { path: '/tasks', label: 'Tasks' },
  { path: '/knowledge', label: 'Knowledge' },
  { path: '/projects', label: 'Projects' },
  { path: '/search', label: 'Search' },
  { path: '/settings', label: 'Settings' },
];

export function Sidebar() {
  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        Personal OS
        {/* Was "v0.1 · mock data", which stopped being true three phases ago and
            was the first thing anyone reads on the screen. */}
        <span className="sidebar-version">obsidian · calendar · tasks · github</span>
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
                ['sidebar-link', isActive ? 'is-active' : ''].filter(Boolean).join(' ')
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
