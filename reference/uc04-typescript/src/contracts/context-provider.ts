import type { LearnerContext, SessionBinding } from '../domain/lesson-context';

/**
 * PORT: Session & learner context.
 * TODAY  -> MockContextProvider
 * LATER  -> Company session/context service
 *
 * getSessionBinding() is a SECURITY boundary: it is the authoritative answer to
 * "who owns this session and which course/lesson is it linked to".
 */
export interface ContextProvider {
  /** Throw ProviderError('NOT_FOUND') for an unknown session, 'UNAVAILABLE' when down. */
  getSessionBinding(sessionId: string): Promise<SessionBinding>;

  /**
   * Optional learner context. Implementations SHOULD return
   * { available: false, ... } instead of throwing when the data is simply missing.
   */
  getLearnerContext(userId: string): Promise<LearnerContext>;
}
