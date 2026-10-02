using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Autodesk.Revit.DB;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;
using RevitMcpBridge.App;
using RevitMcpBridge.Core;

namespace RevitMcpBridge.Handlers
{
  /// <summary>
  /// Element parameters and global parameters — the "sliders" of a Revit model. Values are
  /// exchanged in SI (m, m², m³, degrees); changes accept set / add / multiply / percent.
  /// </summary>
  public static class ParameterHandlers
  {
    public static void Register(RpcDispatcher d)
    {
      d.Register("revit.get_parameters", GetParameters);
      d.Register("revit.set_parameters", SetParameters);
      d.Register("revit.get_global_parameters", GetGlobalParameters);
      d.Register("revit.set_global_parameters", SetGlobalParameters);
    }

    // ------------------------------------------------------------------ element parameters

    public static JArray ParameterList(Document doc, Element e, HashSet<string> names, bool includeEmpty)
    {
      var list = new JArray();
      foreach (Parameter prm in e.Parameters)
      {
        var n = prm.Definition?.Name;
        if (n == null) continue;
        if (names != null && !names.Contains(n)) continue;
        if (!includeEmpty && !prm.HasValue && names == null) continue;
        list.Add(Describe.Parameter(doc, prm));
      }
      return new JArray(list.OrderBy(x => (string)x["name"], StringComparer.OrdinalIgnoreCase));
    }

    private static JToken GetParameters(JObject p)
    {
      var doc = RevitContext.Doc;
      var elements = ElementQuery.From(doc, p).Run(doc);
      int limit = Math.Max(1, Math.Min(2000, RArgs.Int(p, "limit", 50)));
      var names = RArgs.Strings(RArgs.Get(p, "names", "parameters"));
      var nameSet = names.Count > 0 ? new HashSet<string>(names, StringComparer.OrdinalIgnoreCase) : null;
      bool includeType = RArgs.Bool(p, "include_type", true);
      bool includeEmpty = RArgs.Bool(p, "include_empty", false);
      var items = new JArray();
      foreach (var e in elements.Take(limit))
      {
        var o = Describe.Element(doc, e, false);
        o["instance"] = ParameterList(doc, e, nameSet, includeEmpty);
        if (includeType && doc.GetElement(e.GetTypeId()) is ElementType type)
          o["type"] = new JObject { ["id"] = Ids.Str(type.Id), ["name"] = type.Name, ["parameters"] = ParameterList(doc, type, nameSet, includeEmpty) };
        items.Add(o);
      }
      return new JObject { ["total"] = elements.Count, ["returned"] = items.Count, ["elements"] = items };
    }

    /// <summary>
    /// values: {"Height": 12} or changes: [{name, value, mode: set|add|multiply|percent, target: instance|type}].
    /// </summary>
    private static JToken SetParameters(JObject p)
    {
      var doc = RevitContext.Doc;
      var elements = ElementQuery.Target(doc, p).Run(doc);
      if (elements.Count == 0) throw RpcException.NotFound("No element matches.");
      var changes = Changes(p);
      bool typeTarget = string.Equals(RArgs.Str(p, "target", "instance"), "type", StringComparison.OrdinalIgnoreCase);
      int max = RArgs.Int(p, "max_count", 2000);
      if (elements.Count > max) throw RpcException.InvalidParams($"{elements.Count} elements match (> max_count={max}).");

      return Tx.Run(doc, "set parameters", () =>
      {
        var results = new JArray();
        var doneTypes = new HashSet<string>();
        int ok = 0, failed = 0;
        foreach (var e in elements)
        {
          foreach (var c in changes)
          {
            bool onType = typeTarget || c.Target == "type";
            Element owner = e;
            if (onType)
            {
              owner = doc.GetElement(e.GetTypeId());
              if (owner == null) { results.Add(Fail(e, c.Name, "The element has no type.")); failed++; continue; }
              if (!doneTypes.Add(Ids.Str(owner.Id) + "|" + c.Name)) continue; // one change per type
            }
            var prm = Find(owner, c.Name);
            if (prm == null && !onType && doc.GetElement(e.GetTypeId()) is Element type && Find(type, c.Name) != null)
            {
              results.Add(Fail(e, c.Name, "This is a type parameter: repeat with target='type' (it changes every element of the type)."));
              failed++;
              continue;
            }
            if (prm == null) { results.Add(Fail(e, c.Name, "Parameter not found.")); failed++; continue; }
            try
            {
              var before = Describe.Parameter(doc, prm)["value"];
              Write(doc, prm, c.Value, c.Mode);
              var after = Describe.Parameter(doc, prm);
              results.Add(new JObject { ["id"] = Ids.Str(owner.Id), ["name"] = prm.Definition.Name, ["before"] = before, ["after"] = after["value"], ["unit"] = after["unit"] });
              ok++;
            }
            catch (Exception ex)
            {
              results.Add(Fail(owner, c.Name, ex.Message));
              failed++;
            }
          }
        }
        if (ok == 0 && failed > 0)
          throw RpcException.Failed("No parameter could be changed.", new JObject { ["results"] = new JArray(results.Take(50)) });
        return new JObject { ["changed"] = ok, ["failed"] = failed, ["results"] = new JArray(results.Take(500)) };
      });
    }

