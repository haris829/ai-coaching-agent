import type { RawLessonPayload } from '../../contracts/lesson-content-provider';
import type { SessionBinding } from '../../domain/lesson-context';
import { ExplanationLevel } from '../../domain/enums';

/**
 * Deterministic mock data for UC-04.
 *
 * These payloads are deliberately shaped like a THIRD-PARTY content API response (snake_case,
 * optional fields, a related lesson that does not exist in the catalogue) so the normalizer
 * and the "never invent a lesson" rule are exercised for real.
 */

export const COURSE_DP = 'course_dp_101';
export const COURSE_EMPTY = 'course_empty_900';
export const COURSE_MISSING = 'course_does_not_exist';

export const LESSON_LAWFUL_BASIS = 'lesson_dp_01';
export const LESSON_SUBJECT_RIGHTS = 'lesson_dp_02';
export const LESSON_BREACH = 'lesson_dp_03';
export const LESSON_MALFORMED = 'lesson_dp_malformed';
export const LESSON_UNAVAILABLE = 'lesson_dp_unavailable';
export const LESSON_TIMEOUT = 'lesson_dp_timeout';
export const LESSON_MISSING = 'lesson_dp_missing';
export const LESSON_NO_SECTIONS = 'lesson_dp_bare';

export const USER_ENROLLED = 'user_learner_1';
export const USER_ENROLLED_ADVANCED = 'user_learner_2';
export const USER_NOT_ENROLLED = 'user_outsider';
export const USER_NO_CONTEXT = 'user_no_context';

export const SESSION_MAIN = 'sess_main_1';
export const SESSION_SECOND = 'sess_second_2';
export const SESSION_NOT_ENROLLED = 'sess_not_enrolled';
export const SESSION_UNAVAILABLE_LESSON = 'sess_lesson_down';
export const SESSION_MALFORMED_LESSON = 'sess_lesson_malformed';
export const SESSION_MISSING_LESSON = 'sess_lesson_missing';
export const SESSION_TIMEOUT_LESSON = 'sess_lesson_timeout';
export const SESSION_BARE_LESSON = 'sess_lesson_bare';
export const SESSION_NO_CONTEXT_USER = 'sess_no_context';
export const SESSION_BAD_COURSE = 'sess_bad_course';
export const SESSION_ADVANCED = 'sess_advanced';
export const SESSION_UNKNOWN = 'sess_never_created';

export interface MockCourseRecord {
  course_id: string;
  course_name: string;
  lesson_ids: string[];
}

export const MOCK_COURSES: MockCourseRecord[] = [
  {
    course_id: COURSE_DP,
    course_name: 'Data Protection Essentials',
    lesson_ids: [
      LESSON_LAWFUL_BASIS,
      LESSON_SUBJECT_RIGHTS,
      LESSON_BREACH,
      LESSON_MALFORMED,
      LESSON_UNAVAILABLE,
      LESSON_TIMEOUT,
      LESSON_NO_SECTIONS,
    ],
  },
  {
    course_id: COURSE_EMPTY,
    course_name: 'Course With No Lessons',
    lesson_ids: [],
  },
];

