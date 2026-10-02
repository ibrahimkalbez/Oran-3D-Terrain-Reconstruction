using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Linq;
using Grasshopper;
using Grasshopper.GUI.Base;
using Grasshopper.GUI.Canvas;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Data;
using Grasshopper.Kernel.Parameters;
using Grasshopper.Kernel.Special;
using Grasshopper.Kernel.Types;
using Grasshopper.Plugin;
using Newtonsoft.Json.Linq;
using Rhino;
using Rhino.DocObjects;
using Rhino.FileIO;
using Rhino.Geometry;
using RhinoMcpBridge.Core;
using RhinoMcpBridge.Transport;
using Environment = System.Environment;

namespace RhinoMcpBridge.Handlers
{
  /// <summary>
  /// Grasshopper side of the bridge.
  ///
  /// Parameter discovery convention (see docs/GRASSHOPPER_CONVENTIONS.md):
  ///   inputs  = number sliders, toggles, value lists, colour swatches, unconnected panels,
  ///             and unconnected floating parameters holding a value (Number, Integer, Text…);
  ///   outputs = parameters/panels whose nickname starts with "OUT" or that sit in a group named
  ///             OUTPUT(S) / RESULT(S) / SORTIE(S) / RESULTAT(S). When none is tagged, terminal
  ///             floating parameters and connected panels are used.
  /// Names are the object nicknames; unnamed sliders get "Component.Input" from what they feed.
  /// </summary>
  public static class GhBridge
  {
    // ================================================================== documents

    private static GH_RhinoScriptInterface Script()
    {
      var gh = RhinoApp.GetPlugInObject("Grasshopper") as GH_RhinoScriptInterface;
      if (gh == null)
        throw new RpcException(RpcErrorCodes.GrasshopperUnavailable, "Grasshopper is not available in this Rhino.");
      return gh;
    }

    private static void EnsureLoaded()
    {
      var gh = Script();
      if (!gh.IsEditorLoaded()) gh.LoadEditor();
      if (Instances.DocumentServer == null)
        throw new RpcException(RpcErrorCodes.GrasshopperUnavailable, "Grasshopper did not start.");
    }

    private static IEnumerable<GH_Document> Documents()
    {
      var server = Instances.DocumentServer;
      if (server == null) yield break;
      for (int i = 0; i < server.DocumentCount; i++) yield return server[i];
    }

    private static GH_Document ActiveDocument() => Instances.ActiveCanvas?.Document;

    public static GH_Document ResolveDocument(JObject p, bool required = true)
    {
      EnsureLoaded();
      var key = Args.Str(p, "definition") ?? Args.Str(p, "document");
      GH_Document doc = null;
      if (!string.IsNullOrWhiteSpace(key))
      {
        key = key.Trim();
        var docs = Documents().ToList();
        doc = docs.FirstOrDefault(d => Guid.TryParse(key, out var g) && d.DocumentID == g)
              ?? docs.FirstOrDefault(d => !string.IsNullOrEmpty(d.FilePath) && SamePath(d.FilePath, key))
              ?? docs.FirstOrDefault(d => string.Equals(Path.GetFileName(d.FilePath ?? ""), key, StringComparison.OrdinalIgnoreCase))
              ?? docs.FirstOrDefault(d => string.Equals(Path.GetFileNameWithoutExtension(d.FilePath ?? ""), key, StringComparison.OrdinalIgnoreCase))
              ?? docs.FirstOrDefault(d => string.Equals(d.DisplayName, key, StringComparison.OrdinalIgnoreCase));
        if (doc == null && required)
          throw RpcException.NotFound("Grasshopper definition '" + key + "' is not open.",
            new JObject { ["open"] = new JArray(docs.Select(DocInfo)) });
      }
      else
      {
        doc = ActiveDocument() ?? Documents().FirstOrDefault();
      }
      if (doc == null && required)
        throw RpcException.NotFound("No Grasshopper definition is open. Call grasshopper_open_definition first.");
      return doc;
    }

    private static bool SamePath(string a, string b)
    {
      try { return string.Equals(Path.GetFullPath(a), Path.GetFullPath(b), StringComparison.OrdinalIgnoreCase); }
      catch { return false; }
    }

    private static JObject DocInfo(GH_Document doc) => new JObject
    {
      ["id"] = doc.DocumentID.ToString(),
      ["name"] = doc.DisplayName,
      ["path"] = doc.FilePath,
      ["object_count"] = doc.ObjectCount,
      ["enabled"] = doc.Enabled,
      ["active"] = ActiveDocument() == doc,
      ["modified"] = doc.IsModified,
    };

    public static JToken Status()
    {
      var gh = Script();
      bool loaded = gh.IsEditorLoaded();
      var o = new JObject
      {
        ["loaded"] = loaded,
        ["editor_visible"] = loaded && gh.IsEditorVisible(),
        ["solver_enabled"] = GH_Document.EnableSolutions,
      };
      o["definitions"] = loaded ? new JArray(Documents().Select(DocInfo)) : new JArray();
      return o;
    }

    public static JToken OpenDefinition(JObject p)
    {
      EnsureLoaded();
      var path = Path.GetFullPath(Environment.ExpandEnvironmentVariables(Args.Str(p, "path", required: true)));
      if (!File.Exists(path)) throw RpcException.NotFound("File not found: " + path);
      var ext = Path.GetExtension(path).ToLowerInvariant();
      if (ext != ".gh" && ext != ".ghx") throw RpcException.InvalidParams("Expected a .gh or .ghx file.");

      var existing = Documents().FirstOrDefault(d => !string.IsNullOrEmpty(d.FilePath) && SamePath(d.FilePath, path));
      GH_Document doc;
      bool reloaded = false;
      if (existing != null && !Args.Bool(p, "reload", false))
      {
        doc = existing;
      }
      else
      {
        if (existing != null)
        {
          Instances.DocumentServer.RemoveDocument(existing);
          reloaded = true;
        }
        var io = new GH_DocumentIO();
        if (!io.Open(path) || io.Document == null) throw RpcException.Failed("Grasshopper could not read " + path);
        doc = io.Document;
        doc.FilePath = path;
        Instances.DocumentServer.AddDocument(doc);
      }

      if (Instances.ActiveCanvas != null) Instances.ActiveCanvas.Document = doc;
      doc.Enabled = true;
      if (Args.Bool(p, "show_editor", false)) Script().ShowEditor();

      var solve = SolveDocument(doc, Args.Bool(p, "solve", true), true);
      var result = DocInfo(doc);
      result["reloaded"] = reloaded;
      result["solution"] = solve;
      result["parameters"] = ParametersJson(doc, false);
      return result;
    }

    public static JToken CloseDefinition(JObject p)
    {
      var doc = ResolveDocument(p);
      var info = DocInfo(doc);
      if (doc.IsModified && !Args.Bool(p, "discard_changes", false))
        throw RpcException.InvalidParams("The definition has unsaved changes: save it or pass discard_changes=true.");
      doc.Enabled = false;
      Instances.DocumentServer.RemoveDocument(doc);
      if (Instances.ActiveCanvas != null && Instances.ActiveCanvas.Document == doc)
        Instances.ActiveCanvas.Document = Documents().FirstOrDefault();
      RhinoDoc.ActiveDoc?.Views.Redraw();
      return new JObject { ["closed"] = info };
    }

    // ================================================================== definition structure

    public static string Kind(IGH_DocumentObject obj)
    {
      switch (obj)
      {
        case GH_NumberSlider _: return "slider";
        case GH_BooleanToggle _: return "toggle";
        case GH_ValueList _: return "value_list";
        case GH_Panel _: return "panel";
        case GH_ColourSwatch _: return "colour_swatch";
        case GH_ButtonObject _: return "button";
        case GH_Group _: return "group";
        case GH_Scribble _: return "scribble";
        case GH_Cluster _: return "cluster";
        case IGH_Component _: return "component";
        case IGH_Param _: return "parameter";
        default: return "object";
      }
    }

