import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { BridgeError, RpcCodes } from "../bridge/client.js";

const MAX_TEXT = 120_000;

/** JSON for Claude: indented when small, compact when large, truncated beyond MAX_TEXT. */
export function toText(data: unknown): string {
  if (typeof data === "string") return data;
  let text = JSON.stringify(data, null, 2);
  if (text.length > 20_000) text = JSON.stringify(data);
  if (text.length > MAX_TEXT) {
    text = text.slice(0, MAX_TEXT) + `\n…[truncated ${text.length - MAX_TEXT} characters: narrow the request (limit, filter, outputs)]`;
  }
  return text;
}

export function ok(data: unknown, summary?: string): CallToolResult {
  const text = summary ? `${summary}\n\n${toText(data)}` : toText(data);
  return { content: [{ type: "text", text }] };
}

export interface ImagePayload {
  image_base64?: string;
  mime_type?: string;
  [key: string]: unknown;
}

/** Returns the image as MCP image content and the remaining fields as text. */
export function withImage(payload: ImagePayload, summary?: string, extra?: unknown): CallToolResult {
  const { image_base64, mime_type, ...rest } = payload;
  const content: CallToolResult["content"] = [];
  if (image_base64) content.push({ type: "image", data: image_base64, mimeType: mime_type ?? "image/png" });
  const info = extra === undefined ? rest : { ...rest, ...(extra as object) };
  content.push({ type: "text", text: summary ? `${summary}\n\n${toText(info)}` : toText(info) });
  return { content };
}

const HINTS: Record<number, string> = {
  [RpcCodes.NoActiveDocument]: "Open or create a model (Rhino) or a project (Revit) and retry.",
  [RpcCodes.GrasshopperUnavailable]: "Rhino: start Grasshopper (command: Grasshopper). Revit: check that Dynamo for Revit is installed.",
  [RpcCodes.NotFound]: "Check the names/ids listed in 'data' and retry with one of them.",
  [RpcCodes.Ambiguous]: "Several objects match: retry with the id of the intended one.",
  [RpcCodes.Timeout]: "The application is busy (open dialog, edit mode or running command). Ask the user to finish it.",
};

export function fail(err: unknown): CallToolResult {
  let text: string;
  if (err instanceof BridgeError) {
    const hint = err.code !== undefined ? HINTS[err.code] : undefined;
    text = `Error (${err.kind}${err.code !== undefined ? ` ${err.code}` : ""}): ${err.message}`;
    if (hint) text += `\nHint: ${hint}`;
    if (err.data !== undefined) text += `\nData: ${toText(err.data)}`;
  } else if (err instanceof Error) {
    text = `Error: ${err.message}`;
  } else {
    text = `Error: ${String(err)}`;
  }
  return { content: [{ type: "text", text }], isError: true };
}

/** Wraps a tool handler so that any exception becomes an MCP error result Claude can read and act on. */
export function guarded<A>(handler: (args: A, extra: any) => Promise<CallToolResult>) {
  return async (args: A, extra: any): Promise<CallToolResult> => {
    try {
      return await handler(args, extra);
    } catch (err) {
      return fail(err);
    }
  };
}

/** Sends an MCP progress notification when the client asked for progress. */
export async function progress(extra: any, value: number, total: number, message?: string): Promise<void> {
  const token = extra?._meta?.progressToken;
  if (token === undefined || typeof extra?.sendNotification !== "function") return;
  try {
    await extra.sendNotification({
      method: "notifications/progress",
      params: { progressToken: token, progress: value, total, ...(message ? { message } : {}) },
    });
  } catch {
    // Progress is best effort.
  }
}
