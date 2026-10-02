using System;
using System.Collections.Generic;
using System.Linq;
using RevitMcpBridge.Core;
using Xunit;

/// <summary>Ray casting (sun hours, draping) and plan polygons, without Revit.</summary>
public class CoreTests
{
  /// <summary>Axis-aligned box [x0,x1]×[y0,y1]×[z0,z1] as 12 triangles.</summary>
  private static void AddBox(RayMesh m, double x0, double y0, double z0, double x1, double y1, double z1)
  {
    var v = new[]
    {
      new[] { x0, y0, z0 }, new[] { x1, y0, z0 }, new[] { x1, y1, z0 }, new[] { x0, y1, z0 },
      new[] { x0, y0, z1 }, new[] { x1, y0, z1 }, new[] { x1, y1, z1 }, new[] { x0, y1, z1 },
    };
    int[][] quads = { new[] { 0, 1, 2, 3 }, new[] { 4, 5, 6, 7 }, new[] { 0, 1, 5, 4 }, new[] { 1, 2, 6, 5 }, new[] { 2, 3, 7, 6 }, new[] { 3, 0, 4, 7 } };
    foreach (var q in quads)
    {
      void T(int a, int b, int c) => m.AddTriangle(v[a][0], v[a][1], v[a][2], v[b][0], v[b][1], v[b][2], v[c][0], v[c][1], v[c][2]);
      T(q[0], q[1], q[2]);
      T(q[0], q[2], q[3]);
    }
  }

  [Fact]
  public void RayHitsTheNearestFace()
  {
    var m = new RayMesh();
    AddBox(m, 0, 0, 0, 10, 10, 10);
    Assert.Equal(12, m.TriangleCount);
    // From above, straight down: top face at z = 10.
    Assert.Equal(90, m.Nearest(5, 5, 100, 0, 0, -1), 6);
    // Missing the box.
    Assert.Equal(-1, m.Nearest(50, 50, 100, 0, 0, -1));
    var b = m.Bounds();
    Assert.Equal((0.0, 0.0, 0.0, 10.0, 10.0, 10.0), b);
  }

  [Fact]
  public void ShadowsAreBlockedOnlyBehindObstacles()
  {
    var m = new RayMesh();
    AddBox(m, 10, -5, 0, 20, 5, 30); // a 30 m tower east of the origin
    var east = new[] { 1.0, 0, 0.2 };
    double n = Math.Sqrt(east.Sum(x => x * x));
    Assert.True(m.Blocked(0, 0, 1, east[0] / n, east[1] / n, east[2] / n));   // low eastern sun: in the shade
    Assert.False(m.Blocked(0, 0, 1, -east[0] / n, east[1] / n, east[2] / n)); // western sun: lit
    Assert.False(m.Blocked(0, 0, 1, 0, 0, 1));                                 // zenith: lit
  }

  [Fact]
  public void LargeMeshesUseTheHierarchyConsistently()
  {
    // A 100×100 terrain grid (20 000 triangles) with a slope: z = 0.1·x.
    var m = new RayMesh();
    for (int i = 0; i < 100; i++)
      for (int j = 0; j < 100; j++)
      {
        double x0 = i, x1 = i + 1, y0 = j, y1 = j + 1;
        m.AddTriangle(x0, y0, 0.1 * x0, x1, y0, 0.1 * x1, x1, y1, 0.1 * x1);
        m.AddTriangle(x0, y0, 0.1 * x0, x1, y1, 0.1 * x1, x0, y1, 0.1 * x0);
      }
    var rnd = new Random(7);
    for (int k = 0; k < 200; k++)
    {
      double x = rnd.NextDouble() * 99.5, y = rnd.NextDouble() * 99.5;
      double t = m.Nearest(x, y, 50, 0, 0, -1);
      Assert.Equal(0.1 * x, 50 - t, 6); // draped height
    }
  }

  [Fact]
  public void PolygonAreaCentroidAndInside()
  {
    var square = new List<double[]> { new[] { 0.0, 0 }, new[] { 10.0, 0 }, new[] { 10.0, 10 }, new[] { 0.0, 10 } };
    Assert.Equal(100, Polygon2D.SignedArea(square), 9);
    Assert.Equal(-100, Polygon2D.SignedArea(Enumerable.Reverse(square).ToList()), 9);
    Assert.Equal(new[] { 5.0, 5.0 }, Polygon2D.Centroid(square));
    Assert.True(Polygon2D.Inside(new[] { 3.0, 3 }, square));
    Assert.False(Polygon2D.Inside(new[] { 13.0, 3 }, square));
  }

  [Fact]
  public void CourtyardsBecomeHoles()
  {
    var outer = new List<double[]> { new[] { 0.0, 0 }, new[] { 30.0, 0 }, new[] { 30.0, 30 }, new[] { 0.0, 30 } };
    var court = new List<double[]> { new[] { 10.0, 10 }, new[] { 20.0, 10 }, new[] { 20.0, 20 }, new[] { 10.0, 20 } };
    var annex = new List<double[]> { new[] { 40.0, 0 }, new[] { 50.0, 0 }, new[] { 50.0, 10 }, new[] { 40.0, 10 } };
    var parts = Polygon2D.Parts(new[] { court, annex, outer });
    Assert.Equal(2, parts.Count);
    Assert.Single(parts[0].holes);
    Assert.Equal(900 - 100 + 100, Polygon2D.Area(parts), 9);
  }

  [Fact]
  public void ConvexHullOfScatteredPoints()
  {
    var pts = new List<double[]> { new[] { 0.0, 0 }, new[] { 4.0, 0 }, new[] { 4.0, 3 }, new[] { 0.0, 3 }, new[] { 2.0, 1 }, new[] { 1.0, 2 }, new[] { 2.0, 0 } };
    var hull = Polygon2D.ConvexHull(pts);
    Assert.Equal(4, hull.Count);
    Assert.Equal(12, Math.Abs(Polygon2D.SignedArea(hull)), 9);
  }

  [Fact]
  public void SegmentsAreChainedIntoClosedLoops()
  {
    // A property line drawn as 4 segments in arbitrary order and directions, plus an open road.
    var segments = new List<List<double[]>>
    {
      new() { new[] { 10.0, 0, 0 }, new[] { 10.0, 10, 0 } },
      new() { new[] { 0.0, 0, 0 }, new[] { 10.0, 0, 0 } },
      new() { new[] { 0.0, 10, 0 }, new[] { 0.0, 0, 0 } },
      new() { new[] { 0.0, 10, 0 }, new[] { 10.0, 10, 0 } }, // reversed
      new() { new[] { 50.0, 0, 0 }, new[] { 60.0, 0, 0 }, new[] { 70.0, 5, 0 } },
    };
    var chains = Polygon2D.Chain(segments, 1e-3);
    Assert.Equal(2, chains.Count);
    var loop = chains.Single(c => c.Count == 5);
    Assert.Equal(0, Polygon2D.Distance(loop[0], loop[4]), 9); // closed
    Assert.Equal(100, Math.Abs(Polygon2D.SignedArea(loop.Take(4).ToList())), 9);
    Assert.Equal(3, chains.Single(c => c.Count != 5).Count);
  }
}
