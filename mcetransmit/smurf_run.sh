
f="./log/$(date +"%FT%H%M%S")_smurf_run.log"
amcc_dump_bsi --all 10.0.1.30/2 |& tee $f
#amcc_dump_bsi --all 10.0.1.4/2 |& tee $f
#scripts/control-server/start_server.sh -a 10.0.2.102 -c eth-rssi-interleaved -l 0 -t /usr/local/controls/Applications/smurf/cmb_Det/cryo-det/ultrascale+/firmware/targets/MicrowaveMuxBpEthGen2/images/current.pyrogue.tar.gz -d /usr/local/controls/Applications/smurf/cmb_Det/cryo-det/ultrascale+/firmware/targets/MicrowaveMuxBpEthGen2/config/defaults.yml -e test_epics -f Int16 -b 524288
DEFAULTS_YML=/usr/local/controls/Applications/smurf/smurf_upgrade/smurf_cfg/defaults/defaults_keck2019_upgrade.yml
PYROGUE=/usr/local/controls/Applications/smurf/smurf_upgrade/MicrowaveMuxBpEthGen2-0x00000020-20191019135639-mdewart-6d670ac.pyrogue.tar.gz
CRATEID=2
#CRATEID=3
scripts/control-server/start_server.sh -a 10.0.${CRATEID}.102 -c pcie-rssi-interleaved -l 0 -t $PYROGUE -d $DEFAULTS_YML -e test_epics -f Int16 -b 524288

