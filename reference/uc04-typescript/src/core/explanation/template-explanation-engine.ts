import type {
  ExplanationEngine,
  ExplanationRequest,
  GeneratedExplanation,
} from '../../contracts/explanation-engine';
import { ExplanationLevel, FramingType, SourceScope } from '../../domain/enums';
import type { LessonConcept, LessonSection } from '../../domain/lesson-context';
import { sentences, stableHash, truncateWords, uniqueTokens } from '../text';

/**
 * Deterministic, lesson-grounded explanation engine.
 *
 * This is the DEFAULT implementation of the ExplanationEngine port. It is intentionally
 * template-based rather than LLM-backed so that UC-04 can be developed and tested
 * independently and reproducibly. An LLM engine implements the same interface later; the
 * framing choice, duplicate rejection, scoping and protection logic all stay in UC-04 core.
 *
 * Grounding rules enforced here:
 *  - LESSON / COURSE answers are assembled only from the supplied section + concept text.
 *  - Material that is NOT in the lesson (a generic analogy, an illustrative scenario) is
 *    always explicitly labelled as not coming from the lesson.
 *  - GENERAL answers never claim lesson provenance.
 *  - Only a bounded slice of the lesson is used - the full lesson body is never reproduced.
 */
export class TemplateExplanationEngine implements ExplanationEngine {
  async explain(request: ExplanationRequest): Promise<GeneratedExplanation> {
    if (request.sourceScope === SourceScope.GENERAL || !request.section) {
      return {
        text: this.generalAnswer(request),
        framing: request.framing,
        groundedSectionIds: [],
      };
    }

    const section = request.section;
    const concept = request.concept;
    const body = this.framedBody(request, section, concept);
    const parts = [this.attribution(request, section, concept), body, this.levelNote(request)];

    return {
      text: parts.filter((p) => p.length > 0).join('\n\n'),
      framing: request.framing,
      groundedSectionIds: [section.section_id],
    };
  }

  // ---------------------------------------------------------------- attribution & framing

  private attribution(
    request: ExplanationRequest,
    section: LessonSection,
    concept: LessonConcept | null,
  ): string {
    const lessonTitle = request.lesson?.lesson_title ?? 'the linked lesson';
    const subject = concept ? concept.name : section.title;
    if (request.sourceScope === SourceScope.COURSE && request.relatedLesson) {
      return `${subject} — drawing on "${section.title}" in "${lessonTitle}", and connected to the related lesson "${request.relatedLesson.title}" in this course.`;
    }
    return `${subject} — from the section "${section.title}" of "${lessonTitle}".`;
  }

  private framedBody(
    request: ExplanationRequest,
    section: LessonSection,
    concept: LessonConcept | null,
  ): string {
    switch (request.framing) {
      case FramingType.DIRECT:
        return this.direct(request, section, concept);
      case FramingType.ANALOGY:
        return this.analogy(request, section, concept);
      case FramingType.PRACTICAL_EXAMPLE:
        return this.practicalExample(request, section, concept);
      case FramingType.STEP_BY_STEP:
        return this.stepByStep(request, section, concept);
      case FramingType.CONTRAST:
        return this.contrast(request, section, concept);
      case FramingType.SCENARIO:
        return this.scenario(request, section, concept);
      default:
        return this.direct(request, section, concept);
    }
  }

  // ---------------------------------------------------------------------------- framings

  private direct(
    request: ExplanationRequest,
    section: LessonSection,
    concept: LessonConcept | null,
  ): string {
    const lead = this.rotate(
      ['Put plainly', 'The straight answer', 'Stated directly'],
      request.variantSeed,
    );
    const definition = this.definition(section, concept, request.variantSeed);
    const points = this.materialPoints(section, concept, request.variantSeed, 3);
    const pointBlock = points.length
      ? '\nThe lesson anchors it on:\n' + points.map((p) => `- ${p}`).join('\n')
      : '';
    return `${lead}: ${definition}${pointBlock}`;
  }

  private analogy(
    request: ExplanationRequest,
    section: LessonSection,
    concept: LessonConcept | null,
  ): string {
    const lessonAnalogy = concept ? this.rotatePick(concept.analogies, request.variantSeed) : null;
    const definition = this.definition(section, concept, request.variantSeed + 1);
    if (lessonAnalogy) {
      return `An analogy the lesson itself uses: ${lessonAnalogy}\n\nMapping that back: ${definition}`;
    }
    const subject = concept ? concept.name : section.title;
    const generic = this.genericAnalogy(subject, request.variantSeed);
    return `The lesson does not give an analogy for this, so here is one of mine — it is an illustration, not lesson content: ${generic}\n\nWhat the lesson actually says: ${definition}`;
  }

