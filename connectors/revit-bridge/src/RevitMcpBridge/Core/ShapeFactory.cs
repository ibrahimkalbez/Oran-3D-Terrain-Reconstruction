using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.DB;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;

namespace RevitMcpBridge.Core
{
  /// <summary>
  /// Turns the JSON geometry specs of rhino.create_geometry into Revit geometry for a
  /// DirectShape (solids, meshes, curves, points). Coordinates in meters.
  /// </summary>
  public static class ShapeFactory
  {
    public static readonly string[] SupportedTypes =
    {
      "point", "points", "text_dot", "line", "polyline", "curve", "circle", "arc", "ellipse", "rectangle", "polygon",
      "surface", "planar_surface", "box", "sphere", "cylinder", "cone", "extrusion", "loft", "mesh",
    };

    public sealed class Shape
    {
      public readonly List<GeometryObject> Objects = new List<GeometryObject>();
      public string Kind;
      public bool Is2D;
    }

    public static Shape Create(Document doc, JObject spec, Func<Color, double, ElementId> material)
    {
      var type = (RArgs.Str(spec, "type") ?? throw RpcException.InvalidParams("Each geometry needs a 'type'. Supported: " + string.Join(", ", SupportedTypes)))
        .Trim().ToLowerInvariant().Replace(" ", "_");
      var colour = RArgs.Colour(spec["color"]);
      double transparency = RArgs.Num(spec, "transparency", 0);
      var mat = colour != null ? material(colour, transparency) : ElementId.InvalidElementId;
      var options = new SolidOptions(mat, ElementId.InvalidElementId);
      var shape = new Shape { Kind = type };
      switch (type)
      {
        case "point":
        case "text_dot":
          shape.Objects.Add(Point.Create(RArgs.Point(RArgs.Get(spec, "location", "point", "position", "center"), "location")));
          shape.Is2D = true;
          break;
        case "points":
          foreach (var pt in RArgs.Points(RArgs.Get(spec, "points"))) shape.Objects.Add(Point.Create(pt));
          shape.Is2D = true;
          break;
        case "line":
          shape.Objects.Add(Line.CreateBound(RArgs.Point(RArgs.Get(spec, "from", "start", "a"), "from"), RArgs.Point(RArgs.Get(spec, "to", "end", "b"), "to")));
          shape.Is2D = true;
          break;
        case "polyline":
        case "rectangle":
        case "polygon":
        case "circle":
        case "arc":
        case "ellipse":
        case "curve":
          shape.Objects.AddRange(Curves(spec, type));
          shape.Is2D = true;
          break;
        case "planar_surface":
        case "surface":
          shape.Objects.AddRange(Faces(doc, spec, mat));
          break;
        case "box":
          shape.Objects.Add(Box(spec, options));
          break;
        case "extrusion":
          shape.Objects.Add(Extrusion(spec, options));
          break;
        case "cylinder":
          {
            var basePt = RArgs.PointOr(spec, XYZ.Zero, "base", "center", "origin");
            var axis = RArgs.Has(spec, "axis") ? RArgs.Vector(RArgs.Get(spec, "axis"), "axis").Normalize() : XYZ.BasisZ;
            double r = Positive(spec, "radius"), h = Positive(spec, "height");
            var x = Math.Abs(axis.Z) < 0.99 ? XYZ.BasisZ.CrossProduct(axis).Normalize() : XYZ.BasisX;
            var y = axis.CrossProduct(x).Normalize();
            var loop = CircleLoop(basePt, x, y, r);
            shape.Objects.Add(GeometryCreationUtilities.CreateExtrusionGeometry(new List<CurveLoop> { loop }, axis, h, options));
            break;
          }
        case "sphere":
          {
            var c = RArgs.PointOr(spec, XYZ.Zero, "center", "origin");
            double r = Positive(spec, "radius");
            var frame = new Frame(c, XYZ.BasisX, XYZ.BasisY, XYZ.BasisZ);
            var profile = new CurveLoop();
            profile.Append(Arc.Create(c - XYZ.BasisZ * r, c + XYZ.BasisZ * r, c + XYZ.BasisX * r));
            profile.Append(Line.CreateBound(c + XYZ.BasisZ * r, c - XYZ.BasisZ * r));
            shape.Objects.Add(GeometryCreationUtilities.CreateRevolvedGeometry(frame, new List<CurveLoop> { profile }, 0, 2 * Math.PI, options));
            break;
          }
        case "cone":
          {
            var basePt = RArgs.PointOr(spec, XYZ.Zero, "base", "center", "origin");
            double r = Positive(spec, "radius"), h = Positive(spec, "height");
            var frame = new Frame(basePt, XYZ.BasisX, XYZ.BasisY, XYZ.BasisZ);
            var profile = new CurveLoop();
            profile.Append(Line.CreateBound(basePt, basePt + XYZ.BasisX * r));
            profile.Append(Line.CreateBound(basePt + XYZ.BasisX * r, basePt + XYZ.BasisZ * h));
            profile.Append(Line.CreateBound(basePt + XYZ.BasisZ * h, basePt));
            shape.Objects.Add(GeometryCreationUtilities.CreateRevolvedGeometry(frame, new List<CurveLoop> { profile }, 0, 2 * Math.PI, options));
            break;
          }
        case "loft":
          {
            var sections = Lists(RArgs.Get(spec, "sections", "curves"), "sections").Select(pts => Loop(pts, true)).ToList();
            if (sections.Count < 2) throw RpcException.InvalidParams("A loft needs at least 2 closed sections.");
            shape.Objects.Add(GeometryCreationUtilities.CreateLoftGeometry(sections, options));
            break;
          }
        case "mesh":
          shape.Objects.AddRange(Mesh(doc, spec, mat, material));
          break;
        default:
          throw RpcException.InvalidParams("Unknown geometry type '" + type + "' for Revit. Supported: " + string.Join(", ", SupportedTypes) +
            " (use revit.create_elements for walls, floors, levels, families…).");
      }
      return shape;
    }

