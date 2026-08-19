/**
 * Identity switcher for the test UI.
 *
 * There is no login screen because authentication is the company's to provide. The backend's
 * placeholder directory reports its development identities from `GET /api/session`, and this picker
 * selects which bearer token the API client sends. Switching between the administrator and a
 * learner is what makes the admin and learner halves of UC-01 testable in one browser.
 */

import { useEffect, useState, type ReactNode } from 'react';

import { api } from '../api/client';
import { currentToken, setToken, type SessionIdentity } from '../api/session';

export function IdentitySwitcher(): ReactNode {
  const [identities, setIdentities] = useState<SessionIdentity[]>([]);
  const [selected, setSelected] = useState<string>(currentToken() ?? '');
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const session = await api.session();
        if (cancelled) return;
        const available = session.users ?? [];
        setIdentities(available);
        // Default to the administrator so the configuration screen works on a first visit.
        if (!currentToken() && available.length > 0) {
          const admin = available.find((user) => user.role === 'admin') ?? available[0];
          setToken(admin.token);
          setSelected(admin.token);
        }
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  function choose(token: string): void {
    setToken(token || null);
    setSelected(token);
    // A full reload is the simplest correct way to drop every page's cached data for the old
    // identity. This is a test surface; a production app would lift identity into app state.
    window.location.reload();
  }

  if (failed) {
    return <span className="muted">backend unreachable</span>;
  }

  if (identities.length === 0) {
    return <span className="muted">loading identities…</span>;
  }

  return (
    <label className="row" style={{ gap: 6, alignItems: 'center' }}>
      <span className="visually-hidden">Acting as</span>
      <select
        value={selected}
        onChange={(event) => choose(event.target.value)}
        aria-label="Acting as"
      >
        <option value="">Anonymous</option>
        {identities.map((identity) => (
          <option key={identity.token} value={identity.token}>
            {identity.displayName} ({identity.role})
          </option>
        ))}
      </select>
    </label>
  );
}
