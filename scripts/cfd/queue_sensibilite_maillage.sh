#!/bin/bash
# Mesh sensitivity (after the OOM of the full "medium" mesh, 9.4 M cells): level 4 around the Gambetta buildings only,
# then S6 on that mesh. Waits for the realistic wind clips (queue4) to finish. Log: /home/user/work/cfd/queue.log
S=/home/user/Oran-3D-Terrain-Reconstruction/scripts/cfd; C=/home/user/work/cfd/cases
P=/home/user/Oran-3D-Terrain-Reconstruction/results/cfd/post; Q=/home/user/work/cfd/queue.log
. /usr/share/openfoam/etc/bashrc 2>/dev/null; export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
log(){ echo "$(date '+%F %T') $*" >> $Q; }
post(){ log "post $1"; python3 $S/45_postprocess.py $C/$1 $P/$1 > $C/$1/log.post 2>&1; log "post $1 exit $?"
  python3 $S/47_vues_perspective.py $P/$1 $2 $P/$1/vues > $C/$1/log.vues 2>&1; log "vues $1 exit $?"; }
solve(){ mkdir -p $C/$2 && cp -r $C/$1/constant $C/$1/system $C/$2/ && rm -rf $C/$2/constant/polyMesh/sets
  python3 $S/44_case_openfoam.py setup $C/$2 $3 $4 $5 $6 > $C/$2/log.setup 2>&1 || { log "setup $2 FAILED"; return 1; }
  log "solve $2 start"; $C/$2/Allrun > $C/$2/run.out 2>&1; log "solve $2 end: $(grep -c '^Time' $C/$2/log.solver) iterations"; }
while pgrep -f 49_clips_realistes > /dev/null; do sleep 30; done
log "queue5 start (mesh sensitivity, Gambetta level 4)"
python3 $S/44_case_openfoam.py mesh $C/mesh_ref_fin_gam fin_gambetta > /dev/null
$C/mesh_ref_fin_gam/Allmesh > $C/mesh_ref_fin_gam/run.out 2>&1; log "mesh_ref_fin_gam: $(grep -m1 'cells:' $C/mesh_ref_fin_gam/log.checkMesh.final)"
solve mesh_ref_fin_gam S6_ref_fin_gam S6_ete_NNO && post S6_ref_fin_gam S6_ete_NNO
log "queue5 end"
