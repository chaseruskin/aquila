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

from mod import Command, Env, Generic, Blueprint, TclScript
import os
import argparse
from enum import Enum

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
        if s == 'map':
            return Step.Syn
        if s == 'fit':
            return Step.Pnr
        if s == 'asm':
            return Step.Bit
        if s == 'pgm':
            return Step.Pgm
        return ValueError
    pass

class Quartz:

    def __init__(self):
        """
        Create a new instance of the quartus workflow.
        """
        parser = argparse.ArgumentParser(prog='quartz', allow_abbrev=False)
        parser.add_argument('--run', '-r', default='map', choices=['map', 'fit', 'asm', 'pgm'])
        parser.add_argument("--device", action="store", default=None, type=str, help="set the targeted fpga device")
        parser.add_argument('--store', default='sram', choices=['flash', 'sram'], help='specify where to program the bitstream')
        parser.add_argument('--generic', '-g', action='append', type=Generic.from_arg, default=[], metavar='key=value', help='override top-level VHDL generics')
        parser.add_argument('--clock', '-c', metavar='NAME=FREQ', help='constrain a pin as a clock at the set frequency (MHz)')
        args = parser.parse_args()

        self.proc = Step.from_str(args.run)
        self.part = '10M50DAF484C7G'
        if args.device == None:
            print('info: using default part "'+self.part+'" since no part was selected')
        else:
            self.part = args.device

        self.generics: list[Generic] = args.generic

        self.PROG_FLASH = args.store.lower() == 'flash'

        # capture the additional clock constraint
        self.clock = None
        if args.clock != None:
            port, freq = args.clock.split('=')
            period = 1.0/((float(freq)*1.0e6))*1.0e9
            period = round(period, 3)
            self.clock = (str(port), str(period))

        self.output_path = Env.read('ORBIT_OUT_DIR')

        self.top: str = str(Env.read('ORBIT_TOP_NAME', missing_ok=False))
        
        self.proj: str = str(Env.read('ORBIT_PROJECT_NAME'))

        self.sram_bitfile = str(self.top)+'.sof'
        self.flash_bitfile = str(self.top)+'.pof'

        self.tcl_path = self.output_path + '/' + 'run.tcl'
        self.log_path = self.output_path + '/' + 'run.log'

        self.entries = []

    def read_blueprint(self):
        """
        Process the blueprint contents.
        """
        self.entries = Blueprint().parse()

    def run(self):
        """
        Invoke vivado in batch mode to run the generated tcl script.
        """
        # Create the project
        Command('quartus_sh').args(['-t', self.tcl_path]).spawn().unwrap()
        # Run the requested workflow(s)
        if self.proc.value >= Step.Syn.value:
            self.synthesize()
        if self.proc.value >= Step.Pnr.value:
            self.place_and_route()
        if self.proc.value >= Step.Bit.value:
            self.write_bitstream()
        if self.proc.value >= Step.Pgm.value:
            self.program()

    def write_tclscript(self):
        """
        Generate the target's tcl script to be used by vivado.
        """
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
        tcl.push('project_new "'+self.proj+'" -revision "'+self.proj+'" -overwrite')
        tcl.push('set_global_assignment -name DEVICE "'+self.part+'"')
        tcl.push('set_global_assignment -name TOP_LEVEL_ENTITY "'+self.top+'"')
        # set generics for top level entity
        for gen in self.generics:
            tcl.push('set_parameter -name "'+gen.key+'" "'+gen.val+'"')
        tcl.push(TCL_CODE_PROJ_SETTINGS)

    def add_sources(self, tcl: TclScript):
        """
        Generate the tcl commands required to add sources to the project
        workflow.
        """
        tcl.push()
        tcl.comment('(1) Add source files')
        for entry in self.entries:
            if entry.is_vhdl():
                tcl.push(['set_global_assignment', '-name', 'VHDL_FILE', '"'+entry.path+'"', '-library', entry.lib])
            if entry.is_vlog():
                tcl.push(['set_global_assignment', '-name', 'VERILOG_FILE', '"'+entry.path+'"', '-library', entry.lib])
            if entry.is_sysv():
                tcl.push(['set_global_assignment', '-name', 'SYSTEMVERILOG_FILE', '"'+entry.path+'"', '-library', entry.lib])
            if entry.is_aux('SDCF'):
                tcl.push(['set_global_assignment', '-name', 'SDC_FILE', '"'+entry.path+'"'])
                pass
    
        # create a clock constraint xdc
        if self.clock != None:
            clock_sdc = TclScript('clock.sdc')
            name, period = self.clock
            clock_sdc.push(['create_clock', '-name', '{'+name+'}', '-period', period, '[get_ports { '+name+' }]'])
            clock_sdc.save()
            clock_sdc_path = self.output_path + '/' + clock_sdc.get_path()
            tcl.push(['set_global_assignment', '-name', 'SDC_FILE', '"'+clock_sdc_path+'"'])
            pass

    def synthesize(self):
        """
        Run the command for the Quartus project to perform synthesis.
        """
        Command('quartus_map').arg(self.proj).spawn().unwrap()

    def place_and_route(self):
        """
        Run the command for the Quartus project to perform place and route.
        """
        Command('quartus_fit').arg(self.proj).spawn().unwrap()
        Command('quartus_sta').arg(self.proj).spawn().unwrap()

    def write_bitstream(self):
        """
        Run the command for the Quartus project to generate the bitfile.
        """
        Command("quartus_asm").arg(self.proj).spawn().unwrap()
        Command('quartus_pow').arg(self.proj).spawn().unwrap()

    def program(self):
        """
        Run the commands to program a generated bitstream to a connected FPGA device.
        """
        # auto-detect the FPGA programming cable
        out, status = Command("quartus_pgm").arg('-a').output()
        status.unwrap()
        if out.startswith('Error ') == True:
            print(out, end='')
            exit(101)
        tokens = out.split()
        # grab the second token (cable name)
        CABLE = tokens[1]
        pass

        prog_args = ['-c', CABLE, '-m', 'jtag', '-o']
        # program the FPGA board with temporary SRAM file
        if self.PROG_FLASH == False:
            if os.path.exists(self.sram_bitfile) == True:
                Command('quartus_pgm').args(prog_args).args(['p'+';'+self.sram_bitfile]).spawn().unwrap()
            else:
                exit('error: failed to program device: bitstream file '+self.sram_bitfile+' not found')
            pass
        # program the FPGA board with permanent program file
        elif self.PROG_FLASH == True:
            if os.path.exists(self.flash_bitfile) == True:
                Command('quartus_pgm').args(prog_args).args(['bpv'+';'+self.flash_bitfile]).spawn().unwrap()
            else:
                exit('error: failed to program device: bitstream file '+self.flash_bitfile+' not found')
            pass


def main():
    quartz = Quartz()
    quartz.read_blueprint()
    quartz.write_tclscript()
    quartz.run()

    
if __name__ == '__main__':
    main()
