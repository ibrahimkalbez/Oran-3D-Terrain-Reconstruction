using System;
using System.Linq;
using Autodesk.Revit.DB;
using Newtonsoft.Json.Linq;

namespace RevitMcpBridge.Core
{
  /// <summary>JSON views of elements, bounding boxes and parameter values (SI units).</summary>
  public static class Describe
  {
    public static JArray P(XYZ p) => new JArray(U.R(U.ToMeters(p.X)), U.R(U.ToMeters(p.Y)), U.R(U.ToMeters(p.Z)));

    public static JToken BBox(BoundingBoxXYZ b)
    {
      if (b == null) return JValue.CreateNull();
      var min = b.Transform.OfPoint(b.Min);
      var max = b.Transform.OfPoint(b.Max);
      var lo = new XYZ(Math.Min(min.X, max.X), Math.Min(min.Y, max.Y), Math.Min(min.Z, max.Z));
      var hi = new XYZ(Math.Max(min.X, max.X), Math.Max(min.Y, max.Y), Math.Max(min.Z, max.Z));
      return new JObject
      {
        ["min"] = P(lo),
        ["max"] = P(hi),
        ["size"] = new JArray(U.R(U.ToMeters(hi.X - lo.X)), U.R(U.ToMeters(hi.Y - lo.Y)), U.R(U.ToMeters(hi.Z - lo.Z))),
        ["center"] = P((lo + hi) / 2),
      };
    }

    public static JObject Element(Document doc, Element e, bool full)
    {
      var ut = UserData.Read(e);
      var o = new JObject
      {
        ["id"] = Ids.Str(e.Id),
        ["type"] = e.Category?.Name ?? e.GetType().Name,
        ["name"] = e.Name ?? "",
        ["layer"] = ElementQuery.LayerOf(e, ut),
      };
      var family = ElementQuery.FamilyName(doc, e);
      if (family != null) o["family"] = family;
      var typeName = ElementQuery.TypeNameOf(doc, e);
      if (typeName != null) o["type_name"] = typeName;
      var level = ElementQuery.LevelName(doc, e);
      if (level != null) o["level"] = level;
      if (ut.Count > 0) o["user_text"] = ut;
      if (!full) return o;

      o["unique_id"] = e.UniqueId;
      o["bbox"] = BBox(e.get_BoundingBox(null));
      switch (e.Location)
      {
        case LocationPoint lp:
          o["location"] = P(lp.Point);
          try { o["rotation_deg"] = U.R(lp.Rotation * 180 / Math.PI, 3); } catch { /* not rotatable */ }
          break;
        case LocationCurve lc:
          o["location"] = new JObject { ["start"] = P(lc.Curve.GetEndPoint(0)), ["end"] = P(lc.Curve.GetEndPoint(1)), ["length"] = U.R(U.ToMeters(lc.Curve.Length)) };
          break;
      }
      AddMeasure(o, "area", e, BuiltInParameter.HOST_AREA_COMPUTED, BuiltInParameter.ROOM_AREA, BuiltInParameter.MASS_GROSS_AREA);
      AddMeasure(o, "volume", e, BuiltInParameter.HOST_VOLUME_COMPUTED, BuiltInParameter.ROOM_VOLUME, BuiltInParameter.MASS_GROSS_VOLUME);
      var comments = e.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)?.AsString();
      if (!string.IsNullOrEmpty(comments)) o["comments"] = comments;
      var mark = e.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.AsString();
      if (!string.IsNullOrEmpty(mark)) o["mark"] = mark;
      return o;
    }

    private static void AddMeasure(JObject o, string key, Element e, params BuiltInParameter[] candidates)
    {
      foreach (var bip in candidates)
      {
        var p = e.get_Parameter(bip);
        if (p != null && p.HasValue && p.StorageType == StorageType.Double)
        {
          o[key] = U.R(U.ToSI(p.AsDouble(), p.Definition.GetDataType()), 3);
          return;
        }
      }
    }

    /// <summary>Value of a parameter in SI (numbers), text, id or yes/no, with Revit's display string.</summary>
    public static JObject Parameter(Document doc, Autodesk.Revit.DB.Parameter p)
    {
      var spec = SafeSpec(p.Definition);
      var o = new JObject
      {
        ["name"] = p.Definition.Name,
        ["storage"] = p.StorageType.ToString().ToLowerInvariant(),
        ["read_only"] = p.IsReadOnly,
        ["shared"] = p.IsShared,
      };
      var unit = spec != null ? U.SiLabel(spec) : null;
      if (unit != null) o["unit"] = unit;
      if (spec != null && !spec.Empty())
      {
        try { o["data_type"] = LabelUtils.GetLabelForSpec(spec); } catch { /* not a spec */ }
      }
      if (!p.HasValue)
      {
        o["value"] = JValue.CreateNull();
        return o;
      }
      switch (p.StorageType)
      {
        case StorageType.Double:
          o["value"] = U.R(U.ToSI(p.AsDouble(), spec), 6);
          break;
        case StorageType.Integer:
          if (spec == SpecTypeId.Boolean.YesNo) o["value"] = p.AsInteger() != 0;
          else o["value"] = p.AsInteger();
          break;
        case StorageType.String:
          o["value"] = p.AsString();
          break;
        case StorageType.ElementId:
          var id = p.AsElementId();
          o["value"] = Ids.Str(id);
          var target = doc.GetElement(id);
          if (target != null) o["element_name"] = target.Name;
          break;
      }
      var display = p.AsValueString();
      if (!string.IsNullOrEmpty(display)) o["display"] = display;
      return o;
    }

    public static ForgeTypeId SafeSpec(Definition d)
    {
      try { return d.GetDataType(); } catch { return null; }
    }
  }
}