    private sealed class Change
    {
      public string Name;
      public JToken Value;
      public string Mode = "set";
      public string Target;
    }

    private static List<Change> Changes(JObject p)
    {
      var list = new List<Change>();
      if (p["values"] is JObject values)
        foreach (var kv in values) list.Add(new Change { Name = kv.Key, Value = kv.Value });
      if (p["changes"] is JArray arr)
      {
        foreach (var c in arr.OfType<JObject>())
        {
          list.Add(new Change
          {
            Name = RArgs.Str(c, "name", required: true),
            Value = c["value"],
            Mode = (RArgs.Str(c, "mode", "set") ?? "set").ToLowerInvariant(),
            Target = RArgs.Str(c, "target")?.ToLowerInvariant(),
          });
        }
      }
      if (list.Count == 0) throw RpcException.InvalidParams("Give 'values': {\"Name\": value} or 'changes': [{name, value, mode}].");
      return list;
    }

    private static JObject Fail(Element e, string name, string message)
      => new JObject { ["id"] = Ids.Str(e.Id), ["name"] = name, ["error"] = message };

    public static Parameter Find(Element e, string name)
    {
      var matches = e.GetParameters(name);
      if (matches != null && matches.Count > 0) return matches.FirstOrDefault(x => !x.IsReadOnly) ?? matches[0];
      foreach (Parameter prm in e.Parameters)
        if (string.Equals(prm.Definition?.Name, name, StringComparison.OrdinalIgnoreCase)) return prm;
      if (Enum.TryParse(name, true, out BuiltInParameter bip))
      {
        try { return e.get_Parameter(bip); } catch { return null; }
      }
      return null;
    }

    /// <summary>Numeric result of a set / add / multiply / percent change, in SI.</summary>
    public static double Combine(double current, double value, string mode)
    {
      switch (mode)
      {
        case "set": return value;
        case "add": case "delta": return current + value;
        case "multiply": case "scale": return current * value;
        case "percent": return current * (1 + value / 100.0);
      }
      throw RpcException.InvalidParams("'mode' must be set, add, multiply or percent.");
    }

    public static void Write(Document doc, Parameter prm, JToken value, string mode)
    {
      if (prm.IsReadOnly) throw new InvalidOperationException("Read-only parameter (computed or driven by a formula/constraint).");
      var spec = Describe.SafeSpec(prm.Definition);
      bool ok;
      switch (prm.StorageType)
      {
        case StorageType.Double:
          {
            double current = prm.HasValue ? U.ToSI(prm.AsDouble(), spec) : 0;
            double next = Combine(current, RArgs.ToDouble(value, prm.Definition.Name), mode);
            ok = prm.Set(U.FromSI(next, spec));
            break;
          }
        case StorageType.Integer:
          {
            int next;
            if (value.Type == JTokenType.Boolean) next = (bool)value ? 1 : 0;
            else if (value.Type == JTokenType.String && (((string)value).Equals("true", StringComparison.OrdinalIgnoreCase) || ((string)value).Equals("oui", StringComparison.OrdinalIgnoreCase))) next = 1;
            else if (value.Type == JTokenType.String && (((string)value).Equals("false", StringComparison.OrdinalIgnoreCase) || ((string)value).Equals("non", StringComparison.OrdinalIgnoreCase))) next = 0;
            else next = (int)Math.Round(Combine(prm.HasValue ? prm.AsInteger() : 0, RArgs.ToDouble(value, prm.Definition.Name), mode));
            ok = prm.Set(next);
            break;
          }
        case StorageType.String:
          ok = prm.Set(value.Type == JTokenType.String ? (string)value : value.ToString(Newtonsoft.Json.Formatting.None));
          break;
        case StorageType.ElementId:
          {
            ElementId id;
            if (value.Type == JTokenType.Integer || long.TryParse(value.ToString(), out _)) id = Ids.From(long.Parse(value.ToString(), CultureInfo.InvariantCulture));
            else id = ByName(doc, value.ToString(), prm) ?? throw new InvalidOperationException("No element named '" + value + "' fits this parameter.");
            ok = prm.Set(id);
            break;
          }
        default:
          throw new InvalidOperationException("This parameter has no value storage.");
      }
      if (!ok) throw new InvalidOperationException("Revit refused the value.");
    }

