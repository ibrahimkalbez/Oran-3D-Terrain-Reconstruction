using System;
using System.Runtime.ExceptionServices;
using System.Threading;
using Newtonsoft.Json.Linq;
using Rhino;
using McpBridge.Transport;

namespace RhinoMcpBridge.Core
{
  /// <summary>
  /// RhinoCommon and Grasshopper are not thread-safe: every handler runs on Rhino's UI
  /// thread. The HTTP worker thread posts the call and waits for it.
  /// </summary>
  public static class UiThread
  {
    /// <summary>How long a request may wait in the queue before Rhino starts it.</summary>
    public static TimeSpan QueueTimeout = TimeSpan.FromSeconds(90);

    /// <summary>Upper bound for a call that already started (long Grasshopper solutions).</summary>
    public static TimeSpan RunTimeout = TimeSpan.FromMinutes(60);

    public static JToken Invoke(Func<JToken> fn)
    {
      if (!RhinoApp.InvokeRequired) return fn();

      JToken result = null;
      Exception error = null;
      int state = 0; // 0 = queued, 1 = running, 2 = cancelled
      using (var started = new ManualResetEventSlim(false))
      using (var done = new ManualResetEventSlim(false))
      {
        RhinoApp.InvokeOnUiThread(new Action(() =>
        {
          if (Interlocked.CompareExchange(ref state, 1, 0) != 0) return;
          started.Set();
          try { result = fn(); }
          catch (Exception ex) { error = ex; }
          finally { done.Set(); }
        }));

        if (!started.Wait(QueueTimeout))
        {
          if (Interlocked.CompareExchange(ref state, 2, 0) == 0)
          {
            throw new RpcException(RpcErrorCodes.Timeout,
              "Rhino did not pick up the request in time. A modal dialog or a running command is probably " +
              "blocking Rhino: finish or cancel it (Esc) and try again.");
          }
        }

        if (!done.Wait(RunTimeout))
          throw new RpcException(RpcErrorCodes.Timeout, "The operation is still running in Rhino after " + RunTimeout.TotalMinutes + " minutes.");
      }

      if (error != null) ExceptionDispatchInfo.Capture(error).Throw();
      return result;
    }
  }
}
