using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.ExtensibleStorage;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;
using RevitMcpBridge.App;
using RevitMcpBridge.Core;

namespace RevitMcpBridge.Handlers
{
  /// <summary>
  /// revit.get_document, revit.get_types, revit.save_document, revit.open_document.
  /// get_document mirrors rhino.get_document (layers = categories, line styles and mcp.layer
  /// paths) so the connectors' site detection works on both hosts.
  /// </summary>
  public static class DocumentHandlers
  {
    public static void Register(RpcDispatcher d)
    {
      d.Register("revit.get_document", GetDocument);
      d.Register("revit.get_types", GetTypes);
      d.Register("revit.save_document", SaveDocument);
      d.Register("revit.open_document", OpenDocument);
    }

    // ------------------------------------------------------------------ revit.get_document

    private static JToken GetDocument(JObject p)
    {
      var doc = RevitContext.Doc;
      var uidoc = RevitContext.UiDoc;
      int maxLayers = RArgs.Int(p, "max_layers", 500);

      var elements = new FilteredElementCollector(doc).WhereElementIsNotElementType().ToElements()
        .Where(e => e.Category != null && e.Category.CategoryType == CategoryType.Model && !(e is ElementType))
        .ToList();

      var byCategory = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
      var categoryColour = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
      var lineStyles = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
      double minX = double.MaxValue, minY = double.MaxValue, minZ = double.MaxValue, maxX = double.MinValue, maxY = double.MinValue, maxZ = double.MinValue;
      foreach (var e in elements)
      {
        var name = e.Category.Name;
        byCategory[name] = byCategory.TryGetValue(name, out var n) ? n + 1 : 1;
        if (!categoryColour.ContainsKey(name)) categoryColour[name] = RArgs.Hex(e.Category.LineColor);
        if (e is CurveElement ce && ce.LineStyle != null)
          lineStyles[ce.LineStyle.Name] = lineStyles.TryGetValue(ce.LineStyle.Name, out var m) ? m + 1 : 1;
        if (e.Category.Id.Equals(new ElementId(BuiltInCategory.OST_Cameras))) continue;
        var bb = e.get_BoundingBox(null);
        if (bb == null) continue;
        minX = Math.Min(minX, bb.Min.X); minY = Math.Min(minY, bb.Min.Y); minZ = Math.Min(minZ, bb.Min.Z);
        maxX = Math.Max(maxX, bb.Max.X); maxY = Math.Max(maxY, bb.Max.Y); maxZ = Math.Max(maxZ, bb.Max.Z);
      }

      // Pseudo-layers: mcp.layer paths written by the connector (read through an ES filter).
      var userLayers = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
      var schema = Schema.ListSchemas().FirstOrDefault(s => s.SchemaName == "ClaudeMcpUserText");
      if (schema != null)
      {
        foreach (var e in new FilteredElementCollector(doc).WherePasses(new ExtensibleStorageFilter(schema.GUID)))
        {
          var path = UserData.Get(e, UserData.LayerKey);
          if (string.IsNullOrEmpty(path)) continue;
          var parts = path.Split(new[] { "::" }, StringSplitOptions.None);
          for (int i = 1; i <= parts.Length; i++)
          {
            var prefix = string.Join("::", parts.Take(i));
            userLayers[prefix] = userLayers.TryGetValue(prefix, out var k) ? k + 1 : 1;
          }
        }
      }

      var layers = new JArray();
      foreach (var kv in userLayers.OrderBy(k => k.Key, StringComparer.OrdinalIgnoreCase))
        layers.Add(new JObject { ["path"] = kv.Key, ["kind"] = "mcp", ["object_count"] = kv.Value });
      foreach (var kv in byCategory.OrderBy(k => k.Key, StringComparer.OrdinalIgnoreCase))
        layers.Add(new JObject { ["path"] = "Category::" + kv.Key, ["kind"] = "category", ["color"] = categoryColour[kv.Key], ["object_count"] = kv.Value });
      foreach (var kv in lineStyles.OrderBy(k => k.Key, StringComparer.OrdinalIgnoreCase))
        layers.Add(new JObject { ["path"] = "LineStyle::" + kv.Key, ["kind"] = "line_style", ["object_count"] = kv.Value });
      int layerCount = layers.Count;
      while (layers.Count > maxLayers) layers.RemoveAt(layers.Count - 1);

      var levels = new JArray(new FilteredElementCollector(doc).OfClass(typeof(Level)).Cast<Level>().OrderBy(l => l.Elevation)
        .Select(l => (JToken)new JObject { ["id"] = Ids.Str(l.Id), ["name"] = l.Name, ["elevation"] = U.R(U.ToMeters(l.Elevation)) }));

      var activeView = uidoc.ActiveView;
      var views = new JArray(new FilteredElementCollector(doc).OfClass(typeof(View)).Cast<View>()
        .Where(v => !v.IsTemplate && (v.ViewType == ViewType.ThreeD || v.ViewType == ViewType.FloorPlan || v.ViewType == ViewType.Elevation || v.ViewType == ViewType.Section || v.ViewType == ViewType.CeilingPlan || v.ViewType == ViewType.AreaPlan))
        .OrderBy(v => v.ViewType.ToString()).ThenBy(v => v.Name).Take(300)
        .Select(v => (JToken)new JObject
        {
          ["id"] = Ids.Str(v.Id),
          ["name"] = v.Name,
          ["view_type"] = v.ViewType.ToString(),
          ["active"] = activeView != null && v.Id.Equals(activeView.Id),
        }));

      var selection = uidoc.Selection.GetElementIds();
      var selected = new JArray(selection.Take(100).Select(id => doc.GetElement(id)).Where(e => e != null).Select(e => (JToken)Describe.Element(doc, e, false)));

      var links = new JArray(new FilteredElementCollector(doc).OfClass(typeof(RevitLinkInstance)).Cast<RevitLinkInstance>()
        .Select(l => (JToken)new JObject { ["id"] = Ids.Str(l.Id), ["name"] = l.Name, ["loaded"] = l.GetLinkDocument() != null }));

      JArray globals = new JArray();
      if (GlobalParametersManager.AreGlobalParametersAllowed(doc))
        foreach (var id in GlobalParametersManager.GetGlobalParametersOrdered(doc))
          if (doc.GetElement(id) is GlobalParameter gp) globals.Add(gp.Name);

      var info = doc.ProjectInformation;
      var projectInfo = info == null ? null : new JObject
      {
        ["name"] = info.Name,
        ["number"] = info.Number,
        ["address"] = info.Address,
        ["client"] = info.ClientName,
        ["status"] = info.Status,
      };

      return new JObject
      {
        ["host"] = "revit",
        ["file"] = new JObject
        {
          ["name"] = doc.Title,
          ["path"] = doc.PathName,
          ["modified"] = doc.IsModified,
          ["workshared"] = doc.IsWorkshared,
        },
        ["units"] = "meters",
        ["units_note"] = "The bridge always speaks SI: meters, m², m³, degrees (Revit stores feet internally).",
        ["project_information"] = projectInfo,
        ["object_count"] = elements.Count,
        ["objects_by_type"] = JObject.FromObject(byCategory),
        ["bbox"] = minX == double.MaxValue ? null : Describe.BBox(new BoundingBoxXYZ { Min = new XYZ(minX, minY, minZ), Max = new XYZ(maxX, maxY, maxZ) }),
        ["layer_count"] = layerCount,
        ["layers"] = layers,
        ["levels"] = levels,
        ["views"] = views,
        ["active_view"] = activeView == null ? null : new JObject { ["id"] = Ids.Str(activeView.Id), ["name"] = activeView.Name, ["view_type"] = activeView.ViewType.ToString() },
        ["selected_count"] = selection.Count,
        ["selected"] = selected,
        ["links"] = links,
        ["global_parameters"] = globals,
        ["document_user_text"] = info != null ? UserData.Read(info) : new JObject(),
        ["earth_anchor"] = Location(doc),
        ["dynamo"] = DynamoHandlers.Availability(),
      };
    }