    private static JObject ParamSignature(IGH_Param prm, bool withSources)
    {
      var o = new JObject
      {
        ["name"] = prm.Name,
        ["nickname"] = prm.NickName,
        ["type"] = prm.TypeName,
        ["access"] = prm.Access.ToString().ToLowerInvariant(),
        ["optional"] = prm.Optional,
        ["data_count"] = prm.VolatileDataCount,
      };
      if (withSources)
      {
        o["sources"] = new JArray(prm.Sources.Select(s => (JToken)Endpoint(s)));
        o["recipient_count"] = prm.Recipients.Count;
      }
      return o;
    }

    /// <summary>"Component.Output" reference of a parameter, with the owner id.</summary>
    private static JObject Endpoint(IGH_Param prm)
    {
      var owner = prm.Attributes?.GetTopLevel?.DocObject;
      return new JObject
      {
        ["object_id"] = (owner ?? prm).InstanceGuid.ToString(),
        ["object"] = owner != null && owner != prm ? Label(owner) : Label(prm),
        ["param"] = owner != null && owner != prm ? prm.NickName : null,
      };
    }

    public static JToken GetDefinition(JObject p)
    {
      var doc = ResolveDocument(p);
      int max = Args.Int(p, "max_objects", 400);
      bool includeGroups = Args.Bool(p, "include_groups", true);
      var objects = new JArray();
      var counts = new Dictionary<string, int>();
      foreach (var obj in doc.Objects)
      {
        var kind = Kind(obj);
        counts[kind] = counts.TryGetValue(kind, out var n) ? n + 1 : 1;
        if (objects.Count >= max) continue;
        if (kind == "scribble" || (kind == "group" && !includeGroups)) continue;
        objects.Add(ObjectJson(doc, obj));
      }

      var connections = new JArray();
      foreach (var obj in doc.Objects)
      {
        foreach (var input in InputParams(obj))
        {
          foreach (var src in input.Sources)
          {
            var s = Endpoint(src);
            var t = Endpoint(input);
            connections.Add(new JObject
            {
              ["from"] = s["object"] + (s["param"].Type == JTokenType.Null ? "" : "." + s["param"]),
              ["from_id"] = s["object_id"],
              ["to"] = t["object"] + (t["param"].Type == JTokenType.Null ? "" : "." + t["param"]),
              ["to_id"] = t["object_id"],
            });
            if (connections.Count >= 2000) break;
          }
        }
      }

      var result = DocInfo(doc);
      result["object_counts"] = JObject.FromObject(counts);
      result["objects"] = objects;
      result["objects_truncated"] = doc.ObjectCount > objects.Count;
      result["connections"] = connections;
      result["parameters"] = ParametersJson(doc, false);
      return result;
    }

    private static IEnumerable<IGH_Param> InputParams(IGH_DocumentObject obj)
    {
      if (obj is IGH_Component c) return c.Params.Input;
      if (obj is IGH_Param prm) return new[] { prm };
      return Enumerable.Empty<IGH_Param>();
    }

    private static JObject ObjectJson(GH_Document doc, IGH_DocumentObject obj)
    {
      var o = new JObject
      {
        ["id"] = obj.InstanceGuid.ToString(),
        ["kind"] = Kind(obj),
        ["name"] = obj.Name,
        ["nickname"] = obj.NickName,
        ["label"] = Label(obj),
        ["category"] = obj.Category,
        ["subcategory"] = obj.SubCategory,
        ["position"] = obj.Attributes != null ? new JArray(Math.Round(obj.Attributes.Pivot.X), Math.Round(obj.Attributes.Pivot.Y)) : null,
      };
      if (obj is IGH_ActiveObject active)
      {
        o["enabled"] = !active.Locked;
        var level = active.RuntimeMessageLevel;
        if (level == GH_RuntimeMessageLevel.Error || level == GH_RuntimeMessageLevel.Warning)
        {
          o["message_level"] = level.ToString().ToLowerInvariant();
          o["messages"] = new JArray(active.RuntimeMessages(level));
        }
      }
      if (obj is IGH_PreviewObject po && po.IsPreviewCapable) o["preview"] = !po.Hidden;
      switch (obj)
      {
        case IGH_Component comp:
          o["inputs"] = new JArray(comp.Params.Input.Select(x => (JToken)ParamSignature(x, true)));
          o["outputs"] = new JArray(comp.Params.Output.Select(x => (JToken)ParamSignature(x, false)));
          break;
        case GH_Group grp:
          o["members"] = new JArray(grp.ObjectIDs.Select(g => g.ToString()));
          break;
        case IGH_Param prm:
          o["type"] = prm.TypeName;
          o["sources"] = new JArray(prm.Sources.Select(s => (JToken)Endpoint(s)));
          o["recipient_count"] = prm.Recipients.Count;
          o["data_count"] = prm.VolatileDataCount;
          var value = InputValue(obj);
          if (value != null) o["value"] = value;
          break;
      }
      return o;
    }

    // ================================================================== parameter discovery

    private static readonly string[] OutputGroupNames = { "output", "outputs", "result", "results", "sortie", "sorties", "resultat", "resultats", "résultat", "résultats", "metrics", "metriques", "métriques" };
    private static readonly string[] InputGroupNames = { "input", "inputs", "parameter", "parameters", "parametres", "paramètres", "entree", "entrees", "entrée", "entrées" };
    private static readonly HashSet<string> DefaultNicknames = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
      "", "Number Slider", "Slider", "Panel", "Boolean Toggle", "Toggle", "Value List", "List", "Colour Swatch", "Swatch",
      "Number", "Num", "Integer", "Int", "Text", "Txt", "Boolean", "Bool", "Point", "Pt", "Geometry", "Geo", "Brep", "Curve", "Crv",
      "Mesh", "Surface", "Srf", "Data", "Button",
    };

    public sealed class Param
    {
      public IGH_DocumentObject Object;
      public string Name;
      public string Kind;
      public bool IsInput;
    }

    /// <summary>Human label: meaningful nickname, else "Component.Input" for an unnamed slider.</summary>
    public static string Label(IGH_DocumentObject obj)
    {
      var nick = obj.NickName?.Trim() ?? "";
      if (!DefaultNicknames.Contains(nick) && nick.Length > 0) return nick;
      if (obj is IGH_Param prm && prm.Recipients.Count > 0)
      {
        var r = prm.Recipients[0];
        var owner = r.Attributes?.GetTopLevel?.DocObject;
        if (owner != null && owner != r) return (DefaultNicknames.Contains(owner.NickName ?? "") ? owner.Name : owner.NickName) + "." + r.Name;
        return r.NickName;
      }
      return nick.Length > 0 ? nick : obj.Name;
    }

    private static HashSet<Guid> GroupMembers(GH_Document doc, string[] names)
    {
      var set = new HashSet<Guid>();
      foreach (var grp in doc.Objects.OfType<GH_Group>())
      {
        var n = Normalize(grp.NickName);
        if (names.Any(x => n == Normalize(x) || n.StartsWith(Normalize(x))))
          foreach (var id in grp.ObjectIDs) set.Add(id);
      }
      return set;
    }

    private static bool Tagged(string nick, string prefix)
    {
      if (string.IsNullOrEmpty(nick)) return false;
      var n = nick.TrimStart();
      return n.StartsWith(prefix + "_", StringComparison.OrdinalIgnoreCase)
             || n.StartsWith(prefix + ":", StringComparison.OrdinalIgnoreCase)
             || n.StartsWith(prefix + " ", StringComparison.OrdinalIgnoreCase)
             || n.StartsWith(prefix + "-", StringComparison.OrdinalIgnoreCase);
    }

    public static List<Param> DiscoverInputs(GH_Document doc)
    {
      var inGroups = GroupMembers(doc, InputGroupNames);
      var list = new List<Param>();
      foreach (var obj in doc.Objects)
      {
        string kind = null;
        switch (obj)
        {
          case GH_NumberSlider _: kind = "slider"; break;
          case GH_BooleanToggle _: kind = "toggle"; break;
          case GH_ValueList _: kind = "value_list"; break;
          case GH_ColourSwatch _: kind = "colour_swatch"; break;
          case GH_Panel panel when panel.Sources.Count == 0: kind = "panel"; break;
          case IGH_Param prm when prm.Kind == GH_ParamKind.floating && prm.Sources.Count == 0 && !(obj is GH_Panel):
            if (PersistentKind(prm) != null && (prm.VolatileDataCount > 0 || Tagged(prm.NickName, "IN") || inGroups.Contains(prm.InstanceGuid)))
              kind = PersistentKind(prm);
            break;
        }
        if (kind != null) list.Add(new Param { Object = obj, Name = Label(obj), Kind = kind, IsInput = true });
      }
      return list;
    }

