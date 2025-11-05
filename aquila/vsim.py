# Profile: Hyperspace Labs
# Target: vsim
# References: https://github.com/ghdl/ghdl
#
# Run Verilog simulations with verilator.

import argparse
from typing import List

from mod import Command, Env, Generic, Blueprint, Hdl

# set up environment and constants
BENCH = Env.read("ORBIT_TB_NAME", missing_ok=True)
LIBRARY: str = Env.read("ORBIT_PROJECT_LIBRARY", missing_ok=False)

# handle command-line arguments
parser = argparse.ArgumentParser(prog='vsim', allow_abbrev=False)

parser.add_argument('--strict', action='store_true', default=False, help='enable all warnings')
parser.add_argument('--lint', action='store_true', default=False, help='run static analysis and exit')
parser.add_argument('--generic', '-g', action='append', type=Generic.from_arg, default=[], metavar='KEY=VALUE', help='override top-level verilog parameters')

args = parser.parse_args()

GENERICS: List[Generic] = args.generic
LINT_ONLY = bool(args.lint)
WALL = bool(args.strict)

VERI_OPTS = []

if LINT_ONLY == True:
    VERI_OPTS += ['--lint-only']

if WALL == True:
    VERI_OPTS += ['-Wall']

# read blueprint
rtl_order: List[Hdl] = []
for rule in Blueprint().parse():
    if rule.fset == 'VLOG':
        rtl_order += [Hdl(rule.fset, rule.lib, rule.path)]
    elif rule.fset == 'SYSV':
        rtl_order += [Hdl(rule.fset, rule.lib, rule.path)]
    pass

# analyze units
print("info: analyzing source code ...")
item: Hdl
for item in rtl_order:
    print('  ->', Env.quote_str(item.path))
    pass

files = [f.path for f in rtl_order]
Command('verilator') \
    .args(VERI_OPTS) \
    .args(files) \
    .spawn() \
    .unwrap()

# halt workflow here when only providing lint
if LINT_ONLY == True:
    print("info: static analysis complete")
    exit(0)

# run the VHDL simulation
if BENCH is None:
    print('error: cannot proceed any further without a testbench\n\nhint: stop here using \"--lint\" to exit safely or set a testbench to run a simulation')
    exit(101)