    /// <summary>Site location, true north and base points — what solar studies need.</summary>
    public static JToken Location(Document doc)
    {
      try
      {
        var site = doc.SiteLocation;
        var position = doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero);
        var o = new JObject
        {
          ["latitude"] = U.R(site.Latitude * 180 / Math.PI, 6),
          ["longitude"] = U.R(site.Longitude * 180 / Math.PI, 6),
          ["elevation"] = U.R(U.ToMeters(site.Elevation), 3),
          ["place"] = site.PlaceName,
          ["time_zone_hours"] = site.TimeZone,
          ["true_north_deg"] = U.R(position.Angle * 180 / Math.PI, 4),
          ["shared_origin"] = new JObject
          {
            ["east_west"] = U.R(U.ToMeters(position.EastWest), 3),
            ["north_south"] = U.R(U.ToMeters(position.NorthSouth), 3),
            ["elevation"] = U.R(U.ToMeters(position.Elevation), 3),
          },
          ["note"] = "Coordinates exchanged with the bridge are internal (project) coordinates in meters; analysis directions are geographic (x = east, y = true north) and rotated by the bridge.",
        };
        var pbp = BasePoint.GetProjectBasePoint(doc);
        var sp = BasePoint.GetSurveyPoint(doc);
        if (pbp != null) o["project_base_point"] = Describe.P(pbp.Position);
        if (sp != null) o["survey_point"] = Describe.P(sp.Position);
        return o;
      }
      catch (Exception ex)
      {
        return new JObject { ["error"] = ex.Message };
      }
    }

