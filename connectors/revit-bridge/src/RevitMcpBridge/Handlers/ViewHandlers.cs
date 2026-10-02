using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Autodesk.Revit.DB;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;
using RevitMcpBridge.App;
using RevitMcpBridge.Core;

namespace RevitMcpBridge.Handlers
{
  /// <summary>
  /// revit.capture_viewport — same parameters as rhino.capture_viewport. An existing view is
  /// exported as is; a direction (top, iso_sw, aerial…) uses a temporary 3D view inside a
  /// transaction group that is rolled back, so the project is left untouched.
  /// </summary>
  public static class ViewHandlers
  {
    public static void Register(RpcDispatcher d)
    {
      d.Register("revit.capture_viewport", Capture);
    }

    private static JToken Capture(JObject p)
    {
      var doc = RevitContext.Doc;
      int width = Math.Max(200, Math.Min(4096, RArgs.Int(p, "width", 1280)));
      var viewName = RArgs.Str(p, "view");
      var direction = RArgs.Str(p, "direction");
      bool temporary = viewName == null && (direction != null || p["zoom"] != null || p["highlight"] != null || p["display_mode"] != null);

      var dir = Path.Combine(Path.GetTempPath(), "RevitMcpBridge", "capture-" + Guid.NewGuid().ToString("N"));
      Directory.CreateDirectory(dir);
      try
      {
        View view;
        string label;
        if (!temporary)
        {
          view = viewName != null ? FindView(doc, viewName) : RevitContext.UiDoc.ActiveView;
          if (view == null || !view.CanBePrinted) throw RpcException.InvalidParams("This view cannot be exported as an image.");
          label = view.Name;
          Export(doc, view, dir, width, p);
        }
        else
        {
          using (var group = new TransactionGroup(doc, "Claude: capture"))
          {
            group.Start();
            try
            {
              View3D v3;
              using (var t = new Transaction(doc, "Claude: temporary view"))
              {
                t.Start();
                v3 = Temporary3D(doc, p, direction ?? "iso_sw");
                t.Commit();
              }
              label = "Claude capture (" + (direction ?? "iso_sw") + ")";
              Export(doc, v3, dir, width, p);
            }
            finally
            {
              group.RollBack();
            }
          }
        }
        var file = Directory.GetFiles(dir).OrderByDescending(File.GetLastWriteTimeUtc).FirstOrDefault()
                   ?? throw RpcException.Failed("Revit did not write the image.");
        var bytes = File.ReadAllBytes(file);
        var mime = file.EndsWith(".jpg", StringComparison.OrdinalIgnoreCase) ? "image/jpeg" : "image/png";
        var (w, h) = PngSize(bytes);
        var result = new JObject
        {
          ["view"] = label,
          ["label"] = label,
          ["width"] = w > 0 ? w : width,
          ["height"] = h,
          ["display_mode"] = RArgs.Str(p, "display_mode", "Shaded"),
          ["mime_type"] = mime,
          ["bytes"] = bytes.Length,
        };
        var savePath = RArgs.Str(p, "save_path");
        if (!string.IsNullOrEmpty(savePath))
        {
          savePath = Path.GetFullPath(Environment.ExpandEnvironmentVariables(savePath));
          var sd = Path.GetDirectoryName(savePath);
          if (!string.IsNullOrEmpty(sd)) Directory.CreateDirectory(sd);
          File.WriteAllBytes(savePath, bytes);
          result["saved_path"] = savePath;
        }
        if (RArgs.Bool(p, "return_image", true)) result["image_base64"] = Convert.ToBase64String(bytes);
        return result;
      }
      finally
      {
        try { Directory.Delete(dir, true); } catch { /* temp folder */ }
      }
    }

    private static View FindView(Document doc, string name)
    {
      var views = new FilteredElementCollector(doc).OfClass(typeof(View)).Cast<View>().Where(v => !v.IsTemplate).ToList();
      return views.FirstOrDefault(v => string.Equals(v.Name, name, StringComparison.OrdinalIgnoreCase) || Ids.Str(v.Id) == name)
             ?? throw RpcException.NotFound("No view '" + name + "'.", new JObject { ["views"] = new JArray(views.Where(v => v.CanBePrinted).Select(v => v.Name).Take(100)) });
    }

    private static void Export(Document doc, View view, string dir, int width, JObject p)
    {
      bool jpeg = (RArgs.Str(p, "format", "png") ?? "png").StartsWith("jp", StringComparison.OrdinalIgnoreCase);
      var options = new ImageExportOptions
      {
        ExportRange = ExportRange.SetOfViews,
        FilePath = Path.Combine(dir, "capture"),
        ZoomType = ZoomFitType.FitToPage,
        PixelSize = width,
        ImageResolution = ImageResolution.DPI_150,
        FitDirection = FitDirectionType.Horizontal,
        HLRandWFViewsFileType = jpeg ? ImageFileType.JPEGLossless : ImageFileType.PNG,
        ShadowViewsFileType = jpeg ? ImageFileType.JPEGLossless : ImageFileType.PNG,
      };
      options.SetViewsAndSheets(new List<ElementId> { view.Id });
      doc.ExportImage(options);
    }

    private static DisplayStyle Style(string mode)
    {
      switch ((mode ?? "shaded").Trim().ToLowerInvariant())
      {
        case "wireframe": return DisplayStyle.Wireframe;
        case "hidden": case "hiddenline": case "hidden line": return DisplayStyle.HLR;
        case "realistic": case "rendered": return DisplayStyle.Realistic;
        case "consistent": case "flat": case "consistent colors": return DisplayStyle.FlatColors;
        case "shaded_edges": case "shaded with edges": return DisplayStyle.ShadingWithEdges;
        default: return DisplayStyle.ShadingWithEdges;
      }
    }

