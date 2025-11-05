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

from mod import Env, Command, Generic, Blueprint, TclScript
import argparse
from enum import Enum
import os


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
    """
    Enumeration of the possible workflows to run using vivado.
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


class Voodoo:
    
    def __init__(self):
        """
        Create a new instance of the target workflow.
        """
        # collect command-line arguments
        parser = argparse.ArgumentParser(prog='voodoo', allow_abbrev=False)

        parser.add_argument('--generic', '-g', action='append', type=Generic.from_arg, default=[], metavar='KEY=VALUE', help='override top-level generics/parameters')
        parser.add_argument('--part', help='specify the targeted fpga device')
        parser.add_argument('--run', '-r', default='syn', choices=['syn', 'plc', 'rte', 'bit', 'pgm'], help='select the workflow to execute')
        parser.add_argument('--clock', '-c', metavar='NAME=FREQ', help='constrain a pin as a clock at the set frequency (MHz)')
        args = parser.parse_args()

        # capture all command-line arguments into instance variables
        self.proc = Step.from_str(args.run)
        self.part = 'xc7s25-csga324'
        if args.part == None:
            print('info: using default part "'+self.part+'" since no part was selected')
        else:
            self.part = args.part

        self.tcl_generics = []
        for g in args.generic:
            self.tcl_generics += ['-generic', str(g)]
            pass

        # capture the additional clock constraint
        self.clock = None
        if args.clock != None:
            port, freq = args.clock.split('=')
            period = 1.0/((float(freq)*1.0e6))*1.0e9
            period = round(period, 2)
            self.clock = (str(port), str(period))

        # set other necessary instance variables
        self.output_path = Env.read('ORBIT_OUT_DIR')

        self.top: str = str(Env.read('ORBIT_TOP_NAME', missing_ok=False))
        self.bit_file: str = str(self.top)+'.bit'

        self.tcl_path = self.output_path + '/' + 'run.tcl'
        self.log_path = self.output_path + '/' + 'run.log'

        self.entries = []
        pass

    def read_blueprint(self):
        """
        Process the blueprint contents.
        """
        self.entries = Blueprint().parse()

    def run(self):
        """
        Invoke vivado in batch mode to run the generated tcl script.
        """
        result = Command('vivado') \
            .args(['-mode', 'batch', '-nojournal', '-log', self.log_path, '-source', self.tcl_path]) \
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
        if self.proc.value >= Step.Plc.value:
            self.place(tcl)
        if self.proc.value >= Step.Rte.value:
            self.route(tcl)
        if self.proc.value >= Step.Bit.value:
            self.bitstream(tcl)
        if self.proc.value >= Step.Pgm.value:
            self.program(tcl)
        # write the tcl script to its file
        tcl.save()
        
    def import_prelude(self, tcl: TclScript):
        """
        Generate any tcl that is required later in the script.
        """
        tcl.push(TCL_PROC_REPORT_CRITPATHS)
        tcl.comment('Disable webtalk')
        tcl.push('config_webtalk -user off')

    def add_sources(self, tcl: TclScript):
        """
        Generate the tcl commands required to add sources to the non-project mode
        workflow.
        """
        tcl.push()
        tcl.comment('(1) Add source files')
        for entry in self.entries:
            if entry.is_vhdl():
                tcl.push(['read_vhdl', '-library', entry.lib, '"'+entry.path+'"'])
            if entry.is_vlog():
                tcl.push(['read_verilog', '-library', entry.lib, '"'+entry.path+'"'])
            if entry.is_sysv():
                tcl.push(['read_verilog', '-sv', '-library', entry.lib, '"'+entry.path+'"'])
            if entry.is_aux('XDCF'):
                tcl.push(['read_xdc', '"'+entry.path+'"'])
                pass

        # create a clock constraint xdc
        if self.clock != None:
            clock_xdc = TclScript('clock.xdc')
            name, period = self.clock
            clock_xdc.push(['create_clock', '-add', '-name', name, '-period', period, '[get_ports { '+name+' }];'])
            clock_xdc.save()
            clock_xdc_path = self.output_path + '/' + clock_xdc.get_path()
            tcl.push(['read_xdc', '"'+clock_xdc_path+'"'])
            pass

    def synthesize(self, tcl: TclScript):
        """
        Generate tcl commands for performing synthesis.
        """
        tcl.push()
        tcl.comment('(2) Run synthesis task')
        tcl.push(['synth_design', '-top', self.top, '-part', self.part] + self.tcl_generics)
        tcl.push('write_checkpoint -force post_synth.dcp')
        tcl.push('report_timing_summary -file post_synth_timing_summary.rpt')
        tcl.push('report_utilization -file post_synth_util.rpt')
        tcl.comment('Run custom script to report critical timing paths')
        tcl.push('report_critical_paths post_synth_critpath_report.csv')
        pass

    def place(self, tcl: TclScript):
        """
        Generate tcl commands for performing optimizations and placement.
        """
        tcl.push()
        tcl.comment('(3) Run logic optimization, placement, and physical logic optimization')
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
        tcl.push('write_checkpoint -force post_place.dcp')
        tcl.push('report_utilization -file post_place_util.rpt')
        tcl.push('report_timing_summary -file post_place_timing_summary.rpt')

    def route(self, tcl: TclScript):
        """
        Generate tcl commands for performing routing.
        """
        tcl.push()
        tcl.comment('(4) Run routing for the design')
        tcl.push('route_design')
        tcl.push('write_checkpoint -force post_route.dcp')
        tcl.push('report_route_status -file post_route_status.rpt')
        tcl.push('report_timing_summary -file post_route_timing_summary.rpt')
        tcl.push('report_power -file post_route_power.rpt')
        tcl.push('report_drc -file post_impl_drc.rpt')
        tcl.push('write_verilog -force rte_netlist.v -mode timesim -sdf_anno true')

    def bitstream(self, tcl: TclScript):
        """
        Generate the tcl commands to write the bitstream.
        """
        tcl.push()
        tcl.comment('(5) Generate the bitstream')
        tcl.push(['write_bitstream', '-force', self.bit_file])

    def program(self, tcl: TclScript):
        """
        Generate the tcl commands to program the bitstream to a board.
        """
        tcl.push()
        tcl.comment('(6) Program the connected FPGA device')
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


def main():
    voodoo = Voodoo()
    voodoo.read_blueprint()
    voodoo.write_tclscript()
    voodoo.run()
    pass


if __name__ == '__main__':
    main()
