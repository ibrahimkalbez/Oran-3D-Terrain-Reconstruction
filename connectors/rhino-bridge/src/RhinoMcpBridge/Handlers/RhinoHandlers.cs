using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Rhino;
using Rhino.Display;
using Rhino.DocObjects;
using Rhino.FileIO;
using Rhino.Geometry;
using RhinoMcpBridge.Core;
using RhinoMcpBridge.Transport;
using Environment = System.Environment;

namespace RhinoMcpBridge.Handlers
{
  /// <summary>rhino.* methods: read, create, modify and export the active Rhino document.</summary>
  public static class RhinoHandlers
  {
    public static void Register(RpcDispatcher d)
    {
      d.Register("rhino.get_document", GetDocument);
      d.Register("rhino.get_objects", GetObjects);
      d.Register("rhino.create_geometry", CreateGeometry);
      d.Register("rhino.transform_objects", TransformObjects);
      d.Register("rhino.delete_objects", DeleteObjects);
      d.Register("rhino.update_object", UpdateObject);
      d.Register("rhino.create_layer", CreateLayer);
      d.Register("rhino.set_object_data", SetObjectData);
      d.Register("rhino.select_objects", SelectObjects);
      d.Register("rhino.run_command", RunCommand);
      d.Register("rhino.export", Export);
      d.Register("rhino.save_document", SaveDocument);
      d.Register("rhino.open_document", OpenDocument);
    }

    internal static JToken WithUndo(RhinoDoc doc, string label, Func<JToken> fn)
    {
      uint record = doc.BeginUndoRecord("MCP: " + label);
      try
      {
        return fn();
      }
      finally
      {
        if (record != 0) doc.EndUndoRecord(record);
        doc.Views.Redraw();
      }
    }

    // ------------------------------------------------------------------ rhino.get_document

    private static JToken GetDocument(JObject p)
    {
      var doc = RhinoUtil.Doc();
      int maxLayers = Args.Int(p, "max_layers", 500);

      var byType = new Dictionary<string, int>();
      var byLayer = new Dictionary<int, int>();
      var box = BoundingBox.Empty;
      int total = 0;
      var all = new ObjectEnumeratorSettings { NormalObjects = true, LockedObjects = true, HiddenObjects = true, IncludeLights = false, IncludeGrips = false };
      foreach (var obj in doc.Objects.GetObjectList(all))
      {
        total++;
        var t = RhinoUtil.TypeName(obj.ObjectType);
        byType[t] = byType.TryGetValue(t, out var n) ? n + 1 : 1;
        int li = obj.Attributes.LayerIndex;
        byLayer[li] = byLayer.TryGetValue(li, out var m) ? m + 1 : 1;
        if (obj.Geometry != null) box.Union(obj.Geometry.GetBoundingBox(false));
      }

      var layers = new JArray();
      int currentLayer = doc.Layers.CurrentLayerIndex;
      foreach (var layer in doc.Layers.Where(l => !l.IsDeleted).OrderBy(l => l.FullPath, StringComparer.OrdinalIgnoreCase).Take(maxLayers))
      {
        layers.Add(new JObject
        {
          ["path"] = layer.FullPath,
          ["color"] = J.Hex(layer.Color),
          ["visible"] = layer.IsVisible,
          ["locked"] = layer.IsLocked,
          ["current"] = layer.Index == currentLayer,
          ["object_count"] = byLayer.TryGetValue(layer.Index, out var c) ? c : 0,
        });
      }

      var blocks = new JArray();
      foreach (var idef in doc.InstanceDefinitions.Where(i => i != null && !i.IsDeleted))
      {
        blocks.Add(new JObject
        {
          ["name"] = idef.Name,
          ["instance_count"] = idef.GetReferences(0)?.Length ?? 0,
          ["object_count"] = idef.ObjectCount,
        });
      }

      var materials = new JArray(doc.RenderMaterials.Select(m => m.Name).Where(n => !string.IsNullOrEmpty(n)).Distinct());
      if (materials.Count == 0)
        materials = new JArray(doc.Materials.Where(m => !m.IsDeleted).Select(m => m.Name).Where(n => !string.IsNullOrEmpty(n)).Distinct());

      var selected = doc.Objects.GetSelectedObjects(false, false).Take(100)
        .Select(o => (JToken)ObjectQuery.Summary(doc, o, false)).ToList();

      var views = new JArray();
      var active = doc.Views.ActiveView;
      foreach (var view in doc.Views.GetViewList(ViewTypeFilter.Model))
      {
        var vp = view.ActiveViewport;
        views.Add(new JObject
        {
          ["name"] = vp.Name,
          ["display_mode"] = vp.DisplayMode?.EnglishName,
          ["projection"] = vp.IsParallelProjection ? "parallel" : "perspective",
          ["active"] = active != null && view.RuntimeSerialNumber == active.RuntimeSerialNumber,
        });
      }

      var docText = new JObject();
      for (int i = 0; i < doc.Strings.Count; i++)
      {
        var key = doc.Strings.GetKey(i);
        if (!string.IsNullOrEmpty(key)) docText[key] = doc.Strings.GetValue(key);
      }

      var anchor = doc.EarthAnchorPoint;
      JToken earth = JValue.CreateNull();
      if (anchor != null && anchor.EarthLocationIsSet())
      {
        earth = new JObject
        {
          ["latitude"] = anchor.EarthBasepointLatitude,
          ["longitude"] = anchor.EarthBasepointLongitude,
          ["elevation"] = anchor.EarthBasepointElevation,
          ["model_base_point"] = J.P(anchor.ModelBasePoint),
          ["name"] = anchor.Name,
        };
      }

      return new JObject
      {
        ["file"] = new JObject
        {
          ["name"] = doc.Name ?? "(untitled)",
          ["path"] = doc.Path,
          ["modified"] = doc.Modified,
        },
        ["units"] = RhinoUtil.Units(doc.ModelUnitSystem),
        ["tolerance"] = new JObject
        {
          ["absolute"] = doc.ModelAbsoluteTolerance,
          ["angle_degrees"] = doc.ModelAngleToleranceDegrees,
          ["relative"] = doc.ModelRelativeTolerance,
        },
        ["object_count"] = total,
        ["objects_by_type"] = JObject.FromObject(byType),
        ["bbox"] = J.BBox(box),
        ["layer_count"] = doc.Layers.Count(l => !l.IsDeleted),
        ["layers"] = layers,
        ["blocks"] = blocks,
        ["materials"] = materials,
        ["selected_count"] = doc.Objects.GetSelectedObjects(false, false).Count(),
        ["selected"] = new JArray(selected),
        ["views"] = views,
        ["named_views"] = new JArray(doc.NamedViews.Select(v => v.Name)),
        ["document_user_text"] = docText,
        ["earth_anchor"] = earth,
        ["grasshopper"] = GrasshopperStatus(),
      };
    }

