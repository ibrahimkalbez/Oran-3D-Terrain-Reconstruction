/**
 * Small, safe expression language for custom urban rules and objectives.
 *   OUT_GFA / site_area <= 2.5
 *   max(height_max, 0) < 28 and coverage <= 0.6
 *   `Buildings.volume` / 1000
 * Numbers, identifiers (letters, digits, _, .), back-quoted names, + - * / ^ %,
 * comparisons, and/or/not, and the functions min, max, abs, sqrt, round, floor, ceil.
 * No property access, no calls outside this list: nothing from the host can be reached.
 */

type Token = { t: "num"; v: number } | { t: "id"; v: string } | { t: "op"; v: string };

const FUNCTIONS: Record<string, (...a: number[]) => number> = {
  min: Math.min,
  max: Math.max,
  abs: Math.abs,
  sqrt: Math.sqrt,
  round: Math.round,
  floor: Math.floor,
  ceil: Math.ceil,
};

function tokenize(src: string): Token[] {
  const out: Token[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (/\s/.test(c)) {
      i++;
      continue;
    }
    if (/[0-9]/.test(c) || (c === "." && /[0-9]/.test(src[i + 1] ?? ""))) {
      const m = /^(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i.exec(src.slice(i))!;
      out.push({ t: "num", v: Number(m[0]) });
      i += m[0].length;
      continue;
    }
    if (c === "`") {
      const end = src.indexOf("`", i + 1);
      if (end < 0) throw new Error("Unclosed ` in expression.");
      out.push({ t: "id", v: src.slice(i + 1, end) });
      i = end + 1;
      continue;
    }
    if (/[A-Za-z_À-ÿ]/.test(c)) {
      const m = /^[A-Za-z_À-ÿ][A-Za-z0-9_À-ÿ.]*/.exec(src.slice(i))!;
      const word = m[0];
      const lower = word.toLowerCase();
      if (lower === "and" || lower === "or" || lower === "not" || lower === "et" || lower === "ou") {
        out.push({ t: "op", v: lower === "et" ? "and" : lower === "ou" ? "or" : lower });
      } else out.push({ t: "id", v: word });
      i += word.length;
      continue;
    }
    const two = src.slice(i, i + 2);
    if (["<=", ">=", "==", "!=", "&&", "||"].includes(two)) {
      out.push({ t: "op", v: two === "&&" ? "and" : two === "||" ? "or" : two });
      i += 2;
      continue;
    }
    if ("+-*/^%()<>,!".includes(c)) {
      out.push({ t: "op", v: c === "!" ? "not" : c });
      i++;
      continue;
    }
    throw new Error(`Unexpected '${c}' in expression.`);
  }
  return out;
}

export type Value = number | boolean;

export function evaluate(expression: string, vars: Record<string, unknown>): Value {
  const tokens = tokenize(expression);
  let pos = 0;
  const peek = () => tokens[pos];
  const isOp = (v: string) => peek()?.t === "op" && peek()!.v === v;
  const expect = (v: string) => {
    if (!isOp(v)) throw new Error(`Expected '${v}' in expression.`);
    pos++;
  };
  const num = (v: Value): number => (typeof v === "boolean" ? (v ? 1 : 0) : v);
  const lookup = (name: string): number => {
    const keys = [name, name.toLowerCase()];
    for (const k of Object.keys(vars)) if (k.toLowerCase() === name.toLowerCase()) keys.push(k);
    for (const k of keys) {
      const v = vars[k];
      if (typeof v === "number" && Number.isFinite(v)) return v;
      if (typeof v === "boolean") return v ? 1 : 0;
      if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
    }
    throw new Error(`Unknown value '${name}'. Available: ${Object.keys(vars).slice(0, 40).join(", ")}`);
  };

  const parseOr = (): Value => {
    let left = parseAnd();
    while (isOp("or")) {
      pos++;
      const right = parseAnd();
      left = Boolean(left) || Boolean(right);
    }
    return left;
  };
  const parseAnd = (): Value => {
    let left = parseNot();
    while (isOp("and")) {
      pos++;
      const right = parseNot();
      left = Boolean(left) && Boolean(right);
    }
    return left;
  };
  const parseNot = (): Value => {
    if (isOp("not")) {
      pos++;
      return !parseNot();
    }
    return parseCmp();
  };
  const parseCmp = (): Value => {
    const left = parseSum();
    const t = peek();
    if (t?.t === "op" && ["<", "<=", ">", ">=", "==", "!="].includes(t.v)) {
      pos++;
      const a = num(left);
      const b = num(parseSum());
      switch (t.v) {
        case "<": return a < b;
        case "<=": return a <= b + 1e-9;
        case ">": return a > b;
        case ">=": return a >= b - 1e-9;
        case "==": return Math.abs(a - b) <= 1e-9;
        default: return Math.abs(a - b) > 1e-9;
      }
    }
    return left;
  };
  const parseSum = (): Value => {
    let left = num(parseTerm());
    while (isOp("+") || isOp("-")) {
      const op = tokens[pos++].v;
      const right = num(parseTerm());
      left = op === "+" ? left + right : left - right;
    }
    return left;
  };
  const parseTerm = (): Value => {
    let left = num(parseUnary());
    while (isOp("*") || isOp("/") || isOp("%")) {
      const op = tokens[pos++].v;
      const right = num(parseUnary());
      if ((op === "/" || op === "%") && right === 0) throw new Error("Division by zero in expression.");
      left = op === "*" ? left * right : op === "/" ? left / right : left % right;
    }
    return left;
  };
  const parseUnary = (): Value => {
    if (isOp("-")) {
      pos++;
      return -num(parseUnary());
    }
    if (isOp("+")) {
      pos++;
      return num(parseUnary());
    }
    return parsePower();
  };
  const parsePower = (): Value => {
    const base = parseAtom();
    if (isOp("^")) {
      pos++;
      return Math.pow(num(base), num(parseUnary()));
    }
    return base;
  };
  const parseAtom = (): Value => {
    const t = peek();
    if (!t) throw new Error("Unexpected end of expression.");
    if (t.t === "num") {
      pos++;
      return t.v;
    }
    if (t.t === "id") {
      pos++;
      if (isOp("(")) {
        const fn = FUNCTIONS[t.v.toLowerCase()];
        if (!fn) throw new Error(`Unknown function '${t.v}'. Use ${Object.keys(FUNCTIONS).join(", ")}.`);
        pos++;
        const args: number[] = [];
        if (!isOp(")")) {
          args.push(num(parseOr()));
          while (isOp(",")) {
            pos++;
            args.push(num(parseOr()));
          }
        }
        expect(")");
        return fn(...args);
      }
      return lookup(t.v);
    }
    if (isOp("(")) {
      pos++;
      const v = parseOr();
      expect(")");
      return v;
    }
    throw new Error(`Unexpected '${t.v}' in expression.`);
  };

  const result = parseOr();
  if (pos < tokens.length) throw new Error(`Unexpected '${tokens[pos].v}' in expression.`);
  return result;
}

/** Identifiers used by an expression (to tell the user which metrics are missing). */
export function identifiers(expression: string): string[] {
  return [...new Set(tokenize(expression).filter((t) => t.t === "id").map((t) => t.v as string))].filter(
    (id, i, all) => !(FUNCTIONS[id.toLowerCase()] && all.indexOf(id) === i && expression.includes(id + "(")),
  );
}