    private static double Positive(JObject spec, params string[] keys)
    {
      var t = RArgs.Get(spec, keys) ?? throw RpcException.InvalidParams($"'{keys[0]}' is required.");
      double v = RArgs.ToDouble(t, keys[0]);
      if (!(v > 0)) throw RpcException.InvalidParams($"'{keys[0]}' must be positive.");
      return U.ToFeet(v);
    }

    public static List<List<XYZ>> Lists(JToken t, string key)
    {
      if (!(t is JArray arr) || arr.Count == 0) throw RpcException.InvalidParams($"'{key}' must be a list of point lists.");
      if (arr[0] is JArray first && first.Count > 0 && !(first[0] is JArray)) return new List<List<XYZ>> { RArgs.Points(arr, key) };
      return arr.Select((x, i) => RArgs.Points(x, $"{key}[{i}]")).ToList();
    }

    /// <summary>Closed (or open) polyline as a CurveLoop; duplicate and too-close points are dropped.</summary>
    public static CurveLoop Loop(List<XYZ> points, bool closed)
    {
      var pts = Clean(points, closed);
      if (pts.Count < (closed ? 3 : 2)) throw RpcException.InvalidParams("Not enough distinct points (Revit's minimum edge is 0.8 mm).");
      var loop = new CurveLoop();
      for (int i = 0; i < pts.Count - 1; i++) loop.Append(Line.CreateBound(pts[i], pts[i + 1]));
      if (closed) loop.Append(Line.CreateBound(pts[pts.Count - 1], pts[0]));
      return loop;
    }

    public static List<XYZ> Clean(List<XYZ> points, bool closed)
    {
      const double tol = 0.003; // feet, above Revit's short-curve tolerance
      var pts = new List<XYZ>();
      foreach (var p in points)
        if (pts.Count == 0 || pts[pts.Count - 1].DistanceTo(p) > tol) pts.Add(p);
      if (closed && pts.Count > 1 && pts[0].DistanceTo(pts[pts.Count - 1]) <= tol) pts.RemoveAt(pts.Count - 1);
      return pts;
    }

    private static CurveLoop CircleLoop(XYZ c, XYZ x, XYZ y, double r)
    {
      var loop = new CurveLoop();
      loop.Append(Arc.Create(c, r, 0, Math.PI, x, y));
      loop.Append(Arc.Create(c, r, Math.PI, 2 * Math.PI, x, y));
      return loop;
    }

