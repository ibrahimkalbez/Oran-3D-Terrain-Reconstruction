using System;
using System.Diagnostics;
using System.IO;
using Newtonsoft.Json.Linq;

namespace McpBridge.Transport
{
  /// <summary>
  /// Publishes how to reach this host application (Rhino, Revit…) so the MCP server can find
  /// it without any manual configuration:
  ///
  ///   %LOCALAPPDATA%\&lt;AppFolder&gt;\instances\&lt;pid&gt;.json      (AppFolder = RhinoMcpBridge, RevitMcpBridge)
  ///   { "pid", "port", "token", "host_version", "bridge_version", "started_at", "document" }
  ///
  /// The folder lives in the user profile, so only the current Windows user (and
  /// administrators) can read the token. Several instances can run at once:
  /// each one gets its own port and its own file.
  /// </summary>
  public sealed class InstanceRegistry
  {
    /// <summary>%LOCALAPPDATA%\&lt;appFolder&gt;\instances, or $&lt;envVariable&gt;\instances when that variable is set.</summary>
    public static string DefaultDirectory(string appFolder, string envVariable)
    {
      string overrideDir = Environment.GetEnvironmentVariable(envVariable);
      if (!string.IsNullOrWhiteSpace(overrideDir)) return Path.Combine(overrideDir, "instances");
      string root = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
      return Path.Combine(root, appFolder, "instances");
    }

    private readonly string _directory;
    private readonly int _pid;

    public InstanceRegistry(string directory)
    {
      _directory = directory ?? throw new ArgumentNullException(nameof(directory));
      _pid = Process.GetCurrentProcess().Id;
    }

    public string FilePath => Path.Combine(_directory, _pid + ".json");

    public void Publish(int port, string token, string hostVersion, string bridgeVersion, string document)
    {
      Directory.CreateDirectory(_directory);
      var info = new JObject
      {
        ["pid"] = _pid,
        ["port"] = port,
        ["host"] = "127.0.0.1",
        ["token"] = token,
        ["host_version"] = hostVersion,
        ["rhino_version"] = hostVersion, // kept for connector 1.0 clients
        ["bridge_version"] = bridgeVersion,
        ["started_at"] = DateTime.UtcNow.ToString("o"),
        ["document"] = document,
      };
      string tmp = FilePath + ".tmp";
      File.WriteAllText(tmp, info.ToString());
      if (File.Exists(FilePath)) File.Delete(FilePath);
      File.Move(tmp, FilePath);
    }

    /// <summary>Updates only the "document" field (called when the active Rhino document changes).</summary>
    public void UpdateDocument(string document)
    {
      try
      {
        if (!File.Exists(FilePath)) return;
        var info = JObject.Parse(File.ReadAllText(FilePath));
        info["document"] = document;
        File.WriteAllText(FilePath, info.ToString());
      }
      catch
      {
        // Best effort: discovery still works with a stale document name.
      }
    }

    public void Remove()
    {
      try
      {
        if (File.Exists(FilePath)) File.Delete(FilePath);
      }
      catch
      {
        // The MCP server ignores files whose process is gone.
      }
    }

    /// <summary>Deletes files left behind by Rhino processes that no longer exist.</summary>
    public void PruneStale()
    {
      if (!Directory.Exists(_directory)) return;
      foreach (var file in Directory.GetFiles(_directory, "*.json"))
      {
        if (!int.TryParse(Path.GetFileNameWithoutExtension(file), out int pid) || pid == _pid) continue;
        bool alive;
        try
        {
          using (var p = Process.GetProcessById(pid)) alive = !p.HasExited;
        }
        catch
        {
          alive = false;
        }
        if (!alive)
        {
          try { File.Delete(file); } catch { /* ignore */ }
        }
      }
    }
  }
}