    private static string PersistentKind(IGH_Param prm)
    {
      switch (prm)
      {
        case Param_Number _: return "number";
        case Param_Integer _: return "integer";
        case Param_String _: return "text";
        case Param_Boolean _: return "boolean";
        case Param_Point _: return "point";
        case Param_Vector _: return "vector";
        case Param_Colour _: return "colour";
        case Param_FilePath _: return "file_path";
        default: return null;
      }
    }

    public static List<Param> DiscoverOutputs(GH_Document doc, out string mode)
    {
      var inGroups = GroupMembers(doc, OutputGroupNames);
      var tagged = new List<Param>();
      foreach (var obj in doc.Objects)
      {
        if (obj is IGH_Component comp)
        {
          // A component inside an OUTPUT group exposes all its outputs.
          bool compInGroup = inGroups.Contains(comp.InstanceGuid);
          foreach (var output in comp.Params.Output)
          {
            if (compInGroup || Tagged(output.NickName, "OUT"))
              tagged.Add(new Param { Object = output, Name = Tagged(output.NickName, "OUT") ? output.NickName : Label(comp) + "." + output.NickName, Kind = "output" });
          }
          continue;
        }
        if (obj is IGH_Param prm && (Tagged(prm.NickName, "OUT") || inGroups.Contains(prm.InstanceGuid)))
        {
          if (obj is GH_NumberSlider || obj is GH_BooleanToggle || obj is GH_ValueList) continue;
          tagged.Add(new Param { Object = obj, Name = Label(obj), Kind = obj is GH_Panel ? "panel" : "parameter" });
        }
      }
      if (tagged.Count > 0)
      {
        mode = "tagged";
        return tagged;
      }

      mode = "auto";
      var auto = new List<Param>();
      foreach (var obj in doc.Objects)
      {
        if (obj is GH_Panel panel && panel.Sources.Count > 0)
          auto.Add(new Param { Object = obj, Name = Label(obj), Kind = "panel" });
        else if (obj is IGH_Param prm && !(obj is GH_Panel) && prm.Kind == GH_ParamKind.floating && prm.Sources.Count > 0 && prm.Recipients.Count == 0)
          auto.Add(new Param { Object = obj, Name = Label(obj), Kind = "parameter" });
      }
      return auto;
    }

    private static JToken InputValue(IGH_DocumentObject obj)
    {
      switch (obj)
      {
        case GH_NumberSlider s: return Number((double)s.CurrentValue);
        case GH_BooleanToggle t: return t.Value;
        case GH_ValueList v: return v.FirstSelectedItem?.Name;
        case GH_ColourSwatch c: return J.Hex(c.SwatchColour);
        case GH_Panel panel: return panel.UserText;
        case IGH_Param prm when PersistentKind(prm) != null:
          var values = prm.VolatileData.AllData(true).Select(GooValue).Take(50).ToList();
          if (values.Count == 0) return JValue.CreateNull();
          return values.Count == 1 ? values[0] : new JArray(values);
      }
      return null;
    }

    private static JObject InputJson(Param prm)
    {
      var obj = prm.Object;
      var o = new JObject
      {
        ["name"] = prm.Name,
        ["id"] = obj.InstanceGuid.ToString(),
        ["kind"] = prm.Kind,
        ["value"] = InputValue(obj),
      };
      if (obj is GH_NumberSlider s)
      {
        o["min"] = Number((double)s.Slider.Minimum);
        o["max"] = Number((double)s.Slider.Maximum);
        o["decimals"] = s.Slider.DecimalPlaces;
        o["step_type"] = s.Slider.Type.ToString().ToLowerInvariant();
      }
      else if (obj is GH_ValueList vl)
      {
        o["items"] = new JArray(vl.ListItems.Select(i => (JToken)new JObject { ["name"] = i.Name, ["expression"] = i.Expression, ["selected"] = i.Selected }));
        o["list_mode"] = vl.ListMode.ToString().ToLowerInvariant();
      }
      if (obj is IGH_Param p2 && p2.Recipients.Count > 0)
        o["feeds"] = new JArray(p2.Recipients.Take(10).Select(r => (JToken)(Endpoint(r)["object"] + "." + r.Name)));
      return o;
    }

    private static JObject OutputJson(Param prm, int maxValues)
    {
      var param = (IGH_Param)prm.Object;
      var o = new JObject
      {
        ["name"] = prm.Name,
        ["id"] = param.InstanceGuid.ToString(),
        ["owner_id"] = (param.Attributes?.GetTopLevel?.DocObject ?? param).InstanceGuid.ToString(),
        ["kind"] = prm.Kind,
        ["type"] = param.TypeName,
        ["count"] = param.VolatileDataCount,
      };
      var preview = param.VolatileData.AllData(true).Take(maxValues).Select(GooValue).ToList();
      if (preview.Count > 0 && preview.All(v => v.Type != JTokenType.Object)) o["preview"] = new JArray(preview);
      return o;
    }

    private static JObject ParametersJson(GH_Document doc, bool includeOutputs)
    {
      var inputs = DiscoverInputs(doc);
      var o = new JObject
      {
        ["inputs"] = new JArray(inputs.Select(i => (JToken)InputJson(i))),
      };
      var dup = inputs.GroupBy(i => i.Name, StringComparer.OrdinalIgnoreCase).Where(g => g.Count() > 1).Select(g => g.Key).ToList();
      if (dup.Count > 0) o["duplicate_names"] = new JArray(dup);
      if (includeOutputs)
      {
        var outputs = DiscoverOutputs(doc, out var mode);
        o["outputs_mode"] = mode;
        o["outputs"] = new JArray(outputs.Select(x => (JToken)OutputJson(x, 5)));
      }
      return o;
    }

    public static JToken GetParameters(JObject p)
    {
      var doc = ResolveDocument(p);
      var result = ParametersJson(doc, Args.Bool(p, "include_outputs", true));
      result["definition"] = DocInfo(doc);
      return result;
    }

    // ================================================================== lookup

    private static string Normalize(string s)
      => new string((s ?? "").Where(char.IsLetterOrDigit).ToArray()).ToLowerInvariant();

    private static List<Param> Match(List<Param> candidates, string key)
    {
      if (Guid.TryParse(key, out var g))
        return candidates.Where(c => c.Object.InstanceGuid == g).ToList();
      var exact = candidates.Where(c => string.Equals(c.Name, key, StringComparison.OrdinalIgnoreCase)
                                     || string.Equals(c.Object.NickName, key, StringComparison.OrdinalIgnoreCase)).ToList();
      if (exact.Count > 0) return exact;
      var n = Normalize(key);
      return candidates.Where(c => Normalize(c.Name) == n || Normalize(c.Object.NickName) == n).ToList();
    }

    private static Param FindOne(List<Param> candidates, string key, string what)
    {
      var found = Match(candidates, key);
      if (found.Count == 1) return found[0];
      if (found.Count == 0)
        throw RpcException.NotFound($"No {what} named '{key}'.",
          new JObject { ["available"] = new JArray(candidates.Select(c => c.Name).Distinct()) });
      throw new RpcException(RpcErrorCodes.Ambiguous, $"'{key}' matches {found.Count} {what}s: use the id.",
        new JObject { ["candidates"] = new JArray(found.Select(c => (JToken)new JObject { ["name"] = c.Name, ["id"] = c.Object.InstanceGuid.ToString(), ["kind"] = c.Kind })) });
    }

    // ================================================================== set parameter

