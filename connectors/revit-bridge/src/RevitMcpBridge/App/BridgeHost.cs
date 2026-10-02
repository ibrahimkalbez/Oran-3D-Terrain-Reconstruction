using System;
using System.Diagnostics;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;
using RevitMcpBridge.Handlers;

namespace RevitMcpBridge.App
{
  /// <summary>Owns the HTTP server, the dispatcher and the discovery file for this Revit process.</summary>
  public static class BridgeHost
  {
    public const int DefaultPort = 8742;
    public static readonly string Version = typeof(BridgeHost).Assembly.GetName().Version.ToString(3);

    private static HttpRpcServer _server;
    private static InstanceRegistry _registry;
    private static RpcDispatcher _dispatcher;
    private static string _revitVersion;

    public static bool IsRunning => _server?.IsRunning == true;
    public static int Port => _server?.Port ?? 0;
    public static string RegistryFile => _registry?.FilePath;

    public static RpcDispatcher BuildDispatcher()
    {
      var d = new RpcDispatcher { Invoker = work => RevitApp.Queue.Invoke(app => work()) };
      d.Register("bridge.info", Info);
      d.Register("bridge.ping", p => new JObject { ["pong"] = true, ["time"] = DateTime.UtcNow.ToString("o") }, direct: true);
      DocumentHandlers.Register(d);
      ObjectHandlers.Register(d);
      ParameterHandlers.Register(d);
      CreateHandlers.Register(d);
      ViewHandlers.Register(d);
      ExportHandlers.Register(d);
      MetricsHandlers.Register(d);
      DynamoHandlers.Register(d);
      AnalysisHandlers.Register(d);
      return d;
    }

    public static void Start(string revitVersion)
    {
      if (IsRunning) return;
      _revitVersion = revitVersion;
      _dispatcher = BuildDispatcher();
      var token = Environment.GetEnvironmentVariable("REVIT_MCP_TOKEN");
      if (string.IsNullOrWhiteSpace(token)) token = HttpRpcServer.NewToken();
      int port = int.TryParse(Environment.GetEnvironmentVariable("REVIT_MCP_PORT"), out var p) ? p : DefaultPort;

      _server = new HttpRpcServer(_dispatcher, token, Version, "revit-mcp-bridge");
      port = _server.Start(port, 10);
      _registry = new InstanceRegistry(InstanceRegistry.DefaultDirectory("RevitMcpBridge", "REVIT_MCP_BRIDGE_DIR"));
      _registry.PruneStale();
      _registry.Publish(port, token, "Revit " + revitVersion, Version, null);
    }

    public static void Stop()
    {
      _server?.Stop();
      _server = null;
      _registry?.Remove();
    }

    /// <summary>Keeps the discovery file's document name current (called after each request).</summary>
    public static void NoteDocument(string label) => _registry?.UpdateDocument(label);

    private static JToken Info(JObject p)
    {
      var app = RevitContext.App;
      var doc = app.ActiveUIDocument?.Document;
      return new JObject
      {
        ["bridge_version"] = Version,
        ["host"] = "revit",
        ["revit_version"] = app.Application.VersionNumber + " (" + app.Application.VersionBuild + ")",
        ["language"] = app.Application.Language.ToString(),
        ["runtime"] = System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
        ["pid"] = Process.GetCurrentProcess().Id,
        ["port"] = Port,
        ["document"] = doc == null ? null : new JObject { ["title"] = doc.Title, ["path"] = doc.PathName },
        ["dynamo"] = DynamoHandlers.Availability(),
        ["methods"] = new JArray(_dispatcher?.Methods ?? new string[0]),
      };
    }
  }
}
