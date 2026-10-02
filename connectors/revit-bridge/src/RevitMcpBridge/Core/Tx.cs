using System;
using System.Collections.Generic;
using Autodesk.Revit.DB;
using McpBridge.Transport;
using Newtonsoft.Json.Linq;

namespace RevitMcpBridge.Core
{
  /// <summary>
  /// Runs a modification in one named transaction (one Undo step in Revit). Warnings are
  /// collected and dismissed instead of opening dialogs that would block Revit; errors
  /// roll the transaction back and are returned to Claude.
  /// </summary>
  public static class Tx
  {
    private sealed class Collector : IFailuresPreprocessor
    {
      public readonly List<string> Warnings = new List<string>();
      public readonly List<string> Errors = new List<string>();

      public FailureProcessingResult PreprocessFailures(FailuresAccessor accessor)
      {
        foreach (var f in accessor.GetFailureMessages())
        {
          var text = f.GetDescriptionText();
          if (f.GetSeverity() == FailureSeverity.Warning)
          {
            Warnings.Add(text);
            accessor.DeleteWarning(f);
          }
          else
          {
            Errors.Add(text);
          }
        }
        return Errors.Count > 0 ? FailureProcessingResult.ProceedWithRollBack : FailureProcessingResult.Continue;
      }
    }

    public static JToken Run(Document doc, string name, Func<JToken> work)
    {
      var collector = new Collector();
      using (var t = new Transaction(doc, "Claude: " + name))
      {
        var options = t.GetFailureHandlingOptions();
        options.SetFailuresPreprocessor(collector);
        options.SetClearAfterRollback(true);
        t.SetFailureHandlingOptions(options);
        t.Start();
        JToken result;
        try
        {
          result = work();
        }
        catch
        {
          if (t.HasStarted() && !t.HasEnded()) t.RollBack();
          throw;
        }
        var status = t.Commit();
        if (status != TransactionStatus.Committed)
        {
          throw new RpcException(RpcErrorCodes.OperationFailed, "Revit rolled the change back: " + string.Join("; ", collector.Errors),
            new JObject { ["errors"] = new JArray(collector.Errors), ["warnings"] = new JArray(collector.Warnings) });
        }
        if (collector.Warnings.Count > 0 && result is JObject o) o["revit_warnings"] = new JArray(collector.Warnings);
        return result;
      }
    }
  }
}