    public static JToken SetParameter(JObject p)
    {
      var doc = ResolveDocument(p);
      var inputs = DiscoverInputs(doc);
      var requests = new List<JObject>();
      if (p["parameters"] is JArray arr) requests.AddRange(arr.OfType<JObject>());
      else if (p["parameters"] is JObject map)
        foreach (var kv in map) requests.Add(new JObject { ["parameter"] = kv.Key, ["value"] = kv.Value });
      else requests.Add(p);

      // Resolve and validate everything before changing anything.
      var plan = new List<(Param target, JObject req)>();
      foreach (var req in requests)
      {
        var key = Args.Str(req, "parameter") ?? Args.Str(req, "name") ?? Args.Str(req, "id")
                  ?? throw RpcException.InvalidParams("Each change needs 'parameter' (name or id) and 'value'.");
        plan.Add((FindOne(inputs, key, "input parameter"), req));
      }

      var changes = new JArray();
      foreach (var (target, req) in plan)
        changes.Add(Apply(target, req));

      var result = new JObject { ["definition"] = doc.DisplayName, ["changes"] = changes };
      if (Args.Bool(p, "solve", true))
      {
        result["solution"] = SolveDocument(doc, false, true);
        result["outputs"] = new JArray(DiscoverOutputs(doc, out var mode).Take(30).Select(x => (JToken)OutputJson(x, 5)));
      }
      return result;
    }

    private static JObject Apply(Param target, JObject req)
    {
      var obj = target.Object;
      var mode = (Args.Str(req, "mode", "set") ?? "set").Trim().ToLowerInvariant();
      var valueToken = Args.Get(req, "value");
      if (valueToken == null && mode != "toggle") throw RpcException.InvalidParams($"'{target.Name}': 'value' is required.");
      var change = new JObject { ["name"] = target.Name, ["id"] = obj.InstanceGuid.ToString(), ["kind"] = target.Kind, ["old"] = InputValue(obj) };

      switch (obj)
      {
        case GH_NumberSlider s:
          {
            double current = (double)s.CurrentValue;
            double v = Args.ToDouble(valueToken, "value");
            double next;
            switch (mode)
            {
              case "set": next = v; break;
              case "add": case "delta": next = current + v; break;
              case "multiply": case "scale": next = current * v; break;
              case "percent": next = current * (1 + v / 100.0); break;
              default: throw RpcException.InvalidParams("Slider mode must be set, add, multiply or percent.");
            }
            var type = s.Slider.Type;
            if (type == GH_SliderAccuracy.Integer) next = Math.Round(next);
            else if (type == GH_SliderAccuracy.Even) next = 2 * Math.Round(next / 2);
            else if (type == GH_SliderAccuracy.Odd) next = 2 * Math.Floor(next / 2) + 1;
            else next = Math.Round(next, s.Slider.DecimalPlaces);

            double min = (double)s.Slider.Minimum, max = (double)s.Slider.Maximum;
            var onOut = (Args.Str(req, "on_out_of_range", "extend") ?? "extend").ToLowerInvariant();
            if (next < min || next > max)
            {
              if (onOut == "error")
                throw RpcException.InvalidParams($"'{target.Name}' = {next} is outside [{min}, {max}].");
              if (onOut == "clamp")
              {
                next = Math.Max(min, Math.Min(max, next));
                change["clamped"] = true;
              }
              else
              {
                if (next < min) s.Slider.Minimum = (decimal)next;
                if (next > max) s.Slider.Maximum = (decimal)next;
                change["range_extended"] = new JArray(Number((double)s.Slider.Minimum), Number((double)s.Slider.Maximum));
              }
            }
            s.SetSliderValue((decimal)next);
            s.ExpireSolution(false);
            break;
          }

        case GH_BooleanToggle t:
          t.Value = mode == "toggle" ? !t.Value : Args.Bool(req, "value", t.Value);
          t.ExpireSolution(false);
          break;

        case GH_ValueList vl:
          {
            int index = -1;
            var items = vl.ListItems;
            if (valueToken.Type == JTokenType.Integer && Args.Bool(req, "by_index", false))
              index = (int)valueToken;
            if (index < 0)
            {
              var key = valueToken.ToString();
              index = items.FindIndex(i => string.Equals(i.Name, key, StringComparison.OrdinalIgnoreCase));
              if (index < 0) index = items.FindIndex(i => string.Equals(i.Expression?.Trim('"'), key.Trim('"'), StringComparison.OrdinalIgnoreCase));
              if (index < 0 && valueToken.Type == JTokenType.Integer) index = (int)valueToken;
            }
            if (index < 0 || index >= items.Count)
              throw RpcException.InvalidParams($"'{target.Name}' has no item '{valueToken}'.",
                new JObject { ["items"] = new JArray(items.Select(i => i.Name)) });
            vl.SelectItem(index);
            vl.ExpireSolution(false);
            break;
          }

        case GH_ColourSwatch cs:
          cs.SwatchColour = Args.Colour(valueToken) ?? cs.SwatchColour;
          cs.ExpireSolution(false);
          break;

        case GH_Panel panel:
          panel.SetUserText(valueToken.Type == JTokenType.String ? (string)valueToken : valueToken.ToString(Newtonsoft.Json.Formatting.None));
          panel.ExpireSolution(false);
          break;

        case IGH_Param prm:
          SetPersistent(prm, valueToken, mode, target.Name);
          prm.ExpireSolution(false);
          break;

        default:
          throw RpcException.InvalidParams($"'{target.Name}' cannot be set.");
      }
      change["new"] = InputValue(obj);
      return change;
    }

    private static void SetPersistent(IGH_Param prm, JToken value, string mode, string name)
    {
      var values = value is JArray a ? a.ToList() : new List<JToken> { value };
      switch (prm)
      {
        case Param_Number num:
          {
            var current = num.PersistentData.AllData(true).OfType<GH_Number>().Select(x => x.Value).ToList();
            var next = values.Select((v, i) =>
            {
              double d = Args.ToDouble(v, name);
              double c = i < current.Count ? current[i] : (current.Count > 0 ? current[current.Count - 1] : 0);
              switch (mode)
              {
                case "add": return c + d;
                case "multiply": return c * d;
                case "percent": return c * (1 + d / 100.0);
                default: return d;
              }
            }).ToList();
            num.PersistentData.Clear();
            num.SetPersistentData(next.Select(x => new GH_Number(x)));
            return;
          }
        case Param_Integer integer:
          integer.PersistentData.Clear();
          integer.SetPersistentData(values.Select(v => new GH_Integer((int)Math.Round(Args.ToDouble(v, name)))));
          return;
        case Param_String text:
          text.PersistentData.Clear();
          text.SetPersistentData(values.Select(v => new GH_String(v.Type == JTokenType.String ? (string)v : v.ToString())));
          return;
        case Param_Boolean b:
          b.PersistentData.Clear();
          b.SetPersistentData(values.Select(v => new GH_Boolean(Args.Bool(new JObject { ["v"] = v }, "v", false))));
          return;
        case Param_Point pt:
          {
            var pts = value is JArray arr && arr.Count > 0 && (arr[0] is JArray || arr[0] is JObject) ? arr.ToList() : new List<JToken> { value };
            pt.PersistentData.Clear();
            pt.SetPersistentData(pts.Select(v => new GH_Point(Args.Point(v, name))));
            return;
          }
        case Param_Vector vec:
          vec.PersistentData.Clear();
          vec.SetPersistentData(new GH_Vector(Args.Vector(value, name)));
          return;
        case Param_Colour col:
          col.PersistentData.Clear();
          col.SetPersistentData(new GH_Colour(Args.Colour(value) ?? Color.Black));
          return;
        case Param_FilePath fp:
          fp.PersistentData.Clear();
          fp.SetPersistentData(new GH_String(value.ToString()));
          return;
      }
      throw RpcException.InvalidParams($"'{name}' ({prm.TypeName}) cannot be set from Claude.");
    }

    // ================================================================== solve

    public static JToken Solve(JObject p)
    {
      var doc = ResolveDocument(p);
      var result = SolveDocument(doc, Args.Bool(p, "expire_all", false), true);
      result["definition"] = doc.DisplayName;
      result["outputs"] = new JArray(DiscoverOutputs(doc, out _).Take(30).Select(x => (JToken)OutputJson(x, 5)));
      return result;
    }

