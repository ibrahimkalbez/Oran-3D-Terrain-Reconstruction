import { commandAdapter } from "./adapters/command.js";
import { solarAdapter } from "./adapters/solar.js";
import { workbenchAdapter } from "./adapters/workbench.js";
import type { SolverAdapter } from "./store.js";

export const ADAPTERS: SolverAdapter[] = [solarAdapter, workbenchAdapter, commandAdapter];

export function adapter(id: string): SolverAdapter {
  const a = ADAPTERS.find((x) => x.id === id);
  if (!a) throw new Error(`Unknown solver '${id}'. Available: ${ADAPTERS.map((x) => x.id).join(", ")}`);
  return a;
}
