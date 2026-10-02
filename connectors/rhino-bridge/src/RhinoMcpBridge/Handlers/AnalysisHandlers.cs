using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using Rhino;
using Rhino.DocObjects;
using Rhino.Geometry;
using Rhino.Geometry.Intersect;
using RhinoMcpBridge.Core;
using McpBridge.Transport;

namespace RhinoMcpBridge.Handlers
{
  /// <summary>
  /// analysis.* methods used by the Fusion connector (urban rules, trees, simulations):
  /// building footprints, curves as polylines, draping points on a terrain and ray-cast
  /// visibility (sun hours, shadows). Geometry comes from Rhino objects (filter) or from
  /// Grasshopper outputs ("grasshopper": {definition, outputs}).
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

    private sealed class Item
    {
      public string Id;
      public string Name = "";
      public string Layer;
      public string Source;
      public GeometryBase Geometry;
      public JObject UserText = new JObject();
    }

    /// <summary>Rhino objects from the filter, or the geometry of Grasshopper outputs.</summary>
    private static List<Item> Collect(RhinoDoc doc, JObject p)
    {
      var items = new List<Item>();
      if (p["grasshopper"] is JObject gh)
      {
        foreach (var (name, geoms) in GhBridge.OutputGeometry(gh))
        {
          int i = 0;
          foreach (var g in geoms) items.Add(new Item { Id = name + "#" + i++, Name = name, Source = "grasshopper", Geometry = g });
        }
        return items;
      }
      var query = ObjectQuery.From(p);
      if (query.IsEmpty) throw RpcException.InvalidParams("Give a filter (layer, ids, types…) or 'grasshopper': {outputs}.");
      foreach (var obj in query.Run(doc))
      {
        if (obj.Geometry == null) continue;
        items.Add(new Item
        {
          Id = obj.Id.ToString(),
          Name = obj.Attributes.Name ?? "",
          Layer = RhinoUtil.LayerPath(doc, obj.Attributes.LayerIndex),
          Source = "rhino",
          Geometry = obj.Geometry,
          UserText = RhinoUtil.UserText(obj.Attributes),
        });
      }
      return items;
    }

    internal static Mesh ToMesh(GeometryBase g, MeshingParameters mp)
    {
      switch (g)
      {
        case Mesh m: return m.DuplicateMesh();
        case Extrusion ex: return Join(Mesh.CreateFromBrep(ex.ToBrep(false), mp));
        case Brep b: return Join(Mesh.CreateFromBrep(b, mp));
        case Surface s: return Join(Mesh.CreateFromBrep(s.ToBrep(), mp));
        case SubD sd: return Mesh.CreateFromSubD(sd, 2);
      }
      return null;
    }

    private static Mesh Join(Mesh[] parts)
    {
      if (parts == null || parts.Length == 0) return null;
      var m = new Mesh();
      m.Append(parts);
      return m;
    }

    private static JArray Xy(Polyline pl)
    {
      var pts = pl.ToList();
      if (pts.Count > 1 && pts[0].DistanceTo(pts[pts.Count - 1]) < 1e-9) pts.RemoveAt(pts.Count - 1);
      return new JArray(pts.Select(q => (JToken)new JArray(J.R(q.X), J.R(q.Y))));
    }

    private static double SignedArea(Polyline pl)
    {
      double a = 0;
      for (int i = 0; i < pl.Count - 1; i++) a += pl[i].X * pl[i + 1].Y - pl[i + 1].X * pl[i].Y;
      return a / 2;
    }

    private static bool Inside(Point3d p, Polyline poly)
    {
      bool inside = false;
      for (int i = 0, j = poly.Count - 2; i < poly.Count - 1; j = i++)
      {
        var a = poly[i];
        var b = poly[j];
        if ((a.Y > p.Y) != (b.Y > p.Y) && p.X < (b.X - a.X) * (p.Y - a.Y) / (b.Y - a.Y) + a.X) inside = !inside;
      }
      return inside;
    }

