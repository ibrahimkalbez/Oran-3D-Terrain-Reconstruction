import { rng } from "../geometry/sampling.js";
import { valueAt, type Dimension } from "./doe.js";

export interface Evaluation {
  params: Record<string, unknown>;
  metrics: Record<string, number>;
  feasible: boolean;
  violations: number;
  error?: string;
  /** Simulation cases run for this design. */
  simulations?: string[];
}

export interface OptimizeOptions {
  objectives: Record<string, "max" | "min">;
  weights?: Record<string, number>;
  population?: number;
  generations?: number;
  mutation?: number;
  elite?: number;
  seed?: number;
}

export interface OptimizeResult {
  evaluations: Array<Evaluation & { generation: number; score: number }>;
  best: (Evaluation & { generation: number; score: number }) | undefined;
  history: Array<{ generation: number; best_score: number; feasible: number }>;
}

type Genome = number[]; // one value in [0, 1] per dimension

/**
 * Scores every evaluation against all the others (min-max normalised objectives, weighted),
 * so scores stay comparable as the search explores new ranges.
 */
export function scoreAll(evals: Evaluation[], objectives: Record<string, "max" | "min">, weights: Record<string, number> = {}): number[] {
  const keys = Object.keys(objectives);
  const ranges = keys.map((k) => {
    const v = evals.map((e) => e.metrics[k]).filter((x) => Number.isFinite(x));
    return v.length ? [Math.min(...v), Math.max(...v)] : [0, 0];
  });
  const total = keys.reduce((s, k) => s + (weights[k] ?? 1), 0) || 1;
  return evals.map((e) => {
    let s = 0;
    keys.forEach((k, i) => {
      const [lo, hi] = ranges[i];
      const v = e.metrics[k];
      if (!Number.isFinite(v)) return;
      let n = hi === lo ? 1 : (v - lo) / (hi - lo);
      if (objectives[k] === "min") n = 1 - n;
      s += (weights[k] ?? 1) * n;
    });
    return Math.round((s / total) * 10000) / 10000;
  });
}

/** Feasible first, then fewer violations, then higher score (Deb's constraint handling). */
function better(a: { feasible: boolean; violations: number; score: number }, b: { feasible: boolean; violations: number; score: number }): boolean {
  if (a.feasible !== b.feasible) return a.feasible;
  if (!a.feasible && a.violations !== b.violations) return a.violations < b.violations;
  return a.score > b.score;
}

/**
 * Small genetic algorithm (tournament selection, blend crossover, gaussian mutation, elitism),
 * in the spirit of Galapagos. Each evaluation is a Grasshopper solution, so populations stay small.
 */
export async function optimize(
  dims: Dimension[],
  evaluate: (params: Record<string, unknown>) => Promise<Evaluation>,
  options: OptimizeOptions,
  hooks: { progress?: (done: number, total: number, message: string) => void; checkCancelled?: () => void } = {},
): Promise<OptimizeResult> {
  const pop = Math.max(4, options.population ?? 8);
  const gens = Math.max(1, options.generations ?? 5);
  const mutation = options.mutation ?? 0.2;
  const elite = Math.max(1, Math.min(pop - 1, options.elite ?? 2));
  const random = rng(options.seed ?? 1);
  const cache = new Map<string, Evaluation>();
  const all: Array<Evaluation & { generation: number; score: number }> = [];
  const history: OptimizeResult["history"] = [];
  const total = pop * gens;
  let done = 0;

  const decode = (g: Genome) => Object.fromEntries(dims.map((d, i) => [d.name, valueAt(d, g[i])]));
  const run = async (g: Genome, generation: number) => {
    const params = decode(g);
    const key = JSON.stringify(params);
    let ev = cache.get(key);
    if (!ev) {
      hooks.checkCancelled?.();
      try {
        ev = await evaluate(params);
      } catch (err) {
        ev = { params, metrics: {}, feasible: false, violations: 999, error: (err as Error).message };
      }
      cache.set(key, ev);
      all.push({ ...ev, generation, score: 0 });
    }
    done++;
    hooks.progress?.(done, total, `generation ${generation + 1}/${gens}`);
    return ev;
  };

  let population: Genome[] = Array.from({ length: pop }, () => dims.map(() => random()));
  for (let gen = 0; gen < gens; gen++) {
    const evals = [];
    for (const g of population) evals.push({ g, ev: await run(g, gen) });

    const scores = scoreAll(all, options.objectives, options.weights);
    all.forEach((e, i) => (e.score = scores[i]));
    const scoreOf = (ev: Evaluation) => all.find((a) => JSON.stringify(a.params) === JSON.stringify(ev.params))?.score ?? 0;
    const ranked = evals
      .map((x) => ({ ...x, score: scoreOf(x.ev), feasible: x.ev.feasible, violations: x.ev.violations }))
      .sort((a, b) => (better(a, b) ? -1 : better(b, a) ? 1 : 0));
    history.push({ generation: gen + 1, best_score: ranked[0].score, feasible: ranked.filter((r) => r.feasible).length });
    if (gen === gens - 1) break;

    const tournament = () => {
      const a = ranked[Math.floor(random() * ranked.length)];
      const b = ranked[Math.floor(random() * ranked.length)];
      return better(a, b) ? a.g : b.g;
    };
    const next: Genome[] = ranked.slice(0, elite).map((r) => r.g);
    while (next.length < pop) {
      const p1 = tournament();
      const p2 = tournament();
      const child = p1.map((v, i) => {
        const alpha = random();
        let c = alpha * v + (1 - alpha) * p2[i];
        if (random() < mutation) c += (random() - 0.5) * 0.4;
        return Math.max(0, Math.min(1, c));
      });
      next.push(child);
    }
    population = next;
  }

  const sorted = [...all].sort((a, b) => (better(a, b) ? -1 : better(b, a) ? 1 : 0));
  return { evaluations: all, best: sorted[0], history };
}
