import { ProviderError } from '../contracts/errors';
import type { RawLessonPayload } from '../contracts/lesson-content-provider';
import type {
  LessonConcept,
  LessonContext,
  LessonSection,
  RelatedLessonRef,
} from '../domain/lesson-context';

/**
 * The ONLY component that reads provider-shaped lesson JSON.
 *
 * Guarantees:
 *  - a malformed payload raises ProviderError('MALFORMED') instead of producing junk;
 *  - concepts always reference a section that exists;
 *  - related lessons are filtered against the course's real lesson ids (nothing invented);
 *  - missing optional structure (no sections, no related lessons) degrades, it does not throw.
 */
export interface NormalizeOptions {
  courseId: string;
  lessonId: string;
  courseName: string | null;
  /** Real lesson ids in the course. When provided, related lessons are filtered against it. */
  courseLessonIds: string[] | null;
  providerName: string;
  nowIso: string;
}

export interface NormalizationResult {
  lesson: LessonContext;
  /** Non-fatal problems detected while normalizing (dropped refs, missing sections, ...). */
  warnings: string[];
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((v) => asString(v)).filter((v): v is string => v !== null);
}

export function normalizeLesson(
  raw: RawLessonPayload,
  options: NormalizeOptions,
): NormalizationResult {
  const warnings: string[] = [];

  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new ProviderError('MALFORMED', options.providerName, 'Lesson payload is not an object');
  }

  const lessonId = asString(raw['lesson_id']) ?? asString(raw['id']);
  if (!lessonId) {
    throw new ProviderError('MALFORMED', options.providerName, 'Lesson payload has no lesson id');
  }
  if (lessonId !== options.lessonId) {
    throw new ProviderError(
      'MALFORMED',
      options.providerName,
      'Lesson payload id does not match the requested lesson',
    );
  }

  const title = asString(raw['lesson_title']) ?? asString(raw['title']);
  if (!title) {
    throw new ProviderError('MALFORMED', options.providerName, 'Lesson payload has no title');
  }

  const payloadCourseId = asString(raw['course_id']);
  if (payloadCourseId && payloadCourseId !== options.courseId) {
    throw new ProviderError(
      'MALFORMED',
      options.providerName,
      'Lesson payload belongs to a different course than the session binding',
    );
  }

  const sections = normalizeSections(raw['sections'], warnings);
  const sectionIds = new Set(sections.map((s) => s.section_id));
  const concepts = normalizeConcepts(raw['concepts'], sections, sectionIds, warnings);

  // Re-link section.concept_ids so they can only point at concepts that survived normalization.
  const conceptIds = new Set(concepts.map((c) => c.concept_id));
  for (const section of sections) {
    const kept = section.concept_ids.filter((id) => conceptIds.has(id));
    if (kept.length !== section.concept_ids.length) {
      warnings.push('section:' + section.section_id + ':dropped_unknown_concept_ids');
    }
    section.concept_ids = kept;
  }
  for (const concept of concepts) {
    concept.contrasts_with = concept.contrasts_with.filter((id) => conceptIds.has(id));
  }

  const relatedLessons = normalizeRelatedLessons(raw['related_lessons'], options, warnings);

  const lessonContent =
    asString(raw['lesson_content']) ??
    asString(raw['content']) ??
    sections.map((s) => s.title + '\n' + s.content).join('\n\n');

  if (sections.length === 0) warnings.push('lesson_has_no_sections');
  if (concepts.length === 0) warnings.push('lesson_has_no_concepts');
  if (!lessonContent) warnings.push('lesson_has_no_body');

  const lesson: LessonContext = {
    course_id: options.courseId,
    course_name: options.courseName,
    lesson_id: lessonId,
    lesson_title: title,
    lesson_content: lessonContent ?? '',
    sections,
    concepts,
    related_lessons: relatedLessons,
    source: {
      provider: options.providerName,
      revision: asString(raw['revision']),
      normalized_at: options.nowIso,
    },
  };

  return { lesson, warnings };
}

