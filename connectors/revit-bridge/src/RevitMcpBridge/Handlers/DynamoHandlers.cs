using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;
using RevitMcpBridge.App;
using RevitMcpBridge.Core;

namespace RevitMcpBridge.Handlers
{
  /// <summary>
  /// Runs Dynamo graphs the way Dynamo Player does, without showing Dynamo:
  ///   1. DynamoRevit.ExecuteCommand with journal keys (no UI, open the .dyn in manual mode,
  ///      keep the model alive and reuse the open workspace so element bindings survive);
  ///   2. input values set with UpdateModelValueCommand (Player's mechanism);
  ///   3. the graph evaluated synchronously inside Revit's API context, then the output and
  ///      watch nodes read back as JSON.
  /// Dynamo is reached by reflection: one build works with Dynamo 2.x (Revit 2022–2024) and
  /// 3.x (Revit 2025–2026), and the add-in still loads when Dynamo is not installed.
  /// </summary>
  public static class DynamoHandlers
  {
    public static void Register(RpcDispatcher d)
    {
      d.Register("dynamo.status", Status);
      d.Register("dynamo.run", Run);
      d.Register("dynamo.get_workspace", GetWorkspace);
    }

    private const BindingFlags Any = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
    private static JObject _lastRun;

    // ------------------------------------------------------------------ reflection helpers

    private static Assembly Loaded(string name) => AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a =>
    {
      try { return string.Equals(a.GetName().Name, name, StringComparison.OrdinalIgnoreCase); } catch { return false; }
    });

    private static Assembly DynamoRevitAssembly()
    {
      var asm = Loaded("DynamoRevitDS");
      if (asm != null) return asm;
      // Dynamo for Revit ships in <Revit>\AddIns\DynamoForRevit\Revit; it is normally loaded at start-up.
      try
      {
        var revitDir = Path.GetDirectoryName(Process.GetCurrentProcess().MainModule.FileName);
        var candidate = Path.Combine(revitDir ?? "", "AddIns", "DynamoForRevit", "Revit", "DynamoRevitDS.dll");
        if (File.Exists(candidate)) return Assembly.LoadFrom(candidate);
      }
      catch
      {
        // not available
      }
      return null;
    }

    private static object Get(object target, string name)
    {
      if (target == null) return null;
      var type = target as Type ?? target.GetType();
      var instance = target is Type ? null : target;
      for (var t = type; t != null; t = t.BaseType)
      {
        var prop = t.GetProperty(name, Any | BindingFlags.DeclaredOnly);
        if (prop != null && prop.GetIndexParameters().Length == 0) return prop.GetValue(instance);
        var field = t.GetField(name, Any | BindingFlags.DeclaredOnly);
        if (field != null) return field.GetValue(instance);
      }
      return null;
    }

    private static object Call(object target, string name, params object[] args)
    {
      var type = target as Type ?? target.GetType();
      var instance = target is Type ? null : target;
      var method = type.GetMethods(Any).FirstOrDefault(m => m.Name == name && m.GetParameters().Length == args.Length)
                   ?? throw new MissingMethodException(type.FullName, name);
      try
      {
        return method.Invoke(instance, args);
      }
      catch (TargetInvocationException ex) when (ex.InnerException != null)
      {
        throw ex.InnerException;
      }
    }

    private static Type DynamoRevitType(Assembly asm) => asm.GetType("Dynamo.Applications.DynamoRevit");

    private static object Model(Assembly asm) => Get(DynamoRevitType(asm), "RevitDynamoModel");

    private static string State(Assembly asm) => Get(DynamoRevitType(asm), "ModelState")?.ToString() ?? "Unknown";

    /// <summary>Whether Dynamo for Revit is present and running (used by bridge.info).</summary>
    public static JObject Availability()
    {
      try
      {
        var asm = Loaded("DynamoRevitDS");
        if (asm == null) return new JObject { ["available"] = false, ["reason"] = "Dynamo for Revit is not loaded in this Revit session." };
        var core = Loaded("DynamoCore");
        return new JObject
        {
          ["available"] = true,
          ["dynamo_revit_version"] = asm.GetName().Version?.ToString(),
          ["dynamo_core_version"] = core?.GetName().Version?.ToString(),
          ["state"] = State(asm),
        };
      }
      catch (Exception ex)
      {
        return new JObject { ["available"] = false, ["reason"] = ex.Message };
      }
    }

