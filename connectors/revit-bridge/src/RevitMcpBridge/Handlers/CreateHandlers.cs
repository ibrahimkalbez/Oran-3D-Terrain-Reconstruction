using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Structure;
using McpBridge.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using RevitMcpBridge.App;
using RevitMcpBridge.Core;

namespace RevitMcpBridge.Handlers
{
  /// <summary>
  /// revit.create_geometry — the specs of rhino.create_geometry as DirectShapes (trees, analysis
  /// meshes, wind domains, sketches), tagged with user text and a pseudo-layer.
  /// revit.create_elements — native Revit elements: levels, grids, walls, floors, roofs,
  /// family instances, model lines, rooms, masses and terrain.
  /// </summary>
  public static class CreateHandlers
  {
    public const string AppId = "ClaudeMcpBridge";

    public static void Register(RpcDispatcher d)
    {
      d.Register("revit.create_geometry", CreateGeometry);
      d.Register("revit.create_elements", CreateElements);
    }

    // ------------------------------------------------------------------ materials

    /// <summary>Shading material "MCP #RRGGBB" (reused); the colour shows in Shaded views.</summary>
    public static ElementId Material(Document doc, Color c, double transparency)
    {
      var name = "MCP " + RArgs.Hex(c) + (transparency > 0 ? " T" + (int)transparency : "");
      var existing = new FilteredElementCollector(doc).OfClass(typeof(Material)).FirstOrDefault(m => m.Name == name);
      if (existing != null) return existing.Id;
      var id = Autodesk.Revit.DB.Material.Create(doc, name);
      var mat = (Material)doc.GetElement(id);
      mat.Color = c;
      mat.UseRenderAppearanceForShading = false;
      if (transparency > 0) mat.Transparency = (int)Math.Max(0, Math.Min(100, transparency));
      return id;
    }

    private static ElementId CategoryFor(Document doc, JObject spec)
    {
      var cat = RArgs.Str(spec, "category");
      if (cat == null)
      {
        var layer = (RArgs.Str(spec, "layer") ?? "").ToLowerInvariant();
        var kind = spec["user_text"]?["mcp.kind"]?.ToString() ?? "";
        if (kind == "tree" || layer.Contains("vegetation") || layer.Contains("végétation") || layer.Contains("tree") || layer.Contains("arbre")) cat = "OST_Planting";
        else if (kind == "building" || layer.Contains("mass") || layer.Contains("bâti") || layer.Contains("building")) cat = "OST_Mass";
        else cat = "OST_GenericModel";
      }
      var id = ElementQuery.ResolveCategory(doc, cat);
      if (!DirectShape.IsValidCategoryId(id, doc))
        throw RpcException.InvalidParams("Category '" + cat + "' cannot hold free-form geometry (DirectShape). Use Generic Models, Mass, Planting, Site, Topography…");
      return id;
    }

    // ------------------------------------------------------------------ revit.create_geometry

