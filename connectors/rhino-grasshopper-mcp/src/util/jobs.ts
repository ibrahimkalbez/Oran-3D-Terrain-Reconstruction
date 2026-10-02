import { randomUUID } from "node:crypto";

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Job<R = unknown> {
  id: string;
  kind: string;
  description: string;
  status: JobStatus;
  progress: { done: number; total: number; message?: string };
  created_at: string;
  started_at?: string;
  finished_at?: string;
  result?: R;
  error?: string;
  log: string[];
  cancelRequested: boolean;
}

export interface JobHandle {
  job: Job;
  update(done: number, message?: string): void;
  log(line: string): void;
  /** Throws when the user cancelled the job; call between steps. */
  checkCancelled(): void;
}

class CancelledError extends Error {}

/**
 * Runs long operations (variant series, simulations) in the background, one at a time,
 * because Rhino executes requests sequentially anyway. Tools return a job id and Claude
 * polls job_status; no tool call has to stay open for minutes.
 */
export class JobManager {
  private readonly jobs = new Map<string, Job>();
  private chain: Promise<void> = Promise.resolve();
  private readonly waiters = new Map<string, Array<() => void>>();

  start<R>(kind: string, description: string, total: number, run: (h: JobHandle) => Promise<R>): Job<R> {
    const job: Job<R> = {
      id: `job-${randomUUID().slice(0, 8)}`,
      kind,
      description,
      status: "queued",
      progress: { done: 0, total },
      created_at: new Date().toISOString(),
      log: [],
      cancelRequested: false,
    };
    this.jobs.set(job.id, job as Job);
    const handle: JobHandle = {
      job: job as Job,
      update: (done, message) => {
        job.progress = { done, total: job.progress.total, message };
      },
      log: (line) => {
        job.log.push(`${new Date().toISOString().slice(11, 19)} ${line}`);
        if (job.log.length > 200) job.log.splice(0, job.log.length - 200);
      },
      checkCancelled: () => {
        if (job.cancelRequested) throw new CancelledError("Cancelled by the user.");
      },
    };
    this.chain = this.chain.then(async () => {
      if (job.cancelRequested) {
        job.status = "cancelled";
        job.finished_at = new Date().toISOString();
        this.notify(job.id);
        return;
      }
      job.status = "running";
      job.started_at = new Date().toISOString();
      try {
        job.result = await run(handle);
        job.status = "done";
      } catch (err) {
        job.status = err instanceof CancelledError ? "cancelled" : "failed";
        job.error = (err as Error).message;
      } finally {
        job.finished_at = new Date().toISOString();
        this.notify(job.id);
      }
    });
    return job;
  }

  get(id: string): Job | undefined {
    return this.jobs.get(id);
  }

  list(): Job[] {
    return [...this.jobs.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }

  cancel(id: string): Job {
    const job = this.jobs.get(id);
    if (!job) throw new Error(`Unknown job '${id}'.`);
    job.cancelRequested = true;
    return job;
  }

  /** Resolves when the job finishes or after `ms`, whichever comes first. */
  async waitFor(id: string, ms: number): Promise<Job> {
    const job = this.jobs.get(id);
    if (!job) throw new Error(`Unknown job '${id}'.`);
    if (isFinished(job) || ms <= 0) return job;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, ms);
      const list = this.waiters.get(id) ?? [];
      list.push(() => {
        clearTimeout(timer);
        resolve();
      });
      this.waiters.set(id, list);
    });
    return job;
  }

  private notify(id: string) {
    for (const fn of this.waiters.get(id) ?? []) fn();
    this.waiters.delete(id);
  }
}

export function isFinished(job: Job): boolean {
  return job.status === "done" || job.status === "failed" || job.status === "cancelled";
}

/** Job as shown to Claude (without internal fields). */
export function jobView(job: Job, includeResult = true) {
  const { cancelRequested, result, ...rest } = job;
  return includeResult && isFinished(job) ? { ...rest, result } : { ...rest, log: job.log.slice(-10) };
}
