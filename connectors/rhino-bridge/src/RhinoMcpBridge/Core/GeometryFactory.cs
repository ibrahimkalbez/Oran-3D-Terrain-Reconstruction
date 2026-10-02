using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using Rhino;
using Rhino.Geometry;
using McpBridge.Transport;

namespace RhinoMcpBridge.Core
{
  /// <summary>
  /// Turns a JSON geometry spec into RhinoCommon geometry. Every spec has a "type";
  /// the other fields depend on it (see docs/BRIDGE_PROTOCOL.md for the full list).
  /// </summary>
  public static class GeometryFactory
  {
    public static readonly string[] SupportedTypes =
    {
      "point", "points", "line", "polyline", "curve", "circle", "arc", "ellipse", "rectangle", "polygon",
      "surface", "planar_surface", "box", "sphere", "cylinder", "cone", "extrusion", "loft", "pipe",
      "mesh", "text_dot", "text", "brep", "json",
    };

    public static List<GeometryBase> Create(RhinoDoc doc, JObject spec)
    {
      var type = (Args.Str(spec, "type") ?? throw RpcException.InvalidParams("Each geometry needs a 'type'. Supported: " + string.Join(", ", SupportedTypes)))
        .Trim().ToLowerInvariant().Replace(" ", "_");
      double tol = doc.ModelAbsoluteTolerance;
      var plane = Args.Plane(Args.Get(spec, "plane"), Plane.WorldXY);

      switch (type)
      {
        case "point":
          return One(new Point(Args.Point(Args.Get(spec, "location", "point", "position", "center"), "location")));

        case "points":
          return Args.Points(Args.Get(spec, "points")).Select(p => (GeometryBase)new Point(p)).ToList();

        case "line":
          {
            var a = Args.Point(Args.Get(spec, "from", "start", "a"), "from");
            var b = Args.Point(Args.Get(spec, "to", "end", "b"), "to");
            if (a.DistanceTo(b) <= tol) throw RpcException.InvalidParams("Line endpoints are identical.");
            return One(new LineCurve(a, b));
          }

        case "polyline":
          {
            var pts = Args.Points(Args.Get(spec, "points"));
            if (Args.Bool(spec, "closed", false) && pts[0].DistanceTo(pts[pts.Count - 1]) > tol) pts.Add(pts[0]);
            if (pts.Count < 2) throw RpcException.InvalidParams("A polyline needs at least 2 points.");
            return One(new PolylineCurve(pts));
          }

        case "curve":
          {
            var pts = Args.Points(Args.Get(spec, "points", "control_points"));
            int degree = Args.Int(spec, "degree", 3);
            bool closed = Args.Bool(spec, "closed", false);
            bool interpolate = Args.Bool(spec, "interpolate", !Args.Has(spec, "control_points"));
            Curve c = interpolate
              ? Curve.CreateInterpolatedCurve(pts, degree, CurveKnotStyle.Chord)
              : Curve.CreateControlPointCurve(pts, degree);
            if (c == null) throw RpcException.Failed("Could not build the curve from the given points.");
            if (closed && !c.IsClosed)
            {
              if (interpolate)
              {
                var closedPts = new List<Point3d>(pts) { pts[0] };
                c = Curve.CreateInterpolatedCurve(closedPts, degree, CurveKnotStyle.ChordPeriodic) ?? c;
              }
              else
              {
                c = NurbsCurve.Create(true, degree, pts) ?? c;
              }
            }
            return One(c);
          }

        case "circle":
          {
            var center = Args.PointOr(spec, plane.Origin, "center", "origin");
            var normal = Args.Get(spec, "normal");
            var circlePlane = normal != null ? new Plane(center, Args.Vector(normal, "normal")) : MoveTo(plane, center);
            return One(new ArcCurve(new Circle(circlePlane, Positive(spec, "radius"))));
          }

        case "arc":
          {
            if (Args.Has(spec, "start") && Args.Has(spec, "end") && Args.Has(spec, "mid", "through"))
            {
              var arc3 = new Arc(Args.Point(Args.Get(spec, "start")), Args.Point(Args.Get(spec, "mid", "through")), Args.Point(Args.Get(spec, "end")));
              if (!arc3.IsValid) throw RpcException.InvalidParams("The three arc points are collinear.");
              return One(new ArcCurve(arc3));
            }
            var center = Args.PointOr(spec, plane.Origin, "center", "origin");
            double start = RhinoMath.ToRadians(Args.Num(spec, "start_angle", 0));
            double end = RhinoMath.ToRadians(Args.Num(spec, "end_angle", 90));
            var arc = new Arc(new Circle(MoveTo(plane, center), Positive(spec, "radius")), new Interval(start, end));
            return One(new ArcCurve(arc));
          }

        case "ellipse":
          {
            var center = Args.PointOr(spec, plane.Origin, "center", "origin");
            var e = new Ellipse(MoveTo(plane, center), Positive(spec, "radius_x"), Positive(spec, "radius_y"));
            return One(e.ToNurbsCurve());
          }

        case "rectangle":
          {
            Rectangle3d rect;
            if (Args.Has(spec, "corner_a") && Args.Has(spec, "corner_b"))
            {
              rect = new Rectangle3d(plane, Args.Point(Args.Get(spec, "corner_a")), Args.Point(Args.Get(spec, "corner_b")));
            }
            else
            {
              double w = Positive(spec, "width"), h = Positive(spec, "height", "depth");
              if (Args.Has(spec, "center"))
              {
                var c = Args.Point(Args.Get(spec, "center"));
                rect = new Rectangle3d(MoveTo(plane, c), new Interval(-w / 2, w / 2), new Interval(-h / 2, h / 2));
              }
              else
              {
                var corner = Args.PointOr(spec, plane.Origin, "corner", "origin");
                rect = new Rectangle3d(MoveTo(plane, corner), w, h);
              }
            }
            return One(rect.ToNurbsCurve());
          }

        case "polygon":
          {
            var center = Args.PointOr(spec, plane.Origin, "center", "origin");
            int sides = Args.Int(spec, "sides", 6);
            if (sides < 3) throw RpcException.InvalidParams("A polygon needs at least 3 sides.");
            double radius = Positive(spec, "radius");
            double rot = RhinoMath.ToRadians(Args.Num(spec, "rotation", 0));
            var pl = MoveTo(plane, center);
            var pts = new List<Point3d>();
            for (int i = 0; i <= sides; i++)
            {
              double a = rot + 2 * Math.PI * i / sides;
              pts.Add(pl.PointAt(radius * Math.Cos(a), radius * Math.Sin(a)));
            }
            return One(new PolylineCurve(pts));
          }

        case "surface":
          {
            if (Args.Has(spec, "corners"))
            {
              var c = Args.Points(Args.Get(spec, "corners"), "corners");
              if (c.Count != 3 && c.Count != 4) throw RpcException.InvalidParams("'corners' needs 3 or 4 points.");
              var srf = c.Count == 4 ? NurbsSurface.CreateFromCorners(c[0], c[1], c[2], c[3]) : NurbsSurface.CreateFromCorners(c[0], c[1], c[2]);
              if (srf == null) throw RpcException.Failed("Could not build a surface from these corners.");
              return One(srf.ToBrep());
            }
            var pts = Args.Points(Args.Get(spec, "points"));
            int u = Args.Int(spec, "u_count", 0), v = Args.Int(spec, "v_count", 0);
            if (u < 2 || v < 2 || u * v != pts.Count)
              throw RpcException.InvalidParams("A point-grid surface needs 'u_count' × 'v_count' = number of points (each ≥ 2).");
            int deg = Args.Int(spec, "degree", 3);
            var grid = NurbsSurface.CreateThroughPoints(pts, u, v, Math.Min(deg, u - 1), Math.Min(deg, v - 1), false, false);
            if (grid == null) throw RpcException.Failed("Could not fit a surface through the point grid.");
            return One(grid.ToBrep());
          }

        case "planar_surface":
          {
            var curves = Curves(doc, spec);
            var breps = Brep.CreatePlanarBreps(curves, tol);
            if (breps == null || breps.Length == 0) throw RpcException.Failed("The boundary curves are not closed and planar.");
            return breps.Cast<GeometryBase>().ToList();
          }

        case "box":
          {
            Box box;
            if (Args.Has(spec, "min") && Args.Has(spec, "max"))
            {
              box = new Box(new BoundingBox(Args.Point(Args.Get(spec, "min")), Args.Point(Args.Get(spec, "max"))));
            }
            else
            {
              double[] size = Size(spec);
              if (Args.Has(spec, "center"))
              {
                var c = MoveTo(plane, Args.Point(Args.Get(spec, "center")));
                box = new Box(c, new Interval(-size[0] / 2, size[0] / 2), new Interval(-size[1] / 2, size[1] / 2), new Interval(-size[2] / 2, size[2] / 2));
              }
              else
              {
                var c = MoveTo(plane, Args.PointOr(spec, plane.Origin, "corner", "origin"));
                box = new Box(c, new Interval(0, size[0]), new Interval(0, size[1]), new Interval(0, size[2]));
              }
            }
            if (!box.IsValid) throw RpcException.InvalidParams("Box dimensions must be positive.");
            return One(box.ToBrep());
          }

        case "sphere":
          return One(new Sphere(Args.PointOr(spec, Point3d.Origin, "center", "origin"), Positive(spec, "radius")).ToBrep());

        case "cylinder":
          {
            var basePt = Args.PointOr(spec, plane.Origin, "base", "center", "origin");
            var axis = Args.Has(spec, "axis") ? Args.Vector(Args.Get(spec, "axis"), "axis") : plane.ZAxis;
            var circle = new Circle(new Plane(basePt, axis), Positive(spec, "radius"));
            var cyl = new Cylinder(circle, Positive(spec, "height"));
            return One(cyl.ToBrep(Args.Bool(spec, "cap", true), Args.Bool(spec, "cap", true)));
          }

        case "cone":
          {
            var basePt = Args.PointOr(spec, plane.Origin, "base", "center", "origin");
            var axis = Args.Has(spec, "axis") ? Args.Vector(Args.Get(spec, "axis"), "axis") : plane.ZAxis;
            axis.Unitize();
            double h = Positive(spec, "height");
            // Rhino's cone plane sits at the apex with the axis pointing to the base.
            var apex = basePt + axis * h;
            var cone = new Cone(new Plane(apex, -axis), h, Positive(spec, "radius"));
            return One(cone.ToBrep(Args.Bool(spec, "cap", true)));
          }

        case "extrusion":
          return One(Extrude(doc, spec, tol));

        case "loft":
          {
            var curves = Curves(doc, spec, "sections");
            if (curves.Count < 2) throw RpcException.InvalidParams("A loft needs at least 2 section curves.");
            var lofts = Brep.CreateFromLoft(curves, Point3d.Unset, Point3d.Unset, LoftType.Normal, Args.Bool(spec, "closed", false));
            if (lofts == null || lofts.Length == 0) throw RpcException.Failed("Loft failed: check that the sections have the same direction.");
            var result = new List<GeometryBase>();
            foreach (var b in lofts)
            {
              var capped = Args.Bool(spec, "cap", false) ? b.CapPlanarHoles(tol) ?? b : b;
              result.Add(capped);
            }
            return result;
          }

        case "pipe":
          {
            var rail = Curves(doc, spec, "rail").FirstOrDefault() ?? throw RpcException.InvalidParams("A pipe needs a rail ('points' or 'curve_id').");
            var pipes = Brep.CreatePipe(rail, Positive(spec, "radius"), false, PipeCapMode.Flat, true, tol, doc.ModelAngleToleranceRadians);
            if (pipes == null || pipes.Length == 0) throw RpcException.Failed("Pipe failed.");
            return pipes.Cast<GeometryBase>().ToList();
          }

        case "mesh":
          return One(MeshFromSpec(spec));

        case "text_dot":
          {
            var dot = new TextDot(Args.Str(spec, "text", required: true), Args.Point(Args.Get(spec, "location", "point"), "location"));
            if (Args.Has(spec, "font_height")) dot.FontHeight = Args.Int(spec, "font_height", 14);
            return One(dot);
          }

        case "text":
          {
            var loc = Args.PointOr(spec, plane.Origin, "location", "point");
            var te = TextEntity.Create(Args.Str(spec, "text", required: true), MoveTo(plane, loc), doc.DimStyles.Current, false, 0, 0);
            if (Args.Has(spec, "height")) te.TextHeight = Positive(spec, "height");
            return One(te);
          }

        case "brep":
        case "json":
          {
            var json = Args.Get(spec, "json", "data") ?? throw RpcException.InvalidParams("'json' (RhinoCommon/rhino3dm JSON) is required.");
            var text = json.Type == JTokenType.String ? (string)json : json.ToString(Newtonsoft.Json.Formatting.None);
            var obj = Rhino.Runtime.CommonObject.FromJSON(text) as GeometryBase;
            if (obj == null) throw RpcException.InvalidParams("The JSON does not decode to Rhino geometry.");
            return One(obj);
          }
      }
      throw RpcException.InvalidParams("Unknown geometry type '" + type + "'. Supported: " + string.Join(", ", SupportedTypes));
    }

