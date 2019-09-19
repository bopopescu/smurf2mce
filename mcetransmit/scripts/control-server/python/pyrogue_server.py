#!/usr/bin/env python3
#-----------------------------------------------------------------------------
# Title      : PyRogue Server
#-----------------------------------------------------------------------------
# File       : python/pyrogue_server.py
# Created    : 2017-06-20
#-----------------------------------------------------------------------------
# Description:
# Python script to start a PyRogue Control Server
#-----------------------------------------------------------------------------
# This file is part of the pyrogue-control-server software platform. It is subject to
# the license terms in the LICENSE.txt file found in the top-level directory
# of this distribution and at:
#    https://confluence.slac.stanford.edu/display/ppareg/LICENSE.html.
# No part of the rogue software platform, including this file, may be
# copied, modified, propagated, or distributed except according to the terms
# contained in the LICENSE.txt file.
#-----------------------------------------------------------------------------
import sys
import getopt
import socket
import os
import subprocess
import time
import struct
from packaging import version
from pathlib import Path
import threading

import pyrogue
import pyrogue.utilities.fileio
import rogue.interfaces.stream
import MceTransmit

# Print the usage message
def usage(name):
    print("Usage: {}".format(name))
    print("        [-a|--addr IP_address] [-s|--server] [-e|--epics prefix]")
    print("        [-n|--nopoll] [-c|--commType comm_type] [-l|--pcie-rssi-lane index]")
    print("        [-f|--stream-type data_type] [-b|--stream-size byte_size]")
    print("        [-d|--defaults config_file] [-u|--dump-pvs file_name] [--disable-gc]")
    print("        [--disable-bay0] [--disable-bay1] [-w|--windows-title title]")
    print("        [--pcie-dev-rssi pice_device] [--pcie-dev-data pice_device] [-h|--help]")
    print("")
    print("    -h|--help                   : Show this message")
    print("    -a|--addr IP_address        : FPGA IP address. Required when"\
        "the communication type is based on Ethernet.")
    print("    -d|--defaults config_file   : Default configuration file")
    print("    -e|--epics prefix           : Start an EPICS server with",\
        "PV name prefix \"prefix\"")
    print("    -s|--server                 : Server mode, without staring",\
        "a GUI (Must be used with -p and/or -e)")
    print("    -n|--nopoll                 : Disable all polling")
    print("    -c|--commType comm_type     : Communication type with the FPGA",\
        "(defaults to \"eth-rssi-non-interleaved\"")
    print("    -l|--pcie-rssi-lane index   : PCIe RSSI lane (only needed with"\
        "PCIe). Supported values are 0 to 5")
    print("    -b|--stream-size data_size  : Expose the stream data as EPICS",\
        "PVs. Only the first \"data_size\" points will be exposed.",\
        "(Must be used with -e)")
    print("    -f|--stream-type data_type  : Stream data type (UInt16, Int16,",\
        "UInt32 or Int32). Default is UInt16. (Must be used with -e and -b)")
    print("    -u|--dump-pvs file_name     : Dump the PV list to \"file_name\".",\
        "(Must be used with -e)")
    print("    --disable-bay0              : Disable the instantiation of the"\
        "devices for Bay0")
    print("    --disable-bay1              : Disable the instantiation of the"\
        "devices for Bay1")
    print("    --disable-gc                : Disable python's garbage collection"\
        "(enabled by default)")
    print("    -w|--windows-title title    : Set the GUI windows title. If not"\
        "specified, the default windows title will be the name of this script."\
        "This value will be ignored when running in server mode.")
    print("    --pcie-dev-rssi pice_device : Set the PCIe card device name"\
        "used for RSSI (defaults to '/dev/datadev_0')")
    print("    --pcie-dev-data pice_device : Set the PCIe card device name"\
        "used for data (defaults to '/dev/datadev_1')")
    print("")
    print("Examples:")
    print("    {} -a IP_address              :".format(name),\
        " Start a local rogue server, with GUI, without an EPICS servers")
    print("    {} -a IP_address -e prefix    :".format(name),\
        " Start a local rogue server, with GUI, with and EPICS server")
    print("    {} -a IP_address -e prefix -s :".format(name),\
        " Start a local rogue server, without GUI, with an EPICS servers")
    print("")

# Create gui interface
def create_gui(root, title=""):
    app_top = pyrogue.gui.application(sys.argv)
    app_top.setApplicationName(title)
    gui_top = pyrogue.gui.GuiTop(group='GuiTop')
    gui_top.addTree(root)
    print("Starting GUI...\n")

    try:
        app_top.exec_()
    except KeyboardInterrupt:
        # Catch keyboard interrupts while the GUI was open
        pass

    print("GUI was closed...")

# Exit with a error message
def exit_message(message):
    print(message)
    print("")
    exit()

