/** Evidence files under tests/live/artifacts (gitignored). Everything written passes through redact(). */
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { ARTIFACTS_DIR, redact } from './env.ts';

export function artifactPath(...parts: string[]): string {
  const p = join(ARTIFACTS_DIR, ...parts);
  mkdirSync(dirname(p), { recursive: true });
  return p;
}

export function writeJsonArtifact(name: string, data: unknown): string {
  const p = artifactPath(name);
  writeFileSync(p, `${redact(JSON.stringify(data, null, 2))}\n`, 'utf8');
  return p;
}

export interface StepRecord {
  name: string;
  startedAt: string;
  durationMs: number;
  ok: boolean;
  error?: string;
}

/** Named, timed steps; the record is flushed to the artifact after every step. */
export class StepLog {
  readonly steps: StepRecord[] = [];
  readonly data: Record<string, unknown> = {};
  private readonly startedAt = new Date().toISOString();

  constructor(private readonly file: string) {}

  async step<T>(name: string, fn: () => Promise<T>): Promise<T> {
    const t0 = Date.now();
    const rec: StepRecord = { name, startedAt: new Date(t0).toISOString(), durationMs: 0, ok: false };
    this.steps.push(rec);
    try {
      const out = await fn();
      rec.ok = true;
      return out;
    } catch (err) {
      rec.error = redact((err as Error).message ?? String(err)).slice(0, 2000);
      throw err;
    } finally {
      rec.durationMs = Date.now() - t0;
      this.flush();
    }
  }

  set(key: string, value: unknown): void {
    this.data[key] = value;
  }

  flush(extra: Record<string, unknown> = {}): string {
    return writeJsonArtifact(this.file, {
      startedAt: this.startedAt,
      updatedAt: new Date().toISOString(),
      ok: this.steps.length > 0 && this.steps.every((s) => s.ok),
      steps: this.steps,
      ...this.data,
      ...extra,
    });
  }
}
