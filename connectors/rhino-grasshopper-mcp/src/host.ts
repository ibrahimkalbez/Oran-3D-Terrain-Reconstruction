/**
 * The design application behind a connector. The Rhino and Revit bridges speak the same
 * JSON-RPC protocol (same filters, same analysis.* methods); a profile gives the names that
 * differ: environment variables, discovery folder, health-check service and method prefix.
 */
export type HostVerb =
  | "get_document"
  | "get_objects"
  | "create_geometry"
  | "delete_objects"
  | "select_objects"
  | "set_object_data"
  | "transform_objects"
  | "capture_viewport"
  | "export"
  | "save_document"
  | "open_document";

export interface HostProfile {
  id: "rhino" | "revit";
  /** Name used in messages ("Rhino", "Revit"). */
  label: string;
  /** Prefix of the environment variables: RHINO_MCP_PORT, REVIT_MCP_PORT… */
  envPrefix: string;
  /** Folder under %LOCALAPPDATA% where the bridge publishes its discovery files. */
  appFolder: string;
  /** "service" field of the bridge's /health answer. */
  service: string;
  /** Default workspace folder name under Documents. */
  workspaceName: string;
  /** Shown when no bridge answers. */
  notRunningHelp: string;
  methods: Record<HostVerb, string>;
  /** Geometry export format understood by the host for simulation meshes. */
  meshFormat: string;
}

export const RHINO: HostProfile = {
  id: "rhino",
  label: "Rhino",
  envPrefix: "RHINO_MCP",
  appFolder: "RhinoMcpBridge",
  service: "rhino-mcp-bridge",
  workspaceName: "RhinoMCP",
  notRunningHelp:
    "Rhino is not reachable. Check that Rhino 8 is open and that the RhinoMcpBridge plug-in is installed " +
    "(type McpBridgeStatus in the Rhino command line; McpBridgeStart starts it).",
  methods: {
    get_document: "rhino.get_document",
    get_objects: "rhino.get_objects",
    create_geometry: "rhino.create_geometry",
    delete_objects: "rhino.delete_objects",
    select_objects: "rhino.select_objects",
    set_object_data: "rhino.set_object_data",
    transform_objects: "rhino.transform_objects",
    capture_viewport: "rhino.capture_viewport",
    export: "rhino.export",
    save_document: "rhino.save_document",
    open_document: "rhino.open_document",
  },
  meshFormat: "stl",
};

export const REVIT: HostProfile = {
  id: "revit",
  label: "Revit",
  envPrefix: "REVIT_MCP",
  appFolder: "RevitMcpBridge",
  service: "revit-mcp-bridge",
  workspaceName: "RevitMCP",
  notRunningHelp:
    "Revit is not reachable. Check that Revit (2022–2026) is open with a project and that the RevitMcpBridge add-in is " +
    "installed (Add-Ins tab → MCP Bridge → Claude Bridge shows its state; Start / Stop starts it).",
  methods: {
    get_document: "revit.get_document",
    get_objects: "revit.get_objects",
    create_geometry: "revit.create_geometry",
    delete_objects: "revit.delete_objects",
    select_objects: "revit.select_objects",
    set_object_data: "revit.set_object_data",
    transform_objects: "revit.transform_objects",
    capture_viewport: "revit.capture_viewport",
    export: "revit.export",
    save_document: "revit.save_document",
    open_document: "revit.open_document",
  },
  meshFormat: "stl",
};

/** Method name of a host verb, e.g. hostMethod(REVIT, "export") → "revit.export". */
export function hostMethod(profile: HostProfile | undefined, verb: HostVerb): string {
  return (profile ?? RHINO).methods[verb];
}