    private static Polyline Closed(Polyline pl)
    {
      var c = new Polyline(pl);
      if (c.Count > 0 && c[0].DistanceTo(c[c.Count - 1]) > 1e-9) c.Add(c[0]);
      for (int i = 0; i < c.Count; i++) c[i] = new Point3d(c[i].X, c[i].Y, 0);
      return c;
    }

    private static List<Polyline> Outlines(GeometryBase g, double tol, MeshingParameters mp)
    {
      if (g is Extrusion ex && ex.PathTangent.IsParallelTo(Vector3d.ZAxis, 1e-3) != 0)
      {
        var loops = new List<Polyline>();
        for (int i = 0; i < ex.ProfileCount; i++)
        {
          var prof = ex.Profile3d(i, 0.0);
          var pc = prof?.ToPolyline(tol, RhinoMath.ToRadians(2), 0, 0);
          if (pc != null && pc.TryGetPolyline(out Polyline pl)) loops.Add(Closed(pl));
        }
        if (loops.Count > 0) return loops;
      }
      var mesh = ToMesh(g, mp);
      if (mesh == null) return new List<Polyline>();
      var outlines = mesh.GetOutlines(Plane.WorldXY);
      return outlines == null ? new List<Polyline>() : outlines.Where(o => o != null && o.Count >= 3).Select(Closed).ToList();
    }

    // ------------------------------------------------------------------ analysis.footprints

    private static JToken Footprints(JObject p)
    {
      var doc = RhinoUtil.Doc();
      double tol = Args.Num(p, "tolerance", Math.Max(doc.ModelAbsoluteTolerance * 10, 0.01));
      var mp = MeshingParameters.FastRenderMesh;
      var result = new JArray();
      double total = 0;
      foreach (var item in Collect(doc, p))
      {
        var g = item.Geometry;
        if (g is Curve || g is Rhino.Geometry.Point || g is TextDot || g is PointCloud) continue;
        var box = g.GetBoundingBox(true);
        var loops = Outlines(g, tol, mp).OrderByDescending(l => Math.Abs(SignedArea(l))).ToList();
        if (loops.Count == 0) continue;

        // Largest loops are outlines; a loop inside an outline is a courtyard (hole).
        var parts = new List<(Polyline outer, List<Polyline> holes)>();
        foreach (var loop in loops)
        {
          var owner = parts.FindIndex(pt => Inside(loop[0], pt.outer) && Math.Abs(SignedArea(loop)) < Math.Abs(SignedArea(pt.outer)));
          if (owner >= 0) parts[owner].holes.Add(loop);
          else parts.Add((loop, new List<Polyline>()));
        }

        double area = parts.Sum(pt => Math.Abs(SignedArea(pt.outer)) - pt.holes.Sum(h => Math.Abs(SignedArea(h))));
        total += area;
        var amp = AreaMassProperties.Compute(new PolylineCurve(parts[0].outer));
        double volume = 0;
        try
        {
          switch (g)
          {
            case Brep b when b.IsSolid: volume = Math.Abs(b.GetVolume()); break;
            case Extrusion e when e.IsSolid: volume = Math.Abs(e.ToBrep(false).GetVolume()); break;
            case Mesh m when m.IsClosed: volume = Math.Abs(m.Volume()); break;
          }
        }
        catch
        {
          volume = 0;
        }

        result.Add(new JObject
        {
          ["id"] = item.Id,
          ["name"] = item.Name,
          ["layer"] = item.Layer,
          ["source"] = item.Source,
          ["parts"] = new JArray(parts.Select(pt => (JToken)new JObject
          {
            ["outer"] = Xy(pt.outer),
            ["holes"] = new JArray(pt.holes.Select(h => (JToken)Xy(h))),
          })),
          ["area"] = J.N(area),
          ["base_z"] = J.N(box.Min.Z),
          ["top_z"] = J.N(box.Max.Z),
          ["height"] = J.N(box.Max.Z - box.Min.Z),
          ["volume"] = J.N(volume),
          ["centroid"] = amp != null ? new JArray(J.R(amp.Centroid.X), J.R(amp.Centroid.Y)) : null,
          ["user_text"] = item.UserText,
        });
      }
      return new JObject { ["count"] = result.Count, ["total_area"] = J.N(total), ["items"] = result };
    }

