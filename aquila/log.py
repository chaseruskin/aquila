from termcolor import colored


def info(*values, end: str='\n'):
    '''
    Print an informational message to the console.
    '''
    banner = colored("info", "blue", attrs=['bold']) + ':'
    print(banner, *values, end=end)


def warn(*values, end: str='\n'):
    '''
    Print a warning message to the console.
    '''
    banner = colored("warn", "yellow", attrs=['bold']) + ':'
    print(banner, *values, end=end)


def error(*values, end: str='\n', exit_on_err: bool=True):
    '''
    Print an error message to the console.

    If `exit_on_error` is true, exit with code 101.
    '''
    banner = colored("error", "red", attrs=['bold']) + ':'
    print(banner, *values, end=end)
    if exit_on_err:
        exit(101)
