using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace RhinoMcpBridge.Transport
{
  /// <summary>
  /// Maps JSON-RPC method names to handlers and turns a request body into a response body.
  /// Independent of Rhino: the host decides on which thread a handler runs through
  /// <see cref="Invoker"/>.
  /// </summary>
  public sealed class RpcDispatcher
  {
    public delegate JToken Handler(JObject parameters);

    private readonly Dictionary<string, Handler> _handlers =
      new Dictionary<string, Handler>(StringComparer.Ordinal);

    /// <summary>
    /// Runs a handler. The Rhino host replaces it with a UI-thread marshaller;
    /// tests keep the direct call.
    /// </summary>
    public Func<Func<JToken>, JToken> Invoker { get; set; } = f => f();

    /// <summary>Methods that must not be marshalled (they only read thread-safe state).</summary>
    private readonly HashSet<string> _direct = new HashSet<string>(StringComparer.Ordinal);

    public void Register(string method, Handler handler, bool direct = false)
    {
      _handlers[method] = handler;
      if (direct) _direct.Add(method);
    }

    public IReadOnlyCollection<string> Methods => _handlers.Keys.OrderBy(k => k, StringComparer.Ordinal).ToList();

    public static readonly JsonSerializerSettings JsonSettings = new JsonSerializerSettings
    {
      FloatFormatHandling = FloatFormatHandling.String,
      NullValueHandling = NullValueHandling.Include,
      DateParseHandling = DateParseHandling.None,
    };

    /// <summary>Handles one JSON-RPC request body and returns the response body.</summary>
    public string HandleBody(string body)
    {
      JObject request;
      try
      {
        var token = JsonConvert.DeserializeObject<JToken>(body, JsonSettings);
        request = token as JObject;
        if (request == null)
          return Serialize(ErrorResponse(null, RpcErrorCodes.InvalidRequest, "Request must be a JSON object.", null));
      }
      catch (JsonException ex)
      {
        return Serialize(ErrorResponse(null, RpcErrorCodes.ParseError, "Invalid JSON: " + ex.Message, null));
      }
      return Serialize(Handle(request));
    }

    public JObject Handle(JObject request)
    {
      var id = request["id"]?.DeepClone();
      var method = request.Value<string>("method");
      if (string.IsNullOrWhiteSpace(method))
        return ErrorResponse(id, RpcErrorCodes.InvalidRequest, "Missing 'method'.", null);

      if (!_handlers.TryGetValue(method, out var handler))
      {
        return ErrorResponse(id, RpcErrorCodes.MethodNotFound, "Unknown method '" + method + "'.",
          new JObject { ["available"] = new JArray(Methods) });
      }

      var p = request["params"];
      JObject parameters;
      if (p == null || p.Type == JTokenType.Null) parameters = new JObject();
      else if (p is JObject o) parameters = o;
      else return ErrorResponse(id, RpcErrorCodes.InvalidParams, "'params' must be an object.", null);

      try
      {
        JToken result = _direct.Contains(method) ? handler(parameters) : Invoker(() => handler(parameters));
        return new JObject
        {
          ["jsonrpc"] = "2.0",
          ["id"] = id,
          ["result"] = result ?? JValue.CreateNull(),
        };
      }
      catch (RpcException ex)
      {
        return ErrorResponse(id, ex.Code, ex.Message, ex.ErrorData);
      }
      catch (Exception ex)
      {
        var inner = ex is System.Reflection.TargetInvocationException && ex.InnerException != null ? ex.InnerException : ex;
        if (inner is RpcException rex) return ErrorResponse(id, rex.Code, rex.Message, rex.ErrorData);
        return ErrorResponse(id, RpcErrorCodes.InternalError, inner.GetType().Name + ": " + inner.Message,
          new JObject { ["stack"] = Truncate(inner.StackTrace, 2000) });
      }
    }

    public static JObject ErrorResponse(JToken id, int code, string message, JToken data)
    {
      var error = new JObject { ["code"] = code, ["message"] = message };
      if (data != null) error["data"] = data;
      return new JObject { ["jsonrpc"] = "2.0", ["id"] = id ?? JValue.CreateNull(), ["error"] = error };
    }

    public static string Serialize(JToken token) => token.ToString(Formatting.None);

    private static string Truncate(string s, int max)
      => s == null ? null : (s.Length <= max ? s : s.Substring(0, max) + "…");
  }
}
