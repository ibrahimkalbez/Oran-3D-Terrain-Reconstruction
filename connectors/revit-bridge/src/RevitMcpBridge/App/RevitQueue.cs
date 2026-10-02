using System;
using System.Collections.Concurrent;
using System.Runtime.ExceptionServices;
using System.Threading;
using Autodesk.Revit.UI;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;

namespace RevitMcpBridge.App
{
  /// <summary>
  /// The Revit API may only be called from Revit's main thread, inside an API context.
  /// HTTP requests are queued here and executed by an ExternalEvent, which Revit raises
  /// when it is idle; the HTTP thread waits for the result.
  /// </summary>
  public sealed class RevitQueue : IExternalEventHandler
  {
    private sealed class WorkItem
    {
      public Func<UIApplication, JToken> Work;
      public JToken Result;
      public Exception Error;
      public int State; // 0 queued, 1 running, 2 cancelled
      public readonly ManualResetEventSlim Started = new ManualResetEventSlim(false);
      public readonly ManualResetEventSlim Done = new ManualResetEventSlim(false);
    }

    private readonly ConcurrentQueue<WorkItem> _queue = new ConcurrentQueue<WorkItem>();
    private ExternalEvent _event;

    /// <summary>How long a request may wait for Revit to become idle.</summary>
    public TimeSpan QueueTimeout { get; set; } = TimeSpan.FromSeconds(90);

    /// <summary>Upper bound for a call that already started (Dynamo graphs, exports).</summary>
    public TimeSpan RunTimeout { get; set; } = TimeSpan.FromMinutes(60);

    /// <summary>Must be called from a valid API context (OnStartup).</summary>
    public void Initialize()
    {
      _event = ExternalEvent.Create(this);
    }

    public string GetName() => "Revit MCP Bridge";

    public void Execute(UIApplication app)
    {
      while (_queue.TryDequeue(out var item))
      {
        if (Interlocked.CompareExchange(ref item.State, 1, 0) != 0) continue;
        item.Started.Set();
        try
        {
          RevitContext.Set(app);
          item.Result = item.Work(app);
        }
        catch (Exception ex)
        {
          item.Error = ex;
        }
        finally
        {
          item.Done.Set();
        }
      }
    }

    public JToken Invoke(Func<UIApplication, JToken> work)
    {
      if (_event == null) throw new InvalidOperationException("The Revit queue is not initialised.");
      var item = new WorkItem { Work = work };
      _queue.Enqueue(item);
      var request = _event.Raise();
      if (request == ExternalEventRequest.Denied)
        throw new RpcException(RpcErrorCodes.Timeout, "Revit refused the request (the add-in event is disabled).");

      if (!item.Started.Wait(QueueTimeout))
      {
        if (Interlocked.CompareExchange(ref item.State, 2, 0) == 0)
        {
          throw new RpcException(RpcErrorCodes.Timeout,
            "Revit did not pick up the request in time. A dialog, an edit mode (sketch, in-place family) or a running " +
            "command is probably blocking Revit: finish or cancel it (Esc) and try again.");
        }
      }
      if (!item.Done.Wait(RunTimeout))
        throw new RpcException(RpcErrorCodes.Timeout, "The operation is still running in Revit after " + RunTimeout.TotalMinutes + " minutes.");
      if (item.Error != null) ExceptionDispatchInfo.Capture(item.Error).Throw();
      return item.Result;
    }
  }

  /// <summary>The Revit application and active document for the handler being executed.</summary>
  public static class RevitContext
  {
    [ThreadStatic] private static UIApplication _app;

    public static void Set(UIApplication app) => _app = app;

    public static UIApplication App => _app ?? throw new RpcException(RpcErrorCodes.InternalError, "No Revit context.");

    public static Autodesk.Revit.DB.Document Doc
    {
      get
      {
        var doc = App.ActiveUIDocument?.Document;
        if (doc == null) throw new RpcException(RpcErrorCodes.NoActiveDocument, "No active Revit project. Open a project in Revit.");
        if (doc.IsFamilyDocument) throw new RpcException(RpcErrorCodes.NoActiveDocument, "The active document is a family: open a project.");
        return doc;
      }
    }

    public static UIDocument UiDoc => App.ActiveUIDocument ?? throw new RpcException(RpcErrorCodes.NoActiveDocument, "No active Revit project.");
  }
}
