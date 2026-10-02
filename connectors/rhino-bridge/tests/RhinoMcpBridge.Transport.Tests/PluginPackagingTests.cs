using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using Xunit;

/// <summary>
/// Inspects the compiled RhinoMcpBridge.rhp without running Rhino: plug-in class, identity,
/// commands, embedded dependency, and the JSON-RPC methods the MCP server relies on.
/// Build the plug-in first (dotnet build -c Release in src/RhinoMcpBridge).
/// </summary>
public class PluginPackagingTests
{
  private static readonly string Root = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../.."));
  private static readonly string Rhp = Path.Combine(Root, "src/RhinoMcpBridge/bin/Release/net48/RhinoMcpBridge.rhp");

  private static MetadataLoadContext Context()
  {
    var nuget = Environment.GetEnvironmentVariable("NUGET_PACKAGES") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".nuget/packages");
    var paths = new List<string> { Rhp };
    paths.AddRange(Directory.GetFiles(Path.Combine(nuget, "microsoft.netframework.referenceassemblies.net48/1.0.3/build/.NETFramework/v4.8"), "*.dll", SearchOption.AllDirectories));
    paths.AddRange(Directory.GetFiles(Path.Combine(nuget, "rhinocommon"), "*.dll", SearchOption.AllDirectories).Where(p => p.Contains("8.0.23304.9001")));
    paths.AddRange(Directory.GetFiles(Path.Combine(nuget, "grasshopper"), "*.dll", SearchOption.AllDirectories).Where(p => p.Contains("8.0.23304.9001")));
    paths.AddRange(Directory.GetFiles(Path.Combine(nuget, "newtonsoft.json/13.0.3/lib/net45"), "*.dll"));
    return new MetadataLoadContext(new PathAssemblyResolver(paths), "mscorlib");
  }

  [Fact]
  public void PluginIsWellFormed()
  {
    Assert.True(File.Exists(Rhp), "Build the plug-in first: " + Rhp);
    using var mlc = Context();
    var asm = mlc.LoadFromAssemblyPath(Rhp);

    var plugins = asm.GetTypes().Where(t => t.BaseType?.FullName == "Rhino.PlugIns.PlugIn").ToList();
    Assert.Single(plugins);

    var guid = asm.GetCustomAttributesData().Single(a => a.AttributeType.FullName == "System.Runtime.InteropServices.GuidAttribute");
    Assert.Equal("6c1f4c9e-7a52-4f0e-9a3b-2f8d5e1c7b40", (string)guid.ConstructorArguments[0].Value);

    var commands = asm.GetTypes().Where(t => t.BaseType?.FullName == "Rhino.Commands.Command").Select(t => t.Name).OrderBy(n => n).ToList();
    Assert.Equal(new[] { "McpBridgeSettingsCommand", "McpBridgeStartCommand", "McpBridgeStatusCommand", "McpBridgeStopCommand" }, commands);

    Assert.Contains("RhinoMcpBridge.Embedded.Newtonsoft.Json.dll", asm.GetManifestResourceNames());
    var refs = asm.GetReferencedAssemblies().Select(r => r.Name).ToList();
    Assert.Contains("RhinoCommon", refs);
    Assert.Contains("Grasshopper", refs);
  }

  [Fact]
  public void EveryMethodUsedByTheMcpServerIsRegistered()
  {
    var sources = Directory.GetFiles(Path.Combine(Root, "src/RhinoMcpBridge"), "*.cs", SearchOption.AllDirectories)
      .Concat(Directory.GetFiles(Path.Combine(Root, "../shared/McpBridge.Transport"), "*.cs"))
      .Select(File.ReadAllText).Aggregate((a, b) => a + b);
    var registered = new HashSet<string>(Regex.Matches(sources, "(?:Register\\(|\\[)\"([a-z]+\\.[a-z_]+)\"").Select(m => m.Groups[1].Value));

    // Both connectors: the Rhino Grasshopper connector and the Fusion connector built on it.
    var serverSources = new[] { "../rhino-grasshopper-mcp/src", "../fusion-rga-mcp/src" }
      .Select(d => Path.GetFullPath(Path.Combine(Root, d))).Where(Directory.Exists).ToList();
    Assert.Equal(2, serverSources.Count);
    var used = serverSources.SelectMany(d => Directory.GetFiles(d, "*.ts", SearchOption.AllDirectories))
      .SelectMany(f => Regex.Matches(File.ReadAllText(f), "call(?:<[^>]*>)?\\(\\s*\"([a-z]+\\.[a-z_]+)\"").Select(m => m.Groups[1].Value))
      .Distinct().OrderBy(x => x).ToList();

    Assert.NotEmpty(used);
    var missing = used.Where(m => !registered.Contains(m)).ToList();
    Assert.True(missing.Count == 0, "Methods called by the MCP server but not registered in the bridge: " + string.Join(", ", missing));
  }
}