    /// <summary>
    /// Extrudes a closed profile — the typical building-from-footprint operation.
    /// Vertical extrusions of horizontal profiles become light Extrusion objects;
    /// any other direction becomes a capped Brep.
    /// </summary>
    private static GeometryBase Extrude(RhinoDoc doc, JObject spec, double tol)
    {
      var profile = Curves(doc, spec, "profile").FirstOrDefault()
                    ?? throw RpcException.InvalidParams("An extrusion needs a profile: 'points' (closed polyline) or 'curve_id'.");
      bool cap = Args.Bool(spec, "cap", true);
      Vector3d dir;
      if (Args.Has(spec, "direction", "vector"))
      {
        dir = Args.Vector(Args.Get(spec, "direction", "vector"), "direction");
        if (Args.Has(spec, "height"))
        {
          dir.Unitize();
          dir *= Args.NumRequired(spec, "height");
        }
      }
      else
      {
        dir = new Vector3d(0, 0, Args.NumRequired(spec, "height"));
      }
      if (dir.IsTiny()) throw RpcException.InvalidParams("Extrusion height/direction is zero.");

      bool vertical = Math.Abs(dir.X) < tol && Math.Abs(dir.Y) < tol;
      if (vertical && profile.TryGetPlane(out Plane pl, tol) && Math.Abs(Math.Abs(pl.ZAxis.Z) - 1) < 1e-6 && (profile.IsClosed || !cap))
      {
        double h = pl.ZAxis.Z > 0 ? dir.Z : -dir.Z;
        var ex = Extrusion.Create(profile, h, cap && profile.IsClosed);
        if (ex != null) return ex;
      }

      var srf = Surface.CreateExtrusion(profile, dir);
      if (srf == null) throw RpcException.Failed("Extrusion failed.");
      var brep = srf.ToBrep();
      if (cap && profile.IsClosed) brep = brep.CapPlanarHoles(tol) ?? brep;
      if (brep.IsSolid && brep.SolidOrientation == BrepSolidOrientation.Inward) brep.Flip();
      return brep;
    }

