using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using Rhino;
using Rhino.DocObjects;
using McpBridge.Transport;

namespace RhinoMcpBridge.Core
{
  /// <summary>
  /// Object search shared by get/delete/transform/export:
  ///   ids, layer (+ include_sublayers), types, name (wildcards), user_text {key: value|"*"},
  ///   selected, include_hidden, include_locked.
  /// All given criteria must match (AND).
  /// </summary>
  public sealed class ObjectQuery
  {
    public List<Guid> Ids = new List<Guid>();
    public string Layer;
    public bool IncludeSublayers = true;
    public List<ObjectType> Types = new List<ObjectType>();
    public string Name;
    public Dictionary<string, string> UserText = new Dictionary<string, string>();
    /// <summary>Objects matching any of these user-text sets are left out (e.g. analysis meshes).</summary>
    public List<Dictionary<string, string>> ExcludeUserText = new List<Dictionary<string, string>>();
    public bool? Selected;
    public bool IncludeHidden = true;
    public bool IncludeLocked = true;

    public bool IsEmpty =>
      Ids.Count == 0 && Layer == null && Types.Count == 0 && Name == null && UserText.Count == 0 && Selected == null;

    public static ObjectQuery From(JObject p)
    {
      var q = new ObjectQuery();
      // Accept the filter either at the top level or nested under "filter".
      var f = p["filter"] as JObject ?? p;
      q.Ids = Args.Guids(Args.Get(f, "ids", "id", "guids"), "ids");
      q.Layer = Args.Str(f, "layer");
      q.IncludeSublayers = Args.Bool(f, "include_sublayers", true);
      foreach (var t in Args.Strings(Args.Get(f, "types", "type"))) q.Types.Add(RhinoUtil.ParseType(t));
      q.Name = Args.Str(f, "name");
      if (f["user_text"] is JObject ut)
        foreach (var kv in ut) q.UserText[kv.Key] = kv.Value?.Type == JTokenType.Null ? "*" : kv.Value?.ToString() ?? "*";
      else if (f["user_text_key"] != null)
        q.UserText[f.Value<string>("user_text_key")] = f.Value<string>("user_text_value") ?? "*";
      var exclude = f["exclude_user_text"];
      foreach (var set in exclude is JArray ex ? ex.OfType<JObject>() : exclude is JObject one ? new[] { one } : new JObject[0])
        q.ExcludeUserText.Add(set.Properties().ToDictionary(x => x.Name, x => x.Value.Type == JTokenType.Null ? "*" : x.Value.ToString()));
      if (Args.Has(f, "selected")) q.Selected = Args.Bool(f, "selected", false);
      q.IncludeHidden = Args.Bool(f, "include_hidden", true);
      q.IncludeLocked = Args.Bool(f, "include_locked", true);
      return q;
    }

    /// <summary>
    /// Target selection for methods whose top-level fields are data, not filters
    /// (update_object, set_object_data): only "id"/"ids" at the top level, or a nested "filter".
    /// </summary>
    public static ObjectQuery Target(JObject p)
    {
      var q = p["filter"] is JObject f ? From(new JObject { ["filter"] = f }) : new ObjectQuery();
      q.Ids.AddRange(Args.Guids(Args.Get(p, "ids", "id", "guids"), "ids"));
      if (q.IsEmpty) throw RpcException.InvalidParams("Say which objects: 'ids' or 'filter' (layer, types, name, user_text, selected).");
      return q;
    }

    public List<RhinoObject> Run(RhinoDoc doc)
    {
      if (Ids.Count > 0)
      {
        var found = new List<RhinoObject>();
        var missing = new List<string>();
        foreach (var id in Ids)
        {
          var obj = doc.Objects.FindId(id);
          if (obj == null || obj.IsDeleted) missing.Add(id.ToString());
          else found.Add(obj);
        }
        if (missing.Count > 0 && found.Count == 0)
          throw RpcException.NotFound("No object with these ids.", new JObject { ["missing"] = new JArray(missing) });
        return found.Where(Matches(doc)).ToList();
      }

      var settings = new ObjectEnumeratorSettings
      {
        NormalObjects = true,
        LockedObjects = IncludeLocked,
        HiddenObjects = IncludeHidden,
        DeletedObjects = false,
        IncludeLights = Types.Contains(ObjectType.Light),
        IncludeGrips = false,
        ReferenceObjects = true,
        IdefObjects = false,
      };
      if (Selected == true) settings.SelectedObjectsFilter = true;
      if (Types.Count > 0)
      {
        ObjectType mask = ObjectType.None;
        foreach (var t in Types) mask |= t;
        settings.ObjectTypeFilter = mask;
      }
      return doc.Objects.GetObjectList(settings).Where(Matches(doc)).ToList();
    }

