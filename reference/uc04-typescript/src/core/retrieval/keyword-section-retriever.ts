import type {
  LessonMatch,
  MatchAnchor,
  RelatedLessonMatch,
  RetrievalResult,
  SectionRetriever,
} from '../../contracts/section-retriever';
import type { LessonContext, LessonConcept, LessonSection } from '../../domain/lesson-context';
import { contentTokens, uniqueTokens } from '../text';

/**
 * Deterministic lexical retriever - the default relevance-detection implementation.
 *
 * Intentionally NOT a vector database: UC-04 only has to pick a section/concept out of one
 * supplied lesson, which weighted lexical overlap does reliably and reproducibly.
 *
 * Scoring: each query token is weighted by its INVERSE DOCUMENT FREQUENCY across the lesson's
 * own sections, so a distinctive term ("consent", "balancing") counts far more than a term
 * that appears everywhere ("processing", "lesson"). Query tokens that appear nowhere in the
 * lesson keep a baseline weight, which is what pulls genuinely off-lesson questions below the
 * threshold instead of letting one incidental word match carry them.
 *
 * A production embedding retriever can replace this class by implementing SectionRetriever;
 * nothing else in UC-04 changes.
 *
 * Hard rule: it can only ever return objects taken from the supplied LessonContext.
 */
export interface RetrieverThresholds {
  /** Minimum score for an in-lesson match to count as LESSON scope. */
  lessonThreshold: number;
  /** Minimum score for a related lesson to count as COURSE scope. */
  relatedThreshold: number;
}

export const DEFAULT_THRESHOLDS: RetrieverThresholds = {
  lessonThreshold: 0.35,
  relatedThreshold: 0.35,
};

/** Weight given to a query token that does not occur anywhere in the lesson. */
const OOV_WEIGHT = 1;
/** Extra credit when the hit is on a title / concept name / keyword rather than body prose. */
const SALIENT_FIELD_BOOST = 1.35;

interface DocScore {
  score: number;
  /** How many distinct query tokens the document contains. */
  matchedCount: number;
  /** Whether any of those hits landed on a title, concept name or keyword. */
  salientHit: boolean;
  anchor: MatchAnchor;
}

interface SectionDoc {
  section: LessonSection;
  concepts: LessonConcept[];
  /** All tokens in the section, including its concepts. */
  allTokens: Set<string>;
  /** Tokens from titles, concept names and keywords. */
  salientTokens: Set<string>;
  /** Tokens from titles and concept names only - the strongest evidence of topic. */
  nameTokens: Set<string>;
}

export class KeywordSectionRetriever implements SectionRetriever {
  constructor(private readonly thresholds: RetrieverThresholds = DEFAULT_THRESHOLDS) {}

  retrieve(question: string, lesson: LessonContext): RetrievalResult {
    const queryTokens = uniqueTokens(question);
    if (queryTokens.length === 0 || lesson.sections.length === 0) {
      return {
        bestMatch: null,
        matches: [],
        relatedMatch: this.retrieveRelated(queryTokens, lesson),
      };
    }

    const docs = this.buildDocs(lesson);
    const weights = this.tokenWeights(queryTokens, docs);
    const totalWeight = queryTokens.reduce((sum, t) => sum + (weights.get(t) ?? OOV_WEIGHT), 0);
    if (totalWeight === 0) {
      return { bestMatch: null, matches: [], relatedMatch: this.retrieveRelated(queryTokens, lesson) };
    }

    const matches: LessonMatch[] = [];
    const eligible = new Set<string>();
    for (const doc of docs) {
      const scored = this.scoreDoc(
        queryTokens, weights, totalWeight, doc.allTokens, doc.salientTokens, doc.nameTokens,
      );
      if (scored.score <= 0) continue;
      if (this.isEligible(scored, queryTokens.length)) eligible.add(doc.section.section_id);
      matches.push({
        section: doc.section,
        concept: this.bestConcept(queryTokens, weights, totalWeight, doc),
        score: round(scored.score),
        anchor: scored.anchor,
        matched_tokens: scored.matchedCount,
      });
    }

    matches.sort((a, b) => b.score - a.score || a.section.section_id.localeCompare(b.section.section_id));

    const top = matches.find(
      (m) => m.score >= this.thresholds.lessonThreshold && eligible.has(m.section.section_id),
    );

    return {
      bestMatch: top ?? null,
      matches,
      relatedMatch: this.retrieveRelated(queryTokens, lesson),
    };
  }

  findConcept(conceptId: string, lesson: LessonContext): LessonMatch | null {
    const concept = lesson.concepts.find((c) => c.concept_id === conceptId);
    if (!concept) return null;
    const section = lesson.sections.find((s) => s.section_id === concept.section_id);
    if (!section) return null;
    return { section, concept, score: 1, anchor: 'NAME', matched_tokens: 0 };
  }

  // ------------------------------------------------------------------------------ internals