    private static Mesh MeshFromSpec(JObject spec)
    {
      var verts = Args.Points(Args.Get(spec, "vertices"), "vertices");
      if (!(Args.Get(spec, "faces") is JArray faces) || faces.Count == 0) throw RpcException.InvalidParams("'faces' must list vertex indices.");
      var mesh = new Mesh();
      foreach (var v in verts) mesh.Vertices.Add(v);
      for (int i = 0; i < faces.Count; i++)
      {
        if (!(faces[i] is JArray f) || (f.Count != 3 && f.Count != 4)) throw RpcException.InvalidParams($"faces[{i}] must have 3 or 4 indices.");
        var idx = f.Select(x => (int)Args.ToDouble(x)).ToArray();
        if (idx.Any(k => k < 0 || k >= verts.Count)) throw RpcException.InvalidParams($"faces[{i}] references a missing vertex.");
        if (idx.Length == 3) mesh.Faces.AddFace(idx[0], idx[1], idx[2]);
        else mesh.Faces.AddFace(idx[0], idx[1], idx[2], idx[3]);
      }
      if (Args.Get(spec, "vertex_colors") is JArray colours && colours.Count == verts.Count)
      {
        foreach (var c in colours) mesh.VertexColors.Add(Args.Colour(c) ?? System.Drawing.Color.White);
      }
      mesh.Normals.ComputeNormals();
      mesh.Compact();
      if (!mesh.IsValid) throw RpcException.Failed("The mesh is invalid (check face indices).");
      return mesh;
    }