/** The primary lesson used by most tests. */
const LAWFUL_BASIS_PAYLOAD: RawLessonPayload = {
  lesson_id: LESSON_LAWFUL_BASIS,
  course_id: COURSE_DP,
  title: 'Lawful Bases for Processing',
  revision: 'rev-2026-01-14',
  content:
    'Every act of processing personal data needs a lawful basis chosen before the processing starts. This lesson covers what a lawful basis is, how consent works, and when legitimate interests can be relied on instead.',
  sections: [
    {
      section_id: 'sec_basis_intro',
      title: 'What a Lawful Basis Is',
      order: 0,
      body:
        'A lawful basis is the specific justification an organisation records for processing personal data. It must be identified before processing begins and it cannot be swapped afterwards to suit the outcome. Six lawful bases exist and none of them ranks above the others; the right one depends on the purpose of the processing.',
      key_points: [
        'A lawful basis must be chosen before processing starts, not justified afterwards',
        'The basis is tied to a specific purpose, so a new purpose needs its own assessment',
        'No lawful basis outranks another - suitability is what matters',
      ],
      concept_ids: ['concept_lawful_basis'],
    },
    {
      section_id: 'sec_consent',
      title: 'Consent as a Lawful Basis',
      order: 1,
      body:
        'Consent means a freely given, specific, informed and unambiguous indication of the individual wishes. It requires a clear affirmative action, so pre-ticked boxes and silence do not qualify. Consent must be as easy to withdraw as it was to give, and withdrawal ends the processing that relied on it.',
      key_points: [
        'Consent needs a clear affirmative action - silence and pre-ticked boxes do not count',
        'Consent must be as easy to withdraw as it was to give',
        'If processing would continue regardless of the answer, consent is not the honest basis',
      ],
      concept_ids: ['concept_consent'],
    },
    {
      section_id: 'sec_legitimate_interests',
      title: 'Legitimate Interests and the Balancing Test',
      order: 2,
      body:
        'Legitimate interests allows processing where the organisation has a genuine interest, the processing is necessary for it, and the interest is not overridden by the rights of the individual. The three-part balancing test records the purpose, the necessity and the balance. Where the individual would be surprised by the processing, the balance usually fails.',
      key_points: [
        'The balancing test has three parts: purpose, necessity, and the balance of rights',
        'Necessity means there is no less intrusive way of achieving the same purpose',
        'If the individual would be surprised by the processing, the balance usually fails',
      ],
      concept_ids: ['concept_legitimate_interests', 'concept_balancing_test'],
    },
  ],
  concepts: [
    {
      concept_id: 'concept_lawful_basis',
      name: 'Lawful basis',
      section_id: 'sec_basis_intro',
      summary:
        'A lawful basis is the recorded justification for processing personal data, fixed to a specific purpose and chosen before processing begins.',
      keywords: ['lawful', 'basis', 'justification', 'processing', 'purpose', 'six bases'],
      examples: [],
      analogies: [],
      contrasts_with: ['concept_consent'],
    },
    {
      concept_id: 'concept_consent',
      name: 'Consent',
      section_id: 'sec_consent',
      summary:
        'Consent is a freely given, specific, informed and unambiguous indication of wishes, signalled by a clear affirmative action and withdrawable at any time.',
      keywords: ['consent', 'opt in', 'affirmative', 'withdraw', 'freely given', 'unambiguous'],
      examples: [
        'A newsletter sign-up where the subscriber ticks an empty box themselves, and every email carries a one-click unsubscribe that actually stops the mail.',
      ],
      analogies: [
        'Consent works like an invitation to your house: it has to be offered freely, it can be withdrawn, and once it is withdrawn the visit ends.',
      ],
      contrasts_with: ['concept_legitimate_interests'],
    },
    {
      concept_id: 'concept_legitimate_interests',
      name: 'Legitimate interests',
      section_id: 'sec_legitimate_interests',
      summary:
        'Legitimate interests permits processing where a genuine interest exists, the processing is necessary for it, and individual rights do not override it.',
      keywords: ['legitimate', 'interests', 'necessary', 'override', 'rights', 'genuine interest'],
      examples: [
        'Screening a new supplier contact against a fraud database because the business has a real interest in avoiding fraud and no lighter-touch check would do.',
      ],
      analogies: [],
      contrasts_with: ['concept_consent'],
    },
    {
      concept_id: 'concept_balancing_test',
      name: 'Balancing test',
      section_id: 'sec_legitimate_interests',
      summary:
        'The balancing test is the three-part record - purpose, necessity, balance of rights - that must be completed before relying on legitimate interests.',
      keywords: ['balancing', 'test', 'three part', 'purpose', 'necessity', 'balance', 'lia'],
      examples: [],
      analogies: [],
      contrasts_with: [],
    },
  ],
  related_lessons: [
    {
      lesson_id: LESSON_SUBJECT_RIGHTS,
      title: 'Data Subject Rights',
      relationship: 'follow-on lesson',
      keywords: ['rights', 'access request', 'erasure', 'portability', 'object', 'subject access'],
    },
    {
      lesson_id: LESSON_BREACH,
      title: 'Handling a Personal Data Breach',
      relationship: 'later lesson in this course',
      keywords: ['breach', 'notification', '72 hours', 'incident', 'report', 'containment'],
    },
    {
      // NOT in the course catalogue: the normalizer must drop this, proving UC-04 never
      // surfaces a related lesson that does not exist.
      lesson_id: 'lesson_dp_ghost',
      title: 'Ghost Lesson That Does Not Exist',
      relationship: 'phantom',
      keywords: ['ghost', 'phantom'],
    },
  ],
};

const SUBJECT_RIGHTS_PAYLOAD: RawLessonPayload = {
  lesson_id: LESSON_SUBJECT_RIGHTS,
  course_id: COURSE_DP,
  title: 'Data Subject Rights',
  content: 'Individuals hold a set of rights over their personal data, including access, rectification and erasure.',
  sections: [
    {
      section_id: 'sec_access',
      title: 'The Right of Access',
      order: 0,
      body:
        'A subject access request entitles an individual to a copy of their personal data and to information about how it is processed. The response is due within one month, extendable for complex requests.',
      key_points: ['A response is due within one month', 'Identity should be verified before disclosure'],
      concept_ids: ['concept_subject_access'],
    },
  ],
  concepts: [
    {
      concept_id: 'concept_subject_access',
      name: 'Subject access request',
      section_id: 'sec_access',
      summary:
        'A subject access request is a request from an individual for a copy of their personal data and details of its processing.',
      keywords: ['subject access', 'sar', 'copy', 'one month', 'access request'],
      examples: [],
      analogies: [],
      contrasts_with: [],
    },
  ],
  related_lessons: [
    {
      lesson_id: LESSON_LAWFUL_BASIS,
      title: 'Lawful Bases for Processing',
      relationship: 'prerequisite',
      keywords: ['lawful basis', 'consent'],
    },
  ],
};