    /// <summary>A 3D view looking from a named direction, cropped by a section box when zooming on elements.</summary>
    private static View3D Temporary3D(Document doc, JObject p, string direction)
    {
      var vft = new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().FirstOrDefault(v => v.ViewFamily == ViewFamily.ThreeDimensional)
                ?? throw RpcException.Failed("No 3D view type in the project.");
      var view = View3D.CreateIsometric(doc, vft.Id);
      view.Name = "Claude capture " + Guid.NewGuid().ToString("N").Substring(0, 8);
      view.DisplayStyle = Style(RArgs.Str(p, "display_mode"));
      view.DetailLevel = ViewDetailLevel.Fine;

      XYZ forward;
      switch (direction.Trim().ToLowerInvariant())
      {
        case "top": case "plan": forward = new XYZ(0, 0, -1); break;
        case "front": forward = new XYZ(0, 1, 0); break;
        case "back": forward = new XYZ(0, -1, 0); break;
        case "left": forward = new XYZ(1, 0, 0); break;
        case "right": forward = new XYZ(-1, 0, 0); break;
        case "iso_se": forward = new XYZ(-1, 1, -1); break;
        case "iso_ne": forward = new XYZ(-1, -1, -1); break;
        case "iso_nw": forward = new XYZ(1, -1, -1); break;
        case "aerial": case "perspective": forward = new XYZ(1, 1, -0.7); break;
        case "iso_sw": case "iso": default: forward = new XYZ(1, 1, -1); break;
      }
      forward = forward.Normalize();
      var up = Math.Abs(forward.Z) > 0.999 ? XYZ.BasisY : XYZ.BasisZ.Subtract(forward.Multiply(forward.DotProduct(XYZ.BasisZ))).Normalize();

      // Zoom / highlight targets.
      BoundingBoxXYZ box = null;
      var zoom = p["zoom"];
      if (zoom is JObject || zoom is JArray)
      {
        var filter = zoom as JObject ?? new JObject { ["ids"] = zoom };
        box = Bounds(ElementQuery.From(doc, filter).Run(doc));
        if (box == null) throw RpcException.NotFound("Nothing to zoom to.");
      }
      if (p["highlight"] is JObject hl)
      {
        var targets = ElementQuery.From(doc, hl["ids"] is JArray ? new JObject { ["ids"] = hl["ids"] } : hl["filter"] as JObject ?? hl).Run(doc);
        Highlight(doc, view, targets.Select(e => e.Id).ToList(), RArgs.Colour(hl["color"]) ?? new Color(227, 6, 19));
      }
      if (box != null)
      {
        double pad = Math.Max(box.Max.X - box.Min.X, box.Max.Y - box.Min.Y) * 0.05 + U.ToFeet(1);
        view.SetSectionBox(new BoundingBoxXYZ { Min = box.Min - new XYZ(pad, pad, U.ToFeet(0.5)), Max = box.Max + new XYZ(pad, pad, pad) });
        var sectionBoxCat = Category.GetCategory(doc, BuiltInCategory.OST_SectionBox);
        if (sectionBoxCat != null && view.CanCategoryBeHidden(sectionBoxCat.Id)) view.SetCategoryHidden(sectionBoxCat.Id, true);
      }
      var eye = box != null ? (box.Min + box.Max) / 2 - forward * 1000 : XYZ.Zero - forward * 1000;
      view.SetOrientation(new ViewOrientation3D(eye, up, forward));
      return view;
    }

    private static void Highlight(Document doc, View view, List<ElementId> ids, Color colour)
    {
      if (ids.Count == 0) return;
      var solid = new FilteredElementCollector(doc).OfClass(typeof(FillPatternElement)).Cast<FillPatternElement>()
        .FirstOrDefault(f => f.GetFillPattern().IsSolidFill);
      var ogs = new OverrideGraphicSettings();
      ogs.SetProjectionLineColor(colour);
      if (solid != null)
      {
        ogs.SetSurfaceForegroundPatternId(solid.Id);
        ogs.SetSurfaceForegroundPatternColor(colour);
      }
      foreach (var id in ids) view.SetElementOverrides(id, ogs);
    }

    public static BoundingBoxXYZ Bounds(IEnumerable<Element> elements)
    {
      XYZ min = null, max = null;
      foreach (var e in elements)
      {
        var b = e.get_BoundingBox(null);
        if (b == null) continue;
        min = min == null ? b.Min : new XYZ(Math.Min(min.X, b.Min.X), Math.Min(min.Y, b.Min.Y), Math.Min(min.Z, b.Min.Z));
        max = max == null ? b.Max : new XYZ(Math.Max(max.X, b.Max.X), Math.Max(max.Y, b.Max.Y), Math.Max(max.Z, b.Max.Z));
      }
      return min == null ? null : new BoundingBoxXYZ { Min = min, Max = max };
    }

    /// <summary>Width and height from a PNG header (0, 0 for other formats).</summary>
    public static (int, int) PngSize(byte[] b)
    {
      if (b.Length < 24 || b[0] != 0x89 || b[1] != 0x50) return (0, 0);
      int w = (b[16] << 24) | (b[17] << 16) | (b[18] << 8) | b[19];
      int h = (b[20] << 24) | (b[21] << 16) | (b[22] << 8) | b[23];
      return (w, h);
    }
  }
}
