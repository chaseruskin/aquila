"""
Module for interfacing with an Orbit project's manifest file. 
"""

import toml
from aquila import env
from aquila.process import Command
import json
import time
from termcolor import colored
from aquila import log


class Manifest:

    def __init__(self, path: str=None):
        self.path = path if path is not None else env.read('ORBIT_MANIFEST_FILE', missing_ok=False)
        self.data = dict()
        with open(self.path, 'r') as fd:
            self.data = toml.loads(fd.read())

    def get(self, table: str):
        """
        Attempts to fetch data from `table` with the internal TOML dictionary.

        Returns None if missing a key along with way.
        """
        parts = table.split('.')
        subtable = self.data
        for p in parts:
            try:
                subtable = subtable[p]
            except:
                return None
        return subtable


class TestModule:

    def __init__(self, dut: str=None, tb: str=None, generics: dict={}, seed: int=None):
        self.dut = dut
        self.tb = tb
        self.generics = generics
        self.seed = seed

    def get_dirname(self) -> str:
        """
        Returns the unique directory name for this test module.
        """
        gens = ''
        for (k, v) in list(self.generics.items()):
            gens += '_'+str(k)+'='+str(v).replace('.', '-').replace('/', '-').replace('\\', '-')
        seed = ''
        if self.seed is not None:
            seed = '_seed=' + str(self.seed)

        dir_name = ''
        if self.dut is not None:
            dir_name += self.dut
        if self.tb is not None:
            if self.dut is not None:
                dir_name += '__'
            dir_name += self.tb
         
        if len(seed) > 0 or len(gens) > 0:
            dir_name += '_' + gens + seed
        return dir_name
    
    def get_dut(self) -> str:
        return self.dut
    
    def get_tb(self) -> str:
        return self.tb
    
    def get_generics(self) -> dict:
        return self.generics
    
    def get_seed(self) -> int:
        return self.seed
    
    def set_tb(self, name: str):
        self.tb = name

    def set_seed(self, seed: int):
        self.seed = seed

    def is_valid(self) -> bool:
        return self.dut is not None or self.tb is not None
    
    def __str__(self) -> str:
        result = ''
        if self.tb is not None:
            result = self.tb
        if self.dut is not None:
            if self.tb is not None:
                result += '::'
            result += self.dut
        if len(self.generics) > 0:
            result += ' (' + ' '.join([str(k)+'='+str(v) for (k, v) in self.generics.items()]) + ')'
        if self.seed is not None:
            result += ' #'+str(self.seed)
        return result


class TestRunner:

    def __init__(self, table: dict=None, default: TestModule=None):
        """
        Creates a new instance of the test runner
        """
        self.num_passed = 0
        self.start_time = None

        self.table = table if table is not None else Manifest().get('project.metadata.test')

        if self.table is None:
            self.table = []
    
        self.modules = []
        for entry in self.table:
            dut = entry.get('dut')
            tb = entry.get('tb')
            trials = entry.get('trials', [])
            if len(trials) == 0:
                self.modules += [TestModule(dut, tb, {}, None)]
            for trial in trials:
                generics = trial.get('generics', {})
                seed = trial.get('seed')
                self.modules += [TestModule(dut, tb, generics, seed)]
        if default is not None and default.is_valid():
            self.modules = [default]
        
        self.num_trials = len(self.modules)

    def is_isolated(self) -> bool:
        """
        Returns true if an explicit DUT/TB was provided.
        """
        return env.read('ORBIT_DUT_NAME') is not None or env.read('ORBIT_TB_NAME') is not None
    
    def get_modules(self) -> list:
        """
        Returns the list of modules to run.
        """
        return self.modules
    
    def disp_start(self):
        word = 'test' if self.num_trials == 1 else 'tests'
        stmt = '\nrunning '+str(self.num_trials)+' '+word
        print(stmt)
        # record the start time
        self.start_time = time.perf_counter()
    
    def disp_trial_start(self, trial: TestModule):
        stmt = 'test ' + str(trial)
        print(stmt, end=' ')

    def disp_trial_progress(self):
        stmt = '...'
        print(stmt, end=' ')
    
    def disp_trial_result(self, ok: bool, log: str=None):
        if ok:
            self.num_passed += 1
            stmt = colored('ok', "green")
        else:
            stmt = colored('failed', 'red')
            if log is not None:
                stmt += '\n  '+str(log)
        print(stmt)

    def disp_result(self) -> bool:
        # record the end time
        self.end_time = time.perf_counter()

        all_ok = self.num_passed == self.num_trials
        self.num_failed = self.num_trials - self.num_passed

        # determine how many seconds elapsed from start to finish
        elapsed = self.end_time - self.start_time
        
        stmt = '\ntest result: '
        if all_ok:
            stmt += colored('ok', "green")
        else:
            stmt += colored('failed', 'red')
        stmt += '. '+str(self.num_passed)+' passed; '+str(self.num_failed)+' failed; '+'finished in '+str(round(elapsed, 2))+'s\n'
        print(stmt)
        return all_ok
    
    def verify_tests_exist(self):
        """
        Checks that a valid test is available to run.
        """
        if self.is_isolated() and len(self.modules) == 0:
            log.error('no tests defined')
        elif self.modules[0].is_valid() == False:
            log.error('no tests defined')
    

def get_unit_json(name: str) -> dict:
    """
    Returns the JSON dictionary for the desired unit, None if not found.
    """
    data: str = Command([env.read('ORBIT'), 'get', '--json', name]).output()[0]
    if len(data.strip()) == 0:
        return None
    return json.loads(data)