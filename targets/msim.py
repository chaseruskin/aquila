# Runs ModelSim workflows for HDL simulations.
#
# Reference: https://www.microsemi.com/document-portal/doc_view/131617-modelsim-reference-manual

from mod import Env, Generic, Command, Blueprint, DoFile
from typing import List
import argparse


class Msim:

    def __init__(self):
        """
        Create a new instance of the `msim` target.
        """
        parser = argparse.ArgumentParser(prog='msim', allow_abbrev=False)

        parser.add_argument('--run', '-r', default='sim', choices=['comp', 'init', 'sim'], help='specify the workflow to execute')
        parser.add_argument('--gui', action='store_true', help='open the interactive gui')
        parser.add_argument('--generic', '-g', action='append', type=Generic.from_arg, default=[], metavar='KEY=VALUE', help='override top-level VHDL generics')
        parser.add_argument('--top-config', default=None, help='define the top-level configuration unit')

        args = parser.parse_args()

        # capture arguments into instance variables
        self.top_config = args.top_config
        self.gui = bool(args.gui)
        self.stage = str(args.run)
        self.generics: List[Generic] = args.generic

        # additional instance variables
        self.entries = []
        self.working_lib = None
        self.waves_file = None

        self.tb_name = Env.read('ORBIT_TB_NAME')
        self.dut_name = Env.read('ORBIT_DUT_NAME')

        self.out_dir = Env.read('ORBIT_OUT_DIR')
        self.do_file = self.out_dir + '/' + 'run.do'
        self.log_file = self.out_dir + '/' + 'run.log'
        self.wlf_file = self.out_dir + '/' + str(self.tb_name) + '.wlf'

    def read_blueprint(self):
        self.entries = Blueprint().parse()

    def write_dofile(self):
        """
        Generate the series of do file commands to implement the requested workflow.
        """
        do = DoFile(self.do_file)
        if self.stage == 'comp' or self.stage == 'init' or self.stge == 'sim':
            self.compile_sources(do)
        if self.stage == 'init' or self.stage == 'sim':
            self.initialize(do)
        if self.stage == 'sim':
            self.simulate(do)
        if not self.gui:
            do.push('quit')
        do.save()

    def compile_sources(self, do: DoFile):
        """
        Adds commands to compile the source code.
        """
        libs = []
        do.comment("(1) Compile source files")
        for entry in self.entries:
            if entry.lib not in libs:
                do.push(['vlib', entry.lib])
                do.push(['vmap', entry.lib, entry.lib])
                libs += [entry.lib]
            # write command based on fileset
            if entry.is_vhdl():
                do.push(['vcom', '-work', entry.lib, '"'+entry.path+'"'])
            elif entry.is_vlog():
                do.push(['vlog', '-work', entry.lib, '"'+entry.path+'"'])
            elif entry.is_sysv():
                do.push(['vlog', '-sv', '-work', entry.lib, '"'+entry.path+'"'])
            elif entry.is_aux('DOFI'):
                self.waves_file = entry.path
            # capture latest file to be compiled as the one with working lib
            self.working_lib = entry.lib

    def initialize(self, do: DoFile):
        """
        Adds commands to initialize the simulation with vsim.
        """
        do.comment('(2) Initialize simulation')
        top = self.tb_name if self.top_config == None else self.top_config
        vsim_args = [
            '-onfinish', 'stop', '-wlf', self.wlf_file,
            '+nowarn3116',
            '-work', self.working_lib, 
            self.working_lib+'.'+top
        ]
        gen_args = ['-g' + g.to_str() for g in self.generics]
        do.push(['eval', 'vsim'] + vsim_args + gen_args)
        # load waves if exist
        if self.waves_file != None:
            do.push(['source', self.waves_file])
        else:
            do.push('add wave -expand -group TB '+self.tb_name+'/*')
            do.push('add wave -expand -group DUT '+self.tb_name+'/dut/*')
            # toggle leaf names
            do.push('configure wave -signalnamewidth 1')

    def simulate(self, do: DoFile):
        """
        Adds commands to simulate the design with vsim.
        """
        do.comment('(3) Run simulation')
        do.push('run -all')

    def run(self):
        """
        Invoke modelsim to run the generated do file workflow.
        """
        # append modelsim installation path to PATH env variable
        Env.add_path(Env.read("ORBIT_ENV_MODELSIM_PATH", missing_ok=True))
        # reference: https://stackoverflow.com/questions/57392389/what-is-vsim-command-line
        status = Command('vsim') \
            .arg("-batch" if not self.gui else "-gui") \
            .args(['-do', self.do_file]) \
            .args(['-logfile', self.log_file]) \
            .spawn()
        
        print('\n@@@ RUN LOG: \"'+self.log_file+'\" @@@\n')
        status.unwrap()
        # read log file to see if any errors occurred during simulation
        is_okay = False
        with open(self.log_file, 'r') as fd:
            lines = fd.readlines()
            for line in reversed(lines):
                if line.startswith('# Errors: '):
                    is_okay = line.startswith('# Errors: 0')
                    break
        if not is_okay:
            exit(101)


def main():
    msim = Msim()
    msim.read_blueprint()
    msim.write_dofile()
    msim.run()


if __name__ == '__main__':
    main()
