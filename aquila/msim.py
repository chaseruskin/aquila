'''
Backend target process for simulations with ModelSim.
'''

# Runs ModelSim workflows for HDL simulations.
#
# References: 
# - https://www.microsemi.com/document-portal/doc_view/131617-modelsim-reference-manual
# - https://users.ece.cmu.edu/~jhoe/doku/doku.php?id=a_short_intro_to_modelsim_verilog_simulator
# - https://stackoverflow.com/questions/57392389/what-is-vsim-command-line
# - https://wikis.ece.iastate.edu/cpre584/images/3/3c/Modelsim_pe_user_10.0d.pdf

from typing import List
from enum import Enum
import argparse
import os
import hashlib
import sys

from aquila.env import KvPair
from aquila import log
from aquila import env
from aquila.process import Command
from aquila.blueprint import Blueprint, Entry
from aquila.script import DoFile
from aquila.ninja import Ninja

class Mode(Enum):
    COMP = 0
    SIM = 1
    GUI = 2

    @staticmethod
    def get_choices() -> list:
        return ['com', 'sim', 'gui']

    @staticmethod
    def from_str(s: str):
        s = s.lower()
        if s == 'com':
            return Mode.COMP
        elif s == 'sim':
            return Mode.SIM
        elif s == 'gui':
            return Mode.GUI
        else:
            raise ValueError('invalid choice: '+s)

