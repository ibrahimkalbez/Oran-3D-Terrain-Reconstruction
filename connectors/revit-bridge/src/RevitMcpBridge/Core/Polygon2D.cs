using System;
using System.Collections.Generic;
using System.Linq;

namespace RevitMcpBridge.Core
{
  /// <summary>
  /// Plan-polygon helpers used by analysis.footprints / analysis.curves. Independent of the
  /// Revit API (points are double[] {x, y[, z]}) so they are unit-tested on their own.
  /// </summary>
  public static class Polygon2D
  {
    public static double SignedArea(IList<double[]> ring)
    {
      double a = 0;
      for (int i = 0; i < ring.Count; i++)
      {
        var p = ring[i];
        var q = ring[(i + 1) % ring.Count];
        a += p[0] * q[1] - q[0] * p[1];
      }
      return a / 2;
    }

    public static bool Inside(double[] p, IList<double[]> ring)
    {
      bool inside = false;
      for (int i = 0, j = ring.Count - 1; i < ring.Count; j = i++)
      {
        var a = ring[i];
        var b = ring[j];
        if ((a[1] > p[1]) != (b[1] > p[1]) && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
      }
      return inside;
    }

    public static double[] Centroid(IList<double[]> ring)
    {
      double a = 0, cx = 0, cy = 0;
      for (int i = 0; i < ring.Count; i++)
      {
        var p = ring[i];
        var q = ring[(i + 1) % ring.Count];
        double f = p[0] * q[1] - q[0] * p[1];
        a += f;
        cx += (p[0] + q[0]) * f;
        cy += (p[1] + q[1]) * f;
      }
      if (Math.Abs(a) < 1e-12) return new[] { ring.Average(r => r[0]), ring.Average(r => r[1]) };
      return new[] { cx / (3 * a), cy / (3 * a) };
    }

    /// <summary>Andrew's monotone chain (counter-clockwise, no repeated point).</summary>
    public static List<double[]> ConvexHull(IEnumerable<double[]> points)
    {
      var p = points.OrderBy(a => a[0]).ThenBy(a => a[1]).ToList();
      if (p.Count < 3) return p;
      double Cross(double[] o, double[] a, double[] b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
      var hull = new List<double[]>();
      foreach (var pt in p)
      {
        while (hull.Count >= 2 && Cross(hull[hull.Count - 2], hull[hull.Count - 1], pt) <= 0) hull.RemoveAt(hull.Count - 1);
        hull.Add(pt);
      }
      int lower = hull.Count + 1;
      for (int i = p.Count - 2; i >= 0; i--)
      {
        while (hull.Count >= lower && Cross(hull[hull.Count - 2], hull[hull.Count - 1], p[i]) <= 0) hull.RemoveAt(hull.Count - 1);
        hull.Add(p[i]);
      }
      hull.RemoveAt(hull.Count - 1);
      return hull;
    }

    public static double Distance(double[] a, double[] b)
    {
      double s = 0;
      for (int k = 0; k < Math.Min(a.Length, b.Length); k++) s += (a[k] - b[k]) * (a[k] - b[k]);
      return Math.Sqrt(s);
    }

    /// <summary>Joins segments (point lists) sharing end points into polylines; a closed chain ends on its first point.</summary>
    public static List<List<double[]>> Chain(IEnumerable<List<double[]>> segments, double tol)
    {
      var open = segments.Where(s => s.Count >= 2).Select(s => new List<double[]>(s)).ToList();
      var result = new List<List<double[]>>();
      while (open.Count > 0)
      {
        var current = open[0];
        open.RemoveAt(0);
        bool grown = true;
        while (grown && Distance(current[0], current[current.Count - 1]) > tol)
        {
          grown = false;
          for (int i = 0; i < open.Count; i++)
          {
            var s = open[i];
            var head = current[0];
            var tail = current[current.Count - 1];
            if (Distance(tail, s[0]) <= tol) current.AddRange(s.Skip(1));
            else if (Distance(tail, s[s.Count - 1]) <= tol) current.AddRange(Enumerable.Reverse(s).Skip(1));
            else if (Distance(head, s[s.Count - 1]) <= tol) current.InsertRange(0, s.Take(s.Count - 1));
            else if (Distance(head, s[0]) <= tol) current.InsertRange(0, Enumerable.Reverse(s).Take(s.Count - 1));
            else continue;
            open.RemoveAt(i);
            grown = true;
            break;
          }
        }
        result.Add(current);
      }
      return result;
    }

    /// <summary>Groups loops into parts: the largest loops are outlines, a loop inside one is a hole (courtyard).</summary>
    public static List<(List<double[]> outer, List<List<double[]>> holes)> Parts(IEnumerable<List<double[]>> loops)
    {
      var parts = new List<(List<double[]> outer, List<List<double[]>> holes)>();
      foreach (var loop in loops.Where(l => l.Count >= 3).OrderByDescending(l => Math.Abs(SignedArea(l))))
      {
        int owner = parts.FindIndex(pt => Inside(loop[0], pt.outer) && Math.Abs(SignedArea(loop)) < Math.Abs(SignedArea(pt.outer)));
        if (owner >= 0) parts[owner].holes.Add(loop);
        else parts.Add((loop, new List<List<double[]>>()));
      }
      return parts;
    }

    public static double Area(List<(List<double[]> outer, List<List<double[]>> holes)> parts)
      => parts.Sum(pt => Math.Abs(SignedArea(pt.outer)) - pt.holes.Sum(h => Math.Abs(SignedArea(h))));
  }
}