    // ------------------------------------------------------------------ dynamo.status / get_workspace

    private static JToken Status(JObject p)
    {
      var o = Availability();
      var asm = Loaded("DynamoRevitDS");
      var model = asm != null ? Model(asm) : null;
      var ws = model != null ? Get(model, "CurrentWorkspace") : null;
      if (ws != null)
      {
        o["workspace"] = new JObject
        {
          ["file"] = Get(ws, "FileName") as string,
          ["name"] = Get(ws, "Name") as string,
          ["nodes"] = (Get(ws, "Nodes") as IEnumerable)?.Cast<object>().Count() ?? 0,
          ["run_type"] = Get(Get(ws, "RunSettings"), "RunType")?.ToString(),
          ["evaluation_count"] = Convert.ToInt64(Get(ws, "EvaluationCount") ?? 0L),
          ["has_run_without_crash"] = Get(ws, "HasRunWithoutCrash") as bool?,
        };
      }
      if (_lastRun != null) o["last_run"] = _lastRun;
      return o;
    }

    private static JToken GetWorkspace(JObject p)
    {
      var asm = Loaded("DynamoRevitDS") ?? throw Unavailable();
      var model = Model(asm) ?? throw RpcException.NotFound("Dynamo has not been started in this session: run a graph first (dynamo.run).");
      var ws = Get(model, "CurrentWorkspace");
      return new JObject
      {
        ["file"] = Get(ws, "FileName") as string,
        ["nodes"] = Nodes(ws, RArgs.Strings(RArgs.Get(p, "nodes", "outputs")), RArgs.Bool(p, "all", false), RArgs.Int(p, "max_items", 50)),
      };
    }

    private static RpcException Unavailable()
      => new RpcException(RpcErrorCodes.GrasshopperUnavailable, "Dynamo for Revit is not available in this Revit session (is Dynamo installed for this Revit version?).");

    // ------------------------------------------------------------------ dynamo.run

