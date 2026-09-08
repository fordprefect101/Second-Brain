/**
 * Honest stub. Says what is missing and when it arrives, rather than showing an
 * empty list that looks like a bug or a spinner that never resolves.
 */
export function Placeholder({ title, phase }: { title: string; phase: string }) {
  return (
    <>
      <header className="page-header">
        <h1>{title}</h1>
      </header>
      <div className="empty">
        <p className="empty-title">Not built yet</p>
        <p className="empty-detail">{phase}</p>
      </div>
    </>
  );
}
