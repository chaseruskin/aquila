'''
Logic and control for interfacing with the Quartus FPGA toolchain.
'''

# Creates a Quartus project to execute any stage of the FPGA toolchain
# workflow. This script has the ability to override the top-level generics
# through the writing of a TCL script to eventually get called by Quartus.
#
# The script can auto-detect an Intel FPGA connected to the PC to program
# with a .pof or .sof bitstream file.
#
# References:
#   https://www.intel.co.jp/content/dam/altera-www/global/ja_JP/pdfs/literature/an/an312.pdf
#   https://community.intel.com/t5/Intel-Quartus-Prime-Software/Passing-parameter-generic-to-the-top-level-in-Quartus-tcl/td-p/239039

import os
import argparse
from enum import Enum
import sys
import toml

from aquila import env
from aquila import log
from aquila.env import KvPair, Manifest
from aquila.process import Command
from aquila.blueprint import Blueprint, Entry
from aquila.script import TclScript

TCL_CODE_PROJ_SETTINGS = '''\
# Set default configurations and device
set_global_assignment -name NUM_PARALLEL_PROCESSORS "ALL"
set_global_assignment -name VHDL_INPUT_VERSION VHDL_2008
set_global_assignment -name VERILOG_INPUT_VERSION SYSTEMVERILOG_2005
set_global_assignment -name EDA_SIMULATION_TOOL "ModelSim-Altera (VHDL)"
set_global_assignment -name EDA_OUTPUT_DATA_FORMAT "VHDL" -section_id EDA_SIMULATION
set_global_assignment -name EDA_GENERATE_FUNCTIONAL_NETLIST OFF -section_id EDA_SIMULATION
# Use single uncompressed image with memory initialization file
set_global_assignment -name EXTERNAL_FLASH_FALLBACK_ADDRESS 00000000
set_global_assignment -name USE_CONFIGURATION_DEVICE OFF
set_global_assignment -name INTERNAL_FLASH_UPDATE_MODE "SINGLE IMAGE WITH ERAM" 
# Configure tri-state for unused pins     
set_global_assignment -name RESERVE_ALL_UNUSED_PINS_WEAK_PULLUP "AS INPUT TRI-STATED"
'''

class Step(Enum):
    """
    Enumeration of the possible workflows to run using quartus.
    """
    Syn = 0
    Pnr = 1
    Bit = 2
    Pgm = 3
    
    @staticmethod
    def from_str(s: str):
        """
        Convert a `str` datatype into a `Step`.
        """
        s = str(s).lower()
        if s == 'syn':
            return Step.Syn
        if s == 'par':
            return Step.Pnr
        if s == 'bit':
            return Step.Bit
        if s == 'pgm':
            return Step.Pgm
        return ValueError
    pass

