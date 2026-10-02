using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using Xunit;

/// <summary>
/// Inspects the compiled add-ins (Revit 2022–2024 and 2025–2026 builds) without Revit, and checks
/// that every JSON-RPC method called by the Revit MCP servers is registered by the add-in.
/// Build the add-in first (dotnet build -c Release in src/RevitMcpBridge).
/// </summary>
public class AddinPackagingTests
{
  private static readonly string Root = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../.."));
  private static string Nuget => Environment.GetEnvironmentVariable("NUGET_PACKAGES") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".nuget/packages");

  public static IEnumerable<object[]> Builds() => new[]
  {
    new object[] { "net48", "2022.1.21" },
    new object[] { "net8.0-windows", "2025.0.2" },
  };

  private static MetadataLoadContext Context(string dll, string tfm, string revitApi)
  {
    var paths = new List<string> { dll };
    if (tfm == "net48")
    {
      paths.AddRange(Directory.GetFiles(Path.Combine(Nuget, "microsoft.netframework.referenceassemblies.net48/1.0.3/build/.NETFramework/v4.8"), "*.dll", SearchOption.AllDirectories));
      paths.AddRange(Directory.GetFiles(Path.Combine(Nuget, "newtonsoft.json/13.0.3/lib/net45"), "*.dll"));
    }
    else
    {
      paths.AddRange(Directory.GetFiles(Path.GetDirectoryName(typeof(object).Assembly.Location), "*.dll"));
      paths.AddRange(Directory.GetFiles(Path.Combine(Nuget, "newtonsoft.json/13.0.3/lib/netstandard2.0"), "*.dll"));
    }
    paths.AddRange(Directory.GetFiles(Path.Combine(Nuget, "nice3point.revit.api.revitapi", revitApi), "*.dll", SearchOption.AllDirectories));
    paths.AddRange(Directory.GetFiles(Path.Combine(Nuget, "nice3point.revit.api.revitapiui", revitApi), "*.dll", SearchOption.AllDirectories));
    return new MetadataLoadContext(new PathAssemblyResolver(paths.GroupBy(Path.GetFileName).Select(g => g.First())), tfm == "net48" ? "mscorlib" : "System.Private.CoreLib");
  }

  [Theory]
  [MemberData(nameof(Builds))]
  public void AddinIsWellFormed(string tfm, string revitApi)
  {
    var dll = Path.Combine(Root, "src/RevitMcpBridge/bin/Release", tfm, "RevitMcpBridge.dll");
    Assert.True(File.Exists(dll), "Build the add-in first: " + dll);
    using var mlc = Context(dll, tfm, revitApi);
    var asm = mlc.LoadFromAssemblyPath(dll);

    var apps = asm.GetTypes().Where(t => t.GetInterfaces().Any(i => i.FullName == "Autodesk.Revit.UI.IExternalApplication")).Select(t => t.FullName).ToList();
    Assert.Equal(new[] { "RevitMcpBridge.App.RevitApp" }, apps);
    var commands = asm.GetTypes().Where(t => t.GetInterfaces().Any(i => i.FullName == "Autodesk.Revit.UI.IExternalCommand")).Select(t => t.Name).OrderBy(n => n).ToList();
    Assert.Equal(new[] { "StatusCommand", "ToggleCommand" }, commands);
    Assert.Contains(asm.GetTypes(), t => t.GetInterfaces().Any(i => i.FullName == "Autodesk.Revit.UI.IExternalEventHandler"));
    Assert.Contains("RevitMcpBridge.Embedded.Newtonsoft.Json.dll", asm.GetManifestResourceNames());
    var refs = asm.GetReferencedAssemblies().Select(r => r.Name).ToList();
    Assert.Contains("RevitAPI", refs);
    Assert.Contains("RevitAPIUI", refs);
    // Dynamo is reached by reflection: the add-in must load where Dynamo is absent.
    Assert.DoesNotContain(refs, r => r.StartsWith("Dynamo", StringComparison.OrdinalIgnoreCase));
  }

  private static HashSet<string> Registered()
  {
    var sources = Directory.GetFiles(Path.Combine(Root, "src/RevitMcpBridge"), "*.cs", SearchOption.AllDirectories)
      .Where(f => !f.Contains(Path.DirectorySeparatorChar + "obj" + Path.DirectorySeparatorChar))
      .Select(File.ReadAllText).Aggregate((a, b) => a + b);
    return new HashSet<string>(Regex.Matches(sources, "Register\\(\"([a-z]+\\.[a-z_]+)\"").Select(m => m.Groups[1].Value));
  }

  [Fact]
  public void TheRevitContractMatchesTheRhinoOneForSharedModules()
  {
    var registered = Registered();
    foreach (var m in new[] { "analysis.footprints", "analysis.curves", "analysis.drape_points", "analysis.ray_visibility" })
      Assert.Contains(m, registered);
    foreach (var verb in new[] { "get_document", "get_objects", "create_geometry", "delete_objects", "select_objects", "set_object_data", "transform_objects", "capture_viewport", "export", "save_document", "open_document" })
      Assert.Contains("revit." + verb, registered);
    foreach (var m in new[] { "dynamo.run", "dynamo.status", "dynamo.get_workspace", "bridge.info", "bridge.ping" })
      Assert.Contains(m, registered);
  }

  [Fact]
  public void EveryMethodUsedByTheRevitMcpServersIsRegistered()
  {
    var registered = Registered();
    // The Revit servers, the Fusion modules they reuse and the shared host table (rhino-grasshopper-mcp/src/host.ts).
    var servers = new[] { "../revit-dynamo-mcp/src", "../fusion-rda-mcp/src", "../fusion-rga-mcp/src", "../rhino-grasshopper-mcp/src" }
      .Select(d => Path.GetFullPath(Path.Combine(Root, d))).Where(Directory.Exists).ToList();
    Assert.Equal(4, servers.Count);
    var used = servers.SelectMany(d => Directory.GetFiles(d, "*.ts", SearchOption.AllDirectories))
      .SelectMany(f => Regex.Matches(File.ReadAllText(f), "\"((?:revit|dynamo|analysis|bridge)\\.[a-z_]+)\"").Select(m => m.Groups[1].Value))
      .Distinct().OrderBy(x => x).ToList();
    Assert.Contains("dynamo.run", used);
    var missing = used.Where(m => !registered.Contains(m)).ToList();
    Assert.True(missing.Count == 0, "Methods used by the Revit MCP servers but not registered in the add-in: " + string.Join(", ", missing));
  }
}
