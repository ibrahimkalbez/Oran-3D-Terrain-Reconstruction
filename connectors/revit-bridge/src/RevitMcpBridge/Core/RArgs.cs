using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Autodesk.Revit.DB;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;

namespace RevitMcpBridge.Core
{
  /// <summary>Lenient readers for JSON-RPC parameters; coordinates arrive in meters.</summary>
  public static class RArgs
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
          if (double.TryParse(((string)t).Trim().Replace(',', '.'), NumberStyles.Float, CultureInfo.InvariantCulture, out var d)) return d;
          break;
      }
      throw RpcException.InvalidParams($"'{key}' must be a number (got {t.ToString(Newtonsoft.Json.Formatting.None)}).");
    }

    /// <summary>[x, y] or [x, y, z] in meters → Revit XYZ (feet).</summary>
    public static XYZ Point(JToken t, string key = "point")
    {
      if (t is JArray a && (a.Count == 2 || a.Count == 3))
        return U.ToFeet(ToDouble(a[0], key), ToDouble(a[1], key), a.Count == 3 ? ToDouble(a[2], key) : 0);
      if (t is JObject o && o["x"] != null && o["y"] != null)
        return U.ToFeet(ToDouble(o["x"], key), ToDouble(o["y"], key), o["z"] != null ? ToDouble(o["z"], key) : 0);
      throw RpcException.InvalidParams($"'{key}' must be [x, y, z] in meters.");
    }

    public static XYZ PointOr(JObject p, XYZ def, params string[] keys)
    {
      var t = Get(p, keys);
      return t == null ? def : Point(t, keys[0]);
    }

    /// <summary>A direction (unitless) — not converted.</summary>
    public static XYZ Vector(JToken t, string key = "vector")
    {
      if (t != null && t.Type == JTokenType.String)
      {
        switch (((string)t).Trim().ToLowerInvariant())
        {
          case "x": return XYZ.BasisX;
          case "-x": return -XYZ.BasisX;
          case "y": return XYZ.BasisY;
          case "-y": return -XYZ.BasisY;
          case "z": case "up": return XYZ.BasisZ;
          case "-z": case "down": return -XYZ.BasisZ;
        }
      }
      if (t is JArray a && (a.Count == 2 || a.Count == 3))
        return new XYZ(ToDouble(a[0], key), ToDouble(a[1], key), a.Count == 3 ? ToDouble(a[2], key) : 0);
      throw RpcException.InvalidParams($"'{key}' must be [x, y, z].");
    }

    /// <summary>A displacement in meters → feet.</summary>
    public static XYZ Displacement(JToken t, string key = "vector")
    {
      var v = Vector(t, key);
      return new XYZ(U.ToFeet(v.X), U.ToFeet(v.Y), U.ToFeet(v.Z));
    }

    public static List<XYZ> Points(JToken t, string key = "points")
    {
      if (!(t is JArray arr) || arr.Count == 0) throw RpcException.InvalidParams($"'{key}' must be a non-empty list of points.");
      return arr.Select((x, i) => Point(x, $"{key}[{i}]")).ToList();
    }

    public static List<string> Strings(JToken t)
    {
      if (t == null) return new List<string>();
      if (t is JArray arr) return arr.Select(x => x.ToString()).ToList();
      return new List<string> { t.ToString() };
    }

    public static Color Colour(JToken t)
    {
      if (t == null || t.Type == JTokenType.Null) return null;
      if (t is JArray a && a.Count >= 3) return new Color((byte)ToDouble(a[0]), (byte)ToDouble(a[1]), (byte)ToDouble(a[2]));
      var s = t.ToString().Trim();
      if (s.StartsWith("#") && s.Length == 7 && int.TryParse(s.Substring(1), NumberStyles.HexNumber, null, out var rgb))
        return new Color((byte)((rgb >> 16) & 255), (byte)((rgb >> 8) & 255), (byte)(rgb & 255));
      var named = System.Drawing.Color.FromName(s);
      if (named.IsKnownColor) return new Color(named.R, named.G, named.B);
      throw RpcException.InvalidParams("Invalid colour '" + s + "'. Use #RRGGBB or [r, g, b].");
    }

    public static string Hex(Color c) => c == null || !c.IsValid ? null : $"#{c.Red:X2}{c.Green:X2}{c.Blue:X2}";
  }
}
