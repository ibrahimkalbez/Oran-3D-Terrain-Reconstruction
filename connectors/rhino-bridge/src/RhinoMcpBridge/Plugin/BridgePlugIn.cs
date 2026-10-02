using System;
using Rhino;
using Rhino.PlugIns;

namespace RhinoMcpBridge.Plugin
{
  /// <summary>
  /// Rhino plug-in. Loads when Rhino starts and starts the bridge once Rhino is idle,
  /// so Claude can reach Rhino without any manual step. Settings (stored by Rhino):
  ///   port      first port tried (default 8642; the next 9 are tried if it is busy)
  ///   autostart start the bridge with Rhino (default true)
  /// </summary>
  public class BridgePlugIn : PlugIn
  {
    public static BridgePlugIn Instance { get; private set; }

    static BridgePlugIn()
    {
      EmbeddedAssemblies.Install();
    }

    public BridgePlugIn()
    {
      Instance = this;
    }

    public override PlugInLoadTime LoadTime => PlugInLoadTime.AtStartup;

    public int PreferredPort
    {
      get => Settings.GetInteger("port", BridgeHost.DefaultPort);
      set => Settings.SetInteger("port", value);
    }

    public bool AutoStart
    {
      get => Settings.GetBool("autostart", true);
      set => Settings.SetBool("autostart", value);
    }

    protected override LoadReturnCode OnLoad(ref string errorMessage)
    {
      if (AutoStart) RhinoApp.Idle += StartWhenIdle;
      return LoadReturnCode.Success;
    }

    private void StartWhenIdle(object sender, EventArgs e)
    {
      RhinoApp.Idle -= StartWhenIdle;
      try
      {
        var envPort = Environment.GetEnvironmentVariable("RHINO_MCP_PORT");
        int port = int.TryParse(envPort, out var p) ? p : PreferredPort;
        BridgeHost.Start(port);
      }
      catch (Exception ex)
      {
        RhinoApp.WriteLine("Rhino MCP Bridge could not start: {0}. Run McpBridgeStart to retry.", ex.Message);
      }
    }

    protected override void OnShutdown()
    {
      BridgeHost.Stop();
      base.OnShutdown();
    }
  }
}
