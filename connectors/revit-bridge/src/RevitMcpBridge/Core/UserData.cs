using System;
using System.Collections.Generic;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.ExtensibleStorage;
using Newtonsoft.Json.Linq;

namespace RevitMcpBridge.Core
{
  /// <summary>
  /// Key/value "user text" on Revit elements (the equivalent of Rhino's user text), kept in
  /// extensible storage so it travels with the model without adding project parameters.
  /// Used for tags such as mcp.kind, mcp.layer, mcp.tree_set or variant names.
  /// </summary>
  public static class UserData
  {
    private static readonly Guid SchemaId = new Guid("a6d1f2c4-3b7e-4f59-9c21-7e5d0b8a4c13");
    public const string LayerKey = "mcp.layer";

    private static Schema GetSchema(bool create)
    {
      var schema = Schema.Lookup(SchemaId);
      if (schema != null || !create) return schema;
      var builder = new SchemaBuilder(SchemaId);
      builder.SetSchemaName("ClaudeMcpUserText");
      builder.SetDocumentation("Key/value data written by the Claude MCP connector (Revit Dynamo Connector).");
      builder.SetReadAccessLevel(AccessLevel.Public);
      builder.SetWriteAccessLevel(AccessLevel.Public);
      builder.SetVendorId("MCPB");
      builder.AddSimpleField("Json", typeof(string));
      return builder.Finish();
    }

    public static JObject Read(Element e)
    {
      try
      {
        var schema = GetSchema(false);
        if (schema == null) return new JObject();
        var entity = e.GetEntity(schema);
        if (!entity.IsValid()) return new JObject();
        var json = entity.Get<string>("Json");
        return string.IsNullOrEmpty(json) ? new JObject() : JObject.Parse(json);
      }
      catch
      {
        return new JObject();
      }
    }

    public static string Get(Element e, string key) => Read(e).Value<string>(key);

    /// <summary>Merges values (null deletes a key). Must run inside a transaction.</summary>
    public static JObject Write(Element e, IDictionary<string, JToken> values)
    {
      var schema = GetSchema(true);
      var data = Read(e);
      foreach (var kv in values)
      {
        if (kv.Value == null || kv.Value.Type == JTokenType.Null) data.Remove(kv.Key);
        else data[kv.Key] = kv.Value.Type == JTokenType.String ? (string)kv.Value : kv.Value.ToString(Newtonsoft.Json.Formatting.None);
      }
      var entity = new Entity(schema);
      entity.Set("Json", data.ToString(Newtonsoft.Json.Formatting.None));
      e.SetEntity(entity);
      return data;
    }
  }
}
