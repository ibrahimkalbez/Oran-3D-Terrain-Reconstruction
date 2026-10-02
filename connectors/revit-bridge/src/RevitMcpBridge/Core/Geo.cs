using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.DB;

namespace RevitMcpBridge.Core
{
  /// <summary>Element geometry access: solids, meshes and triangles (meters).</summary>
  public static class Geo
  {
    private static Options Opts() => new Options { DetailLevel = ViewDetailLevel.Fine, ComputeReferences = false, IncludeNonVisibleObjects = false };

    public static IEnumerable<GeometryObject> Objects(Element e)
    {
      var ge = e.get_Geometry(Opts());
      if (ge == null) yield break;
      foreach (var o in Flatten(ge)) yield return o;
    }

    private static IEnumerable<GeometryObject> Flatten(GeometryElement ge)
    {
      foreach (var o in ge)
      {
        if (o is GeometryInstance gi)
        {
          var inner = gi.GetInstanceGeometry();
          if (inner != null) foreach (var x in Flatten(inner)) yield return x;
        }
        else if (o is GeometryElement nested)
        {
          foreach (var x in Flatten(nested)) yield return x;
        }
        else yield return o;
      }
    }

    public static List<Solid> Solids(Element e) => Objects(e).OfType<Solid>().Where(s => s.Volume > 1e-9 || s.Faces.Size > 0).ToList();

    /// <summary>Adds the element's triangles (faces of solids, meshes) to the ray mesh, in meters.</summary>
    public static int AddTriangles(Element e, RayMesh target)
    {
      int n = 0;
      foreach (var o in Objects(e))
      {
        if (o is Solid s)
        {
          foreach (Face f in s.Faces)
          {
            Mesh m;
            try { m = f.Triangulate(0.5); } catch { continue; }
            if (m != null) n += AddMesh(m, target);
          }
        }
        else if (o is Mesh mesh)
        {
          n += AddMesh(mesh, target);
        }
      }
      return n;
    }

    private static int AddMesh(Mesh m, RayMesh target)
    {
      for (int i = 0; i < m.NumTriangles; i++)
      {
        var t = m.get_Triangle(i);
        XYZ a = t.get_Vertex(0), b = t.get_Vertex(1), c = t.get_Vertex(2);
        target.AddTriangle(U.ToMeters(a.X), U.ToMeters(a.Y), U.ToMeters(a.Z), U.ToMeters(b.X), U.ToMeters(b.Y), U.ToMeters(b.Z), U.ToMeters(c.X), U.ToMeters(c.Y), U.ToMeters(c.Z));
      }
      return m.NumTriangles;
    }

    /// <summary>Triangles as flat lists (meters) for STL/OBJ export.</summary>
    public static List<XYZ[]> Triangles(Element e)
    {
      var list = new List<XYZ[]>();
      foreach (var o in Objects(e))
      {
        IEnumerable<Mesh> meshes = o is Solid s ? s.Faces.Cast<Face>().Select(f => { try { return f.Triangulate(0.5); } catch { return null; } }) : o is Mesh m ? new[] { m } : Enumerable.Empty<Mesh>();
        foreach (var mesh in meshes)
        {
          if (mesh == null) continue;
          for (int i = 0; i < mesh.NumTriangles; i++)
          {
            var t = mesh.get_Triangle(i);
            list.Add(new[] { t.get_Vertex(0), t.get_Vertex(1), t.get_Vertex(2) });
          }
        }
      }
      return list;
    }
  }
}
