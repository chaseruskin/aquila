# Aquila

A collection of settings and processes for FPGA development with
[Orbit](https://github.com/chaseruskin/orbit), a package manager and build system for VHDL, Verilog, and SystemVerilog.

All settings are configured in [`config.toml`](aquila/config.toml). This is the file that must be recognized by Orbit for these settings to go into effect.

> __Aquila__ is a constellation on the celestial equator, which represents the bird that carried Zeus/Jupiter's thunderbolts in Greek-Roman mythology.

### Installing

1. Install the repository as a Python package using `pip` (or your favorite Python package manager):
```
pip install git+https://github.com/chaseruskin/aquila.git
```

2. Include the path to the configuration file using `orbit`:
```
orbit config --push include="$(aquila-config --config-path)"
```

### Importing

Although not necessary, Aquila is distributed as an installable Python package such that it can be leveraged in future Orbit configurations that may exist outside of this project.

After Aquila is installed, you can import the `aquila` package into your own Python modules:
``` py
import aquila
from aquila import ninja

nj = ninja.Ninja()
```

## Targets

Targets define processes for producing build artifacts to be invoked during the execution stage of Orbit's build process.

The following simulators/toolchains are supported:

Tool | Target(s) | Build | Test | Dependencies
-- | -- | -- | -- | --
GHDL | `ghdl` | | y | python, ninja
ModelSim | `msim` | | y | python, ninja
Vivado | `vi` | y | | python, ninja
Quartus | `quartz` | y | | python

## Protocols

Protocols define processes for downloading a project from the internet to be invoked during the download phase of Orbit's installation process.

The following protocols are supported:

Tool | Protocol(s) | Patterns | Dependencies
-- | -- | -- | --
git | `git` | `*.git` |