function normalizeSections(value: unknown, warnings: string[]): LessonSection[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) {
    warnings.push('sections_not_an_array');
    return [];
  }
  const sections: LessonSection[] = [];
  value.forEach((entry, index) => {
    if (entry === null || typeof entry !== 'object' || Array.isArray(entry)) {
      warnings.push('section_index_' + index + '_malformed');
      return;
    }
    const record = entry as Record<string, unknown>;
    const id = asString(record['section_id']) ?? asString(record['id']);
    const title = asString(record['title']);
    const content = asString(record['content']) ?? asString(record['body']);
    if (!id || !title) {
      warnings.push('section_index_' + index + '_missing_id_or_title');
      return;
    }
    if (!content) warnings.push('section:' + id + ':missing_content');
    sections.push({
      section_id: id,
      title,
      content: content ?? '',
      key_points: asStringArray(record['key_points']),
      concept_ids: asStringArray(record['concept_ids']),
      order: typeof record['order'] === 'number' ? (record['order'] as number) : index,
    });
  });
  return sections.sort((a, b) => a.order - b.order);
}

function normalizeConcepts(
  value: unknown,
  sections: LessonSection[],
  sectionIds: Set<string>,
  warnings: string[],
): LessonConcept[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) {
    warnings.push('concepts_not_an_array');
    return [];
  }
  const concepts: LessonConcept[] = [];
  const seen = new Set<string>();
  value.forEach((entry, index) => {
    if (entry === null || typeof entry !== 'object' || Array.isArray(entry)) {
      warnings.push('concept_index_' + index + '_malformed');
      return;
    }
    const record = entry as Record<string, unknown>;
    const id = asString(record['concept_id']) ?? asString(record['id']);
    const name = asString(record['name']) ?? asString(record['title']);
    if (!id || !name) {
      warnings.push('concept_index_' + index + '_missing_id_or_name');
      return;
    }
    if (seen.has(id)) {
      warnings.push('concept:' + id + ':duplicate_dropped');
      return;
    }
    let sectionId = asString(record['section_id']);
    if (!sectionId || !sectionIds.has(sectionId)) {
      // A concept must belong to a real section. Fall back to the section that lists it.
      const owner = sections.find((s) => s.concept_ids.includes(id));
      if (owner) {
        sectionId = owner.section_id;
      } else {
        warnings.push('concept:' + id + ':dropped_no_valid_section');
        return;
      }
    }
    seen.add(id);
    concepts.push({
      concept_id: id,
      name,
      summary: asString(record['summary']) ?? asString(record['definition']) ?? '',
      section_id: sectionId,
      keywords: asStringArray(record['keywords']),
      examples: asStringArray(record['examples']),
      analogies: asStringArray(record['analogies']),
      contrasts_with: asStringArray(record['contrasts_with']),
    });
  });
  return concepts;
}

function normalizeRelatedLessons(
  value: unknown,
  options: NormalizeOptions,
  warnings: string[],
): RelatedLessonRef[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) {
    warnings.push('related_lessons_not_an_array');
    return [];
  }
  const refs: RelatedLessonRef[] = [];
  value.forEach((entry, index) => {
    if (entry === null || typeof entry !== 'object' || Array.isArray(entry)) {
      warnings.push('related_lesson_index_' + index + '_malformed');
      return;
    }
    const record = entry as Record<string, unknown>;
    const id = asString(record['lesson_id']) ?? asString(record['id']);
    const title = asString(record['title']);
    if (!id || !title) {
      warnings.push('related_lesson_index_' + index + '_missing_id_or_title');
      return;
    }
    if (id === options.lessonId) {
      warnings.push('related_lesson:' + id + ':self_reference_dropped');
      return;
    }
    // Never surface a lesson the course catalogue does not actually contain.
    if (options.courseLessonIds && !options.courseLessonIds.includes(id)) {
      warnings.push('related_lesson:' + id + ':not_in_course_dropped');
      return;
    }
    refs.push({
      lesson_id: id,
      title,
      relationship: asString(record['relationship']) ?? 'related',
      keywords: asStringArray(record['keywords']),
    });
  });
  return refs;
}
