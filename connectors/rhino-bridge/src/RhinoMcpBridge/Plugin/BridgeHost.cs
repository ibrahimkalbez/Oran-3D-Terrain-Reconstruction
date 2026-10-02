using System;
using System.Diagnostics;
using System.Reflection;
using Newtonsoft.Json.Linq;
using Rhino;
using RhinoMcpBridge.Core;
using RhinoMcpBridge.Handlers;
using RhinoMcpBridge.Transport;

namespace RhinoMcpBridge.Plugin
{
  /// <summary>Owns the HTTP server, the dispatcher and the discovery file for this Rhino process.</summary>
  public static class BridgeHost
  {
    public const int DefaultPort = 8642;
    public static readonly string Version = typeof(BridgeHost).Assembly.GetName().Version.ToString(3);

    private static HttpRpcServer _server;
    private static InstanceRegistry _registry;
    private static RpcDispatcher _dispatcher;
    private static string _token;

    public static bool Verbose { get; set; }
    public static bool IsRunning => _server?.IsRunning == true;
    public static int Port => _server?.Port ?? 0;
    public static string RegistryFile => _registry?.FilePath;

    public static RpcDispatcher BuildDispatcher()
    {
      var d = new RpcDispatcher { Invoker = UiThread.Invoke };
      d.Register("bridge.info", Info);
      d.Register("bridge.ping", p => new JObject { ["pong"] = true, ["time"] = DateTime.UtcNow.ToString("o") }, direct: true);
      RhinoHandlers.Register(d);
      ViewHandlers.Register(d);
      GrasshopperHandlers.Register(d);
      return d;
    }

    public static void Start(int preferredPort)
    {
      if (IsRunning) return;
      _dispatcher = BuildDispatcher();
      _token = Environment.GetEnvironmentVariable("RHINO_MCP_TOKEN");
      if (string.IsNullOrWhiteSpace(_token)) _token = HttpRpcServer.NewToken();

      _server = new HttpRpcServer(_dispatcher, _token, Version) { Log = Log };
      int port = _server.Start(preferredPort, 10);

      _registry = new InstanceRegistry();
      _registry.PruneStale();
      _registry.Publish(port, _token, RhinoApp.Version.ToString(), Version, DocumentLabel());

      RhinoDoc.EndOpenDocument -= OnDocumentChanged;
      RhinoDoc.EndOpenDocument += OnDocumentChanged;
      RhinoDoc.NewDocument -= OnDocumentChanged;
      RhinoDoc.NewDocument += OnDocumentChanged;
      RhinoDoc.EndSaveDocument -= OnDocumentChanged;
      RhinoDoc.EndSaveDocument += OnDocumentChanged;

      RhinoApp.WriteLine("Rhino MCP Bridge {0} listening on http://127.0.0.1:{1} (Claude connector ready).", Version, port);
    }

    public static void Stop()
    {
      RhinoDoc.EndOpenDocument -= OnDocumentChanged;
      RhinoDoc.NewDocument -= OnDocumentChanged;
      RhinoDoc.EndSaveDocument -= OnDocumentChanged;
      _server?.Stop();
      _server = null;
      _registry?.Remove();
    }

    private static void OnDocumentChanged(object sender, EventArgs e) => _registry?.UpdateDocument(DocumentLabel());

    private static string DocumentLabel()
    {
      var doc = RhinoDoc.ActiveDoc;
      if (doc == null) return null;
      return string.IsNullOrEmpty(doc.Path) ? (doc.Name ?? "(untitled)") : doc.Path;
    }

    private static void Log(string line)
    {
      if (!Verbose) return;
      RhinoApp.InvokeOnUiThread(new Action(() => RhinoApp.WriteLine(line)));
    }

    private static JToken Info(JObject p)
    {
      var doc = RhinoDoc.ActiveDoc;
      return new JObject
      {
        ["bridge_version"] = Version,
        ["rhino_version"] = RhinoApp.Version.ToString(),
        ["runtime"] = System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
        ["pid"] = Process.GetCurrentProcess().Id,
        ["port"] = Port,
        ["document"] = doc == null ? null : new JObject
        {
          ["name"] = doc.Name,
          ["path"] = doc.Path,
          ["units"] = RhinoUtil.Units(doc.ModelUnitSystem),
          ["object_count"] = doc.Objects.Count,
        },
        ["grasshopper_loaded"] = GrasshopperHandlers.IsLoaded(),
        ["methods"] = new JArray(_dispatcher?.Methods ?? new string[0]),
      };
    }
  }
}
