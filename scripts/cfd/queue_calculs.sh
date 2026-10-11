#!/bin/bash
# Sequential CFD queue (one job at a time on the 4 cores). Log: /home/user/work/cfd/queue.log
S=/home/user/Oran-3D-Terrain-Reconstruction/scripts/cfd; C=/home/user/work/cfd/cases; P=/home/user/Oran-3D-Terrain-Reconstruction/results/cfd/post
Q=/home/user/work/cfd/queue.log
log(){ echo "$(date '+%F %T') $*" >> $Q; }
wait_done(){ until grep -q "$2" "$1" 2>/dev/null; do sleep 30; done; }
post(){ # case sid
  log "post $1"; python3 $S/45_postprocess.py $C/$1 $P/$1 > $C/$1/log.post 2>&1; log "post $1 exit $?"
  python3 $S/47_vues_perspective.py $P/$1 $2 $P/$1/vues > $C/$1/log.vues 2>&1; log "vues $1 exit $?"
}
solve(){ # mesh_case case sid [--thermal]
  mkdir -p $C/$2 && cp -r $C/$1/constant $C/$1/system $C/$2/ && rm -rf $C/$2/constant/polyMesh/sets
  python3 $S/44_case_openfoam.py setup $C/$2 $3 $4 > $C/$2/log.setup 2>&1 || { log "setup $2 FAILED"; return 1; }
  log "solve $2 start"; $C/$2/Allrun > $C/$2/run.out 2>&1; log "solve $2 end: $(grep -c '^Time' $C/$2/log.solver) iterations"
}
log "queue start"
wait_done $C/S6_coarse/run.out SOLVE_DONE; log "S6_coarse (emprises reelles) done"
CFD_GEO=/home/user/Oran-3D-Terrain-Reconstruction/results/cfd/geometrie_emprises_reelles post S6_coarse S6_ete_NNO
python3 $S/44_case_openfoam.py mesh $C/mesh_ref_coarse coarse > /dev/null; log "mesh_ref_coarse start"
$C/mesh_ref_coarse/Allmesh > $C/mesh_ref_coarse/run.out 2>&1; log "mesh_ref_coarse: $(grep -m1 'cells:' $C/mesh_ref_coarse/log.checkMesh.final)"
solve mesh_ref_coarse S6_ref_coarse S6_ete_NNO && post S6_ref_coarse S6_ete_NNO
solve mesh_ref_coarse S2_ref_coarse S2_SO && post S2_ref_coarse S2_SO
solve mesh_ref_coarse S6T_ref_coarse S6_ete_NNO --thermal && post S6T_ref_coarse S6_ete_NNO_thermique
python3 $S/44_case_openfoam.py mesh $C/mesh_ref_medium medium > /dev/null; log "mesh_ref_medium start"
$C/mesh_ref_medium/Allmesh > $C/mesh_ref_medium/run.out 2>&1; log "mesh_ref_medium: $(grep -m1 'cells:' $C/mesh_ref_medium/log.checkMesh.final)"
solve mesh_ref_medium S6_ref_medium S6_ete_NNO && post S6_ref_medium S6_ete_NNO
log "queue end"
