import { describe, expect, it } from 'vitest';
import { normalizeLesson } from '../src/core/lesson-normalizer';
import { ProviderError } from '../src/contracts/errors';
import {
  COURSE_DP,
  LESSON_LAWFUL_BASIS,
  LESSON_SUBJECT_RIGHTS,
  MOCK_LESSON_PAYLOADS,
} from '../src/adapters/mock/fixtures';

const baseOptions = {
  courseId: COURSE_DP,
  lessonId: LESSON_LAWFUL_BASIS,
  courseName: 'Data Protection Essentials',
  courseLessonIds: [LESSON_LAWFUL_BASIS, LESSON_SUBJECT_RIGHTS],
  providerName: 'test_provider',
  nowIso: '2026-01-01T00:00:00.000Z',
};

describe('lesson normalization (internal lesson contract)', () => {
  it('normalizes a provider payload into the internal LessonContext', () => {
    const raw = MOCK_LESSON_PAYLOADS[LESSON_LAWFUL_BASIS]!;
    const { lesson } = normalizeLesson(raw, baseOptions);

    expect(lesson.lesson_id).toBe(LESSON_LAWFUL_BASIS);
    expect(lesson.course_id).toBe(COURSE_DP);
    expect(lesson.lesson_title).toBe('Lawful Bases for Processing');
    expect(lesson.sections.map((s) => s.section_id)).toEqual([
      'sec_basis_intro',
      'sec_consent',
      'sec_legitimate_interests',
    ]);
    expect(lesson.concepts.map((c) => c.concept_id)).toContain('concept_balancing_test');
    // Every concept points at a section that exists.
    const sectionIds = new Set(lesson.sections.map((s) => s.section_id));
    for (const concept of lesson.concepts) expect(sectionIds.has(concept.section_id)).toBe(true);
  });

  it('drops a related lesson that is not in the course catalogue', () => {
    const raw = MOCK_LESSON_PAYLOADS[LESSON_LAWFUL_BASIS]!;
    // The fixture deliberately contains a "ghost" related lesson.
    expect(JSON.stringify(raw)).toContain('lesson_dp_ghost');

    const { lesson, warnings } = normalizeLesson(raw, baseOptions);

    expect(lesson.related_lessons.map((r) => r.lesson_id)).toEqual([LESSON_SUBJECT_RIGHTS]);
    expect(warnings).toContain('related_lesson:lesson_dp_ghost:not_in_course_dropped');
  });

  it('keeps only related lessons present in the catalogue when more are known', () => {
    const raw = MOCK_LESSON_PAYLOADS[LESSON_LAWFUL_BASIS]!;
    const { lesson } = normalizeLesson(raw, {
      ...baseOptions,
      courseLessonIds: [LESSON_LAWFUL_BASIS, LESSON_SUBJECT_RIGHTS, 'lesson_dp_03'],
    });
    expect(lesson.related_lessons.map((r) => r.lesson_id).sort()).toEqual(['lesson_dp_02', 'lesson_dp_03']);
    expect(lesson.related_lessons.map((r) => r.lesson_id)).not.toContain('lesson_dp_ghost');
  });

  it('rejects a malformed payload instead of inventing content', () => {
    expect(() => normalizeLesson({ lesson_id: LESSON_LAWFUL_BASIS }, baseOptions)).toThrow(ProviderError);
    expect(() => normalizeLesson({ title: 'No id' }, baseOptions)).toThrow(ProviderError);
    expect(() =>
      normalizeLesson({ lesson_id: 'a_different_lesson', title: 'x' }, baseOptions),
    ).toThrow(/does not match/);
    expect(() =>
      normalizeLesson({ lesson_id: LESSON_LAWFUL_BASIS, title: 'x', course_id: 'other_course' }, baseOptions),
    ).toThrow(/different course/);
  });

  it('degrades gracefully when optional structure is missing', () => {
    const { lesson, warnings } = normalizeLesson(
      { lesson_id: LESSON_LAWFUL_BASIS, title: 'Bare lesson', content: 'Some prose.' },
      baseOptions,
    );
    expect(lesson.sections).toEqual([]);
    expect(lesson.concepts).toEqual([]);
    expect(lesson.related_lessons).toEqual([]);
    expect(warnings).toContain('lesson_has_no_sections');
  });

  it('drops a concept that does not belong to any real section', () => {
    const { lesson, warnings } = normalizeLesson(
      {
        lesson_id: LESSON_LAWFUL_BASIS,
        title: 'Lesson',
        sections: [{ section_id: 'sec_a', title: 'A', body: 'body', concept_ids: ['concept_a'] }],
        concepts: [
          { concept_id: 'concept_a', name: 'A', section_id: 'sec_a', summary: 's' },
          { concept_id: 'concept_orphan', name: 'Orphan', section_id: 'sec_nonexistent', summary: 's' },
        ],
      },
      baseOptions,
    );
    expect(lesson.concepts.map((c) => c.concept_id)).toEqual(['concept_a']);
    expect(warnings).toContain('concept:concept_orphan:dropped_no_valid_section');
  });

  it('unlinks section concept ids that did not survive normalization', () => {
    const { lesson } = normalizeLesson(
      {
        lesson_id: LESSON_LAWFUL_BASIS,
        title: 'Lesson',
        sections: [
          { section_id: 'sec_a', title: 'A', body: 'body', concept_ids: ['concept_a', 'concept_missing'] },
        ],
        concepts: [{ concept_id: 'concept_a', name: 'A', section_id: 'sec_a', summary: 's' }],
      },
      baseOptions,
    );
    expect(lesson.sections[0]!.concept_ids).toEqual(['concept_a']);
  });

  it('ignores a self-referential related lesson', () => {
    const { lesson, warnings } = normalizeLesson(
      {
        lesson_id: LESSON_LAWFUL_BASIS,
        title: 'Lesson',
        related_lessons: [{ lesson_id: LESSON_LAWFUL_BASIS, title: 'Itself' }],
      },
      baseOptions,
    );
    expect(lesson.related_lessons).toEqual([]);
    expect(warnings.some((w) => w.includes('self_reference_dropped'))).toBe(true);
  });
});
