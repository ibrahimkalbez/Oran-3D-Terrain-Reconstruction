#!/bin/bash
# After the divergence of S6T_ref_coarse: stabilised thermal run S6T2 (initialised from S6_ref_coarse),
# then the fine mesh + S6 fine, then the realistic wind clips. Log: /home/user/work/cfd/queue.log
S=/home/user/Oran-3D-Terrain-Reconstruction/scripts/cfd; V=/home/user/Oran-3D-Terrain-Reconstruction/scripts/video_cfd
C=/home/user/work/cfd/cases; P=/home/user/Oran-3D-Terrain-Reconstruction/results/cfd/post; Q=/home/user/work/cfd/queue.log
. /usr/share/openfoam/etc/bashrc 2>/dev/null; export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
log(){ echo "$(date '+%F %T') $*" >> $Q; }
post(){ log "post $1"; python3 $S/45_postprocess.py $C/$1 $P/$1 > $C/$1/log.post 2>&1; log "post $1 exit $?"
  python3 $S/47_vues_perspective.py $P/$1 $2 $P/$1/vues > $C/$1/log.vues 2>&1; log "vues $1 exit $?"; }
solve(){ mkdir -p $C/$2 && cp -r $C/$1/constant $C/$1/system $C/$2/ && rm -rf $C/$2/constant/polyMesh/sets
  python3 $S/44_case_openfoam.py setup $C/$2 $3 $4 $5 $6 > $C/$2/log.setup 2>&1 || { log "setup $2 FAILED"; return 1; }
  log "solve $2 start"; $C/$2/Allrun > $C/$2/run.out 2>&1; log "solve $2 end: $(grep -c '^Time' $C/$2/log.solver) iterations"; }
log "queue3 start"
solve mesh_ref_coarse S6T2_ref_coarse S6_ete_NNO --thermal --init-from $C/S6_ref_coarse && post S6T2_ref_coarse S6_ete_NNO_thermique
python3 $S/44_case_openfoam.py mesh $C/mesh_ref_medium medium > /dev/null; log "mesh_ref_medium start"
$C/mesh_ref_medium/Allmesh > $C/mesh_ref_medium/run.out 2>&1; log "mesh_ref_medium: $(grep -m1 'cells:' $C/mesh_ref_medium/log.checkMesh.final)"
solve mesh_ref_medium S6_ref_medium S6_ete_NNO && post S6_ref_medium S6_ete_NNO
log "queue end"
cd $V && nice -n 5 python3 49_clips_realistes.py /home/user/work/cfd/scene_realiste $P/S6_ref_coarse wind /home/user/work/clips > /home/user/work/clips/log_wind_S6.txt 2>&1
log "clips wind S6 done"
