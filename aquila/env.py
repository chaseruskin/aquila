"""
Wrapper module for accessing/modifying environment variables.
"""

import os
from aquila import log

class KvPair:
    """
    A key-value pair, useful for storing generics/parameters provided on the command-line.
    """
    def __init__(self, key: str, val: str):
        self.key = key
        self.val = val

    @staticmethod
    def from_str(s: str):
        # split on equal sign
        words = s.split('=', 1)
        if len(words) != 2:
            return None
        return KvPair(words[0], words[1])
    
    @staticmethod
    def from_arg(s: str):
        import argparse
        result = KvPair.from_str(s)
        if result is None:
            msg = "key-value pair "+__quote_str(s)+" is missing <value>"
            raise argparse.ArgumentTypeError(msg)
        return result

    def to_str(self) -> str:
        return self.key+'='+self.val
    
    def __str__(self):
        return self.key+'='+self.val
    
    @staticmethod
    def into_dict(pairs: list) -> dict:
        """
        Takes a list of KvPair instances and translates them into a dictionary.
        """
        result = {}
        for p in pairs:
            result[p.key] = p.val
        return result
    

class Seed:
    """
    An integer value used to set randomness.
    """

    MIN_SEED_VALUE = 0
    MAX_SEED_VALUE = (2**32)-1

    def __init__(self, seed: int=None):
        import random
        self.seed = seed
        if seed is None:
            self.seed = random.randint(Seed.MIN_SEED_VALUE, Seed.MAX_SEED_VALUE)
    
    def get_seed(self) -> int:
        """
        Returns the random seed.
        """
        return self.seed
    
    @staticmethod
    def from_str(s: str):
        if s is not None:
            s = int(s)
        return Seed(s)
    

def verify_all_generics_have_values(data: dict, gens: dict) -> bool:
    """
    Verifies all generics have some value, either from the command-line or as a default, where
    `data` is the raw string holding the serialized data of the top-level and `cli` is the list of generics passed
    from the command-line.

    Exits 101 if a generic value is not supplied.
    """
    dut_gens = data['generics']
    missing_gen = False
    for gen in dut_gens:
        if gen['default'] is None:
            if gen['name'] not in gens:
                log.error('missing value for generic "'+gen['name']+'"', exit_on_err=False)
                missing_gen = True
    if missing_gen == True:
        exit(101)


def read(key: str, default: str=None, missing_ok: bool=True) -> None:
    try:
        value = os.environ[key]
    except KeyError:
        value = None
    # do not allow empty values to trigger variable
    if value is not None and len(value) == 0:
        value = None
    if value is None:
        if missing_ok == False:
            exit("error: environment variable "+__quote_str(key)+" does not exist")
        else:
            value = default
    return value


def write(key: str, value: str):
    os.environ[key] = str(value)


def add_path(path: str, key: str='PATH') -> bool:
    """
    Adds the `path` to the environment variable `key`.
    """
    if path is not None and os.path.exists(path) and len(path) > 0 and os.getenv(key) is None:
        os.environ[key] = path
        return True
    if path is not None and os.path.exists(path) and len(path) > 0 and path not in os.getenv(key):
        os.environ[key] += os.pathsep + path
        return True
    return False


def prepend(key, value: str):
    if value is not None and os.path.exists(value) and len(value) > 0 and (os.getenv(key) is None or value not in os.getenv(key)):
        if os.getenv(key) is None:
            os.environ[key] = value + os.pathsep
        else:
            os.environ[key] = value + os.pathsep + os.environ[key]

@staticmethod
def append(key, value: str):
    if value is not None and os.path.exists(value) and len(value) > 0 and (os.getenv(key) is None or value not in os.getenv(key)):
        if os.getenv(key) is None:
            os.environ[key] = value
        else:
            os.environ[key] += os.pathsep + value


def __quote_str(s: str) -> str:
    """
    Wraps the string `s` around double quotes `\"` characters."
    """
    return '\"' + s + '\"'
