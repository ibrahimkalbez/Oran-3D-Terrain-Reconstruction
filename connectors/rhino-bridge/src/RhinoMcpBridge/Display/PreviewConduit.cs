using System.Collections.Generic;
using System.Drawing;
using Rhino.Display;
using Rhino.Geometry;

namespace RhinoMcpBridge.Display
{
  /// <summary>
  /// Draws geometry that is not in the Rhino document (Grasshopper results, variants)
  /// as a white architectural model with dark edges. Used for viewport captures so the
  /// image does not depend on Grasshopper's own preview settings.
  /// </summary>
  public sealed class PreviewConduit : DisplayConduit
  {
    private readonly List<Mesh> _meshes = new List<Mesh>();
    private readonly List<Curve> _edges = new List<Curve>();
    private readonly List<Curve> _curves = new List<Curve>();
    private readonly List<Point3d> _points = new List<Point3d>();
    private BoundingBox _box = BoundingBox.Empty;
    private DisplayMaterial _material = new DisplayMaterial(Color.FromArgb(245, 245, 242), 0.0);

    public Color EdgeColor { get; set; } = Color.FromArgb(70, 70, 70);
    public Color CurveColor { get; set; } = Color.FromArgb(200, 30, 30);

    public BoundingBox Box => _box;
    public bool HasGeometry => _meshes.Count + _curves.Count + _points.Count > 0;

    public void SetColor(Color shade)
    {
      _material = new DisplayMaterial(shade, 0.0);
    }

    public void Clear()
    {
      _meshes.Clear();
      _edges.Clear();
      _curves.Clear();
      _points.Clear();
      _box = BoundingBox.Empty;
    }

    public void Add(IEnumerable<GeometryBase> geometry, bool drawEdges = true)
    {
      var mp = MeshingParameters.FastRenderMesh;
      foreach (var g in geometry)
      {
        if (g == null) continue;
        _box.Union(g.GetBoundingBox(false));
        switch (g)
        {
          case Rhino.Geometry.Point pt:
            _points.Add(pt.Location);
            break;
          case Curve c:
            _curves.Add(c);
            break;
          case Mesh m:
            _meshes.Add(m);
            if (drawEdges)
            {
              var naked = m.GetNakedEdges();
              if (naked != null) foreach (var pl in naked) _edges.Add(new PolylineCurve(pl));
            }
            break;
          case Extrusion ex:
            AddBrep(ex.ToBrep(false), mp, drawEdges);
            break;
          case Brep b:
            AddBrep(b, mp, drawEdges);
            break;
          case Surface s:
            AddBrep(s.ToBrep(), mp, drawEdges);
            break;
          case SubD sd:
            var sm = Mesh.CreateFromSubD(sd, 2);
            if (sm != null) _meshes.Add(sm);
            break;
        }
      }
    }

    private void AddBrep(Brep b, MeshingParameters mp, bool drawEdges)
    {
      if (b == null) return;
      var meshes = Mesh.CreateFromBrep(b, mp);
      if (meshes != null) _meshes.AddRange(meshes);
      if (drawEdges)
      {
        var edges = b.DuplicateEdgeCurves(false);
        if (edges != null) _edges.AddRange(edges);
      }
    }

    protected override void CalculateBoundingBox(CalculateBoundingBoxEventArgs e)
    {
      if (_box.IsValid) e.IncludeBoundingBox(_box);
    }

    protected override void CalculateBoundingBoxZoomExtents(CalculateBoundingBoxEventArgs e)
    {
      if (_box.IsValid) e.IncludeBoundingBox(_box);
    }

    protected override void PostDrawObjects(DrawEventArgs e)
    {
      foreach (var m in _meshes) e.Display.DrawMeshShaded(m, _material);
      foreach (var c in _edges) e.Display.DrawCurve(c, EdgeColor, 1);
      foreach (var c in _curves) e.Display.DrawCurve(c, CurveColor, 2);
      foreach (var p in _points) e.Display.DrawPoint(p, PointStyle.RoundSimple, 4, CurveColor);
    }
  }
}
