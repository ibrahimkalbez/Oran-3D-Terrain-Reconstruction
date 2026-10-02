using System;
using System.Collections.Generic;

namespace RevitMcpBridge.Core
{
  /// <summary>
  /// Triangle soup with a bounding-volume hierarchy for fast ray casting (sun hours, shadows,
  /// draping points on a terrain). Independent of the Revit API — unit-tested on its own.
  /// Coordinates in any consistent unit (the bridge uses meters).
  /// </summary>
  public sealed class RayMesh
  {
    private readonly List<double> _v = new List<double>(); // 9 doubles per triangle
    private Node _root;
    private int[] _order;

    private sealed class Node
    {
      public double MinX, MinY, MinZ, MaxX, MaxY, MaxZ;
      public Node Left, Right;
      public int Start, Count; // leaf range in _order
    }

    public int TriangleCount => _v.Count / 9;

    public void AddTriangle(double ax, double ay, double az, double bx, double by, double bz, double cx, double cy, double cz)
    {
      _v.Add(ax); _v.Add(ay); _v.Add(az);
      _v.Add(bx); _v.Add(by); _v.Add(bz);
      _v.Add(cx); _v.Add(cy); _v.Add(cz);
      _root = null;
    }

    public (double minX, double minY, double minZ, double maxX, double maxY, double maxZ) Bounds()
    {
      Build();
      return _root == null ? (0, 0, 0, 0, 0, 0) : (_root.MinX, _root.MinY, _root.MinZ, _root.MaxX, _root.MaxY, _root.MaxZ);
    }

    public void Build()
    {
      if (_root != null || TriangleCount == 0) return;
      _order = new int[TriangleCount];
      for (int i = 0; i < _order.Length; i++) _order[i] = i;
      _root = BuildNode(0, _order.Length);
    }

    private double C(int tri, int axis) => (_v[tri * 9 + axis] + _v[tri * 9 + 3 + axis] + _v[tri * 9 + 6 + axis]) / 3.0;

    private Node BuildNode(int start, int count)
    {
      var n = new Node { MinX = double.MaxValue, MinY = double.MaxValue, MinZ = double.MaxValue, MaxX = double.MinValue, MaxY = double.MinValue, MaxZ = double.MinValue, Start = start, Count = count };
      for (int i = start; i < start + count; i++)
      {
        int t = _order[i] * 9;
        for (int k = 0; k < 9; k += 3)
        {
          n.MinX = Math.Min(n.MinX, _v[t + k]); n.MaxX = Math.Max(n.MaxX, _v[t + k]);
          n.MinY = Math.Min(n.MinY, _v[t + k + 1]); n.MaxY = Math.Max(n.MaxY, _v[t + k + 1]);
          n.MinZ = Math.Min(n.MinZ, _v[t + k + 2]); n.MaxZ = Math.Max(n.MaxZ, _v[t + k + 2]);
        }
      }
      if (count <= 8) return n;
      double dx = n.MaxX - n.MinX, dy = n.MaxY - n.MinY, dz = n.MaxZ - n.MinZ;
      int axis = dx >= dy && dx >= dz ? 0 : dy >= dz ? 1 : 2;
      Array.Sort(_order, start, count, Comparer<int>.Create((a, b) => C(a, axis).CompareTo(C(b, axis))));
      int half = count / 2;
      n.Left = BuildNode(start, half);
      n.Right = BuildNode(start + half, count - half);
      n.Count = 0;
      return n;
    }

    private static bool HitsBox(Node n, double ox, double oy, double oz, double ix, double iy, double iz, double tMax)
    {
      double t1 = (n.MinX - ox) * ix, t2 = (n.MaxX - ox) * ix;
      double tmin = Math.Min(t1, t2), tmax = Math.Max(t1, t2);
      t1 = (n.MinY - oy) * iy; t2 = (n.MaxY - oy) * iy;
      tmin = Math.Max(tmin, Math.Min(t1, t2)); tmax = Math.Min(tmax, Math.Max(t1, t2));
      t1 = (n.MinZ - oz) * iz; t2 = (n.MaxZ - oz) * iz;
      tmin = Math.Max(tmin, Math.Min(t1, t2)); tmax = Math.Min(tmax, Math.Max(t1, t2));
      return tmax >= Math.Max(tmin, 0) && tmin <= tMax;
    }

    /// <summary>Möller–Trumbore; returns t ≥ tMin or −1.</summary>
    private double HitTriangle(int tri, double ox, double oy, double oz, double dx, double dy, double dz, double tMin)
    {
      int b = tri * 9;
      double e1x = _v[b + 3] - _v[b], e1y = _v[b + 4] - _v[b + 1], e1z = _v[b + 5] - _v[b + 2];
      double e2x = _v[b + 6] - _v[b], e2y = _v[b + 7] - _v[b + 1], e2z = _v[b + 8] - _v[b + 2];
      double px = dy * e2z - dz * e2y, py = dz * e2x - dx * e2z, pz = dx * e2y - dy * e2x;
      double det = e1x * px + e1y * py + e1z * pz;
      if (Math.Abs(det) < 1e-12) return -1;
      double inv = 1.0 / det;
      double tx = ox - _v[b], ty = oy - _v[b + 1], tz = oz - _v[b + 2];
      double u = (tx * px + ty * py + tz * pz) * inv;
      if (u < 0 || u > 1) return -1;
      double qx = ty * e1z - tz * e1y, qy = tz * e1x - tx * e1z, qz = tx * e1y - ty * e1x;
      double v = (dx * qx + dy * qy + dz * qz) * inv;
      if (v < 0 || u + v > 1) return -1;
      double t = (e2x * qx + e2y * qy + e2z * qz) * inv;
      return t >= tMin ? t : -1;
    }

    /// <summary>Distance along the (unit) direction to the nearest triangle, or −1 when nothing is hit.</summary>
    public double Nearest(double ox, double oy, double oz, double dx, double dy, double dz, double tMin = 1e-6)
    {
      Build();
      if (_root == null) return -1;
      double best = double.MaxValue;
      Walk(_root, ox, oy, oz, dx, dy, dz, tMin, ref best, false);
      return best == double.MaxValue ? -1 : best;
    }

    /// <summary>True when any triangle is hit (early exit: used for shadows).</summary>
    public bool Blocked(double ox, double oy, double oz, double dx, double dy, double dz, double tMin = 1e-6)
    {
      Build();
      if (_root == null) return false;
      double best = double.MaxValue;
      return Walk(_root, ox, oy, oz, dx, dy, dz, tMin, ref best, true);
    }

    private bool Walk(Node n, double ox, double oy, double oz, double dx, double dy, double dz, double tMin, ref double best, bool any)
    {
      double ix = 1.0 / (dx == 0 ? 1e-300 : dx), iy = 1.0 / (dy == 0 ? 1e-300 : dy), iz = 1.0 / (dz == 0 ? 1e-300 : dz);
      var stack = new Stack<Node>();
      stack.Push(n);
      bool hit = false;
      while (stack.Count > 0)
      {
        var node = stack.Pop();
        if (!HitsBox(node, ox, oy, oz, ix, iy, iz, best)) continue;
        if (node.Left == null)
        {
          for (int i = node.Start; i < node.Start + node.Count; i++)
          {
            double t = HitTriangle(_order[i], ox, oy, oz, dx, dy, dz, tMin);
            if (t >= 0 && t < best)
            {
              best = t;
              hit = true;
              if (any) return true;
            }
          }
        }
        else
        {
          stack.Push(node.Left);
          stack.Push(node.Right);
        }
      }
      return hit;
    }
  }
}
