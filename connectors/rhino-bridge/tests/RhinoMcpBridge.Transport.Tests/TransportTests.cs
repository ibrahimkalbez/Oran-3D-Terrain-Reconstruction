using System;
using System.IO;
using System.Net.Http;
using System.Net.Sockets;
using System.Text;
using Newtonsoft.Json.Linq;
using RhinoMcpBridge.Transport;
using Xunit;

public class TransportTests : IDisposable
{
  private const string Token = "test-token-123";
  private readonly HttpRpcServer _server;
  private readonly RpcDispatcher _dispatcher;
  private readonly HttpClient _http = new HttpClient();
  private readonly int _port;

  public TransportTests()
  {
    _dispatcher = new RpcDispatcher();
    _dispatcher.Register("echo", p => p);
    _dispatcher.Register("fail", p => throw RpcException.NotFound("missing thing", new JObject { ["k"] = 1 }));
    _dispatcher.Register("crash", p => throw new InvalidOperationException("boom"));
    _server = new HttpRpcServer(_dispatcher, Token, "9.9.9");
    _port = _server.Start(18642 + new Random().Next(0, 2000), 20);
  }

  public void Dispose()
  {
    _server.Dispose();
    _http.Dispose();
  }

  private HttpRequestMessage Rpc(string body, string token = Token)
  {
    var req = new HttpRequestMessage(HttpMethod.Post, $"http://127.0.0.1:{_port}/rpc")
    {
      Content = new StringContent(body, Encoding.UTF8, "application/json"),
    };
    if (token != null) req.Headers.Add("Authorization", "Bearer " + token);
    return req;
  }

  private JObject Send(HttpRequestMessage req, out int status)
  {
    var resp = _http.Send(req);
    status = (int)resp.StatusCode;
    return JObject.Parse(resp.Content.ReadAsStringAsync().Result);
  }

  [Fact]
  public void HealthNeedsNoTokenAndLeaksNoSecret()
  {
    var body = _http.GetStringAsync($"http://127.0.0.1:{_port}/health").Result;
    var json = JObject.Parse(body);
    Assert.True((bool)json["ok"]);
    Assert.Equal("9.9.9", (string)json["version"]);
    Assert.DoesNotContain(Token, body);
  }

  [Fact]
  public void RpcRoundTripPreservesIdAndParams()
  {
    var json = Send(Rpc("{\"jsonrpc\":\"2.0\",\"id\":42,\"method\":\"echo\",\"params\":{\"a\":[1,2,3],\"s\":\"é\"}}"), out var status);
    Assert.Equal(200, status);
    Assert.Equal(42, (int)json["id"]);
    Assert.Equal("é", (string)json["result"]["s"]);
    Assert.Equal(3, ((JArray)json["result"]["a"]).Count);
  }

  [Fact]
  public void MissingOrWrongTokenIsRejected()
  {
    Send(Rpc("{\"id\":1,\"method\":\"echo\"}", token: null), out var s1);
    Assert.Equal(401, s1);
    Send(Rpc("{\"id\":1,\"method\":\"echo\"}", token: "wrong"), out var s2);
    Assert.Equal(401, s2);
  }

  [Fact]
  public void AlternativeTokenHeaderIsAccepted()
  {
    var req = Rpc("{\"id\":1,\"method\":\"echo\"}", token: null);
    req.Headers.Add("X-Rhino-Mcp-Token", Token);
    Send(req, out var status);
    Assert.Equal(200, status);
  }

  [Fact]
  public void BrowserOriginIsRejected()
  {
    var req = Rpc("{\"id\":1,\"method\":\"echo\"}");
    req.Headers.Add("Origin", "https://evil.example");
    Send(req, out var status);
    Assert.Equal(403, status);
  }

  [Fact]
  public void ForeignHostHeaderIsRejected()
  {
    var req = Rpc("{\"id\":1,\"method\":\"echo\"}");
    req.Headers.Host = "attacker.example:" + _port;
    Send(req, out var status);
    Assert.Equal(403, status);
  }

