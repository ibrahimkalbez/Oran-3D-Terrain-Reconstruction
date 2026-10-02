using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using Autodesk.Revit.DB;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;
using RevitMcpBridge.App;
using RevitMcpBridge.Core;

namespace RevitMcpBridge.Handlers
{
  /// <summary>
  /// revit.export — the selection (filter) to a file whose extension picks the format:
  ///   .stl / .obj  written by the bridge (triangulated, meters) — simulation meshes, 3D printing;
  ///   .sat         ACIS solids (ANSYS SpaceClaim / DesignModeler / Discovery import them);
  ///   .ifc .dwg .fbx  through Revit's exporters, limited to the selection by a temporary view.
  /// </summary>
  public static class ExportHandlers
  {
    public static void Register(RpcDispatcher d)
    {
      d.Register("revit.export", Export);
    }

    private static readonly BuiltInCategory[] DefaultCategories =
    {
      BuiltInCategory.OST_Mass, BuiltInCategory.OST_Walls, BuiltInCategory.OST_Floors, BuiltInCategory.OST_Roofs,
      BuiltInCategory.OST_GenericModel, BuiltInCategory.OST_Columns, BuiltInCategory.OST_StructuralColumns,
      BuiltInCategory.OST_StructuralFraming, BuiltInCategory.OST_CurtainWallPanels, BuiltInCategory.OST_CurtainWallMullions,
      BuiltInCategory.OST_Planting, BuiltInCategory.OST_Site, BuiltInCategory.OST_Topography, BuiltInCategory.OST_Stairs,
      BuiltInCategory.OST_Doors, BuiltInCategory.OST_Windows, BuiltInCategory.OST_Ceilings,
    };

    /// <summary>The filtered elements, or every building/site element when the filter is empty.</summary>
    public static List<Element> Selection(Document doc, JObject p)
    {
      var query = ElementQuery.From(doc, p);
      if (!query.IsEmpty) return query.Run(doc);
      var cats = DefaultCategories.Select(c => { try { return Category.GetCategory(doc, c)?.Id; } catch { return null; } }).Where(id => id != null).ToList();
      var toposolid = ElementQuery.ResolveCategorySafe(doc, "OST_Toposolid");
      if (toposolid != null) cats.Add(toposolid);
      return new FilteredElementCollector(doc).WhereElementIsNotElementType().WherePasses(new ElementMulticategoryFilter(cats)).ToElements().ToList();
    }

    private static JToken Export(JObject p)
    {
      var doc = RevitContext.Doc;
      var path = Path.GetFullPath(Environment.ExpandEnvironmentVariables(RArgs.Str(p, "path", required: true)));
      var dir = Path.GetDirectoryName(path);
      if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
      var elements = Selection(doc, p);
      if (elements.Count == 0) throw RpcException.NotFound("Nothing to export.");
      var ext = Path.GetExtension(path).ToLowerInvariant();
      var info = new JObject();
      switch (ext)
      {
        case ".stl":
          info = WriteStl(path, elements, RArgs.Bool(p, "ascii", false));
          break;
        case ".obj":
          info = WriteObj(path, elements);
          break;
        case ".sat":
        case ".dwg":
        case ".fbx":
        case ".ifc":
          WithSelectionView(doc, elements, ext, view =>
          {
            var name = Path.GetFileNameWithoutExtension(path);
            bool ok;
            switch (ext)
            {
              case ".sat":
                ok = doc.Export(dir, name, new List<ElementId> { view.Id }, new SATExportOptions());
                break;
              case ".dwg":
                ok = doc.Export(dir, name, new List<ElementId> { view.Id }, new DWGExportOptions { MergedViews = true });
                break;
              case ".fbx":
                var set = new ViewSet();
                set.Insert(view);
                ok = doc.Export(dir, name, set, new FBXExportOptions());
                break;
              default:
                ok = doc.Export(dir, name, new IFCExportOptions { FilterViewId = view.Id, ExportBaseQuantities = true });
                break;
            }
            if (!ok) throw RpcException.Failed("Revit's " + ext + " exporter reported a failure.");
          });
          break;
        default:
          throw RpcException.InvalidParams("Unsupported format '" + ext + "'. Use .stl, .obj, .sat, .ifc, .dwg or .fbx (STEP: export .sat and convert in SpaceClaim, or use .stl).");
      }
      if (!File.Exists(path)) throw RpcException.Failed("Export to '" + path + "' did not produce the file.");
      info["path"] = path;
      info["format"] = ext.TrimStart('.');
      info["object_count"] = elements.Count;
      info["bytes"] = new FileInfo(path).Length;
      info["units"] = ext == ".stl" || ext == ".obj" ? "meters" : "project units of the exporter";
      return info;
    }

