import type {
  ActivityEvent,
  ActivityQuery,
  ActivityRepository,
  ExplainedConceptRecord,
} from '../../contracts/activity-repository';
import type {
  ExplanationAttempt,
  ExplanationHistoryStore,
} from '../../contracts/explanation-history-store';
import type { FalsePositiveLog, FalsePositiveRecord } from '../../contracts/false-positive-log';
import { ActivityType } from '../../domain/enums';

/**
 * Development-grade persistence.
 *
 * These are intentionally in-memory: UC-04 is not the owner of the company database. Each
 * class implements a port the company adapter will implement instead. `failNext` /
 * `alwaysFail` exist so tests can prove a persistence outage does not fail a coaching turn.
 */

export class InMemoryActivityRepository implements ActivityRepository {
  private events: ActivityEvent[] = [];
  alwaysFail = false;
  failNext = 0;

  async append(event: ActivityEvent): Promise<void> {
    this.guard();
    this.events.push({ ...event, metadata: { ...event.metadata } });
  }

  async list(query: ActivityQuery): Promise<ActivityEvent[]> {
    this.guard();
    return this.events.filter((e) => matches(e, query)).map((e) => ({ ...e }));
  }

  async listExplainedConcepts(query: ActivityQuery): Promise<ExplainedConceptRecord[]> {
    this.guard();
    const relevant = this.events.filter(
      (e) =>
        matches(e, query) &&
        (e.activity_type === ActivityType.CONCEPT_EXPLAINED ||
          e.activity_type === ActivityType.EXPLAIN_DIFFERENTLY),
    );

    const byKey = new Map<string, ExplainedConceptRecord>();
    for (const event of relevant) {
      const key = [event.user_id, event.session_id, event.course_id, event.lesson_id, event.concept_id ?? event.topic].join('|');
      const existing = byKey.get(key);
      if (existing) {
        existing.explanation_count += 1;
        existing.difficulty_signal_count += event.difficulty_signal ? 1 : 0;
        if (event.timestamp < existing.first_explained_at) existing.first_explained_at = event.timestamp;
        if (event.timestamp > existing.last_explained_at) existing.last_explained_at = event.timestamp;
      } else {
        byKey.set(key, {
          user_id: event.user_id,
          session_id: event.session_id,
          course_id: event.course_id,
          lesson_id: event.lesson_id,
          concept_id: event.concept_id,
          topic: event.topic,
          explanation_count: 1,
          difficulty_signal_count: event.difficulty_signal ? 1 : 0,
          first_explained_at: event.timestamp,
          last_explained_at: event.timestamp,
        });
      }
    }
    return Array.from(byKey.values());
  }

  /** Test helper - not part of the port. */
  reset(): void {
    this.events = [];
    this.alwaysFail = false;
    this.failNext = 0;
  }

  private guard(): void {
    if (this.alwaysFail) throw new Error('activity repository unavailable');
    if (this.failNext > 0) {
      this.failNext -= 1;
      throw new Error('activity repository transient failure');
    }
  }
}

function matches(event: ActivityEvent, query: ActivityQuery): boolean {
  if (query.user_id && event.user_id !== query.user_id) return false;
  if (query.session_id && event.session_id !== query.session_id) return false;
  if (query.course_id && event.course_id !== query.course_id) return false;
  if (query.lesson_id && event.lesson_id !== query.lesson_id) return false;
  if (query.activity_type && event.activity_type !== query.activity_type) return false;
  return true;
}

export class InMemoryExplanationHistoryStore implements ExplanationHistoryStore {
  private attempts = new Map<string, ExplanationAttempt[]>();
  alwaysFail = false;

  async listAttempts(sessionId: string, conceptId: string): Promise<ExplanationAttempt[]> {
    if (this.alwaysFail) throw new Error('explanation history store unavailable');
    return (this.attempts.get(key(sessionId, conceptId)) ?? []).map((a) => ({ ...a }));
  }

  async record(attempt: ExplanationAttempt): Promise<void> {
    if (this.alwaysFail) throw new Error('explanation history store unavailable');
    const k = key(attempt.session_id, attempt.concept_id);
    const list = this.attempts.get(k) ?? [];
    list.push({ ...attempt });
    this.attempts.set(k, list);
  }

  async clearSession(sessionId: string): Promise<void> {
    for (const k of Array.from(this.attempts.keys())) {
      if (k.startsWith(`${sessionId}::`)) this.attempts.delete(k);
    }
  }

  reset(): void {
    this.attempts.clear();
    this.alwaysFail = false;
  }
}

function key(sessionId: string, conceptId: string): string {
  return `${sessionId}::${conceptId}`;
}

export class InMemoryFalsePositiveLog implements FalsePositiveLog {
  private records: FalsePositiveRecord[] = [];
  alwaysFail = false;

  async record(entry: FalsePositiveRecord): Promise<void> {
    if (this.alwaysFail) throw new Error('false positive log unavailable');
    this.records.push({ ...entry });
  }

  async list(sessionId?: string): Promise<FalsePositiveRecord[]> {
    if (this.alwaysFail) throw new Error('false positive log unavailable');
    return this.records
      .filter((r) => (sessionId ? r.session_id === sessionId : true))
      .map((r) => ({ ...r }));
  }

  reset(): void {
    this.records = [];
    this.alwaysFail = false;
  }
}
