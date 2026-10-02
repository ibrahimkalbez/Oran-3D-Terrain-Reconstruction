using System;
using System.Drawing;
using System.Linq;
using Newtonsoft.Json.Linq;
using Rhino;
using Rhino.Display;
using Rhino.Geometry;
using RhinoMcpBridge.Core;
using RhinoMcpBridge.Display;
using RhinoMcpBridge.Transport;

namespace RhinoMcpBridge.Handlers
{
  /// <summary>rhino.capture_viewport: camera, display mode, Grasshopper preview and image capture.</summary>
  public static class ViewHandlers
  {
    private static readonly PreviewConduit Conduit = new PreviewConduit();
    private static readonly PreviewConduit Highlight = new PreviewConduit { EdgeColor = Color.FromArgb(120, 0, 0) };

    public static void Register(RpcDispatcher d)
    {
      d.Register("rhino.capture_viewport", Capture);
      d.Register("rhino.list_display_modes", p => new JObject
      {
        ["display_modes"] = new JArray(DisplayModeDescription.GetDisplayModes().Select(m => m.EnglishName)),
      });
    }

    private static RhinoView FindView(RhinoDoc doc, string name)
    {
      if (string.IsNullOrWhiteSpace(name)) return doc.Views.ActiveView ?? doc.Views.GetViewList(ViewTypeFilter.Model).FirstOrDefault();
      var views = doc.Views.GetViewList(ViewTypeFilter.Model);
      var view = views.FirstOrDefault(v => string.Equals(v.ActiveViewport.Name, name, StringComparison.OrdinalIgnoreCase))
                 ?? views.FirstOrDefault(v => v.ActiveViewport.Name.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0);
      if (view == null)
        throw RpcException.NotFound("No view named '" + name + "'.", new JObject { ["views"] = new JArray(views.Select(v => v.ActiveViewport.Name)) });
      return view;
    }

    public static DisplayModeDescription FindDisplayMode(string name)
    {
      var modes = DisplayModeDescription.GetDisplayModes();
      var mode = modes.FirstOrDefault(m => string.Equals(m.EnglishName, name, StringComparison.OrdinalIgnoreCase))
                 ?? modes.FirstOrDefault(m => string.Equals(m.LocalName, name, StringComparison.OrdinalIgnoreCase))
                 ?? modes.FirstOrDefault(m => m.EnglishName.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0);
      if (mode == null)
        throw RpcException.NotFound("Unknown display mode '" + name + "'.", new JObject { ["display_modes"] = new JArray(modes.Select(m => m.EnglishName)) });
      return mode;
    }

    private static bool TryDirection(string name, out Vector3d dir, out Vector3d up, out bool parallel)
    {
      parallel = true;
      up = Vector3d.ZAxis;
      switch ((name ?? "").Trim().ToLowerInvariant())
      {
        case "top": case "plan": case "dessus": dir = -Vector3d.ZAxis; up = Vector3d.YAxis; return true;
        case "bottom": dir = Vector3d.ZAxis; up = Vector3d.YAxis; return true;
        case "front": case "south": case "sud": dir = Vector3d.YAxis; return true;
        case "back": case "north": case "nord": dir = -Vector3d.YAxis; return true;
        case "right": case "east": case "est": dir = -Vector3d.XAxis; return true;
        case "left": case "west": case "ouest": dir = Vector3d.XAxis; return true;
        case "iso_sw": case "sw": dir = new Vector3d(1, 1, -1); return true;
        case "iso_se": case "se": dir = new Vector3d(-1, 1, -1); return true;
        case "iso_ne": case "ne": dir = new Vector3d(-1, -1, -1); return true;
        case "iso_nw": case "nw": dir = new Vector3d(1, -1, -1); return true;
        case "perspective": case "persp": dir = new Vector3d(1, 1, -0.7); parallel = false; return true;
        case "aerial": case "bird": dir = new Vector3d(0.6, 1, -1.1); parallel = false; return true;
      }
      dir = Vector3d.Unset;
      return false;
    }