    /// <summary>
    /// Runs an exporter on a temporary 3D view showing only the elements. IFC needs an open
    /// transaction; the others need none. The view is removed by rolling the group back.
    /// </summary>
    private static void WithSelectionView(Document doc, List<Element> elements, string ext, Action<View3D> export)
    {
      using (var group = new TransactionGroup(doc, "Claude: export"))
      {
        group.Start();
        try
        {
          View3D view;
          using (var t = new Transaction(doc, "Claude: export view"))
          {
            t.Start();
            var vft = new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().First(v => v.ViewFamily == ViewFamily.ThreeDimensional);
            view = View3D.CreateIsometric(doc, vft.Id);
            view.Name = "Claude export " + Guid.NewGuid().ToString("N").Substring(0, 8);
            view.DetailLevel = ViewDetailLevel.Fine;
            view.IsolateElementsTemporary(elements.Select(e => e.Id).ToList());
            view.ConvertTemporaryHideIsolateToPermanent();
            t.Commit();
          }
          if (ext == ".ifc")
          {
            using (var t = new Transaction(doc, "Claude: IFC export"))
            {
              t.Start();
              export(view);
              t.RollBack();
            }
          }
          else
          {
            export(view);
          }
        }
        finally
        {
          group.RollBack();
        }
      }
    }

    // ------------------------------------------------------------------ STL / OBJ

    private static IEnumerable<(Element element, List<XYZ[]> triangles)> Meshes(IEnumerable<Element> elements)
    {
      foreach (var e in elements)
      {
        var tris = Geo.Triangles(e);
        if (tris.Count > 0) yield return (e, tris);
      }
    }

    private static JObject WriteStl(string path, List<Element> elements, bool ascii)
    {
      var all = Meshes(elements).ToList();
      int count = all.Sum(m => m.triangles.Count);
      if (count == 0) throw RpcException.NotFound("The selection has no 3D geometry.");
      if (ascii)
      {
        using (var w = new StreamWriter(path, false, new UTF8Encoding(false)))
        {
          w.WriteLine("solid revit");
          foreach (var (_, tris) in all)
            foreach (var t in tris)
            {
              var n = Normal(t);
              w.WriteLine(string.Format(CultureInfo.InvariantCulture, "facet normal {0:R} {1:R} {2:R}", n.X, n.Y, n.Z));
              w.WriteLine("outer loop");
              foreach (var v in t) w.WriteLine(string.Format(CultureInfo.InvariantCulture, "vertex {0:R} {1:R} {2:R}", U.ToMeters(v.X), U.ToMeters(v.Y), U.ToMeters(v.Z)));
              w.WriteLine("endloop");
              w.WriteLine("endfacet");
            }
          w.WriteLine("endsolid revit");
        }
      }
      else
      {
        using (var w = new BinaryWriter(File.Create(path)))
        {
          var header = new byte[80];
          var text = Encoding.ASCII.GetBytes("Revit MCP Bridge STL (meters)");
          Array.Copy(text, header, text.Length);
          w.Write(header);
          w.Write((uint)count);
          foreach (var (_, tris) in all)
            foreach (var t in tris)
            {
              var n = Normal(t);
              w.Write((float)n.X); w.Write((float)n.Y); w.Write((float)n.Z);
              foreach (var v in t) { w.Write((float)U.ToMeters(v.X)); w.Write((float)U.ToMeters(v.Y)); w.Write((float)U.ToMeters(v.Z)); }
              w.Write((ushort)0);
            }
        }
      }
      return new JObject { ["triangles"] = count, ["elements_with_geometry"] = all.Count };
    }

    private static XYZ Normal(XYZ[] t)
    {
      var n = (t[1] - t[0]).CrossProduct(t[2] - t[0]);
      return n.GetLength() > 1e-12 ? n.Normalize() : XYZ.BasisZ;
    }

    /// <summary>OBJ with one group per element (name = category + id) and shared vertices per element.</summary>
    private static JObject WriteObj(string path, List<Element> elements)
    {
      int vertexBase = 1, triangles = 0, groups = 0;
      using (var w = new StreamWriter(path, false, new UTF8Encoding(false)))
      {
        w.WriteLine("# Revit MCP Bridge OBJ (meters)");
        foreach (var (e, tris) in Meshes(elements))
        {
          groups++;
          var name = ((e.Category?.Name ?? "Element") + "_" + Ids.Str(e.Id)).Replace(' ', '_');
          w.WriteLine("o " + name);
          var index = new Dictionary<(long, long, long), int>();
          var faces = new List<int[]>();
          foreach (var t in tris)
          {
            var f = new int[3];
            for (int k = 0; k < 3; k++)
            {
              var key = ((long)Math.Round(t[k].X * 1e6), (long)Math.Round(t[k].Y * 1e6), (long)Math.Round(t[k].Z * 1e6));
              if (!index.TryGetValue(key, out var vi))
              {
                vi = vertexBase + index.Count;
                index[key] = vi;
                w.WriteLine(string.Format(CultureInfo.InvariantCulture, "v {0:R} {1:R} {2:R}", U.ToMeters(t[k].X), U.ToMeters(t[k].Y), U.ToMeters(t[k].Z)));
              }
              f[k] = vi;
            }
            faces.Add(f);
          }
          foreach (var f in faces) w.WriteLine("f " + f[0] + " " + f[1] + " " + f[2]);
          vertexBase += index.Count;
          triangles += faces.Count;
        }
      }
      if (triangles == 0) throw RpcException.NotFound("The selection has no 3D geometry.");
      return new JObject { ["triangles"] = triangles, ["elements_with_geometry"] = groups };
    }
  }
}