class KeepAlive(rogue.interfaces.stream.Master, threading.Thread):
    """
    Class used to keep alive the streaming data UDP connection.

    It is a Rogue Master device, which will be connected to the
    UDP Client slave.

    It will run a thread which will send an UDP packet every
    5 seconds to avoid the connection to be closed. After
    instantiate an object of this class, and connect it to the
    UDP Client slave, its 'start()' method must be called to
    start the thread itself.
    """
    def __init__(self):
        super().__init__()
        threading.Thread.__init__(self)

        # Define the thread as a daemon so it is killed as
        # soon as the main program exits.
        self.daemon = True

        # Request a 1-byte frame from the slave.
        self.frame = self._reqFrame(1, True)

        # Create a 1-byte element to be sent to the
        # slave. The content of the packet is not
        # important.
        self.ba = bytearray(1)

    def run(self):
        """
        This method is called the the class' 'start()'
        method is called.

        It implements an infinite loop that send an UDP
        packet every 5 seconds.
        """
        while True:
            self.frame.write(self.ba,0)
            self._sendFrame(self.frame)
            time.sleep(5)

class LocalServer(pyrogue.Root):
    """
    Local Server class. This class configure the whole rogue application.
    """
    def __init__(self, ip_addr, config_file, server_mode, epics_prefix,\
        polling_en, comm_type, pcie_rssi_lane, stream_pv_size, stream_pv_type,\
        pv_dump_file, disable_bay0, disable_bay1, disable_gc, windows_title,\
        pcie_dev_rssi, pcie_dev_data):

        try:
            pyrogue.Root.__init__(self, name='AMCc', description='AMC Carrier')

            # File writer for streaming interfaces
            # DDR interface (TDEST 0x80 - 0x87)
            stm_data_writer = pyrogue.utilities.fileio.StreamWriter(name='streamDataWriter')
            self.add(stm_data_writer)
            # Streaming interface (TDEST 0xC0 - 0xC7)
            stm_interface_writer = pyrogue.utilities.fileio.StreamWriter(name='streamingInterface')
            self.add(stm_interface_writer)

            # Workaround to FpgaTopLelevel not supporting rssi = None
            if pcie_rssi_lane == None:
                pcie_rssi_lane = 0

            # Instantiate Fpga top level
            fpga = FpgaTopLevel(ipAddr=ip_addr,
                commType=comm_type,
                pcieRssiLink=pcie_rssi_lane,
                disableBay0=disable_bay0,
                disableBay1=disable_bay1)

            # Add devices
            self.add(fpga)

            # Create stream interfaces
            self.ddr_streams = []       # DDR streams


            # Our smurf2mce receiver
            # The data stream comes from TDEST 0xC1
            self.smurf2mce = MceTransmit.Smurf2MCE()
            self.smurf2mce.setDebug( False )

            # Check if we are using PCIe or Ethernet communication.
            if 'pcie-' in comm_type:
                # If we are suing PCIe communication, used AxiStreamDmas to get the DDR and streaming streams.

                # DDR streams. We are only using the first 2 channel of each AMC daughter card, i.e.
                # channels 0, 1, 4, 5.
                for i in [0, 1, 4, 5]:
                    self.ddr_streams.append(
                        rogue.hardware.axi.AxiStreamDma(pcie_dev_rssi,(pcie_rssi_lane*0x100 + 0x80 + i), True))

                # Streaming interface stream
                self.streaming_stream =
                    rogue.hardware.axi.AxiStreamDma(pcie_dev_data,(pcie_rssi_lane*0x100 + 0xC1), True)

                # When PCIe communication is used, we connect the stream data directly to the receiver:
                # Stream -> smurf2mce receiver
                pyrogue.streamConnect(self.streaming_stream, self.smurf2mce)

            else:
                # If we are using Ethernet: DDR streams comes over the RSSI+packetizer channel, and
                # the streaming streams comes over a pure UDP channel.

                # DDR streams. The FpgaTopLevel class will defined a 'stream' interface exposing them.
                # We are only using the first 2 channel of each AMC daughter card, i.e. channels 0, 1, 4, 5.
                for i in [0, 1, 4, 5]:
                    self.ddr_streams.append(fpga.stream.application(0x80 + i))

                # Streaming interface stream. It comes over UDP, port 8195, without RSSI,
                # so we use an UDP Client receiver.
                self.streaming_stream = rogue.protocols.udp.Client(ip_addr, 8195, True)

                # When Ethernet communication is used, We use a FIFO between the stream data and the receiver:
                # Stream -> FIFO -> smurf2mce receiver
                self.smurf2mce_fifo = rogue.interfaces.stream.Fifo(1000,0,True)
                pyrogue.streamConnect(self.streaming_stream, self.smurf2mce_fifo)
                pyrogue.streamConnect(self.smurf2mce_fifo, self.smurf2mce)

                # Create a KeepAlive object and connect it to the UDP Client.
                # It is used to keep the UDP connection open. This in only needed when
                # using Ethernet communication, as the PCIe FW implements this functionality.
                self.keep_alive = KeepAlive()
                pyrogue.streamConnect(self.keep_alive, self.streaming_stream)
                # Start the KeepAlive thread
                self.keep_alive.start()

            # Add data streams (0-3) to file channels (0-3)
            for i in range(4):

                ## DDR streams
                pyrogue.streamConnect(self.ddr_streams[i],
                    stm_data_writer.getChannel(i))

            ## Streaming interface streams
            # We have already connected it to the smurf2mce receiver,
            # so we need to tapping it to the data writer.
            pyrogue.streamTap(self.streaming_stream, stm_interface_writer.getChannel(0))

            # Run control for streaming interfaces
            self.add(pyrogue.RunControl(
                name='streamRunControl',
                description='Run controller',
                cmd=fpga.SwDaqMuxTrig,
                rates={
                    1:  '1 Hz',
                    10: '10 Hz',
                    30: '30 Hz'}))

            # lcaPut limits the maximun lenght of a string to 40 chars, as defined
            # in the EPICS R3.14 CA reference manual. This won't allowed to use the
            # command 'ReadConfig' with a long file path, which is usually the case.
            # This function is a workaround to that problem. Fomr matlab one can
            # just call this function without arguments an the function ReadConfig
            # will be called with a predefined file passed during startup
            # However, it can be usefull also win the GUI, so it is always added.
            self.config_file = config_file
            self.add(pyrogue.LocalCommand(
                name='setDefaults',
                description='Set default configuration',
                function=self.set_defaults_cmd))

            # If Garbage collection was disable, add this local variable to allow users
            # to manually run the garbage collection.
            if disable_gc:
                self.add(pyrogue.LocalCommand(
                    name='runGarbageCollection',
                    description='runGarbageCollection',
                    function=self.run_garbage_collection))

            self.add(pyrogue.LocalVariable(
                name='mcetransmitDebug',
                description='Enable mce transmit debug',
                mode='RW',
                value=False,
                localSet=lambda value: self.smurf2mce.setDebug(value),
                hidden=False))

            # Lost frame counter from smurf2mce
            self.add(pyrogue.LocalVariable(
                name='frameLossCnt',
                description='Lost frame Counter',
                mode='RO',
                value=0,
                localGet=self.smurf2mce.getFrameLossCnt,
                pollInterval=1,
                hidden=False))

            # Received frame counter from smurf2mce
            self.add(pyrogue.LocalVariable(
                name='frameRxCnt',
                description='Received frame Counter',
                mode='RO',
                value=0,
                localGet=self.smurf2mce.getFrameRxCnt,
                pollInterval=1,
                hidden=False))

            # Out-of-order frame counter from smurf2mce
            self.add(pyrogue.LocalVariable(
                name='frameOutOrderCnt',
                description='Number of time out-of-order frames are detected',
                mode='RO',
                value=0,
                localGet=self.smurf2mce.getFrameOutOrderCnt,
                pollInterval=1,
                hidden=False))

            # Bad frame counter
            self.add(pyrogue.LocalVariable(
                name='badFrameCnt',
                description='Number of lost frames due to a bad frame',
                mode='RO',
                value=0,
                localGet=self.smurf2mce.getBadFrameCnt,
                pollInterval=1,
                hidden=False))

            # Command to clear all the frame counters on smurf2mce
            self.add(pyrogue.LocalCommand(
                name='clearFrameCnt',
                description='Clear all the frame counters',
                function=self.smurf2mce.clearFrameCnt))

            # Start the root
            print("Starting rogue server")
            self.start(pollEn=polling_en)

            self.ReadAll()

        except KeyboardInterrupt:
            print("Killing server creation...")
            super(LocalServer, self).stop()
            exit()

        # Show image build information
        try:
            print("")
            print("FPGA image build information:")
            print("===================================")
            print("BuildStamp              : {}"\
                .format(self.FpgaTopLevel.AmcCarrierCore.AxiVersion.BuildStamp.get()))
            print("FPGA Version            : 0x{:x}"\
                .format(self.FpgaTopLevel.AmcCarrierCore.AxiVersion.FpgaVersion.get()))
            print("Git hash                : 0x{:x}"\
                .format(self.FpgaTopLevel.AmcCarrierCore.AxiVersion.GitHash.get()))
        except AttributeError as attr_error:
            print("Attibute error: {}".format(attr_error))
        print("")

        # Start the EPICS server
        if epics_prefix:
            print("Starting EPICS server using prefix \"{}\"".format(epics_prefix))

            self.epics = pyrogue.protocols.epics.EpicsCaServer(base=epics_prefix, root=self)

            # PVs for stream data
            if stream_pv_size:

                print("Enabling stream data on PVs (buffer size = {} points, data type = {})"\
                    .format(stream_pv_size,stream_pv_type))

                self.stream_fifos  = []
                self.stream_slaves = []
                for i in range(4):
                    self.stream_slaves.append(self.epics.createSlave(name="AMCc:Stream{}".format(i), maxSize=stream_pv_size, type=stream_pv_type))

                    # Calculate number of bytes needed on the fifo
                    if '16' in stream_pv_type:
                        fifo_size = stream_pv_size * 2
                    else:
                        fifo_size = stream_pv_size * 4

                    self.stream_fifos.append(rogue.interfaces.stream.Fifo(1000, fifo_size, True)) # changes
                    self.stream_fifos[i]._setSlave(self.stream_slaves[i])
                    pyrogue.streamTap(self.ddr_streams[i], self.stream_fifos[i])

            self.epics.start()

            # Dump the PV list to the specified file
            if pv_dump_file:
                try:
                    # Try to open the output file
                    f = open(pv_dump_file, "w")
                except IOError:
                    print("Could not open the PV dump file \"{}\"".format(pv_dump_file))
                else:
                    with f:
                        print("Dumping PV list to \"{}\"...".format(pv_dump_file))
                        try:
                            try:
                                # Redirect the stdout to the output file momentarily
                                original_stdout, sys.stdout = sys.stdout, f
                                self.epics.dump()
                            finally:
                                sys.stdout = original_stdout

                            print("Done!")
                        except:
                            # Capture error from epics.dump() if any
                            print("Errors were found during epics.dump()")

        # If no in server Mode, start the GUI
        if not server_mode:
            create_gui(self, title=windows_title)
        else:
            # Stop the server when Crtl+C is pressed
            print("")
            print("Running in server mode now. Press Ctrl+C to stop...")
            try:
                # Wait for Ctrl+C
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

    # Function for setting a default configuration.
    def set_defaults_cmd(self):
        # Check if a default configuration file has been defined
        if not self.config_file:
            print('No default configuration file was specified...')
            return

        print('Setting defaults from file {}'.format(self.config_file))
        self.ReadConfig(self.config_file)

    def stop(self):
        print("Stopping servers...")
        if hasattr(self, 'epics'):
            print("Stopping EPICS server...")
            self.epics.stop()
        super(LocalServer, self).stop()

    def run_garbage_collection(self):
        print("Running garbage collection...")
        gc.collect()
        print( gc.get_stats() )

