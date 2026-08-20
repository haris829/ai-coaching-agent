/**
 * Which role the selected identity has, according to the backend.
 *
 * The identity switcher chooses a bearer token; it does not know what that token *is*. Three of the
 * newer screens need to, because UC-08's grant panel, UC-09's assessor queue and UC-10's dashboard
 * are each meant for exactly one role.
 *
 * The role is read from `GET /api/session` rather than inferred from the token, for the same reason
 * every other decision in this UI defers to the backend: the server resolves credentials, and a
 * client that decided for itself which token was an administrator's would be a second, divergent
 * implementation of authorization.
 *
 * **This is presentation only.** Hiding a panel is a courtesy, not a control — every endpoint
 * behind these screens enforces the role itself and refuses the wrong credential with 403. If this
 * hook returned the wrong answer, the UI would look odd and nothing would become permitted.
 */

import { useEffect, useState } from 'react';

import { api } from '../api/client';
import type { SessionUser } from '../api/session';

export type Role = SessionUser['role'];

export interface RoleState {
  role: Role | null;
  user: SessionUser | null;
  loading: boolean;
}

export function useRole(): RoleState {
  const [state, setState] = useState<RoleState>({ role: null, user: null, loading: true });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const session = await api.session();
        if (cancelled) return;
        setState({ role: session.user?.role ?? null, user: session.user ?? null, loading: false });
      } catch {
        // An unreachable backend is not a role. Reporting "no role" hides the role-specific
        // panels, which is the safe direction: the alternative is rendering an administrator
        // panel whose every action will fail.
        if (!cancelled) setState({ role: null, user: null, loading: false });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