/** A lesson with a body but no sections/concepts - exercises graceful degradation. */
const BARE_LESSON_PAYLOAD: RawLessonPayload = {
  lesson_id: LESSON_NO_SECTIONS,
  course_id: COURSE_DP,
  title: 'Course Orientation',
  content: 'This short orientation explains how the course is structured and how long each lesson takes.',
};

/** Malformed on purpose: no title at all. */
const MALFORMED_LESSON_PAYLOAD: RawLessonPayload = {
  lesson_id: LESSON_MALFORMED,
  course_id: COURSE_DP,
  sections: 'this should have been an array',
};

export const MOCK_LESSON_PAYLOADS: Record<string, RawLessonPayload> = {
  [LESSON_LAWFUL_BASIS]: LAWFUL_BASIS_PAYLOAD,
  [LESSON_SUBJECT_RIGHTS]: SUBJECT_RIGHTS_PAYLOAD,
  [LESSON_NO_SECTIONS]: BARE_LESSON_PAYLOAD,
  [LESSON_MALFORMED]: MALFORMED_LESSON_PAYLOAD,
};

export interface MockEnrollmentRecord {
  user_id: string;
  course_ids: string[];
}

export const MOCK_ENROLLMENTS: MockEnrollmentRecord[] = [
  // COURSE_MISSING models a stale enrollment: the learner is enrolled, but the course
  // catalogue no longer has the course. Enrollment is verified BEFORE the course lookup, so
  // this is the only way to reach the COURSE_NOT_FOUND path.
  { user_id: USER_ENROLLED, course_ids: [COURSE_DP, COURSE_EMPTY, COURSE_MISSING] },
  { user_id: USER_ENROLLED_ADVANCED, course_ids: [COURSE_DP] },
  { user_id: USER_NO_CONTEXT, course_ids: [COURSE_DP] },
  { user_id: USER_NOT_ENROLLED, course_ids: [] },
];

export const MOCK_SESSIONS: SessionBinding[] = [
  { session_id: SESSION_MAIN, user_id: USER_ENROLLED, course_id: COURSE_DP, lesson_id: LESSON_LAWFUL_BASIS },
  { session_id: SESSION_SECOND, user_id: USER_ENROLLED, course_id: COURSE_DP, lesson_id: LESSON_LAWFUL_BASIS },
  { session_id: SESSION_ADVANCED, user_id: USER_ENROLLED_ADVANCED, course_id: COURSE_DP, lesson_id: LESSON_LAWFUL_BASIS },
  { session_id: SESSION_NOT_ENROLLED, user_id: USER_NOT_ENROLLED, course_id: COURSE_DP, lesson_id: LESSON_LAWFUL_BASIS },
  { session_id: SESSION_UNAVAILABLE_LESSON, user_id: USER_ENROLLED, course_id: COURSE_DP, lesson_id: LESSON_UNAVAILABLE },
  { session_id: SESSION_MALFORMED_LESSON, user_id: USER_ENROLLED, course_id: COURSE_DP, lesson_id: LESSON_MALFORMED },
  { session_id: SESSION_MISSING_LESSON, user_id: USER_ENROLLED, course_id: COURSE_DP, lesson_id: LESSON_MISSING },
  { session_id: SESSION_TIMEOUT_LESSON, user_id: USER_ENROLLED, course_id: COURSE_DP, lesson_id: LESSON_TIMEOUT },
  { session_id: SESSION_BARE_LESSON, user_id: USER_ENROLLED, course_id: COURSE_DP, lesson_id: LESSON_NO_SECTIONS },
  { session_id: SESSION_NO_CONTEXT_USER, user_id: USER_NO_CONTEXT, course_id: COURSE_DP, lesson_id: LESSON_LAWFUL_BASIS },
  { session_id: SESSION_BAD_COURSE, user_id: USER_ENROLLED, course_id: COURSE_MISSING, lesson_id: LESSON_LAWFUL_BASIS },
];

export const MOCK_LEARNER_LEVELS: Record<string, ExplanationLevel> = {
  [USER_ENROLLED]: ExplanationLevel.BEGINNER,
  [USER_ENROLLED_ADVANCED]: ExplanationLevel.ADVANCED,
};
