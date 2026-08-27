import { describe, expect, it } from 'vitest';
import { KeywordSectionRetriever } from '../src/core/retrieval/keyword-section-retriever';
import { normalizeLesson } from '../src/core/lesson-normalizer';
import {
  COURSE_DP,
  LESSON_LAWFUL_BASIS,
  LESSON_SUBJECT_RIGHTS,
  MOCK_LESSON_PAYLOADS,
} from '../src/adapters/mock/fixtures';

function lesson() {
  return normalizeLesson(MOCK_LESSON_PAYLOADS[LESSON_LAWFUL_BASIS]!, {
    courseId: COURSE_DP,
    lessonId: LESSON_LAWFUL_BASIS,
    courseName: 'Data Protection Essentials',
    courseLessonIds: [LESSON_LAWFUL_BASIS, LESSON_SUBJECT_RIGHTS],
    providerName: 'test',
    nowIso: '2026-01-01T00:00:00.000Z',
  }).lesson;
}

describe('section and concept retrieval', () => {
  const retriever = new KeywordSectionRetriever();

  it('identifies the section that actually covers the question', () => {
    const result = retriever.retrieve('What does consent actually mean in this lesson?', lesson());
    expect(result.bestMatch?.section.section_id).toBe('sec_consent');
    expect(result.bestMatch?.concept?.concept_id).toBe('concept_consent');
    expect(result.bestMatch!.score).toBeGreaterThan(0.35);
  });

  it('picks the right concept when one section teaches several', () => {
    const result = retriever.retrieve('How does the balancing test work?', lesson());
    expect(result.bestMatch?.section.section_id).toBe('sec_legitimate_interests');
    expect(result.bestMatch?.concept?.concept_id).toBe('concept_balancing_test');
  });

  it('returns no in-lesson match for an off-lesson question', () => {
    const result = retriever.retrieve('How do I bake sourdough bread at home?', lesson());
    expect(result.bestMatch).toBeNull();
    expect(result.relatedMatch).toBeNull();
  });

  it('does not latch onto a section that merely reuses an everyday word', () => {
    // "answer" appears in the consent section's prose, but the question is not about consent.
    const result = retriever.retrieve('What is the answer to question 4?', lesson());
    expect(result.bestMatch).toBeNull();
  });

  it('matches a real related lesson from the same course', () => {
    const result = retriever.retrieve('What is a subject access request?', lesson());
    expect(result.bestMatch).toBeNull();
    expect(result.relatedMatch?.related.lesson_id).toBe(LESSON_SUBJECT_RIGHTS);
  });

  it('only ever returns sections and concepts present in the supplied lesson', () => {
    const l = lesson();
    const sectionIds = new Set(l.sections.map((s) => s.section_id));
    const conceptIds = new Set(l.concepts.map((c) => c.concept_id));
    const relatedIds = new Set(l.related_lessons.map((r) => r.lesson_id));

    for (const question of [
      'consent withdrawal',
      'balancing test necessity',
      'lawful basis purpose',
      'subject access request',
      'nothing at all like this lesson',
    ]) {
      const result = retriever.retrieve(question, l);
      for (const match of result.matches) {
        expect(sectionIds.has(match.section.section_id)).toBe(true);
        if (match.concept) expect(conceptIds.has(match.concept.concept_id)).toBe(true);
      }
      if (result.relatedMatch) expect(relatedIds.has(result.relatedMatch.related.lesson_id)).toBe(true);
    }
  });

  it('finds a concept by id and refuses an unknown one', () => {
    const l = lesson();
    expect(retriever.findConcept('concept_consent', l)?.section.section_id).toBe('sec_consent');
    expect(retriever.findConcept('concept_does_not_exist', l)).toBeNull();
  });

  it('handles a lesson with no sections without throwing', () => {
    const bare = { ...lesson(), sections: [], concepts: [] };
    const result = retriever.retrieve('anything', bare);
    expect(result.bestMatch).toBeNull();
    expect(result.matches).toEqual([]);
  });

  it('is deterministic across repeated calls', () => {
    const l = lesson();
    const a = retriever.retrieve('How is consent withdrawn?', l);
    const b = retriever.retrieve('How is consent withdrawn?', l);
    expect(a.bestMatch?.section.section_id).toBe(b.bestMatch?.section.section_id);
    expect(a.bestMatch?.score).toBe(b.bestMatch?.score);
  });
});
