using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using Autodesk.Revit.DB;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;
using RevitMcpBridge.App;

namespace RevitMcpBridge.Core
{
  /// <summary>
  /// Element search with the same filter vocabulary as the Rhino bridge, so the connectors'
  /// modules work on both: ids, layer, types (categories), name, user_text, exclude_user_text,
  /// selected — plus Revit-specific family, type_name and level.
  ///
  /// "Layers" in Revit: "Category::Walls", "LineStyle::Roads", or an mcp.layer user-text path
  /// written by the connector (e.g. "Vegetation::Trees").
  /// </summary>
  public sealed class ElementQuery
  {
    public List<string> Ids = new List<string>();
    public string Layer;
    public List<ElementId> Categories = new List<ElementId>();
    public string Name;
    public string Family;
    public string TypeName;
    public string Level;
    public Dictionary<string, string> UserText = new Dictionary<string, string>();
    public List<Dictionary<string, string>> ExcludeUserText = new List<Dictionary<string, string>>();
    public bool? Selected;

    public bool IsEmpty => Ids.Count == 0 && Layer == null && Categories.Count == 0 && Name == null && Family == null &&
                           TypeName == null && Level == null && UserText.Count == 0 && Selected == null;

    private static readonly Dictionary<string, string> Aliases = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
    {
      ["mass"] = "OST_Mass", ["masses"] = "OST_Mass", ["masse"] = "OST_Mass", ["volume"] = "OST_Mass",
      ["wall"] = "OST_Walls", ["walls"] = "OST_Walls", ["mur"] = "OST_Walls", ["murs"] = "OST_Walls",
      ["floor"] = "OST_Floors", ["floors"] = "OST_Floors", ["sol"] = "OST_Floors", ["sols"] = "OST_Floors", ["dalle"] = "OST_Floors",
      ["roof"] = "OST_Roofs", ["roofs"] = "OST_Roofs", ["toit"] = "OST_Roofs", ["toits"] = "OST_Roofs",
      ["generic"] = "OST_GenericModel", ["generic model"] = "OST_GenericModel", ["generic models"] = "OST_GenericModel",
      ["modèle générique"] = "OST_GenericModel", ["modèles génériques"] = "OST_GenericModel",
      ["planting"] = "OST_Planting", ["plantes"] = "OST_Planting", ["végétation"] = "OST_Planting", ["vegetation"] = "OST_Planting", ["trees"] = "OST_Planting",
      ["property line"] = "OST_SiteProperty", ["property lines"] = "OST_SiteProperty", ["lignes de propriété"] = "OST_SiteProperty", ["parcelles"] = "OST_SiteProperty",
      ["topography"] = "OST_Topography", ["topographie"] = "OST_Topography", ["toposolid"] = "OST_Toposolid", ["terrain"] = "OST_Toposolid",
      ["room"] = "OST_Rooms", ["rooms"] = "OST_Rooms", ["pièce"] = "OST_Rooms", ["pièces"] = "OST_Rooms",
      ["area"] = "OST_Areas", ["areas"] = "OST_Areas",
      ["column"] = "OST_Columns", ["columns"] = "OST_Columns", ["structural column"] = "OST_StructuralColumns", ["structural columns"] = "OST_StructuralColumns",
      ["beam"] = "OST_StructuralFraming", ["beams"] = "OST_StructuralFraming", ["structural framing"] = "OST_StructuralFraming",
      ["door"] = "OST_Doors", ["doors"] = "OST_Doors", ["window"] = "OST_Windows", ["windows"] = "OST_Windows",
      ["lines"] = "OST_Lines", ["model lines"] = "OST_Lines", ["curve"] = "OST_Lines", ["curves"] = "OST_Lines",
      ["site"] = "OST_Site", ["parking"] = "OST_Parking", ["roads"] = "OST_Roads", ["levels"] = "OST_Levels", ["grids"] = "OST_Grids",
      ["curtain panels"] = "OST_CurtainWallPanels", ["stairs"] = "OST_Stairs", ["furniture"] = "OST_Furniture",
      ["mesh"] = "OST_GenericModel", ["brep"] = "OST_GenericModel", ["extrusion"] = "OST_GenericModel",
    };

