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
  /// revit.get_objects, revit.delete_objects, revit.select_objects, revit.set_object_data and
  /// revit.transform_objects — same parameters and results as their rhino.* counterparts.
  /// </summary>
  public static class ObjectHandlers
  {
    public static void Register(RpcDispatcher d)
    {
      d.Register("revit.get_objects", GetObjects);
      d.Register("revit.delete_objects", DeleteObjects);
      d.Register("revit.select_objects", SelectObjects);
      d.Register("revit.set_object_data", SetObjectData);
      d.Register("revit.transform_objects", TransformObjects);
    }

    // ------------------------------------------------------------------ revit.get_objects

    private static JToken GetObjects(JObject p)
    {
      var doc = RevitContext.Doc;
      var elements = ElementQuery.From(doc, p).Run(doc);
      int offset = Math.Max(0, RArgs.Int(p, "offset", 0));
      int limit = Math.Max(1, Math.Min(5000, RArgs.Int(p, "limit", 200)));
      string detail = (RArgs.Str(p, "detail", "summary") ?? "summary").ToLowerInvariant();
      bool full = detail == "full" || detail == "parameters";

      var page = new JArray();
      foreach (var e in elements.Skip(offset).Take(limit))
      {
        var o = Describe.Element(doc, e, full);
        if (detail == "parameters") o["parameters"] = ParameterHandlers.ParameterList(doc, e, null, false);
        page.Add(o);
      }
      var byType = elements.GroupBy(e => e.Category?.Name ?? e.GetType().Name).ToDictionary(g => g.Key, g => g.Count());
      var byLayer = elements.GroupBy(e => ElementQuery.LayerOf(e, UserData.Read(e)) ?? "?")
        .OrderByDescending(g => g.Count()).Take(50).ToDictionary(g => g.Key, g => g.Count());
      return new JObject
      {
        ["total"] = elements.Count,
        ["offset"] = offset,
        ["returned"] = page.Count,
        ["truncated"] = offset + page.Count < elements.Count,
        ["by_type"] = JObject.FromObject(byType),
        ["by_layer"] = JObject.FromObject(byLayer),
        ["objects"] = page,
      };
    }

    // ------------------------------------------------------------------ revit.delete_objects

    private static JToken DeleteObjects(JObject p)
    {
      var doc = RevitContext.Doc;
      var query = ElementQuery.From(doc, p);
      if (query.IsEmpty) throw RpcException.InvalidParams("Refusing to delete without ids or a filter.");
      List<Element> elements;
      try
      {
        elements = query.Run(doc);
      }
      catch (RpcException ex) when (ex.Code == RpcErrorCodes.NotFound && query.Ids.Count > 0)
      {
        elements = new List<Element>();
      }
      int max = RArgs.Int(p, "max_count", 1000);
      if (query.Ids.Count == 0 && elements.Count > max)
        throw RpcException.InvalidParams($"The filter matches {elements.Count} elements (> max_count={max}). Narrow it or raise max_count.");

      if (RArgs.Bool(p, "dry_run", false))
      {
        return new JObject
        {
          ["dry_run"] = true,
          ["would_delete"] = elements.Count,
          ["objects"] = new JArray(elements.Take(200).Select(e => Describe.Element(doc, e, false))),
        };
      }
      if (elements.Count == 0) return new JObject { ["deleted"] = 0, ["requested"] = 0, ["ids"] = new JArray() };

      return Tx.Run(doc, "delete elements", () =>
      {
        var ids = elements.Select(e => e.Id).ToList();
        var removed = doc.Delete(ids);
        return new JObject
        {
          ["deleted"] = elements.Count,
          ["requested"] = elements.Count,
          ["deleted_with_dependents"] = removed?.Count ?? 0,
          ["ids"] = new JArray(ids.Take(500).Select(Ids.Str)),
        };
      });
    }

    // ------------------------------------------------------------------ revit.select_objects

    private static JToken SelectObjects(JObject p)
    {
      var doc = RevitContext.Doc;
      var uidoc = RevitContext.UiDoc;
      var mode = (RArgs.Str(p, "mode", "replace") ?? "replace").ToLowerInvariant();
      var current = new HashSet<long>(uidoc.Selection.GetElementIds().Select(Ids.Of));
      if (mode == "clear" || mode == "none")
      {
        uidoc.Selection.SetElementIds(new List<ElementId>());
        return new JObject { ["unselected"] = current.Count, ["selected_count"] = 0 };
      }
      var elements = ElementQuery.From(doc, p).Run(doc);
      var ids = new HashSet<long>(elements.Select(e => Ids.Of(e.Id)));
      HashSet<long> next;
      switch (mode)
      {
        case "add": next = new HashSet<long>(current.Concat(ids)); break;
        case "remove": next = new HashSet<long>(current.Where(i => !ids.Contains(i))); break;
        case "replace": next = ids; break;
        default: throw RpcException.InvalidParams("'mode' must be replace, add, remove or clear.");
      }
      uidoc.Selection.SetElementIds(next.Select(Ids.From).ToList());
      if (RArgs.Bool(p, "zoom", false) && ids.Count > 0)
      {
        try { uidoc.ShowElements(ids.Select(Ids.From).ToList()); } catch { /* the active view cannot show them */ }
      }
      return new JObject
      {
        ["mode"] = mode,
        ["matched"] = elements.Count,
        ["changed"] = next.Count != current.Count || next.Any(i => !current.Contains(i)),
        ["selected_count"] = next.Count,
      };
    }

    // ------------------------------------------------------------------ revit.set_object_data

    /// <summary>
    /// Writes user text (extensible storage), the mcp.layer pseudo-layer, the name (where Revit
    /// allows it), Comments and Mark. target="document" writes on Project Information.
    /// </summary>
    private static JToken SetObjectData(JObject p)
    {
      var doc = RevitContext.Doc;
      if (string.Equals(RArgs.Str(p, "target"), "document", StringComparison.OrdinalIgnoreCase))
      {
        var values = p["user_text"] as JObject ?? throw RpcException.InvalidParams("'user_text' is required.");
        return Tx.Run(doc, "document data", () =>
        {
          var written = UserData.Write(doc.ProjectInformation, values.Properties().ToDictionary(x => x.Name, x => x.Value));
          return new JObject { ["target"] = "document", ["written"] = written };
        });
      }

      var elements = ElementQuery.Target(doc, p).Run(doc);
      if (elements.Count == 0) throw RpcException.NotFound("No element matches.");
      var userText = p["user_text"] as JObject;
      var layer = RArgs.Str(p, "layer");
      var name = RArgs.Str(p, "name");
      var comments = RArgs.Str(p, "comments");
      var mark = RArgs.Str(p, "mark");
      if (userText == null && layer == null && name == null && comments == null && mark == null)
        throw RpcException.InvalidParams("Nothing to write: give user_text, layer, name, comments or mark.");

      return Tx.Run(doc, "element data", () =>
      {
        var results = new JArray();
        foreach (var e in elements)
        {
          var o = new JObject { ["id"] = Ids.Str(e.Id) };
          var values = new Dictionary<string, JToken>();
          if (userText != null) foreach (var kv in userText) values[kv.Key] = kv.Value;
          if (layer != null) values[UserData.LayerKey] = layer;
          if (name != null)
          {
            try
            {
              e.Name = name;
              o["name"] = name;
            }
            catch
            {
              values["name"] = name; // Revit does not allow renaming this element: kept as user text
              o["name_stored_as_user_text"] = true;
            }
          }
          if (values.Count > 0) o["user_text"] = UserData.Write(e, values);
          if (comments != null) o["comments_set"] = SetText(e, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, comments);
          if (mark != null) o["mark_set"] = SetText(e, BuiltInParameter.ALL_MODEL_MARK, mark);
          results.Add(o);
        }
        return new JObject { ["updated_count"] = results.Count, ["objects"] = results };
      });
    }

    private static bool SetText(Element e, BuiltInParameter bip, string value)
    {
      var prm = e.get_Parameter(bip);
      if (prm == null || prm.IsReadOnly) return false;
      return prm.Set(value);
    }

    // ------------------------------------------------------------------ revit.transform_objects

    private static JToken TransformObjects(JObject p)
    {
      var doc = RevitContext.Doc;
      var elements = ElementQuery.From(doc, p).Run(doc);
      if (elements.Count == 0) throw RpcException.NotFound("No element matches the selection.");
      var ops = new List<JObject>();
      if (p["operations"] is JArray arr) ops.AddRange(arr.OfType<JObject>());
      else if (p["operation"] != null) ops.Add(p);
      if (ops.Count == 0) throw RpcException.InvalidParams("Give 'operations': [{\"operation\": \"translate\"|\"rotate\"|\"mirror\", ...}].");
      bool copy = RArgs.Bool(p, "copy", false);
      int copies = Math.Max(1, RArgs.Int(p, "copies", 1));
      if (copies > 1) copy = true;
      if (copies > 1000) throw RpcException.InvalidParams("'copies' is limited to 1000.");
      var before = Bounds(elements);

      return Tx.Run(doc, copy ? "copy/transform" : "transform", () =>
      {
        var results = new JArray();
        var applied = new JArray(ops.Select(o => (JToken)(RArgs.Str(o, "operation") ?? RArgs.Str(o, "type"))));
        var sources = elements.Select(e => e.Id).ToList();
        for (int k = 1; k <= copies; k++)
        {
          ICollection<ElementId> targets = sources;
          if (copy)
          {
            targets = ElementTransformUtils.CopyElements(doc, sources, XYZ.Zero);
          }
          foreach (var op in ops) Apply(doc, targets, op, before, k);
          int i = 0;
          foreach (var id in targets)
          {
            results.Add(new JObject { ["source"] = Ids.Str(sources[Math.Min(i, sources.Count - 1)]), ["id"] = Ids.Str(id), ["copy_index"] = copy ? k : 0 });
            i++;
          }
        }
        doc.Regenerate();
        var after = Bounds(results.Select(r => doc.GetElement(Ids.From(long.Parse((string)r["id"])))).Where(e => e != null));
        return new JObject
        {
          ["operations"] = applied,
          ["copied"] = copy,
          ["count"] = results.Count,
          ["results"] = results,
          ["bbox_before"] = before == null ? null : Describe.BBox(before),
          ["bbox_after"] = after == null ? null : Describe.BBox(after),
        };
      });
    }

    private static BoundingBoxXYZ Bounds(IEnumerable<Element> elements)
    {
      XYZ min = null, max = null;
      foreach (var e in elements)
      {
        var b = e.get_BoundingBox(null);
        if (b == null) continue;
        min = min == null ? b.Min : new XYZ(Math.Min(min.X, b.Min.X), Math.Min(min.Y, b.Min.Y), Math.Min(min.Z, b.Min.Z));
        max = max == null ? b.Max : new XYZ(Math.Max(max.X, b.Max.X), Math.Max(max.Y, b.Max.Y), Math.Max(max.Z, b.Max.Z));
      }
      return min == null ? null : new BoundingBoxXYZ { Min = min, Max = max };
    }

    /// <summary>Applies one operation; with copies, translations and rotations are multiplied by the copy index.</summary>
    private static void Apply(Document doc, ICollection<ElementId> ids, JObject op, BoundingBoxXYZ box, int step)
    {
      var kind = (RArgs.Str(op, "operation") ?? RArgs.Str(op, "type") ?? "").Trim().ToLowerInvariant();
      XYZ center = box == null ? XYZ.Zero : (box.Min + box.Max) / 2;
      XYZ Anchor()
      {
        if (RArgs.Has(op, "origin", "center", "base_point")) return RArgs.Point(RArgs.Get(op, "origin", "center", "base_point"), "origin");
        switch ((RArgs.Str(op, "anchor", "center") ?? "center").ToLowerInvariant())
        {
          case "bottom": case "base": return new XYZ(center.X, center.Y, box?.Min.Z ?? 0);
          case "top": return new XYZ(center.X, center.Y, box?.Max.Z ?? 0);
          case "min": return box?.Min ?? XYZ.Zero;
          case "world_origin": case "origin": return XYZ.Zero;
          default: return center;
        }
      }

      switch (kind)
      {
        case "translate":
        case "move":
          {
            XYZ v = RArgs.Has(op, "from") && RArgs.Has(op, "to")
              ? RArgs.Point(RArgs.Get(op, "to")) - RArgs.Point(RArgs.Get(op, "from"))
              : RArgs.Displacement(RArgs.Get(op, "vector", "offset", "translation"), "vector");
            ElementTransformUtils.MoveElements(doc, ids, v * step);
            return;
          }
        case "rotate":
          {
            double angle = RArgs.NumRequired(op, "angle") * Math.PI / 180 * step;
            var axisDir = RArgs.Has(op, "axis") ? RArgs.Vector(RArgs.Get(op, "axis"), "axis").Normalize() : XYZ.BasisZ;
            var origin = Anchor();
            ElementTransformUtils.RotateElements(doc, ids, Line.CreateUnbound(origin, axisDir), angle);
            return;
          }
        case "mirror":
          {
            var normal = RArgs.Vector(RArgs.Get(op, "normal") ?? new JArray(1, 0, 0), "normal").Normalize();
            var plane = Plane.CreateByNormalAndOrigin(normal, Anchor());
            ElementTransformUtils.MirrorElements(doc, ids, plane, false);
            return;
          }
        case "scale":
          throw RpcException.InvalidParams("Revit elements cannot be scaled: change their parameters (revit.set_parameters) or the Dynamo inputs instead.");
      }
      throw RpcException.InvalidParams("Unknown operation '" + kind + "'. Use translate, rotate or mirror.");
    }
  }
}
