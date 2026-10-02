using System;
using Rhino;
using Rhino.Commands;
using Rhino.Input;

namespace RhinoMcpBridge.Plugin
{
  /// <summary>Starts the bridge (it normally starts by itself with Rhino).</summary>
  public class McpBridgeStartCommand : Command
  {
    public override string EnglishName => "McpBridgeStart";

    protected override Result RunCommand(RhinoDoc doc, RunMode mode)
    {
      if (BridgeHost.IsRunning)
      {
        RhinoApp.WriteLine("Rhino MCP Bridge is already running on port {0}.", BridgeHost.Port);
        return Result.Success;
      }
      try
      {
        BridgeHost.Start(BridgePlugIn.Instance.PreferredPort);
        return Result.Success;
      }
      catch (Exception ex)
      {
        RhinoApp.WriteLine("Rhino MCP Bridge could not start: {0}", ex.Message);
        return Result.Failure;
      }
    }
  }

  public class McpBridgeStopCommand : Command
  {
    public override string EnglishName => "McpBridgeStop";

    protected override Result RunCommand(RhinoDoc doc, RunMode mode)
    {
      BridgeHost.Stop();
      RhinoApp.WriteLine("Rhino MCP Bridge stopped. Claude can no longer reach this Rhino until McpBridgeStart.");
      return Result.Success;
    }
  }

  public class McpBridgeStatusCommand : Command
  {
    public override string EnglishName => "McpBridgeStatus";

    protected override Result RunCommand(RhinoDoc doc, RunMode mode)
    {
      if (!BridgeHost.IsRunning)
      {
        RhinoApp.WriteLine("Rhino MCP Bridge {0}: stopped. Run McpBridgeStart.", BridgeHost.Version);
        return Result.Success;
      }
      RhinoApp.WriteLine("Rhino MCP Bridge {0}: running on http://127.0.0.1:{1}", BridgeHost.Version, BridgeHost.Port);
      RhinoApp.WriteLine("  discovery file: {0}", BridgeHost.RegistryFile);
      RhinoApp.WriteLine("  autostart: {0}, preferred port: {1}, verbose log: {2}",
        BridgePlugIn.Instance.AutoStart, BridgePlugIn.Instance.PreferredPort, BridgeHost.Verbose);
      return Result.Success;
    }
  }

  /// <summary>Changes the bridge settings: preferred port, autostart and request logging.</summary>
  public class McpBridgeSettingsCommand : Command
  {
    public override string EnglishName => "McpBridgeSettings";

    protected override Result RunCommand(RhinoDoc doc, RunMode mode)
    {
      var plugin = BridgePlugIn.Instance;
      int port = plugin.PreferredPort;
      if (RhinoGet.GetInteger("Preferred port (1024-65535)", true, ref port, 1024, 65535) != Result.Success) return Result.Cancel;
      bool autostart = plugin.AutoStart;
      if (RhinoGet.GetBool("Start with Rhino", true, "No", "Yes", ref autostart) != Result.Success) return Result.Cancel;
      bool verbose = BridgeHost.Verbose;
      if (RhinoGet.GetBool("Log each request on the command line", true, "No", "Yes", ref verbose) != Result.Success) return Result.Cancel;

      bool restart = port != plugin.PreferredPort && BridgeHost.IsRunning;
      plugin.PreferredPort = port;
      plugin.AutoStart = autostart;
      BridgeHost.Verbose = verbose;
      if (restart)
      {
        BridgeHost.Stop();
        BridgeHost.Start(port);
      }
      RhinoApp.WriteLine("Rhino MCP Bridge settings saved (port {0}, autostart {1}, verbose {2}).", port, autostart, verbose);
      return Result.Success;
    }
  }
}
