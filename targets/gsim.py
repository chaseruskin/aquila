# Defines a common workflow for working with the GHDL simulator and software
# models written in Python used for generating test vector I/O. Generics
# are passed to the software script as well as the VHDL testbench for
# synchronization across code. Works with GHDL mcode backend.
#
# The script is designed to be an entry point for an Orbit target.
#
# References:
#   https://github.com/ghdl/ghdl

from mod import Command, Status, Env, Generic, Blueprint, Entry
import argparse
from typing import List

def main():
    # append ghdl path to PATH env variable
    Env.add_path(Env.read("ORBIT_ENV_GHDL_PATH", missing_ok=True))

    # handle command-line arguments
    parser = argparse.ArgumentParser(prog='gsim', allow_abbrev=False)

    parser.add_argument('--lint', action='store_true', default=False, help='run static analysis and exit')
    parser.add_argument('--generic', '-g', action='append', type=Generic.from_arg, default=[], metavar='KEY=VALUE', help='override top-level VHDL generics')
    parser.add_argument('--std', action='store', default='93', metavar='EDITION', help="specify the VHDL edition (87, 93, 02, 08, 19)")
    parser.add_argument('--relax', action='store_true', help='enable relaxed semantic rules for ghdl')
    parser.add_argument('--exit-on', action='store', default='error', metavar='LEVEL', help='select severity level to exit on (default: error)')

    args = parser.parse_args()

    # set up environment and constants
    TB_NAME = Env.read("ORBIT_TB_NAME", missing_ok=True)
    LIBRARY: str = Env.read("ORBIT_IP_LIBRARY", missing_ok=False)

    IS_RELAXED = bool(args.relax)
    GENERICS: List[Generic] = args.generic
    STD_VHDL = str(args.std)
    LINT_ONLY = bool(args.lint)
    SEVERITY_LVL = str(args.exit_on)

    # construct the options for GHDL
    GHDL_OPTS = ['--ieee=synopsys', '--syn-binding']

    GHDL_OPTS += ['--std='+STD_VHDL]

    if IS_RELAXED == True:
        GHDL_OPTS += ['-frelaxed']

    # read blueprint
    compile_order: List[Entry] = []
    for entry in Blueprint().get_entries():
        if entry.is_vhdl():
            compile_order += [entry]
        pass

    # analyze units
    print("info: analyzing hdl source code ...")
    item: Entry
    for item in compile_order:
        print('  ->', Env.quote_str(item.path))
        Command('ghdl') \
            .arg('-a') \
            .args(GHDL_OPTS) \
            .args(['--work='+str(item.lib), item.path]) \
            .spawn() \
            .unwrap()
        pass

    pass

    # halt workflow here when only providing lint
    if LINT_ONLY == True:
        print("info: static analysis complete")
        exit(0)

    # run the VHDL simulation
    if TB_NAME is None:
        print('error: cannot proceed any further without a testbench\n\nhint: stop here using \"--lint\" to exit safely or set a testbench to run a simulation')
        exit(101)

    VCD_FILE = str(TB_NAME)+'.vcd'

    print("info: entering simulation for testbench", Env.quote_str(TB_NAME), "...")
    status: Status = Command('ghdl') \
        .arg('-r') \
        .args(GHDL_OPTS) \
        .args(['--work='+LIBRARY, TB_NAME, '--vcd='+VCD_FILE, '--assert-level='+SEVERITY_LVL]) \
        .args(['-g' + item.to_str() for item in GENERICS]) \
        .spawn()

    # tell user where the vcd file is
    print('info: simulation complete')
    vcd_path = Env.read("ORBIT_MANIFEST_DIR") + '/' + Env.read("ORBIT_TARGET_DIR") + '/' + Env.read("ORBIT_OUT_DIR") + '/' + VCD_FILE
    print('info: vcd available at: \"'+vcd_path+'\"')
    status.unwrap()
    pass


if __name__ == '__main__':
    main()
