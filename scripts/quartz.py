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

from mod import Command, Env, Generic, Blueprint, TclScript, Entry
from typing import List
import os
import argparse
import toml
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
    Plc = 1
    Rte = 2
    Bit = 3
    Pgm = 4
    
    @staticmethod
    def from_str(s: str):
        """
        Convert a `str` datatype into a `Step`.
        """
        s = str(s).lower()
        if s == 'syn':
            return Step.Syn
        if s == 'plc':
            return Step.Plc
        if s == 'rte':
            return Step.Rte
        if s == 'bit':
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
        parser.add_argument('--run, -r', default='map', choices=['map', 'fit', 'asm', 'pgm'])
        parser.add_argument("--device", action="store", default=None, type=str, help="set the targeted fpga device")
        parser.add_argument('--store', default='sram', choices=['flash', 'sram'], help='specify where to program the bitstream')
        parser.add_argument('--generic', '-g', action='append', type=Generic.from_arg, default=[], metavar='key=value', help='override top-level VHDL generics')
        parser.add_argument('--clock', '-c', metavar='NAME=FREQ', help='constrain a pin as a clock at the set frequency (MHz)')
        args = parser.parse_args()

        self.proc = Step.from_str(args.run)
        self.part = '10M50DAF484C7G'
        if args.part == None:
            print('info: using default part "'+self.part+'" since no part was selected')
        else:
            self.part = args.part

        self.generics: list[Generic] = args.generics

        # capture the additional clock constraint
        self.clock = None
        if args.clock != None:
            port, freq = args.clock.split('=')
            period = 1.0/((float(freq)*1.0e6))*1.0e9
            period = round(period, 3)
            self.clock = (str(port), str(period))

        self.output_path = Env.read('ORBIT_OUT_DIR')

        self.top: str = str(Env.read('ORBIT_TOP_NAME', missing_ok=False))
        
        self.proj: str = str(Env.read('ORBIT_IP_NAME'))

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
        result = Command('quartus_sh') \
            .args(['-t', self.tcl_path]) \
            .spawn()
        # report to the user where the log can be found
        print('\n@@@ RUN LOG: \"'+self.log_path+'\" @@@\n')
        result.unwrap()

    def write_tclscript(self):
        """
        Generate the target's tcl script to be used by vivado.
        """
        tcl = TclScript(self.tcl_path)
        # write required introduction tcl comments and commands
        self.import_prelude(tcl)
        # add source files
        self.add_sources(tcl)
        # generate the necessary tcl commands for the requested workflow
        if self.proc.value >= Step.Syn.value:
            self.synthesize(tcl)
        # if self.proc.value >= Step.Plc.value:
        #     self.place(tcl)
        # if self.proc.value >= Step.Rte.value:
        #     self.route(tcl)
        # if self.proc.value >= Step.Bit.value:
        #     self.bitstream(tcl)
        # if self.proc.value >= Step.Pgm.value:
        #     self.program(tcl)
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

    def synthesize(self, tcl: TclScript):
        """
        Generate tcl commands for performing synthesis.
        """
        tcl.push()
        tcl.comment('(2) Run synthesis task')
        tcl.push('exec quartus_map '+self.proj)



def main():
    quartz = Quartz()
    quartz.read_blueprint()
    quartz.write_tclscript()
    quartz.run()

#     # temporarily appends quartus installation path to PATH env variable
#     Env.add_path(Env.read("ORBIT_ENV_QUARTUS_DIR", missing_ok=True))

#     ## Handle command-line arguments

#     parser = argparse.ArgumentParser(prog='quartz', allow_abbrev=False)

#     parser.add_argument("--synth", action="store_true", default=False, help="execute analysize and synthesis")
#     parser.add_argument("--route", action="store_true", default=False, help="execute place and route")
#     parser.add_argument("--sta", action="store_true", default=False, help="execute static timing analysis")
#     parser.add_argument("--bit", action="store_true", default=False, help="generate bitstream file")

#     parser.add_argument("--open", action="store_true", default=False, help="open quartus project in gui")
#     parser.add_argument("--compile", action="store_true", default=False, help="full toolflow")
#     parser.add_argument("--eda-netlist", action="store_true", default=False, help="generate eda timing netlist")

#     parser.add_argument("--board", action="store", default=None, type=str, help="board configuration file name")
#     parser.add_argument("--prog-sram", action="store_true", default=False, help="program with temporary bitfile")
#     parser.add_argument("--prog-flash", action="store_true", default=False, help="program with permanent bitfile")

#     parser.add_argument("--family", action="store", default=None, type=str, help="targeted fpga family")
#     parser.add_argument("--device", action="store", default=None, type=str, help="targeted fpga device")

#     parser.add_argument('--generic', '-g', action='append', type=Generic.from_arg, default=[], metavar='key=value', help='override top-level VHDL generics')

#     args = parser.parse_args()