    internal static JObject SolveDocument(GH_Document doc, bool expireAll, bool run)
    {
      var o = new JObject();
      if (!run)
      {
        o["solved"] = false;
        return o;
      }
      if (!GH_Document.EnableSolutions)
      {
        GH_Document.EnableSolutions = true;
        o["solver_was_disabled"] = true;
      }
      doc.Enabled = true;
      var sw = Stopwatch.StartNew();
      doc.NewSolution(expireAll, GH_SolutionMode.Silent);
      sw.Stop();
      o["solved"] = true;
      o["duration_ms"] = sw.ElapsedMilliseconds;
      o["state"] = doc.SolutionState.ToString().ToLowerInvariant();

      var errors = new JArray();
      var warnings = new JArray();
      foreach (var active in doc.ActiveObjects())
      {
        var level = active.RuntimeMessageLevel;
        if (level != GH_RuntimeMessageLevel.Error && level != GH_RuntimeMessageLevel.Warning) continue;
        var entry = new JObject
        {
          ["id"] = active.InstanceGuid.ToString(),
          ["object"] = Label(active),
          ["messages"] = new JArray(active.RuntimeMessages(level).Take(5)),
        };
        if (level == GH_RuntimeMessageLevel.Error) errors.Add(entry); else warnings.Add(entry);
      }
      o["errors"] = errors;
      o["warnings"] = warnings;
      RhinoDoc.ActiveDoc?.Views.Redraw();
      return o;
    }

    // ================================================================== results

    public static JToken GetResults(JObject p)
    {
      var doc = ResolveDocument(p);
      if (Args.Bool(p, "solve", false)) SolveDocument(doc, false, true);
      int maxItems = Math.Max(0, Math.Min(10000, Args.Int(p, "max_items", 50)));
      bool items = Args.Bool(p, "include_items", false);
      bool json = Args.Bool(p, "include_geometry_json", false);

      var selected = SelectOutputs(doc, p, out var mode);
      var outputs = new JArray();
      foreach (var prm in selected) outputs.Add(ResultJson(prm, maxItems, items, json));

      // Definition-wide metrics: every numeric output becomes a metric, and geometry totals are added.
      var metrics = new JObject();
      foreach (JObject o in outputs)
      {
        var name = o.Value<string>("name");
        if (o["number"] != null) metrics[name] = o["number"];
        else if (o["stats"] is JObject st && st["sum"] != null) metrics[name + ".sum"] = st["sum"];
        if (o["geometry"] is JObject g)
        {
          if (g.Value<double?>("total_area") > 0) metrics[name + ".area"] = g["total_area"];
          if (g.Value<double?>("total_volume") > 0) metrics[name + ".volume"] = g["total_volume"];
          if (g.Value<double?>("total_length") > 0) metrics[name + ".length"] = g["total_length"];
          metrics[name + ".count"] = g["count"];
        }
      }

      return new JObject
      {
        ["definition"] = DocInfo(doc),
        ["outputs_mode"] = mode,
        ["outputs"] = outputs,
        ["metrics"] = metrics,
        ["inputs"] = new JArray(DiscoverInputs(doc).Select(i => (JToken)new JObject { ["name"] = i.Name, ["value"] = InputValue(i.Object) })),
      };
    }

    /// <summary>Outputs named in p["outputs"] (names/ids, any parameter of the definition) or the discovered outputs.</summary>
    private static List<Param> SelectOutputs(GH_Document doc, JObject p, out string mode)
    {
      var discovered = DiscoverOutputs(doc, out mode);
      var names = Args.Strings(Args.Get(p, "outputs"));
      if (names.Count == 0) return discovered;
      mode = "explicit";

      var all = new List<Param>(discovered);
      foreach (var obj in doc.Objects)
      {
        if (obj is IGH_Component comp)
          foreach (var output in comp.Params.Output)
            all.Add(new Param { Object = output, Name = Label(comp) + "." + output.NickName, Kind = "output" });
        else if (obj is IGH_Param prm)
          all.Add(new Param { Object = obj, Name = Label(obj), Kind = obj is GH_Panel ? "panel" : "parameter" });
      }
      var result = new List<Param>();
      foreach (var n in names)
      {
        var found = Match(all, n);
        if (found.Count == 0)
        {
          // "Component.Output" written with the component's real name.
          found = all.Where(a => a.Object is IGH_Param ap && Normalize(Endpoint(ap)["object"] + "." + ap.NickName) == Normalize(n)
                                 || a.Object is IGH_Param bp && Normalize(Endpoint(bp)["object"] + "." + bp.Name) == Normalize(n)).ToList();
        }
        if (found.Count == 0)
          throw RpcException.NotFound("No output '" + n + "'.", new JObject { ["available"] = new JArray(discovered.Select(d => d.Name)) });
        if (!result.Any(r => r.Object == found[0].Object)) result.Add(found[0]);
      }
      return result;
    }

    private static JObject ResultJson(Param prm, int maxItems, bool includeItems, bool includeJson)
    {
      var param = (IGH_Param)prm.Object;
      var data = param.VolatileData;
      var o = new JObject
      {
        ["name"] = prm.Name,
        ["id"] = param.InstanceGuid.ToString(),
        ["type"] = param.TypeName,
        ["count"] = data.DataCount,
        ["branches"] = data.PathCount,
      };
      if (data.PathCount > 1)
      {
        o["tree"] = new JArray(data.Paths.Take(50).Select(path => (JToken)new JObject
        {
          ["path"] = path.ToString(),
          ["count"] = data.get_Branch(path)?.Count ?? 0,
        }));
      }

      var goos = data.AllData(true).ToList();
      var numbers = new List<double>();
      var texts = new List<string>();
      var totals = new GeometryInfo.Totals();
      var typeCounts = new Dictionary<string, int>();
      var itemJson = new JArray();
      foreach (var goo in goos)
      {
        var geom = ToGeometry(goo);
        if (geom != null)
        {
          totals.Add(geom);
          var k = GeometryInfo.Kind(geom);
          typeCounts[k] = typeCounts.TryGetValue(k, out var c) ? c + 1 : 1;
          if ((includeItems || includeJson) && itemJson.Count < maxItems) itemJson.Add(GeometryInfo.Describe(geom, includeItems, includeJson));
          continue;
        }
        var v = GooValue(goo);
        if (v.Type == JTokenType.Float || v.Type == JTokenType.Integer) numbers.Add((double)v);
        else if (v.Type == JTokenType.String)
        {
          var s = (string)v;
          if (double.TryParse(s.Trim().Replace(',', '.'), NumberStyles.Float, CultureInfo.InvariantCulture, out var d)) numbers.Add(d);
          else texts.Add(s);
        }
        if (itemJson.Count < maxItems) itemJson.Add(v);
      }

      if (numbers.Count == 1 && goos.Count == 1) o["number"] = Number(numbers[0]);
      if (numbers.Count > 0)
      {
        o["values"] = new JArray(numbers.Take(maxItems).Select(Number));
        o["stats"] = new JObject
        {
          ["count"] = numbers.Count,
          ["min"] = Number(numbers.Min()),
          ["max"] = Number(numbers.Max()),
          ["sum"] = Number(numbers.Sum()),
          ["mean"] = Number(numbers.Average()),
        };
      }
      if (texts.Count > 0) o["texts"] = new JArray(texts.Take(maxItems));
      if (totals.Count > 0)
      {
        var g = totals.ToJson();
        g["types"] = JObject.FromObject(typeCounts);
        o["geometry"] = g;
        if (includeItems || includeJson) o["items"] = itemJson;
      }
      o["truncated"] = goos.Count > maxItems;
      return o;
    }

