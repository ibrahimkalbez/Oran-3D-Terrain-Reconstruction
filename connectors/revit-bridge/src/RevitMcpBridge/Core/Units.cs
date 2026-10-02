using System;
using Autodesk.Revit.DB;

namespace RevitMcpBridge.Core
{
  /// <summary>
  /// The bridge speaks SI to Claude (meters, m², m³, degrees); Revit stores feet and radians.
  /// The factors are exact definitions, identical in every Revit version.
  /// </summary>
  public static class U
  {
    public const double FootInMeters = 0.3048;
    public const double SqFootInSqMeters = FootInMeters * FootInMeters;
    public const double CuFootInCuMeters = FootInMeters * FootInMeters * FootInMeters;

    public static double ToFeet(double meters) => meters / FootInMeters;
    public static double ToMeters(double feet) => feet * FootInMeters;
    public static double ToM2(double sqFeet) => sqFeet * SqFootInSqMeters;
    public static double ToM3(double cuFeet) => cuFeet * CuFootInCuMeters;

    public static XYZ ToFeet(double x, double y, double z) => new XYZ(ToFeet(x), ToFeet(y), ToFeet(z));
    public static double[] ToMeters(XYZ p) => new[] { ToMeters(p.X), ToMeters(p.Y), ToMeters(p.Z) };

    public static double R(double v, int decimals = 4)
    {
      if (double.IsNaN(v) || double.IsInfinity(v)) return 0;
      return Math.Round(v, decimals);
    }

    /// <summary>Converts an internal value to SI according to the parameter's data type.</summary>
    public static double ToSI(double internalValue, ForgeTypeId spec)
    {
      if (spec == null || spec.Empty()) return internalValue;
      try
      {
        if (UnitUtils.IsMeasurableSpec(spec))
        {
          var unit = SiUnit(spec);
          if (unit != null) return UnitUtils.ConvertFromInternalUnits(internalValue, unit);
        }
      }
      catch
      {
        // not measurable
      }
      return internalValue;
    }

    /// <summary>Converts an SI value to Revit's internal unit for the parameter's data type.</summary>
    public static double FromSI(double siValue, ForgeTypeId spec)
    {
      if (spec == null || spec.Empty()) return siValue;
      try
      {
        if (UnitUtils.IsMeasurableSpec(spec))
        {
          var unit = SiUnit(spec);
          if (unit != null) return UnitUtils.ConvertToInternalUnits(siValue, unit);
        }
      }
      catch
      {
        // not measurable
      }
      return siValue;
    }

    /// <summary>The SI unit exposed for a spec, or null when the value is used as stored.</summary>
    public static ForgeTypeId SiUnit(ForgeTypeId spec)
    {
      if (spec == SpecTypeId.Length) return UnitTypeId.Meters;
      if (spec == SpecTypeId.Area) return UnitTypeId.SquareMeters;
      if (spec == SpecTypeId.Volume) return UnitTypeId.CubicMeters;
      if (spec == SpecTypeId.Angle) return UnitTypeId.Degrees;
      if (spec == SpecTypeId.Slope) return UnitTypeId.SlopeDegrees;
      return null;
    }

    public static string SiLabel(ForgeTypeId spec)
    {
      if (spec == SpecTypeId.Length) return "m";
      if (spec == SpecTypeId.Area) return "m²";
      if (spec == SpecTypeId.Volume) return "m³";
      if (spec == SpecTypeId.Angle || spec == SpecTypeId.Slope) return "°";
      return null;
    }
  }

  /// <summary>ElementId helpers that compile for Revit 2022–2026 (Value replaced IntegerValue in 2024).</summary>
  public static class Ids
  {
    public static long Of(ElementId id)
    {
#if NET8_0_OR_GREATER
      return id.Value;
#else
      return id.IntegerValue;
#endif
    }

    public static ElementId From(long value)
    {
#if NET8_0_OR_GREATER
      return new ElementId(value);
#else
      return new ElementId((int)value);
#endif
    }

    public static string Str(ElementId id) => Of(id).ToString();
  }
}