class PcieDev():
    """
    Class to setup each PCIe device

    This class contains wrapper to facilitate the process of setting
    up each PCIe device independently by the PcieCard class.

    This class must be used in a 'with' block in order to ensure that the
    RSSI connection is close correctly during exit even in the case of an
    exception condition.

    """
    def __init__(self, dev, name, description):
        import rogue.hardware.axi
        import SmurfKcu1500RssiOffload as fpga
        self.root = pyrogue.Root(name=name,description=description)
        memMap = rogue.hardware.axi.AxiMemMap(dev)
        self.root.add(fpga.Core(memBase=memMap))

    def __enter__(self):
        self.root.start(pollEn='False',initRead='True')
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.root.stop()

    def get_id(self):
        """
        Get the Device ID
        """
        return int(self.root.Core.AxiPcieCore.AxiVersion.DeviceId.get())

    def get_local_mac(self, lane):
        """
        Get the local MAC address for the specified Ethernet lane.
        """
        return self.root.Core.EthPhyGrp.EthConfig[lane].LocalMac.get()

    def set_local_mac(self, lane, mac):
        """
        Set the local MAC address for the specified Ethernet lane.
        """
        return self.root.Core.EthPhyGrp.EthConfig[lane].LocalMac.set(mac)

    def get_local_ip(self, lane):
        """
        Get the local IP address for the specified Ethernet lane.
        """
        return self.root.Core.EthPhyGrp.EthConfig[lane].LocalIp.get()

    def set_local_ip(self, lane, ip):
        """
        Set the local IP address for the specified Ethernet lane.
        """
        return self.root.Core.EthPhyGrp.EthConfig[lane].LocalIp.set(ip)

    def get_remote_ip(self, lane, client):
        """
        Get the remote IP address for the specified Ethernet lane.
        """
        return self.root.Core.UdpGrp.UdpEngine[lane].ClientRemoteIp[client].get()

    def set_remote_ip(self, lane, client, ip):
        """
        Set the remote IP address for the specified Ethernet lane.
        """
        return self.root.Core.UdpGrp.UdpEngine[lane].ClientRemoteIp[client].set(ip)

    def open_lane(self, lane, ip):
        """
        Open the RSSI connection on the specified lane, using the specified IP address.
        """
        print("    Opening PCIe RSSI lane {}".format(lane))
        self.root.Core.UdpGrp.UdpConfig[lane].EnKeepAlive.set(1)
        self.root.Core.UdpGrp.UdpConfig[lane].KeepAliveConfig.set(0x2E90EDD0)  # 5 seconds
        self.root.Core.UdpGrp.RssiClient[lane].OpenConn.set(1)
        self.root.Core.UdpGrp.RssiClient[lane].CloseConn.set(0)
        self.root.Core.UdpGrp.UdpEngine[lane].ClientRemoteIp[0].set(ip)
        self.root.Core.UdpGrp.UdpEngine[lane].ClientRemoteIp[1].set(ip)
        self.root.Core.UdpGrp.UdpEngine[lane].ClientRemotePort[0].set(8198)
        self.root.Core.UdpGrp.UdpEngine[lane].ClientRemotePort[1].set(8195)

        # Print register status after setting them
        self.__print_lane_registers(lane)

    def close_lane(self, lane):
        """
        Close the RSSI connection on the specified Ethernet lane.
        """
        print("    Closing PCIe RSSI lane {}".format(lane))
        self.root.Core.UdpGrp.UdpConfig[lane].KeepAliveConfig.set(0)
        self.root.Core.UdpGrp.RssiClient[lane].OpenConn.set(0)
        self.root.Core.UdpGrp.RssiClient[lane].CloseConn.set(1)
        self.root.Core.UdpGrp.UdpEngine[lane].ClientRemotePort[0].set(0)
        self.root.Core.UdpGrp.UdpEngine[lane].ClientRemotePort[1].set(8192)

        # Print register status after setting them
        self.__print_lane_registers(lane)

    def __print_lane_registers(self, lane):
        """
        Print the register for the specified Ethernet lane.
        """
        print("      PCIe register status:")
        print("      Core.UdpGrp.UdpConfig[{}].EnKeepAlive         = {}".format(lane,
            self.root.Core.UdpGrp.UdpConfig[lane].EnKeepAlive.get()))
        print("      Core.UdpGrp.UdpConfig[{}].KeepAliveConfig     = 0x{:02X}".format(lane,
            self.root.Core.UdpGrp.UdpConfig[lane].KeepAliveConfig.get()))
        print("      Core.UdpGrp.RssiClient[{}].OpenConn           = {}".format(lane,
            self.root.Core.UdpGrp.RssiClient[lane].OpenConn.get()))
        print("      Core.UdpGrp.RssiClient[{}].CloseConn          = {}".format(lane,
            self.root.Core.UdpGrp.RssiClient[lane].CloseConn.get()))
        print("      Core.UdpGrp.UdpEngine[{}].ClientRemotePort[0] = {}".format(lane,
            self.root.Core.UdpGrp.UdpEngine[lane].ClientRemotePort[0].get()))
        print("      Core.UdpGrp.UdpEngine[{}].ClientRemoteIp[0]   = {}".format(lane,
            self.root.Core.UdpGrp.UdpEngine[lane].ClientRemoteIp[0].get()))
        print("      Core.UdpGrp.UdpEngine[{}].ClientRemotePort[1] = {}".format(lane,
            self.root.Core.UdpGrp.UdpEngine[lane].ClientRemotePort[1].get()))
        print("      Core.UdpGrp.UdpEngine[{}].ClientRemoteIp[1]   = {}".format(lane,
            self.root.Core.UdpGrp.UdpEngine[lane].ClientRemoteIp[1].get()))
        print("      Core.EthPhyGrp.EthConfig[{}].LocalMac         = {}".format(lane,
            self.root.Core.EthPhyGrp.EthConfig[lane].LocalMac.get()))
        print("      Core.EthPhyGrp.EthConfig[{}].LocalIp          = {}".format(lane,
            self.root.Core.EthPhyGrp.EthConfig[lane].LocalIp.get()))
        print("")

    def print_version(self):
        """
        Print the PCIe device firmware information.
        """
        print("  ==============================================================")
        print("                   {}".format(self.root.description))
        print("  ==============================================================")
        print("    FW Version      : 0x{:08X}".format(
            self.root.Core.AxiPcieCore.AxiVersion.FpgaVersion.get()))
        print("    FW GitHash      : 0x{:040X}".format(
            self.root.Core.AxiPcieCore.AxiVersion.GitHash.get()))
        print("    FW image name   : {}".format(
            self.root.Core.AxiPcieCore.AxiVersion.ImageName.get()))
        print("    FW build env    : {}".format(
            self.root.Core.AxiPcieCore.AxiVersion.BuildEnv.get()))
        print("    FW build server : {}".format(
            self.root.Core.AxiPcieCore.AxiVersion.BuildServer.get()))
        print("    FW build date   : {}".format(
            self.root.Core.AxiPcieCore.AxiVersion.BuildDate.get()))
        print("    FW builder      : {}".format(
            self.root.Core.AxiPcieCore.AxiVersion.Builder.get()))
        print("    Up time         : {}".format(
            self.root.Core.AxiPcieCore.AxiVersion.UpTime.get()))
        print("    Xilinx DNA ID   : 0x{:032X}".format(
            self.root.Core.AxiPcieCore.AxiVersion.DeviceDna.get()))
        print("    Device ID       : {}".format(
            self.root.Core.AxiPcieCore.AxiVersion.DeviceId.get()))
        print("  ==============================================================")
        print("")