    internal static JToken GooValue(IGH_Goo goo)
    {
      switch (goo)
      {
        case null: return JValue.CreateNull();
        case GH_Number n: return Number(n.Value);
        case GH_Integer i: return i.Value;
        case GH_Boolean b: return b.Value;
        case GH_String s: return s.Value;
        case GH_Point pt: return J.P(pt.Value);
        case GH_Vector v: return J.V(v.Value);
        case GH_Interval iv: return new JArray(Number(iv.Value.T0), Number(iv.Value.T1));
        case GH_Colour c: return J.Hex(c.Value);
        case GH_Plane pl: return J.Plane(pl.Value);
      }
      var geom = ToGeometry(goo);
      if (geom != null) return new JObject { ["geometry_type"] = GeometryInfo.Kind(geom), ["bbox"] = J.BBox(geom.GetBoundingBox(true)) };
      return goo.ToString();
    }

    /// <summary>Converts geometric goo (curves, breps, boxes, circles, points…) to RhinoCommon geometry.</summary>
    internal static GeometryBase ToGeometry(IGH_Goo goo)
    {
      if (goo == null) return null;
      if (goo is GH_Point p) return new Rhino.Geometry.Point(p.Value);
      if (!(goo is IGH_GeometricGoo)) return null;
      object raw = goo.ScriptVariable();
      switch (raw)
      {
        case GeometryBase g: return g.Duplicate();
        case Point3d pt: return new Rhino.Geometry.Point(pt);
        case Line line: return new LineCurve(line);
        case Polyline pl: return new PolylineCurve(pl);
        case Circle circle: return new ArcCurve(circle);
        case Arc arc: return new ArcCurve(arc);
        case Rectangle3d rect: return rect.ToNurbsCurve();
        case Box box: return box.ToBrep();
      }
      try
      {
        return GH_Convert.ToGeometryBase(goo);
      }
      catch
      {
        return null;
      }
    }

    private static JToken Number(double v)
    {
      if (double.IsNaN(v) || double.IsInfinity(v)) return JValue.CreateNull();
      if (Math.Abs(v - Math.Round(v)) < 1e-12 && Math.Abs(v) < 1e15) return new JValue((long)Math.Round(v));
      return new JValue(Math.Round(v, 6));
    }

    // ================================================================== geometry collection

    /// <summary>
    /// Geometry for previews and exports, grouped by source name: the given outputs, or every
    /// component/parameter whose preview is on (what Grasshopper itself shows in Rhino).
    /// </summary>
    internal static List<(string name, List<GeometryBase> geometry)> CollectGeometry(GH_Document doc, JObject p, string source)
    {
      var groups = new List<(string, List<GeometryBase>)>();
      List<IGH_Param> parameters = new List<IGH_Param>();
      var names = new List<string>();

      if (Args.Has(p, "outputs") || source == "outputs")
      {
        foreach (var prm in SelectOutputs(doc, p, out _))
        {
          parameters.Add((IGH_Param)prm.Object);
          names.Add(prm.Name);
        }
      }
      else
      {
        foreach (var obj in doc.Objects)
        {
          if (!(obj is IGH_PreviewObject po) || po.Hidden || !po.IsPreviewCapable) continue;
          if (obj is IGH_ActiveObject ao && ao.Locked) continue;
          if (obj is IGH_Component comp)
          {
            foreach (var output in comp.Params.Output)
            {
              parameters.Add(output);
              names.Add(Label(comp) + "." + output.NickName);
            }
          }
          else if (obj is IGH_Param prm)
          {
            parameters.Add(prm);
            names.Add(Label(prm));
          }
        }
      }

      for (int i = 0; i < parameters.Count; i++)
      {
        var list = new List<GeometryBase>();
        foreach (var goo in parameters[i].VolatileData.AllData(true))
        {
          var g = ToGeometry(goo);
          if (g != null && g.IsValid) list.Add(g);
        }
        if (list.Count > 0) groups.Add((names[i], list));
      }
      return groups;
    }

    /// <summary>
    /// Called by rhino.capture_viewport: draws the Grasshopper result through the bridge's own
    /// conduit and switches Grasshopper's preview off until the returned scope is disposed,
    /// so the capture shows each object once with a consistent look.
    /// </summary>
    internal static IDisposable BeginPreview(JObject p, Display.PreviewConduit conduit, bool required, out int count)
    {
      count = 0;
      var doc = ResolveDocument(p, required);
      if (doc == null) return null;
      var geometry = CollectGeometry(doc, p, Args.Has(p, "outputs") ? "outputs" : "preview").SelectMany(g => g.geometry).ToList();
      count = geometry.Count;
      if (count == 0) return null;
      conduit.Add(geometry);
      var old = doc.PreviewMode;
      doc.PreviewMode = GH_PreviewMode.Disabled;
      return new Scope(() => doc.PreviewMode = old);
    }

    private sealed class Scope : IDisposable
    {
      private Action _onDispose;
      public Scope(Action onDispose) { _onDispose = onDispose; }
      public void Dispose()
      {
        _onDispose?.Invoke();
        _onDispose = null;
      }
    }

    // ================================================================== export / bake

    public const string BakeTagKey = "mcp.bake_tag";

    public static JToken ExportGeometry(JObject p)
    {
      var ghDoc = ResolveDocument(p);
      var rhinoDoc = RhinoUtil.Doc();
      var source = Args.Has(p, "outputs") ? "outputs" : (Args.Str(p, "source", "outputs") ?? "outputs").ToLowerInvariant();
      var groups = CollectGeometry(ghDoc, p, source == "preview" ? "preview" : "outputs");
      if (groups.Count == 0 && source != "preview") groups = CollectGeometry(ghDoc, new JObject(), "preview");
      if (groups.Count == 0) throw RpcException.NotFound("The definition has no geometry to export (check outputs and previews).");

      string baseLayer = Args.Str(p, "layer") ?? ("Grasshopper::" + SafeLayerName(Path.GetFileNameWithoutExtension(ghDoc.FilePath ?? ghDoc.DisplayName)));
      bool perOutput = Args.Bool(p, "layer_per_output", true);
      var userText = p["user_text"] as JObject ?? new JObject();
      var tag = Args.Str(p, "bake_tag");
      var filePath = Args.Str(p, "file_path");

      if (!string.IsNullOrEmpty(filePath))
      {
        filePath = Path.GetFullPath(Environment.ExpandEnvironmentVariables(filePath));
        Directory.CreateDirectory(Path.GetDirectoryName(filePath));
        using (var file = new File3dm())
        {
          file.Settings.ModelUnitSystem = rhinoDoc.ModelUnitSystem;
          file.Settings.ModelAbsoluteTolerance = rhinoDoc.ModelAbsoluteTolerance;
          int count = 0;
          foreach (var (name, geoms) in groups)
          {
            var layer = new Layer { Name = SafeLayerName(name) };
            file.AllLayers.Add(layer);
            int li = file.AllLayers.Count - 1;
            foreach (var g in geoms)
            {
              var attr = new ObjectAttributes { LayerIndex = li };
              attr.SetUserString("mcp.output", name);
              foreach (var kv in userText) attr.SetUserString(kv.Key, kv.Value?.ToString() ?? "");
              file.Objects.Add(g, attr);
              count++;
            }
          }
          if (!file.Write(filePath, 8)) throw RpcException.Failed("Could not write " + filePath);
          return new JObject
          {
            ["mode"] = "file",
            ["path"] = filePath,
            ["object_count"] = count,
            ["outputs"] = new JArray(groups.Select(g => (JToken)new JObject { ["name"] = g.name, ["count"] = g.geometry.Count })),
          };
        }
      }

      return RhinoHandlers.WithUndo(rhinoDoc, "bake Grasshopper", () =>
      {
        int removed = 0;
        if (!string.IsNullOrEmpty(tag) && Args.Bool(p, "replace", true))
        {
          var old = rhinoDoc.Objects.GetObjectList(new ObjectEnumeratorSettings { HiddenObjects = true, LockedObjects = true })
            .Where(o => o.Attributes.GetUserString(BakeTagKey) == tag).Select(o => o.Id).ToList();
          removed = rhinoDoc.Objects.Delete(old, true);
        }

        int groupIndex = -1;
        if (Args.Bool(p, "group", false))
          groupIndex = rhinoDoc.Groups.Add(tag ?? ("GH " + DateTime.Now.ToString("yyyyMMdd-HHmmss")));

        var created = new JArray();
        var perGroup = new JArray();
        foreach (var (name, geoms) in groups)
        {
          var layerPath = perOutput ? baseLayer + "::" + SafeLayerName(name) : baseLayer;
          int li = RhinoUtil.EnsureLayer(rhinoDoc, layerPath);
          int n = 0;
          foreach (var g in geoms)
          {
            var attr = rhinoDoc.CreateDefaultAttributes();
            attr.LayerIndex = li;
            attr.SetUserString("mcp.output", name);
            if (!string.IsNullOrEmpty(tag)) attr.SetUserString(BakeTagKey, tag);
            foreach (var kv in userText) attr.SetUserString(kv.Key, kv.Value?.ToString() ?? "");
            if (groupIndex >= 0) attr.AddToGroup(groupIndex);
            var id = rhinoDoc.Objects.Add(g, attr);
            if (id != Guid.Empty)
            {
              n++;
              if (created.Count < 2000) created.Add(id.ToString());
            }
          }
          perGroup.Add(new JObject { ["name"] = name, ["layer"] = layerPath, ["count"] = n });
        }
        return new JObject
        {
          ["mode"] = "bake",
          ["baked_count"] = perGroup.Sum(x => x.Value<int>("count")),
          ["replaced"] = removed,
          ["outputs"] = perGroup,
          ["ids"] = created,
        };
      });
    }