    /// <summary>Internal → geographic (shared) directions: x = east, y = true north.</summary>
    public static Transform SharedTransform(Document doc)
    {
      try
      {
        return doc.ActiveProjectLocation.GetTotalTransform();
      }
      catch
      {
        return Transform.Identity;
      }
    }

    // ------------------------------------------------------------------ revit.get_types

    /// <summary>Loadable family symbols and system types (walls, floors, roofs, levels…) by category.</summary>
    private static JToken GetTypes(JObject p)
    {
      var doc = RevitContext.Doc;
      var collector = new FilteredElementCollector(doc).WhereElementIsElementType();
      var cats = RArgs.Strings(RArgs.Get(p, "categories", "category", "types"));
      if (cats.Count > 0)
        collector = collector.WherePasses(new ElementMulticategoryFilter(cats.Select(c => ElementQuery.ResolveCategory(doc, c)).ToList()));
      var nameRx = RArgs.Str(p, "name") is string n ? ElementQuery.Wildcard(n) : null;
      int limit = Math.Max(1, Math.Min(5000, RArgs.Int(p, "limit", 500)));
      var types = collector.Cast<ElementType>()
        .Where(t => t.Category != null && (nameRx == null || nameRx.IsMatch(t.Name) || nameRx.IsMatch(t.FamilyName ?? "")))
        .OrderBy(t => t.Category.Name).ThenBy(t => t.FamilyName).ThenBy(t => t.Name)
        .ToList();
      return new JObject
      {
        ["total"] = types.Count,
        ["types"] = new JArray(types.Take(limit).Select(t => (JToken)new JObject
        {
          ["id"] = Ids.Str(t.Id),
          ["category"] = t.Category.Name,
          ["family"] = t.FamilyName,
          ["name"] = t.Name,
          ["kind"] = t is FamilySymbol ? "family_symbol" : t.GetType().Name,
        })),
      };
    }

    // ------------------------------------------------------------------ save / open

    private static JToken SaveDocument(JObject p)
    {
      var doc = RevitContext.Doc;
      var path = RArgs.Str(p, "path");
      bool copy = RArgs.Bool(p, "copy", false);
      if (string.IsNullOrEmpty(path))
      {
        if (string.IsNullOrEmpty(doc.PathName)) throw RpcException.InvalidParams("The project was never saved: give a 'path' (.rvt).");
        if (doc.IsWorkshared && RArgs.Bool(p, "synchronize", false))
        {
          doc.SynchronizeWithCentral(new TransactWithCentralOptions(), new SynchronizeWithCentralOptions { Comment = RArgs.Str(p, "comment", "Claude") });
          return new JObject { ["path"] = doc.PathName, ["synchronized"] = true };
        }
        doc.Save();
        return new JObject { ["path"] = doc.PathName, ["copy"] = false, ["bytes"] = File.Exists(doc.PathName) ? new FileInfo(doc.PathName).Length : 0 };
      }
      path = Path.GetFullPath(Environment.ExpandEnvironmentVariables(path));
      if (!path.EndsWith(".rvt", StringComparison.OrdinalIgnoreCase)) path += ".rvt";
      var dir = Path.GetDirectoryName(path);
      if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
      var options = new SaveAsOptions { OverwriteExistingFile = RArgs.Bool(p, "overwrite", true) };
      if (doc.IsWorkshared) options.SetWorksharingOptions(new WorksharingSaveAsOptions { SaveAsCentral = false });
      if (copy)
      {
        // SaveAs would re-target the open document to the new file, so a copy is the saved model copied on disk.
        if (string.IsNullOrEmpty(doc.PathName) || doc.IsWorkshared)
          throw RpcException.InvalidParams("Copies need a saved, non-workshared project: save it once (or use copy=false).");
        doc.Save();
        File.Copy(doc.PathName, path, true);
      }
      else
      {
        doc.SaveAs(path, options);
      }
      return new JObject { ["path"] = path, ["copy"] = copy, ["bytes"] = File.Exists(path) ? new FileInfo(path).Length : 0 };
    }

    private static JToken OpenDocument(JObject p)
    {
      var app = RevitContext.App;
      var path = Path.GetFullPath(Environment.ExpandEnvironmentVariables(RArgs.Str(p, "path", required: true)));
      if (!File.Exists(path)) throw RpcException.NotFound("File not found: " + path);
      var opened = app.OpenAndActivateDocument(path);
      var doc = opened.Document;
      return new JObject
      {
        ["path"] = doc.PathName,
        ["name"] = doc.Title,
        ["is_family"] = doc.IsFamilyDocument,
        ["units"] = "meters",
      };
    }
  }
}
