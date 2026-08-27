/**
 * Provider-facing error taxonomy.
 *
 * Adapters throw these (or plain Errors); UC-04 core catches everything and maps it to an
 * explicit status. No provider failure is allowed to escape the service.
 */
export type ProviderFailureKind =
  | 'NOT_FOUND'
  | 'UNAVAILABLE'
  | 'TIMEOUT'
  | 'MALFORMED'
  | 'FORBIDDEN'
  | 'UNKNOWN';

export class ProviderError extends Error {
  readonly kind: ProviderFailureKind;
  readonly provider: string;

  constructor(kind: ProviderFailureKind, provider: string, message: string) {
    super(message);
    this.name = 'ProviderError';
    this.kind = kind;
    this.provider = provider;
  }
}

export function failureKindOf(err: unknown): ProviderFailureKind {
  if (err instanceof ProviderError) return err.kind;
  return 'UNKNOWN';
}
