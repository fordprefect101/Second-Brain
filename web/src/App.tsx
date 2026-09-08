import { Outlet } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';

/**
 * Layout shell. A layout route has no path of its own — its children render into
 * <Outlet />, so the sidebar mounts once and survives navigation.
 */
export function App() {
  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
