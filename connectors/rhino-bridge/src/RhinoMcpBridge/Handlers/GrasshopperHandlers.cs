using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using Rhino.PlugIns;
using McpBridge.Transport;

namespace RhinoMcpBridge.Handlers
{
  /// <summary>
  /// grasshopper.* methods. This class only forwards to <see cref="GhBridge"/>, which is the
  /// only type that touches Grasshopper.dll: Rhino starts the bridge without loading
  /// Grasshopper, and Grasshopper is loaded on the first grasshopper.* call.
  /// </summary>
  public static class GrasshopperHandlers
  {
    public static readonly Guid GrasshopperPlugInId = new Guid("B45A29B1-4343-4035-989E-044E8580D9CF");

    public static void Register(RpcDispatcher d)
    {
      var methods = new Dictionary<string, RpcDispatcher.Handler>
      {
        ["grasshopper.status"] = p => GhBridge.Status(),
        ["grasshopper.open_definition"] = p => GhBridge.OpenDefinition(p),
        ["grasshopper.close_definition"] = p => GhBridge.CloseDefinition(p),
        ["grasshopper.get_definition"] = p => GhBridge.GetDefinition(p),
        ["grasshopper.get_parameters"] = p => GhBridge.GetParameters(p),
        ["grasshopper.set_parameter"] = p => GhBridge.SetParameter(p),
        ["grasshopper.solve"] = p => GhBridge.Solve(p),
        ["grasshopper.get_results"] = p => GhBridge.GetResults(p),
        ["grasshopper.create_component"] = p => GhBridge.CreateComponent(p),
        ["grasshopper.connect_components"] = p => GhBridge.ConnectComponents(p),
        ["grasshopper.export_geometry"] = p => GhBridge.ExportGeometry(p),
        ["grasshopper.save_definition"] = p => GhBridge.SaveDefinition(p),
        ["grasshopper.capture_canvas"] = p => GhBridge.CaptureCanvas(p),
        ["grasshopper.search_components"] = p => GhBridge.SearchComponents(p),
      };
      foreach (var kv in methods) d.Register(kv.Key, kv.Value);
    }

    public static bool IsLoaded()
    {
      return PlugIn.PlugInExists(GrasshopperPlugInId, out bool loaded, out _) && loaded;
    }

    /// <summary>Status for rhino.get_document: never forces Grasshopper to load.</summary>
    public static JToken StatusIfLoaded()
    {
      if (!IsLoaded()) return new JObject { ["loaded"] = false };
      return GhBridge.Status();
    }
  }
}
