import express, { type Express, type Request, type Response } from 'express';
import type { UC04Container } from '../composition-root';
import { buildUC04 } from '../composition-root';
import { CoachingStatus } from '../domain/enums';
import { ProviderError } from '../contracts/errors';
import { validateCoachingRequest } from './validation';

/**
 * UC-04 HTTP surface. Thin by design: it authenticates, validates, delegates and maps status
 * to an HTTP code. No business logic lives here.
 *
 * Auth is a placeholder: the principal is read from `x-user-id`, standing in for whatever the
 * company gateway injects (JWT subject, session cookie, mTLS identity). Swapping it out does
 * not touch UC-04 core - see INTEGRATION.md.
 */
export const API_BASE = '/api/v1/uc04';

/** Terminal statuses mapped to HTTP codes. Everything else is a 200 with a structured body. */
const STATUS_HTTP_CODES: Partial<Record<CoachingStatus, number>> = {
  [CoachingStatus.SESSION_NOT_FOUND]: 404,
  [CoachingStatus.SESSION_FORBIDDEN]: 403,
  [CoachingStatus.ENROLLMENT_REQUIRED]: 403,
  [CoachingStatus.COURSE_NOT_FOUND]: 404,
  [CoachingStatus.ENROLLMENT_UNVERIFIED]: 503,
  [CoachingStatus.CONTEXT_UNAVAILABLE]: 503,
};

export function createServer(container: UC04Container = buildUC04()): Express {
  const app = express();
  app.use(express.json({ limit: '64kb' }));

  app.get(`${API_BASE}/health`, (_req: Request, res: Response) => {
    res.json({ status: 'ok', use_case: 'UC-04 Course Content Coaching' });
  });

  /** Main coaching turn. */
  app.post(`${API_BASE}/coaching/turns`, async (req: Request, res: Response) => {
    const principal = headerValue(req, 'x-user-id');
    const validation = validateCoachingRequest(principal, req.body);
    if (!validation.ok) {
      res.status(400).json({ error: validation.error, field: validation.field });
      return;
    }

    try {
      const result = await container.service.handleTurn(validation.value);
      const code = STATUS_HTTP_CODES[result.status] ?? 200;
      res.status(code).json(
        validation.ignoredFields.length > 0
          ? { ...result, ignored_request_fields: validation.ignoredFields }
          : result,
      );
    } catch (error) {
      // UC-04 core is not supposed to throw; if it ever does, fail safe rather than leak.
      res.status(500).json({ error: 'Coaching turn failed', status: 'INTERNAL_ERROR' });
    }
  });

  /**
   * Explained-concept activity for a session. Access-controlled: a user can only read the
   * activity of a session they own. Exposed for future UCs (e.g. gap tracking).
   */
  app.get(`${API_BASE}/coaching/sessions/:sessionId/explained-concepts`, async (req: Request, res: Response) => {
    const principal = headerValue(req, 'x-user-id');
    if (!principal) {
      res.status(401).json({ error: 'Authenticated principal is required' });
      return;
    }
    const sessionId = req.params['sessionId'];
    if (!sessionId) {
      res.status(400).json({ error: 'sessionId is required' });
      return;
    }
    try {
      const concepts = await container.service.listExplainedConcepts(principal, sessionId);
      res.json({ session_id: sessionId, explained_concepts: concepts });
    } catch (error) {
      if (error instanceof ProviderError && error.kind === 'FORBIDDEN') {
        res.status(403).json({ error: 'You do not have access to this coaching session.' });
        return;
      }
      if (error instanceof ProviderError && error.kind === 'NOT_FOUND') {
        res.status(404).json({ error: 'That coaching session does not exist.' });
        return;
      }
      res.status(503).json({ error: 'Session activity is temporarily unavailable.' });
    }
  });

  return app;
}

function headerValue(req: Request, name: string): string | undefined {
  const value = req.headers[name];
  if (Array.isArray(value)) return value[0];
  return typeof value === 'string' ? value : undefined;
}
