'''
Logic and control for interfacing with the Vivado FPGA toolchain.
'''

# Provides the glue logic between a filelist and the Xilinx Vivado EDA tool.
# This script generates a tcl script and executes it using Vivado in a 
# subprocess.
#
# Dependencies:
#   Vivado (tested: 2019.2)
#
# Reference:
#   https://grittyengineer.com/vivado-non-project-mode-releasing-vivados-true-potential/
#   https://www.xilinx.com/support/documents/sw_manuals/xilinx2022_2/ug894-vivado-tcl-scripting.pdf

import argparse
from enum import Enum
import sys
import os

from aquila import log
from aquila import env
from aquila.env import KvPair, Manifest
from aquila.process import Command
from aquila.blueprint import Blueprint, Entry
from aquila.script import TclScript
from aquila.ninja import Ninja


TCL_PROC_REPORT_CRITPATHS = '''\
# Generate a CSV file that provides a summary of the first 50 violations for
# both setup and hold analysis (maximum of 100 paths are reported).
proc report_critical_paths { file_name } {
    # Open the specified output file in write mode
    set fh [open $file_name w]
    # Write the CSV format to a file header
    puts $fh "startpoint,endpoint,delaytype,slack,#levels,#luts"
    # Iterate through both Min and Max delay types
    foreach delayType {max min} {
        # Collect details from the 50 worst timing paths for the current analysis
        # (max = setup/recovery, min = hold/removal)
        # The $path variable contains a Timing Path object.
        foreach path [get_timing_paths -delay_type $delayType -max_paths 50 -nworst 1] {
            # Get the LUT cells of the timing paths
            set luts [get_cells -filter {REF_NAME =~ LUT*} -of_object $path]
            # Get the startpoint of the Timing Path object
            set startpoint [get_property STARTPOINT_PIN $path]
            # Get the endpoint of the Timing Path object
            set endpoint [get_property ENDPOINT_PIN $path]
            # Get the slack on the Timing Path object
            set slack [get_property SLACK $path]
            # Get the number of logic levels between startpoint and endpoint
            set levels [get_property LOGIC_LEVELS $path]
            # Save the collected path details to the CSV file
            puts $fh "$startpoint,$endpoint,$delayType,$slack,$levels,[llength $luts]"
        } 
    }
    # Close the output file
    close $fh
    puts "info: wrote critical path csv file $file_name"
    return 0
};
'''


class Step(Enum):
    '''
    Enumeration of the possible workflows to run using vivado.
    '''
    Syn = 0
    Plc = 1
    Rte = 2
    Bit = 3
    Pgm = 4
    
    @staticmethod
    def from_str(s: str):
        '''
        Convert a `str` datatype into a `Step`.
        '''
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