    /// <summary>Grasshopper state without forcing Grasshopper to load.</summary>
    private static JToken GrasshopperStatus()
    {
      try
      {
        return GrasshopperHandlers.StatusIfLoaded();
      }
      catch (Exception ex)
      {
        return new JObject { ["loaded"] = false, ["error"] = ex.Message };
      }
    }

    // ------------------------------------------------------------------ rhino.get_objects

    private static JToken GetObjects(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var objects = ObjectQuery.From(p).Run(doc);
      int offset = Math.Max(0, Args.Int(p, "offset", 0));
      int limit = Math.Max(1, Math.Min(5000, Args.Int(p, "limit", 200)));
      string detail = (Args.Str(p, "detail", "summary") ?? "summary").ToLowerInvariant();
      bool full = detail == "full" || detail == "geometry";
      bool json = detail == "geometry";

      var page = objects.Skip(offset).Take(limit).Select(o => (JToken)ObjectQuery.Summary(doc, o, full, json)).ToList();
      var byType = objects.GroupBy(o => RhinoUtil.TypeName(o.ObjectType)).ToDictionary(g => g.Key, g => g.Count());
      var byLayer = objects.GroupBy(o => RhinoUtil.LayerPath(doc, o.Attributes.LayerIndex) ?? "?")
        .OrderByDescending(g => g.Count()).Take(50).ToDictionary(g => g.Key, g => g.Count());

      return new JObject
      {
        ["total"] = objects.Count,
        ["offset"] = offset,
        ["returned"] = page.Count,
        ["truncated"] = offset + page.Count < objects.Count,
        ["by_type"] = JObject.FromObject(byType),
        ["by_layer"] = JObject.FromObject(byLayer),
        ["objects"] = new JArray(page),
      };
    }

    // ------------------------------------------------------------------ rhino.create_geometry