    /// <summary>
    /// params: path (.dyn), inputs ({guid|name: value} or [{id|name, value}]), outputs (node
    /// names to return; default: output and watch nodes), reload (re-read the file from disk),
    /// max_items (per list).
    /// </summary>
    private static JToken Run(JObject p)
    {
      var app = RevitContext.App;
      _ = RevitContext.Doc; // a project must be open
      var path = Path.GetFullPath(Environment.ExpandEnvironmentVariables(RArgs.Str(p, "path", required: true)));
      if (!File.Exists(path)) throw RpcException.NotFound("Graph not found: " + path);
      var asm = DynamoRevitAssembly() ?? throw Unavailable();
      var revitType = DynamoRevitType(asm) ?? throw Unavailable();
      var dataType = asm.GetType("Dynamo.Applications.DynamoRevitCommandData") ?? throw Unavailable();
      bool reload = RArgs.Bool(p, "reload", false);
      var sw = Stopwatch.StartNew();

      // 1. Start Dynamo without UI (first call) or reuse it, and open the graph in manual mode.
      var journal = new Dictionary<string, string>
      {
        ["dynShowUI"] = "false",
        ["dynAutomation"] = "false",
        ["dynPath"] = path,
        ["dynPathExecute"] = "false",
        ["dynForceManualRun"] = "true",
        ["dynModelShutDown"] = "false",
        ["dynPathCheckExisting"] = reload ? "false" : "true",
      };
      var data = Activator.CreateInstance(dataType);
      dataType.GetProperty("Application").SetValue(data, app);
      dataType.GetProperty("JournalData").SetValue(data, journal);
      var dynamoRevit = Activator.CreateInstance(revitType);
      var started = Call(dynamoRevit, "ExecuteCommand", data);
      if (started != null && started.ToString() != "Succeeded")
        throw RpcException.Failed("Dynamo did not start (" + started + "). Open Dynamo once manually to check its installation.");

      var model = Model(asm) ?? throw RpcException.Failed("Dynamo started but exposes no model.");
      var ws = Get(model, "CurrentWorkspace") ?? throw RpcException.Failed("Dynamo has no open workspace.");
      var file = Get(ws, "FileName") as string;
      if (!string.Equals(Path.GetFullPath(file ?? ""), path, StringComparison.OrdinalIgnoreCase))
      {
        throw RpcException.Failed("Dynamo did not open the graph (current workspace: '" + file + "'). " +
          "If Dynamo's window is open with unsaved changes, save or close that graph and retry.");
      }

      // 2. Inputs (Dynamo Player's UpdateModelValueCommand).
      var nodes = (Get(ws, "Nodes") as IEnumerable)?.Cast<object>().ToList() ?? new List<object>();
      var applied = new JArray();
      foreach (var (key, value) in Inputs(p))
      {
        var node = FindNode(nodes, key) ?? throw RpcException.NotFound("No node '" + key + "' in " + Path.GetFileName(path) + ".",
          new JObject { ["inputs"] = new JArray(nodes.Where(n => (Get(n, "IsSetAsInput") as bool?) == true).Select(n => (string)Get(n, "Name"))) });
        var guid = (Guid)Get(node, "GUID");
        var text = ValueText(value);
        var cmdType = FindType(model.GetType(), "UpdateModelValueCommand") ?? throw RpcException.Failed("This Dynamo version has no UpdateModelValueCommand.");
        var cmd = Activator.CreateInstance(cmdType, guid, "Value", text);
        Call(model, "ExecuteCommand", cmd);
        applied.Add(new JObject { ["node"] = Get(node, "Name") as string, ["id"] = guid.ToString(), ["value"] = text });
      }

      // 3. Evaluate synchronously inside this API call.
      var homeWs = ws;
      long before = Convert.ToInt64(Get(homeWs, "EvaluationCount") ?? 0L);
      var scheduler = Get(model, "Scheduler");
      object previousMode = scheduler != null ? Get(scheduler, "ProcessMode") : null;
      try
      {
        if (scheduler != null)
        {
          int guard = 0;
          while ((Get(scheduler, "HasPendingTasks") as bool?) == true && guard++ < 10000) Call(scheduler, "ProcessNextTask", false);
          var modeProp = scheduler.GetType().GetProperty("ProcessMode");
          modeProp?.SetValue(scheduler, Enum.Parse(modeProp.PropertyType, "Synchronous"));
        }
        var runSettings = Get(homeWs, "RunSettings");
        if ((Get(runSettings, "RunEnabled") as bool?) == false)
          throw RpcException.Failed("Dynamo refuses to run this graph now (run disabled: a run may already be in progress).");
        Call(homeWs, "Run");
      }
      finally
      {
        if (scheduler != null && previousMode != null) scheduler.GetType().GetProperty("ProcessMode")?.SetValue(scheduler, previousMode);
      }
      long after = Convert.ToInt64(Get(homeWs, "EvaluationCount") ?? 0L);

      var outputs = Nodes(homeWs, RArgs.Strings(RArgs.Get(p, "outputs")), false, RArgs.Int(p, "max_items", 50));
      var problems = new JArray(outputs.OfType<JObject>().Where(o => o["messages"] != null && (string)o["state"] != "Active"));
      var errors = nodes.Where(n => { var s = Get(n, "State")?.ToString(); return s == "Error" || s == "AstBuildBroken"; })
        .Select(n => (JToken)new JObject { ["node"] = Get(n, "Name") as string, ["id"] = Get(n, "GUID")?.ToString(), ["messages"] = Messages(n) }).ToList();
      var warnings = nodes.Where(n => { var s = Get(n, "State")?.ToString(); return s == "Warning" || s == "PersistentWarning"; })
        .Select(n => (JToken)new JObject { ["node"] = Get(n, "Name") as string, ["id"] = Get(n, "GUID")?.ToString(), ["messages"] = Messages(n) }).ToList();

      var result = new JObject
      {
        ["path"] = path,
        ["evaluated"] = after > before,
        ["evaluation_count"] = after,
        ["duration_s"] = Math.Round(sw.Elapsed.TotalSeconds, 2),
        ["inputs_applied"] = applied,
        ["outputs"] = outputs,
        ["errors"] = new JArray(errors),
        ["warnings"] = new JArray(warnings.Take(50)),
        ["dynamo_state"] = State(asm),
      };
      _lastRun = new JObject
      {
        ["path"] = path,
        ["time"] = DateTime.UtcNow.ToString("o"),
        ["evaluated"] = after > before,
        ["errors"] = errors.Count,
        ["warnings"] = warnings.Count,
        ["duration_s"] = result["duration_s"],
      };
      if (errors.Count > 0 && RArgs.Bool(p, "fail_on_error", false))
        throw RpcException.Failed(errors.Count + " node(s) failed in " + Path.GetFileName(path) + ".", result);
      return result;
    }