    private static JToken Capture(JObject p)
    {
      var doc = RhinoUtil.Doc();
      var view = FindView(doc, Args.Str(p, "view"));
      var vp = view.ActiveViewport;
      bool restore = Args.Bool(p, "restore", true);
      int width = Math.Max(64, Math.Min(4096, Args.Int(p, "width", 1280)));
      int height = Math.Max(64, Math.Min(4096, Args.Int(p, "height", 800)));

      var previewMode = (Args.Str(p, "preview", "auto") ?? "auto").ToLowerInvariant();
      IDisposable ghPreview = null;
      int previewCount = 0;
      Conduit.Clear();
      Highlight.Clear();
      try
      {
        if (previewMode != "none" && GrasshopperHandlers.IsLoaded())
          ghPreview = GhBridge.BeginPreview(p, Conduit, previewMode == "grasshopper", out previewCount);
        if (Args.Get(p, "highlight") is JObject highlight) AddHighlight(doc, highlight);
        Conduit.Enabled = Conduit.HasGeometry;
        Highlight.Enabled = Highlight.HasGeometry;

        var oldMode = vp.DisplayMode;
        if (restore) vp.PushViewProjection();
        try
        {
          var modeName = Args.Str(p, "display_mode");
          if (!string.IsNullOrEmpty(modeName)) vp.DisplayMode = FindDisplayMode(modeName);
          ApplyCamera(doc, vp, p);

          var capture = new ViewCapture
          {
            Width = width,
            Height = height,
            ScaleScreenItems = false,
            DrawAxes = Args.Bool(p, "draw_axes", false),
            DrawGrid = Args.Bool(p, "draw_grid", false),
            DrawGridAxes = Args.Bool(p, "draw_grid", false),
            TransparentBackground = Args.Bool(p, "transparent_background", false),
          };
          using (var bmp = capture.CaptureToBitmap(view))
          {
            if (bmp == null) throw RpcException.Failed("Rhino returned no image (is the Rhino window minimised?).");
            var result = ImageUtil.Encode(bmp, p, "viewport");
            result["view"] = vp.Name;
            result["display_mode"] = vp.DisplayMode?.EnglishName;
            result["projection"] = vp.IsParallelProjection ? "parallel" : "perspective";
            result["camera"] = new JObject { ["location"] = J.P(vp.CameraLocation), ["target"] = J.P(vp.CameraTarget) };
            result["grasshopper_preview_objects"] = previewCount;
            return result;
          }
        }
        finally
        {
          if (restore)
          {
            vp.PopViewProjection();
            if (vp.DisplayMode?.Id != oldMode?.Id) vp.DisplayMode = oldMode;
          }
          view.Redraw();
        }
      }
      finally
      {
        Conduit.Enabled = false;
        Conduit.Clear();
        Highlight.Enabled = false;
        Highlight.Clear();
        ghPreview?.Dispose();
        doc.Views.Redraw();
      }
    }

    private static void AddHighlight(RhinoDoc doc, JObject highlight)
    {
      var objects = ObjectQuery.From(highlight).Run(doc);
      Highlight.SetColor(Args.Colour(highlight["color"]) ?? Color.FromArgb(227, 6, 19));
      Highlight.Add(objects.Select(o => o.Geometry));
    }

    private static void ApplyCamera(RhinoDoc doc, RhinoViewport vp, JObject p)
    {
      var direction = Args.Str(p, "direction");
      var projection = Args.Str(p, "projection");
      bool moved = false;

      if (!string.IsNullOrEmpty(direction))
      {
        if (!TryDirection(direction, out var dir, out var up, out var parallel))
          throw RpcException.InvalidParams("Unknown direction '" + direction + "'. Use top, front, back, left, right, iso_sw, iso_se, iso_ne, iso_nw, perspective or aerial.");
        if (projection != null) parallel = projection.Equals("parallel", StringComparison.OrdinalIgnoreCase);
        if (parallel) vp.ChangeToParallelProjection(true);
        else vp.ChangeToPerspectiveProjection(true, Args.Num(p, "lens", 35));
        vp.SetCameraDirection(dir, true);
        vp.CameraUp = up;
        moved = true;
      }
      else if (projection != null)
      {
        if (projection.Equals("parallel", StringComparison.OrdinalIgnoreCase)) vp.ChangeToParallelProjection(true);
        else vp.ChangeToPerspectiveProjection(true, Args.Num(p, "lens", 35));
      }

      if (p["camera"] is JObject cam)
      {
        var target = Args.Point(cam["target"] ?? throw RpcException.InvalidParams("camera.target is required."), "camera.target");
        var location = Args.Point(cam["location"] ?? throw RpcException.InvalidParams("camera.location is required."), "camera.location");
        if (vp.IsParallelProjection && projection == null) vp.ChangeToPerspectiveProjection(true, Args.Num(cam, "lens", 35));
        vp.SetCameraLocations(target, location);
        if (cam["lens"] != null) vp.Camera35mmLensLength = Args.Num(cam, "lens", 35);
        return; // an explicit camera is not re-framed
      }

      var zoom = p["zoom"];
      string zoomMode = zoom == null ? (moved ? "extents" : "none") : (zoom.Type == JTokenType.String ? ((string)zoom).ToLowerInvariant() : "objects");
      switch (zoomMode)
      {
        case "none":
          break;
        case "extents":
          vp.ZoomExtents();
          break;
        case "selected":
          vp.ZoomExtentsSelected();
          break;
        case "preview":
          {
            var box = Conduit.Box;
            box.Union(Highlight.Box);
            if (box.IsValid) vp.ZoomBoundingBox(Pad(box));
          }
          break;
        case "objects":
          {
            var filter = zoom as JObject ?? new JObject { ["ids"] = zoom };
            var box = BoundingBox.Empty;
            foreach (var o in ObjectQuery.From(filter).Run(doc)) box.Union(o.Geometry.GetBoundingBox(true));
            if (!box.IsValid) throw RpcException.NotFound("Nothing to zoom to.");
            vp.ZoomBoundingBox(Pad(box));
            break;
          }
        default:
          throw RpcException.InvalidParams("'zoom' must be none, extents, selected, preview, or an id list / filter.");
      }
    }

    private static BoundingBox Pad(BoundingBox b)
    {
      var d = b.Diagonal.Length * 0.05;
      b.Inflate(d);
      return b;
    }
  }
}
