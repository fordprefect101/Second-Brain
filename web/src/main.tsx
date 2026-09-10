import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';

import { App } from './App';
import { Home } from './pages/Home';
import { Inbox } from './pages/Inbox';
import { Knowledge } from './pages/Knowledge';
import { Search } from './pages/Search';
import { Tasks } from './pages/Tasks';
import { Projects } from './pages/Projects';
import { Settings } from './pages/Settings';
import { Placeholder } from './pages/Placeholder';
import './index.css';

/**
 * Real URLs, not useState navigation: /inbox must be linkable and survive a refresh.
 *
 * App is a layout route — no path, children render into its <Outlet />.
 */
const router = createBrowserRouter([
  {
    element: <App />,
    children: [
      { path: '/', element: <Home /> },
      { path: '/inbox', element: <Inbox /> },
      { path: '/knowledge', element: <Knowledge /> },
      { path: '/search', element: <Search /> },
      { path: '/tasks', element: <Tasks /> },
      { path: '/settings', element: <Settings /> },

      // Navigable but empty. Real pages replace these in later phases; the routes
      // exist now so the information architecture is testable by clicking it.
      { path: '/projects', element: <Projects /> },
      {
        path: '/areas',
        element: <Placeholder title="Areas" phase="Phase 2 — needs a knowledge source" />,
      },
      {
        path: '/resources',
        element: <Placeholder title="Resources" phase="Phase 4 — Drive and Notion" />,
      },
      {
        path: '/ideas',
        element: <Placeholder title="Ideas" phase="Step 5 — routed captures land here" />,
      },
      {
        path: '/goals',
        element: <Placeholder title="Goals" phase="Phase 2 — ownership undecided" />,
      },

      { path: '*', element: <Placeholder title="Not found" phase="No such section" /> },
    ],
  },
]);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
