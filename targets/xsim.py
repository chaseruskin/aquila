#   Simple execution process for the Vivado Simulator (xsim).
#
#   Assumes Vivado's command-line tools are available via your PATH environment
#   variable.

import argparse
from mod import Blueprint, Env, Tcl, Generic, Command

def main():
    # append xsim (vivado) installation path to PATH env variable
    Env.add_path(Env.read("ORBIT_ENV_VIVADO_PATH", missing_ok=True))

    # handle command-line arguments
    parser = argparse.ArgumentParser(prog='xsim', allow_abbrev=False)

    parser.add_argument('--generic', '-g', action='append', type=Generic.from_arg, default=[], metavar='KEY=VALUE', help='override top-level VHDL generics')
    parser.add_argument('--mode', choices=['comp', 'elab', 'sim'], default='sim', help='select a workflow')
    parser.add_argument('--gui', action='store_true', default=False, help='open the gui')

    args = parser.parse_args()

    gui = args.gui
    mode = args.mode
    generics = args.generic

    # process the blueprint

    vhdl_src = []
    vlog_src = []
    sysv_src = []

    steps = Blueprint().parse()

    for step in steps:
        if step.is_vhdl():
            vhdl_src += [step]
        elif step.is_vlog():
            vlog_src += [step]
        elif step.is_sysv():
            sysv_src += [step]
        pass

    # compile sources
    print('info: compiling source files ...')

    local_lib = 'work'
    for step in steps:
        if step.is_builtin():
            print('  ->', Env.quote_str(step.path))
            local_lib = step.lib
        if step.is_vhdl():
            Command('xvhdl').arg('-incr').arg('-work').arg(step.lib).arg(step.path).spawn().unwrap()
        elif step.is_vlog():
            Command('xvlog').arg('-incr').arg('-work').arg(step.lib).arg(step.path).spawn().unwrap()
        elif step.is_sysv():
            Command('xvlog').arg('-sv').arg('-incr').arg('-work').arg(step.lib).arg(step.path).spawn().unwrap()
        pass

    if mode == 'comp':
        print('info: compilation complete')
        exit(0)

    # convert the stored generics into xelab compatible options for the command-line
    gen_args = []
    for g in generics:
        gen_args += ['-generic_top', g.to_str()]
        pass

    DUT_NAME = Env.read("ORBIT_DUT_NAME", missing_ok=False)
    TB_NAME = Env.read("ORBIT_TB_NAME", missing_ok=False)

    # elaborate the testbench
    Command('xelab').args(['-debug', 'typical']).args(['-top', local_lib + '.' + TB_NAME]).args(['-snapshot', TB_NAME]).args(gen_args).spawn().unwrap()

    if mode == 'elab':
        print('info: elaboration complete')
        exit(0)

    # create a basic tcl file
    tcl = Tcl('orbit.tcl')
    tcl.push('log_wave -recursive *', raw=True)
    tcl.push(['run', 'all'])
    tcl.push(['exit'])
    tcl.save()

    run_args = ['-R', '-tclbatch', 'orbit.tcl']

    # run the simulation 
    Command('xsim').arg(TB_NAME).args(run_args).spawn().unwrap()

    if mode == 'sim':
        print('info: simulation complete')
        exit(0)
    pass


if __name__ == '__main__':
    main()


# run_args = ['-R']
# if(tcl_config != None and sim_mode == GUI and False): # disable tcl files for now
#     run_args = ['--tclbatch', tcl_config]
# elif(sim_mode == REVIEW):
#     run_args = []

# if(sim_mode == CL):
#     log_wave_tcl_cmd = "log_wave -recursive *" if(wf_config == None) else "open_wave_config "+wf_config
#     simple_tcl = log_wave_tcl_cmd+'\nrun all\nexit\n'
#     with open('batch.tcl', 'w') as cl_tcl:
#         cl_tcl.write(simple_tcl)
#     run_args = ['--tclbatch', 'batch.tcl']

# gui_args = ['--gui'] if(sim_mode == GUI or sim_mode == REVIEW) else []

# # use an existing waveform config file if found for the current testbench and in gui mode
# wave_args = ['--view '+wf_config] if(wf_config != None and (sim_mode == GUI or sim_mode == REVIEW)) else []

# log_args = ['--log', LOG_FILE] if(sim_mode == CL) else []

# # verify a .wdb file exists for review
# if sim_mode == REVIEW and os.path.exists(snapshot+'.wdb') == False:
#     exit('error: no waveform database file (.wdb) found for \''+snapshot+'\'')

# snapshot_arg = [snapshot] if(sim_mode != REVIEW) else [snapshot+'.wdb']

# # run simulation through xilinx xsim (`run_args` must be last)
# if sim == True:
#     xsim_args = snapshot_arg + gui_args + wave_args + log_args + run_args
#     invoke('xsim', xsim_args)

#     if sim_mode == CL:
#         # open log to verify simulation passed
#         errors = 0
#         failures = 0
#         with open(LOG_FILE, 'r') as log:
#             for line in log.readlines():
#                 # skip comments
#                 if line.startswith('#'):
#                     continue
#                 # count errors and failures
#                 line = line.lower()
#                 if line.startswith('error: '):
#                     errors += 1
#                 elif line.startswith('failure: '):
#                     failures += 1
#             pass

#         # verify the simulation passed with no problems
#         if(errors > 0 or failures > 0):
#             exit('error: simulation reported '+str(errors)+' errors and '+str(failures)+' failures')