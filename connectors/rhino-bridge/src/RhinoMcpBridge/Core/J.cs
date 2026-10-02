using System;
using System.Drawing;
using Newtonsoft.Json.Linq;
using Rhino.Geometry;

namespace RhinoMcpBridge.Core
{
  /// <summary>Compact JSON writers for Rhino values (rounded to keep responses readable).</summary>
  public static class J
  {
    public const int Decimals = 6;

    public static double R(double v)
    {
      if (double.IsNaN(v) || double.IsInfinity(v)) return 0;
      return Math.Round(v, Decimals);
    }

    public static JToken N(double v)
    {
      if (double.IsNaN(v) || double.IsInfinity(v)) return JValue.CreateNull();
      return new JValue(Math.Round(v, Decimals));
    }

    public static JArray P(Point3d p) => new JArray(R(p.X), R(p.Y), R(p.Z));
    public static JArray V(Vector3d v) => new JArray(R(v.X), R(v.Y), R(v.Z));

    public static JObject Plane(Plane pl) => new JObject
    {
      ["origin"] = P(pl.Origin),
      ["x_axis"] = V(pl.XAxis),
      ["y_axis"] = V(pl.YAxis),
      ["normal"] = V(pl.ZAxis),
    };

    public static JToken BBox(BoundingBox b)
    {
      if (!b.IsValid) return JValue.CreateNull();
      return new JObject
      {
        ["min"] = P(b.Min),
        ["max"] = P(b.Max),
        ["size"] = new JArray(R(b.Max.X - b.Min.X), R(b.Max.Y - b.Min.Y), R(b.Max.Z - b.Min.Z)),
        ["center"] = P(b.Center),
      };
    }

    public static string Hex(Color c)
      => c.A == 255 ? $"#{c.R:X2}{c.G:X2}{c.B:X2}" : $"#{c.A:X2}{c.R:X2}{c.G:X2}{c.B:X2}";
  }
}
