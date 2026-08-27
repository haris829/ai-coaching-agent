/** Deterministic text utilities shared by retrieval, fingerprinting and the classifier. */

const STOPWORDS = new Set([
  'a','an','the','and','or','but','if','then','than','that','this','these','those','is','are','was','were','be','been',
  'being','am','do','does','did','doing','have','has','had','having','i','you','he','she','it','we','they','me','him',
  'her','us','them','my','your','his','its','our','their','to','of','in','on','at','for','with','about','as','by','from',
  'into','over','under','again','further','so','can','could','would','should','will','shall','may','might','must','not',
  'no','nor','very','just','also','too','there','here','what','which','who','whom','when','where','why','how','please',
  'tell','explain','give','me','some','any','all','more','most','other','such','own','same','because',
]);

export function normalizeText(input: string): string {
  return input
    .toLowerCase()
    .replace(/[‘’“”]/g, "'")
    .replace(/[^a-z0-9'\s-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Lowercased, punctuation-stripped tokens (stopwords kept). */
export function tokenize(input: string): string[] {
  const normalized = normalizeText(input);
  if (!normalized) return [];
  return normalized.split(' ').filter(Boolean);
}

/** Content tokens: stopwords removed, very short tokens dropped, light singularisation. */
export function contentTokens(input: string): string[] {
  return tokenize(input)
    .filter((t) => t.length > 2 && !STOPWORDS.has(t))
    .map(stem);
}

/** Extremely small, deterministic suffix stemmer - enough for lexical overlap scoring. */
export function stem(token: string): string {
  let t = token;
  // Deliberately conservative: no 'er'/'ed' rules, which would mangle words like "answer".
  for (const suffix of ['ations', 'ation', 'ings', 'ing', 'ies', 'es', 's']) {
    if (t.length > suffix.length + 2 && t.endsWith(suffix)) {
      t = t.slice(0, -suffix.length);
      if (suffix === 'ies') t += 'y';
      break;
    }
  }
  return t;
}

export function uniqueTokens(input: string): string[] {
  return Array.from(new Set(contentTokens(input)));
}

export function jaccard(a: readonly string[], b: readonly string[]): number {
  if (a.length === 0 && b.length === 0) return 1;
  const setA = new Set(a);
  const setB = new Set(b);
  let intersection = 0;
  for (const t of setA) if (setB.has(t)) intersection += 1;
  const union = setA.size + setB.size - intersection;
  return union === 0 ? 0 : intersection / union;
}

/** Overlap of `query` tokens covered by `target` tokens, weighted toward rarer long tokens. */
export function coverageScore(queryTokens: readonly string[], targetTokens: readonly string[]): number {
  if (queryTokens.length === 0) return 0;
  const target = new Set(targetTokens);
  let matchedWeight = 0;
  let totalWeight = 0;
  for (const token of new Set(queryTokens)) {
    const weight = 1 + Math.min(token.length, 12) / 12;
    totalWeight += weight;
    if (target.has(token)) matchedWeight += weight;
  }
  return totalWeight === 0 ? 0 : matchedWeight / totalWeight;
}

/** FNV-1a 64-bit (as hex) - stable across processes, no crypto dependency needed. */
export function stableHash(input: string): string {
  let hash = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  const mask = 0xffffffffffffffffn;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= BigInt(input.charCodeAt(i));
    hash = (hash * prime) & mask;
  }
  return hash.toString(16).padStart(16, '0');
}

/** Split prose into sentences, preserving order. */
export function sentences(input: string): string[] {
  return input
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

export function truncateWords(input: string, maxWords: number): string {
  const words = input.split(/\s+/).filter(Boolean);
  if (words.length <= maxWords) return input.trim();
  return `${words.slice(0, maxWords).join(' ')}...`;
}