    private static string SafeLayerName(string s)
    {
      var cleaned = new string((s ?? "output").Select(c => char.IsLetterOrDigit(c) || c == ' ' || c == '_' || c == '-' || c == '.' ? c : '_').ToArray()).Trim();
      return cleaned.Length == 0 ? "output" : cleaned;
    }

    public static JToken SaveDefinition(JObject p)
    {
      var doc = ResolveDocument(p);
      var path = Args.Str(p, "path");
      bool copy = Args.Bool(p, "copy", !string.IsNullOrEmpty(path));
      if (string.IsNullOrEmpty(path))
      {
        if (string.IsNullOrEmpty(doc.FilePath)) throw RpcException.InvalidParams("The definition was never saved: give a 'path'.");
        path = doc.FilePath;
        copy = false;
      }
      path = Path.GetFullPath(Environment.ExpandEnvironmentVariables(path));
      Directory.CreateDirectory(Path.GetDirectoryName(path));
      var previousPath = doc.FilePath;
      var io = new GH_DocumentIO(doc);
      if (!io.SaveQuiet(path)) throw RpcException.Failed("Could not save " + path);
      if (copy && !string.IsNullOrEmpty(previousPath)) doc.FilePath = previousPath;
      return new JObject { ["path"] = path, ["copy"] = copy, ["bytes"] = new FileInfo(path).Length };
    }

    // ================================================================== editing the graph

    public static JToken SearchComponents(JObject p)
    {
      EnsureLoaded();
      var query = Args.Str(p, "query", required: true);
      int limit = Args.Int(p, "limit", 25);
      var n = Normalize(query);
      var hits = Instances.ComponentServer.ObjectProxies
        .Where(x => !x.Obsolete && x.Desc != null)
        .Select(x => new
        {
          Proxy = x,
          Score = Normalize(x.Desc.Name) == n || Normalize(x.Desc.NickName) == n ? 0
                : Normalize(x.Desc.Name).StartsWith(n) ? 1
                : Normalize(x.Desc.Name).Contains(n) || Normalize(x.Desc.Description ?? "").Contains(n) ? 2 : 9,
        })
        .Where(x => x.Score < 9)
        .OrderBy(x => x.Score).ThenBy(x => x.Proxy.Desc.Name)
        .Take(limit)
        .Select(x => (JToken)new JObject
        {
          ["name"] = x.Proxy.Desc.Name,
          ["nickname"] = x.Proxy.Desc.NickName,
          ["category"] = x.Proxy.Desc.Category,
          ["subcategory"] = x.Proxy.Desc.SubCategory,
          ["guid"] = x.Proxy.Guid.ToString(),
          ["description"] = x.Proxy.Desc.Description,
        });
      return new JObject { ["query"] = query, ["results"] = new JArray(hits) };
    }

    public static JToken CreateComponent(JObject p)
    {
      var doc = ResolveDocument(p, required: false);
      if (doc == null)
      {
        doc = new GH_Document();
        Instances.DocumentServer.AddDocument(doc);
        if (Instances.ActiveCanvas != null) Instances.ActiveCanvas.Document = doc;
      }

      IGH_ObjectProxy proxy = null;
      var guidText = Args.Str(p, "component_guid");
      if (!string.IsNullOrEmpty(guidText)) proxy = Instances.ComponentServer.EmitObjectProxy(Args.Guid(guidText, "component_guid"));
      else
      {
        var name = Args.Str(p, "component", null) ?? Args.Str(p, "name", required: true);
        var n = Normalize(name);
        var category = Args.Str(p, "category");
        var candidates = Instances.ComponentServer.ObjectProxies.Where(x => !x.Obsolete && x.Desc != null
          && (category == null || string.Equals(x.Desc.Category, category, StringComparison.OrdinalIgnoreCase))).ToList();
        var exact = candidates.Where(x => Normalize(x.Desc.Name) == n).ToList();
        if (exact.Count == 0) exact = candidates.Where(x => Normalize(x.Desc.NickName) == n).ToList();
        if (exact.Count > 1)
        {
          // Prefer the built-in Grasshopper components over plug-in homonyms.
          var builtin = exact.Where(x => x.LibraryGuid == Guid.Empty || Instances.ComponentServer.FindAssembly(x.LibraryGuid)?.Name == "Grasshopper").ToList();
          if (builtin.Count == 1) exact = builtin;
        }
        if (exact.Count == 0)
          throw RpcException.NotFound("No component named '" + name + "'. Use grasshopper_search_components.");
        if (exact.Count > 1)
          throw new RpcException(RpcErrorCodes.Ambiguous, "'" + name + "' matches several components: pass component_guid or category.",
            new JObject { ["candidates"] = new JArray(exact.Take(10).Select(x => (JToken)new JObject { ["name"] = x.Desc.Name, ["category"] = x.Desc.Category, ["guid"] = x.Guid.ToString() })) });
        proxy = exact[0];
      }
      if (proxy == null) throw RpcException.NotFound("Component not found.");

      var obj = proxy.CreateInstance();
      if (obj == null) throw RpcException.Failed("Grasshopper could not create '" + proxy.Desc.Name + "'.");
      obj.CreateAttributes();
      obj.Attributes.Pivot = Position(doc, p);
      var nick = Args.Str(p, "nickname");
      if (!string.IsNullOrEmpty(nick)) obj.NickName = nick;

      Configure(obj, p);
      doc.AddObject(obj, false);
      obj.Attributes.ExpireLayout();

      if (Args.Bool(p, "solve", true)) SolveDocument(doc, false, true);
      Instances.ActiveCanvas?.Refresh();
      var o = ObjectJson(doc, obj);
      o["definition"] = doc.DisplayName;
      return o;
    }

    private static PointF Position(GH_Document doc, JObject p)
    {
      if (Args.Get(p, "position") is JArray pos && pos.Count >= 2)
        return new PointF((float)Args.ToDouble(pos[0]), (float)Args.ToDouble(pos[1]));
      var nearId = Args.Str(p, "near");
      if (nearId != null && Guid.TryParse(nearId, out var g))
      {
        var near = doc.FindObject(g, true);
        if (near?.Attributes != null) return new PointF(near.Attributes.Bounds.Right + 80, near.Attributes.Bounds.Top);
      }
      if (doc.ObjectCount == 0) return new PointF(100, 100);
      var bounds = doc.Objects.Where(o => o.Attributes != null).Select(o => o.Attributes.Bounds).ToList();
      float right = bounds.Max(b => b.Right);
      float top = bounds.Min(b => b.Top);
      return new PointF(right + 120, top);
    }

