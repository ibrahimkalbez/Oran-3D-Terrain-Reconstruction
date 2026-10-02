using System;
using Newtonsoft.Json.Linq;

namespace McpBridge.Transport
{
  /// <summary>JSON-RPC 2.0 error codes used by the bridge.</summary>
  public static class RpcErrorCodes
  {
    public const int ParseError = -32700;
    public const int InvalidRequest = -32600;
    public const int MethodNotFound = -32601;
    public const int InvalidParams = -32602;
    public const int InternalError = -32603;

    // Bridge-specific codes (JSON-RPC reserves -32000..-32099 for servers).
    public const int NoActiveDocument = -32001;
    public const int GrasshopperUnavailable = -32002;
    public const int NotFound = -32003;
    public const int Timeout = -32004;
    public const int Ambiguous = -32005;
    public const int OperationFailed = -32006;
    public const int Unauthorized = -32010;
  }

  /// <summary>
  /// Exception carrying a JSON-RPC error. Handlers throw it to return a structured,
  /// actionable error to the MCP server (and from there to Claude).
  /// </summary>
  public class RpcException : Exception
  {
    public int Code { get; }
    public JToken ErrorData { get; }

    public RpcException(int code, string message, JToken data = null) : base(message)
    {
      Code = code;
      ErrorData = data;
    }

    public static RpcException InvalidParams(string message, JToken data = null)
      => new RpcException(RpcErrorCodes.InvalidParams, message, data);

    public static RpcException NotFound(string message, JToken data = null)
      => new RpcException(RpcErrorCodes.NotFound, message, data);

    public static RpcException Failed(string message, JToken data = null)
      => new RpcException(RpcErrorCodes.OperationFailed, message, data);
  }
}