class Vi:
    '''
    Interface for backend build process for the Vivado FPGA toolchain.
    '''

    # Part to use when one is not specified by the user
    DEFAULT_PART = 'xc7s25-csga324'

    # List of Vivado messages to adjust severity levels
    MSG_SEV_MAP = {
        'ERROR' : [
            # inferred latches
            'Synth 8-327',
            # read signal missing in sensivity list
            'Synth 8-614',
        ]
    }

    def __init__(self, step: str, part: str, generics: list, clock: KvPair):
        '''
        Construct a new Vi instance.
        '''
        self.man = Manifest()
        self.bp = Blueprint()
        self.entries = self.bp.get_entries()

        self.step = Step.from_str(step)

        cfg_part = self.man.get('project.metadata.vivado.part')
        if part is not None:
            self.part = part
        elif cfg_part is not None:
            self.part = cfg_part
        else:
            log.info('using default part '+self.part+' since no part was defined')
            self.part = Vi.DEFAULT_PART            

        self.OUT_DIR = env.read('ORBIT_OUT_DIR')
        self.TOP_NAME = env.read('ORBIT_TOP_NAME', missing_ok=False)

        self.bit_file = self.TOP_NAME + '.bit'

        self.syn_tcl = TclScript(self.OUT_DIR + '/' + 'syn.tcl')
        self.plc_tcl = TclScript(self.OUT_DIR + '/' + 'plc.tcl')
        self.rte_tcl = TclScript(self.OUT_DIR + '/' + 'rte.tcl')
        self.bit_tcl = TclScript(self.OUT_DIR + '/' + 'bit.tcl')
        self.pgm_tcl = TclScript(self.OUT_DIR + '/' + 'pgm.tcl')

        self.log_path = self.OUT_DIR + '/' + 'run.log'

        self.generics = generics
        self.clock = clock

        self.nj = Ninja()

    @staticmethod
    def from_args(args: list):
        '''
        Construct a new Vi instance from a set of arguments.
        '''
        parser = argparse.ArgumentParser(prog='vi', allow_abbrev=False)

        parser.add_argument('--run', '-r', default='syn', choices=['syn', 'plc', 'rte', 'bit', 'pgm'], help='select the workflow to execute')
        parser.add_argument('--part', metavar='DEVICE', default=None, help='specify the targeted fpga device')
        parser.add_argument('--generic', '-g', action='append', type=KvPair.from_arg, default=[], metavar='KEY=VALUE', help='set top-level generics')
        parser.add_argument('--clock', '-c', metavar='NAME=FREQ', type=KvPair.from_arg, help='constrain a pin as a clock at the set frequency (MHz)')
        
        args = parser.parse_args()
        return Vi(
            step=args.run,
            part=args.part,
            generics=args.generic,
            clock=args.clock,
        )

    def prepare(self):
        '''
        Generate the target's tcl script to be used by vivado.
        '''
        env.verify_all_generics_have_values(env.read('ORBIT_TOP_JSON'), self.generics)

        vivado_cmd = 'vivado' if os.name != 'nt' else 'vivado.bat'
        
        self.nj.add_def_var('opts', '-mode batch -nojournal -applog -log '+self.log_path)
        
        self.nj.add_rule('fpga', vivado_cmd+' ${opts} -source ${in}')

        # the top-most command to call when using the Ninja build system
        self.top_cmd = None

        # generate the necessary tcl commands for the requested workflow
        self.import_prelude(self.syn_tcl)
        dep_files = self.add_sources(self.syn_tcl)
        syn_dcp = self.synthesize(self.syn_tcl)
        self.nj.add_build('fpga', [syn_dcp], [self.syn_tcl.get_path()], dep_files)
        if self.step.value == Step.Syn.value:
            self.top_cmd = syn_dcp
        # placement tcl script
        self.import_prelude(self.plc_tcl)
        plc_dcp = self.place(self.plc_tcl, syn_dcp)
        self.nj.add_build('fpga', [plc_dcp], [self.plc_tcl.get_path()], [syn_dcp])
        if self.step.value == Step.Plc.value:
            self.top_cmd = plc_dcp
        # routing tcl script
        self.import_prelude(self.rte_tcl)
        rte_dcp = self.route(self.rte_tcl, plc_dcp)
        self.nj.add_build('fpga', [rte_dcp], [self.rte_tcl.get_path()], [plc_dcp])
        if self.step.value == Step.Rte.value:
            self.top_cmd = rte_dcp
        # bitstream tcl script
        self.import_prelude(self.bit_tcl)
        bitfile = self.bitstream(self.bit_tcl, rte_dcp)
        self.nj.add_build('fpga', [bitfile], [self.bit_tcl.get_path()], [rte_dcp])
        if self.step.value == Step.Bit.value:
            self.top_cmd = bitfile
        # programming tcl script
        self.import_prelude(self.pgm_tcl)
        self.program(self.pgm_tcl)
        self.nj.add_build('fpga', ['out'], [self.pgm_tcl.get_path()], [bitfile])
        if self.step.value == Step.Pgm.value:
            self.top_cmd = None

        self.nj.save()
        
    def import_prelude(self, tcl: TclScript):
        '''
        Generate any tcl that is required later in the script.
        '''
        tcl.push(TCL_PROC_REPORT_CRITPATHS)
        tcl.comment('Disable webtalk')
        tcl.push('config_webtalk -user off')
        # adjust message severity levels
        for (lvl, msgs) in Vi.MSG_SEV_MAP.items():
            for msg in msgs:
                tcl.push('set_msg_config -id {'+msg+'} -new_severity {'+lvl+'}')

    def add_sources(self, tcl: TclScript):
        '''
        Generate the tcl commands required to add sources to the non-project mode
        workflow.
        '''
        tcl.push()
        tcl.comment_step('Add source files')
        entry: Entry

        src_files = []
        for entry in self.entries:
            if entry.is_vhdl():
                tcl.push(['read_vhdl', '-vhdl2008', '-library', entry.lib, '"'+entry.path+'"'])
                src_files += [entry.path]
            if entry.is_vlog():
                tcl.push(['read_verilog', '-library', entry.lib, '"'+entry.path+'"'])
                src_files += [entry.path]
            if entry.is_sysv():
                tcl.push(['read_verilog', '-sv', '-library', entry.lib, '"'+entry.path+'"'])
                src_files += [entry.path]
            if entry.is_aux('XDCF'):
                tcl.push(['read_xdc', '"'+entry.path+'"'])
                src_files += [entry.path]
                pass

        # create a clock constraint xdc
        if self.clock is not None:
            clock_xdc_path = self.OUT_DIR + '/' + 'clocks.xdc'
            clock_xdc = TclScript(clock_xdc_path)

            name = self.clock.key
            period = 1.0/((float(self.clock.val)*1.0e6))*1.0e9
            period = round(period, 2)

            clock_xdc.push(['create_clock', '-add', '-name', name, '-period', period, '[get_ports { '+name+' }];'])
            if self.requires_save(clock_xdc):
                clock_xdc.save()
            
            tcl.push(['read_xdc', '"'+clock_xdc.get_path()+'"'])
            src_files += [clock_xdc.get_path()]
        return src_files
    
    def requires_save(self, tcl: TclScript) -> bool:
        '''
        Check if this TCL script requires saving (overwriting its existing contents).
        '''
        contents_match = False
        if os.path.exists(tcl.get_path()):
            existing_data = ''
            with open(tcl.get_path(), 'r') as fd:
                existing_data = fd.read()
            contents_match = existing_data == tcl.get_data()
        return contents_match == False

    def synthesize(self, tcl: TclScript) -> str:
        '''
        Generate tcl commands for performing synthesis.
        '''
        dcp = 'post_syn.dcp'
        tcl.push()
        tcl.comment_step('Run synthesis task')
        tcl.push(['synth_design', '-top', self.TOP_NAME, '-part', self.part] + ['-generic '+str(g) for g in self.generics])
        tcl.push('write_checkpoint -force '+dcp)
        tcl.push('report_timing_summary -file post_syn_timing_summary.rpt')
        tcl.push('report_utilization -file post_syn_util.rpt')
        tcl.comment('Run custom script to report critical timing paths')
        tcl.push('report_critical_paths post_syn_timing.csv')
        if self.requires_save(tcl):
            tcl.save()
        return dcp

    def place(self, tcl: TclScript, last_dcp: str):
        '''
        Generate tcl commands for performing optimizations and placement.
        '''
        dcp = 'post_plc.dcp'
        tcl.push()
        tcl.comment_step('Load previous design checkpoint')
        tcl.push('open_checkpoint '+last_dcp)
        tcl.push()
        tcl.comment_step('Run logic optimization, placement, and physical logic optimization')
        tcl.push('opt_design')
        tcl.push('report_critical_paths post_opt_critpath_report.csv')
        tcl.push('place_design')
        tcl.push('report_clock_utilization -file clock_util.rpt')
        tcl.comment('Optionally run optimization if there are timing violations after placement')
        tcl.push('if {[get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]] < 0} {')
        tcl.indent()
        tcl.push('puts "info: found setup timing violations => running physical optimization"')
        tcl.push('phys_opt_design')
        tcl.dedent()
        tcl.push('}')
        tcl.push('write_checkpoint -force '+dcp)
        tcl.push('report_utilization -file post_plc_util.rpt')
        tcl.push('report_timing_summary -file post_plc_timing_summary.rpt')
        if self.requires_save(tcl):
            tcl.save()
        return dcp

    def route(self, tcl: TclScript, last_dcp: str):
        '''
        Generate tcl commands for performing routing.
        '''
        dcp = 'post_rte.dcp'
        tcl.push()
        tcl.comment_step('Load previous design checkpoint')
        tcl.push('open_checkpoint '+last_dcp)
        tcl.push()
        tcl.comment_step('Run routing for the design')
        tcl.push('route_design')
        tcl.push('write_checkpoint -force '+dcp)
        tcl.push('report_route_status -file post_rte_status.rpt')
        tcl.push('report_timing_summary -file post_rte_timing_summary.rpt')
        tcl.push('report_power -file post_rte_power.rpt')
        tcl.push('report_drc -file post_impl_drc.rpt')
        # tcl.push('write_verilog -force rte_netlist.v -mode timesim -sdf_anno true')
        if self.requires_save(tcl):
            tcl.save()
        return dcp

    def bitstream(self, tcl: TclScript, last_dcp: str):
        '''
        Generate the tcl commands to write the bitstream.
        '''
        tcl.push()
        tcl.comment_step('Load previous design checkpoint')
        tcl.push('open_checkpoint '+last_dcp)
        tcl.push()
        tcl.comment_step('Generate the bitstream')
        tcl.push(['write_bitstream', '-force', self.bit_file])
        if self.requires_save(tcl):
            tcl.save()
        return self.bit_file

    def program(self, tcl: TclScript):
        '''
        Generate the tcl commands to program the bitstream to a board.
        '''
        tcl.push()
        tcl.comment_step('Program the connected FPGA device')
        tcl.push('open_hw_manager')
        tcl.push('connect_hw_server -allow_non_jtag')
        tcl.push('open_hw_target')
        tcl.comment('Find the Xilinx FPGA device connected to the local machine')
        tcl.push('set device [lindex [get_hw_devices "xc*"] 0]')
        tcl.push('puts "info: detected FPGA device $device"')
        tcl.push('current_hw_device $device')
        tcl.push('refresh_hw_device -update_hw_probes false $device')
        tcl.push('set_property "PROBES.FILE" {} $device')
        tcl.push('set_property "FULL_PROBES.FILE" {} $device')
        tcl.push(['set_property', '"PROGRAM.FILE"', self.bit_file, '$device'])
        tcl.comment('Program and refresh the detected FPGA device')
        tcl.push('program_hw_devices $device')
        tcl.push('refresh_hw_device $device')
        if self.requires_save(tcl):
            tcl.save()

    def run(self):
        '''
        Invoke vivado in batch mode to run the generated tcl script.
        '''
        cmd = [] if self.top_cmd is None else [self.top_cmd]
        stat = Command(['ninja'] + cmd).spawn()
        # report to the user where the log can be found
        if os.path.exists(self.log_path):
            print('\n@@@ RUN LOG: \"'+self.log_path+'\" @@@\n')
        stat.unwrap()


def main():
    vi = Vi.from_args(sys.argv[1:])
    log.info('preparing build...')
    vi.prepare()
    log.info('running backend process...')
    vi.run()

if __name__ == '__main__':
    main()