    private static IEnumerable<GeometryObject> Curves(JObject spec, string type)
    {
      switch (type)
      {
        case "polyline":
          {
            bool closed = RArgs.Bool(spec, "closed", false);
            var loop = Loop(RArgs.Points(RArgs.Get(spec, "points")), closed);
            return loop.Cast<GeometryObject>().ToList();
          }
        case "rectangle":
          {
            XYZ a, b;
            if (RArgs.Has(spec, "corner_a") && RArgs.Has(spec, "corner_b"))
            {
              a = RArgs.Point(RArgs.Get(spec, "corner_a"));
              b = RArgs.Point(RArgs.Get(spec, "corner_b"));
            }
            else
            {
              double w = Positive(spec, "width"), h = Positive(spec, "height", "depth");
              if (RArgs.Has(spec, "center"))
              {
                var c = RArgs.Point(RArgs.Get(spec, "center"));
                a = new XYZ(c.X - w / 2, c.Y - h / 2, c.Z);
              }
              else a = RArgs.PointOr(spec, XYZ.Zero, "corner", "origin");
              b = new XYZ(a.X + w, a.Y + h, a.Z);
            }
            var pts = new List<XYZ> { a, new XYZ(b.X, a.Y, a.Z), new XYZ(b.X, b.Y, a.Z), new XYZ(a.X, b.Y, a.Z) };
            return Loop(pts, true).Cast<GeometryObject>().ToList();
          }
        case "polygon":
          {
            var c = RArgs.PointOr(spec, XYZ.Zero, "center", "origin");
            int sides = RArgs.Int(spec, "sides", 6);
            if (sides < 3) throw RpcException.InvalidParams("A polygon needs at least 3 sides.");
            double r = Positive(spec, "radius"), rot = RArgs.Num(spec, "rotation", 0) * Math.PI / 180;
            var pts = Enumerable.Range(0, sides).Select(i => c + new XYZ(r * Math.Cos(rot + 2 * Math.PI * i / sides), r * Math.Sin(rot + 2 * Math.PI * i / sides), 0)).ToList();
            return Loop(pts, true).Cast<GeometryObject>().ToList();
          }
        case "circle":
          {
            var c = RArgs.PointOr(spec, XYZ.Zero, "center", "origin");
            return CircleLoop(c, XYZ.BasisX, XYZ.BasisY, Positive(spec, "radius")).Cast<GeometryObject>().ToList();
          }
        case "arc":
          {
            if (RArgs.Has(spec, "start") && RArgs.Has(spec, "end") && RArgs.Has(spec, "through", "mid"))
              return new List<GeometryObject> { Arc.Create(RArgs.Point(RArgs.Get(spec, "start")), RArgs.Point(RArgs.Get(spec, "end")), RArgs.Point(RArgs.Get(spec, "through", "mid"))) };
            var c = RArgs.PointOr(spec, XYZ.Zero, "center", "origin");
            double a0 = RArgs.Num(spec, "start_angle", 0) * Math.PI / 180, a1 = RArgs.Num(spec, "end_angle", 90) * Math.PI / 180;
            return new List<GeometryObject> { Arc.Create(c, Positive(spec, "radius"), a0, a1, XYZ.BasisX, XYZ.BasisY) };
          }
        case "ellipse":
          {
            var c = RArgs.PointOr(spec, XYZ.Zero, "center", "origin");
            var e = Ellipse.CreateCurve(c, Positive(spec, "radius_x"), Positive(spec, "radius_y"), XYZ.BasisX, XYZ.BasisY, 0, 2 * Math.PI);
            return new List<GeometryObject> { e };
          }
        case "curve":
          {
            var pts = Clean(RArgs.Points(RArgs.Get(spec, "points", "control_points")), false);
            if (pts.Count < 2) throw RpcException.InvalidParams("A curve needs at least 2 points.");
            if (pts.Count == 2) return new List<GeometryObject> { Line.CreateBound(pts[0], pts[1]) };
            bool closed = RArgs.Bool(spec, "closed", false);
            return new List<GeometryObject> { HermiteSpline.Create(pts, closed) };
          }
      }
      throw RpcException.InvalidParams("Unknown curve type " + type);
    }

