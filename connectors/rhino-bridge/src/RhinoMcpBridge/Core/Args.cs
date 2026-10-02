using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json.Linq;
using Rhino.Geometry;
using RhinoMcpBridge.Transport;

namespace RhinoMcpBridge.Core
{
  /// <summary>Lenient readers for JSON-RPC parameters. Errors become InvalidParams with a clear message.</summary>
  public static class Args
  {
    public static JToken Get(JObject p, params string[] keys)
    {
      foreach (var k in keys)
      {
        var t = p[k];
        if (t != null && t.Type != JTokenType.Null) return t;
      }
      return null;
    }

    public static bool Has(JObject p, params string[] keys) => Get(p, keys) != null;

    public static string Str(JObject p, string key, string def = null, bool required = false)
    {
      var t = Get(p, key);
      if (t == null)
      {
        if (required) throw RpcException.InvalidParams($"'{key}' is required.");
        return def;
      }
      return t.Type == JTokenType.String ? (string)t : t.ToString(Newtonsoft.Json.Formatting.None);
    }

    public static double Num(JObject p, string key, double def)
    {
      var t = Get(p, key);
      return t == null ? def : ToDouble(t, key);
    }

    public static double NumRequired(JObject p, string key)
    {
      var t = Get(p, key) ?? throw RpcException.InvalidParams($"'{key}' is required.");
      return ToDouble(t, key);
    }

    public static double? NumOpt(JObject p, string key)
    {
      var t = Get(p, key);
      return t == null ? (double?)null : ToDouble(t, key);
    }

    public static int Int(JObject p, string key, int def)
    {
      var t = Get(p, key);
      return t == null ? def : (int)Math.Round(ToDouble(t, key));
    }

    public static bool Bool(JObject p, string key, bool def)
    {
      var t = Get(p, key);
      if (t == null) return def;
      if (t.Type == JTokenType.Boolean) return (bool)t;
      var s = t.ToString().Trim().ToLowerInvariant();
      if (s == "true" || s == "1" || s == "yes" || s == "oui") return true;
      if (s == "false" || s == "0" || s == "no" || s == "non") return false;
      throw RpcException.InvalidParams($"'{key}' must be a boolean.");
    }

    public static double ToDouble(JToken t, string key = "value")
    {
      switch (t.Type)
      {
        case JTokenType.Integer:
        case JTokenType.Float:
          return t.Value<double>();
        case JTokenType.Boolean:
          return (bool)t ? 1 : 0;
        case JTokenType.String:
          if (double.TryParse(((string)t).Trim().Replace(',', '.'), NumberStyles.Float, CultureInfo.InvariantCulture, out var d))
            return d;
          break;
      }
      throw RpcException.InvalidParams($"'{key}' must be a number (got {t.ToString(Newtonsoft.Json.Formatting.None)}).");
    }

    // ------------------------------------------------------------------ geometry values

    /// <summary>Accepts [x,y], [x,y,z], {"x":..,"y":..,"z":..} or "x,y,z".</summary>
    public static Point3d Point(JToken t, string key = "point")
    {
      if (t == null) throw RpcException.InvalidParams($"'{key}' is required.");
      double[] v = Triple(t, key);
      return new Point3d(v[0], v[1], v[2]);
    }

    public static Point3d PointOr(JObject p, Point3d def, params string[] keys)
    {
      var t = Get(p, keys);
      return t == null ? def : Point(t, keys[0]);
    }

    public static Vector3d Vector(JToken t, string key = "vector")
    {
      if (t == null) throw RpcException.InvalidParams($"'{key}' is required.");
      if (t.Type == JTokenType.String)
      {
        switch (((string)t).Trim().ToLowerInvariant())
        {
          case "x": case "+x": return Vector3d.XAxis;
          case "-x": return -Vector3d.XAxis;
          case "y": case "+y": return Vector3d.YAxis;
          case "-y": return -Vector3d.YAxis;
          case "z": case "+z": case "up": return Vector3d.ZAxis;
          case "-z": case "down": return -Vector3d.ZAxis;
        }
      }
      double[] v = Triple(t, key);
      return new Vector3d(v[0], v[1], v[2]);
    }

    public static List<Point3d> Points(JToken t, string key = "points")
    {
      if (!(t is JArray arr) || arr.Count == 0) throw RpcException.InvalidParams($"'{key}' must be a non-empty array of points.");
      return arr.Select((x, i) => Point(x, $"{key}[{i}]")).ToList();
    }

