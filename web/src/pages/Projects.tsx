import { useEffect, useState } from 'react';
import {
  connectGitHub,
  isRateLimited,
  listActivity,
  listRepos,
  needsToken,
  type Activity,
  type Repository,
} from '../api/github';
import { SourceBadge } from '../components/SourceBadge';
import { ListSkeleton } from '../components/Tile';
import { relativeTime } from '../lib/time';

/**
 * Projects, backed by GitHub.
 *
 * Repositories are sorted by last push, which is a more reliable activity signal
 * than the events feed: GitHub's events endpoint returns PUBLIC events only, so
 * private work is invisible there but still shows up in pushedAt.
 */
export function Projects() {
  const [repos, setRepos] = useState<Repository[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);
  const [connect, setConnect] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [token, setToken] = useState('');
  const [saving, setSaving] = useState(false);

  function load() {
    setLoading(true);
    return Promise.allSettled([listRepos(), listActivity()]).then(([r, a]) => {
      if (r.status === 'fulfilled') {
        setRepos(r.value);
        setConnect(false);
      } else if (needsToken(r.reason)) {
        setConnect(true);
      } else if (isRateLimited(r.reason)) {
        setError(r.reason.message);
      } else {
        setError(r.reason?.message ?? 'Could not load repositories.');
      }
      if (a.status === 'fulfilled') setActivity(a.value);
      setLoading(false);
    });
  }

  useEffect(() => {
    void load();
  }, []);

  async function submitToken(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await connectGitHub(token.trim());
      setToken('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not connect.');
    } finally {
      setSaving(false);
    }
  }

  if (connect) {
    return (
      <>
        <header className="page-header">
          <h1>Projects</h1>
          <p className="page-subtitle">Connect GitHub to see your repositories</p>
        </header>

        <form className="capture" onSubmit={submitToken}>
          <p className="list-item-body" style={{ marginBottom: 10 }}>
            Create a token at <code>github.com/settings/tokens</code> with{' '}
            <strong>repo</strong> scope, then paste it here. It is stored in your
            macOS Keychain, never in a file.
          </p>
          <input
            className="search-input"
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="ghp_… or github_pat_…"
            autoComplete="off"
          />
          <button type="submit" className="button" disabled={!token.trim() || saving}>
            {saving ? 'Checking…' : 'Connect'}
          </button>
          {error && <p className="capture-error">{error}</p>}
        </form>
      </>
    );
  }

  return (
    <>
      <header className="page-header">
        <h1>Projects</h1>
        <p className="page-subtitle">
          {loading ? 'Loading…' : `${repos.length} repositories`}
        </p>
      </header>

      {error && <p className="banner is-error">{error}</p>}

      {loading && <ListSkeleton rows={5} />}

      <ul className="list">
        {repos.map((repo) => (
          <li key={repo.id} className="list-item" data-source="github">
            <div className="list-item-meta">
              <SourceBadge source="github" />
              {repo.private && <span className="kind">private</span>}
              {repo.language && <span>{repo.language}</span>}
              <time dateTime={repo.pushedAt}>{relativeTime(repo.pushedAt)}</time>
            </div>
            <h3 className="list-item-title">
              {repo.url ? (
                <a className="plain-link" href={repo.url} target="_blank" rel="noreferrer">
                  {repo.name}
                </a>
              ) : (
                repo.name
              )}
            </h3>
            {repo.description && <p className="list-item-body">{repo.description}</p>}
          </li>
        ))}
      </ul>

      {activity.length > 0 && (
        <>
          <h2 className="section-heading">Recent activity</h2>
          <ul className="list">
            {activity.slice(0, 10).map((event) => (
              <li key={event.id} className="list-item">
                <div className="list-item-meta">
                  <span className="kind">{event.kind}</span>
                  <span>{event.repository}</span>
                  <time dateTime={event.occurredAt}>{relativeTime(event.occurredAt)}</time>
                </div>
                <p className="list-item-body">{event.summary}</p>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