    private static JToken CreateGeometry(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var specs = new List<JObject>();
      if (p["geometries"] is JArray arr) specs.AddRange(arr.OfType<JObject>());
      else if (p["type"] != null) specs.Add(p);
      if (specs.Count == 0) throw RpcException.InvalidParams("Give 'geometries': [ {\"type\": ...}, ... ] or a single spec with 'type'.");

      // Defaults applied to every spec (layer, color, user_text, group…).
      var defaults = p["defaults"] as JObject ?? new JObject();

      // Build everything first: a bad spec fails the whole call before the document changes.
      var built = new List<(JObject spec, List<GeometryBase> geoms)>();
      for (int i = 0; i < specs.Count; i++)
      {
        try
        {
          built.Add((specs[i], GeometryFactory.Create(doc, specs[i])));
        }
        catch (RpcException ex)
        {
          throw new RpcException(ex.Code, $"geometries[{i}] ({specs[i].Value<string>("type")}): {ex.Message}", ex.ErrorData);
        }
      }

      bool select = Args.Bool(p, "select", false);
      return WithUndo(doc, "create geometry", () =>
      {
        var created = new JArray();
        if (select) doc.Objects.UnselectAll();
        for (int i = 0; i < built.Count; i++)
        {
          var merged = (JObject)defaults.DeepClone();
          merged.Merge(built[i].spec, new JsonMergeSettings { MergeArrayHandling = MergeArrayHandling.Replace });
          var attr = RhinoUtil.Attributes(doc, merged);
          foreach (var g in built[i].geoms)
          {
            var id = doc.Objects.Add(g, attr);
            if (id == Guid.Empty) throw RpcException.Failed($"Rhino refused geometries[{i}] (invalid geometry).");
            if (select) doc.Objects.Select(id);
            var item = new JObject
            {
              ["index"] = i,
              ["id"] = id.ToString(),
              ["geometry_type"] = GeometryInfo.Kind(g),
              ["layer"] = RhinoUtil.LayerPath(doc, attr.LayerIndex),
              ["bbox"] = J.BBox(g.GetBoundingBox(true)),
            };
            if (!string.IsNullOrEmpty(attr.Name)) item["name"] = attr.Name;
            created.Add(item);
          }
        }
        return new JObject { ["created_count"] = created.Count, ["created"] = created };
      });
    }

    // ------------------------------------------------------------------ rhino.transform_objects

    private static JToken TransformObjects(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var objects = ObjectQuery.From(p).Run(doc);
      if (objects.Count == 0) throw RpcException.NotFound("No object matches the selection.");

      var box = BoundingBox.Empty;
      foreach (var o in objects) box.Union(o.Geometry.GetBoundingBox(true));

      var ops = new List<JObject>();
      if (p["operations"] is JArray arr) ops.AddRange(arr.OfType<JObject>());
      else if (p["operation"] != null) ops.Add(p);
      if (ops.Count == 0) throw RpcException.InvalidParams("Give 'operations': [{\"operation\": \"translate\"|\"rotate\"|\"scale\"|\"mirror\"|\"orient\"|\"matrix\", ...}].");

      var xform = Transform.Identity;
      var applied = new JArray();
      foreach (var op in ops)
      {
        var t = BuildTransform(op, box);
        xform = t * xform;
        applied.Add(op.Value<string>("operation"));
      }

      bool copy = Args.Bool(p, "copy", false);
      int copies = Math.Max(1, Args.Int(p, "copies", 1));
      if (copies > 1) copy = true;
      if (copies > 10000) throw RpcException.InvalidParams("'copies' is limited to 10000.");

      return WithUndo(doc, copy ? "copy/transform" : "transform", () =>
      {
        var results = new JArray();
        var step = xform;
        for (int k = 1; k <= copies; k++)
        {
          foreach (var o in objects)
          {
            var newId = doc.Objects.Transform(o.Id, step, !copy);
            results.Add(new JObject { ["source"] = o.Id.ToString(), ["id"] = newId == Guid.Empty ? null : newId.ToString(), ["copy_index"] = copy ? k : 0 });
          }
          step = xform * step;
        }
        var newBox = BoundingBox.Empty;
        foreach (var r in results)
        {
          var idStr = r.Value<string>("id");
          if (idStr == null) continue;
          var obj = doc.Objects.FindId(new Guid(idStr));
          if (obj != null) newBox.Union(obj.Geometry.GetBoundingBox(true));
        }
        return new JObject
        {
          ["operations"] = applied,
          ["copied"] = copy,
          ["count"] = results.Count,
          ["failed"] = results.Count(r => r.Value<string>("id") == null),
          ["results"] = results,
          ["bbox_before"] = J.BBox(box),
          ["bbox_after"] = J.BBox(newBox),
        };
      });
    }

