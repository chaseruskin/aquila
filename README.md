# Orbit Targets

A collection of scripts implementing processes and workflows to use as
[Orbit](https://github.com/chaseruskin/orbit) targets for FPGA development.

## Available Tools

The following simulators/toolchains are supported:

Tool | Target(s) | Build | Test
-- | -- | -- | -- 
GHDL | `gsim` | y | y
ModelSim | `msim` | y | y
Vivado Simulator | `xsim` | y | y
Vivado | `voodoo`, `xpro` | y |
Quartus | `quartz` | y |


## Installing

> __Note__: Before installing the targets, it is assumed you are working on a system with [`orbit`](https://github.com/chaseruskin/orbit) already installed.

To apply these configurations to Orbit:

1. Clone this repository using `git`:

```
git clone https://github.com/chaseruskin/orbit-targets.git "$(orbit env ORBIT_HOME)/targets/chaseruskin"
```

2. Install the required Python packages using `pip`:
```
pip install -r "$(orbit env ORBIT_HOME)/targets/chaseruskin/requirements.txt"
```

3. Include the configuration file using `orbit`:

```
orbit config --push include="targets/chaseruskin/config.toml"
```

## Updating

To receive the latest changes:

```
git -C "$(orbit env ORBIT_HOME)/targets/chaseruskin" pull
```