    private static Type FindType(Type modelType, string nested)
    {
      for (var t = modelType; t != null; t = t.BaseType)
      {
        var n = t.GetNestedType(nested, BindingFlags.Public | BindingFlags.NonPublic);
        if (n != null) return n;
      }
      return null;
    }

    private static IEnumerable<(string key, JToken value)> Inputs(JObject p)
    {
      var t = p["inputs"];
      if (t is JObject o)
      {
        foreach (var kv in o) yield return (kv.Key, kv.Value);
      }
      else if (t is JArray arr)
      {
        foreach (var item in arr.OfType<JObject>())
        {
          var key = RArgs.Str(item, "id") ?? RArgs.Str(item, "name") ?? throw RpcException.InvalidParams("Each input needs 'id' or 'name'.");
          yield return (key, item["value"]);
        }
      }
    }

    private static object FindNode(List<object> nodes, string key)
    {
      if (Guid.TryParse(key, out var g)) return nodes.FirstOrDefault(n => (Guid)Get(n, "GUID") == g);
      var byName = nodes.Where(n => string.Equals(Get(n, "Name") as string, key, StringComparison.OrdinalIgnoreCase)).ToList();
      return byName.FirstOrDefault(n => (Get(n, "IsSetAsInput") as bool?) == true) ?? byName.FirstOrDefault();
    }

    /// <summary>Values as Dynamo's UpdateModelValue expects them (invariant culture, lowercase booleans).</summary>
    public static string ValueText(JToken value)
    {
      if (value == null || value.Type == JTokenType.Null) return "";
      switch (value.Type)
      {
        case JTokenType.Boolean: return (bool)value ? "true" : "false";
        case JTokenType.Integer: return ((long)value).ToString(CultureInfo.InvariantCulture);
        case JTokenType.Float: return ((double)value).ToString("R", CultureInfo.InvariantCulture);
        case JTokenType.String: return (string)value;
        default: return value.ToString(Newtonsoft.Json.Formatting.None);
      }
    }

    // ------------------------------------------------------------------ node values

    private static JArray Messages(object node)
    {
      var list = new JArray();
      if (Get(node, "NodeInfos") is IEnumerable infos)
      {
        foreach (var info in infos) list.Add(Get(info, "Message") as string ?? info.ToString());
      }
      else if (Get(node, "ToolTipText") is string tip && !string.IsNullOrEmpty(tip))
      {
        list.Add(tip);
      }
      return list;
    }

    /// <summary>Output nodes, watch nodes, or the named ones; every node when all=true.</summary>
    private static JArray Nodes(object ws, List<string> names, bool all, int maxItems)
    {
      var nodes = (Get(ws, "Nodes") as IEnumerable)?.Cast<object>().ToList() ?? new List<object>();
      IEnumerable<object> selected;
      if (names.Count > 0)
        selected = nodes.Where(n => names.Any(k => string.Equals(k, Get(n, "Name") as string, StringComparison.OrdinalIgnoreCase) || string.Equals(k, Get(n, "GUID")?.ToString(), StringComparison.OrdinalIgnoreCase)));
      else if (all)
        selected = nodes;
      else
        selected = nodes.Where(n => (Get(n, "IsSetAsOutput") as bool?) == true || n.GetType().Name.StartsWith("Watch", StringComparison.Ordinal));
      var list = new JArray();
      foreach (var n in selected.Take(500))
      {
        var o = new JObject
        {
          ["name"] = Get(n, "Name") as string,
          ["id"] = Get(n, "GUID")?.ToString(),
          ["node_type"] = n.GetType().Name,
          ["state"] = Get(n, "State")?.ToString(),
          ["is_input"] = Get(n, "IsSetAsInput") as bool?,
          ["is_output"] = Get(n, "IsSetAsOutput") as bool?,
        };
        try
        {
          int budget = Math.Max(10, maxItems * 20);
          o["value"] = Mirror(Get(n, "CachedValue"), maxItems, 0, ref budget);
        }
        catch (Exception ex)
        {
          o["value_error"] = ex.Message;
        }
        var messages = Messages(n);
        if (messages.Count > 0) o["messages"] = messages;
        list.Add(o);
      }
      return list;
    }

