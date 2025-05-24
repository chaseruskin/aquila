# Creates a Vivado project for interactive use.
#
# Dependencies:
#   Vivado (tested: 2019.2)
#
# Reference:
#   https://grittyengineer.com/vivado-project-mode-tcl-script/

from mod import Env, Command, Generic, Tcl, Esc
from voodoo import Step
import argparse
import os

def main():
    # collect command-line arguments
    parser = argparse.ArgumentParser(prog='xpro', allow_abbrev=False)

    parser.add_argument('--generic', '-g', action='append', type=Generic.from_arg, default=[], metavar='KEY=VALUE', help='override top-level VHDL generics')
    parser.add_argument('--part', default='', required=False, help='specify the FPGA part number')
    parser.add_argument('--run', '-r', required=False, choices=['synth', 'impl', 'bit'], help='select the step to perform')
    parser.add_argument('--no-gui', action='store_true', help='do not open the gui')
    parser.add_argument('--interactive', '-i', action='store_true', help='leave the session running on error')
    parser.add_argument('--no-bat', action='store_true', help='do not use .bat extension to call vivado')

    args = parser.parse_args()

    # convert argument values to script variables
    fpga_part = str(args.part)
    no_bat = bool(args.no_bat)
    eda_step = Step.from_str(args.run) if args.run != None else None
    no_gui = bool(args.no_gui)
    generics = args.generic
    mode = 'batch' if args.interactive == False else 'tcl'

    # convert environment variables to script constants
    TOP = Env.read('ORBIT_TOP_NAME', missing_ok=True)
    IP_NAME: str = str(Env.read('ORBIT_IP_NAME', missing_ok=False))
    BLUEPRINT_FILE: str = str(Env.read('ORBIT_BLUEPRINT', missing_ok=False))
    XPR_FILE: str = str(IP_NAME) + '.xpr'

    VIVADO_CMD = 'vivado.bat' if no_bat == False and os.name == 'nt' else 'vivado'

    tcl = Tcl('orbit.tcl')

    # try to disable webtalk
    tcl.push(['config_webtalk', '-user', 'off'])

    # try to open project if it already exists
    if os.path.exists(XPR_FILE) == True:
        tcl.push(['open_project', XPR_FILE])
    else:
        if len(fpga_part) == 0:
            print('warning: using default xilinx because a part was not provided')
            # create the project
            tcl.push(['create_project', XPR_FILE, '.'])
        else:
            # create the project with specified part
            tcl.push(['create_project', '-part', fpga_part, XPR_FILE, '.'])
            pass
        pass

    # set project settings
    tcl.push(['set_property', 'simulator_language', 'mixed', Esc('['), 'current_project', Esc(']')])
    # update the part if one was provided
    if len(fpga_part) > 0:
        tcl.push(['set_property', 'part', fpga_part, Esc('['), 'current_project', Esc(']')])

    vhdl_count = 0
    vlog_count = 0
    # read source code files from blueprint
    with open(BLUEPRINT_FILE, 'r') as fh:
        steps = fh.read().splitlines()
        for step in steps:
            fileset, lib, path = step.split('\t', maxsplit=3)
            if fileset == 'VHDL':
                tcl.push(['set', 'file_obj', Esc('['), 'add_files', '-fileset', 'sources_1', path, Esc(']')])
                tcl.push('if { $file_obj != "" } {', raw=True)
                tcl.indent()
                tcl.push(['set_property', 'library', lib, '$file_obj'])
                tcl.dedent()
                tcl.push('}', raw=True)
                vhdl_count += 1
            if fileset == 'VLOG' or fileset == 'SYSV':
                tcl.push(['set', 'file_obj', Esc('['), 'add_files', '-fileset', 'sources_1', path, Esc(']')])
                tcl.push('if { $file_obj != "" } {', raw=True)
                tcl.indent()
                tcl.push(['set_property', 'library', lib, '$file_obj'])
                tcl.dedent()
                tcl.push('}', raw=True)
                vlog_count += 1
            if fileset == 'XDCF':
                tcl.push(['add_files', '-fileset', 'constrs_1', path])
            pass
        pass

    # set the target language based on what the majority of the codebase is written in
    target_language = 'verilog' if vlog_count >= vhdl_count else 'VHDL'
    tcl.push(['set_property', 'target_language', target_language, Esc('['), 'current_project', Esc(']')])

    # set the top-level design
    tcl.push(['set_property', 'top', TOP, Esc('['), 'get_fileset', 'sources_1', Esc(']')])

    # refresh compile order
    tcl.push(['update_compile_order', '-fileset', 'sources_1'])

    # set generics
    tcl_generics = []
    if len(generics) > 0:
        tcl.push(['set', 'original_generics', Esc('['), 'get_property', 'generic', Esc('['), 'current_fileset', Esc(']'), Esc(']')])
        unwind_generics = ''
        for g in generics:
            unwind_generics += str(g) + ' '
        tcl.push(['set_property', 'generic', '$original_generics ' + unwind_generics, Esc('['), 'current_fileset', Esc(']')])
        pass

    if eda_step != None:
        # launch synthesis
        if eda_step.value >= Step.Synth.value:
            # TODO: use the following command to reset if already exists
            # tcl.push(['reset_run', 'synth_1'])
            tcl.push(['launch_runs', 'synth_1'])
            tcl.push(['wait_on_run', 'synth_1'])
        # launch implementation
        if eda_step.value >= Step.Impl.value:
            # TODO: use the following command to reset if already exists
            # tcl.push(['reset_run', 'impl_1'])
            if eda_step.value >= Step.Bit.value:
                tcl.push(['launch_runs', 'impl_1', '-to_step', 'write_bitstream'])
            else:
                tcl.push(['launch_runs', 'impl_1'])
            tcl.push(['wait_on_run', 'impl_1'])
            pass
        pass

    # open the gui
    if no_gui == False:
        tcl.push(['start_gui'])
    else:
        tcl.push(['exit'])
    tcl.save()

    Command(VIVADO_CMD) \
        .args(['-mode', mode, '-nojournal', '-nolog', '-source', tcl.get_path()]) \
        .spawn()
    pass


if __name__ == '__main__':
    main()