    private static double[] Triple(JToken t, string key)
    {
      if (t is JArray a)
      {
        if (a.Count < 2 || a.Count > 3) throw RpcException.InvalidParams($"'{key}' must have 2 or 3 coordinates.");
        return new[] { ToDouble(a[0], key), ToDouble(a[1], key), a.Count == 3 ? ToDouble(a[2], key) : 0.0 };
      }
      if (t is JObject o)
      {
        return new[]
        {
          ToDouble(o["x"] ?? o["X"] ?? throw RpcException.InvalidParams($"'{key}.x' missing."), key),
          ToDouble(o["y"] ?? o["Y"] ?? throw RpcException.InvalidParams($"'{key}.y' missing."), key),
          o["z"] != null ? ToDouble(o["z"], key) : (o["Z"] != null ? ToDouble(o["Z"], key) : 0.0),
        };
      }
      if (t.Type == JTokenType.String)
      {
        var parts = ((string)t).Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length == 2 || parts.Length == 3)
          return new[] { ToDouble(parts[0], key), ToDouble(parts[1], key), parts.Length == 3 ? ToDouble(parts[2], key) : 0.0 };
      }
      throw RpcException.InvalidParams($"'{key}' must be [x,y,z] (got {t.ToString(Newtonsoft.Json.Formatting.None)}).");
    }

    /// <summary>
    /// Accepts null (world XY), "xy"/"yz"/"zx"/"xz", {"origin","normal"} or {"origin","x_axis","y_axis"}.
    /// </summary>
    public static Plane Plane(JToken t, Plane def)
    {
      if (t == null || t.Type == JTokenType.Null) return def;
      if (t.Type == JTokenType.String)
      {
        switch (((string)t).Trim().ToLowerInvariant())
        {
          case "xy": case "world_xy": case "top": return Rhino.Geometry.Plane.WorldXY;
          case "yz": case "world_yz": case "right": return Rhino.Geometry.Plane.WorldYZ;
          case "zx": case "xz": case "world_zx": case "front": return Rhino.Geometry.Plane.WorldZX;
        }
        throw RpcException.InvalidParams("Unknown plane '" + (string)t + "'. Use xy, yz, zx or an object.");
      }
      if (t is JObject o)
      {
        var origin = o["origin"] != null ? Point(o["origin"], "plane.origin") : def.Origin;
        if (o["x_axis"] != null && o["y_axis"] != null)
          return new Plane(origin, Vector(o["x_axis"], "plane.x_axis"), Vector(o["y_axis"], "plane.y_axis"));
        if (o["normal"] != null || o["z_axis"] != null)
          return new Plane(origin, Vector(o["normal"] ?? o["z_axis"], "plane.normal"));
        var pl = def;
        pl.Origin = origin;
        return pl;
      }
      throw RpcException.InvalidParams("'plane' must be a string or an object.");
    }

    public static Guid Guid(JToken t, string key = "id")
    {
      if (t != null && System.Guid.TryParse(t.ToString().Trim(), out var g)) return g;
      throw RpcException.InvalidParams($"'{key}' must be a GUID (got {t}).");
    }

    public static List<Guid> Guids(JToken t, string key = "ids")
    {
      if (t == null) return new List<Guid>();
      if (t is JArray arr) return arr.Select((x, i) => Guid(x, $"{key}[{i}]")).ToList();
      return new List<Guid> { Guid(t, key) };
    }

    public static List<string> Strings(JToken t)
    {
      if (t == null) return new List<string>();
      if (t is JArray arr) return arr.Select(x => x.ToString()).ToList();
      return new List<string> { t.ToString() };
    }

    /// <summary>Accepts "#RRGGBB", "#AARRGGBB", [r,g,b], [r,g,b,a] or a .NET colour name.</summary>
    public static Color? Colour(JToken t)
    {
      if (t == null || t.Type == JTokenType.Null) return null;
      if (t is JArray a && (a.Count == 3 || a.Count == 4))
      {
        int r = (int)ToDouble(a[0]), g = (int)ToDouble(a[1]), b = (int)ToDouble(a[2]);
        int alpha = a.Count == 4 ? (int)ToDouble(a[3]) : 255;
        return Color.FromArgb(Clamp(alpha), Clamp(r), Clamp(g), Clamp(b));
      }
      var s = t.ToString().Trim();
      if (s.StartsWith("#"))
      {
        var hex = s.Substring(1);
        if (hex.Length == 6 && int.TryParse(hex, NumberStyles.HexNumber, null, out var rgb))
          return Color.FromArgb(255, (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255);
        if (hex.Length == 8 && uint.TryParse(hex, NumberStyles.HexNumber, null, out var argb))
          return Color.FromArgb((int)((argb >> 24) & 255), (int)((argb >> 16) & 255), (int)((argb >> 8) & 255), (int)(argb & 255));
      }
      var named = Color.FromName(s);
      if (named.IsKnownColor) return named;
      throw RpcException.InvalidParams("Invalid colour '" + s + "'. Use #RRGGBB, [r,g,b] or a colour name.");
    }

    private static int Clamp(int v) => Math.Max(0, Math.Min(255, v));
  }
}
