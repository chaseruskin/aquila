# Runs ModelSim workflows for HDL simulations.
#
# References: 
# - https://www.microsemi.com/document-portal/doc_view/131617-modelsim-reference-manual
# - https://users.ece.cmu.edu/~jhoe/doku/doku.php?id=a_short_intro_to_modelsim_verilog_simulator
# - https://stackoverflow.com/questions/57392389/what-is-vsim-command-line
# - https://wikis.ece.iastate.edu/cpre584/images/3/3c/Modelsim_pe_user_10.0d.pdf

from mod import Env, Generic, Command, Blueprint, DoFile
from ninja import Ninja

from typing import List
from enum import Enum
import argparse
import os
import hashlib

class Mode(Enum):
    COMP = 0
    SIM = 1
    GUI = 2

    @staticmethod
    def get_choices() -> list:
        return ['comp', 'sim', 'gui']

    @staticmethod
    def from_str(s: str):
        s = s.lower()
        if s == 'comp':
            return Mode.COMP
        elif s == 'sim':
            return Mode.SIM
        elif s == 'gui':
            return Mode.GUI
        else:
            raise ValueError('invalid choice: '+s)

class Msim:

    def __init__(self):
        """
        Create a new instance of the `msim` target.
        """
        parser = argparse.ArgumentParser(prog='msim', allow_abbrev=False)

        parser.add_argument('--run', '-r', metavar='MODE', default='sim', choices=Mode.get_choices(), help='specify the mode to run')
        parser.add_argument('--generic', '-g', action='append', type=Generic.from_arg, default=[], metavar='KEY=VALUE', help='override top-level generics')

        args = parser.parse_args()

        # capture arguments into instance variables
        self.mode = Mode.from_str(args.run)
        self.generics: List[Generic] = args.generic

        # additional instance variables
        self.entries = Blueprint().get_entries()
        self.work_lib = Env.read('ORBIT_IP_LIBRARY')
        self.libs = set()

        self.tb_name = Env.read('ORBIT_TB_NAME')
        self.dut_name = Env.read('ORBIT_DUT_NAME')

        # append modelsim installation path to PATH env variable
        Env.add_path(Env.read("MODELSIM_PATH", missing_ok=True))

        self.out_dir = Env.read('ORBIT_OUT_DIR')
        self.cmp_log = self.out_dir + '/' + 'compile.log'
        self.do_file = self.out_dir + '/' + 'run.do'
        self.sim_log = self.out_dir + '/' + 'run.log'
        self.wlf_file = self.out_dir + '/' + str(self.tb_name) + '.wlf'
        # set modelsim to modify the current output directory
        ini_file = self.out_dir + '/' + 'modelsim.ini'
        if os.path.exists(ini_file) == False:
            Command('vmap').args(['-quiet', '-c']).spawn()
        Env.write('MODELSIM', ini_file)

    def write_build_file(self):
        """
        Writes a ninja build file.
        """
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

    def build(self):
        """
        Calls ninja to compile the source files.
        """
        # build the list of source files
        status = Command('ninja').spawn()
        if status.is_err():
            print('@@@ BUILD FAILED (see log: \"'+self.cmp_log+'\")')
        if self.mode == Mode.COMP:
            print('\n@@@ BUILD LOG: \"'+self.cmp_log+'\" @@@\n')
            exit(0)

    def write_do_file(self):
        """
        Generate the series of do file commands to implement the requested workflow.
        """
        do = DoFile(self.do_file)
        if self.mode == Mode.SIM or self.mode == Mode.GUI:
            self.initialize(do)
        if self.mode == Mode.SIM:
            self.simulate(do)
        do.save()

    def initialize(self, do: DoFile):
        """
        Adds commands to initialize the simulation with vsim.
        """
        do.comment('(1) Map libraries')
        for (lib, path) in self.libs:
            do.push(['vmap', '-quiet', lib, path])

        # (#) Compile the design
        # - already accomplished prior by using ninja build system

        do.comment('(2) Load the design into the simulator')
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
                if entry.fset == 'DOFI':
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
        """
        Adds commands to simulate the design with vsim.
        """
        do.comment('(3) Run the simulation')
        do.push('run -all')
        do.push('quit')

    def run(self):
        """
        Invoke modelsim to run the generated do file workflow.
        """
        status = Command('vsim') \
            .arg("-batch" if not self.mode == Mode.GUI else "-gui") \
            .args(['-do', self.do_file]) \
            .args(['-logfile', self.sim_log]) \
            .spawn()
        
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
    msim = Msim()
    # compile sources
    msim.write_build_file()
    msim.build()
    # run simulation
    msim.write_do_file()
    msim.run()


if __name__ == '__main__':
    main()