    // ------------------------------------------------------------------ analysis.curves

    private static JToken Curves(JObject p)
    {
      var doc = RhinoUtil.Doc();
      double tol = Args.Num(p, "tolerance", Math.Max(doc.ModelAbsoluteTolerance * 10, 0.01));
      double maxSegment = Args.Num(p, "max_segment", 0);
      var result = new JArray();
      foreach (var item in Collect(doc, p))
      {
        var loops = new List<Curve>();
        switch (item.Geometry)
        {
          case Curve c: loops.Add(c); break;
          case Hatch h: loops.AddRange(h.Get3dCurves(true)); break;
          case Brep b: loops.AddRange(Curve.JoinCurves(b.DuplicateNakedEdgeCurves(true, false), tol)); break;
          case Extrusion ex: loops.AddRange(Curve.JoinCurves(ex.ToBrep(false).DuplicateNakedEdgeCurves(true, false), tol)); break;
          case Surface s: loops.AddRange(Curve.JoinCurves(s.ToBrep().DuplicateNakedEdgeCurves(true, false), tol)); break;
          case Mesh m:
            var naked = m.GetNakedEdges();
            if (naked != null) loops.AddRange(naked.Select(pl => (Curve)new PolylineCurve(pl)));
            break;
        }
        int k = 0;
        foreach (var c in loops)
        {
          var pc = c.ToPolyline(tol, RhinoMath.ToRadians(2), 0, maxSegment);
          if (pc == null || !pc.TryGetPolyline(out Polyline pl)) continue;
          var pts = pl.ToList();
          bool closed = c.IsClosed;
          if (closed && pts.Count > 1 && pts[0].DistanceTo(pts[pts.Count - 1]) < 1e-9) pts.RemoveAt(pts.Count - 1);
          double area = 0;
          if (closed && c.IsPlanar())
          {
            var amp = AreaMassProperties.Compute(c);
            if (amp != null) area = amp.Area;
          }
          result.Add(new JObject
          {
            ["id"] = loops.Count > 1 ? item.Id + "#" + k : item.Id,
            ["object_id"] = item.Id,
            ["name"] = item.Name,
            ["layer"] = item.Layer,
            ["closed"] = closed,
            ["points"] = new JArray(pts.Select(q => (JToken)J.P(q))),
            ["length"] = J.N(c.GetLength()),
            ["area"] = J.N(area),
            ["user_text"] = item.UserText,
          });
          k++;
        }
      }
      return new JObject { ["count"] = result.Count, ["items"] = result };
    }

    // ------------------------------------------------------------------ analysis.drape_points

    /// <summary>Obstacle or terrain mesh from a target filter (default: every visible solid/mesh).</summary>
    /// <summary>
    /// Mesh of the target: one source (filter or {grasshopper}) or a list of sources whose
    /// geometry is combined — e.g. the Grasshopper design plus the surrounding Rhino city.
    /// </summary>
    private static Mesh TargetMesh(RhinoDoc doc, JToken target, out BoundingBox box)
    {
      var items = new List<Item>();
      if (target is JArray sources)
      {
        foreach (var source in sources.OfType<JObject>()) items.AddRange(Collect(doc, source));
      }
      else if (target is JObject one)
      {
        items = Collect(doc, one);
      }
      else
      {
        // Default obstacles: every visible solid or mesh, except the analysis results and
        // helper volumes created by the connectors (sun-hours maps, wind domains).
        items = Collect(doc, new JObject
        {
          ["types"] = new JArray("brep", "extrusion", "mesh", "subd", "surface"),
          ["include_hidden"] = false,
          ["exclude_user_text"] = new JArray(new JObject { ["mcp.kind"] = "analysis" }, new JObject { ["mcp.kind"] = "wind_domain" }),
        });
      }
      var mesh = new Mesh();
      var mp = MeshingParameters.Default;
      foreach (var item in items)
      {
        var m = ToMesh(item.Geometry, mp);
        if (m != null) mesh.Append(m);
      }
      box = mesh.GetBoundingBox(false);
      return mesh;
    }