    private static void Configure(IGH_DocumentObject obj, JObject p)
    {
      switch (obj)
      {
        case GH_NumberSlider s:
          {
            double min = Args.Num(p, "min", 0), max = Args.Num(p, "max", 100);
            if (max <= min) throw RpcException.InvalidParams("Slider 'max' must be greater than 'min'.");
            bool integer = Args.Bool(p, "integer", false);
            s.Slider.Minimum = (decimal)min;
            s.Slider.Maximum = (decimal)max;
            s.Slider.Type = integer ? GH_SliderAccuracy.Integer : GH_SliderAccuracy.Float;
            s.Slider.DecimalPlaces = integer ? 0 : Args.Int(p, "decimals", 2);
            s.SetSliderValue((decimal)Args.Num(p, "value", min));
            break;
          }
        case GH_Panel panel:
          if (Args.Has(p, "text", "value")) panel.SetUserText(Args.Str(p, "text") ?? Args.Str(p, "value"));
          break;
        case GH_BooleanToggle t:
          t.Value = Args.Bool(p, "value", false);
          break;
        case GH_ValueList vl:
          if (Args.Get(p, "items") is JArray items && items.Count > 0)
          {
            vl.ListItems.Clear();
            foreach (var item in items)
            {
              if (item is JObject io)
                vl.ListItems.Add(new GH_ValueListItem(io.Value<string>("name"), io["value"]?.ToString() ?? io.Value<string>("name")));
              else
                vl.ListItems.Add(new GH_ValueListItem(item.ToString(), "\"" + item + "\""));
            }
            vl.SelectItem(0);
          }
          break;
        case IGH_Param prm when Args.Has(p, "value") && PersistentKind(prm) != null:
          SetPersistent(prm, Args.Get(p, "value"), "set", prm.NickName);
          break;
      }
    }

    public static JToken ConnectComponents(JObject p)
    {
      var doc = ResolveDocument(p);
      var source = p["source"] as JObject ?? throw RpcException.InvalidParams("'source' is required: {\"id\" or \"name\", \"output\": name|index}.");
      var target = p["target"] as JObject ?? throw RpcException.InvalidParams("'target' is required: {\"id\" or \"name\", \"input\": name|index}.");
      var srcParam = ResolveEndpoint(doc, source, output: true);
      var tgtParam = ResolveEndpoint(doc, target, output: false);
      var mode = (Args.Str(p, "mode", "connect") ?? "connect").ToLowerInvariant();

      switch (mode)
      {
        case "connect":
          if (!tgtParam.Sources.Contains(srcParam)) tgtParam.AddSource(srcParam);
          break;
        case "replace":
          tgtParam.RemoveAllSources();
          tgtParam.AddSource(srcParam);
          break;
        case "disconnect":
          tgtParam.RemoveSource(srcParam);
          break;
        default:
          throw RpcException.InvalidParams("'mode' must be connect, replace or disconnect.");
      }
      tgtParam.ExpireSolution(false);
      var result = new JObject
      {
        ["mode"] = mode,
        ["source"] = Endpoint(srcParam),
        ["target"] = Endpoint(tgtParam),
        ["target_sources"] = tgtParam.Sources.Count,
      };
      if (Args.Bool(p, "solve", true)) result["solution"] = SolveDocument(doc, false, true);
      Instances.ActiveCanvas?.Refresh();
      return result;
    }

    private static IGH_Param ResolveEndpoint(GH_Document doc, JObject spec, bool output)
    {
      IGH_DocumentObject obj = null;
      var idText = Args.Str(spec, "id");
      if (idText != null)
      {
        obj = doc.FindObject(Args.Guid(idText, "id"), false);
        if (obj == null) throw RpcException.NotFound("No Grasshopper object " + idText + ".");
        // An id may designate a component parameter directly.
        if (obj is IGH_Param direct && direct.Kind != GH_ParamKind.floating) return direct;
        obj = obj.Attributes?.GetTopLevel?.DocObject ?? obj;
      }
      else
      {
        var name = Args.Str(spec, "name") ?? Args.Str(spec, "nickname") ?? throw RpcException.InvalidParams("Endpoint needs 'id' or 'name'.");
        var matches = doc.Objects.Where(o => string.Equals(o.NickName, name, StringComparison.OrdinalIgnoreCase)
                                          || string.Equals(Label(o), name, StringComparison.OrdinalIgnoreCase)).ToList();
        if (matches.Count == 0) matches = doc.Objects.Where(o => Normalize(o.NickName) == Normalize(name) || Normalize(o.Name) == Normalize(name)).ToList();
        if (matches.Count == 0) throw RpcException.NotFound("No Grasshopper object named '" + name + "'.");
        if (matches.Count > 1)
          throw new RpcException(RpcErrorCodes.Ambiguous, "'" + name + "' is ambiguous: use the id.",
            new JObject { ["candidates"] = new JArray(matches.Take(10).Select(m => (JToken)new JObject { ["id"] = m.InstanceGuid.ToString(), ["label"] = Label(m), ["kind"] = Kind(m) })) });
        obj = matches[0];
      }

      if (obj is IGH_Param floating) return floating;
      if (!(obj is IGH_Component comp)) throw RpcException.InvalidParams(Label(obj) + " has no parameters.");
      var list = output ? comp.Params.Output : comp.Params.Input;
      var key = Args.Get(spec, output ? "output" : "input", "param", "parameter");
      if (key == null)
      {
        if (list.Count == 1 || output) return list[0];
        throw RpcException.InvalidParams($"{Label(comp)} has {list.Count} inputs: say which ('input': name or index).",
          new JObject { ["inputs"] = new JArray(list.Select(x => x.Name)) });
      }
      if (key.Type == JTokenType.Integer)
      {
        int i = (int)key;
        if (i < 0 || i >= list.Count) throw RpcException.InvalidParams($"Index {i} is out of range (0..{list.Count - 1}).");
        return list[i];
      }
      var k = key.ToString();
      var found = list.FirstOrDefault(x => string.Equals(x.Name, k, StringComparison.OrdinalIgnoreCase) || string.Equals(x.NickName, k, StringComparison.OrdinalIgnoreCase))
                  ?? list.FirstOrDefault(x => Normalize(x.Name) == Normalize(k) || Normalize(x.NickName) == Normalize(k));
      if (found == null)
        throw RpcException.NotFound($"{Label(comp)} has no {(output ? "output" : "input")} '{k}'.",
          new JObject { ["available"] = new JArray(list.Select(x => x.Name + " (" + x.NickName + ")")) });
      return found;
    }

    // ================================================================== canvas capture

    public static JToken CaptureCanvas(JObject p)
    {
      var doc = ResolveDocument(p);
      var canvas = Instances.ActiveCanvas ?? throw new RpcException(RpcErrorCodes.GrasshopperUnavailable, "The Grasshopper editor is not loaded.");
      if (canvas.Document != doc) canvas.Document = doc;
      int width = Math.Max(200, Math.Min(4096, Args.Int(p, "width", 1600)));
      int height = Math.Max(200, Math.Min(4096, Args.Int(p, "height", 1000)));

      Bitmap bmp = null;
      try
      {
        var vp = new GH_Viewport(canvas.Viewport);
        vp.Width = width;
        vp.Height = height;
        var attributes = doc.Objects.Where(o => o.Attributes != null).Select(o => o.Attributes).ToList();
        if (attributes.Count > 0) vp.Focus(attributes);
        vp.ComputeProjection();
        bmp = canvas.GenerateHiResImageTile(vp, Color.White);
      }
      catch (Exception ex)
      {
        RhinoApp.WriteLine("RhinoMcpBridge: canvas tile render failed, using the visible canvas ({0})", ex.Message);
        bmp = null;
      }
      if (bmp == null)
      {
        if (!Script().IsEditorVisible()) Script().ShowEditor();
        bmp = new Bitmap(Math.Max(1, canvas.Width), Math.Max(1, canvas.Height));
        canvas.DrawToBitmap(bmp, new Rectangle(0, 0, bmp.Width, bmp.Height));
      }
      using (bmp)
      {
        return ImageUtil.Encode(bmp, p, "grasshopper_canvas");
      }
    }
  }
}