    private static Solid Box(JObject spec, SolidOptions options)
    {
      XYZ min, max;
      if (RArgs.Has(spec, "min") && RArgs.Has(spec, "max"))
      {
        var a = RArgs.Point(RArgs.Get(spec, "min"));
        var b = RArgs.Point(RArgs.Get(spec, "max"));
        min = new XYZ(Math.Min(a.X, b.X), Math.Min(a.Y, b.Y), Math.Min(a.Z, b.Z));
        max = new XYZ(Math.Max(a.X, b.X), Math.Max(a.Y, b.Y), Math.Max(a.Z, b.Z));
      }
      else
      {
        var sizeT = RArgs.Get(spec, "size");
        XYZ size;
        if (sizeT is JArray s && s.Count == 3) size = U.ToFeet(RArgs.ToDouble(s[0]), RArgs.ToDouble(s[1]), RArgs.ToDouble(s[2]));
        else size = new XYZ(Positive(spec, "width", "x"), Positive(spec, "depth", "length", "y"), Positive(spec, "height", "z"));
        if (RArgs.Has(spec, "center"))
        {
          var c = RArgs.Point(RArgs.Get(spec, "center"));
          min = c - size / 2;
        }
        else min = RArgs.PointOr(spec, XYZ.Zero, "corner", "origin");
        max = min + size;
      }
      if (max.X - min.X < 0.003 || max.Y - min.Y < 0.003 || max.Z - min.Z < 0.003) throw RpcException.InvalidParams("Box dimensions must be positive.");
      var pts = new List<XYZ> { min, new XYZ(max.X, min.Y, min.Z), new XYZ(max.X, max.Y, min.Z), new XYZ(min.X, max.Y, min.Z) };
      return GeometryCreationUtilities.CreateExtrusionGeometry(new List<CurveLoop> { Loop(pts, true) }, XYZ.BasisZ, max.Z - min.Z, options);
    }

    /// <summary>Profile (closed polyline, optional holes) extruded vertically by 'height' or along 'direction'.</summary>
    private static Solid Extrusion(JObject spec, SolidOptions options)
    {
      var profiles = Lists(RArgs.Get(spec, "profile", "points", "curves"), "profile");
      var loops = new List<CurveLoop>();
      foreach (var pts in profiles) loops.Add(Loop(pts, true));
      if (spec["holes"] is JArray holes)
        foreach (var h in holes) loops.Add(Loop(RArgs.Points(h, "holes"), true));
      // Outer loop counter-clockwise, holes clockwise (Revit requires consistent orientation).
      var plane = loops[0].HasPlane() ? loops[0].GetPlane() : null;
      var normal = plane?.Normal ?? XYZ.BasisZ;
      if (normal.Z < 0) normal = -normal;
      for (int i = 0; i < loops.Count; i++)
      {
        bool ccw = loops[i].IsCounterclockwise(normal);
        if ((i == 0) != ccw) loops[i].Flip();
      }
      XYZ dir;
      double dist;
      if (RArgs.Has(spec, "direction", "vector"))
      {
        var v = RArgs.Displacement(RArgs.Get(spec, "direction", "vector"), "direction");
        if (RArgs.Has(spec, "height")) { dir = v.Normalize(); dist = Positive(spec, "height"); }
        else { dir = v.Normalize(); dist = v.GetLength(); }
      }
      else
      {
        dir = normal;
        dist = Positive(spec, "height");
      }
      if (dist < 0.003) throw RpcException.InvalidParams("The extrusion height must be positive.");
      return GeometryCreationUtilities.CreateExtrusionGeometry(loops, dir, dist, options);
    }