    /// <summary>Category from an English name, an OST_ name, a French name or the localized display name.</summary>
    public static ElementId ResolveCategory(Document doc, string name)
    {
      var key = name.Trim();
      if (key.StartsWith("Category::", StringComparison.OrdinalIgnoreCase)) key = key.Substring(10);
      string ost = Aliases.TryGetValue(key, out var a) ? a : key.StartsWith("OST_", StringComparison.OrdinalIgnoreCase) ? key : "OST_" + key.Replace(" ", "");
      if (Enum.TryParse(ost, true, out BuiltInCategory bic))
      {
        try
        {
          var cat = Category.GetCategory(doc, bic);
          if (cat != null) return cat.Id;
        }
        catch
        {
          // not a real category in this version
        }
      }
      foreach (Category c in doc.Settings.Categories)
        if (string.Equals(c.Name, key, StringComparison.OrdinalIgnoreCase)) return c.Id;
      throw RpcException.NotFound("Unknown category '" + name + "'. Examples: Mass, Walls, Floors, Roofs, Generic Models, Planting, Property Lines, Toposolid, Rooms, Lines.");
    }

    /// <summary>Category id, or null when it does not exist in this Revit version (e.g. Toposolid before 2024).</summary>
    public static ElementId ResolveCategorySafe(Document doc, string name)
    {
      try { return ResolveCategory(doc, name); } catch (RpcException) { return null; }
    }

    public static ElementQuery From(Document doc, JObject p)
    {
      var f = p["filter"] as JObject ?? p;
      var q = new ElementQuery();
      q.Ids = RArgs.Strings(RArgs.Get(f, "ids", "id"));
      q.Layer = RArgs.Str(f, "layer");
      foreach (var c in RArgs.Strings(RArgs.Get(f, "types", "categories", "category"))) q.Categories.Add(ResolveCategory(doc, c));
      q.Name = RArgs.Str(f, "name");
      q.Family = RArgs.Str(f, "family");
      q.TypeName = RArgs.Str(f, "type_name");
      q.Level = RArgs.Str(f, "level");
      if (f["user_text"] is JObject ut)
        foreach (var kv in ut) q.UserText[kv.Key] = kv.Value == null || kv.Value.Type == JTokenType.Null ? "*" : kv.Value.ToString();
      var exclude = f["exclude_user_text"];
      foreach (var set in exclude is JArray ex ? ex.OfType<JObject>() : exclude is JObject one ? new[] { one } : new JObject[0])
        q.ExcludeUserText.Add(set.Properties().ToDictionary(x => x.Name, x => x.Value.Type == JTokenType.Null ? "*" : x.Value.ToString()));
      if (RArgs.Has(f, "selected")) q.Selected = RArgs.Bool(f, "selected", false);
      return q;
    }

    /// <summary>Selection for methods whose top-level fields are data: only ids or a nested filter.</summary>
    public static ElementQuery Target(Document doc, JObject p)
    {
      var q = p["filter"] is JObject f ? From(doc, new JObject { ["filter"] = f }) : new ElementQuery();
      q.Ids.AddRange(RArgs.Strings(RArgs.Get(p, "ids", "id")));
      if (q.IsEmpty) throw RpcException.InvalidParams("Say which elements: 'ids' or 'filter' (categories, layer, name, user_text, level…).");
      return q;
    }

    public static Element Find(Document doc, string id)
    {
      if (long.TryParse(id, out var n))
      {
        var e = doc.GetElement(Core.Ids.From(n));
        if (e != null) return e;
      }
      return doc.GetElement(id); // UniqueId
    }