    internal static Transform BuildTransform(JObject op, BoundingBox selectionBox)
    {
      var kind = (Args.Str(op, "operation") ?? Args.Str(op, "type") ?? "").Trim().ToLowerInvariant();
      Point3d Anchor()
      {
        if (Args.Has(op, "origin", "center", "base_point")) return Args.Point(Args.Get(op, "origin", "center", "base_point"), "origin");
        switch ((Args.Str(op, "anchor", "center") ?? "center").ToLowerInvariant())
        {
          case "bottom": case "base": return new Point3d(selectionBox.Center.X, selectionBox.Center.Y, selectionBox.Min.Z);
          case "top": return new Point3d(selectionBox.Center.X, selectionBox.Center.Y, selectionBox.Max.Z);
          case "min": return selectionBox.Min;
          case "world_origin": case "origin": return Point3d.Origin;
          default: return selectionBox.Center;
        }
      }

      switch (kind)
      {
        case "translate":
        case "move":
          if (Args.Has(op, "from") && Args.Has(op, "to"))
            return Transform.Translation(Args.Point(Args.Get(op, "to")) - Args.Point(Args.Get(op, "from")));
          return Transform.Translation(Args.Vector(Args.Get(op, "vector", "offset", "translation"), "vector"));

        case "rotate":
          {
            double angle = RhinoMath.ToRadians(Args.NumRequired(op, "angle"));
            var axis = Args.Has(op, "axis") ? Args.Vector(Args.Get(op, "axis"), "axis") : Vector3d.ZAxis;
            return Transform.Rotation(angle, axis, Anchor());
          }

        case "scale":
          {
            var origin = Anchor();
            if (Args.Get(op, "factors") is JArray f && f.Count == 3)
            {
              var pl = Plane.WorldXY;
              pl.Origin = origin;
              return Transform.Scale(pl, Args.ToDouble(f[0]), Args.ToDouble(f[1]), Args.ToDouble(f[2]));
            }
            double factor = Args.NumRequired(op, "factor");
            if (factor == 0) throw RpcException.InvalidParams("Scale factor cannot be 0.");
            return Transform.Scale(origin, factor);
          }

        case "mirror":
          {
            Plane pl;
            if (op["plane"] != null) pl = Args.Plane(op["plane"], Plane.WorldYZ);
            else pl = new Plane(Anchor(), Args.Vector(Args.Get(op, "normal") ?? new JArray(1, 0, 0), "normal"));
            return Transform.Mirror(pl);
          }

        case "orient":
          {
            if (Args.Has(op, "from_points") && Args.Has(op, "to_points"))
            {
              var a = Args.Points(Args.Get(op, "from_points"), "from_points");
              var b = Args.Points(Args.Get(op, "to_points"), "to_points");
              if (a.Count < 3 || b.Count < 3) throw RpcException.InvalidParams("3-point orient needs 3 points on each side.");
              return Transform.PlaneToPlane(new Plane(a[0], a[1], a[2]), new Plane(b[0], b[1], b[2]));
            }
            var from = Args.Plane(Args.Get(op, "from", "from_plane"), Plane.WorldXY);
            var to = Args.Plane(Args.Get(op, "to", "to_plane") ?? throw RpcException.InvalidParams("'to' plane is required."), Plane.WorldXY);
            return Transform.PlaneToPlane(from, to);
          }

        case "matrix":
          {
            if (!(op["matrix"] is JArray m) || m.Count != 4) throw RpcException.InvalidParams("'matrix' must be 4 rows of 4 numbers.");
            var t = Transform.Identity;
            for (int r = 0; r < 4; r++)
            {
              if (!(m[r] is JArray row) || row.Count != 4) throw RpcException.InvalidParams("'matrix' must be 4 rows of 4 numbers.");
              for (int c = 0; c < 4; c++) t[r, c] = Args.ToDouble(row[c], "matrix");
            }
            return t;
          }
      }
      throw RpcException.InvalidParams("Unknown operation '" + kind + "'. Use translate, rotate, scale, mirror, orient or matrix.");
    }

    // ------------------------------------------------------------------ rhino.delete_objects