    private static JToken DrapePoints(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var points = Args.Points(Args.Get(p, "points"), "points");
      var target = p["target"];
      if (target == null || target.Type == JTokenType.Null) throw RpcException.InvalidParams("'target' (filter of the terrain objects) is required.");
      var mesh = TargetMesh(doc, target, out var box);
      if (mesh.Faces.Count == 0) throw RpcException.NotFound("The target contains no surface or mesh.");
      double top = box.Max.Z + 10;
      double offset = Args.Num(p, "offset", 0);
      var output = new JArray();
      int hits = 0;
      foreach (var pt in points)
      {
        var ray = new Ray3d(new Point3d(pt.X, pt.Y, top), -Vector3d.ZAxis);
        double t = Intersection.MeshRay(mesh, ray);
        if (t >= 0)
        {
          hits++;
          output.Add(new JArray(J.R(pt.X), J.R(pt.Y), J.R(top - t + offset)));
        }
        else
        {
          output.Add(new JArray(J.R(pt.X), J.R(pt.Y), JValue.CreateNull()));
        }
      }
      return new JObject { ["points"] = output, ["hits"] = hits, ["misses"] = points.Count - hits };
    }

    // ------------------------------------------------------------------ analysis.ray_visibility

    /// <summary>
    /// For each point, sums the weights of the directions that are not blocked by obstacles
    /// (sun hours when directions are sun vectors weighted by hours). Directions point
    /// from the point towards the source; optional normals drop back-facing directions.
    /// </summary>
    private static JToken RayVisibility(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var points = Args.Points(Args.Get(p, "points"), "points");
      var dirs = Args.Points(Args.Get(p, "directions"), "directions").Select(q => { var v = new Vector3d(q); v.Unitize(); return v; }).ToList();
      var weights = Args.Get(p, "weights") is JArray w ? w.Select(x => Args.ToDouble(x)).ToList() : dirs.Select(_ => 1.0).ToList();
      if (weights.Count != dirs.Count) throw RpcException.InvalidParams("'weights' must have one value per direction.");
      List<Vector3d> normals = null;
      if (Args.Get(p, "normals") is JArray na)
      {
        normals = Args.Points(na, "normals").Select(q => new Vector3d(q)).ToList();
        if (normals.Count != points.Count) throw RpcException.InvalidParams("'normals' must have one vector per point.");
      }
      long rays = (long)points.Count * dirs.Count;
      long maxRays = (long)Args.Num(p, "max_rays", 5_000_000);
      if (rays > maxRays) throw RpcException.InvalidParams($"{rays} rays requested (> max_rays={maxRays}): use fewer points or directions.");

      var obstacles = p["obstacles"];
      var mesh = TargetMesh(doc, obstacles == null || obstacles.Type == JTokenType.Null ? null : obstacles, out _);
      double offset = Args.Num(p, "offset", 0.05);
      bool hasObstacles = mesh.Faces.Count > 0;
      var values = new JArray();
      var visibleCounts = new JArray();
      double sum = 0, min = double.MaxValue, max = double.MinValue;
      var sw = System.Diagnostics.Stopwatch.StartNew();
      for (int i = 0; i < points.Count; i++)
      {
        double value = 0;
        int visible = 0;
        for (int k = 0; k < dirs.Count; k++)
        {
          var dir = dirs[k];
          if (normals != null && normals[i] * dir <= 0) continue;
          var start = points[i] + dir * offset + (normals != null ? normals[i] * offset : Vector3d.Zero);
          bool blocked = hasObstacles && Intersection.MeshRay(mesh, new Ray3d(start, dir)) >= 0;
          if (!blocked)
          {
            value += weights[k];
            visible++;
          }
        }
        values.Add(J.N(value));
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
          ["points"] = points.Count,
          ["directions"] = dirs.Count,
          ["min"] = J.N(points.Count > 0 ? min : 0),
          ["max"] = J.N(points.Count > 0 ? max : 0),
          ["mean"] = J.N(points.Count > 0 ? sum / points.Count : 0),
          ["obstacle_faces"] = mesh.Faces.Count,
          ["duration_ms"] = sw.ElapsedMilliseconds,
        },
      };
    }
  }
}
