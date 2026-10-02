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
  /// analysis.* — identical contract to the Rhino bridge, so the Fusion modules (urban rules,
  /// trees, sun hours, wind domains) run unchanged on Revit models: building footprints,
  /// curves/boundaries as polylines, draping points on a terrain and ray-cast visibility.
  /// Everything in meters, internal coordinates.
  /// </summary>
  public static class AnalysisHandlers
  {
    public static void Register(RpcDispatcher d)
    {
      d.Register("analysis.footprints", Footprints);
      d.Register("analysis.curves", Curves);
      d.Register("analysis.drape_points", DrapePoints);
      d.Register("analysis.ray_visibility", RayVisibility);
    }

    private static readonly HashSet<string> PassThroughParameters = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
      "floors", "niveaux", "etages", "étages", "storeys", "stories", "nb_niveaux", "levels",
      "use", "usage", "affectation", "fonction", "program", "zone", "zonage",
    };

    private static List<Element> Collect(Document doc, JObject p)
    {
      if (p["grasshopper"] != null)
        throw RpcException.InvalidParams("Revit has no Grasshopper: select Revit elements (categories, layer, user_text…) — Dynamo results are Revit elements.");
      var query = ElementQuery.From(doc, p);
      if (query.IsEmpty) throw RpcException.InvalidParams("Give a filter (categories, layer, ids, user_text…).");
      return query.Run(doc);
    }

    /// <summary>User text plus the few parameters the urban rules read (floors, use, zone).</summary>
    private static JObject UserTextOf(Document doc, Element e)
    {
      var ut = UserData.Read(e);
      foreach (Parameter prm in e.Parameters)
      {
        var name = prm.Definition?.Name;
        if (name == null || !PassThroughParameters.Contains(name) || ut[name] != null || !prm.HasValue) continue;
        var v = prm.StorageType == StorageType.String ? prm.AsString() : prm.StorageType == StorageType.Integer ? prm.AsInteger().ToString() : prm.AsValueString();
        if (!string.IsNullOrEmpty(v)) ut[name] = v;
      }
      if (ut["floors"] == null && e is FamilyInstance fi && e.Category?.Id.Equals(new ElementId(BuiltInCategory.OST_Mass)) == true)
      {
        try
        {
          var levels = MassInstanceUtils.GetMassLevelIds(doc, fi.Id);
          if (levels.Count > 0) ut["floors"] = levels.Count.ToString();
        }
        catch
        {
          // no mass floors
        }
      }
      return ut;
    }

    // ------------------------------------------------------------------ polygons

    private static List<double[]> Xy(IEnumerable<XYZ> pts) => pts.Select(q => new[] { U.ToMeters(q.X), U.ToMeters(q.Y) }).ToList();

    private static List<XYZ> Tessellate(CurveLoop loop)
    {
      var pts = new List<XYZ>();
      foreach (var c in loop)
      {
        var t = c.Tessellate();
        for (int i = 0; i < t.Count - 1; i++) pts.Add(t[i]);
      }
      return pts;
    }

    /// <summary>Plan outlines of one solid: projection on a horizontal plane (outer loop + holes).</summary>
    private static List<List<double[]>> Outlines(Solid solid, double z, out bool approximate)
    {
      approximate = false;
      try
      {
        var analyzer = ExtrusionAnalyzer.Create(solid, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, new XYZ(0, 0, z)), XYZ.BasisZ);
        var face = analyzer.GetExtrusionBase();
        var loops = face.GetEdgesAsCurveLoops().Select(l => Xy(Tessellate(l))).Where(l => l.Count >= 3).ToList();
        if (loops.Count > 0) return loops;
      }
      catch
      {
        // fall back to the hull below
      }
      approximate = true;
      var pts = new List<double[]>();
      foreach (Face f in solid.Faces)
      {
        try
        {
          var m = f.Triangulate(0.2);
          for (int i = 0; i < m.Vertices.Count; i++) pts.Add(new[] { U.ToMeters(m.Vertices[i].X), U.ToMeters(m.Vertices[i].Y) });
        }
        catch
        {
          // skip the face
        }
      }
      var hull = Polygon2D.ConvexHull(pts);
      return hull.Count >= 3 ? new List<List<double[]>> { hull } : new List<List<double[]>>();
    }

    private static List<Solid> Pieces(Element e)
    {
      var solids = Geo.Solids(e).Where(s => Math.Abs(s.Volume) > 1e-6).ToList();
      if (solids.Count <= 1) return solids;
      try
      {
        var merged = solids[0];
        for (int i = 1; i < solids.Count; i++) merged = BooleanOperationsUtils.ExecuteBooleanOperation(merged, solids[i], BooleanOperationsType.Union);
        return SolidUtils.SplitVolumes(merged).ToList();
      }
      catch
      {
        return solids;
      }
    }

    private static JArray Ring(List<double[]> ring) => new JArray(ring.Select(q => (JToken)new JArray(U.R(q[0]), U.R(q[1]))));

    // ------------------------------------------------------------------ analysis.footprints

    private static JToken Footprints(JObject p)
    {
      var doc = RevitContext.Doc;
      var result = new JArray();
      double total = 0;
      foreach (var e in Collect(doc, p))
      {
        if (e is CurveElement || e is SpatialElement) continue;
        var bb = e.get_BoundingBox(null);
        if (bb == null) continue;
        var pieces = Pieces(e);
        bool approximate = false;
        var loops = new List<List<double[]>>();
        foreach (var s in pieces)
        {
          loops.AddRange(Outlines(s, bb.Min.Z, out var approx));
          approximate |= approx;
        }
        if (loops.Count == 0)
        {
          // Mesh-only geometry (topography, imported meshes): hull of its triangles.
          var pts = Geo.Triangles(e).SelectMany(t => t).Select(q => new[] { U.ToMeters(q.X), U.ToMeters(q.Y) }).ToList();
          var hull = Polygon2D.ConvexHull(pts);
          if (hull.Count < 3) continue;
          loops.Add(hull);
          approximate = true;
        }
        var parts = Polygon2D.Parts(loops);
        double area = Polygon2D.Area(parts);
        total += area;
        double volume = pieces.Sum(s => U.ToM3(Math.Abs(s.Volume)));
        var c = Polygon2D.Centroid(parts[0].outer);
        var item = new JObject
        {
          ["id"] = Ids.Str(e.Id),
          ["name"] = UserData.Get(e, "name") ?? e.Name ?? "",
          ["layer"] = ElementQuery.LayerOf(e, UserData.Read(e)),
          ["category"] = e.Category?.Name,
          ["source"] = "revit",
          ["parts"] = new JArray(parts.Select(pt => (JToken)new JObject { ["outer"] = Ring(pt.outer), ["holes"] = new JArray(pt.holes.Select(h => (JToken)Ring(h))) })),
          ["area"] = U.R(area, 3),
          ["base_z"] = U.R(U.ToMeters(bb.Min.Z), 3),
          ["top_z"] = U.R(U.ToMeters(bb.Max.Z), 3),
          ["height"] = U.R(U.ToMeters(bb.Max.Z - bb.Min.Z), 3),
          ["volume"] = U.R(volume, 3),
          ["centroid"] = new JArray(U.R(c[0]), U.R(c[1])),
          ["user_text"] = UserTextOf(doc, e),
        };
        if (approximate) item["approximate"] = true;
        result.Add(item);
      }
      return new JObject { ["count"] = result.Count, ["total_area"] = U.R(total, 3), ["items"] = result };
    }

    // ------------------------------------------------------------------ analysis.curves

    private static List<List<XYZ>> Chain(List<List<XYZ>> segments, double tol)
      => Polygon2D.Chain(segments.Select(s => s.Select(q => new[] { q.X, q.Y, q.Z }).ToList()), tol)
        .Select(c => c.Select(q => new XYZ(q[0], q[1], q[2])).ToList()).ToList();

    private static List<List<XYZ>> CurvesOf(Document doc, Element e, bool outlines)
    {
      const double tol = 0.004; // ~1.2 mm
      switch (e)
      {
        case CurveElement ce:
          return new List<List<XYZ>> { ce.GeometryCurve.Tessellate().ToList() };
        case SpatialElement se:
          {
            var loops = new List<List<XYZ>>();
            var segments = se.GetBoundarySegments(new SpatialElementBoundaryOptions());
            if (segments != null)
              foreach (var loop in segments)
                loops.AddRange(Chain(loop.Select(s => s.GetCurve().Tessellate().ToList()).ToList(), tol));
            return loops;
          }
        case FilledRegion fr:
          return fr.GetBoundaries().Select(l => { var pts = Tessellate(l); pts.Add(pts[0]); return pts; }).ToList();
      }
      var curves = Geo.Objects(e).OfType<Curve>().Select(c => c.Tessellate().ToList()).ToList();
      if (curves.Count > 0) return Chain(curves, tol);
      if (!outlines) return new List<List<XYZ>>();
      // Floors, toposolids, masses, slabs: plan outline at the top of the element.
      var bb = e.get_BoundingBox(null);
      if (bb == null) return new List<List<XYZ>>();
      var result = new List<List<XYZ>>();
      foreach (var s in Pieces(e))
        foreach (var ring in Outlines(s, bb.Max.Z, out _))
        {
          var pts = ring.Select(q => new XYZ(U.ToFeet(q[0]), U.ToFeet(q[1]), bb.Max.Z)).ToList();
          pts.Add(pts[0]);
          result.Add(pts);
        }
      return result;
    }

    private static JToken Curves(JObject p)
    {
      var doc = RevitContext.Doc;
      bool outlines = RArgs.Bool(p, "outlines", true);
      var result = new JArray();
      foreach (var e in Collect(doc, p))
      {
        var loops = CurvesOf(doc, e, outlines);
        var ut = UserTextOf(doc, e);
        int k = 0;
        foreach (var raw in loops)
        {
          if (raw.Count < 2) continue;
          var pts = ShapeFactory.Clean(raw, false);
          bool closed = pts.Count > 2 && pts[0].DistanceTo(pts[pts.Count - 1]) < 0.004;
          if (closed) pts.RemoveAt(pts.Count - 1);
          double length = 0;
          for (int i = 1; i < pts.Count; i++) length += pts[i].DistanceTo(pts[i - 1]);
          if (closed) length += pts[pts.Count - 1].DistanceTo(pts[0]);
          double area = closed ? Math.Abs(Polygon2D.SignedArea(Xy(pts))) : 0;
          result.Add(new JObject
          {
            ["id"] = loops.Count > 1 ? Ids.Str(e.Id) + "#" + k : Ids.Str(e.Id),
            ["object_id"] = Ids.Str(e.Id),
            ["name"] = UserData.Get(e, "name") ?? e.Name ?? "",
            ["layer"] = ElementQuery.LayerOf(e, UserData.Read(e)),
            ["closed"] = closed,
            ["points"] = new JArray(pts.Select(q => (JToken)Describe.P(q))),
            ["length"] = U.R(U.ToMeters(length), 3),
            ["area"] = U.R(area, 3),
            ["user_text"] = ut,
          });
          k++;
        }
      }
      return new JObject { ["count"] = result.Count, ["items"] = result };
    }

    // ------------------------------------------------------------------ meshes for rays

    /// <summary>Obstacle or terrain mesh (meters) from a filter or a list of filters.</summary>
    private static RayMesh TargetMesh(Document doc, JToken target)
    {
      var elements = new List<Element>();
      if (target is JArray sources)
      {
        foreach (var source in sources.OfType<JObject>()) elements.AddRange(Collect(doc, source));
      }
      else if (target is JObject one)
      {
        elements = Collect(doc, one);
      }
      else
      {
        // Default obstacles: building and site elements, except the connectors' analysis results
        // and helper volumes (sun-hours maps, wind domains).
        var excluded = new[] { "analysis", "wind_domain" };
        elements = ExportHandlers.Selection(doc, new JObject())
          .Where(e => !excluded.Contains(UserData.Get(e, "mcp.kind") ?? "")).ToList();
      }
      var mesh = new RayMesh();
      foreach (var e in elements.GroupBy(x => Ids.Of(x.Id)).Select(g => g.First())) Geo.AddTriangles(e, mesh);
      return mesh;
    }

    // ------------------------------------------------------------------ analysis.drape_points

    private static JToken DrapePoints(JObject p)
    {
      var doc = RevitContext.Doc;
      var pts = PointsMeters(RArgs.Get(p, "points"), "points");
      var target = p["target"];
      if (target == null || target.Type == JTokenType.Null) throw RpcException.InvalidParams("'target' (filter of the terrain elements: Toposolid, Topography, Floors…) is required.");
      var mesh = TargetMesh(doc, target);
      if (mesh.TriangleCount == 0) throw RpcException.NotFound("The target has no surface.");
      var b = mesh.Bounds();
      double top = b.maxZ + 10;
      double offset = RArgs.Num(p, "offset", 0);
      var output = new JArray();
      int hits = 0;
      foreach (var q in pts)
      {
        double t = mesh.Nearest(q[0], q[1], top, 0, 0, -1);
        if (t >= 0)
        {
          hits++;
          output.Add(new JArray(U.R(q[0]), U.R(q[1]), U.R(top - t + offset)));
        }
        else output.Add(new JArray(U.R(q[0]), U.R(q[1]), JValue.CreateNull()));
      }
      return new JObject { ["points"] = output, ["hits"] = hits, ["misses"] = pts.Count - hits };
    }

    private static List<double[]> PointsMeters(JToken t, string key)
    {
      if (!(t is JArray arr) || arr.Count == 0) throw RpcException.InvalidParams($"'{key}' must be a non-empty list of [x, y, z].");
      return arr.Select((x, i) =>
      {
        if (!(x is JArray a) || a.Count < 2) throw RpcException.InvalidParams($"'{key}[{i}]' must be [x, y] or [x, y, z].");
        return new[] { RArgs.ToDouble(a[0], key), RArgs.ToDouble(a[1], key), a.Count > 2 ? RArgs.ToDouble(a[2], key) : 0 };
      }).ToList();
    }

    // ------------------------------------------------------------------ analysis.ray_visibility

    /// <summary>
    /// For each point, sums the weights of the directions that are not blocked (sun hours when
    /// the directions are sun vectors weighted by hours). Directions are geographic (x = east,
    /// y = true north) and rotated into project coordinates unless true_north=false.
    /// </summary>
    private static JToken RayVisibility(JObject p)
    {
      var doc = RevitContext.Doc;
      var pts = PointsMeters(RArgs.Get(p, "points"), "points");
      var dirsRaw = PointsMeters(RArgs.Get(p, "directions"), "directions");
      bool trueNorth = RArgs.Bool(p, "true_north", true);
      var toInternal = trueNorth ? DocumentHandlers.SharedTransform(doc).Inverse : Transform.Identity;
      var dirs = dirsRaw.Select(d =>
      {
        var v = toInternal.OfVector(new XYZ(d[0], d[1], d[2]));
        return v.GetLength() > 1e-12 ? v.Normalize() : XYZ.BasisZ;
      }).ToList();
      var weights = RArgs.Get(p, "weights") is JArray w ? w.Select(x => RArgs.ToDouble(x)).ToList() : dirs.Select(_ => 1.0).ToList();
      if (weights.Count != dirs.Count) throw RpcException.InvalidParams("'weights' must have one value per direction.");
      List<XYZ> normals = null;
      if (RArgs.Get(p, "normals") is JArray na)
      {
        normals = PointsMeters(na, "normals").Select(n => new XYZ(n[0], n[1], n[2])).ToList();
        if (normals.Count != pts.Count) throw RpcException.InvalidParams("'normals' must have one vector per point.");
      }
      long rays = (long)pts.Count * dirs.Count;
      long maxRays = (long)RArgs.Num(p, "max_rays", 5_000_000);
      if (rays > maxRays) throw RpcException.InvalidParams($"{rays} rays requested (> max_rays={maxRays}): use fewer points or directions.");

      var obstacles = p["obstacles"];
      var mesh = TargetMesh(doc, obstacles == null || obstacles.Type == JTokenType.Null ? null : obstacles);
      double offset = RArgs.Num(p, "offset", 0.05);
      bool hasObstacles = mesh.TriangleCount > 0;
      var values = new JArray();
      var visibleCounts = new JArray();
      double sum = 0, min = double.MaxValue, max = double.MinValue;
      var sw = System.Diagnostics.Stopwatch.StartNew();
      for (int i = 0; i < pts.Count; i++)
      {
        double value = 0;
        int visible = 0;
        var n = normals?[i];
        for (int k = 0; k < dirs.Count; k++)
        {
          var d = dirs[k];
          if (n != null && n.DotProduct(d) <= 0) continue;
          double ox = pts[i][0] + d.X * offset + (n != null ? n.X * offset : 0);
          double oy = pts[i][1] + d.Y * offset + (n != null ? n.Y * offset : 0);
          double oz = pts[i][2] + d.Z * offset + (n != null ? n.Z * offset : 0);
          if (!hasObstacles || !mesh.Blocked(ox, oy, oz, d.X, d.Y, d.Z))
          {
            value += weights[k];
            visible++;
          }
        }
        values.Add(U.R(value, 4));
        visibleCounts.Add(visible);
        sum += value;
        min = Math.Min(min, value);
        max = Math.Max(max, value);
      }
      return new JObject
      {
        ["values"] = values,
        ["visible_counts"] = visibleCounts,
        ["stats"] = new JObject
        {
          ["points"] = pts.Count,
          ["directions"] = dirs.Count,
          ["min"] = U.R(pts.Count > 0 ? min : 0, 4),
          ["max"] = U.R(pts.Count > 0 ? max : 0, 4),
          ["mean"] = U.R(pts.Count > 0 ? sum / pts.Count : 0, 4),
          ["obstacle_triangles"] = mesh.TriangleCount,
          ["true_north_applied"] = trueNorth,
          ["seconds"] = Math.Round(sw.Elapsed.TotalSeconds, 2),
        },
      };
    }
  }
}