  private practicalExample(
    request: ExplanationRequest,
    section: LessonSection,
    concept: LessonConcept | null,
  ): string {
    const example = concept ? this.rotatePick(concept.examples, request.variantSeed) : null;
    const definition = this.definition(section, concept, request.variantSeed + 2);
    if (example) {
      return `A worked example from the lesson: ${example}\n\nWhy it works: ${definition}`;
    }
    const points = this.materialPoints(section, concept, request.variantSeed + 2, 2);
    const applied = points.length
      ? points.map((p, i) => `Applying it, step ${i + 1}: ${p}`).join('\n')
      : `Applying it means working straight from the definition: ${definition}`;
    return `This lesson does not carry a worked example for this concept, so here is the lesson's own material applied concretely (the framing is mine, the substance is the lesson's):\n${applied}`;
  }

  private stepByStep(
    request: ExplanationRequest,
    section: LessonSection,
    concept: LessonConcept | null,
  ): string {
    const steps = this.materialPoints(section, concept, request.variantSeed, 4);
    if (steps.length === 0) {
      return `Broken down: ${this.definition(section, concept, request.variantSeed + 3)}`;
    }
    const numbered = steps.map((s, i) => `${i + 1}. ${s}`).join('\n');
    return `Taken one step at a time, as the lesson lays it out:\n${numbered}\n\nHold those together and you have the whole idea.`;
  }

  private contrast(
    request: ExplanationRequest,
    section: LessonSection,
    concept: LessonConcept | null,
  ): string {
    const other = this.contrastTarget(request, concept);
    const definition = this.definition(section, concept, request.variantSeed + 4);
    if (other) {
      const subject = concept ? concept.name : section.title;
      return `It helps to set this against something next to it in the same lesson.\n- ${subject}: ${definition}\n- ${other.name}: ${this.trim(other.summary || 'covered separately in this lesson')}\nThe distinction is what keeps the two from collapsing into one idea.`;
    }
    const points = this.materialPoints(section, concept, request.variantSeed + 4, 2);
    if (points.length >= 2) {
      return `Contrasting the two halves of what the lesson says:\n- On one side: ${points[0]}\n- On the other: ${points[1]}\nThe concept is what holds both together: ${definition}`;
    }
    return `Contrasted with the loose everyday use of the term, the lesson is specific: ${definition}`;
  }

  private scenario(
    request: ExplanationRequest,
    section: LessonSection,
    concept: LessonConcept | null,
  ): string {
    const definition = this.definition(section, concept, request.variantSeed + 5);
    const point = this.materialPoints(section, concept, request.variantSeed + 5, 1)[0];
    const course = request.courseName ?? request.lesson?.course_name ?? 'this course';
    const situation = point
      ? `you are partway through ${course} and you hit exactly the situation this section describes: ${point}`
      : `you are partway through ${course} and this section's material is what you have to act on`;
    return `Picture the situation (the scene is mine, the substance is the lesson's): ${situation}\n\nWhat you would lean on is this: ${definition}`;
  }

  // ------------------------------------------------------------------------- GENERAL scope

  private generalAnswer(request: ExplanationRequest): string {
    const topic = this.topicPhrase(request.question);
    const notice = request.lesson
      ? 'This is not covered by the linked lesson, so I am answering from general knowledge rather than lesson content.'
      : 'The linked lesson content is not available to me right now, so I am answering from general knowledge rather than lesson content.';
    // Name the course without implying the question belongs to it.
    const courseLine = request.courseName
      ? ` Your linked course is ${request.courseName}; this question sits outside its material.`
      : '';
    const framed = this.generalFraming(request.framing, topic, request.variantSeed);
    return `${notice}${courseLine}\n\n${framed}\n\nIf you want to go deeper on this than the lesson allows, we can move into a free-form session.`;
  }

