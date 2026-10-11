"""
A02 - ANSYS Fluent solve of one Oran scenario with PyFluent (settings API, Fluent >= 2023 R1).

NOT EXECUTED in the preparation environment (no ANSYS there): to be run on the workstation
where Fluent is installed. Inputs come from the OpenFOAM preparation (identical mesh and
boundary data, so Fluent and OpenFOAM results can be compared one to one):

  <pkg>/mesh/oran_<level>.msh          mesh exported by foamMeshToFluent (zones: ground,
                                        buildings_CENTRE_VILLE, buildings_GAMBETTA,
                                        buildings_BD_DES_LIONS, west, east, south, north, top)
  <pkg>/scenarios/<id>/scenario.json   roles of the boundaries (inlet / outlet / slip), u*, z0
  <pkg>/scenarios/<id>/*.prof          Fluent profiles: inlet U,V,W,k,epsilon per face centre;
                                        thermal case: ground / roof temperatures (K)

Usage (Windows or Linux, ANSYS Fluent + ansys-fluent-core installed):
  python A02_pyfluent_solve.py <pkg_dir> <scenario_id> [--level medium] [--cores 8] [--iter 2000]
Writes <pkg>/runs/<id>/: case/data (.cas.h5/.dat.h5), residuals.csv, report.json, exports
(pedestrian-level and section data as CSV for Rhino re-import), log.txt.
"""
import argparse
import json
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg"); ap.add_argument("sid")
    ap.add_argument("--level", default="medium"); ap.add_argument("--cores", type=int, default=8)
    ap.add_argument("--iter", type=int, default=2000)
    a = ap.parse_args()
    import ansys.fluent.core as pyfluent

    pkg = Path(a.pkg); sdir = pkg / "scenarios" / a.sid
    meta = json.loads((sdir / "scenario.json").read_text())
    run = pkg / "runs" / a.sid; run.mkdir(parents=True, exist_ok=True)
    log = open(run / "log.txt", "a", encoding="utf-8")

    def L(msg):
        log.write(time.strftime("%H:%M:%S ") + msg + "\n"); log.flush(); print(msg)

    thermal = bool(meta.get("thermal"))
    L(f"launch Fluent 3ddp, {a.cores} cores, scenario {a.sid}, thermal={thermal}")
    s = pyfluent.launch_fluent(mode="solver", precision="double", processor_count=a.cores,
                               ui_mode="no_gui", dimension=3, cleanup_on_exit=True)
    s.file.read_mesh(file_name=str(pkg / "mesh" / f"oran_{a.level}.msh"))
    s.mesh.check(); s.mesh.quality()
    # --- physics
    s.setup.models.viscous.model = "k-epsilon"
    s.setup.models.viscous.k_epsilon_model = "realizable"
    s.setup.models.viscous.options.near_wall_treatment.wall_treatment = "standard-wall-fn"
    if thermal:
        s.setup.models.energy.enabled = True
        s.setup.general.operating_conditions.gravity = {"enable": True, "components": [0.0, 0.0, -9.81]}
        air = s.setup.materials.fluid["air"]
        air.density.option = "boussinesq"; air.density.value = 1.16
        air.thermal_expansion_coefficient.value = 3.3e-3
        s.setup.general.operating_conditions.operating_temperature = 273.15 + meta["thermal_bc"]["Tair_inflow_C"]
    for prof in sorted(sdir.glob("*.prof")):
        s.tui.file.read_profile(str(prof))
    bc = s.setup.boundary_conditions
    for patch, role in meta["roles"].items():
        if role == "inlet":
            bc.set_zone_type(zone_list=[patch], new_type="velocity-inlet")
            vi = bc.velocity_inlet[patch]
            vi.momentum.velocity_specification_method = "Components"
            vi.momentum.velocity_components = [
                {"option": "profile", "profile_name": f"inlet_{patch}", "field_name": "u"},
                {"option": "profile", "profile_name": f"inlet_{patch}", "field_name": "v"},
                {"option": "profile", "profile_name": f"inlet_{patch}", "field_name": "w"}]
            vi.turbulence.turbulence_specification = "K and Epsilon"
            vi.turbulence.turbulent_kinetic_energy = {"option": "profile", "profile_name": f"inlet_{patch}", "field_name": "k"}
            vi.turbulence.turbulent_dissipation_rate = {"option": "profile", "profile_name": f"inlet_{patch}", "field_name": "epsilon"}
            if thermal:
                vi.thermal.temperature = 273.15 + meta["thermal_bc"]["Tair_inflow_C"]
        elif role == "outlet":
            bc.set_zone_type(zone_list=[patch], new_type="pressure-outlet")
        else:
            bc.set_zone_type(zone_list=[patch], new_type="symmetry")
    bc.set_zone_type(zone_list=["top"], new_type="symmetry")
    # ground: aerodynamic roughness -> equivalent sand-grain height ks = 9.793 z0 / Cs (Blocken et al. 2007),
    # here the median z0 of each class is applied through the profile-free uniform value of the urban class
    g = bc.wall["ground"]
    g.momentum.roughness_models = "Standard"
    g.momentum.roughness_height = 9.793 * 0.05 / 0.5
    g.momentum.roughness_const = 0.5
    if thermal:
        for w in ["ground"] + [z for z in bc.wall.keys() if z.startswith("buildings_")]:
            bc.wall[w].thermal.thermal_condition = "Temperature"
            bc.wall[w].thermal.temperature = {"option": "profile", "profile_name": f"T_{w}", "field_name": "t"}
    # --- numerics (same as OpenFOAM: SIMPLE, second order momentum)
    s.solution.methods.p_v_coupling.flow_scheme = "SIMPLE"
    s.solution.methods.spatial_discretization.discretization_scheme = {"mom": "second-order-upwind", "k": "first-order-upwind",
                                                                       "epsilon": "first-order-upwind"}
    s.solution.monitor.residual.equations["continuity"].absolute_criteria = 1e-4
    s.solution.report_definitions.flux["mass_in"] = {"boundaries": [p for p, r in meta["roles"].items() if r == "inlet"]}
    s.solution.report_definitions.flux["mass_out"] = {"boundaries": [p for p, r in meta["roles"].items() if r == "outlet"] + ["top"]}
    s.solution.initialization.hybrid_initialize()
    L("iterate"); t0 = time.time()
    s.solution.run_calculation.iterate(iter_count=a.iter)
    L(f"done in {time.time() - t0:.0f} s")
    s.file.write(file_type="case-data", file_name=str(run / f"oran_{a.sid}"))
    # --- exports for Rhino (ASCII, x y z + fields): ground-following data are taken on the 'ground' zone
    # adjacent cells and on iso-surfaces z = const created here
    s.results.surfaces.iso_surface["z_10m"] = {"field": "z-coordinate", "iso_values": [10.0]}
    fields = ["velocity-magnitude", "x-velocity", "y-velocity", "z-velocity", "pressure"] + (["temperature"] if thermal else [])
    s.file.export.ascii(file_name=str(run / "export_z10m.csv"), surface_name_list=["z_10m"], delimiter="comma",
                        cell_func_domain=fields, location="node")
    for z in ["ground"] + [z for z in bc.wall.keys() if z.startswith("buildings_")]:
        s.file.export.ascii(file_name=str(run / f"export_{z}.csv"), surface_name_list=[z], delimiter="comma",
                            cell_func_domain=fields, location="node")
    rep = {"scenario": a.sid, "level": a.level, "iterations": a.iter, "thermal": thermal,
           "solver": "ANSYS Fluent (PyFluent)", "exports": sorted(p.name for p in run.glob("export_*.csv"))}
    (run / "report.json").write_text(json.dumps(rep, indent=1))
    L("exports written"); s.exit()


if __name__ == "__main__":
    main()