    private static JToken DeleteObjects(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var query = ObjectQuery.From(p);
      if (query.IsEmpty) throw RpcException.InvalidParams("Refusing to delete without ids or a filter.");
      var objects = query.Run(doc);
      int max = Args.Int(p, "max_count", 1000);
      if (query.Ids.Count == 0 && objects.Count > max)
        throw RpcException.InvalidParams($"The filter matches {objects.Count} objects (> max_count={max}). Narrow it or raise max_count.");

      if (Args.Bool(p, "dry_run", false))
      {
        return new JObject
        {
          ["dry_run"] = true,
          ["would_delete"] = objects.Count,
          ["objects"] = new JArray(objects.Take(200).Select(o => ObjectQuery.Summary(doc, o, false))),
        };
      }

      return WithUndo(doc, "delete objects", () =>
      {
        var locked = objects.Where(o => o.IsLocked).Select(o => o.Id).ToList();
        foreach (var id in locked) doc.Objects.Unlock(id, true);
        int deleted = doc.Objects.Delete(objects.Select(o => o.Id), true);
        return new JObject
        {
          ["deleted"] = deleted,
          ["requested"] = objects.Count,
          ["unlocked_before_delete"] = locked.Count,
          ["ids"] = new JArray(objects.Take(500).Select(o => o.Id.ToString())),
        };
      });
    }

    // ------------------------------------------------------------------ rhino.update_object

    private static JToken UpdateObject(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var objects = ObjectQuery.Target(p).Run(doc);
      if (objects.Count == 0) throw RpcException.NotFound("No object matches.");
      var props = p["properties"] as JObject ?? p;
      var geometrySpec = p["geometry"] as JObject;
      if (geometrySpec != null && objects.Count != 1) throw RpcException.InvalidParams("Replacing geometry needs exactly one object.");

      return WithUndo(doc, "update objects", () =>
      {
        var updated = new JArray();
        foreach (var obj in objects)
        {
          var attr = obj.Attributes.Duplicate();
          var changes = new JArray();
          if (props["name"] != null) { attr.Name = props.Value<string>("name") ?? ""; changes.Add("name"); }
          if (Args.Has(props, "layer"))
          {
            attr.LayerIndex = RhinoUtil.EnsureLayer(doc, Args.Str(props, "layer"));
            changes.Add("layer");
          }
          var colourToken = props["color"] ?? props["colour"];
          if (colourToken != null)
          {
            var s = colourToken.Type == JTokenType.Null ? "by_layer" : colourToken.ToString().ToLowerInvariant();
            if (s == "by_layer" || s == "bylayer" || s == "layer") attr.ColorSource = ObjectColorSource.ColorFromLayer;
            else
            {
              attr.ObjectColor = Args.Colour(colourToken).Value;
              attr.ColorSource = ObjectColorSource.ColorFromObject;
            }
            changes.Add("color");
          }
          if (Args.Has(props, "material"))
          {
            var matName = Args.Str(props, "material");
            int mi = doc.Materials.Find(matName, true);
            if (mi < 0) throw RpcException.NotFound("Material '" + matName + "' not found.");
            attr.MaterialIndex = mi;
            attr.MaterialSource = ObjectMaterialSource.MaterialFromObject;
            changes.Add("material");
          }
          if (changes.Count > 0) doc.Objects.ModifyAttributes(obj, attr, true);

          if (Args.Has(props, "visible"))
          {
            if (Args.Bool(props, "visible", true)) doc.Objects.Show(obj.Id, true); else doc.Objects.Hide(obj.Id, true);
            changes.Add("visible");
          }
          if (Args.Has(props, "locked"))
          {
            if (Args.Bool(props, "locked", false)) doc.Objects.Lock(obj.Id, true); else doc.Objects.Unlock(obj.Id, true);
            changes.Add("locked");
          }
          if (geometrySpec != null)
          {
            var geoms = GeometryFactory.Create(doc, geometrySpec);
            if (geoms.Count != 1) throw RpcException.InvalidParams("The replacement spec must produce exactly one geometry.");
            if (!Replace(doc, obj.Id, geoms[0])) throw RpcException.Failed("Rhino could not replace the geometry.");
            changes.Add("geometry");
          }
          var fresh = doc.Objects.FindId(obj.Id);
          updated.Add(new JObject
          {
            ["id"] = obj.Id.ToString(),
            ["changed"] = changes,
            ["object"] = fresh != null ? ObjectQuery.Summary(doc, fresh, false) : null,
          });
        }
        return new JObject { ["updated_count"] = updated.Count, ["updated"] = updated };
      });
    }