  private generalFraming(framing: FramingType, topic: string, seed: number): string {
    switch (framing) {
      case FramingType.STEP_BY_STEP:
        return `Working through ${topic} in order: start from what the term names, then what it is for, then where it shows up in practice, then the mistakes people usually make with it.`;
      case FramingType.ANALOGY:
        return `By analogy, ${topic} behaves like ${this.genericAnalogy(topic, seed)}`;
      case FramingType.PRACTICAL_EXAMPLE:
        return `In practice, ${topic} tends to show up when you have to make a concrete decision and need a rule to decide by rather than an opinion.`;
      case FramingType.CONTRAST:
        return `${this.capitalize(topic)} is most easily understood by what it is not: it is not a synonym for the nearest everyday word people reach for, and the difference is usually where the value is.`;
      case FramingType.SCENARIO:
        return `Imagine you had to act on ${topic} tomorrow with no one to ask - the first thing you would need is a working definition, and the second is a way to check yourself.`;
      case FramingType.DIRECT:
      default:
        return `On ${topic}: the broader topic is well defined outside this lesson, and I can walk you through it at whatever depth you want.`;
    }
  }

  // ------------------------------------------------------------------------------ helpers

  /** The concept definition, or a bounded slice of the section prose. Never the whole lesson. */
  private definition(
    section: LessonSection,
    concept: LessonConcept | null,
    seed: number,
  ): string {
    if (concept && concept.summary) return this.trim(concept.summary);
    const bodySentences = sentences(section.content);
    if (bodySentences.length === 0) {
      return this.trim(section.title);
    }
    const start = bodySentences.length > 0 ? Math.abs(seed) % bodySentences.length : 0;
    const picked = [bodySentences[start], bodySentences[(start + 1) % bodySentences.length]]
      .filter((s): s is string => Boolean(s))
      .filter((s, i, arr) => arr.indexOf(s) === i);
    return this.trim(picked.join(' '));
  }

  /**
   * Bounded grounding material: the section's key points when supplied, otherwise sentences
   * from the section body. Rotated by `seed` so a retry genuinely selects different material.
   */
  private materialPoints(
    section: LessonSection,
    concept: LessonConcept | null,
    seed: number,
    limit: number,
  ): string[] {
    const pool =
      section.key_points.length > 0 ? [...section.key_points] : sentences(section.content);
    if (concept && concept.summary && pool.length === 0) return [this.trim(concept.summary)];
    if (pool.length === 0) return [];
    const offset = Math.abs(seed) % pool.length;
    const rotated = [...pool.slice(offset), ...pool.slice(0, offset)];
    return rotated.slice(0, Math.min(limit, rotated.length)).map((p) => this.trim(p));
  }

  /** A real, supplied concept to contrast against - never an invented one. */
  private contrastTarget(
    request: ExplanationRequest,
    concept: LessonConcept | null,
  ): LessonConcept | null {
    const lesson = request.lesson;
    if (!lesson || !concept) return null;
    for (const id of concept.contrasts_with) {
      const found = lesson.concepts.find((c) => c.concept_id === id);
      if (found) return found;
    }
    const sibling = lesson.concepts.find((c) => c.concept_id !== concept.concept_id);
    return sibling ?? null;
  }

  private genericAnalogy(subject: string, seed: number): string {
    const pool = [
      `a checklist you run before acting, so the same decision does not have to be re-argued each time`,
      `a map legend: it does not move the territory, it tells you how to read it`,
      `the rules of a game - dull to read on their own, but nothing makes sense without them`,
      `a recipe's method section: the ingredients matter, but the order is what makes it work`,
    ];
    const index = (Math.abs(seed) + Number.parseInt(stableHash(subject).slice(0, 4), 16)) % pool.length;
    return pool[index] as string;
  }

  private topicPhrase(question: string): string {
    const tokens = uniqueTokens(question).slice(0, 6);
    if (tokens.length === 0) return 'this topic';
    return tokens.join(' ');
  }

  private levelNote(request: ExplanationRequest): string {
    switch (request.explanationLevel) {
      case ExplanationLevel.BEGINNER:
        return 'If any term there is unfamiliar, say which one and I will unpack that first before we go further.';
      case ExplanationLevel.ADVANCED:
        return 'I have kept this at the level the lesson pitches it; say the word if you want the edge cases and caveats too.';
      case ExplanationLevel.INTERMEDIATE:
        return 'Tell me if you want this tightened up or opened out.';
      default:
        return '';
    }
  }

  private rotate(pool: string[], seed: number): string {
    return pool[Math.abs(seed) % pool.length] as string;
  }

  private rotatePick(pool: string[], seed: number): string | null {
    if (pool.length === 0) return null;
    return pool[Math.abs(seed) % pool.length] as string;
  }

  private trim(text: string): string {
    return truncateWords(text.trim(), 60);
  }

  private capitalize(text: string): string {
    return text.length === 0 ? text : text[0]!.toUpperCase() + text.slice(1);
  }
}