    private Func<RhinoObject, bool> Matches(RhinoDoc doc)
    {
      HashSet<int> layers = null;
      if (Layer != null)
      {
        int idx = RhinoUtil.FindLayer(doc, Layer);
        if (idx < 0) throw RpcException.NotFound("Layer '" + Layer + "' does not exist.");
        layers = new HashSet<int> { idx };
        if (IncludeSublayers)
        {
          var root = doc.Layers[idx];
          foreach (var l in doc.Layers)
            if (!l.IsDeleted && l.FullPath.StartsWith(root.FullPath + "::", StringComparison.OrdinalIgnoreCase)) layers.Add(l.Index);
        }
      }
      var nameRx = Name != null ? RhinoUtil.Wildcard(Name) : null;
      var userRx = UserText.ToDictionary(kv => kv.Key, kv => kv.Value == "*" ? null : RhinoUtil.Wildcard(kv.Value));
      var excludeRx = ExcludeUserText.Select(set => set.ToDictionary(kv => kv.Key, kv => kv.Value == "*" ? null : RhinoUtil.Wildcard(kv.Value))).ToList();

      return obj =>
      {
        if (Types.Count > 0 && !Types.Contains(obj.ObjectType)) return false;
        if (layers != null && !layers.Contains(obj.Attributes.LayerIndex)) return false;
        if (nameRx != null && !nameRx.IsMatch(obj.Attributes.Name ?? "")) return false;
        if (Selected.HasValue && (obj.IsSelected(false) > 0) != Selected.Value) return false;
        foreach (var kv in userRx)
        {
          var value = obj.Attributes.GetUserString(kv.Key);
          if (value == null) return false;
          if (kv.Value != null && !kv.Value.IsMatch(value)) return false;
        }
        foreach (var set in excludeRx)
        {
          bool all = set.All(kv =>
          {
            var value = obj.Attributes.GetUserString(kv.Key);
            return value != null && (kv.Value == null || kv.Value.IsMatch(value));
          });
          if (all && set.Count > 0) return false;
        }
        return true;
      };
    }

    public static JObject Summary(RhinoDoc doc, RhinoObject obj, bool full, bool includeJson = false)
    {
      var o = new JObject
      {
        ["id"] = obj.Id.ToString(),
        ["type"] = RhinoUtil.TypeName(obj.ObjectType),
        ["name"] = obj.Attributes.Name ?? "",
        ["layer"] = RhinoUtil.LayerPath(doc, obj.Attributes.LayerIndex),
      };
      if (!full) return o;
      var attr = obj.Attributes;
      o["visible"] = attr.Visible && !obj.IsHidden;
      o["locked"] = obj.IsLocked;
      o["selected"] = obj.IsSelected(false) > 0;
      o["color"] = J.Hex(obj.Attributes.DrawColor(doc));
      o["color_source"] = attr.ColorSource.ToString();
      o["user_text"] = RhinoUtil.UserText(attr);
      var groups = attr.GetGroupList();
      if (groups != null && groups.Length > 0)
        o["groups"] = new JArray(groups.Select(g => doc.Groups.GroupName(g)));
      if (obj is InstanceObject io && io.InstanceDefinition != null)
        o["block"] = io.InstanceDefinition.Name;
      if (obj.Geometry != null)
      {
        foreach (var kv in GeometryInfo.Describe(obj.Geometry, true, includeJson))
          o[kv.Key] = kv.Value;
      }
      return o;
    }
  }
}