#     generics: List[Generic] = args.generic

#     # determine if to program the FPGA board
#     pgm_temporary = args.prog_sram
#     pgm_permanent = args.prog_flash

#     # device selected here is read from .board file if is None
#     FAMILY = args.family
#     DEVICE = args.device

#     # the quartus project will reside in a folder the same name as the IP
#     PROJECT = Env.read("ORBIT_IP_NAME", missing_ok=False)

#     # will be overridden when programming to board with auto-detection by quartus
#     CABLE = "USB-Blaster"

#     # determine if to open the quartus project in GUI
#     open_project = args.open

#     # default flow is none (won't execute any flow)
#     flow = None
#     synth = impl = asm = sta = eda_netlist = False
#     if args.compile == True:
#         flow = '-compile'
#     else:
#         # run up through synthesis
#         if args.synth == True:
#             synth = True
#         # run up through fitting
#         if args.route == True:
#             synth = impl = True
#         # run up through static timing analysis
#         if args.sta == True:
#             synth = impl = sta = True
#         # run up through assembly
#         if args.bit == True:
#             synth = impl = sta = asm = True
#         # run up through generating eda timing netlist
#         if args.eda_netlist == True:
#             synth = impl = sta = asm = eda_netlist = True
#         # use a supported device to generate .SDO and .VHO files for timing simulation
#         if eda_netlist == True:
#             FAMILY = "MAXII"
#             DEVICE = "EPM2210F324I5"
#         pass

#     ## Collect data from the blueprint

#     # list of (lib, path)
#     src_files = []
#     # list of paths to board design files
#     bdf_files = []

#     board_config = None
#     # read/parse blueprint file
#     for step in Blueprint().parse():
#         if step.is_builtin():
#             src_files += [step]
#         elif step.is_aux("BDF"):
#             bdf_files += [step.path]
#         elif step.is_aux('BOARD'):
#             if board_config == None and args.board is None:
#                 board_config = toml.load(step.path)
#                 print('info: loaded board file:', step.path)
#             # match filename with the filename provided on command-line
#             elif os.path.splitext(os.path.basename(step.path))[0] == args.board:
#                 board_config = toml.load(step.path)
#                 print('info: loaded board file:', step.path)
#             pass
#         pass

#     # verify we got a matching board file if specified from the command-line
#     if board_config is None and args.board is not None:
#         print("error: board file "+Env.quote_str(args.board)+" is not found in blueprint")
#         exit(101)

#     if board_config is not None:
#         FAMILY = board_config["part"]["FAMILY"]
#         DEVICE = board_config["part"]["DEVICE"]

#     top_unit = Env.read("ORBIT_TOP_NAME", missing_ok=False)

#     if FAMILY == None:
#         print("error: FPGA \"FAMILY\" must be specified in .board file's `[part]` table")
#         exit(101)
#     if DEVICE == None:
#         print("error: FPGA \"DEVICE\" must be specified in .board file's `[part]` table")
#         exit(101)
#     # verify the board has pin assignments
#     if board_config is None or (board_config is not None and 'pins' not in board_config.keys()):
#         print("warning: no pin assignments found due to missing `[pins]` table in board file")

#     # --- Process data -------------------------------------------------------------

#     # Define initial project settings
#     PROJECT_SETTINGS = """\
# # Quartus project TCL script automatically generated by Orbit. DO NOT EDIT.
# load_package flow

# #### General project settings ####

# # Create the project and overwrite any settings or files that exist
# project_new """ + Env.quote_str(PROJECT) + """ -revision """ + Env.quote_str(PROJECT) + """ -overwrite
# # Set default configurations and device
# set_global_assignment -name NUM_PARALLEL_PROCESSORS """ + Env.quote_str("ALL") + """
# set_global_assignment -name VHDL_INPUT_VERSION VHDL_1993
# set_global_assignment -name VERILOG_INPUT_VERSION SYSTEMVERILOG_2005
# set_global_assignment -name EDA_SIMULATION_TOOL "ModelSim-Altera (VHDL)"
# set_global_assignment -name EDA_OUTPUT_DATA_FORMAT "VHDL" -section_id EDA_SIMULATION
# set_global_assignment -name EDA_GENERATE_FUNCTIONAL_NETLIST OFF -section_id EDA_SIMULATION
# set_global_assignment -name FAMILY """ + Env.quote_str(FAMILY) + """
# set_global_assignment -name DEVICE """ + Env.quote_str(DEVICE) + """
# # Use single uncompressed image with memory initialization file
# set_global_assignment -name EXTERNAL_FLASH_FALLBACK_ADDRESS 00000000
# set_global_assignment -name USE_CONFIGURATION_DEVICE OFF
# set_global_assignment -name INTERNAL_FLASH_UPDATE_MODE "SINGLE IMAGE WITH ERAM" 
# # Configure tri-state for unused pins     
# set_global_assignment -name RESERVE_ALL_UNUSED_PINS_WEAK_PULLUP "AS INPUT TRI-STATED"
# """