    private static bool Replace(RhinoDoc doc, Guid id, GeometryBase g)
    {
      switch (g)
      {
        case Point pt: return doc.Objects.Replace(id, pt.Location);
        case Curve c: return doc.Objects.Replace(id, c);
        case Extrusion ex: return doc.Objects.Replace(id, ex);
        case Brep b: return doc.Objects.Replace(id, b);
        case Surface s: return doc.Objects.Replace(id, s);
        case Mesh m: return doc.Objects.Replace(id, m);
        case SubD sd: return doc.Objects.Replace(id, sd);
        case TextDot dot: return doc.Objects.Replace(id, dot);
        case TextEntity te: return doc.Objects.Replace(id, te);
        case PointCloud pc: return doc.Objects.Replace(id, pc);
      }
      return false;
    }

    // ------------------------------------------------------------------ rhino.create_layer

    private static JToken CreateLayer(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var specs = new List<JObject>();
      if (p["layers"] is JArray arr) specs.AddRange(arr.Select(t => t is JObject o ? o : new JObject { ["path"] = t }));
      else specs.Add(p);

      return WithUndo(doc, "create layers", () =>
      {
        var result = new JArray();
        foreach (var spec in specs)
        {
          var path = Args.Str(spec, "path") ?? Args.Str(spec, "name") ?? throw RpcException.InvalidParams("'path' is required (e.g. \"Urban::Buildings\").");
          bool existed = RhinoUtil.FindLayer(doc, RhinoUtil.NormalizeLayerPath(path)) >= 0 &&
                         doc.Layers.FindByFullPath(RhinoUtil.NormalizeLayerPath(path), -1) >= 0;
          int idx = RhinoUtil.EnsureLayer(doc, path, Args.Colour(Args.Get(spec, "color", "colour")));
          var layer = doc.Layers[idx];
          if (Args.Has(spec, "visible")) layer.IsVisible = Args.Bool(spec, "visible", true);
          if (Args.Has(spec, "locked")) layer.IsLocked = Args.Bool(spec, "locked", false);
          if (Args.Has(spec, "material"))
          {
            int mi = doc.Materials.Find(Args.Str(spec, "material"), true);
            if (mi >= 0) layer.RenderMaterialIndex = mi;
          }
          if (Args.Bool(spec, "current", false)) doc.Layers.SetCurrentLayerIndex(idx, true);
          result.Add(new JObject
          {
            ["path"] = layer.FullPath,
            ["id"] = layer.Id.ToString(),
            ["index"] = idx,
            ["created"] = !existed,
            ["color"] = J.Hex(layer.Color),
            ["current"] = doc.Layers.CurrentLayerIndex == idx,
          });
        }
        return new JObject { ["layers"] = result };
      });
    }

    // ------------------------------------------------------------------ rhino.set_object_data

    public const string MetadataKey = "mcp.metadata";
    public const string ParameterPrefix = "param.";

    private static JToken SetObjectData(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var target = (Args.Str(p, "target", "objects") ?? "objects").ToLowerInvariant();

      if (target == "document")
      {
        return WithUndo(doc, "document data", () =>
        {
          var written = new JObject();
          if (p["user_text"] is JObject ut)
          {
            foreach (var kv in ut)
            {
              if (kv.Value == null || kv.Value.Type == JTokenType.Null) doc.Strings.Delete(kv.Key);
              else doc.Strings.SetString(kv.Key, AsText(kv.Value));
              written[kv.Key] = kv.Value?.DeepClone();
            }
          }
          if (p["metadata"] is JObject meta)
          {
            var merged = MergeJson(doc.Strings.GetValue(MetadataKey), meta);
            doc.Strings.SetString(MetadataKey, merged.ToString(Formatting.None));
            written[MetadataKey] = merged;
          }
          return new JObject { ["target"] = "document", ["written"] = written };
        });
      }

      var objects = ObjectQuery.Target(p).Run(doc);
      if (objects.Count == 0) throw RpcException.NotFound("No object matches.");

      return WithUndo(doc, "object data", () =>
      {
        var results = new JArray();
        foreach (var obj in objects)
        {
          var attr = obj.Attributes.Duplicate();
          if (p["name"] != null) attr.Name = p.Value<string>("name") ?? "";
          if (p["user_text"] is JObject ut)
          {
            foreach (var kv in ut)
            {
              if (kv.Value == null || kv.Value.Type == JTokenType.Null) attr.DeleteUserString(kv.Key);
              else attr.SetUserString(kv.Key, AsText(kv.Value));
            }
          }
          if (p["parameters"] is JObject prm)
          {
            foreach (var kv in prm)
            {
              if (kv.Value == null || kv.Value.Type == JTokenType.Null) attr.DeleteUserString(ParameterPrefix + kv.Key);
              else attr.SetUserString(ParameterPrefix + kv.Key, AsText(kv.Value));
            }
          }
          if (p["metadata"] is JObject meta)
          {
            var merged = MergeJson(attr.GetUserString(MetadataKey), meta);
            attr.SetUserString(MetadataKey, merged.ToString(Formatting.None));
          }
          doc.Objects.ModifyAttributes(obj, attr, true);
          results.Add(new JObject
          {
            ["id"] = obj.Id.ToString(),
            ["name"] = attr.Name ?? "",
            ["user_text"] = RhinoUtil.UserText(attr),
          });
        }
        return new JObject { ["updated_count"] = results.Count, ["objects"] = results };
      });
    }