    /// <summary>Resolves a level, material or type given by name for an ElementId parameter.</summary>
    private static ElementId ByName(Document doc, string name, Parameter prm)
    {
      IEnumerable<Element> candidates = new FilteredElementCollector(doc).OfClass(typeof(Level)).ToElements()
        .Concat(new FilteredElementCollector(doc).OfClass(typeof(Material)).ToElements())
        .Concat(new FilteredElementCollector(doc).OfClass(typeof(Phase)).ToElements());
      var hit = candidates.FirstOrDefault(e => string.Equals(e.Name, name, StringComparison.OrdinalIgnoreCase));
      if (hit != null) return hit.Id;
      var types = new FilteredElementCollector(doc).WhereElementIsElementType().ToElements();
      var type = types.FirstOrDefault(t => string.Equals(t.Name, name, StringComparison.OrdinalIgnoreCase));
      return type?.Id;
    }

    // ------------------------------------------------------------------ global parameters

    private static void RequireGlobals(Document doc)
    {
      if (!GlobalParametersManager.AreGlobalParametersAllowed(doc))
        throw RpcException.Failed("This document does not support global parameters.");
    }

    public static JObject DescribeGlobal(GlobalParameter gp)
    {
      var def = gp.GetDefinition();
      var spec = Describe.SafeSpec(def);
      var o = new JObject { ["id"] = Ids.Str(gp.Id), ["name"] = gp.Name };
      var unit = spec != null ? U.SiLabel(spec) : null;
      if (unit != null) o["unit"] = unit;
      try { if (spec != null && !spec.Empty()) o["data_type"] = LabelUtils.GetLabelForSpec(spec); } catch { /* not a spec */ }
      var value = gp.GetValue();
      switch (value)
      {
        case DoubleParameterValue dv: o["value"] = U.R(U.ToSI(dv.Value, spec), 6); break;
        case IntegerParameterValue iv: o["value"] = spec == SpecTypeId.Boolean.YesNo ? (JToken)(iv.Value != 0) : iv.Value; break;
        case StringParameterValue sv: o["value"] = sv.Value; break;
        case ElementIdParameterValue ev: o["value"] = Ids.Str(ev.Value); break;
        default: o["value"] = JValue.CreateNull(); break;
      }
      o["reporting"] = gp.IsReporting;
      var formula = gp.GetFormula();
      if (!string.IsNullOrEmpty(formula)) o["formula"] = formula;
      return o;
    }

    private static JToken GetGlobalParameters(JObject p)
    {
      var doc = RevitContext.Doc;
      RequireGlobals(doc);
      var names = RArgs.Strings(RArgs.Get(p, "names"));
      var list = new JArray();
      foreach (var id in GlobalParametersManager.GetGlobalParametersOrdered(doc))
      {
        if (!(doc.GetElement(id) is GlobalParameter gp)) continue;
        if (names.Count > 0 && !names.Any(n => string.Equals(n, gp.Name, StringComparison.OrdinalIgnoreCase))) continue;
        list.Add(DescribeGlobal(gp));
      }
      return new JObject { ["count"] = list.Count, ["parameters"] = list };
    }

