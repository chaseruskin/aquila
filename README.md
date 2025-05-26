# Constellation

A collection of related settings and processes for FPGA development with
[Orbit](https://github.com/chaseruskin/orbit), a package manager and build system for VHDL, Verilog, and SystemVerilog.

All settings are configured in [`config.toml`](config.toml). This is the file that must be recognized by Orbit for these settings to go into effect. 

### Installing

1. Clone this repository using `git`:
```
git clone https://github.com/chaseruskin/constellation.git "$(orbit env ORBIT_HOME)/ext/constellation"
```

2. Install the required Python packages using `pip`:
```
pip install -r "$(orbit env ORBIT_HOME)/ext/constellation/requirements.txt"
```

3. Include the configuration file using `orbit`:
```
orbit config --push include="ext/constellation/config.toml"
```


## Targets

Targets define processes for producing build artifacts to be invoked during the execution stage of Orbit's build process.

The following simulators/toolchains are supported:

Tool | Target(s) | Build | Test | Dependencies
-- | -- | -- | -- | --
GHDL | `gee` | y | y | python
ModelSim | `mojo` | y | y | python, ninja
Vivado Simulator | `visi` | y | y | python
Vivado | `voodoo`, `xpro` | y | | python
Quartus | `quartz` | y | | python

## Protocols

Protocols define processes for downloading an ip from the internet to be invoked during the download phase of Orbit's installation process.

The following protocols are supported:

Tool | Protocol(s) | Patterns | Dependencies
-- | -- | -- | --
git | `git` | `*.git`