    /// <summary>
    /// Curves from 'curve_ids' / 'curve_id' (existing Rhino objects) or point lists:
    /// 'points' (one closed or open polyline) or '<listKey>' (list of point lists).
    /// </summary>
    public static List<Curve> Curves(RhinoDoc doc, JObject spec, string listKey = "curves")
    {
      // Profiles are closed by default (building footprints); rails and sections are not.
      bool defaultClosed = listKey == "profile";
      bool closed = Args.Bool(spec, "closed", defaultClosed);
      var result = new List<Curve>();
      foreach (var id in Args.Guids(Args.Get(spec, "curve_ids", "curve_id")))
      {
        var obj = doc.Objects.FindId(id) ?? throw RpcException.NotFound("No object " + id + ".");
        if (!(obj.Geometry is Curve c)) throw RpcException.InvalidParams("Object " + id + " is not a curve.");
        result.Add(c.DuplicateCurve());
      }
      if (Args.Get(spec, listKey, "curves") is JArray lists && lists.Count > 0)
      {
        if (IsPointList(lists))
        {
          result.Add(Polyline(doc, Args.Points(lists, listKey), closed));
        }
        else
        {
          foreach (var item in lists)
          {
            if (item is JArray pl && IsPointList(pl)) result.Add(Polyline(doc, Args.Points(pl, listKey), listKey == "profile" && closed));
            else if (item.Type == JTokenType.String) result.AddRange(Curves(doc, new JObject { ["curve_id"] = item }));
            else throw RpcException.InvalidParams($"'{listKey}' items must be point lists or curve ids.");
          }
        }
      }
      if (Args.Has(spec, "points"))
        result.Add(Polyline(doc, Args.Points(Args.Get(spec, "points")), closed));
      return result;
    }