    private static ForgeTypeId SpecFromName(string type)
    {
      switch ((type ?? "length").ToLowerInvariant())
      {
        case "length": return SpecTypeId.Length;
        case "area": return SpecTypeId.Area;
        case "volume": return SpecTypeId.Volume;
        case "angle": return SpecTypeId.Angle;
        case "number": case "double": return SpecTypeId.Number;
        case "integer": case "int": return SpecTypeId.Int.Integer;
        case "yesno": case "bool": case "boolean": return SpecTypeId.Boolean.YesNo;
        case "text": case "string": return SpecTypeId.String.Text;
      }
      throw RpcException.InvalidParams("Unknown global parameter type '" + type + "'. Use length, area, volume, angle, number, integer, yesno or text.");
    }

    /// <summary>values: {"Hauteur_R+": 15} or changes: [{name, value, mode, create_type}]. Missing ones are created when 'create' is true.</summary>
    private static JToken SetGlobalParameters(JObject p)
    {
      var doc = RevitContext.Doc;
      RequireGlobals(doc);
      var changes = Changes(p);
      bool create = RArgs.Bool(p, "create", false);
      var createTypes = p["changes"] is JArray arr
        ? arr.OfType<JObject>().Where(c => c["create_type"] != null).ToDictionary(c => (string)c["name"], c => (string)c["create_type"], StringComparer.OrdinalIgnoreCase)
        : new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

      return Tx.Run(doc, "global parameters", () =>
      {
        var results = new JArray();
        foreach (var c in changes)
        {
          var id = GlobalParametersManager.FindByName(doc, c.Name);
          GlobalParameter gp = id != null && id != ElementId.InvalidElementId ? doc.GetElement(id) as GlobalParameter : null;
          bool created = false;
          if (gp == null)
          {
            if (!create && !createTypes.ContainsKey(c.Name))
              throw RpcException.NotFound("No global parameter '" + c.Name + "'. Set create=true to create it.",
                new JObject { ["available"] = new JArray(GlobalParametersManager.GetGlobalParametersOrdered(doc).Select(i => (doc.GetElement(i) as GlobalParameter)?.Name)) });
            createTypes.TryGetValue(c.Name, out var t);
            gp = GlobalParameter.Create(doc, c.Name, SpecFromName(t ?? (c.Value?.Type == JTokenType.String ? "text" : c.Value?.Type == JTokenType.Boolean ? "yesno" : "length")));
            created = true;
          }
          if (!string.IsNullOrEmpty(gp.GetFormula()))
            throw RpcException.Failed("'" + gp.Name + "' is driven by a formula (" + gp.GetFormula() + "): change its inputs instead.");
          var spec = Describe.SafeSpec(gp.GetDefinition());
          var before = DescribeGlobal(gp)["value"];
          var current = gp.GetValue();
          switch (current)
          {
            case DoubleParameterValue dv:
              gp.SetValue(new DoubleParameterValue(U.FromSI(Combine(U.ToSI(dv.Value, spec), RArgs.ToDouble(c.Value, c.Name), c.Mode), spec)));
              break;
            case IntegerParameterValue iv:
              {
                int next = c.Value.Type == JTokenType.Boolean ? ((bool)c.Value ? 1 : 0) : (int)Math.Round(Combine(iv.Value, RArgs.ToDouble(c.Value, c.Name), c.Mode));
                gp.SetValue(new IntegerParameterValue(next));
                break;
              }
            case StringParameterValue _:
              gp.SetValue(new StringParameterValue(c.Value.ToString()));
              break;
            case ElementIdParameterValue _:
              gp.SetValue(new ElementIdParameterValue(Ids.From(long.Parse(c.Value.ToString(), CultureInfo.InvariantCulture))));
              break;
            default:
              if (spec == SpecTypeId.String.Text) gp.SetValue(new StringParameterValue(c.Value.ToString()));
              else if (spec == SpecTypeId.Int.Integer || spec == SpecTypeId.Boolean.YesNo) gp.SetValue(new IntegerParameterValue((int)Math.Round(RArgs.ToDouble(c.Value, c.Name))));
              else gp.SetValue(new DoubleParameterValue(U.FromSI(RArgs.ToDouble(c.Value, c.Name), spec)));
              break;
          }
          var after = DescribeGlobal(gp);
          after["before"] = before;
          if (created) after["created"] = true;
          results.Add(after);
        }
        return new JObject { ["changed"] = results.Count, ["parameters"] = results };
      });
    }
  }
}