    private static JToken CreateGeometry(JObject p)
    {
      var doc = RevitContext.Doc;
      var specs = new List<JObject>();
      if (p["geometries"] is JArray arr) specs.AddRange(arr.OfType<JObject>());
      else if (p["type"] != null) specs.Add(p);
      if (specs.Count == 0) throw RpcException.InvalidParams("Give 'geometries': [ {\"type\": ...}, ... ] or a single spec with 'type'.");
      var defaults = p["defaults"] as JObject ?? new JObject();
      bool select = RArgs.Bool(p, "select", false);

      return Tx.Run(doc, "create geometry", () =>
      {
        var created = new JArray();
        var newIds = new List<ElementId>();
        for (int i = 0; i < specs.Count; i++)
        {
          var spec = (JObject)defaults.DeepClone();
          spec.Merge(specs[i], new JsonMergeSettings { MergeArrayHandling = MergeArrayHandling.Replace });
          ShapeFactory.Shape shape;
          try
          {
            shape = ShapeFactory.Create(doc, spec, (c, t) => Material(doc, c, t));
          }
          catch (RpcException ex)
          {
            throw new RpcException(ex.Code, $"geometries[{i}] ({spec.Value<string>("type")}): {ex.Message}", ex.ErrorData);
          }
          catch (Exception ex)
          {
            throw RpcException.Failed($"geometries[{i}] ({spec.Value<string>("type")}): Revit refused the geometry — {ex.Message}");
          }
          var ds = DirectShape.CreateElement(doc, CategoryFor(doc, spec));
          ds.ApplicationId = AppId;
          ds.ApplicationDataId = Guid.NewGuid().ToString();
          if (!ds.IsValidShape(shape.Objects))
          {
            // Curves this category cannot hold as a DirectShape become model lines.
            if (shape.Objects.Count > 0 && shape.Objects.All(o => o is Curve))
            {
              doc.Delete(ds.Id);
              foreach (var line in ModelLines(doc, shape.Objects.Cast<Curve>(), spec))
              {
                newIds.Add(line.Id);
                created.Add(new JObject { ["index"] = i, ["id"] = Ids.Str(line.Id), ["geometry_type"] = shape.Kind, ["category"] = line.Category?.Name, ["layer"] = RArgs.Str(spec, "layer") ?? ElementQuery.LayerOf(line, null) });
              }
              continue;
            }
            throw RpcException.Failed($"geometries[{i}] ({shape.Kind}): this geometry is not valid for a DirectShape.");
          }
          ds.SetShape(shape.Objects);
          var name = RArgs.Str(spec, "name") ?? (shape.Kind == "text_dot" ? RArgs.Str(spec, "text") : null);
          if (!string.IsNullOrEmpty(name))
          {
            try { ds.SetName(name); } catch { /* older API: the name stays in user text */ }
          }
          var values = new Dictionary<string, JToken>();
          if (spec["user_text"] is JObject ut) foreach (var kv in ut) values[kv.Key] = kv.Value;
          var layer = RArgs.Str(spec, "layer");
          if (!string.IsNullOrEmpty(layer)) values[UserData.LayerKey] = layer;
          if (!string.IsNullOrEmpty(name)) values["name"] = name;
          values["mcp.geometry"] = shape.Kind;
          UserData.Write(ds, values);
          newIds.Add(ds.Id);
          var item = new JObject
          {
            ["index"] = i,
            ["id"] = Ids.Str(ds.Id),
            ["geometry_type"] = shape.Kind,
            ["category"] = ds.Category?.Name,
            ["layer"] = layer ?? "Category::" + ds.Category?.Name,
          };
          if (!string.IsNullOrEmpty(name)) item["name"] = name;
          created.Add(item);
        }
        doc.Regenerate();
        foreach (var item in created.OfType<JObject>())
        {
          var e = doc.GetElement(Ids.From(long.Parse((string)item["id"])));
          item["bbox"] = Describe.BBox(e?.get_BoundingBox(null));
        }
        if (select) RevitContext.UiDoc.Selection.SetElementIds(newIds);
        return new JObject { ["created_count"] = created.Count, ["created"] = created };
      });
    }

    /// <summary>Model lines on sketch planes through each curve, tagged like a DirectShape would be.</summary>
    private static List<Element> ModelLines(Document doc, IEnumerable<Curve> curves, JObject spec)
    {
      var made = new List<Element>();
      foreach (var c in curves)
      {
        Plane plane;
        if (c is Arc arc) plane = Plane.CreateByNormalAndOrigin(arc.Normal, arc.Center);
        else if (c is Ellipse el) plane = Plane.CreateByNormalAndOrigin(el.Normal, el.Center);
        else
        {
          var a = c.GetEndPoint(0);
          var b = c.GetEndPoint(1);
          var normal = Math.Abs(a.Z - b.Z) < 1e-6 ? XYZ.BasisZ : (b - a).CrossProduct(XYZ.BasisZ);
          plane = Plane.CreateByNormalAndOrigin(normal.IsZeroLength() ? XYZ.BasisX : normal.Normalize(), a);
        }
        var mc = doc.Create.NewModelCurve(c, SketchPlane.Create(doc, plane));
        var values = new Dictionary<string, JToken>();
        if (spec["user_text"] is JObject ut) foreach (var kv in ut) values[kv.Key] = kv.Value;
        if (RArgs.Str(spec, "layer") is string layer) values[UserData.LayerKey] = layer;
        if (values.Count > 0) UserData.Write(mc, values);
        made.Add(mc);
      }
      return made;
    }