  private buildDocs(lesson: LessonContext): SectionDoc[] {
    return lesson.sections.map((section) => {
      const concepts = lesson.concepts.filter((c) => c.section_id === section.section_id);
      const nameTokens = new Set<string>([
        ...contentTokens(section.title),
        ...concepts.flatMap((c) => contentTokens(c.name)),
      ]);
      const salient = new Set<string>([
        ...nameTokens,
        ...concepts.flatMap((c) => contentTokens(c.keywords.join(' '))),
      ]);
      const all = new Set<string>([
        ...salient,
        ...contentTokens(section.content),
        ...contentTokens(section.key_points.join(' ')),
        ...concepts.flatMap((c) => contentTokens(c.summary)),
      ]);
      return { section, concepts, allTokens: all, salientTokens: salient, nameTokens };
    });
  }

  /** IDF over the lesson's sections; tokens absent from the whole lesson keep a baseline weight. */
  private tokenWeights(queryTokens: string[], docs: SectionDoc[]): Map<string, number> {
    const n = docs.length;
    const weights = new Map<string, number>();
    for (const token of queryTokens) {
      const df = docs.reduce((count, doc) => count + (doc.allTokens.has(token) ? 1 : 0), 0);
      if (df === 0) {
        weights.set(token, OOV_WEIGHT);
        continue;
      }
      // df = 1 (unique to one section) -> highest weight; df = n (everywhere) -> lowest.
      const idf = Math.log(1 + n / df) / Math.log(1 + n);
      weights.set(token, 0.35 + 1.65 * idf);
    }
    return weights;
  }

  /**
   * Eligibility guards, applied on top of the raw score:
   *
   *  - SALIENT HIT: a query term must land on the section title, a concept name or a concept
   *    keyword. Matching body prose alone is how an unrelated question ("what is the answer to
   *    question 4") latches onto a section that happens to reuse an everyday word.
   *  - SUBSTANCE: one incidental token is not a topic. A single matched token only counts when
   *    it dominates a short question ("explain consent"); otherwise at least two must match.
   */
  private isEligible(scored: DocScore, queryTokenCount: number): boolean {
    if (!scored.salientHit) return false;
    if (scored.matchedCount >= 2) return true;
    return queryTokenCount > 0 && scored.matchedCount / queryTokenCount >= 0.5;
  }

  private scoreDoc(
    queryTokens: string[],
    weights: Map<string, number>,
    totalWeight: number,
    tokens: Set<string>,
    salient: Set<string>,
    names: Set<string> = new Set(),
  ): DocScore {
    let matched = 0;
    let matchedCount = 0;
    let salientHit = false;
    let nameHit = false;
    for (const token of queryTokens) {
      if (!tokens.has(token)) continue;
      matchedCount += 1;
      const weight = weights.get(token) ?? OOV_WEIGHT;
      if (names.has(token)) nameHit = true;
      if (salient.has(token)) {
        salientHit = true;
        matched += weight * SALIENT_FIELD_BOOST;
      } else {
        matched += weight;
      }
    }
    const anchor: MatchAnchor =
      matchedCount === 0 ? 'NONE' : nameHit ? 'NAME' : salientHit ? 'KEYWORD' : 'BODY';
    return { score: Math.min(1, matched / totalWeight), matchedCount, salientHit, anchor };
  }

  private bestConcept(
    queryTokens: string[],
    weights: Map<string, number>,
    totalWeight: number,
    doc: SectionDoc,
  ): LessonConcept | null {
    let best: LessonConcept | null = null;
    let bestScore = 0;
    for (const concept of doc.concepts) {
      const salient = new Set([...contentTokens(concept.name), ...contentTokens(concept.keywords.join(' '))]);
      const all = new Set([...salient, ...contentTokens(concept.summary)]);
      const { score } = this.scoreDoc(queryTokens, weights, totalWeight, all, salient);
      if (score > bestScore || (score === bestScore && best && concept.concept_id < best.concept_id && score > 0)) {
        bestScore = score;
        best = concept;
      }
    }
    // A concept is only named when it actually carries some of the query's weight.
    return bestScore > 0 ? best : (doc.concepts[0] ?? null);
  }

  /** Ranked related lessons. Only ever real refs carried by the normalized lesson. */
  private retrieveRelated(queryTokens: string[], lesson: LessonContext): RelatedLessonMatch | null {
    if (queryTokens.length === 0) return null;
    let best: RelatedLessonMatch | null = null;
    for (const related of lesson.related_lessons) {
      const target = new Set(contentTokens([related.title, related.keywords.join(' ')].join(' ')));
      // Related refs are tiny, so use plain unweighted coverage of the query.
      let matched = 0;
      for (const token of queryTokens) if (target.has(token)) matched += 1;
      const score = round(matched / queryTokens.length);
      if (score < this.thresholds.relatedThreshold) continue;
      if (!best || score > best.score || (score === best.score && related.lesson_id < best.related.lesson_id)) {
        best = { related, score };
      }
    }
    return best;
  }
}

function round(value: number): number {
  return Math.round(value * 10000) / 10000;
}