    private static bool IsPointList(JArray arr)
      => arr.Count > 0 && ((arr[0] is JArray first && first.Count > 0 && !(first[0] is JArray)) || arr[0] is JObject);

    private static Curve Polyline(RhinoDoc doc, List<Point3d> pts, bool closed)
    {
      if (closed && pts[0].DistanceTo(pts[pts.Count - 1]) > doc.ModelAbsoluteTolerance) pts.Add(pts[0]);
      if (pts.Count < 2) throw RpcException.InvalidParams("A polyline needs at least 2 points.");
      return new PolylineCurve(pts);
    }

    private static double[] Size(JObject spec)
    {
      if (Args.Get(spec, "size") is JArray s && s.Count == 3)
        return s.Select(x => Args.ToDouble(x, "size")).ToArray();
      return new[] { Positive(spec, "width"), Positive(spec, "depth", "length"), Positive(spec, "height") };
    }

    private static double Positive(JObject spec, params string[] keys)
    {
      var t = Args.Get(spec, keys) ?? throw RpcException.InvalidParams($"'{keys[0]}' is required.");
      double v = Args.ToDouble(t, keys[0]);
      if (!(v > 0)) throw RpcException.InvalidParams($"'{keys[0]}' must be > 0.");
      return v;
    }

    private static Plane MoveTo(Plane pl, Point3d origin)
    {
      pl.Origin = origin;
      return pl;
    }

    private static List<GeometryBase> One(GeometryBase g) => new List<GeometryBase> { g };
  }
}