class Msim:

    @staticmethod
    def from_args(args: list):
        '''
        Construct a new Msim instance from a set of arguments.
        '''
        parser = argparse.ArgumentParser(prog='msim', allow_abbrev=False)

        parser.add_argument('--run', '-r', metavar='MODE', default='sim', choices=Mode.get_choices(), help='specify the mode to run')
        parser.add_argument('--generic', '-g', action='append', type=KvPair.from_arg, default=[], metavar='KEY=VALUE', help='set top-level generics')

        args = parser.parse_args()
        return Msim(
            step=Mode.from_str(args.run),
            generics=args.generic
        )

    def __init__(self, step: Mode, generics: list):
        '''
        Construct a new Msim instance.
        '''
        self.bp = Blueprint()
        # capture arguments into instance variables
        self.mode = step
        self.generics: List[KvPair] = generics

        # additional instance variables
        self.entries = Blueprint().get_entries()
        self.work_lib = env.read('ORBIT_PROJECT_LIBRARY')
        self.libs = set()

        self.tb_name = env.read('ORBIT_TB_NAME')
        self.dut_name = env.read('ORBIT_DUT_NAME')

        # append modelsim installation path to PATH env variable
        env.add_path(env.read("MODELSIM_PATH", missing_ok=True))

        # verify we are using the json plan for incremental compilation
        bp_plan = self.bp.get_plan()
        if bp_plan != 'json':
            log.error('using unsupported blueprint plan "'+bp_plan+'": ghdl requires using the "json" plan')

        self.out_dir = env.read('ORBIT_OUT_DIR')
        self.cmp_log = self.out_dir + '/' + 'compile.log'
        self.do_file = self.out_dir + '/' + 'run.do'
        self.sim_log = self.out_dir + '/' + 'run.log'
        self.wlf_file = self.out_dir + '/' + str(self.tb_name) + '.wlf'
        # set modelsim to modify the current output directory
        self.ini_file = self.out_dir + '/' + 'modelsim.ini'
        env.write('MODELSIM', self.ini_file)

    def prepare_compilation(self):
        '''
        Writes a ninja build file.
        '''

        if self.mode.value != Mode.COMP.value:
            env.verify_all_generics_have_values(env.read('ORBIT_TB_JSON'), self.generics)

        nj = Ninja()

        def gen_out_file_name(path: str):
            name = os.path.splitext(os.path.basename(path))[0]
            sum = hashlib.sha1(bytes(path, 'utf-8')).hexdigest()[:8]
            return 'build/' + name + '.' + sum

        nj.add_def_var('lib', 'work')
        nj.add_def_var('opts', '-nologo -appendlog -logfile '+self.cmp_log)

        nj.add_rule('vhdl', 'vcom ${opts} -2008 -work ${lib} ${in} -outf ${out}')
        nj.add_rule('vlog', 'vlog ${opts} -work ${lib} ${in} -outf ${out}')
        nj.add_rule('sysv', 'vlog ${opts} -sv -work ${lib} ${in} -outf ${out}')

        entry: Entry
        for entry in self.entries:
            if not entry.is_builtin():
                continue
            self.libs.add((entry.lib, entry.lib))
            rule = entry.fset.lower()
            out = gen_out_file_name(entry.path)
            deps = [gen_out_file_name(p) for p in entry.deps]
            # add the build into the dependency graph
            nj.add_build(rule, [out], [entry.path], deps, {'lib': entry.lib})
        nj.save()

    def compile(self):
        '''
        Calls ninja to compile the source files.
        '''
        if os.path.exists(self.ini_file) == False:
            Command(['vmap', '-quiet', '-c']).spawn()
        # build the list of source files
        status = Command(['ninja']).spawn()
        if status.is_err():
            if os.path.exists(self.cmp_log):
                print('\n@@@ COMPILATION FAILED (see log: \"'+self.cmp_log+'\")\n')
            else:
                print('\n@@@ COMPILATION FAILED @@@\n')
            exit(status.value)
        if self.mode == Mode.COMP:
            print('\n@@@ COMPILATION LOG: \"'+self.cmp_log+'\" @@@\n')
            exit(status.value)

    def prepare_run(self):
        '''
        Generate the series of do file commands to implement the requested workflow.
        '''
        do = DoFile(self.do_file)
        if self.mode == Mode.SIM or self.mode == Mode.GUI:
            self.initialize(do)
        if self.mode == Mode.SIM:
            self.simulate(do)
        do.save()

    def initialize(self, do: DoFile):
        '''
        Adds commands to initialize the simulation with vsim.
        '''
        do.comment_step('Map libraries')
        for (lib, path) in self.libs:
            do.push(['vmap', '-quiet', lib, path])

        do.comment_step('Load the design into the simulator')
        vsim_args = [
            '-onfinish', 'stop', '-wlf', self.wlf_file,
            '+nowarn3116',
            '-work', self.work_lib,
            self.work_lib+'.'+self.tb_name
        ]
        # enable full visibility into every aspect of the design
        if self.mode == Mode.GUI:
            vsim_args += ['-voptargs=+acc']
        gen_args = ['-g' + g.to_str() for g in self.generics]
        
        do.push(['eval', 'vsim'] + vsim_args + gen_args)
        # load waves if exist and using GUI mode
        if self.mode == Mode.GUI:
            # try to find a waves file
            wave_file = None
            for entry in self.entries:
                if entry.fset == 'WAV':
                    wave_file = entry.path
                    break
            if wave_file != None:
                do.push(['source', wave_file])
            else:
                do.push('add wave -group tb -expand '+self.tb_name+'/*')
                do.push('add wave -group dut -expand '+self.tb_name+'/dut/*')
                # toggle leaf names
                do.push('configure wave -signalnamewidth 1')

    def simulate(self, do: DoFile):
        '''
        Adds commands to simulate the design with vsim.
        '''
        do.comment_step('Run the simulation')
        do.push('run -all')
        do.push('quit')

    def run(self):
        '''
        Invoke modelsim to run the generated do file workflow.
        '''
        status = Command(['vsim', 
            '-batch' if not self.mode == Mode.GUI else '-gui', 
            '-do', self.do_file,
            '-logfile', self.sim_log,
        ]).spawn()
        
        # perform some post-processing to get a valid exit code
        print('\n@@@ SIMULATION LOG: \"'+self.sim_log+'\" @@@\n')
        status.unwrap()
        # read log file to see if any errors occurred during simulation
        is_okay = False
        with open(self.sim_log, 'r') as fd:
            lines = fd.readlines()
            for line in reversed(lines):
                if line.startswith('# Errors: '):
                    is_okay = line.startswith('# Errors: 0')
                    break
        if not is_okay:
            exit(101)


def main():
    msim = Msim.from_args(sys.argv[1:])
    msim.prepare_compilation()
    msim.compile()
    msim.prepare_run()
    msim.run()


if __name__ == '__main__':
    main()