    private static IEnumerable<GeometryObject> Faces(Document doc, JObject spec, ElementId mat)
    {
      var builder = Builder();
      builder.OpenConnectedFaceSet(false);
      if (RArgs.Has(spec, "corners"))
      {
        var c = RArgs.Points(RArgs.Get(spec, "corners"), "corners");
        if (c.Count == 3) builder.AddFace(new TessellatedFace(c, mat));
        else if (c.Count == 4)
        {
          builder.AddFace(new TessellatedFace(new List<XYZ> { c[0], c[1], c[2] }, mat));
          builder.AddFace(new TessellatedFace(new List<XYZ> { c[0], c[2], c[3] }, mat));
        }
        else throw RpcException.InvalidParams("'corners' needs 3 or 4 points.");
      }
      else
      {
        var loops = Lists(RArgs.Get(spec, "points", "curves", "boundary"), "points").Select(l => Clean(l, true)).ToList();
        builder.AddFace(new TessellatedFace(loops.Cast<IList<XYZ>>().ToList(), mat));
      }
      builder.CloseConnectedFaceSet();
      return Build(builder);
    }

    private static TessellatedShapeBuilder Builder() => new TessellatedShapeBuilder
    {
      Target = TessellatedShapeBuilderTarget.AnyGeometry,
      Fallback = TessellatedShapeBuilderFallback.Mesh,
    };

    private static IList<GeometryObject> Build(TessellatedShapeBuilder builder)
    {
      builder.Build();
      var result = builder.GetBuildResult();
      if (result.Outcome == TessellatedShapeBuilderOutcome.Nothing)
        throw RpcException.Failed("Revit could not build the shape (degenerate faces or edges shorter than 0.8 mm?).");
      return result.GetGeometricalObjects();
    }

    /// <summary>Mesh from vertices/faces; vertex colours become a few shading materials (one per colour band).</summary>
    private static IEnumerable<GeometryObject> Mesh(Document doc, JObject spec, ElementId mat, Func<Color, double, ElementId> material)
    {
      var verts = RArgs.Points(RArgs.Get(spec, "vertices"), "vertices");
      if (!(RArgs.Get(spec, "faces") is JArray faces) || faces.Count == 0) throw RpcException.InvalidParams("'faces' must list vertex indices.");
      List<Color> colours = null;
      if (RArgs.Get(spec, "vertex_colors") is JArray vc && vc.Count == verts.Count) colours = vc.Select(RArgs.Colour).ToList();
      var builder = Builder();
      builder.OpenConnectedFaceSet(false);
      int added = 0;
      for (int i = 0; i < faces.Count; i++)
      {
        if (!(faces[i] is JArray f) || (f.Count != 3 && f.Count != 4)) throw RpcException.InvalidParams($"faces[{i}] must have 3 or 4 indices.");
        var idx = f.Select(x => (int)RArgs.ToDouble(x)).ToArray();
        if (idx.Any(k => k < 0 || k >= verts.Count)) throw RpcException.InvalidParams($"faces[{i}] references a missing vertex.");
        var faceMat = mat;
        if (colours != null)
        {
          int r = 0, g = 0, b = 0, n = 0;
          foreach (var k in idx)
          {
            var c = colours[k];
            if (c == null) continue;
            r += c.Red; g += c.Green; b += c.Blue; n++;
          }
          if (n > 0) faceMat = material(Quantize(r / n, g / n, b / n), 0);
        }
        var tris = idx.Length == 3 ? new[] { idx } : new[] { new[] { idx[0], idx[1], idx[2] }, new[] { idx[0], idx[2], idx[3] } };
        foreach (var t in tris)
        {
          XYZ a = verts[t[0]], bb = verts[t[1]], cc = verts[t[2]];
          if (a.DistanceTo(bb) < 0.003 || bb.DistanceTo(cc) < 0.003 || cc.DistanceTo(a) < 0.003) continue;
          if ((bb - a).CrossProduct(cc - a).GetLength() < 1e-9) continue;
          builder.AddFace(new TessellatedFace(new List<XYZ> { a, bb, cc }, faceMat));
          added++;
        }
      }
      builder.CloseConnectedFaceSet();
      if (added == 0) throw RpcException.InvalidParams("Every face of the mesh is degenerate.");
      return Build(builder);
    }

    /// <summary>Colour bands of 32 levels per channel keep the number of materials small.</summary>
    public static Color Quantize(int r, int g, int b)
    {
      byte Q(int v) => (byte)Math.Min(255, (int)Math.Round(v / 32.0) * 32);
      return new Color(Q(r), Q(g), Q(b));
    }
  }
}