class PcieCard():
    """
    Class to setup the PCIe card devices.

    This class takes care of setting up both PCIe card devices according to the
    communication type used.

    If the PCIe card is present in the system:
    - All the RSSI connection lanes which point to the target IP address will
      be closed.
    - If PCIe communication type is used:
        - Verify that the DeviceId are correct for the RSSI (ID = 0) and the DATA
          (ID = 1) devices.
        - the RSSI connection is open in the specific lane. Also, when the the server
          is closed, the RSSI connection is closed.
    -

    If the PCIe card is not present:
    - If PCIe communication type is used, the program is terminated.
    - If ETH communication type is used, then this class does not do anything.

    This class must be used in a 'with' block in order to ensure that the
    RSSI connection is close correctly during exit even in the case of an
    exception condition.
    """

    def __init__(self, comm_type, lane, ip_addr, dev_rssi, dev_data):

        print("Setting up the RSSI PCIe card...")

        # Get system status:

        # Check if the PCIe card for RSSI is present in the system
        if Path(dev_rssi).exists():
            self.pcie_rssi_present = True
            self.pcie_rssi = PcieDev(dev=dev_rssi, name='pcie_rssi', description='PCIe for RSSI')
        else:
            self.pcie_rssi_present = False

        # Check if the PCIe card for DATA is present in the system
        if Path(dev_data).exists():
            self.pcie_data_present = True
            self.pcie_data = PcieDev(dev=dev_data, name='pcie_data', description='PCIe for DATA')
        else:
            self.pcie_data_present = False

        # Check if we use the PCIe for communication
        if 'pcie-' in comm_type:
            self.use_pcie = True
        else:
            self.use_pcie = False

        # We need the IP address when the PCIe card is present, but not in used too.
        # If the PCIe card is present, this value could be updated later.
        # We need to know the IP address so we can look for all RSSI lanes that point
        # to it and close their connections.
        self.ip_addr = ip_addr

        # Look for configuration errors:

        if self.use_pcie:
            # Check if we are trying to use PCIe communication without the PCIe
            # cards present in the system
            if not self.pcie_rssi_present:
                exit_message("  ERROR: PCIe device {} not present.".format(dev_rssi))

            if not self.pcie_data_present:
                exit_message("  ERROR: PCIe device {} not present.".format(dev_data))

            # Verify the lane number is valid
            if lane == None:
                exit_message("  ERROR: Must specify an RSSI lane number")

            if lane in range(0, 6):
                self.lane = lane
            else:
                exit_message("  ERROR: Invalid RSSI lane number. Must be between 0 and 5")

            # We should need to check that the IP address is defined when PCIe is present
            # and not in used, but that is enforce in the main function.

            # Not more configuration errors at this point

            # Prepare the PCIe (DATA)
            with self.pcie_data as pcie:
                # Verify that its DeviceID is correct
                dev_data_id = pcie.get_id()
                if dev_data_id != 1:
                    exit_message("  ERROR: The DeviceId for the PCIe dev for DATA is {} instead "\
                        "of 1. Choose the correct device.".format(dev_data_id))

                # Print FW information
                pcie.print_version()


            # Prepare the PCIe (RSSI)
            with self.pcie_rssi as pcie:
                # Verify that its DeviceID is correct
                dev_rssi_id = pcie.get_id()
                if dev_rssi_id != 0:
                    exit_message("  ERROR: The DeviceId for the PCIe dev for RSSI is {} instead "\
                        "of 0. Choose the correct device.".format(dev_rssi_id))

                # Print FW information
                pcie.print_version()

                # Verify if the PCIe card is configured with a MAC and IP address.
                # If not, load default values before it can be used.
                valid_local_mac_addr = True
                local_mac_addr = pcie.get_local_mac(lane=self.lane)
                if local_mac_addr == "00:00:00:00:00:00":
                    valid_local_mac_addr = False
                    pcie.set_local_mac(lane=self.lane, mac="08:00:56:00:45:5{}".format(lane))
                    local_mac_addr = pcie.get_local_mac(lane=self.lane)

                valid_local_ip_addr = True
                local_ip_addr = pcie.get_local_ip(lane=self.lane)
                if local_ip_addr == "0.0.0.0":
                    valid_local_ip_addr = False
                    pcie.set_local_ip(lane=self.lane, ip="10.0.1.20{}".format(lane))
                    local_ip_addr = pcie.get_local_ip(lane=self.lane)


                # If the IP was not defined, read the one from the register space.
                # Note: this could be the case only the PCIe is in use.
                if not ip_addr:
                    ip_addr = pcie.get_remote_ip(lane=self.lane, client=0)

                    # Check if the IP address read from the PCIe card is valid
                    try:
                        socket.inet_pton(socket.AF_INET, ip_addr)
                    except socket.error:
                        exit_message("ERROR: IP Address read from the PCIe card: {} is invalid.".format(ip_addr))

                # Update the IP address.
                # Note: when the PCIe card is not in use, the IP will be defined
                # by the user.
                self.ip_addr = ip_addr

        # Print system configuration and status
        print("  - PCIe for RSSI present in the system    : {}".format(
            "Yes" if self.pcie_rssi_present else "No"))
        print("  - PCIe for Data present in the system    : {}".format(
            "Yes" if self.pcie_data_present else "No"))
        print("  - PCIe based communicartion selected     : {}".format(
            "Yes" if self.use_pcie else "No"))

        # Show IP address and lane when the PCIe is in use
        if self.use_pcie:
            print("  - Valid MAC address                      : {}".format(
                "Yes" if valid_local_mac_addr else "No. A default address was loaded"))
            print("  - Valid IP address                       : {}".format(
                "Yes" if valid_local_ip_addr else "No. A default address was loaded"))
            print("  - Local MAC address:                     : {}".format(local_mac_addr))
            print("  - Local IP address:                      : {}".format(local_ip_addr))
            print("  - Using IP address                       : {}".format(self.ip_addr))
            print("  - Using RSSI lane number                 : {}".format(self.lane))
            print("")

        # When the PCIe card is not present we don't do anything

    def __enter__(self):
        # Check if the PCIe card is present. If not, do not do anything.
        if self.pcie_rssi_present:

            # Close all RSSI lanes that point to the target IP address
            self.__close_all_rssi()

            # Check if the PCIe card is used. If not, do not do anything.
            if self.use_pcie:

                # Open the RSSI lane
                with self.pcie_rssi as pcie:
                    pcie.open_lane(lane=self.lane, ip=self.ip_addr)

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # Check if the PCIe card is present. If not, do not do anything.
        if self.pcie_rssi_present:

            # Check if the PCIe card is used. If not, do not do anything.
            if self.use_pcie:

                # Close the RSSI lane before exit,
                with self.pcie_rssi as pcie:
                    pcie.close_lane(self.lane)

    def __close_all_rssi(self):
        """
        Close all lanes with the target IP address
        """

        # Check if the PCIe is present
        if self.pcie_rssi_present:
            with self.pcie_rssi as pcie:
                print("  * Looking for RSSI lanes pointing to {}...".format(self.ip_addr))
                # Look for links with the target IP address, and close their RSSI connection
                for i in range(6):
                    if self.ip_addr == pcie.get_remote_ip(lane=i, client=0):
                        print("    Lane {} points to it. Disabling it...".format(i))
                        pcie.close_lane(i)
                        print("")
                print("  Done!")
                print("")

