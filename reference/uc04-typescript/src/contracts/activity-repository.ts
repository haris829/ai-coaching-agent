import type { ActivityType, DifficultySignalType, SourceScope } from '../domain/enums';

/**
 * PORT: Activity / progress events.
 * TODAY  -> InMemoryActivityRepository
 * LATER  -> Company database / event system
 *
 * A logging failure must never fail a coaching turn (see CourseCoachingService).
 */
export interface ActivityEvent {
  event_id: string;
  activity_type: ActivityType;
  user_id: string;
  session_id: string;
  course_id: string | null;
  lesson_id: string | null;
  /** Concept id when the turn was grounded in a lesson concept, else a free-text topic. */
  concept_id: string | null;
  topic: string | null;
  source_scope: SourceScope;
  timestamp: string;
  /** Difficulty SIGNAL, not a diagnosis. */
  difficulty_signal: boolean;
  signal_type: DifficultySignalType | null;
  /** Small, non-sensitive extras (framing used, related lesson id, ...). No lesson prose. */
  metadata: Record<string, string | number | boolean | null>;
}

export interface ActivityQuery {
  user_id?: string;
  session_id?: string;
  course_id?: string;
  lesson_id?: string;
  activity_type?: ActivityType;
}

/** Aggregated view future UCs (e.g. gap tracking) will consume. UC-04 only writes the events. */
export interface ExplainedConceptRecord {
  user_id: string;
  session_id: string;
  course_id: string | null;
  lesson_id: string | null;
  concept_id: string | null;
  topic: string | null;
  explanation_count: number;
  difficulty_signal_count: number;
  first_explained_at: string;
  last_explained_at: string;
}

export interface ActivityRepository {
  append(event: ActivityEvent): Promise<void>;
  list(query: ActivityQuery): Promise<ActivityEvent[]>;
  /** Derived from CONCEPT_EXPLAINED / EXPLAIN_DIFFERENTLY events. */
  listExplainedConcepts(query: ActivityQuery): Promise<ExplainedConceptRecord[]>;
}
