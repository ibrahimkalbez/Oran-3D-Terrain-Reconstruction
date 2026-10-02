using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.DB;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;
using RevitMcpBridge.App;
using RevitMcpBridge.Core;

namespace RevitMcpBridge.Handlers
{
  /// <summary>
  /// revit.metrics — quantities for variant comparison and urban rules: counts, areas,
  /// volumes, lengths and heights grouped by category, level, type, family, a user-text key
  /// or a parameter; plus floor area per level (surface de plancher) and rooms.
  /// </summary>
  public static class MetricsHandlers
  {
    public static void Register(RpcDispatcher d)
    {
      d.Register("revit.metrics", Metrics);
    }

    private sealed class Acc
    {
      public int Count;
      public double Area, Volume, Length, MinZ = double.MaxValue, MaxZ = double.MinValue;
    }

    private static double Measure(Element e, params BuiltInParameter[] candidates)
    {
      foreach (var bip in candidates)
      {
        var prm = e.get_Parameter(bip);
        if (prm != null && prm.HasValue && prm.StorageType == StorageType.Double) return U.ToSI(prm.AsDouble(), Describe.SafeSpec(prm.Definition));
      }
      return 0;
    }

    public static double AreaOf(Element e) => Measure(e, BuiltInParameter.HOST_AREA_COMPUTED, BuiltInParameter.ROOM_AREA, BuiltInParameter.MASS_GROSS_SURFACE_AREA);
    public static double VolumeOf(Element e)
    {
      var v = Measure(e, BuiltInParameter.HOST_VOLUME_COMPUTED, BuiltInParameter.ROOM_VOLUME, BuiltInParameter.MASS_GROSS_VOLUME);
      if (v > 0) return v;
      if (e is DirectShape || e is FamilyInstance)
      {
        try { return Geo.Solids(e).Sum(s => U.ToM3(Math.Abs(s.Volume))); } catch { return 0; }
      }
      return 0;
    }

    private static string Key(Document doc, Element e, string groupBy)
    {
      if (groupBy.StartsWith("user_text:", StringComparison.OrdinalIgnoreCase)) return UserData.Get(e, groupBy.Substring(10)) ?? "(none)";
      if (groupBy.StartsWith("parameter:", StringComparison.OrdinalIgnoreCase))
      {
        var prm = ParameterHandlers.Find(e, groupBy.Substring(10));
        return prm == null ? "(none)" : (prm.AsValueString() ?? prm.AsString() ?? "(empty)");
      }
      switch (groupBy)
      {
        case "level": return ElementQuery.LevelName(doc, e) ?? "(no level)";
        case "type": return ElementQuery.TypeNameOf(doc, e) ?? "(no type)";
        case "family": return ElementQuery.FamilyName(doc, e) ?? "(no family)";
        case "layer": return ElementQuery.LayerOf(e, UserData.Read(e)) ?? "?";
        case "none": return "all";
        default: return e.Category?.Name ?? "?";
      }
    }

    private static JToken Metrics(JObject p)
    {
      var doc = RevitContext.Doc;
      var elements = ExportHandlers.Selection(doc, p);
      var groupBy = (RArgs.Str(p, "group_by", "category") ?? "category").Trim();
      var groups = new Dictionary<string, Acc>(StringComparer.OrdinalIgnoreCase);
      var total = new Acc();
      foreach (var e in elements)
      {
        var key = Key(doc, e, groupBy);
        if (!groups.TryGetValue(key, out var acc)) groups[key] = acc = new Acc();
        double area = AreaOf(e), volume = VolumeOf(e);
        double length = e.Location is LocationCurve lc ? U.ToMeters(lc.Curve.Length) : Measure(e, BuiltInParameter.CURVE_ELEM_LENGTH);
        var bb = e.get_BoundingBox(null);
        foreach (var a in new[] { acc, total })
        {
          a.Count++;
          a.Area += area;
          a.Volume += volume;
          a.Length += length;
          if (bb != null)
          {
            a.MinZ = Math.Min(a.MinZ, U.ToMeters(bb.Min.Z));
            a.MaxZ = Math.Max(a.MaxZ, U.ToMeters(bb.Max.Z));
          }
        }
      }

      JObject Json(string key, Acc a)
      {
        var o = new JObject
        {
          ["count"] = a.Count,
          ["area_m2"] = U.R(a.Area, 2),
          ["volume_m3"] = U.R(a.Volume, 2),
          ["length_m"] = U.R(a.Length, 2),
        };
        if (key != null) o = new JObject { ["key"] = key }.Also(o);
        if (a.MaxZ > a.MinZ) { o["min_z"] = U.R(a.MinZ, 3); o["max_z"] = U.R(a.MaxZ, 3); o["height_m"] = U.R(a.MaxZ - a.MinZ, 3); }
        return o;
      }

      // Floor area per level (floors) and rooms — the usual "surface de plancher" inputs.
      var floorsByLevel = new JObject();
      double floorTotal = 0;
      foreach (var f in new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Floors).WhereElementIsNotElementType())
      {
        var lvl = ElementQuery.LevelName(doc, f) ?? "(no level)";
        double a = AreaOf(f);
        floorTotal += a;
        floorsByLevel[lvl] = U.R((floorsByLevel.Value<double?>(lvl) ?? 0) + a, 2);
      }
      var rooms = new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().ToElements().Where(r => AreaOf(r) > 0).ToList();
      var massFloors = new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_MassFloor).WhereElementIsNotElementType().ToElements();

      return new JObject
      {
        ["group_by"] = groupBy,
        ["groups"] = new JArray(groups.OrderBy(g => g.Key, StringComparer.OrdinalIgnoreCase).Select(g => (JToken)Json(g.Key, g.Value))),
        ["totals"] = Json(null, total),
        ["floor_area"] = new JObject { ["total_m2"] = U.R(floorTotal, 2), ["by_level"] = floorsByLevel },
        ["mass_floor_area_m2"] = U.R(massFloors.Sum(AreaOf), 2),
        ["rooms"] = new JObject { ["count"] = rooms.Count, ["area_m2"] = U.R(rooms.Sum(AreaOf), 2) },
        ["levels"] = new FilteredElementCollector(doc).OfClass(typeof(Level)).GetElementCount(),
      };
    }

    private static JObject Also(this JObject first, JObject second)
    {
      foreach (var kv in second) first[kv.Key] = kv.Value;
      return first;
    }
  }
}