# Main body
if __name__ == "__main__":
    ip_addr = ""
    epics_prefix = ""
    config_file = ""
    server_mode = False
    polling_en = True
    stream_pv_size = 0
    stream_pv_type = "UInt16"
    stream_pv_valid_types = ["UInt16", "Int16", "UInt32", "Int32"]
    comm_type = "eth-rssi-non-interleaved";
    comm_type_valid_types = ["eth-rssi-non-interleaved", "eth-rssi-interleaved", "pcie-rssi-interleaved"]
    pcie_rssi_lane=None
    pv_dump_file= ""
    pcie_dev_rssi="/dev/datadev_0"
    pcie_dev_data="/dev/datadev_1"
    disable_bay0=False
    disable_bay1=False
    disable_gc=False
    windows_title=""

    # Only Rogue version >= 2.6.0 are supported. Before this version the EPICS
    # interface was based on PCAS which is not longer supported.
    try:
        ver = pyrogue.__version__
        if (version.parse(ver) <= version.parse('2.6.0')):
            raise ImportError('Rogue version <= 2.6.0 is unsupported')
    except AttributeError:
        print("Error when trying to get the version of Rogue")
        pritn("Only version of Rogue > 2.6.0 are supported")
        raise

    # Read Arguments
    try:
        opts, _ = getopt.getopt(sys.argv[1:],
            "ha:se:d:nb:f:c:l:u:w:",
            ["help", "addr=", "server", "epics=", "defaults=", "nopoll",
            "stream-size=", "stream-type=", "commType=", "pcie-rssi-lane=", "dump-pvs=",
            "disable-bay0", "disable-bay1", "disable-gc", "windows-title=", "pcie-dev-rssi=",
            "pcie-dev-data="])
    except getopt.GetoptError:
        usage(sys.argv[0])
        sys.exit()

    for opt, arg in opts:
        if opt in ("-h", "--help"):
            usage(sys.argv[0])
            sys.exit()
        elif opt in ("-a", "--addr"):        # IP Address
            ip_addr = arg
        elif opt in ("-s", "--server"):      # Server mode
            server_mode = True
        elif opt in ("-e", "--epics"):       # EPICS prefix
            epics_prefix = arg
        elif opt in ("-n", "--nopoll"):      # Disable all polling
            polling_en = False
        elif opt in ("-b", "--stream-size"): # Stream data size (on PVs)
            try:
                stream_pv_size = int(arg)
            except ValueError:
                exit_message("ERROR: Invalid stream PV size")
        elif opt in ("-f", "--stream-type"): # Stream data type (on PVs)
            if arg in stream_pv_valid_types:
                stream_pv_type = arg
            else:
                print("Invalid data type. Using {} instead".format(stream_pv_type))
        elif opt in ("-d", "--defaults"):   # Default configuration file
            config_file = arg
        elif opt in ("-c", "--commType"):   # Communication type
            if arg in comm_type_valid_types:
                comm_type = arg
            else:
                print("Invalid communication type. Valid choises are:")
                for c in comm_type_valid_types:
                    print("  - \"{}\"".format(c))
                exit_message("ERROR: Invalid communication type")
        elif opt in ("-l", "--pcie-rssi-lane"):       # PCIe RSSI Lane
            pcie_rssi_lane = int(arg)
        elif opt in ("-u", "--dump-pvs"):   # Dump PV file
            pv_dump_file = arg
        elif opt in ("--disable-bay0"):
            disable_bay0 = True
        elif opt in ("--disable-bay1"):
            disable_bay1 = True
        elif opt in ("--disable-gc"):
            disable_gc = True
        elif opt in ("-w", "--windows-title"):
            windows_title = arg
        elif opt in ("--pcie-dev-rssi"):
            pcie_dev_rssi = arg
        elif opt in ("--pcie-dev-data"):
            pcie_dev_data = arg

    # Disable garbage collection if requested
    if disable_gc:
        import gc
        gc.disable()
        print("GARBAGE COLLECTION DISABLED")

    # Verify if IP address is valid
    if ip_addr:
        try:
            socket.inet_pton(socket.AF_INET, ip_addr)
        except socket.error:
            exit_message("ERROR: Invalid IP Address.")

    # Check connection with the board if using eth communication
    if "eth-" in comm_type:
        if not ip_addr:
            exit_message("ERROR: Must specify an IP address for Ethernet base communication devices.")

        print("")
        print("Trying to ping the FPGA...")
        try:
           dev_null = open(os.devnull, 'w')
           subprocess.check_call(["ping", "-c2", ip_addr], stdout=dev_null, stderr=dev_null)
           print("    FPGA is online")
           print("")
        except subprocess.CalledProcessError:
           exit_message("    ERROR: FPGA can't be reached!")

    if server_mode and not (epics_prefix):
        exit_message("    ERROR: Can not start in server mode without the EPICS server enabled")

    # Try to import the FpgaTopLevel definition
    try:
        from FpgaTopLevel import FpgaTopLevel
    except ImportError as ie:
        print("Error importing FpgaTopLevel: {}".format(ie))
        exit()

    # If EPICS server is enable, import the epics module
    if epics_prefix:
        import pyrogue.protocols.epics

    # Import the QT and gui modules if not in server mode
    if not server_mode:
        import pyrogue.gui

    # The PCIeCard object will take care of setting up the PCIe card (if present)
    with PcieCard(lane=pcie_rssi_lane, comm_type=comm_type, ip_addr=ip_addr, dev_rssi=pcie_dev_rssi,
        dev_data=pcie_dev_data):

        # Start pyRogue server
        server = LocalServer(
            ip_addr=ip_addr,
            config_file=config_file,
            server_mode=server_mode,
            epics_prefix=epics_prefix,
            polling_en=polling_en,
            comm_type=comm_type,
            pcie_rssi_lane=pcie_rssi_lane,
            stream_pv_size=stream_pv_size,
            stream_pv_type=stream_pv_type,
            pv_dump_file=pv_dump_file,
            disable_bay0=disable_bay0,
            disable_bay1=disable_bay1,
            disable_gc=disable_gc,
            windows_title=windows_title,
            pcie_dev_rssi=pcie_dev_rssi,
            pcie_dev_data=pcie_dev_data)

    # Stop server
    server.stop()

    print("")