    private static string AsText(JToken v) => v.Type == JTokenType.String ? (string)v : v.ToString(Formatting.None);

    private static JObject MergeJson(string existing, JObject patch)
    {
      JObject baseObj;
      try { baseObj = string.IsNullOrEmpty(existing) ? new JObject() : JObject.Parse(existing); }
      catch { baseObj = new JObject { ["_previous"] = existing }; }
      baseObj.Merge(patch, new JsonMergeSettings { MergeArrayHandling = MergeArrayHandling.Replace, MergeNullValueHandling = MergeNullValueHandling.Merge });
      foreach (var prop in baseObj.Properties().Where(x => x.Value.Type == JTokenType.Null).ToList()) prop.Remove();
      return baseObj;
    }

    // ------------------------------------------------------------------ rhino.select_objects

    private static JToken SelectObjects(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var mode = (Args.Str(p, "mode", "replace") ?? "replace").ToLowerInvariant();
      if (mode == "clear")
      {
        int n = doc.Objects.UnselectAll();
        doc.Views.Redraw();
        return new JObject { ["unselected"] = n, ["selected_count"] = 0 };
      }
      var objects = ObjectQuery.From(p).Run(doc);
      if (mode == "replace") doc.Objects.UnselectAll();
      int changed = doc.Objects.Select(objects.Select(o => o.Id), mode != "remove");
      if (Args.Bool(p, "zoom", false))
      {
        var box = BoundingBox.Empty;
        foreach (var o in objects) box.Union(o.Geometry.GetBoundingBox(true));
        if (box.IsValid) doc.Views.ActiveView?.ActiveViewport.ZoomBoundingBox(box);
      }
      doc.Views.Redraw();
      return new JObject
      {
        ["mode"] = mode,
        ["matched"] = objects.Count,
        ["changed"] = changed,
        ["selected_count"] = doc.Objects.GetSelectedObjects(false, false).Count(),
      };
    }

    // ------------------------------------------------------------------ rhino.run_command

    private static JToken RunCommand(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var script = Args.Str(p, "command", required: true);
      bool echo = Args.Bool(p, "echo", false);
      uint before = RhinoObject.NextRuntimeSerialNumber;
      bool ok = RhinoApp.RunScript(script, echo);
      var created = doc.Objects.AllObjectsSince(before).Where(o => !o.IsDeleted).ToList();
      doc.Views.Redraw();
      return new JObject
      {
        ["success"] = ok,
        ["command"] = script,
        ["created_count"] = created.Count,
        ["created"] = new JArray(created.Take(500).Select(o => ObjectQuery.Summary(doc, o, false))),
      };
    }

    // ------------------------------------------------------------------ rhino.export

    private static JToken Export(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var path = Args.Str(p, "path", required: true);
      path = Path.GetFullPath(Environment.ExpandEnvironmentVariables(path));
      var dir = Path.GetDirectoryName(path);
      if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
      var query = ObjectQuery.From(p);
      var objects = query.IsEmpty
        ? doc.Objects.GetObjectList(new ObjectEnumeratorSettings { HiddenObjects = false, LockedObjects = true }).ToList()
        : query.Run(doc);
      if (objects.Count == 0) throw RpcException.NotFound("Nothing to export.");

      var ext = Path.GetExtension(path).ToLowerInvariant();
      bool ok;
      if (ext == ".3dm")
      {
        ok = Write3dm(doc, objects, path, Args.Int(p, "version", 8));
      }
      else
      {
        var previous = doc.Objects.GetSelectedObjects(false, false).Select(o => o.Id).ToList();
        doc.Objects.UnselectAll();
        doc.Objects.Select(objects.Select(o => o.Id), true);
        try
        {
          ok = doc.ExportSelected(path);
        }
        finally
        {
          doc.Objects.UnselectAll();
          doc.Objects.Select(previous, true);
          doc.Views.Redraw();
        }
      }
      if (!ok || !File.Exists(path)) throw RpcException.Failed("Export to '" + path + "' failed (is the format supported?).");
      return new JObject
      {
        ["path"] = path,
        ["format"] = ext.TrimStart('.'),
        ["object_count"] = objects.Count,
        ["bytes"] = new FileInfo(path).Length,
      };
    }