#     # 1. write TCL file for quartus project

#     tcl = Tcl('orbit.tcl')

#     tcl.push(PROJECT_SETTINGS, raw=True)

#     tcl.push('#### Application-specific settings ####', end='\n\n', raw=True)

#     tcl.push('# Add source code files to the project', raw=True)

#     # generate the required tcl text for adding source files (vhdl, verilog, sv, bdf)
#     src: Step
#     for src in src_files:
#         if src.is_vhdl():
#             tcl.push("set_global_assignment -name VHDL_FILE "+Env.quote_str(src.path)+" -library "+Env.quote_str(src.lib), raw=True)
#         elif src.is_vlog():
#             tcl.push("set_global_assignment -name VERILOG_FILE "+Env.quote_str(src.path)+" -library "+Env.quote_str(src.lib), raw=True)
#         elif src.is_sysv():
#             tcl.push("set_global_assignment -name SYSTEMVERILOG_FILE "+Env.quote_str(src.path)+" -library "+Env.quote_str(src.lib), raw=True)
#         pass
#    # exit(101)
#     for bdf in bdf_files:
#         tcl.push("set_global_assignment -name BDF_FILE "+Env.quote_str(bdf), raw=True)

#     # set the top level entity
#     tcl.push('# Set the top level entity', raw=True)
#     tcl.push("set_global_assignment -name TOP_LEVEL_ENTITY "+Env.quote_str(top_unit), raw=True)

#     # set generics for top level entity
#     if len(generics) > 0:
#         tcl.push('# Set generics for top level entity', raw=True)
#         generic: Generic
#         for generic in generics:
#             tcl.push("set_parameter -name "+Env.quote_str(generic.key)+" "+Env.quote_str(str(generic.val)), raw=True)
#         pass

#     # set the pin assignments
#     if board_config is not None and 'pins' in board_config.keys():
#         tcl.push('# Set the pin assignments', raw=True)
#         for (pin, port) in board_config['pins'].items():
#             tcl.push("set_location_assignment "+Env.quote_str(pin)+" -to "+Env.quote_str(port), raw=True)
#         pass

#     # run a preset workflow
#     if flow is not None:
#         tcl.push('execute_flow '+flow, raw=True)
#         pass

#     # close the newly created project
#     tcl.push('project_close', raw=True)

#     # finish writing the TCL script and save it to disk
#     tcl.save()

#     # 2. run quartus with TCL script

#     # execute quartus using the generated tcl script
#     Command("quartus_sh").args(['-t', tcl.get_path()]).spawn().unwrap()

#     # 3. perform a specified toolflow

#     # synthesize design
#     if synth == True:
#         Command("quartus_map").arg(PROJECT).spawn().unwrap()
#     # route design to board
#     if impl == True:
#         Command("quartus_fit").arg(PROJECT).spawn().unwrap()
#     # perform static timing analysis
#     if sta == True:
#         Command("quartus_sta").arg(PROJECT).spawn().unwrap()
#     # generate bitstream
#     if asm == True:
#         Command("quartus_asm").arg(PROJECT).spawn().unwrap()
#     # generate necessary files for timing simulation
#     if eda_netlist == True:
#         Command("quartus_eda").args([PROJECT, '--simulation']).spawn().unwrap()

#     # 4. program the FPGA board

#     # auto-detect the FPGA programming cable
#     if pgm_temporary == True or pgm_permanent == True:
#         out, status = Command("quartus_pgm").arg('-a').output()
#         status.unwrap()
#         if out.startswith('Error ') == True:
#             print(out, end='')
#             exit(101)
#         tokens = out.split()
#         # grab the second token (cable name)
#         CABLE = tokens[1]
#         pass

#     prog_args = ['-c', CABLE, '-m', 'jtag', '-o']
#     # program the FPGA board with temporary SRAM file
#     if pgm_temporary == True:
#         if os.path.exists(PROJECT+'.sof') == True:
#             Command('quartus_pgm').args(prog_args).args(['p'+';'+PROJECT+'.sof']).spawn().unwrap()
#         else:
#             exit('error: bitstream .sof file not found')
#         pass
#     # program the FPGA board with permanent program file
#     elif pgm_permanent == True:
#         if os.path.exists(PROJECT+'.pof') == True:
#             Command('quartus_pgm').args(prog_args).args(['bpv'+';'+PROJECT+'.pof']).spawn().unwrap()
#         else:
#             exit('error: bitstream .pof file not found')
#         pass

#     # 5. open the quartus project

#     # open the project using quartus GUI
#     if open_project == True:
#         Command('quartus').arg(PROJECT+'.qpf').spawn().unwrap()
#         pass
#     pass
    
if __name__ == '__main__':
    main()
