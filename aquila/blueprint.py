from typing import List as _List
from aquila import env

class Entry:
    """
    A single source item within a blueprint.
    """

    def __init__(self, fset: str, lib: str, path: str, deps: list=[]):
        self.fset = str(fset).upper().replace(' ', '-').replace('_', '-')
        self.lib = lib
        self.path = path
        self.deps = deps

    def is_builtin(self) -> bool:
        """
        Checks if the entry belongs to a builtin fileset (VHDL, VLOG, SYSV).
        """
        return self.fset == 'VHDL' or self.fset == 'VLOG' or self.fset == 'SYSV'
    
    def is_set(self, fset) -> bool:
        """
        Checks if the given entry belongs to this fileset `fset`.
        """
        return self.fset == str(fset).upper().replace(' ', '-').replace('_', '-')
    
    def is_aux(self, fset: str) -> bool:
        return self.fset == str(fset).upper().replace(' ', '-').replace('_', '-')

    def is_vhdl(self) -> bool:
        """
        Checks if the given entry belongs to the builtin VHDL filset.
        """
        return self.fset == 'VHDL'
    
    def is_vlog(self) -> bool:
        """
        Checks if the given entry belongs to the builtin VLOG filset.
        """
        return self.fset == 'VLOG'
    
    def is_sysv(self) -> bool:
        """
        Checks if the given entry belongs to the builtin SYSV filset.
        """
        return self.fset == 'SYSV'
    
    def get_deps(self) -> list:
        """
        Returns the list of file dependencies for the given entry.
        """
        return self.deps


class Blueprint:
    """
    A data structure that contains the topologically sorted list of all source entries.
    """

    def __init__(self, path: str=None, plan: str=None):
        """
        Loads entries from a blueprint.

        If no path and/or plan is provided, then it reads from the Orbit set environment variables.
        """
        import json
        self._file = path if path is not None else env.read("ORBIT_BLUEPRINT", missing_ok=False)
        self._plan = plan if plan is not None else env.read("ORBIT_BLUEPRINT_PLAN", missing_ok=False)

        self._entries = []
        # extract the list of entries from the file according to its plan
        with open(self._file, 'r') as bp:
            if self.get_plan() == 'tsv':
                for line in bp.readlines():
                    fset, lib, path = line.strip().split('\t')
                    self._entries += [Entry(fset, lib, path)]
            elif self.get_plan() == 'json':
                data = json.load(bp)
                for d in data:
                    self._entries += [Entry(d['fileset'], d['library'], d['filepath'], d['dependencies'])]
    
    def get_entries(self) -> _List[Entry]:
        """
        Returns the topologically sorted list of entries from the current
        blueprint.
        """
        return self._entries
    
    def get_plan(self) -> str:
        """
        Returns which plan was used for the current blueprint.
        """
        return self._plan

    def get_file(self) -> str:
        """
        Return the name of the file used to load the current list of entries.
        """
        return self._file