    public static Regex Wildcard(string pattern)
      => new Regex("^" + Regex.Escape(pattern).Replace("\\*", ".*").Replace("\\?", ".") + "$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    public List<Element> Run(Document doc)
    {
      IEnumerable<Element> source;
      if (Ids.Count > 0)
      {
        var found = Ids.Select(i => Find(doc, i)).Where(e => e != null).ToList();
        if (found.Count == 0) throw RpcException.NotFound("No element with these ids.", new JObject { ["ids"] = new JArray(Ids) });
        source = found;
      }
      else
      {
        var collector = new FilteredElementCollector(doc).WhereElementIsNotElementType();
        if (Categories.Count > 0) collector = collector.WherePasses(new ElementMulticategoryFilter(Categories));
        source = Categories.Count > 0
          ? collector.ToElements()
          : collector.ToElements().Where(e => e.Category != null && e.Category.CategoryType == CategoryType.Model && !(e is ElementType));
      }

      HashSet<long> selection = null;
      if (Selected.HasValue)
        selection = new HashSet<long>(RevitContext.UiDoc.Selection.GetElementIds().Select(Core.Ids.Of));
      var nameRx = Name != null ? Wildcard(Name) : null;
      var familyRx = Family != null ? Wildcard(Family) : null;
      var typeRx = TypeName != null ? Wildcard(TypeName) : null;
      var userRx = UserText.ToDictionary(kv => kv.Key, kv => kv.Value == "*" ? null : Wildcard(kv.Value));
      var excludeRx = ExcludeUserText.Select(set => set.ToDictionary(kv => kv.Key, kv => kv.Value == "*" ? null : Wildcard(kv.Value))).ToList();
      bool needUserText = userRx.Count > 0 || excludeRx.Count > 0 || (Layer != null && !IsCategoryLayer(Layer) && !IsLineStyleLayer(Layer));

      return source.Where(e =>
      {
        if (selection != null && selection.Contains(Core.Ids.Of(e.Id)) != Selected.Value) return false;
        if (nameRx != null && !nameRx.IsMatch(e.Name ?? "")) return false;
        if (familyRx != null && !familyRx.IsMatch(FamilyName(doc, e) ?? "")) return false;
        if (typeRx != null && !typeRx.IsMatch(TypeNameOf(doc, e) ?? "")) return false;
        if (Level != null && !string.Equals(LevelName(doc, e), Level, StringComparison.OrdinalIgnoreCase)) return false;
        JObject ut = needUserText ? UserData.Read(e) : null;
        if (Layer != null && !MatchesLayer(doc, e, Layer, ut)) return false;
        foreach (var kv in userRx)
        {
          var v = ut.Value<string>(kv.Key);
          if (v == null || (kv.Value != null && !kv.Value.IsMatch(v))) return false;
        }
        foreach (var set in excludeRx)
        {
          if (set.Count > 0 && set.All(kv => { var v = ut.Value<string>(kv.Key); return v != null && (kv.Value == null || kv.Value.IsMatch(v)); }))
            return false;
        }
        return true;
      }).ToList();
    }

    private static bool IsCategoryLayer(string layer) => layer.StartsWith("Category::", StringComparison.OrdinalIgnoreCase);
    private static bool IsLineStyleLayer(string layer) => layer.StartsWith("LineStyle::", StringComparison.OrdinalIgnoreCase);

    private static bool MatchesLayer(Document doc, Element e, string layer, JObject ut)
    {
      if (IsCategoryLayer(layer))
      {
        var cat = ResolveCategory(doc, layer);
        return e.Category != null && Core.Ids.Of(e.Category.Id) == Core.Ids.Of(cat);
      }
      if (IsLineStyleLayer(layer))
      {
        var style = layer.Substring("LineStyle::".Length);
        return e is CurveElement ce && ce.LineStyle != null && string.Equals(ce.LineStyle.Name, style, StringComparison.OrdinalIgnoreCase);
      }
      var path = ut?.Value<string>(UserData.LayerKey);
      if (path != null && (string.Equals(path, layer, StringComparison.OrdinalIgnoreCase) || path.StartsWith(layer + "::", StringComparison.OrdinalIgnoreCase)))
        return true;
      return e.Category != null && string.Equals(e.Category.Name, layer, StringComparison.OrdinalIgnoreCase);
    }

    public static string LayerOf(Element e, JObject ut)
    {
      var path = ut?.Value<string>(UserData.LayerKey);
      if (!string.IsNullOrEmpty(path)) return path;
      if (e is CurveElement ce && ce.LineStyle != null) return "LineStyle::" + ce.LineStyle.Name;
      return e.Category != null ? "Category::" + e.Category.Name : null;
    }

    public static string FamilyName(Document doc, Element e)
    {
      if (e is FamilyInstance fi) return fi.Symbol?.FamilyName;
      var type = doc.GetElement(e.GetTypeId()) as ElementType;
      return type?.FamilyName;
    }

    public static string TypeNameOf(Document doc, Element e) => (doc.GetElement(e.GetTypeId()) as ElementType)?.Name;

    public static string LevelName(Document doc, Element e)
    {
      var id = e.LevelId;
      if (id == null || id == ElementId.InvalidElementId)
      {
        var p = e.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM) ?? e.get_Parameter(BuiltInParameter.SCHEDULE_LEVEL_PARAM) ?? e.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT);
        id = p?.AsElementId();
      }
      return id == null || id == ElementId.InvalidElementId ? null : doc.GetElement(id)?.Name;
    }
  }
}
