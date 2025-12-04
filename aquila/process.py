import shutil
import subprocess
from typing import List, Tuple
from enum import Enum

from aquila import env
from aquila import log

class Status(Enum):
    """
    An indication of whether a process is okay or not.
    """
    OKAY = 0
    FAIL = 101

    @staticmethod
    def from_int(code: int):
        if code == 0:
            return Status.OKAY
        else:
            return Status.FAIL

    def unwrap(self):
        # print an error message
        if self == Status.FAIL:
            exit(Status.FAIL.value)
        pass

    def is_ok(self) -> bool:
        return self == Status.OKAY

    def is_err(self) -> bool:
        return self == Status.FAIL
    
    def __int__(self):
        return int(self.value)
    pass


class Command:
    """
    A invocation of a command along with any arguments.
    """

    def __init__(self, args: list):
        self._command = shutil.which(args[0])
        if self._command == None:
            self._command = args[0]
        self._args = args[1:]

    def args(self, args: List[str]):
        if args is not None and len(args) > 0:
            self._args += args
        return self
    
    def arg(self, arg: str):
        # skip strings that are empty
        if arg is not None and str(arg) != '':
            self._args += [str(arg)]
        return self
    
    def spawn(self, verbose: bool=False) -> Status:
        job = [self._command] + self._args
        if verbose == True:
            command_line = self._command
            for c in self._args:
                command_line += ' ' + '"'+c+'"'
            log.info(command_line)
        try:
            child = subprocess.Popen(job)
        except FileNotFoundError:
            log.error('command not found: \"'+self._command+'\"', exit_on_err=False)
            return Status.FAIL
        status = child.wait()
        return Status.from_int(status)
    
    def record(self, path: str, mode: str='w') -> Status:
        """
        Writes the stdout and stderr to a file at `path`.
        """
        import re
        with open(path, mode) as fd:
            popen = subprocess.Popen([self._command] + self._args, stdout=fd, stderr=fd)
            status = popen.wait()
        text = ''
        with open(path, 'r') as fd:
            text = fd.read()
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        text = ansi_escape.sub('', text)
        with open(path, 'w') as fd:
            fd.write(text)
        return Status.from_int(status)
    
    def stream(self, path: str, mode: str='w') -> Status:
        """
        Writes the stdout and stderr to the terminal while also recording it to a file.
        """
        import re
        def execute(cmd):
            popen = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            for stdout_line in iter(popen.stdout.readline, ""):
                yield stdout_line
            popen.stdout.close()
            return_code = popen.wait()
            if return_code:
                raise subprocess.CalledProcessError(return_code, cmd)
        
        job = [self._command] + self._args
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        fd = open(path, mode)
        try:
            for line in execute(job):
                print(line, end='')
                data = ansi_escape.sub('', line)
                fd.write(data)
        except subprocess.CalledProcessError:
            fd.close()
            return Status.FAIL
        fd.close()
        return Status.OKAY

    def output(self, verbose: bool=False) -> Tuple[str, Status]:
        """
        Captures a subprocess's command output (stdout) to a string.

        Still outputs diagnostic output (stderr) to the console.
        """
        job = [self._command] + self._args
        # display the command being executed
        if verbose == True:
            command_line = self._command
            for c in self._args:
                command_line += ' ' + '"'+c+'"'
            log.info(command_line)
        # execute the command and capture channels for stdout and stderr
        try:
            pipe = subprocess.Popen(job, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except FileNotFoundError:
            log.error('command not found: \"'+self._command+'\"', exit_on_err=False)
            return ('', Status.FAIL)
        out, err = pipe.communicate()
        if err is not None:
            return (err.decode('utf-8'), Status.FAIL)
        if out is not None:
            return (out.decode('utf-8'), Status.OKAY)
        return ('', Status.OKAY)
    pass