    // ------------------------------------------------------------------ revit.create_elements

    public static readonly string[] Kinds = { "level", "grid", "wall", "floor", "roof", "family_instance", "model_line", "room", "mass", "terrain" };

    private static JToken CreateElements(JObject p)
    {
      var doc = RevitContext.Doc;
      var specs = new List<JObject>();
      if (p["elements"] is JArray arr) specs.AddRange(arr.OfType<JObject>());
      else if (p["kind"] != null) specs.Add(p);
      if (specs.Count == 0) throw RpcException.InvalidParams("Give 'elements': [{\"kind\": \"" + string.Join("|", Kinds) + "\", ...}].");
      var defaults = p["defaults"] as JObject ?? new JObject();

      return Tx.Run(doc, "create elements", () =>
      {
        var created = new JArray();
        for (int i = 0; i < specs.Count; i++)
        {
          var spec = (JObject)defaults.DeepClone();
          spec.Merge(specs[i], new JsonMergeSettings { MergeArrayHandling = MergeArrayHandling.Replace });
          var kind = (RArgs.Str(spec, "kind") ?? "").Trim().ToLowerInvariant();
          List<Element> made;
          try
          {
            made = CreateOne(doc, kind, spec);
            doc.Regenerate();
          }
          catch (RpcException ex)
          {
            throw new RpcException(ex.Code, $"elements[{i}] ({kind}): {ex.Message}", ex.ErrorData);
          }
          catch (Exception ex) when (!(ex is RpcException))
          {
            throw RpcException.Failed($"elements[{i}] ({kind}): {ex.Message}");
          }
          var values = new Dictionary<string, JToken>();
          if (spec["user_text"] is JObject ut) foreach (var kv in ut) values[kv.Key] = kv.Value;
          if (RArgs.Str(spec, "layer") is string layer) values[UserData.LayerKey] = layer;
          foreach (var e in made)
          {
            if (values.Count > 0) UserData.Write(e, values);
            if (spec["parameters"] is JObject prms)
              foreach (var kv in prms)
              {
                var prm = ParameterHandlers.Find(e, kv.Key) ?? throw RpcException.NotFound($"elements[{i}]: parameter '{kv.Key}' not found on {e.Category?.Name}.");
                ParameterHandlers.Write(doc, prm, kv.Value, "set");
              }
            var o = Describe.Element(doc, e, false);
            o["index"] = i;
            o["kind"] = kind;
            created.Add(o);
          }
        }
        return new JObject { ["created_count"] = created.Count, ["created"] = created };
      });
    }

