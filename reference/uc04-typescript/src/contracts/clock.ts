/** PORT: time & id generation, injected so tests stay deterministic. */
export interface Clock {
  nowIso(): string;
}

export interface IdGenerator {
  next(prefix: string): string;
}

export class SystemClock implements Clock {
  nowIso(): string {
    return new Date().toISOString();
  }
}

export class FixedClock implements Clock {
  private tick = 0;
  constructor(private readonly baseMs: number = Date.UTC(2026, 0, 1, 0, 0, 0)) {}
  nowIso(): string {
    // Monotonic, deterministic: one second per call.
    return new Date(this.baseMs + this.tick++ * 1000).toISOString();
  }
}

export class SequentialIdGenerator implements IdGenerator {
  private counters = new Map<string, number>();
  next(prefix: string): string {
    const n = (this.counters.get(prefix) ?? 0) + 1;
    this.counters.set(prefix, n);
    return `${prefix}_${String(n).padStart(6, '0')}`;
  }
}
