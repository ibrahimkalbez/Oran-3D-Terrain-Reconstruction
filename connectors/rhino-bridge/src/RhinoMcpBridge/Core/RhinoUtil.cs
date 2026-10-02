using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using Rhino;
using Rhino.DocObjects;
using McpBridge.Transport;

namespace RhinoMcpBridge.Core
{
  /// <summary>Document helpers shared by the handlers: active document, layers, attributes, type names.</summary>
  public static class RhinoUtil
  {
    public static RhinoDoc Doc()
    {
      var doc = RhinoDoc.ActiveDoc;
      if (doc == null)
        throw new RpcException(RpcErrorCodes.NoActiveDocument, "No active Rhino document. Open or create a model in Rhino.");
      return doc;
    }

    // ---------------------------------------------------------------- object types

    private static readonly Dictionary<ObjectType, string> TypeNames = new Dictionary<ObjectType, string>
    {
      [ObjectType.Point] = "point",
      [ObjectType.PointSet] = "point_cloud",
      [ObjectType.Curve] = "curve",
      [ObjectType.Surface] = "surface",
      [ObjectType.Brep] = "brep",
      [ObjectType.Extrusion] = "extrusion",
      [ObjectType.Mesh] = "mesh",
      [ObjectType.SubD] = "subd",
      [ObjectType.Annotation] = "annotation",
      [ObjectType.TextDot] = "text_dot",
      [ObjectType.InstanceReference] = "block_instance",
      [ObjectType.Light] = "light",
      [ObjectType.Hatch] = "hatch",
      [ObjectType.ClipPlane] = "clipping_plane",
      [ObjectType.Detail] = "detail",
    };

    public static string TypeName(ObjectType t) => TypeNames.TryGetValue(t, out var s) ? s : t.ToString().ToLowerInvariant();

    public static ObjectType ParseType(string name)
    {
      var key = name.Trim().ToLowerInvariant().Replace(" ", "_");
      switch (key)
      {
        case "points": key = "point"; break;
        case "curves": case "line": case "polyline": case "polycurve": case "nurbs_curve": key = "curve"; break;
        case "surfaces": key = "surface"; break;
        case "breps": case "polysurface": case "polysurfaces": case "solid": key = "brep"; break;
        case "extrusions": key = "extrusion"; break;
        case "meshes": key = "mesh"; break;
        case "block": case "blocks": case "instance": key = "block_instance"; break;
        case "text": case "dimension": key = "annotation"; break;
        case "textdot": case "dot": key = "text_dot"; break;
      }
      foreach (var kv in TypeNames)
        if (kv.Value == key) return kv.Key;
      throw RpcException.InvalidParams("Unknown object type '" + name + "'. Known: " + string.Join(", ", TypeNames.Values));
    }

    // ---------------------------------------------------------------- layers

    public static string LayerPath(RhinoDoc doc, int layerIndex)
    {
      if (layerIndex < 0 || layerIndex >= doc.Layers.Count) return null;
      return doc.Layers[layerIndex].FullPath;
    }

    public static int FindLayer(RhinoDoc doc, string path)
    {
      if (string.IsNullOrWhiteSpace(path)) return -1;
      var normalized = NormalizeLayerPath(path);
      int idx = doc.Layers.FindByFullPath(normalized, -1);
      if (idx >= 0 && !doc.Layers[idx].IsDeleted) return idx;
      // Fall back to a unique short name ("Buildings" for "Model::Buildings").
      var matches = doc.Layers.Where(l => !l.IsDeleted && string.Equals(l.Name, normalized, StringComparison.OrdinalIgnoreCase)).ToList();
      return matches.Count == 1 ? matches[0].Index : -1;
    }

    public static string NormalizeLayerPath(string path)
    {
      var parts = path.Replace("/", "::").Replace("\\", "::").Split(new[] { "::" }, StringSplitOptions.RemoveEmptyEntries)
        .Select(s => s.Trim()).Where(s => s.Length > 0);
      return string.Join("::", parts);
    }

