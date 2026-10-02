using System;
using System.IO;
using System.Linq;
using System.Reflection;

namespace RhinoMcpBridge.Plugin
{
  /// <summary>
  /// Loads Newtonsoft.Json from the copy embedded in the .rhp when Rhino does not provide it,
  /// so the plug-in installs as a single file. Must run before any JSON type is used.
  /// </summary>
  internal static class EmbeddedAssemblies
  {
    private const string ResourcePrefix = "RhinoMcpBridge.Embedded.";
    private static bool _installed;

    public static void Install()
    {
      if (_installed) return;
      _installed = true;
      AppDomain.CurrentDomain.AssemblyResolve += Resolve;
    }

    private static Assembly Resolve(object sender, ResolveEventArgs args)
    {
      var name = new AssemblyName(args.Name).Name;
      if (name != "Newtonsoft.Json") return null;

      var loaded = AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a => a.GetName().Name == name);
      if (loaded != null) return loaded;

      using (var stream = typeof(EmbeddedAssemblies).Assembly.GetManifestResourceStream(ResourcePrefix + name + ".dll"))
      {
        if (stream == null) return null;
        using (var ms = new MemoryStream())
        {
          stream.CopyTo(ms);
          return Assembly.Load(ms.ToArray());
        }
      }
    }
  }
}
