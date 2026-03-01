# Third-Party Notices

This project uses or interacts with third-party software. Relevant notices are
listed below.

---

## Graphviz

**Website:** <https://graphviz.org>  
**License:** Eclipse Public License 1.0 (EPL-1.0)  
**License text:** <https://www.eclipse.org/legal/epl-v10.html>  
**Source repository:** <https://gitlab.com/graphviz/graphviz>

### Usage

`kicad-pcb` uses the `dot` command-line tool from Graphviz to compute
schematic layout positions when `--layout auto` or `--layout graphviz` is
specified.  Graphviz is **not bundled** with this package; it must be
installed separately on the host system.

> **Note:** A future release may include an optional bundled `dot` binary
> placed under `kicad-pcb/src/kicad_pcb/bin/dot`.  If a bundled binary is
> added it will be subject to the same EPL-1.0 terms listed below and its
> source will be referenced here.

### Installation

| Platform | Command |
|----------|---------|
| Debian / Ubuntu | `sudo apt-get install graphviz` |
| macOS (Homebrew) | `brew install graphviz` |
| Windows | Download installer from <https://graphviz.org/download/> |

To use a specific or non-standard `dot` binary, set the `GRAPHVIZ_DOT`
environment variable to its absolute path:

```bash
export GRAPHVIZ_DOT=/opt/local/bin/dot
```

Run `python scripts/kicad_pcb.py doctor` to confirm which binary is
discovered.

### EPL-1.0 Redistribution Summary

The Eclipse Public License 1.0 is a weak copyleft licence. Key points for
redistribution:

- You may use, copy, distribute and modify the software.
- If you distribute a modified version of an EPL-licensed program you must
  make the source of your modifications available under EPL-1.0 (or a
  compatible "Secondary License" as permitted by EPL-1.0 § 7).
- If you distribute the **unmodified** binary only (as an external tool, not
  linked into your product), no source disclosure is required for *your*
  code, but the Graphviz copyright notice and this EPL-1.0 reference must be
  included.

**This project does not link against, modify, or redistribute any Graphviz
library or binary.** Graphviz is invoked as a subprocess.  Users are
responsible for installing Graphviz on their own systems in accordance with
the EPL-1.0 terms.

Full license text: <https://www.eclipse.org/legal/epl-v10.html>
