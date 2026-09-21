import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';

import { App } from './App';
import { Inbox } from './pages/Inbox';
import { Knowledge } from './pages/Knowledge';
import { NoteDetail } from './pages/NoteDetail';
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
      // No route for '/'. App renders the grid unconditionally, so the index is
      // an empty Outlet — and every child below is therefore an overlay over it.
      { path: '/inbox', element: <Inbox /> },
      { path: '/knowledge', element: <Knowledge /> },
      { path: '/notes/:id', element: <NoteDetail /> },
      { path: '/search', element: <Search /> },
      { path: '/tasks', element: <Tasks /> },
      { path: '/settings', element: <Settings /> },

      { path: '/projects', element: <Projects /> },

      // Areas, Resources, Ideas and Goals lived here as stubs while the
      // information architecture was being tested by clicking it. Removed once
      // this became a daily tool: Ideas and Resources are already covered by a
      // capture's `kind`, and Areas and Goals had no owner and no data.
      { path: '*', element: <Placeholder title="Not found" phase="No such section" /> },
    ],
  },
]);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
