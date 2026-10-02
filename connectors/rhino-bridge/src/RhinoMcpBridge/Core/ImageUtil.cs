using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace RhinoMcpBridge.Core
{
  public static class ImageUtil
  {
    /// <summary>
    /// Encodes a bitmap as PNG (default) or JPEG, returns it in base64 and optionally saves it
    /// to p["save_path"]. Set p["return_image"]=false to only save.
    /// </summary>
    public static JObject Encode(Bitmap bmp, JObject p, string label)
    {
      var format = (Args.Str(p, "format", "png") ?? "png").ToLowerInvariant();
      bool jpeg = format == "jpg" || format == "jpeg";
      byte[] bytes;
      using (var ms = new MemoryStream())
      {
        if (jpeg)
        {
          var codec = ImageCodecInfo.GetImageEncoders().First(c => c.FormatID == ImageFormat.Jpeg.Guid);
          using (var parameters = new EncoderParameters(1))
          {
            parameters.Param[0] = new EncoderParameter(Encoder.Quality, (long)Math.Max(30, Math.Min(100, Args.Int(p, "quality", 88))));
            bmp.Save(ms, codec, parameters);
          }
        }
        else
        {
          bmp.Save(ms, ImageFormat.Png);
        }
        bytes = ms.ToArray();
      }

      var result = new JObject
      {
        ["label"] = label,
        ["width"] = bmp.Width,
        ["height"] = bmp.Height,
        ["mime_type"] = jpeg ? "image/jpeg" : "image/png",
        ["bytes"] = bytes.Length,
      };

      var savePath = Args.Str(p, "save_path");
      if (!string.IsNullOrEmpty(savePath))
      {
        savePath = Path.GetFullPath(Environment.ExpandEnvironmentVariables(savePath));
        var dir = Path.GetDirectoryName(savePath);
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        File.WriteAllBytes(savePath, bytes);
        result["saved_path"] = savePath;
      }
      if (Args.Bool(p, "return_image", true)) result["image_base64"] = Convert.ToBase64String(bytes);
      return result;
    }
  }
}
