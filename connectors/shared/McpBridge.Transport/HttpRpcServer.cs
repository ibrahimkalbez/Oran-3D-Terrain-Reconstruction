using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace McpBridge.Transport
{
  /// <summary>
  /// Minimal HTTP/1.1 server bound to the loopback interface only.
  ///
  /// Endpoints
  ///   GET  /health  → liveness probe, no secret information, no authentication.
  ///   POST /rpc     → one JSON-RPC 2.0 request, requires "Authorization: Bearer &lt;token&gt;".
  ///
  /// It is written on top of TcpListener rather than HttpListener so it needs no
  /// http.sys URL reservation and no administrator rights on Windows.
  ///
  /// Security: loopback bind, exclusive port, bearer token compared in constant time,
  /// requests carrying an Origin header are refused (blocks browser pages), and the Host
  /// header must name the loopback address (blocks DNS-rebinding attacks).
  /// </summary>
  public sealed class HttpRpcServer : IDisposable
  {
    public const int MaxHeaderBytes = 32 * 1024;
    public const long MaxBodyBytes = 256L * 1024 * 1024;

    private readonly RpcDispatcher _dispatcher;
    private readonly byte[] _token;
    private readonly string _serviceVersion;
    private readonly string _serviceName;
    private TcpListener _listener;
    private Thread _acceptThread;
    private volatile bool _running;
    private int _activeRequests;

    public int Port { get; private set; }
    public bool IsRunning => _running;
    public int ActiveRequests => _activeRequests;

    /// <summary>Called with a short line for every request and error (for the Rhino command line / log).</summary>
    public Action<string> Log { get; set; } = _ => { };

    public HttpRpcServer(RpcDispatcher dispatcher, string token, string serviceVersion, string serviceName = "rhino-mcp-bridge")
    {
      _serviceName = serviceName;
      _dispatcher = dispatcher ?? throw new ArgumentNullException(nameof(dispatcher));
      if (string.IsNullOrEmpty(token)) throw new ArgumentException("A token is required.", nameof(token));
      _token = Encoding.UTF8.GetBytes(token);
      _serviceVersion = serviceVersion;
    }

    /// <summary>Starts on the first free port in [firstPort, firstPort + attempts).</summary>
    public int Start(int firstPort, int attempts = 10)
    {
      if (_running) return Port;
      Exception last = null;
      for (int i = 0; i < Math.Max(1, attempts); i++)
      {
        int port = firstPort + i;
        try
        {
          var listener = new TcpListener(IPAddress.Loopback, port);
          listener.ExclusiveAddressUse = true;
          listener.Start();
          _listener = listener;
          Port = ((IPEndPoint)listener.LocalEndpoint).Port;
          _running = true;
          _acceptThread = new Thread(AcceptLoop) { IsBackground = true, Name = "McpBridge.Accept" };
          _acceptThread.Start();
          return Port;
        }
        catch (SocketException ex)
        {
          last = ex;
        }
      }
      throw new InvalidOperationException(
        $"No free port in {firstPort}..{firstPort + attempts - 1}: {last?.Message}", last);
    }

    public void Stop()
    {
      _running = false;
      try { _listener?.Stop(); } catch { /* already stopped */ }
      _listener = null;
    }

    public void Dispose() => Stop();

    private void AcceptLoop()
    {
      while (_running)
      {
        TcpClient client;
        try
        {
          client = _listener.AcceptTcpClient();
        }
        catch (Exception)
        {
          if (!_running) return;
          continue;
        }
        Task.Run(() => Serve(client));
      }
    }

    private void Serve(TcpClient client)
    {
      Interlocked.Increment(ref _activeRequests);
      try
      {
        using (client)
        using (var stream = client.GetStream())
        {
          client.ReceiveTimeout = 60000;
          client.SendTimeout = 60000;
          HttpRequest request;
          try
          {
            request = ReadRequest(stream);
          }
          catch (HttpError he)
          {
            Write(stream, he.Status, ErrorJson(he.Message));
            return;
          }
          if (request == null) return;
          Route(stream, request);
        }
      }
      catch (Exception ex)
      {
        Log("bridge: connection error: " + ex.Message);
      }
      finally
      {
        Interlocked.Decrement(ref _activeRequests);
      }
    }

    private void Route(Stream stream, HttpRequest request)
    {
      if (request.Headers.ContainsKey("origin"))
      {
        Write(stream, 403, ErrorJson("Browser requests are not accepted."));
        return;
      }
      if (!IsLoopbackHost(request.Header("host")))
      {
        Write(stream, 403, ErrorJson("Invalid Host header."));
        return;
      }

      var path = request.Path;
      int q = path.IndexOf('?');
      if (q >= 0) path = path.Substring(0, q);

      if (request.Method == "GET" && path == "/health")
      {
        var health = new JObject
        {
          ["ok"] = true,
          ["service"] = _serviceName,
          ["version"] = _serviceVersion,
          ["auth"] = "bearer",
        };
        Write(stream, 200, health.ToString(Newtonsoft.Json.Formatting.None));
        return;
      }

      if (path != "/rpc")
      {
        Write(stream, 404, ErrorJson("Not found. Use POST /rpc or GET /health."));
        return;
      }
      if (request.Method != "POST")
      {
        Write(stream, 405, ErrorJson("Use POST for /rpc."));
        return;
      }
      if (!IsAuthorized(request))
      {
        var body = RpcDispatcher.Serialize(RpcDispatcher.ErrorResponse(null, RpcErrorCodes.Unauthorized,
          "Missing or invalid bridge token.", null));
        Write(stream, 401, body);
        return;
      }

      string requestBody = Encoding.UTF8.GetString(request.Body);
      var started = DateTime.UtcNow;
      string response = _dispatcher.HandleBody(requestBody);
      Log($"bridge: rpc {Summarize(requestBody)} in {(DateTime.UtcNow - started).TotalMilliseconds:0} ms");
      Write(stream, 200, response);
    }

    private bool IsAuthorized(HttpRequest request)
    {
      string value = request.Header("authorization");
      const string prefix = "Bearer ";
      string presented = null;
      if (value != null && value.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        presented = value.Substring(prefix.Length).Trim();
      else
        presented = request.Header("x-rhino-mcp-token");
      if (presented == null) return false;
      return FixedTimeEquals(Encoding.UTF8.GetBytes(presented), _token);
    }

    private bool IsLoopbackHost(string host)
    {
      if (string.IsNullOrEmpty(host)) return true; // HTTP/1.0 clients may omit it
      string h = host.Trim().ToLowerInvariant();
      if (h.StartsWith("["))
      {
        int end = h.IndexOf(']');
        h = end > 0 ? h.Substring(1, end - 1) : h;
      }
      else
      {
        int colon = h.LastIndexOf(':');
        if (colon > 0) h = h.Substring(0, colon);
      }
      return h == "127.0.0.1" || h == "localhost" || h == "::1";
    }

    private static bool FixedTimeEquals(byte[] a, byte[] b)
    {
      int diff = a.Length ^ b.Length;
      for (int i = 0; i < Math.Min(a.Length, b.Length); i++) diff |= a[i] ^ b[i];
      return diff == 0;
    }

    private static string Summarize(string body)
    {
      try
      {
        var o = JObject.Parse(body);
        return o.Value<string>("method") ?? "?";
      }
      catch
      {
        return "?";
      }
    }

    private static string ErrorJson(string message)
      => new JObject { ["ok"] = false, ["error"] = message }.ToString(Newtonsoft.Json.Formatting.None);

    // ---------------------------------------------------------------- HTTP parsing

    private sealed class HttpError : Exception
    {
      public int Status { get; }
      public HttpError(int status, string message) : base(message) { Status = status; }
    }

    public sealed class HttpRequest
    {
      public string Method;
      public string Path;
      public Dictionary<string, string> Headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
      public byte[] Body = new byte[0];
      public string Header(string name) => Headers.TryGetValue(name, out var v) ? v : null;
    }

    /// <summary>Reads one request. Returns null when the client closed the connection without sending anything.</summary>
    public static HttpRequest ReadRequest(Stream stream)
    {
      var headerBytes = new MemoryStream();
      int matched = 0; // progress through "\r\n\r\n"
      var one = new byte[1];
      while (true)
      {
        int n = stream.Read(one, 0, 1);
        if (n == 0)
        {
          if (headerBytes.Length == 0) return null;
          throw new HttpError(400, "Connection closed inside the request header.");
        }
        headerBytes.WriteByte(one[0]);
        if (headerBytes.Length > MaxHeaderBytes) throw new HttpError(431, "Request header too large.");
        byte c = one[0];
        if ((matched == 0 || matched == 2) && c == '\r') matched++;
        else if ((matched == 1 || matched == 3) && c == '\n') matched++;
        else matched = c == '\r' ? 1 : 0;
        if (matched == 4) break;
      }

      string head = Encoding.ASCII.GetString(headerBytes.ToArray());
      var lines = head.Split(new[] { "\r\n" }, StringSplitOptions.None);
      var parts = lines[0].Split(' ');
      if (parts.Length < 3) throw new HttpError(400, "Malformed request line.");

      var request = new HttpRequest { Method = parts[0].ToUpperInvariant(), Path = parts[1] };
      for (int i = 1; i < lines.Length; i++)
      {
        string line = lines[i];
        if (line.Length == 0) continue;
        int colon = line.IndexOf(':');
        if (colon <= 0) throw new HttpError(400, "Malformed header line.");
        request.Headers[line.Substring(0, colon).Trim()] = line.Substring(colon + 1).Trim();
      }

      string te = request.Header("transfer-encoding");
      if (te != null && te.IndexOf("chunked", StringComparison.OrdinalIgnoreCase) >= 0)
      {
        request.Body = ReadChunked(stream);
        return request;
      }

      string cl = request.Header("content-length");
      if (cl != null)
      {
        if (!long.TryParse(cl, out long length) || length < 0) throw new HttpError(400, "Invalid Content-Length.");
        if (length > MaxBodyBytes) throw new HttpError(413, "Request body too large.");
        request.Body = ReadExactly(stream, (int)length);
      }
      return request;
    }

    private static byte[] ReadExactly(Stream stream, int length)
    {
      var buffer = new byte[length];
      int read = 0;
      while (read < length)
      {
        int n = stream.Read(buffer, read, length - read);
        if (n == 0) throw new HttpError(400, "Connection closed inside the request body.");
        read += n;
      }
      return buffer;
    }

    private static byte[] ReadChunked(Stream stream)
    {
      var body = new MemoryStream();
      while (true)
      {
        string sizeLine = ReadLine(stream);
        int semi = sizeLine.IndexOf(';');
        if (semi >= 0) sizeLine = sizeLine.Substring(0, semi);
        if (!int.TryParse(sizeLine.Trim(), System.Globalization.NumberStyles.HexNumber, null, out int size) || size < 0)
          throw new HttpError(400, "Invalid chunk size.");
        if (size == 0)
        {
          while (ReadLine(stream).Length > 0) { } // trailers
          return body.ToArray();
        }
        if (body.Length + size > MaxBodyBytes) throw new HttpError(413, "Request body too large.");
        var chunk = ReadExactly(stream, size);
        body.Write(chunk, 0, chunk.Length);
        ReadLine(stream);
      }
    }

    private static string ReadLine(Stream stream)
    {
      var sb = new StringBuilder();
      var one = new byte[1];
      while (true)
      {
        int n = stream.Read(one, 0, 1);
        if (n == 0) throw new HttpError(400, "Connection closed inside a chunk.");
        if (one[0] == '\n') break;
        if (one[0] != '\r') sb.Append((char)one[0]);
        if (sb.Length > 1024) throw new HttpError(400, "Line too long.");
      }
      return sb.ToString();
    }

    private static void Write(Stream stream, int status, string json)
    {
      byte[] body = Encoding.UTF8.GetBytes(json);
      string reason = status switch
      {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        413 => "Payload Too Large",
        431 => "Request Header Fields Too Large",
        _ => "Error",
      };
      string head = $"HTTP/1.1 {status} {reason}\r\n" +
                    "Content-Type: application/json; charset=utf-8\r\n" +
                    $"Content-Length: {body.Length}\r\n" +
                    "Cache-Control: no-store\r\n" +
                    "Connection: close\r\n\r\n";
      byte[] headBytes = Encoding.ASCII.GetBytes(head);
      stream.Write(headBytes, 0, headBytes.Length);
      stream.Write(body, 0, body.Length);
      stream.Flush();
    }

    /// <summary>Cryptographically random URL-safe token.</summary>
    public static string NewToken(int bytes = 32)
    {
      var data = new byte[bytes];
      using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(data);
      return Convert.ToBase64String(data).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }
  }
}