    private static JToken Mirror(object md, int maxItems, int depth, ref int budget)
    {
      if (md == null || budget-- <= 0) return JValue.CreateNull();
      if ((Get(md, "IsNull") as bool?) == true) return JValue.CreateNull();
      if ((Get(md, "IsCollection") as bool?) == true)
      {
        var arr = new JArray();
        var items = (Call(md, "GetElements") as IEnumerable)?.Cast<object>().ToList() ?? new List<object>();
        foreach (var child in items.Take(maxItems)) arr.Add(depth > 6 ? (JToken)"…" : Mirror(child, maxItems, depth + 1, ref budget));
        if (items.Count > maxItems) arr.Add($"… {items.Count - maxItems} more");
        return arr;
      }
      return Plain(Get(md, "Data"), maxItems, depth, ref budget);
    }

    /// <summary>Dynamo values → JSON: numbers, text, Revit elements, points, dictionaries, lists.</summary>
    private static JToken Plain(object data, int maxItems, int depth, ref int budget)
    {
      switch (data)
      {
        case null: return JValue.CreateNull();
        case string s: return s;
        case bool b: return b;
        case int i: return i;
        case long l: return l;
        case double d: return double.IsNaN(d) || double.IsInfinity(d) ? (JToken)d.ToString(CultureInfo.InvariantCulture) : Math.Round(d, 6);
        case float f: return Math.Round(f, 6);
      }
      var type = data.GetType();
      var ns = type.Namespace ?? "";
      if (ns.StartsWith("Revit.Elements", StringComparison.Ordinal))
      {
        var o = new JObject { ["element_id"] = Get(data, "Id")?.ToString(), ["revit_class"] = type.Name };
        try { o["name"] = Get(data, "Name") as string; } catch { /* some wrappers throw */ }
        return o;
      }
      if (ns.StartsWith("Autodesk.DesignScript.Geometry", StringComparison.Ordinal))
      {
        if (type.Name == "Point" || type.Name == "Vector")
          return new JArray(Math.Round(Convert.ToDouble(Get(data, "X")), 6), Math.Round(Convert.ToDouble(Get(data, "Y")), 6), Math.Round(Convert.ToDouble(Get(data, "Z")), 6));
        return new JObject { ["geometry"] = type.Name, ["text"] = Truncate(data.ToString(), 200) };
      }
      if (type.Name == "Dictionary" && Get(data, "Keys") is IEnumerable keys)
      {
        var o = new JObject();
        foreach (var k in keys.Cast<object>().Take(maxItems))
        {
          object v = null;
          try { v = Call(data, "ValueAtKey", k); } catch { /* keep null */ }
          o[k.ToString()] = depth > 6 ? (JToken)"…" : Plain(v, maxItems, depth + 1, ref budget);
        }
        return o;
      }
      if (data is IEnumerable seq)
      {
        var arr = new JArray();
        foreach (var item in seq.Cast<object>().Take(maxItems)) arr.Add(depth > 6 ? (JToken)"…" : Plain(item, maxItems, depth + 1, ref budget));
        return arr;
      }
      return Truncate(data.ToString(), 500);
    }

    private static string Truncate(string s, int max) => s == null ? null : (s.Length <= max ? s : s.Substring(0, max) + "…");
  }
}