  [Fact]
  public void ErrorsAreStructured()
  {
    var notFound = Send(Rpc("{\"id\":\"a\",\"method\":\"fail\"}"), out _);
    Assert.Equal(RpcErrorCodes.NotFound, (int)notFound["error"]["code"]);
    Assert.Equal(1, (int)notFound["error"]["data"]["k"]);

    var crash = Send(Rpc("{\"id\":2,\"method\":\"crash\"}"), out _);
    Assert.Equal(RpcErrorCodes.InternalError, (int)crash["error"]["code"]);
    Assert.Contains("boom", (string)crash["error"]["message"]);

    var unknown = Send(Rpc("{\"id\":3,\"method\":\"nope\"}"), out _);
    Assert.Equal(RpcErrorCodes.MethodNotFound, (int)unknown["error"]["code"]);
    Assert.Contains("echo", unknown["error"]["data"]["available"].ToString());

    var parse = Send(Rpc("{not json"), out _);
    Assert.Equal(RpcErrorCodes.ParseError, (int)parse["error"]["code"]);

    var badParams = Send(Rpc("{\"id\":4,\"method\":\"echo\",\"params\":[1]}"), out _);
    Assert.Equal(RpcErrorCodes.InvalidParams, (int)badParams["error"]["code"]);
  }

  [Fact]
  public void LargeBodyIsReadCompletely()
  {
    var big = new string('x', 5_000_000);
    var json = Send(Rpc("{\"id\":1,\"method\":\"echo\",\"params\":{\"big\":\"" + big + "\"}}"), out var status);
    Assert.Equal(200, status);
    Assert.Equal(big.Length, ((string)json["result"]["big"]).Length);
  }

  [Fact]
  public void ChunkedBodyIsSupported()
  {
    using var client = new TcpClient("127.0.0.1", _port);
    using var stream = client.GetStream();
    var body = "{\"id\":7,\"method\":\"echo\",\"params\":{\"v\":1}}";
    var half = body.Length / 2;
    var request = $"POST /rpc HTTP/1.1\r\nHost: 127.0.0.1:{_port}\r\nAuthorization: Bearer {Token}\r\nTransfer-Encoding: chunked\r\n\r\n" +
                  $"{half:X}\r\n{body.Substring(0, half)}\r\n{(body.Length - half):X}\r\n{body.Substring(half)}\r\n0\r\n\r\n";
    var bytes = Encoding.ASCII.GetBytes(request);
    stream.Write(bytes, 0, bytes.Length);
    var response = new StreamReader(stream).ReadToEnd();
    Assert.StartsWith("HTTP/1.1 200", response);
    Assert.Contains("\"id\":7", response);
  }

  [Fact]
  public void SecondServerTakesNextPort()
  {
    using var other = new HttpRpcServer(_dispatcher, Token, "1");
    int port = other.Start(_port, 5);
    Assert.NotEqual(_port, port);
  }

  [Fact]
  public void TokensAreRandomAndUrlSafe()
  {
    var a = HttpRpcServer.NewToken();
    var b = HttpRpcServer.NewToken();
    Assert.NotEqual(a, b);
    Assert.True(a.Length >= 40);
    Assert.DoesNotContain("+", a);
    Assert.DoesNotContain("/", a);
  }

  [Fact]
  public void RegistryPublishesAndRemovesInstanceFile()
  {
    var dir = Path.Combine(Path.GetTempPath(), "rmb-" + Guid.NewGuid());
    var registry = new InstanceRegistry(dir);
    registry.Publish(_port, Token, "8.0", "1.0.0", "C:/model.3dm");
    var info = JObject.Parse(File.ReadAllText(registry.FilePath));
    Assert.Equal(_port, (int)info["port"]);
    Assert.Equal(Token, (string)info["token"]);
    registry.UpdateDocument("C:/other.3dm");
    Assert.Equal("C:/other.3dm", (string)JObject.Parse(File.ReadAllText(registry.FilePath))["document"]);
    File.WriteAllText(Path.Combine(dir, "999999.json"), "{}");
    registry.PruneStale();
    Assert.False(File.Exists(Path.Combine(dir, "999999.json")));
    registry.Remove();
    Assert.False(File.Exists(registry.FilePath));
    Directory.Delete(dir, true);
  }
}