    /// <summary>Returns the index of the layer at <paramref name="path"/>, creating missing parents.</summary>
    public static int EnsureLayer(RhinoDoc doc, string path, Color? colour = null)
    {
      var normalized = NormalizeLayerPath(path);
      if (normalized.Length == 0) throw RpcException.InvalidParams("Layer path is empty.");
      int existing = doc.Layers.FindByFullPath(normalized, -1);
      if (existing >= 0 && !doc.Layers[existing].IsDeleted)
      {
        if (colour.HasValue)
        {
          var layer = doc.Layers[existing];
          layer.Color = colour.Value;
        }
        return existing;
      }

      var names = normalized.Split(new[] { "::" }, StringSplitOptions.None);
      Guid parent = Guid.Empty;
      int index = -1;
      for (int i = 0; i < names.Length; i++)
      {
        string partial = string.Join("::", names.Take(i + 1));
        index = doc.Layers.FindByFullPath(partial, -1);
        if (index < 0 || doc.Layers[index].IsDeleted)
        {
          if (!ModelComponent.IsValidComponentName(names[i]))
            throw RpcException.InvalidParams("Invalid layer name '" + names[i] + "'.");
          var layer = new Layer { Name = names[i], ParentLayerId = parent };
          if (i == names.Length - 1 && colour.HasValue) layer.Color = colour.Value;
          index = doc.Layers.Add(layer);
          if (index < 0) throw RpcException.Failed("Rhino refused to create layer '" + partial + "'.");
        }
        parent = doc.Layers[index].Id;
      }
      return index;
    }

    // ---------------------------------------------------------------- attributes

    /// <summary>
    /// Builds object attributes from a spec: layer, name, color, user_text, group, visible.
    /// Unknown keys are ignored so the same spec can carry geometry fields.
    /// </summary>
    public static ObjectAttributes Attributes(RhinoDoc doc, JObject spec, ObjectAttributes baseAttributes = null)
    {
      var attr = baseAttributes?.Duplicate() ?? doc.CreateDefaultAttributes();
      var layer = Args.Str(spec, "layer");
      if (!string.IsNullOrWhiteSpace(layer)) attr.LayerIndex = EnsureLayer(doc, layer);
      var name = Args.Str(spec, "name");
      if (name != null) attr.Name = name;
      var colour = Args.Colour(Args.Get(spec, "color", "colour"));
      if (colour.HasValue)
      {
        attr.ObjectColor = colour.Value;
        attr.ColorSource = ObjectColorSource.ColorFromObject;
      }
      if (spec["user_text"] is JObject ut)
      {
        foreach (var kv in ut)
        {
          if (kv.Value == null || kv.Value.Type == JTokenType.Null) attr.DeleteUserString(kv.Key);
          else attr.SetUserString(kv.Key, kv.Value.Type == JTokenType.String ? (string)kv.Value : kv.Value.ToString(Newtonsoft.Json.Formatting.None));
        }
      }
      var group = Args.Str(spec, "group");
      if (!string.IsNullOrWhiteSpace(group))
      {
        int gi = doc.Groups.Find(group);
        if (gi < 0) gi = doc.Groups.Add(group);
        attr.AddToGroup(gi);
      }
      if (Args.Has(spec, "visible")) attr.Visible = Args.Bool(spec, "visible", true);
      return attr;
    }

    public static JObject UserText(ObjectAttributes attr)
    {
      var o = new JObject();
      var strings = attr.GetUserStrings();
      foreach (string key in strings.AllKeys) o[key] = strings[key];
      return o;
    }

    // ---------------------------------------------------------------- matching

    public static Regex Wildcard(string pattern)
    {
      var rx = "^" + Regex.Escape(pattern).Replace("\\*", ".*").Replace("\\?", ".") + "$";
      return new Regex(rx, RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    }

    public static string Units(UnitSystem u)
    {
      switch (u)
      {
        case UnitSystem.Millimeters: return "mm";
        case UnitSystem.Centimeters: return "cm";
        case UnitSystem.Meters: return "m";
        case UnitSystem.Kilometers: return "km";
        case UnitSystem.Inches: return "in";
        case UnitSystem.Feet: return "ft";
        default: return u.ToString();
      }
    }
  }
}
