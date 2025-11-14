'''
Backend target process for simulations with GHDL.
'''

import argparse
from typing import List
import os
import hashlib
import glob
import shutil
import sys

from aquila import log
from aquila import env
from aquila.blueprint import Blueprint, Entry
from aquila.env import KvPair, Seed
from aquila.process import Command
from aquila.ninja import Ninja

class Ghdl:

    SIM_MODE = 'sim'
    COM_MODE = 'com'

    MODES = [COM_MODE, SIM_MODE]

    def __init__(self, mode: str, generics: list, seed: Seed, time_res: str):
        '''
        Construct a new GHDL instance.
        '''
        self._mode = mode
        self._generics: List[KvPair] = generics
        self._time_res = time_res
        self._seed = seed
        # additional instance variables
        self.bp = Blueprint()
        self.entries = self.bp.get_entries()
        self.work_lib = env.read('ORBIT_PROJECT_LIBRARY')
        self.libs = set()
        self._base_opts = ['--std=08', '--ieee=synopsys', '--workdir=build', '-P=build']
        self.dut_name = env.read('ORBIT_DUT_NAME')
        self.dut_path = env.read('ORBIT_DUT_FILE')
        self.tb_name = env.read('ORBIT_TB_NAME')
        self.top_sim_lib = env.read('ORBIT_PROJECT_LIBRARY')
        self.out_path = env.read('ORBIT_OUT_DIR')
        self.top_sim_name = self.dut_name if self.tb_name is None else self.tb_name
        self.top_json = env.read('ORBIT_DUT_JSON') if env.read('ORBIT_TB_JSON') is None else env.read('ORBIT_TB_JSON')
        # verify we are using the json plan for incremental compilation
        bp_plan = self.bp.get_plan()
        if bp_plan != 'json':
            log.error('using unsupported blueprint plan "'+bp_plan+'": ghdl requires using the "json" plan')

    @staticmethod
    def from_args(args: list):
        parser = argparse.ArgumentParser('ghdl', allow_abbrev=False)

        parser.add_argument('--run', '-r', action='store', choices=Ghdl.MODES, default=Ghdl.SIM_MODE)
        parser.add_argument('--generic', '-g', action='append', type=KvPair.from_arg, default=[], metavar='KEY=VALUE', help='set top-level generics')
        parser.add_argument('--time-res', '-t', metavar='UNITS', default='ps', help='set the simulation time resolution')

        args = parser.parse_args(args)
        return Ghdl(
            mode=args.run,
            generics=args.generic,
            seed=None,
            time_res=args.time_res,
        )

    def prepare(self):
        '''
        Writes a ninja build file.
        '''
        if self.top_json is not None:
            env.verify_all_generics_have_values(self.top_json, self._generics)

        nj = Ninja()

        def gen_out_file_name(path: str):
            name = os.path.splitext(os.path.basename(path))[0]
            sum = hashlib.sha1(bytes(path, 'utf-8')).hexdigest()[:8]
            return 'build/' + name + '.' + sum

        nj.add_def_var('lib', 'work')
        nj.add_def_var('opts', '-a '+' '.join(self._base_opts))

        nj.add_rule('vhdl', 'ghdl ${opts} --snap=${out} --work=${lib} ${in} > ${out}')

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
        # build the list of source files
        status = Command(['ninja']).spawn()
        if status.is_err():
            print('\n@@@ COMPILATION COMPLETE [FAILED] @@@\n')
            exit(status.value)
        elif self._mode == Ghdl.COM_MODE:
            print('\n@@@ COMPILATION COMPLETE [PASSED] @@@\n')
            exit(status.value)

    def run(self, extra_args: list=[]):
        '''
        Run the simulation.
        '''
        if self.top_sim_name is None:
            log.error('no top-level specified: cannot run simulation')
            
        fst_file = 'waves.fst'
        log_file = 'run.log'
        fcov_file = 'fcov.rpt'
        ccov_file = 'ccov.rpt'

        log_path = self.out_path + '/' + log_file
        fst_path = self.out_path + '/' + fst_file
        fcov_path = self.out_path + '/' + fcov_file
        ccov_path = self.out_path + '/' + ccov_file

        status = Command(['ghdl', '-r'] + self._base_opts + [
            '--time-resolution='+self._time_res, 
            '--coverage',
            '--work='+self.top_sim_lib,
            self.top_sim_name, 
            '--fst='+fst_path,
        ] + ['-g' + item.to_str() for item in self._generics] + extra_args).stream(log_path)
        
        ccov_files = glob.glob(self.out_path + '/coverage-*.json')
        # create the code cover report (TODO: go back use `ghdl coverage` command)
        for cf in ccov_files:
            import json
            with open(cf, 'r') as fd:
                cov_json = json.loads(fd.read())
            for table in cov_json['outputs']:
                if table['file'] == env.read('ORBIT_DUT_FILE'):
                    self.generate_code_coverage_file(table, ccov_file)
            os.remove(cf)

        # save off files as regression
        regression_dir = self.get_regression_dir()
        os.makedirs(regression_dir, exist_ok=True)

        print()
        if os.path.exists(ccov_path):
            log.info('code coverage report available at: \"'+ccov_path+'\"')
            shutil.copyfile(ccov_path, regression_dir+'/'+ccov_file)
        if os.path.exists(fcov_path):
            log.info('functional coverage report available at: \"'+fcov_path+'\"')
            shutil.copyfile(fcov_path, regression_dir+'/'+fcov_file)
        if os.path.exists(fst_file):
            log.info('simulation waveform available at: \"'+fst_path+'\"')
        if os.path.exists(log_path):
            log.info('simulation log available at: \"'+log_path+'\"')
            shutil.copyfile(log_path, regression_dir+'/'+log_file)
        print()

        is_ok = status.is_ok()
        is_ok = is_ok and self.analyze_results(log_path)

        if is_ok:
            print('@@@ SIMULATION COMPLETE [PASSED] @@@')
            exit(0)
        else:
            print('@@@ SIMULATION COMPLETE [FAILED] @@@')
            exit(101)

    def get_regression_dir(self) -> str:
        base_dir = self.out_path + '/' + 'regressions'
        gens = ''
        for g in self._generics:
            gens += '_'+g.key+'='+g.val.replace('.', '-').replace('/', '-').replace('\\', '-')
        seed = ''
        if self._seed is not None:
            seed = '_seed=' + str(self._seed.get_seed())

        full_path = base_dir + '/' + self.top_sim_name 
        if len(seed) > 0 or len(gens) > 0:
            full_path += '_' + gens + seed
        return full_path

    def generate_code_coverage_file(self, table: dict, out_path: str):
        '''
        Reads the structured json `table` and writes a nicer code coverage file.
        '''
        summary = '0/0 100.0%'

        hit_lines = 0
        total_lines = len(table['result'])
        for (_, hits) in table['result'].items():
            if hits > 0:
                hit_lines += 1

        if total_lines > 0:
            summary = str(hit_lines) + '/' + str(total_lines) + ' ' + str(round(float(hit_lines)/float(total_lines)*100.0, 1))+'%'
      
        with open(table['file'], 'r') as fd:
            src_code = fd.readlines()

        empty_prefix = '     -:'
        annotated_src_code = [
            # write the source
            empty_prefix+'    0:Source: '+table['file'],
            # write the summary
            empty_prefix+'    0:Summary: '+str(summary),
        ]

        for (i, src_line) in enumerate(src_code):
            i = i+1
            src_line = src_line.rstrip()
            num = '-'
            if str(i) in table['result']:
                num = str(table['result'][str(i)])
                # make zero hit locations more noticeable
                if num == '0':
                    num = '#####'
            num += ':'
            line_no = str(i)+':'
            prefix = num.rjust(7) + line_no.rjust(6)
            annotated_src_code += [prefix+src_line]
        with open(out_path, 'w') as fd:
            fd.write('\n'.join(annotated_src_code))

    def analyze_results(self, log_file) -> bool:
        '''
        Parses simulation output to determine a proper exit code.

        Returns True if passed, and False if failed.
        '''
        if os.path.exists(log_file) == False:
            return False
        has_err = False
        with open(log_file, 'r') as fd:
            has_err = fd.read().lower().count('error):')
        if has_err:
            return False
        return True


def main():
    ghdl = Ghdl.from_args(sys.argv[1:])
    ghdl.prepare()
    log.info('compiling source files...')
    ghdl.compile()
    log.info('running simulation...')
    ghdl.run()


if __name__ == '__main__':
    main()