class Quartz:

    DEFAULT_PART = '10M50DAF484C7G'

    def __init__(self, step: Step, part: str, generics: list, clock: KvPair, store: str):
        '''
        Construct a new Quartz instance.
        '''

        self.man = Manifest()

        self.step = step
        self.part = part
        self.generics = generics
        self.clock = clock
        self.store = store

        self.OUT_DIR = env.read('ORBIT_OUT_DIR')
        self.TOP_NAME: str = str(env.read('ORBIT_TOP_NAME', missing_ok=False))
        self.PROJECT_NAME: str = str(env.read('ORBIT_PROJECT_NAME'))

        cfg_part = self.man.get('project.metadata.quartus.part')
        if part is not None:
            self.part = part
        elif cfg_part is not None:
            self.part = cfg_part
        else:
            self.part = Quartz.DEFAULT_PART
            log.info('using default part "'+self.part+'" since no part was selected')

        self.sram_bitfile = self.TOP_NAME + '.sof'
        self.flash_bitfile = self.TOP_NAME + '.pof'

        self.tcl_path = self.OUT_DIR + '/' + 'run.tcl'
        self.log_path = self.OUT_DIR + '/' + 'run.log'

        self.entries = Blueprint().get_entries()

    @staticmethod
    def from_args(args: list):
        """
        Create a new instance of the quartus workflow.
        """
        parser = argparse.ArgumentParser(prog='quartz', allow_abbrev=False)

        parser.add_argument('--run', '-r', default='syn', choices=['syn', 'par', 'bit', 'pgm'])
        parser.add_argument("--part", action="store", default=None, type=str, help="set the targeted fpga device")
        parser.add_argument('--store', default='sram', choices=['flash', 'sram'], help='specify where to program the bitstream')
        parser.add_argument('--generic', '-g', action='append', type=KvPair.from_arg, default=[], metavar='KEY=VALUE', help='set top-level generics')
        parser.add_argument('--clock', '-c', metavar='NAME=FREQ', help='constrain a pin as a clock at the set frequency (MHz)')

        args = parser.parse_args(args)

        return Quartz(
            step=Step.from_str(args.run),
            part=args.part,
            generics=args.generic,
            clock=args.clock,
            store=args.store
        )
    
    def store_in_flash(self) -> bool:
        return self.store == 'flash'

    def run(self):
        """
        Invoke vivado in batch mode to run the generated tcl script.
        """
        # Create the project
        Command(['quartus_sh', '-t', self.tcl_path]).spawn().unwrap()
        # Run the requested workflow(s)
        if self.step.value >= Step.Syn.value:
            self.synthesize()
        if self.step.value >= Step.Pnr.value:
            self.place_and_route()
        if self.step.value >= Step.Bit.value:
            self.write_bitstream()
        if self.step.value >= Step.Pgm.value:
            self.program()

    def prepare(self):
        """
        Generate the target's tcl script to be used by vivado.
        """
        # verify all generics are set
        env.verify_all_generics_have_values(env.read('ORBIT_TOP_JSON'), self.generics)
        # create the tcl script
        tcl = TclScript(self.tcl_path)
        # write required introduction tcl comments and commands
        self.import_prelude(tcl)
        # add source files
        self.add_sources(tcl)
        tcl.push('project_close')
        # write the tcl script to its file
        tcl.save()

    def import_prelude(self, tcl: TclScript):
        """
        Generate any tcl that is required later in the script.
        """
        tcl.push('load_package flow')
        tcl.push('project_new "'+self.PROJECT_NAME+'" -revision "'+self.PROJECT_NAME+'" -overwrite')
        tcl.push('set_global_assignment -name DEVICE "'+self.part+'"')
        tcl.push('set_global_assignment -name TOP_LEVEL_ENTITY "'+self.TOP_NAME+'"')
        # set generics for top level entity
        gen: KvPair
        for gen in self.generics:
            tcl.push('set_parameter -name "'+gen.key+'" "'+gen.val+'"')
        tcl.push(TCL_CODE_PROJ_SETTINGS)

    def add_sources(self, tcl: TclScript):
        """
        Generate the tcl commands required to add sources to the project
        workflow.
        """
        tcl.push()
        tcl.comment_step('Add source files')

        pdc_path = None
        entry: Entry
        for entry in self.entries:
            if entry.is_vhdl():
                tcl.push(['set_global_assignment', '-name', 'VHDL_FILE', '"'+entry.path+'"', '-library', entry.lib])
            if entry.is_vlog():
                tcl.push(['set_global_assignment', '-name', 'VERILOG_FILE', '"'+entry.path+'"', '-library', entry.lib])
            if entry.is_sysv():
                tcl.push(['set_global_assignment', '-name', 'SYSTEMVERILOG_FILE', '"'+entry.path+'"', '-library', entry.lib])
            if entry.is_aux('SDCF'):
                tcl.push(['set_global_assignment', '-name', 'SDC_FILE', '"'+entry.path+'"'])
            if entry.is_aux('PDCF'):
                pdc_path = entry.path

        # create a clock constraint xdc
        if self.clock != None:
            clock_sdc_path = self.OUT_DIR + '/' + 'clock.sdc'
            clock_sdc = TclScript(clock_sdc_path)
            port = self.clock.key
            freq = self.clock.val

            period = 1.0/((float(freq)*1.0e6))*1.0e9
            period = round(period, 3)
            self.clock = (str(port), str(period))

            clock_sdc.push(['create_clock', '-name', '{'+port+'}', '-period', period, '[get_ports { '+port+' }]'])
            clock_sdc.save()
            tcl.push(['set_global_assignment', '-name', 'SDC_FILE', '"'+clock_sdc.get_path()+'"'])

        if pdc_path is not None:
            tcl.comment_step('Set pin constraints')
            with open(pdc_path, 'r') as fd:
                pdc_dict = toml.loads(fd.read())
            for (pin, port) in pdc_dict.items():
                tcl.push(['set_location_assignment', 'PIN_'+str(pin), '-to', '"'+str(port)+'"'])

    def synthesize(self):
        """
        Run the command for the Quartus project to perform synthesis.
        """
        Command(['quartus_map', self.PROJECT_NAME]).spawn().unwrap()

    def place_and_route(self):
        """
        Run the command for the Quartus project to perform place and route.
        """
        Command(['quartus_fit', self.PROJECT_NAME]).spawn().unwrap()
        Command(['quartus_sta', self.PROJECT_NAME]).spawn().unwrap()

    def write_bitstream(self):
        """
        Run the command for the Quartus project to generate the bitfile.
        """
        Command(['quartus_asm', self.PROJECT_NAME]).spawn().unwrap()
        Command(['quartus_pow', self.PROJECT_NAME]).spawn().unwrap()

    def program(self):
        """
        Run the commands to program a generated bitstream to a connected FPGA device.
        """
        # auto-detect the FPGA programming cable
        out, status = Command(['quartus_pgm', '-a']).output()
        status.unwrap()
        if out.startswith('Error ') == True:
            print(out, end='')
            log.error('failed to detect FPGA programing cable: exited with response: '+str(out))
        tokens = out.split()
        # grab the second token (cable name)
        CABLE = tokens[1]

        prog_args = ['-c', CABLE, '-m', 'jtag', '-o']
        # program the FPGA board with temporary SRAM file
        if self.store_in_flash() == False:
            if os.path.exists(self.sram_bitfile) == True:
                Command(['quartus_pgm'] + prog_args + ['p'+';'+self.sram_bitfile]).spawn().unwrap()
            else:
                log.error('failed to program device: bitstream file '+self.sram_bitfile+' not found')
        # program the FPGA board with permanent program file
        elif self.store_in_flash() == True:
            if os.path.exists(self.flash_bitfile) == True:
                Command(['quartus_pgm'] + prog_args + ['bpv'+';'+self.flash_bitfile]).spawn().unwrap()
            else:
                log.error('failed to program device: bitstream file '+self.flash_bitfile+' not found')


def main():
    quartz = Quartz.from_args(sys.argv[1:])
    quartz.prepare()
    quartz.run()

    
if __name__ == '__main__':
    main()