    /// <summary>Writes objects (with their layers and attributes) to a new .3dm file.</summary>
    internal static bool Write3dm(RhinoDoc doc, IEnumerable<RhinoObject> objects, string path, int version)
    {
      using (var file = new File3dm())
      {
        file.Settings.ModelUnitSystem = doc.ModelUnitSystem;
        file.Settings.ModelAbsoluteTolerance = doc.ModelAbsoluteTolerance;
        var layers = new Dictionary<string, (int index, Guid id)>(StringComparer.OrdinalIgnoreCase);
        foreach (var obj in objects)
        {
          if (obj.Geometry == null) continue;
          var attr = obj.Attributes.Duplicate();
          attr.LayerIndex = CopyLayerPath(doc, file, layers, obj.Attributes.LayerIndex);
          attr.RemoveFromAllGroups();
          attr.MaterialIndex = -1;
          attr.MaterialSource = ObjectMaterialSource.MaterialFromLayer;
          file.Objects.Add(obj.Geometry, attr);
        }
        return file.Write(path, version);
      }
    }

    /// <summary>Recreates the layer and its parents in the file; returns the file layer index.</summary>
    private static int CopyLayerPath(RhinoDoc doc, File3dm file, Dictionary<string, (int index, Guid id)> created, int layerIndex)
    {
      var names = doc.Layers[layerIndex].FullPath.Split(new[] { "::" }, StringSplitOptions.None);
      Guid parent = Guid.Empty;
      int index = -1;
      for (int i = 0; i < names.Length; i++)
      {
        string partial = string.Join("::", names.Take(i + 1));
        if (!created.TryGetValue(partial, out var entry))
        {
          var copy = new Layer { Name = names[i], ParentLayerId = parent, Id = Guid.NewGuid() };
          int srcIdx = doc.Layers.FindByFullPath(partial, -1);
          if (srcIdx >= 0) copy.Color = doc.Layers[srcIdx].Color;
          file.AllLayers.Add(copy);
          entry = (file.AllLayers.Count - 1, copy.Id);
          created[partial] = entry;
        }
        parent = entry.id;
        index = entry.index;
      }
      return index;
    }

    // ------------------------------------------------------------------ save / open

    private static JToken SaveDocument(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var path = Args.Str(p, "path");
      bool copy = Args.Bool(p, "copy", false);
      bool ok;
      if (string.IsNullOrEmpty(path))
      {
        if (string.IsNullOrEmpty(doc.Path)) throw RpcException.InvalidParams("The document was never saved: give a 'path'.");
        path = doc.Path;
        ok = doc.Save();
      }
      else
      {
        path = Path.GetFullPath(Environment.ExpandEnvironmentVariables(path));
        if (!path.EndsWith(".3dm", StringComparison.OrdinalIgnoreCase)) path += ".3dm";
        var dir = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        ok = copy ? doc.WriteFile(path, new FileWriteOptions { SuppressDialogBoxes = true }) : doc.SaveAs(path);
      }
      if (!ok) throw RpcException.Failed("Saving to '" + path + "' failed.");
      return new JObject { ["path"] = path, ["copy"] = copy, ["bytes"] = File.Exists(path) ? new FileInfo(path).Length : 0 };
    }

    private static JToken OpenDocument(JObject p)
    {
      var path = Path.GetFullPath(Environment.ExpandEnvironmentVariables(Args.Str(p, "path", required: true)));
      if (!File.Exists(path)) throw RpcException.NotFound("File not found: " + path);
      var current = RhinoDoc.ActiveDoc;
      if (current != null && Args.Bool(p, "discard_changes", false)) current.Modified = false;
      var doc = RhinoDoc.Open(path, out bool alreadyOpen);
      if (doc == null) throw RpcException.Failed("Rhino could not open " + path);
      return new JObject
      {
        ["path"] = doc.Path,
        ["name"] = doc.Name,
        ["already_open"] = alreadyOpen,
        ["units"] = RhinoUtil.Units(doc.ModelUnitSystem),
        ["object_count"] = doc.Objects.Count,
      };
    }
  }
}