    private static List<Element> CreateOne(Document doc, string kind, JObject s)
    {
      switch (kind)
      {
        case "level":
          {
            var level = Level.Create(doc, U.ToFeet(RArgs.NumRequired(s, "elevation")));
            if (RArgs.Str(s, "name") is string name) level.Name = name;
            var result = new List<Element> { level };
            if (RArgs.Bool(s, "create_view", true))
            {
              var vft = new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().FirstOrDefault(v => v.ViewFamily == ViewFamily.FloorPlan);
              if (vft != null) ViewPlan.Create(doc, vft.Id, level.Id);
            }
            return result;
          }
        case "grid":
          {
            var grid = Grid.Create(doc, Line.CreateBound(RArgs.Point(RArgs.Get(s, "from", "start"), "from"), RArgs.Point(RArgs.Get(s, "to", "end"), "to")));
            if (RArgs.Str(s, "name") is string name) grid.Name = name;
            return new List<Element> { grid };
          }
        case "wall":
          {
            var level = LevelOf(doc, s);
            var type = TypeOf<WallType>(doc, s, BuiltInCategory.OST_Walls);
            double height = U.ToFeet(RArgs.Num(s, "height", 3));
            double offset = U.ToFeet(RArgs.Num(s, "base_offset", 0));
            bool structural = RArgs.Bool(s, "structural", false);
            var curves = new List<Curve>();
            if (RArgs.Has(s, "points"))
            {
              var loop = ShapeFactory.Loop(RArgs.Points(RArgs.Get(s, "points")), RArgs.Bool(s, "closed", false));
              curves.AddRange(loop);
            }
            else curves.Add(Line.CreateBound(Flat(RArgs.Point(RArgs.Get(s, "from", "start"), "from"), level), Flat(RArgs.Point(RArgs.Get(s, "to", "end"), "to"), level)));
            return curves.Select(c => (Element)Wall.Create(doc, Flatten(c, level), type.Id, level.Id, height, offset, false, structural)).ToList();
          }
        case "floor":
          {
            var level = LevelOf(doc, s);
            var type = TypeOf<FloorType>(doc, s, BuiltInCategory.OST_Floors);
            var loops = Loops(s, level);
            var floor = Floor.Create(doc, loops, type.Id, level.Id);
            if (RArgs.Has(s, "offset")) floor.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)?.Set(U.ToFeet(RArgs.Num(s, "offset", 0)));
            return new List<Element> { floor };
          }
        case "roof":
          {
            var level = LevelOf(doc, s);
            var type = TypeOf<RoofType>(doc, s, BuiltInCategory.OST_Roofs);
            var loop = Loops(s, level)[0];
            var footprint = new CurveArray();
            foreach (var c in loop) footprint.Append(c);
            var roof = doc.Create.NewFootPrintRoof(footprint, level, type, out ModelCurveArray edges);
            double slope = RArgs.Num(s, "slope_deg", 0);
            if (slope > 0)
            {
              foreach (ModelCurve edge in edges)
              {
                roof.set_DefinesSlope(edge, true);
                roof.set_SlopeAngle(edge, Math.Tan(slope * Math.PI / 180));
              }
            }
            if (RArgs.Has(s, "offset")) roof.get_Parameter(BuiltInParameter.ROOF_LEVEL_OFFSET_PARAM)?.Set(U.ToFeet(RArgs.Num(s, "offset", 0)));
            return new List<Element> { roof };
          }
        case "family_instance":
          {
            var symbol = Symbol(doc, s);
            if (!symbol.IsActive) symbol.Activate();
            var level = RArgs.Has(s, "level") ? LevelOf(doc, s) : null;
            var points = RArgs.Has(s, "locations", "points") ? RArgs.Points(RArgs.Get(s, "locations", "points")) : new List<XYZ> { RArgs.Point(RArgs.Get(s, "location", "point"), "location") };
            double rotation = RArgs.Num(s, "rotation_deg", RArgs.Num(s, "rotation", 0)) * Math.PI / 180;
            var made = new List<Element>();
            foreach (var pt in points)
            {
              var fi = level != null
                ? doc.Create.NewFamilyInstance(new XYZ(pt.X, pt.Y, pt.Z), symbol, level, StructuralType.NonStructural)
                : doc.Create.NewFamilyInstance(pt, symbol, StructuralType.NonStructural);
              if (level != null && Math.Abs(pt.Z - level.Elevation) > 1e-6)
                fi.get_Parameter(BuiltInParameter.INSTANCE_ELEVATION_PARAM)?.Set(pt.Z - level.Elevation);
              if (Math.Abs(rotation) > 1e-9)
                ElementTransformUtils.RotateElement(doc, fi.Id, Line.CreateUnbound(pt, XYZ.BasisZ), rotation);
              made.Add(fi);
            }
            return made;
          }
        case "model_line":
          {
            var pts = RArgs.Has(s, "points") ? RArgs.Points(RArgs.Get(s, "points")) : new List<XYZ> { RArgs.Point(RArgs.Get(s, "from", "start"), "from"), RArgs.Point(RArgs.Get(s, "to", "end"), "to") };
            var loop = ShapeFactory.Loop(pts, RArgs.Bool(s, "closed", false));
            GraphicsStyle style = null;
            if (RArgs.Str(s, "line_style") is string styleName)
            {
              var lines = Category.GetCategory(doc, BuiltInCategory.OST_Lines);
              var sub = lines.SubCategories.Cast<Category>().FirstOrDefault(c => string.Equals(c.Name, styleName, StringComparison.OrdinalIgnoreCase));
              if (sub == null && RArgs.Bool(s, "create_style", true))
              {
                sub = doc.Settings.Categories.NewSubcategory(lines, styleName);
                if (RArgs.Colour(s["color"]) is Color c) sub.LineColor = c;
              }
              style = sub?.GetGraphicsStyle(GraphicsStyleType.Projection);
            }
            var made = new List<Element>();
            foreach (var c in loop)
            {
              var a = c.GetEndPoint(0);
              var b = c.GetEndPoint(1);
              var normal = Math.Abs(a.Z - b.Z) < 1e-6 ? XYZ.BasisZ : (b - a).CrossProduct(XYZ.BasisZ).Normalize();
              if (normal.IsZeroLength()) normal = XYZ.BasisX;
              var plane = SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(normal, a));
              var mc = doc.Create.NewModelCurve(c, plane);
              if (style != null) mc.LineStyle = style;
              made.Add(mc);
            }
            return made;
          }
        case "room":
          {
            var level = LevelOf(doc, s);
            var pt = RArgs.Point(RArgs.Get(s, "location", "point"), "location");
            var room = doc.Create.NewRoom(level, new UV(pt.X, pt.Y));
            if (RArgs.Str(s, "name") is string name) room.Name = name;
            if (RArgs.Str(s, "number") is string number) room.Number = number;
            return new List<Element> { room };
          }
        case "mass":
          {
            // A footprint extruded as a DirectShape in the Mass category: the massing study object.
            var spec = (JObject)s.DeepClone();
            spec["type"] = "extrusion";
            var shape = ShapeFactory.Create(doc, spec, (c, t) => Material(doc, c, t));
            var ds = DirectShape.CreateElement(doc, new ElementId(BuiltInCategory.OST_Mass));
            ds.ApplicationId = AppId;
            ds.ApplicationDataId = Guid.NewGuid().ToString();
            ds.SetShape(shape.Objects);
            if (RArgs.Str(s, "name") is string name) { try { ds.SetName(name); } catch { /* kept as user text */ } }
            return new List<Element> { ds };
          }
        case "terrain":
          return new List<Element> { Terrain(doc, s) };
      }
      throw RpcException.InvalidParams("Unknown kind '" + kind + "'. Use " + string.Join(", ", Kinds) + ".");
    }

    private static XYZ Flat(XYZ p, Level level) => new XYZ(p.X, p.Y, level.Elevation);

    private static Curve Flatten(Curve c, Level level)
    {
      var a = c.GetEndPoint(0);
      var b = c.GetEndPoint(1);
      return Line.CreateBound(new XYZ(a.X, a.Y, level.Elevation), new XYZ(b.X, b.Y, level.Elevation));
    }

    private static List<CurveLoop> Loops(JObject s, Level level)
    {
      var lists = ShapeFactory.Lists(RArgs.Get(s, "points", "profile", "boundary"), "points");
      if (s["holes"] is JArray holes) foreach (var h in holes) lists.Add(RArgs.Points(h, "holes"));
      return lists.Select(l => ShapeFactory.Loop(l.Select(p => Flat(p, level)).ToList(), true)).ToList();
    }

    public static Level LevelOf(Document doc, JObject s)
    {
      var levels = new FilteredElementCollector(doc).OfClass(typeof(Level)).Cast<Level>().OrderBy(l => l.Elevation).ToList();
      if (levels.Count == 0) throw RpcException.NotFound("The project has no level.");
      var t = RArgs.Get(s, "level");
      if (t == null) return levels[0];
      var key = t.ToString();
      var hit = levels.FirstOrDefault(l => string.Equals(l.Name, key, StringComparison.OrdinalIgnoreCase) || Ids.Str(l.Id) == key);
      if (hit != null) return hit;
      throw RpcException.NotFound("No level '" + key + "'.", new JObject { ["levels"] = new JArray(levels.Select(l => l.Name)) });
    }

    private static T TypeOf<T>(Document doc, JObject s, BuiltInCategory cat) where T : ElementType
    {
      var all = new FilteredElementCollector(doc).OfClass(typeof(T)).Cast<T>().Where(t => t.Category != null && t.Category.Id.Equals(new ElementId(cat))).ToList();
      if (all.Count == 0) throw RpcException.NotFound("No " + typeof(T).Name + " in the project.");
      var name = RArgs.Str(s, "type");
      if (name == null) return all[0];
      var rx = ElementQuery.Wildcard(name);
      return all.FirstOrDefault(t => rx.IsMatch(t.Name)) ?? throw RpcException.NotFound("No type '" + name + "'.", new JObject { ["types"] = new JArray(all.Select(t => t.Name)) });
    }

    private static FamilySymbol Symbol(Document doc, JObject s)
    {
      var family = RArgs.Str(s, "family");
      var type = RArgs.Str(s, "type");
      if (family == null && type == null) throw RpcException.InvalidParams("Give 'family' and/or 'type' (see revit.get_types).");
      var famRx = family != null ? ElementQuery.Wildcard(family) : null;
      var typeRx = type != null ? ElementQuery.Wildcard(type) : null;
      var symbols = new FilteredElementCollector(doc).OfClass(typeof(FamilySymbol)).Cast<FamilySymbol>()
        .Where(x => (famRx == null || famRx.IsMatch(x.FamilyName)) && (typeRx == null || typeRx.IsMatch(x.Name))).ToList();
      if (symbols.Count == 0) throw RpcException.NotFound("No loaded family type matches (family '" + family + "', type '" + type + "'). Load the family first.");
      return symbols[0];
    }

    /// <summary>
    /// Terrain from points (DEM samples): Toposolid on Revit 2024+, TopographySurface before.
    /// Reflection keeps one build for every version.
    /// </summary>
    private static Element Terrain(Document doc, JObject s)
    {
      var pts = RArgs.Points(RArgs.Get(s, "points"), "points");
      if (pts.Count < 3) throw RpcException.InvalidParams("A terrain needs at least 3 points.");
      var api = typeof(Wall).Assembly;
      var toposolid = api.GetType("Autodesk.Revit.DB.Toposolid");
      if (toposolid != null)
      {
        var level = LevelOf(doc, s);
        var typeClass = api.GetType("Autodesk.Revit.DB.ToposolidType");
        var typeId = new FilteredElementCollector(doc).OfClass(typeClass).FirstElementId();
        var create = toposolid.GetMethod("Create", new[] { typeof(Document), typeof(IList<XYZ>), typeof(ElementId), typeof(ElementId) });
        if (create != null && typeId != null && typeId != ElementId.InvalidElementId)
        {
          try
          {
            return (Element)create.Invoke(null, new object[] { doc, pts, typeId, level.Id });
          }
          catch (TargetInvocationException ex)
          {
            throw RpcException.Failed("Toposolid.Create failed: " + (ex.InnerException?.Message ?? ex.Message));
          }
        }
      }
      var topo = api.GetType("Autodesk.Revit.DB.Architecture.TopographySurface");
      var legacy = topo?.GetMethod("Create", new[] { typeof(Document), typeof(IList<XYZ>) });
      if (legacy == null) throw RpcException.Failed("This Revit version has neither Toposolid nor TopographySurface creation.");
      try
      {
        return (Element)legacy.Invoke(null, new object[] { doc, pts });
      }
      catch (TargetInvocationException ex)
      {
        throw RpcException.Failed("TopographySurface.Create failed: " + (ex.InnerException?.Message ?? ex.Message));
      }
    }
  }
}
