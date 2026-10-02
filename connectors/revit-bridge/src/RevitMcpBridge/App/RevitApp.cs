using System;
using System.IO;
using System.Linq;
using System.Reflection;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace RevitMcpBridge.App
{
  /// <summary>
  /// Revit add-in entry point. Starts the bridge when Revit starts and adds an "MCP Bridge"
  /// panel to the Add-Ins tab (status, start/stop).
  /// </summary>
  public class RevitApp : IExternalApplication
  {
    public static RevitQueue Queue { get; private set; }

    static RevitApp()
    {
      EmbeddedAssemblies.Install();
    }

    public Result OnStartup(UIControlledApplication application)
    {
      try
      {
        Queue = new RevitQueue();
        Queue.Initialize();
        CreateRibbon(application);
        // Keeps the discovery file's project name current (used to pick a Revit session by project).
        application.ViewActivated += (sender, e) => BridgeHost.NoteDocument(e.Document?.Title);
        if (!string.Equals(Environment.GetEnvironmentVariable("REVIT_MCP_AUTOSTART"), "false", StringComparison.OrdinalIgnoreCase))
          BridgeHost.Start(application.ControlledApplication.VersionNumber);
        return Result.Succeeded;
      }
      catch (Exception ex)
      {
        TaskDialog.Show("Revit MCP Bridge", "The bridge could not start: " + ex.Message);
        return Result.Failed;
      }
    }

    public Result OnShutdown(UIControlledApplication application)
    {
      BridgeHost.Stop();
      return Result.Succeeded;
    }

    private static void CreateRibbon(UIControlledApplication application)
    {
      var panel = application.CreateRibbonPanel("MCP Bridge");
      string assembly = Assembly.GetExecutingAssembly().Location;
      panel.AddItem(new PushButtonData("McpBridgeStatus", "Claude\nBridge", assembly, typeof(StatusCommand).FullName)
      {
        ToolTip = "State of the Claude bridge (Revit Dynamo Connector): port, active document, Dynamo.",
      });
      panel.AddItem(new PushButtonData("McpBridgeToggle", "Start /\nStop", assembly, typeof(ToggleCommand).FullName)
      {
        ToolTip = "Start or stop Claude's access to this Revit session.",
      });
    }
  }

  [Transaction(TransactionMode.ReadOnly)]
  public class StatusCommand : IExternalCommand
  {
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
    {
      var text = BridgeHost.IsRunning
        ? $"Running on http://127.0.0.1:{BridgeHost.Port}\nVersion {BridgeHost.Version}\nDiscovery file: {BridgeHost.RegistryFile}\n\nClaude can now drive this Revit session."
        : "Stopped. Click Start / Stop to let Claude reach this Revit session.";
      TaskDialog.Show("Revit MCP Bridge", text);
      return Result.Succeeded;
    }
  }

  [Transaction(TransactionMode.ReadOnly)]
  public class ToggleCommand : IExternalCommand
  {
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
    {
      if (BridgeHost.IsRunning)
      {
        BridgeHost.Stop();
        TaskDialog.Show("Revit MCP Bridge", "Stopped: Claude can no longer reach this Revit session.");
      }
      else
      {
        BridgeHost.Start(commandData.Application.Application.VersionNumber);
        TaskDialog.Show("Revit MCP Bridge", $"Started on port {BridgeHost.Port}.");
      }
      return Result.Succeeded;
    }
  }

  /// <summary>
  /// Resolves Newtonsoft.Json: the copy Revit already loaded when it is version 13 or later (Revit
  /// 2025+), otherwise the 13.0.3 copy embedded in the add-in, loaded side by side (Revit 2022–2024
  /// may ship an older version that lacks APIs the bridge uses).
  /// </summary>
  internal static class EmbeddedAssemblies
  {
    private static bool _installed;
    private static Assembly _embedded;

    public static void Install()
    {
      if (_installed) return;
      _installed = true;
      AppDomain.CurrentDomain.AssemblyResolve += (sender, args) =>
      {
        var requested = new AssemblyName(args.Name);
        if (requested.Name != "Newtonsoft.Json") return null;
        var loaded = AppDomain.CurrentDomain.GetAssemblies()
          .Where(a => a.GetName().Name == requested.Name)
          .OrderByDescending(a => a.GetName().Version)
          .FirstOrDefault();
        if (loaded != null && loaded.GetName().Version >= (requested.Version ?? new Version(13, 0))) return loaded;
        if (_embedded != null) return _embedded;
        using (var stream = typeof(EmbeddedAssemblies).Assembly.GetManifestResourceStream("RevitMcpBridge.Embedded." + requested.Name + ".dll"))
        {
          if (stream == null) return loaded;
          using (var ms = new MemoryStream())
          {
            stream.CopyTo(ms);
            _embedded = Assembly.Load(ms.ToArray());
            return _embedded;
          }
        }
      };
    }
  }
}
