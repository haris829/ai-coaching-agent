/**
 * Which identity the test UI is acting as.
 *
 * The merged backend resolves a bearer token to a principal with a role, so the same token works
 * for the question bank and for quiz configuration. This module holds the currently selected
 * token, persists it across reloads, and exposes the development identities the backend reports
 * from `GET /api/session`.
 *
 * This is a **development affordance**, not an authentication implementation: there is no login,
 * because identity is the company's to provide. Everything here is confined to this file and the
 * identity switcher in the top bar.
 */

const STORAGE_KEY = 'quiz-agent.token';

export interface SessionUser {
  id: number;
  displayName: string;
  /**
   * Three roles, not two. `assessor` arrived with UC-09 and is genuinely distinct: an
   * administrator credential is *refused* on the assessor endpoints, because a human review exists
   * so that a named person signs off on a learner's result. Treating it as a flavour of admin here
   * would misrepresent what the backend enforces.
   */
  role: 'admin' | 'learner' | 'assessor';
}

export interface SessionIdentity extends SessionUser {
  email: string;
  token: string;
}

export interface SessionInfo {
  user: SessionUser | null;
  users?: SessionIdentity[];
}

let token: string | null = readStoredToken();

function readStoredToken(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private browsing or a blocked storage API — fall back to in-memory only.
    return null;
  }
}

export function currentToken(): string | null {
  return token;
}

export function setToken(next: string | null): void {
  token = next;
  try {
    if (next) window.localStorage.setItem(STORAGE_KEY, next);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore: the in-memory value still applies for this page.
  }
}

/** Headers every request carries. `X-Admin-User` only matters when no token is selected. */
export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const result: Record<string, string> = { 'X-Admin-User': 'test-ui', ...extra };
  if (token) result.Authorization = `Bearer ${token}`;
  return result;
}
