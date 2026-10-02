using System;
using Newtonsoft.Json.Linq;
using Rhino.FileIO;
using Rhino.Geometry;

namespace RhinoMcpBridge.Core
{
  /// <summary>Describes geometry for Claude: type, bounding box and, on request, measurements.</summary>
  public static class GeometryInfo
  {
    public static JObject Describe(GeometryBase g, bool metrics, bool includeJson = false)
    {
      var o = new JObject { ["geometry_type"] = Kind(g) };
      var bbox = g.GetBoundingBox(true);
      o["bbox"] = J.BBox(bbox);

      switch (g)
      {
        case Point pt:
          o["location"] = J.P(pt.Location);
          break;
        case PointCloud pc:
          o["point_count"] = pc.Count;
          break;
        case Curve c:
          o["closed"] = c.IsClosed;
          o["planar"] = c.IsPlanar();
          o["degree"] = c.Degree;
          o["start"] = J.P(c.PointAtStart);
          o["end"] = J.P(c.PointAtEnd);
          if (c.TryGetPolyline(out Polyline pl)) o["vertex_count"] = pl.Count;
          if (metrics)
          {
            o["length"] = J.N(c.GetLength());
            if (c.IsClosed && c.IsPlanar())
            {
              var amp = AreaMassProperties.Compute(c);
              if (amp != null)
              {
                o["enclosed_area"] = J.N(amp.Area);
                o["centroid"] = J.P(amp.Centroid);
              }
            }
          }
          break;
        case Extrusion ex:
          o["capped"] = ex.IsCappedAtTop && ex.IsCappedAtBottom;
          o["is_solid"] = ex.IsSolid;
          if (metrics) AddBrepMetrics(o, ex.ToBrep(false));
          break;
        case Brep b:
          o["faces"] = b.Faces.Count;
          o["edges"] = b.Edges.Count;
          o["is_solid"] = b.IsSolid;
          o["is_manifold"] = b.IsManifold;
          if (metrics) AddBrepMetrics(o, b);
          break;
        case Surface s:
          o["is_planar"] = s.IsPlanar();
          if (metrics) AddBrepMetrics(o, s.ToBrep());
          break;
        case Mesh m:
          o["vertices"] = m.Vertices.Count;
          o["faces"] = m.Faces.Count;
          o["is_closed"] = m.IsClosed;
          o["is_manifold"] = m.IsManifold(true, out _, out _);
          if (metrics)
          {
            var amp = AreaMassProperties.Compute(m);
            if (amp != null)
            {
              o["area"] = J.N(amp.Area);
              o["centroid"] = J.P(amp.Centroid);
            }
            if (m.IsClosed) o["volume"] = J.N(m.Volume());
          }
          break;
        case SubD sd:
          o["faces"] = sd.Faces.Count;
          if (metrics)
          {
            var brep = sd.ToBrep(SubDToBrepOptions.Default);
            if (brep != null) AddBrepMetrics(o, brep);
          }
          break;
        case TextDot dot:
          o["text"] = dot.Text;
          o["location"] = J.P(dot.Point);
          break;
        case TextEntity te:
          o["text"] = te.PlainText;
          o["plane"] = J.Plane(te.Plane);
          break;
        case InstanceReferenceGeometry irg:
          o["definition_id"] = irg.ParentIdefId.ToString();
          break;
      }

      if (includeJson)
      {
        try
        {
          o["json"] = JToken.Parse(g.ToJSON(new SerializationOptions()));
        }
        catch (Exception ex)
        {
          o["json_error"] = ex.Message;
        }
      }
      return o;
    }

    private static void AddBrepMetrics(JObject o, Brep b)
    {
      if (b == null) return;
      try
      {
        o["area"] = J.N(b.GetArea());
        if (b.IsSolid)
        {
          var vmp = VolumeMassProperties.Compute(b);
          if (vmp != null)
          {
            o["volume"] = J.N(Math.Abs(vmp.Volume));
            o["centroid"] = J.P(vmp.Centroid);
          }
        }
        else
        {
          var amp = AreaMassProperties.Compute(b);
          if (amp != null) o["centroid"] = J.P(amp.Centroid);
        }
      }
      catch (Exception ex)
      {
        o["metrics_error"] = ex.Message;
      }
    }

    public static string Kind(GeometryBase g)
    {
      switch (g)
      {
        case Point _: return "point";
        case PointCloud _: return "point_cloud";
        case LineCurve _: return "line";
        case PolylineCurve _: return "polyline";
        case ArcCurve a: return a.IsCompleteCircle ? "circle" : "arc";
        case PolyCurve _: return "polycurve";
        case NurbsCurve _: return "nurbs_curve";
        case Curve _: return "curve";
        case Extrusion _: return "extrusion";
        case Brep b: return b.IsSolid ? "brep_solid" : (b.Faces.Count == 1 ? "surface" : "polysurface");
        case Surface _: return "surface";
        case Mesh _: return "mesh";
        case SubD _: return "subd";
        case TextDot _: return "text_dot";
        case TextEntity _: return "text";
        case InstanceReferenceGeometry _: return "block_instance";
        default: return g.ObjectType.ToString().ToLowerInvariant();
      }
    }

    /// <summary>Area, volume and length aggregated into running totals (used for result metrics).</summary>
    public sealed class Totals
    {
      public int Count;
      public double Length, Area, Volume;
      public BoundingBox Box = BoundingBox.Empty;

      public void Add(GeometryBase g)
      {
        Count++;
        Box.Union(g.GetBoundingBox(true));
        try
        {
          switch (g)
          {
            case Curve c:
              Length += c.GetLength();
              break;
            case Extrusion ex:
              AddBrep(ex.ToBrep(false));
              break;
            case Brep b:
              AddBrep(b);
              break;
            case Surface s:
              Area += AreaMassProperties.Compute(s)?.Area ?? 0;
              break;
            case Mesh m:
              Area += AreaMassProperties.Compute(m)?.Area ?? 0;
              if (m.IsClosed) Volume += Math.Abs(m.Volume());
              break;
          }
        }
        catch
        {
          // Measurements are best effort; invalid geometry is still counted.
        }
      }

      private void AddBrep(Brep b)
      {
        if (b == null) return;
        Area += b.GetArea();
        if (b.IsSolid) Volume += Math.Abs(b.GetVolume());
      }

      public JObject ToJson() => new JObject
      {
        ["count"] = Count,
        ["total_length"] = J.N(Length),
        ["total_area"] = J.N(Area),
        ["total_volume"] = J.N(Volume),
        ["bbox"] = J.BBox(Box),
      };
    }
  }
}
