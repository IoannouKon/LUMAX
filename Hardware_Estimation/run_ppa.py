#!/usr/bin/env python3
"""One-file LUMAX ASIC area/timing/power flow for Chipyard 1.13.0 / SKY130 HD.

Examples (no installation or synthesis happens with 'check'):
  python3 run_ppa.py check --b 4 --rf 4 --ys 2 --ids 8
  python3 run_ppa.py start --b 4 --rf 4 --ys 2 --ids 8 --macro-bundle /path/srams.json
  python3 run_ppa.py status

Maintained source: this file only. Hammer JSON, Tcl, netlists, reports and cache
manifests are generated outputs. Existing Config.scala and simulator builds are
never overwritten. An existing Chipyard jar is reused when it is newer than all
checked external inputs; current LUMAX sources are compiled afresh with the
Chisel compiler plugin into a private overlay. Otherwise SBT builds an isolated
jar. --rebuild-chipyard forces SBT. Content-checked stage caches are reused.

Real SRAM IP is required; blackboxes are NOT accepted as zero-area memories.
By default, wrappers/manifest are generated from ~/sram22_sky130_macros after
elaboration. Single-port SRAMs share the controller's mutually exclusive logical
operations. Simulation assertions reject collisions; no reads are silently lost.
This mapping requires one operation per bank/cycle and a common port clock. It
does NOT implement an arbitrarily concurrent multiported SRAM. The muxes and
read-output holding registers are included in physical synthesis.
--macro-bundle can instead name a custom technology-IP manifest:
 {
   "wrappers": ["./lumax_sram_wrappers.v"],
   "contracts": {
     "I_MEM_0_ext": {
       "depth":64,"width":64,"read":3,"write":1,"readwrite":0,
       "latency_cycles":1,"physical_cells":{"my_sram":3}
     }
   },
   "libraries": [{
     "cell":"my_sram","depth":64,"width":64,
     "lef":"./my_sram.lef",
     "liberty":{"tt":"./tt.lib","ss":"./ss.lib","ff":"./ff.lib"}
   }]
 }
There must be one contract per emitted *_ext SRAM type, with exact port geometry.
Wrappers must preserve the emitted synchronous memory semantics, including
read/write collision behavior and masks. Port adaptation/replication is real
hardware and must be represented by the wrappers and physical_cells counts.
The script checks interfaces and counts; it does not prove SRAM-wrapper behavior.
Files in the manifest are resolved relative to the manifest directory. Three
SRAM timing corners must match the standard-cell TT/SS/FF corners below.

Power defaults to explicitly labelled statistical activity, NOT workload power.
For workload power supply --vcd and --vcd-scope for THIS mapped hierarchy. Review
the activity-annotation report; RTL traces may leave internal mapped nets
unannotated. Missing SRAM power models make total power unavailable. No energy
per matmul is reported without explicit --matmul-cycles and workload activity.

The physical flow currently stops after placement and clock-tree synthesis.
Global/detailed routing and OpenRCX extraction are disabled; the old routed
flow is retained in comments. Wire parasitics are estimated from placement.
Post-CTS timing repair and verbose intermediate reports are disabled too;
one final area/timing/power analysis remains enabled.
No FPGA is targeted. Timing is an estimate for this ASIC technology,
floorplan and PVT; Fmax is a path-based estimate, not a frequency-search result.
DRC/LVS signoff, fabrication, package/SoC power and IR-drop closure are outside
this accelerator-block comparison. Timing and constraint failures remain
visible. Routing feasibility is not checked by this pre-route flow.
"""

import argparse
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve()
# This archived copy lives outside the Chipyard tree; CLI flags override both.
DEFAULT_CHIPYARD = Path(os.environ.get("LUMAX_CHIPYARD", str(Path.home()/"chipyard_1.13.0"))).expanduser()
DEFAULT_WORK_ROOT = Path(os.environ.get("LUMAX_PPA_WORK_ROOT", str(Path.home()/".cache/lumax-ppa"))).expanduser()
# Change these defaults here; each is also an explicit command-line option.
DEFAULTS = dict(b=4, rf=4, ys=2, ids=8, activations="fp16", max_r=8,
                max_k=4097, max_m=128, period_ns=20.0, uncertainty_ns=1.0,
                utilization=0.40, input_delay_ns=2.0, output_delay_ns=2.0,
                output_load_pf=0.05, threads=4, timeout=14400,
                activity=0.10, duty=0.50)
TOP = "LUMAXExample"
FLOW_MODE = "placement_cts_quick"
# Skip costly post-CTS timing repair and routing-dependent stages for now.
# Previous pre-route flow also ran the expensive post-CTS timing repair:
# SKIPPED_PAR_STEPS = ("global_route", "global_route_resize", "detailed_route", "extraction")
SKIPPED_PAR_STEPS = ("clock_tree_resize", "global_route", "global_route_resize",
                     "detailed_route", "extraction")
CORNERS = {"ss": ("setup", "1.60 V", "100 C", "ss_100C_1v60"),
           "ff": ("hold", "1.95 V", "-40 C", "ff_n40C_1v95"),
           "tt": ("extra", "1.80 V", "25 C", "tt_025C_1v80")}


class FlowError(RuntimeError):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def say(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def tcl(value):
    """A literal Tcl word; reject brace/newline injection in user file paths."""
    value = str(value)
    if any(c in value for c in "{}\n\r"):
        raise FlowError("Tcl paths/names cannot contain braces or newlines: " + value)
    return "{" + value + "}"


def execute(argv, cwd, env, log, timeout):
    start = time.monotonic()
    log = Path(log)
    with log.open("w") as out:
        out.write("COMMAND " + shlex.join(map(str, argv)) + "\n")
        out.flush()
        proc = subprocess.Popen(list(map(str, argv)), cwd=cwd, env=env,
                                stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            rc = proc.wait(timeout=timeout)
            if rc:
                raise FlowError(f"Exit {rc}: {log}")
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            raise
    return round(time.monotonic() - start, 3)


def cached_stage(folder, name, inputs, outputs, action):
    """Reuse only complete outputs with verified content, never merely a filename."""
    marker = folder / (name + ".done.json")
    key = fingerprint(inputs)
    if marker.exists():
        old = json.loads(marker.read_text())
        if old.get("key") == key and all(
                p.is_file() and old.get("outputs", {}).get(str(p)) == digest(p)
                for p in outputs):
            say("REUSE " + name)
            return dict(reused=True, seconds=0, original_seconds=old["seconds"])
    marker.unlink(missing_ok=True)
    # Stale reports from a failed rerun must never be mistaken for fresh output.
    for p in outputs:
        p.unlink(missing_ok=True)
    say("START " + name)
    start = time.monotonic()
    action()
    for p in outputs:
        if not p.is_file() or not p.stat().st_size:
            raise FlowError("Missing/empty output: " + str(p))
    seconds = round(time.monotonic() - start, 3)
    save(marker, dict(key=key, inputs=inputs, seconds=seconds,
                      outputs={str(p): digest(p) for p in outputs}))
    say(f"DONE {name}: {seconds}s")
    return dict(reused=False, seconds=seconds)


def activated_environment(cy):
    python = cy / ".conda-env/bin/python"
    conda = Path.home() / "miniforge3/etc/profile.d/conda.sh"
    if not python.is_file() or not conda.is_file():
        raise FlowError("Chipyard conda environment not found")
    shell = ('set -e; source "$1" >/dev/null; cd "$2"; '
             'source ./env.sh >/dev/null; "$3" -c '
             + shlex.quote('import json,os; print(json.dumps(dict(os.environ)))'))
    r = subprocess.run(["bash", "-c", shell, "ppa", str(conda), str(cy), str(python)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise FlowError("Cannot activate Chipyard: " + r.stderr[-500:])
    env = json.loads(r.stdout)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("JAVA_TOOL_OPTIONS", "-Xmx8G -Xss8M")
    return env


def locate(value, candidates, env):
    for candidate in ([value] if value else candidates):
        found = shutil.which(str(candidate), path=env.get("PATH"))
        if found:
            return str(Path(found).resolve())
    return None


def load_bundle(path):
    if not path:
        raise FlowError("Missing --macro-bundle: real SRAM wrappers + LEF + TT/SS/FF Liberty are required")
    path = Path(path).expanduser().resolve()
    data = json.loads(path.read_text())
    def file(value):
        p = Path(value).expanduser()
        p = p if p.is_absolute() else path.parent / p
        p = p.resolve()
        if not p.is_file():
            raise FlowError("Missing SRAM IP file: " + str(p))
        return str(p)
    data["wrappers"] = [file(p) for p in data["wrappers"]]
    if not data["wrappers"] or not data["contracts"] or not data["libraries"]:
        raise FlowError("SRAM bundle cannot be empty")
    for lib in data["libraries"]:
        lib["lef"] = file(lib["lef"])
        lib["liberty"] = {c: file(lib["liberty"][c]) for c in CORNERS}
        if lib["depth"] <= 0 or lib["width"] <= 0:
            raise FlowError("Invalid physical SRAM capacity")
        if not re.search(r"\bMACRO\s+" + re.escape(lib["cell"]) + r"\b", Path(lib["lef"]).read_text()):
            raise FlowError("SRAM LEF does not contain " + lib["cell"])
    data["manifest"] = str(path)
    data["file_hashes"] = {str(p): digest(p) for p in bundle_files(data)}
    return data


def bundle_files(bundle):
    return sorted(set(bundle["wrappers"] + [p for lib in bundle["libraries"]
                      for p in [lib["lef"], *lib["liberty"].values()]]))


def catalog_files(catalog):
    return sorted(p for p in catalog.glob("sram22_*/*")
                  if p.suffix in (".lib", ".lef", ".v"))


def sram22_catalog(catalog):
    """Read actual macro geometry/area; never infer area from storage bits."""
    macros = []
    for model in sorted(catalog.glob("sram22_*/*.v")):
        cell = model.stem
        match = re.fullmatch(r"sram22_(\d+)x(\d+)m\d+w\d+", cell)
        if not match:
            continue
        depth, width = map(int, match.groups())
        lef = model.with_suffix(".lef")
        libs = {c: model.parent/(cell+"_"+v[3]+".lib") for c,v in CORNERS.items()}
        if not lef.is_file() or not all(p.is_file() for p in libs.values()):
            continue
        size = re.search(r"\bSIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;", lef.read_text())
        mask = re.search(r"localparam\s+WMASK_WIDTH\s*=\s*(\d+)", model.read_text())
        if size and mask:
            macros.append(dict(cell=cell, depth=depth, width=width, mask_width=int(mask[1]),
                lef=str(lef), liberty={k:str(v) for k,v in libs.items()}, model=str(model),
                area_um2=float(size[1])*float(size[2])))
    if not macros:
        raise FlowError("No usable SRAM22 LEF/Verilog/TT/SS/FF macro views in " + str(catalog))
    return macros


def memory_wrapper(mem, macro):
    """One synchronous bank, tiled in width; strict one-operation contract."""
    name, depth, width = mem['module_name'], mem['depth'], mem['width']
    reads = mem['read']
    if mem['write'] != 1 or mem['readwrite'] or mem.get('masked') or reads < 1:
        raise FlowError("Automatic SRAM22 mapping requires unmasked read ports + one write port")
    aw, paw = max(1, (depth-1).bit_length()), (macro['depth']-1).bit_length()
    pw, lanes = macro['width'], math.ceil(width/macro['width'])
    ports = []
    for r in range(reads):
        ports += [f"input [{aw-1}:0] R{r}_addr", f"input R{r}_en", f"input R{r}_clk", f"output [{width-1}:0] R{r}_data"]
    ports += [f"input [{aw-1}:0] W0_addr", "input W0_en", "input W0_clk", f"input [{width-1}:0] W0_data"]
    text = [f"// SRAM22 port sharing for {name}: logical {depth}x{width}, {reads}R/1W.",
        "// Valid only with mutually exclusive bank operations and common clocks.",
        "// No latency added; disabled read ports retain their previous values.",
        f"module {name}(\n  "+",\n  ".join(ports)+"\n);",
        "wire read_en = "+" | ".join(f"R{r}_en" for r in range(reads))+";",
        "wire chip_en = W0_en | read_en;",
        f"wire [{paw-1}:0] address = W0_en ? W0_addr : "+
        " ".join(f"R{r}_en ? R{r}_addr :" for r in range(reads))+f" {paw}'d0;",
        f"wire [{lanes*pw-1}:0] macro_output;",
        f"wire [{lanes*pw-1}:0] write_data = W0_data;"]
    for lane in range(lanes):
        text += [f"{macro['cell']} lane_{lane} (",
            f"  .clk(R0_clk), .rstb(1'b1), .ce(chip_en), .we(W0_en),",
            f"  .wmask({macro['mask_width']}'h{(1<<macro['mask_width'])-1:x}), .addr(address),",
            f"  .din(write_data[{(lane+1)*pw-1}:{lane*pw}]), .dout(macro_output[{(lane+1)*pw-1}:{lane*pw}])",
            ");"]
    if reads == 1:
        text.append(f"assign R0_data = macro_output[{width-1}:0];")
    else:
        text += [f"reg [{reads-1}:0] last_read;",
                 "always @(posedge R0_clk) last_read <= {"+", ".join(f"R{r}_en" for r in reversed(range(reads)))+"};"]
        for r in range(reads):
            text += [f"reg [{width-1}:0] held_{r};",
                f"always @(posedge R0_clk) if (last_read[{r}]) held_{r} <= macro_output[{width-1}:0];",
                f"assign R{r}_data = last_read[{r}] ? macro_output[{width-1}:0] : held_{r};"]
    enables = ["W0_en"]+[f"R{r}_en" for r in range(reads)]
    pairs = [f"({a} && {b})" for i,a in enumerate(enables) for b in enables[i+1:]]
    clocks = " || ".join(["W0_clk !== R0_clk"]+[f"R{r}_clk !== R0_clk" for r in range(1,reads)])
    text += ["`ifndef SYNTHESIS", "always @(posedge R0_clk) begin",
        "  if ("+" || ".join(pairs)+f') $fatal(1, "LUMAX_SRAM_PORT_COLLISION {name}");',
        f'  if ({clocks}) $fatal(1, "LUMAX_SRAM_CLOCK_MISMATCH {name}");',
        "end", "`endif", "endmodule", ""]
    return "\n".join(text), lanes


def auto_bundle(metadata, folder, catalog):
    folder.mkdir(parents=True, exist_ok=True)
    macros, selected, contracts, sources = sram22_catalog(catalog), {}, {}, []
    for mem in metadata:
        options = [m for m in macros if m['depth'] >= mem['depth']]
        if not options:
            raise FlowError("No SRAM22 macro deep enough for " + mem['module_name'])
        macro = min(options, key=lambda m:(m['area_um2']*math.ceil(mem['width']/m['width']), m['cell']))
        source, lanes = memory_wrapper(mem, macro)
        sources.append(source)
        selected[macro['cell']] = macro
        contracts[mem['module_name']] = {k:mem[k] for k in ('depth','width','read','write','readwrite')}
        contracts[mem['module_name']].update(latency_cycles=1, physical_cells={macro['cell']:lanes},
            port_sharing='one logical bank operation per cycle; common clock; verified in simulation')
    wrapper = folder/'lumax_sram_wrappers.v'
    wrapper.write_text('\n'.join(sources))
    manifest = folder/'macro_bundle.json'
    save(manifest, dict(wrappers=[str(wrapper)], contracts=contracts,
        libraries=[{k:m[k] for k in ('cell','depth','width','lef','liberty')} for m in selected.values()],
        models=[m['model'] for m in selected.values()],
        mapping='SRAM22 single-port sharing; adapter mux/register area included; no controller stall insertion'))
    return load_bundle(manifest)


def verify_sram_mapping(metadata, bundle, folder, env, timeout=600):
    """Real Verilator tests against the downloaded SRAM behavioral models.

    Checks one-clock read latency, every logical read port, inactive-port hold,
    writes, idle cycles, address tails and the collision detector. This verifies
    the adapter under its stated contract; it is not a proof of the controller's
    one-operation invariant.
    """
    folder.mkdir(parents=True, exist_ok=True)
    code = ["module tb;", "reg clk=0; always #5 clk=~clk;"]
    for index, mem in enumerate(metadata):
        depth, width, reads = mem['depth'], mem['width'], mem['read']
        aw, prefix = max(1, (depth-1).bit_length()), f"m{index}"
        code += [f"reg {prefix}_done=0;", f"reg [{width-1}:0] {prefix}_gold[0:{depth-1}];",
                 f"reg [{width-1}:0] {prefix}_expected[0:{reads-1}];",
                 f"reg [{reads-1}:0] {prefix}_known=0;"]
        pins = []
        for r in range(reads):
            code += [f"reg [{aw-1}:0] {prefix}_a{r}=0; reg {prefix}_en{r}=0; wire [{width-1}:0] {prefix}_q{r};"]
            pins += [f".R{r}_addr({prefix}_a{r}), .R{r}_en({prefix}_en{r}), .R{r}_clk(clk), .R{r}_data({prefix}_q{r})"]
        code += [f"reg [{aw-1}:0] {prefix}_wa=0; reg {prefix}_we=0; reg [{width-1}:0] {prefix}_d=0;"]
        pins += [f".W0_addr({prefix}_wa), .W0_en({prefix}_we), .W0_clk(clk), .W0_data({prefix}_d)"]
        code += [mem['module_name']+f" {prefix}("+", ".join(pins)+");",
                 f"initial begin : test_{index}", "integer k, op, address;",
                 f"for(k=0;k<{depth};k=k+1) begin",
                 f"  @(negedge clk); {prefix}_we=1; {prefix}_wa=k; {prefix}_d={{$urandom,$urandom}};",
                 f"  {prefix}_gold[k]={prefix}_d; @(posedge clk); #1;", "end"]
        if index == 0:
            code += ['if ($test$plusargs("collision")) begin',
                     f"  @(negedge clk); {prefix}_we=1; {prefix}_en0=1; @(posedge clk); #1;",
                     '$fatal(1,"Collision check did not fire"); end']
        code += ["for(k=0;k<2500;k=k+1) begin", "  @(negedge clk);"]
        code += [f"  {prefix}_en{r}=0;" for r in range(reads)]
        code += [f"  {prefix}_we=0; op=$urandom%{reads+2}; address=$urandom%{depth};",
                 f"  if(op==0) begin {prefix}_we=1; {prefix}_wa=address; {prefix}_d={{$urandom,$urandom}}; {prefix}_gold[address]={prefix}_d; end"]
        for r in range(reads):
            code += [f"  if(op=={r+1}) begin {prefix}_en{r}=1; {prefix}_a{r}=address; {prefix}_expected[{r}]={prefix}_gold[address]; {prefix}_known[{r}]=1; end"]
        code += ["  @(posedge clk); #1;"]
        for r in range(reads):
            code += [f'  if({prefix}_known[{r}] && {prefix}_q{r} !== {prefix}_expected[{r}]) $fatal(1,"SRAM mismatch {mem["module_name"]} R{r} step=%0d",k);']
        code += ["end", "@(negedge clk);"]
        code += [f"{prefix}_en{r}=0;" for r in range(reads)]
        code += [f"{prefix}_we=0; {prefix}_done=1; end"]
    code += ["initial begin wait("+" && ".join(f"m{i}_done" for i in range(len(metadata)))+");",
             '$display("LUMAX_SRAM_ADAPTER_PASS"); $finish; end', "endmodule"]
    tb = folder/'tb.sv'
    tb.write_text('\n'.join(code)+'\n')
    verilator = shutil.which('verilator', path=env['PATH'])
    if not verilator:
        raise FlowError("Verilator is required to validate automatic SRAM adapters")
    execute([verilator, '--binary', '--timing', '--assert', '-Wno-fatal', '--top-module', 'tb',
             '-j', '2', '--Mdir', folder/'obj', tb, *bundle['wrappers'], *bundle['models']],
            folder, env, folder/'compile.log', timeout)
    execute([folder/'obj/Vtb'], folder, env, folder/'simulation.log', timeout)
    if 'LUMAX_SRAM_ADAPTER_PASS' not in (folder/'simulation.log').read_text():
        raise FlowError("SRAM simulation did not finish")
    with (folder/'collision.log').open('w') as log:
        proc = subprocess.run([str(folder/'obj/Vtb'), '+collision'], cwd=folder, env=env,
                              stdout=log, stderr=subprocess.STDOUT, timeout=60,
                              preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0,0)))
    if proc.returncode == 0 or 'LUMAX_SRAM_PORT_COLLISION' not in (folder/'collision.log').read_text():
        raise FlowError("SRAM collision detector failed its negative test")
    save(folder/'verified.json', dict(status='PASS', verilator=True, cycles_per_memory=2500,
        checks=['synchronous reads','all read ports','write/readback','disabled-port holds','idle cycles','collision rejection'],
        scope='Adapter under one-operation/common-clock contract; not a controller invariant proof',
        wrappers={p:digest(p) for p in bundle['wrappers']}))


def verify_controller_memories(args, rtl, bundle, folder, env):
    """Run actual LUMAX RTL with macro models and collision checks enabled.

    A C++ TileLink memory responder supplies variable latency/backpressure.
    Three sequential matmuls cross K windows when capacities allow, exercise
    two row groups and partial tails, and compare every output to a software
    dot product. This is bounded regression coverage, not a formal proof.
    """
    folder.mkdir(parents=True, exist_ok=True)
    cpp = r'''
#include "VLUMAXExample.h"
#include "verilated.h"
#include <vector>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <algorithm>
struct Reply { int source,size,opcode; uint64_t data,due; };
struct Sim {
  VLUMAXExample t;
  std::vector<uint8_t> memory=std::vector<uint8_t>(16*1024*1024,0xA5);
  std::vector<Reply> replies;
  uint64_t cycle=0;
  uint32_t rng=0x12345678;
  uint32_t random(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
  bool tick(){
    int selected=-1;
    for(int i=0;i<(int)replies.size();++i) if(replies[i].due<=cycle) selected=i;
    t.auto_tl_out_a_ready=(cycle%11!=0);
    t.auto_tl_out_d_valid=(selected>=0);
    if(selected>=0){auto r=replies[selected];t.auto_tl_out_d_bits_opcode=r.opcode;
      t.auto_tl_out_d_bits_size=r.size;t.auto_tl_out_d_bits_source=r.source;t.auto_tl_out_d_bits_data=r.data;}
    t.clock=0;t.eval();
    bool cmd=t.io_cmd_valid&&t.io_cmd_ready;
    bool d=t.auto_tl_out_d_valid&&t.auto_tl_out_d_ready;
    bool a=t.auto_tl_out_a_valid&&t.auto_tl_out_a_ready;
    Reply next{};
    if(a){
      uint64_t address=t.auto_tl_out_a_bits_address&~uint64_t(7);
      if(address+8>memory.size()) throw std::runtime_error("DMA address outside test memory");
      bool read=t.auto_tl_out_a_bits_opcode==4;
      if(!read&&t.auto_tl_out_a_bits_opcode>1) throw std::runtime_error("Unexpected TileLink opcode");
      if(read) std::memcpy(&next.data,&memory[address],8);
      else for(int i=0;i<8;++i) if(t.auto_tl_out_a_bits_mask&(1u<<i)) memory[address+i]=(t.auto_tl_out_a_bits_data>>(8*i))&255;
      next.source=t.auto_tl_out_a_bits_source;next.size=t.auto_tl_out_a_bits_size;
      next.opcode=read?1:0;next.due=cycle+2+random()%7;
    }
    t.clock=1;t.eval();
    if(d) replies.erase(replies.begin()+selected);
    if(a) replies.push_back(next);
    ++cycle;t.clock=0;t.eval();return cmd;
  }
  void command(int fn,uint64_t a=0,uint64_t b=0){
    t.io_cmd_bits_inst_funct=fn;t.io_cmd_bits_rs1=a;t.io_cmd_bits_rs2=b;t.io_cmd_valid=1;
    int wait=0;while(!tick()) if(++wait>1000000) throw std::runtime_error("RoCC command timeout");
    t.io_cmd_valid=0;
  }
  Sim(){
    t.io_resp_ready=1;t.io_cmd_bits_inst_opcode=0x0b;
    t.io_cmd_bits_inst_xs1=1;t.io_cmd_bits_inst_xs2=1;
    t.auto_tl_out_d_bits_denied=0;t.auto_tl_out_d_bits_corrupt=0;
    t.reset=1;for(int i=0;i<10;++i)tick();t.reset=0;for(int i=0;i<10;++i)tick();
  }
};
int main(int argc,char**argv){
  Verilated::commandArgs(argc,argv);
  try {
    Sim s;int total=0;
    for(int wb: {8,4,2}){
      int window=BANKS*RF*(1<<(8-wb));
      int R=std::min(MAX_R,YS*2), K=std::min(MAX_K,window+1), M=std::min(MAX_M,7);
      const int X=0x1000,W=0x200000,O=0x800000;
      std::vector<int> x(R*K),weights(K*M);
      std::fill(s.memory.begin()+X,s.memory.begin()+X+R*K*2+16,0);
      std::fill(s.memory.begin()+W,s.memory.begin()+W+(K*M*wb+7)/8+16,0);
      for(int i=0;i<R*K;++i){
        int q=int(s.random()%63)-31;x[i]=q;uint16_t raw;
        if(FP16){float f=q/8.0f;uint32_t bits;std::memcpy(&bits,&f,4);
          raw=q?uint16_t(((bits>>16)&0x8000)|((((bits>>23)&255)-127+15)<<10)|((bits>>13)&1023)):0;}
        else raw=uint16_t(q);
        std::memcpy(&s.memory[X+2*i],&raw,2);
      }
      for(int c=0;c<M;++c)for(int k=0;k<K;++k){
        int q=int(s.random()%(1u<<wb))-(1<<(wb-1));weights[k*M+c]=q;
        int bit=(c*K+k)*wb;s.memory[W+bit/8]|=uint8_t((q&((1<<wb)-1))<<(bit%8));
      }
      s.command(8,16|(uint64_t(wb)<<6)|(uint64_t(32)<<12));
      s.command(3,K,YS);s.command(5,(R+YS-1)/YS,R);s.command(2,O,M);s.command(0,X,BANKS*RF);
      int scaled=BANKS*RF*(1<<(8-wb));
      int ws=scaled/(8/wb),limit=(K+scaled-1)/scaled;
      s.command(1,W,uint64_t(scaled)|(uint64_t(ws)<<16)|(uint64_t(limit)<<32));
      s.command(10);s.command(6);
      bool busy=false,finished=false;
      for(int i=0;i<1000000;++i){s.tick();if(s.t.io_busy)busy=true;
        if(busy&&!s.t.io_busy&&s.replies.empty()){for(int j=0;j<10;++j)s.tick();finished=true;break;}}
      if(!finished)throw std::runtime_error("Matmul timeout");
      for(int r=0;r<R;++r)for(int c=0;c<M;++c){
        int64_t sum=0;for(int k=0;k<K;++k)sum+=int64_t(x[r*K+k])*weights[k*M+c];
        uint32_t expected,actual;float value=sum/8.0f;
        if(FP16)std::memcpy(&expected,&value,4);else expected=uint32_t(sum);
        std::memcpy(&actual,&s.memory[O+4*(r*M+c)],4);
        if(actual!=expected){std::printf("MISMATCH W%d r%d c%d actual=%08x expected=%08x\n",wb,r,c,actual,expected);return 1;}
        ++total;
      }
      std::printf("CONTROLLER PASS W%d X=%dx%d W=%dx%d outputs=%d cycles=%llu\n",wb,R,K,K,M,R*M,(unsigned long long)s.cycle);
    }
    std::printf("LUMAX_SRAM_CONTROLLER_PASS outputs=%d\n",total);return 0;
  }catch(const std::exception&e){std::fprintf(stderr,"ERROR: %s\n",e.what());return 1;}
}
'''
    defines = dict(BANKS=args.b, RF=args.rf, YS=args.ys, MAX_R=args.max_r,
                   MAX_K=args.max_k, MAX_M=args.max_m, FP16=int(args.activations=='fp16'))
    source = folder/'controller.cpp'
    source.write_text('\n'.join(f'#define {k} {v}' for k,v in defines.items())+'\n'+cpp)
    verilator = shutil.which('verilator', path=env['PATH'])
    execute([verilator, '--cc', '--exe', '--build', '--assert', '-Wno-fatal',
             '--top-module', TOP, '-j', '2', '--Mdir', folder/'obj',
             rtl, *bundle['wrappers'], *bundle['models'], source],
            folder, env, folder/'compile.log', 1200)
    execute([folder/'obj'/('V'+TOP)], folder, env, folder/'simulation.log', 300)
    log = (folder/'simulation.log').read_text()
    if 'LUMAX_SRAM_CONTROLLER_PASS' not in log:
        raise FlowError('Controller/macro simulation did not pass')
    save(folder/'verified.json', dict(status='PASS', actual_verilator=True,
        rtl_sha256=digest(rtl), precisions=[8,4,2], sequential=True,
        wrappers={p:digest(p) for p in bundle['wrappers']},
        scope='Bounded controller regression with macro models, port assertions and exact software dot products; not a formal proof'))


def preflight(args):
    cy = args.chipyard.resolve()
    env = activated_environment(cy)
    found = {}
    for name, candidates in dict(
        yosys=[Path.home()/".conda-yosys/bin/yosys", "yosys"],
        openroad=[Path.home()/".conda-openroad/bin/openroad", "openroad"],
        firtool=["firtool"], java=["java"], sbt=[cy/".conda-env/bin/sbt"], verilator=["verilator"]).items():
        found[name] = locate(getattr(args, name, None), candidates, env)
    missing = [name for name, path in found.items() if not path]
    pdk = (args.pdk or cy/".conda-env/share/pdk/sky130A").resolve()
    for rel in ("libs.ref/sky130_fd_sc_hd/lib", "libs.ref/sky130_fd_sc_hd/lef",
                "libs.ref/sky130_fd_sc_hd/techlef"):
        if not (pdk/rel).is_dir():
            missing.append(str(pdk/rel))
    rc = pdk/"libs.tech/openlane/rules.openrcx.sky130A.nom.spef_extractor"
    # Routed flow only: OpenRCX rules are not needed for placement estimates.
    # if not rc.is_file():
    #     missing.append(str(rc))
    bundle, catalog_hashes = None, None
    try:
        if args.macro_bundle:
            bundle = load_bundle(args.macro_bundle)
        else:
            sram22_catalog(args.sram_catalog)
            catalog_hashes = {str(p):digest(p) for p in catalog_files(args.sram_catalog)}
    except (FlowError, OSError, ValueError, KeyError) as error:
        missing.append(str(error))
    py = cy/".conda-env/bin/python"
    probe = subprocess.run([str(py), "-c", "import hammer.vlsi,hammer.par.openroad,hammer.synthesis.yosys"],
                           env=env, capture_output=True, text=True, timeout=30)
    if probe.returncode:
        missing.append("Hammer/OpenROAD/Yosys Python plugins")
    rc_probe = subprocess.run([str(py), "-c",
        "import pathlib,hammer.technology.sky130 as t; print(pathlib.Path(t.__file__).parent/'extra/setRC.tcl')"],
        env=env, capture_output=True, text=True, timeout=30)
    placement_rc = Path(rc_probe.stdout.strip()) if not rc_probe.returncode else None
    if placement_rc is None or not placement_rc.is_file():
        missing.append("Hammer SKY130 placement RC settings (extra/setRC.tcl)")
    versions = {}
    for name in ("yosys", "openroad", "firtool", "java", "verilator"):
        if found[name]:
            flag = dict(yosys="-V", openroad="-version", firtool="--version", java="-version", verilator="--version")[name]
            r = subprocess.run([found[name], flag], env=env, capture_output=True, text=True, timeout=30)
            if r.returncode:
                missing.append(f"{name}: version command failed")
            versions[name] = (r.stdout + r.stderr).strip()[:1500]
    capabilities = {}
    if found['openroad']:
        # Old routed capability: 'extract_parasitics'. No extraction is run now.
        commands = ['estimate_parasitics','report_checks','report_power','set_power_activity','read_vcd']
        script = '\n'.join('puts "PPA_CAP '+c+' [llength [info commands '+c+']]"' for c in commands)+'\nexit\n'
        r = subprocess.run([found['openroad'],'-exit','/dev/stdin'], input=script,
                           env=env, capture_output=True, text=True, timeout=30)
        capabilities = {k:bool(int(v)) for k,v in re.findall(r'PPA_CAP (\w+) (\d+)',r.stdout)}
        for c in commands[:-1]:
            if not capabilities.get(c):missing.append('OpenROAD command missing: '+c)
    if args.vcd and not args.vcd.is_file():
        missing.append("VCD: " + str(args.vcd))
    if args.vcd and not args.vcd_scope:
        missing.append("--vcd-scope is required with --vcd")
    if args.vcd and not capabilities.get('read_vcd'):
        missing.append('This OpenROAD build has no read_vcd; use statistical power or a compatible newer STA build')
    return dict(chipyard=str(cy), pdk=str(pdk), tools=found, versions=versions,
                missing=missing, bundle=bundle, catalog=str(args.sram_catalog),
                catalog_hashes=catalog_hashes, capabilities=capabilities, rc=str(rc),
                placement_rc=str(placement_rc) if placement_rc else None,
                flow_mode=FLOW_MODE, routing_performed=False), env


def source_snapshot(cy):
    files = []
    skip = {"target", ".git", "generated-src", "node_modules", "__pycache__", ".bloop",
            "verification", "software", "test_run_dir", "logs", ".classpath_cache"}
    suffixes = {".scala", ".sbt", ".properties", ".java", ".v", ".sv", ".fir", ".json", ".conf"}
    for root in (cy/"generators", cy/"tools", cy/"project"):
        for parent, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in names:
                p = Path(parent)/name
                if p.suffix in suffixes or "resources" in p.parts:
                    files.append(p)
    files += [cy/"build.sbt"]
    return {str(p.relative_to(cy)): digest(p) for p in sorted(set(files)) if p.is_file()}


def configure(original, args):
    replacements = dict(Rin=args.max_r, Cin=args.max_k, Cout=args.max_m,
        x_slice=args.b, Mem_row_factor=args.rf, y_slice=args.ys, Dma_Ids=args.ids,
        FP16_ACTIVATIONS=str(args.activations == "fp16").lower())
    text = original
    for key, value in replacements.items():
        text, n = re.subn(r"(?m)^(\s*" + key + r"\s*=\s*)(?:\d+|true|false)\b",
                          lambda m: m[1] + str(value), text)
        if n != 1:
            raise FlowError("Cannot override exactly one Config.scala field: " + key)
    return text


def extract_accelerator(source, dest):
    text = source.read_text()
    starts = list(re.finditer(r"^  (?:public )?(?:ext)?module ([^ :]+)\s*:", text, re.M))
    modules = {m[1]: text[m.start():starts[i+1].start() if i+1 < len(starts) else len(text)]
               for i, m in enumerate(starts)}
    required, pending = set(), [TOP]
    while pending:
        name = pending.pop()
        if name in required:
            continue
        if name not in modules:
            raise FlowError("Unresolved FIRRTL module: " + name)
        required.add(name)
        pending += re.findall(r"\binst \S+ of (\S+)", modules[name])
    if "DmaModule" not in required:
        raise FlowError("LUMAX DMA was not included")
    modules[TOP], n = re.subn(r"(?m)^(    input reset :) Reset\b", r"\1 UInt<1>", modules[TOP], count=1)
    if n != 1:
        raise FlowError("Cannot bind RoCC reset to synchronous reset")
    dest.write_text(text.splitlines()[0] + "\ncircuit " + TOP + " :\n" +
                    "\n".join(modules[n] for n in modules if n in required))


def split_firtool(folder):
    parts = re.split(r'^// ----- 8< ----- FILE "([^"]+)" ----- 8< -----\s*\n',
                     (folder/"LUMAX.raw").read_text(), flags=re.M)
    (folder/"LUMAX.v").write_text(parts[0])
    for i in range(1, len(parts), 2):
        p = folder/parts[i]
        if folder.resolve() not in p.resolve().parents:
            raise FlowError("Unsafe firtool output path")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(parts[i+1])


def liberty_cells(path):
    """Read balanced cell groups; delay tables contain nested braces."""
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", Path(path).read_text(), flags=re.S)
    cells = {}
    for m in re.finditer(r'\bcell\s*\(\s*"?([^\s"()]+)"?\s*\)\s*\{', text):
        depth, end, quoted, escaped = 1, m.end(), False, False
        while depth and end < len(text):
            char = text[end]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted:
                depth += (char == "{") - (char == "}")
            end += 1
        if depth:
            raise FlowError("Unbalanced Liberty cell in " + str(path))
        body = text[m.end():end-1]
        area = re.search(r"\barea\s*:\s*([\d.eE+-]+)\s*;", body)
        cells[m[1]] = dict(area=float(area[1]) if area else None,
            register=bool(re.search(r"\b(?:ff|latch)\s*\(", body)),
            power=bool(re.search(r"\binternal_power\s*\(", body) and
                       re.search(r"\b(?:leakage_power\s*\(|cell_leakage_power\s*:)", body)))
    return cells


def validate_bundle(bundle, metadata, rtl):
    contracts = bundle["contracts"]
    if set(contracts) != {m["module_name"] for m in metadata}:
        raise FlowError("SRAM contracts do not match emitted memories; inspect metadata/seq_mems.json")
    wrappers = "\n".join(Path(p).read_text() for p in bundle["wrappers"])
    expected = {}
    for mem in metadata:
        c = contracts[mem["module_name"]]
        for key in ("depth", "width", "read", "write", "readwrite"):
            if c.get(key) != mem[key]:
                raise FlowError(f"SRAM contract mismatch {mem['module_name']}.{key}")
        if c.get("latency_cycles") != 1 or not c.get("physical_cells"):
            raise FlowError("SRAM contract needs latency_cycles=1 and physical_cells counts")
        if not re.search(r"\bmodule\s+" + re.escape(mem["module_name"]) + r"\s*\(", wrappers):
            raise FlowError("Missing SRAM wrapper module " + mem["module_name"])
        for cell, count in c["physical_cells"].items():
            if not isinstance(count, int) or count < 1:
                raise FlowError("Invalid SRAM physical instance count")
            expected[cell] = expected.get(cell, 0) + count * len(mem["hierarchy"])
    if set(expected) - {lib["cell"] for lib in bundle["libraries"]}:
        raise FlowError("Missing physical SRAM libraries")
    # Hierarchy checking rejects missing physical cells. The IP provider must
    # separately verify wrapper behavior and the declared memory contract.
    return expected


def hammer_entry():
    """Use this same source file as Hammer's synthesis/P&R hook driver."""
    from hammer.vlsi import CLIDriver, HammerTool

    def flatten(tool: HammerTool) -> bool:
        tool.append("hierarchy -check -top " + tool.top_module)
        tool.append("flatten")
        return True

    def generic(tool: HammerTool) -> bool:
        tool.append("yosys proc")
        tool.append("hierarchy -check -top " + tool.top_module)
        tool.append("synth -top " + tool.top_module + " -noshare -noabc")
        tool.append("opt -purge")
        latch = tool.get_setting("synthesis.yosys.latch_map_file")
        if latch:
            tool.append("techmap -map " + latch)
        # A complete SDC is generated by the parent flow, rather than using the
        # plugin's legacy unconstrained 'set_load 5' SDC fragment.
        shutil.copyfile(tool.get_setting("lumax.ppa.sdc"), tool.mapped_sdc_path)
        return True

    def map_flops(tool: HammerTool) -> bool:
        tool.append("dffunmap")
        tool.append("dfflibmap -liberty " + tool.liberty_files_tt.split()[0])
        return True

    def json_netlist(tool: HammerTool) -> bool:
        tool.append("write_json " + tcl(Path(tool.run_dir)/"mapped.json"))
        return True

    # Old routed export, retained for restoring the complete routing flow:
    # def route_outputs(tool: HammerTool) -> bool:
    #     tool.block_append("write_db " + tcl(Path(tool.run_dir)/"routed.odb"))
    #     tool.block_append("write_def " + tcl(Path(tool.run_dir)/"routed.def"))
    #     tool.block_append("write_verilog " + tcl(Path(tool.run_dir)/"routed.v"))
    #     return True

    def placed_outputs(tool: HammerTool) -> bool:
        # Save real post-CTS placement, never label these outputs as routed.
        tool.block_append("global_connect")
        tool.block_append("estimate_parasitics -placement")
        tool.block_append("write_db " + tcl(Path(tool.run_dir)/"placed.odb"))
        tool.block_append("write_def " + tcl(Path(tool.run_dir)/"placed.def"))
        tool.block_append("write_verilog " + tcl(Path(tool.run_dir)/"placed.v"))
        return True

    class Driver(CLIDriver):
        def get_extra_synthesis_hooks(self):
            return [HammerTool.make_pre_insertion_hook("syn_generic", flatten),
                    HammerTool.make_replacement_hook("syn_generic", generic),
                    HammerTool.make_pre_insertion_hook("syn_map", map_flops),
                    HammerTool.make_post_insertion_hook("write_outputs", json_netlist)]

        def get_extra_par_hooks(self):
            # Old full-flow hook: all default routing/extraction steps enabled.
            # return [HammerTool.make_replacement_hook("write_design", route_outputs)]
            return [*(HammerTool.make_removal_hook(step) for step in SKIPPED_PAR_STEPS),
                    HammerTool.make_replacement_hook("write_design", placed_outputs)]

    sys.argv.remove("--_hammer-driver")
    Driver().main()


def sdc_text(args):
    return f"""# Nanoseconds and picofarads, consistent with SKY130 Liberty.
create_clock -name clock -period {args.period_ns} [get_ports clock]
set_clock_uncertainty {args.uncertainty_ns} [get_clocks clock]
set inports {{}}
foreach pin [all_inputs] {{
  if {{[lsearch -exact [get_ports clock] $pin] < 0}} {{ lappend inports $pin }}
}}
set_input_delay {args.input_delay_ns} -clock clock $inports
set_input_transition 0.15 $inports
set_output_delay {args.output_delay_ns} -clock clock [all_outputs]
set_load {args.output_load_pf} [all_outputs]
"""


def hammer_config(args, pre, folder, bundle):
    extra = []
    for lib in bundle["libraries"]:
        for key, (_, voltage, temp, _) in CORNERS.items():
            extra.append({"library": dict(name=lib["cell"] + "_" + key,
                nldm_liberty_file=lib["liberty"][key], lef_file=lib["lef"],
                corner=dict(nmos={"tt":"typical", "ss":"slow", "ff":"fast"}[key],
                            pmos={"tt":"typical", "ss":"slow", "ff":"fast"}[key], temperature=temp),
                supplies=dict(VDD=voltage, GND="0 V"))})
    return {
        "vlsi.core.technology":"hammer.technology.sky130",
        "technology.sky130.sky130A":pre["pdk"],
        "vlsi.core.synthesis_tool":"hammer.synthesis.yosys",
        "vlsi.core.par_tool":"hammer.par.openroad",
        "vlsi.core.build_system":"none", "vlsi.core.max_threads":args.threads,
        "synthesis.inputs.top_module":TOP,
        "synthesis.inputs.input_files":[str(folder/"LUMAX.v"), *bundle["wrappers"]],
        "synthesis.yosys.yosys_bin":pre["tools"]["yosys"],
        "par.openroad.openroad_bin":pre["tools"]["openroad"],
        "vlsi.technology.extra_libraries":extra,
        "vlsi.inputs.clocks":[dict(name="clock", period=f"{args.period_ns}ns",
                                   uncertainty=f"{args.uncertainty_ns}ns")],
        "vlsi.inputs.mmmc_corners":[dict(name=name, type=role, voltage=v, temp=t)
                                    for role, v, t, name in CORNERS.values()],
        "vlsi.inputs.default_output_load":f"{args.output_load_pf} pF",
        "lumax.ppa.sdc":str(folder/"constraints.sdc"),
        "par.openroad.floorplan_mode":"auto_macro",
        "par.openroad.timing_driven":True,
        # "par.openroad.write_reports":True,  # verbose intermediate timing reports
        "par.openroad.write_reports":False,  # keep only our final PPA analysis
        "par.openroad.global_placement.density":min(args.utilization + .15, .80),
        "par.openroad.setrc_file":pre["placement_rc"],
        # Old routed flow used one nominal extraction corner plus SS/FF/TT cells:
        # "par.openroad.openrcx_techfiles":[pre["rc"]],  # routed extraction disabled
        "vlsi.inputs.pin_mode":"generated", "vlsi.inputs.pin.generate_mode":"full_auto",
        "vlsi.inputs.pin.assignments":[dict(pins="*", layers=["met2", "met4"], side="bottom")],
    }


def analyze_tcl(folder, args):
    """Analyze post-CTS placement using the same RC settings as Hammer."""
    par = folder/"build/par-rundir"
    return f"""
# Post-CTS placement with connected PG; no signal routing or extracted SPEF.
source {tcl(folder/'analysis_libs.tcl')}
# Old routed flow: read_db {tcl(par/'routed.odb')}
read_db {tcl(par/'placed.odb')}
read_sdc {tcl(folder/'constraints.sdc')}
set_propagated_clock [all_clocks]
# Old routed flow: foreach corner {{setup hold extra}} {{ read_spef -corner $corner {tcl(par/(TOP+'.par.spef'))} }}
source {tcl(folder/'placement_rc.tcl')}
estimate_parasitics -placement
set out [open {tcl(folder/'timing.tsv')} w]
foreach corner {{setup hold extra}} {{
  set p [find_timing_paths -corner $corner -path_delay max -group_count 1]
  if {{[llength $p] > 0}} {{ puts $out "$corner.setup_slack_ns\t[get_property [lindex $p 0] slack]" }}
  set h [find_timing_paths -corner $corner -path_delay min -group_count 1]
  if {{[llength $h] > 0}} {{ puts $out "$corner.hold_slack_ns\t[get_property [lindex $h 0] slack]" }}
  set r [all_registers]
  if {{[llength $r] > 0}} {{
    set p [find_timing_paths -corner $corner -path_delay max -group_count 1 -from $r -to $r]
    if {{[llength $p] > 0}} {{ puts $out "$corner.reg_setup_slack_ns\t[get_property [lindex $p 0] slack]" }}
  }}
  lumax_report "checks_$corner.rpt" [list report_checks -corner $corner -path_delay min_max -group_count 10]
}}
close $out
lumax_report constraints_check.rpt [list check_setup -verbose]
lumax_report electrical_checks.rpt [list report_check_types -max_slew -max_capacitance -max_fanout -violators]
set block [ord::get_db_block]
set units [$block getDbUnitsPerMicron]
set area [$block getCoreArea]
set out [open {tcl(folder/'core_area.txt')} w]
puts $out [expr {{double([$area dx])*[$area dy]/$units/$units}}]
close $out
set counts [dict create]
foreach inst [$block getInsts] {{
  set m [$inst getMaster]
  set n [$m getName]
  dict incr counts $n
}}
set out [open {tcl(folder/'placed_cells.tsv')} w]
dict for {{name count}} $counts {{
  set master [[ord::get_db] findMaster $name]
  puts $out "$name\t$count\t[expr {{double([$master getWidth])*[$master getHeight]/$units/$units}}]\t[$master getType]"
}}
close $out
{('read_vcd -scope '+tcl(args.vcd_scope)+' '+tcl(args.vcd.resolve())) if args.vcd else ('set_power_activity -input -activity '+str(args.activity)+' -duty '+str(args.duty))}
set_power_activity -input_ports [get_ports reset] -activity 0 -duty 0
if {{[llength [info commands report_activity_annotation]]}} {{
  lumax_report activity_annotation.rpt [list report_activity_annotation]
}}
lumax_report power_tt.rpt [list report_power -corner extra]
puts "LUMAX_PPA_ANALYSIS_COMPLETE"
exit
"""


def parse_power(text):
    # OpenSTA reports watts: internal, switching, leakage, total, optional %.
    m = re.search(r"(?m)^\s*Total\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)", text)
    if not m:
        raise FlowError("Cannot parse OpenSTA power report")
    return dict(zip(("internal_w", "switching_w", "leakage_w", "total_w"), map(float, m.groups())))


def summarize(folder, args, bundle, metadata, library_cells, physical_dir):
    macro_names = {l["cell"] for l in bundle["libraries"]}
    area = dict(logic_um2=0.0, registers_um2=0.0, sram_um2=0.0, physical_only_um2=0.0)
    counts = dict(logic_cells=0, register_cells=0, sram_instances=0, physical_only_cells=0)
    physical_bits, memory = 0, []
    macros = {l["cell"]: l for l in bundle["libraries"]}
    power_complete = True
    for line in (folder/"placed_cells.tsv").read_text().splitlines():
        name, number, size, kind = line.split("\t")
        number, size = int(number), float(size)
        lib = library_cells.get(name)
        if name in macro_names:
            group, count_group = "sram_um2", "sram_instances"
            bits = macros[name]["depth"] * macros[name]["width"] * number
            physical_bits += bits
            memory.append(dict(cell=name, instances=number, area_um2=size*number, bits=bits))
        elif any(token in kind for token in ("FILL", "TAP", "ENDCAP", "SPACER")):
            group, count_group = "physical_only_um2", "physical_only_cells"
        elif lib and lib["register"]:
            group, count_group = "registers_um2", "register_cells"
        elif lib:
            group, count_group = "logic_um2", "logic_cells"
        else:
            raise FlowError("Unmodelled placed cell: " + name)
        area[group] += size * number
        counts[count_group] += number
        if name in macro_names and (not lib or not lib["power"]):
            power_complete = False
    area["placed_cell_and_macro_um2"] = sum(area.values())
    area["core_um2"] = float((folder/"core_area.txt").read_text())
    area["unoccupied_core_um2"] = area["core_um2"] - area["placed_cell_and_macro_um2"]
    if area["unoccupied_core_um2"] < 0:
        raise FlowError("Placed cell area exceeds core area; review floorplan/macro placement")
    area["logical_sram_bits"] = sum(m["depth"]*m["width"]*len(m["hierarchy"]) for m in metadata)
    area["physical_sram_bits"] = physical_bits
    area["basis"] = "post-CTS placed cells and SRAM macros; no routing closure"
    area.update(counts)
    area["memories"] = memory
    timing = {k: float(v) for k, v in (line.split("\t") for line in (folder/"timing.tsv").read_text().splitlines())}
    slack = timing.get("setup.reg_setup_slack_ns")
    min_period = args.period_ns - slack if slack is not None else None
    timing["estimated_fmax_mhz"] = 1000/min_period if min_period and min_period > 0 else None
    timing["fmax_basis"] = "SS register-path slack after placement/CTS with estimated wire RC; not a routed Fmax or target-clock search"
    timing["rc_corner"] = "placement-estimated interconnect using SKY130 layer RC; cell corners SS/FF/TT"
    timing["parasitics_basis"] = "estimate_parasitics -placement"
    checks = (folder/"constraints_check.rpt").read_text()
    timing["constraint_warnings"] = bool(re.search(r"(?i)warning|unconstrained|no clock|no input delay|no output delay", checks))
    timing["target_setup_met"] = timing.get("setup.setup_slack_ns", -math.inf) >= 0
    timing["hold_met"] = timing.get("hold.hold_slack_ns", -math.inf) >= 0
    timing["electrical_violations"] = "VIOLATED" in (folder/"electrical_checks.rpt").read_text()
    # Old routed completion checks (not meaningful without routing):
    # drc = physical_dir/(TOP+"_route_drc.rpt")
    # timing["routing_drc_report"] = str(drc)
    # timing["routing_drc_clean"] = drc.is_file() and not drc.read_text().strip()
    timing["routing_performed"] = False
    timing["routing_drc_report"] = None
    timing["routing_drc_clean"] = None
    power = parse_power((folder/"power_tt.rpt").read_text())
    power["basis"] = "VCD annotated + propagated activity" if args.vcd else "statistical input activity; not workload-specific"
    power["activity"] = args.activity if not args.vcd else None
    power["duty"] = args.duty if not args.vcd else None
    power["frequency_mhz"] = 1000/args.period_ns
    power["corner"] = "TT 25 C 1.80 V"
    power["parasitics_basis"] = "placement-estimated wire capacitance; post-CTS clock network"
    power["all_sram_power_models_present"] = power_complete
    if not power_complete:
        power["incomplete_model_report_w"] = power["total_w"]
        power["total_w"] = None
    power["activity_annotation_report"] = str(folder/"activity_annotation.rpt") if (folder/"activity_annotation.rpt").exists() else None
    power["energy_per_matmul_j"] = (power["total_w"]*args.matmul_cycles*args.period_ns*1e-9
        if args.matmul_cycles and args.vcd and power["total_w"] is not None else None)
    return area, timing, power


def report(folder, result):
    save(folder/"report.json", result)
    text = ["# LUMAX ASIC PPA", "", "Status: **" + result["status"] + "**", "",
            "Scope: LUMAX + DMA accelerator block; SKY130 HD. Rocket, caches, pad ring and package excluded.", "",
            "Flow: placement + clock-tree synthesis; post-CTS timing repair, intermediate timing reports, routing and OpenRCX disabled. Timing and power use placement-estimated wire RC.", "",
            "Configuration: `" + json.dumps(result["configuration"], sort_keys=True) + "`", ""]
    if result.get("error"):
        text += ["Incomplete: " + result["error"], ""]
    if result.get("area"):
        a, tm, p = result["area"], result["timing"], result["power"]
        text += ["| Area component | µm² |", "|---|---:|"]
        for key in ("logic_um2", "registers_um2", "sram_um2", "physical_only_um2", "placed_cell_and_macro_um2", "core_um2", "unoccupied_core_um2"):
            text.append(f"| {key} | {a[key]:,.2f} |")
        text += ["", f"SRAM: {a['sram_instances']} physical instances; {a['logical_sram_bits']:,} logical bits; {a['physical_sram_bits']:,} physical bits.",
            f"Logic cells: {a['logic_cells']:,}; register cells: {a['register_cells']:,}; physical-only cells: {a['physical_only_cells']:,}.",
            "Core area includes placement whitespace; it must not be added again to component area.", "",
            "| SRAM cell | Instances | Physical bits | Area µm² |", "|---|---:|---:|---:|"]
        for mem in a["memories"]:
            text.append(f"| {mem['cell']} | {mem['instances']} | {mem['bits']:,} | {mem['area_um2']:,.2f} |")
        text += ["", f"Estimated PRE-ROUTE Fmax: {tm['estimated_fmax_mhz']} MHz. {tm['fmax_basis']}.",
            f"Target clock: {1000/result['configuration']['period_ns']:.3f} MHz; target setup met: {tm['target_setup_met']}; hold met: {tm['hold_met']}.",
            f"Constraint warnings: {tm['constraint_warnings']}; electrical violations: {tm['electrical_violations']}. These require review before using the timing/power numbers for design decisions.",
            f"RC/PVT basis: {tm['rc_corner']}. Setup/hold/electrical checks are estimates before routing; routability and routing DRC are untested.", "",
            f"Total power: {p['total_w']} W. Basis: {p['basis']}; {p['corner']}; {p['frequency_mhz']:.3f} MHz.",
            "Power is evaluated at the specified target clock; it does not establish that this clock is achievable.",
            f"Power components: internal {p['internal_w']} W; switching {p['switching_w']} W; leakage {p['leakage_w']} W. {p['parasitics_basis']}.",
            f"SRAM power models complete: {p['all_sram_power_models_present']}. Energy/matmul: {p['energy_per_matmul_j']} J.", ""]
        if not p['all_sram_power_models_present']:
            text += ["Power components above cover only the available models; missing SRAM power is not treated as zero. Total power is unavailable.", ""]
    text += ["No physical hardware is needed. This flow performs ASIC floorplanning, placement and clock-tree synthesis only.",
             "Global routing, post-route resizing, detailed routing and OpenRCX extraction are commented out/disabled for now. No routed database or SPEF is required.",
             "COMPLETE means the pre-route estimation flow and its available checks completed; it does not establish routing/timing closure.",
             "No fabricated-silicon frequency/power guarantee, DRC/LVS signoff or SRAM-wrapper correctness proof is claimed.",
             "Power comparisons need the same clock and activity/workload. Timing and area need the same technology, capacities and floorplan policy.",
             "Generated logs/configs/Tcl and cached-stage manifests retain the provenance. Null means unavailable, never zero.", ""]
    (folder/"report.md").write_text("\n".join(text))


def run_flow(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = (root/"runner.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise FlowError("A PPA runner is already active in " + str(root))
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    folder = root/"runs"/stamp
    folder.mkdir(parents=True)
    config = {k: getattr(args, k) for k in DEFAULTS}
    config["workload_label"] = args.workload_label
    result = dict(status="RUNNING", configuration=config, area=None, timing=None,
                  power=None, stages={}, started=stamp, flow_mode=FLOW_MODE,
                  routing_performed=False, skipped_physical_steps=list(SKIPPED_PAR_STEPS))
    save(root/"latest.json", dict(folder=str(folder), pid=os.getpid()))
    start = time.monotonic()
    original_hash = None
    snapshot = None
    try:
        pre, env = preflight(args)
        # Save no environment variables: they can contain credentials.
        save(folder/"preflight.json", pre)
        if pre["missing"]:
            result["status"] = "BLOCKED"
            raise FlowError("; ".join(pre["missing"]))
        cy = Path(pre["chipyard"])
        source = cy/"generators/LUMAX/src/main/scala/Config.scala"
        original = source.read_text()
        original_hash = digest(source)
        snapshot = source_snapshot(cy)
        source_key = fingerprint(snapshot)
        assembly = root/"cache/assembly"/source_key
        assembly.mkdir(parents=True, exist_ok=True)
        jar = assembly/"chipyard.jar"
        build = ';project chipyard; set assembly / assemblyOutputPath := file(' + json.dumps(str(jar)) + '); assembly'
        existing = cy/'.classpath_cache/chipyard.jar'
        reuse_existing = (not args.rebuild_chipyard and existing.is_file() and
            all((cy/name).stat().st_mtime <= existing.stat().st_mtime for name in snapshot
                if not name.startswith('generators/LUMAX/')))
        def prepare_assembly():
            if reuse_existing:
                shutil.copyfile(existing, jar)
                (assembly/'assembly.log').write_text(
                    'Reused existing Chipyard dependency assembly: '+str(existing)+'\n'
                    'Guard: no checked non-LUMAX source/resource/build input is newer.\n'
                    'All current LUMAX Scala sources are recompiled into a private overlay.\n')
            else:
                execute([pre["tools"]["sbt"], build], cy, env, assembly/"assembly.log", args.timeout)
        result["stages"]["assembly"] = cached_stage(assembly, "assembly", dict(sources=snapshot, java=pre["versions"]["java"],
            sbt_launcher=digest(cy/"scripts/sbt-launch.jar"),
            base_jar_sha256=digest(existing) if reuse_existing else None), [jar], prepare_assembly)
        text = configure(original, args)
        # Hash all relevant technology views, not merely their paths.
        tech_files = list(Path(pre["pdk"]).glob("libs.ref/sky130_fd_sc_hd/**/*.lef"))
        tech_files += list(Path(pre["pdk"]).glob("libs.ref/sky130_fd_sc_hd/**/*.tlef"))
        stdlibs = {c: Path(pre["pdk"])/f"libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__{v[3]}.lib" for c, v in CORNERS.items()}
        # Old routed extraction input: [Path(pre["rc"])]. Use placement RC now.
        tech_files += list(stdlibs.values()) + [Path(pre["placement_rc"])]
        tech_hash = {str(p): digest(p) for p in tech_files}
        bundle = pre["bundle"]
        design_spec = dict(jar=digest(jar), config=text, versions=pre["versions"],
            script=digest(SCRIPT), technology=tech_hash, bundle=bundle,
            automatic_catalog=pre["catalog_hashes"],
            constraints={k: config[k] for k in ("period_ns", "uncertainty_ns", "input_delay_ns", "output_delay_ns", "output_load_pf", "utilization", "threads")})
        design = root/"cache/designs"/fingerprint(design_spec)
        design.mkdir(parents=True, exist_ok=True)
        save(folder/"design.json", dict(path=str(design), fingerprint=fingerprint(design_spec)))
        save(design/"provenance.json", design_spec)
        (design/"Config.scala").write_text(text)
        def elaborate():
            classes = design/"classes"
            classes.mkdir(exist_ok=True)
            coursier = Path.home()/".cache/coursier"
            compiler = next(coursier.rglob("scala-compiler-2.13.12.jar"))
            reflect = next(coursier.rglob("scala-reflect-2.13.12.jar"))
            plugin = next(coursier.rglob('chisel-plugin_2.13.12-6.5.0.jar'))
            sources = [p for p in (cy/'generators/LUMAX/src/main/scala').rglob('*.scala') if p.name != 'Config.scala']
            execute([pre["tools"]["java"], "-cp", os.pathsep.join(map(str, (compiler, reflect, jar))),
                "scala.tools.nsc.Main", "-classpath", jar, '-Xplugin:'+str(plugin), '-language:reflectiveCalls',
                "-d", classes, design/"Config.scala", *sources],
                design, env, design/"compile_config.log", args.timeout)
            for bits in (32, 64):
                shutil.copyfile(cy/f"generators/testchipip/src/main/resources/testchipip/bootrom/bootrom.rv{bits}.img", design/f"bootrom.rv{bits}.img")
            execute([pre["tools"]["java"], "-cp", str(classes)+os.pathsep+str(jar),
                "chipyard.Generator", "--target-dir", design, "--name", "PPA",
                "--top-module", "chipyard.harness.TestHarness", "--legacy-configs", "chipyard:LUMAXROcketConfig"],
                cy, env, design/"elaborate.log", args.timeout)
            extract_accelerator(design/"PPA.fir", design/"LUMAX.fir")
            execute([pre["tools"]["firtool"], design/"LUMAX.fir", "--format=fir", "--verilog", "--disable-opt",
                "--preserve-aggregate=none", "--disable-all-randomization", "--strip-debug-info", "--repl-seq-mem",
                "--repl-seq-mem-file="+str(design/"seq_mems.conf"),
                "--lowering-options=disallowLocalVariables,disallowPackedArrays,noAlwaysComb", "-o", design/"LUMAX.raw"],
                design, env, design/"firtool.log", args.timeout)
            split_firtool(design)
        result["stages"]["rtl"] = cached_stage(design, "rtl", design_spec,
            [design/"LUMAX.v", design/"metadata/seq_mems.json"], elaborate)
        metadata = json.loads((design/"metadata/seq_mems.json").read_text())
        save(folder/"memory_requirements.json", metadata)
        if bundle is None:
            bundle = auto_bundle(metadata, design/"memories", args.sram_catalog)
        save(folder/"memory_mapping.json", bundle)
        expected_macros = validate_bundle(bundle, metadata, design/"LUMAX.v")
        if not args.macro_bundle:
            result["stages"]["sram_validation"] = cached_stage(design, "sram_validation",
                dict(wrappers=bundle['file_hashes'], models={p:digest(p) for p in bundle['models']}),
                [design/'memory_validation/verified.json'],
                lambda: verify_sram_mapping(metadata, bundle, design/'memory_validation', env))
            result["stages"]["controller_memory_validation"] = cached_stage(design, "controller_memory_validation",
                dict(rtl=digest(design/'LUMAX.v'), wrappers=bundle['file_hashes'],
                     b=args.b, rf=args.rf, ys=args.ys, activations=args.activations),
                [design/'controller_memory_validation/verified.json'],
                lambda: verify_controller_memories(args, design/'LUMAX.v', bundle,
                                                  design/'controller_memory_validation', env))
        result['memory_mapping'] = dict(manifest=bundle['manifest'],
            assumption='SRAM22 shared ports require at most one operation per bank per cycle and common clocks'
            if not args.macro_bundle else 'Custom SRAM contract',
            controller_contract_regression_passed=not bool(args.macro_bundle),
            controller_invariant_proved=False)
        libcells = {}
        for p in [stdlibs["tt"], *[Path(l["liberty"]["tt"]) for l in bundle["libraries"]]]:
            libcells.update(liberty_cells(p))
        (design/"constraints.sdc").write_text(sdc_text(args))
        cfg = hammer_config(args, pre, design, bundle)
        save(design/"synthesis.json", cfg)
        py = cy/".conda-env/bin/python"
        base_cmd = [py, SCRIPT, "--_hammer-driver", "--obj_dir", design/"build"]
        syn = design/"build/syn-rundir"
        def synthesize():
            execute([*base_cmd, "-p", design/"synthesis.json", "syn"], design, env, design/"synthesis.log", args.timeout)
            if "Found and reported 0 problems." not in (syn/(TOP+".synth_check.rpt")).read_text():
                raise FlowError("Yosys check failed")
            mapped = json.loads((syn/"mapped.json").read_text())["modules"][TOP]["cells"]
            counts = {}
            for c in mapped.values():
                name = c["type"].lstrip("\\")
                if name not in libcells or libcells[name]["area"] is None:
                    raise FlowError("Unmapped/unknown-area cell after synthesis: " + name)
                counts[name] = counts.get(name, 0) + 1
            if any(counts.get(n, 0) != number for n, number in expected_macros.items()):
                raise FlowError("Physical SRAM counts do not match the wrapper contracts")
            save(design/"synthesis_cells.json", counts)
        result["stages"]["synthesis"] = cached_stage(design, "synthesis", design_spec,
            [syn/(TOP+".mapped.v"), syn/"mapped.json", design/"synthesis_cells.json", syn/"config_db_tmp.json"], synthesize)
        counts = json.loads((design/"synthesis_cells.json").read_text())
        mapped_area = sum(libcells[n]["area"]*number for n, number in counts.items())
        side = math.ceil(math.sqrt(mapped_area/args.utilization) + 100)
        cfg["vlsi.inputs.placement_constraints"] = [dict(path=TOP, type="toplevel", x=0, y=0,
            width=side, height=side, margins=dict(left=20, right=20, top=20, bottom=20))]
        # Explicit par inputs avoid depending on stale syn-to-par transfer files.
        cfg.update({"par.inputs.top_module":TOP, "par.inputs.input_files":[str(syn/(TOP+".mapped.v"))],
                    "par.inputs.post_synth_sdc":str(design/"constraints.sdc")})
        save(design/"physical.json", cfg)
        shutil.copyfile(pre["placement_rc"], design/"placement_rc.tcl")
        par = design/"build/par-rundir"
        # Old complete route/extraction stage retained for later restoration:
        # result["stages"]["physical"] = cached_stage(design, "physical", dict(spec=design_spec, cells=counts, side=side),
        #     [par/"routed.odb", par/"routed.def", par/"routed.v", par/(TOP+".par.spef")],
        #     lambda: execute([*base_cmd, "-p", design/"physical.json", "par"], design, env, design/"physical.log", args.timeout))
        result["stages"]["physical"] = cached_stage(design, "physical", dict(spec=design_spec, cells=counts, side=side),
            [par/"placed.odb", par/"placed.def", par/"placed.v"],
            lambda: execute([*base_cmd, "-p", design/"physical.json", "par"], design, env, design/"physical.log", args.timeout))
        # Reopen the placed ODB with all three explicitly matched library corners.
        lines = ["define_corners setup hold extra"]
        for corner, (role, _, _, _) in CORNERS.items():
            for p in [stdlibs[corner], *[l["liberty"][corner] for l in bundle["libraries"]]]:
                lines.append("read_liberty -corner " + role + " " + tcl(p))
        (folder/"analysis_libs.tcl").write_text("\n".join(lines)+"\n")
        # Reports are per invocation, while the placed design is reusable.
        shutil.copyfile(design/"constraints.sdc", folder/"constraints.sdc")
        shutil.copyfile(design/"placement_rc.tcl", folder/"placement_rc.tcl")
        # Marker-based capture works around empty 'report_power > file' output
        # in the OpenSTA version bundled with Chipyard's older OpenROAD.
        capture = '''proc lumax_report {name cmd} {
  puts "LUMAX_REPORT_BEGIN $name"
  uplevel 1 $cmd
  puts "LUMAX_REPORT_END $name"
}
'''
        analysis = capture + analyze_tcl(design, args).replace(str(design/"analysis_libs.tcl"), str(folder/"analysis_libs.tcl"))
        analysis = analysis.replace(str(design/"placement_rc.tcl"), str(folder/"placement_rc.tcl"))
        for name in ("timing.tsv", "checks_", "constraints_check.rpt", "electrical_checks.rpt", "core_area.txt", "placed_cells.tsv", "activity_annotation.rpt", "power_tt.rpt"):
            analysis = analysis.replace(str(design/name), str(folder/name))
        (folder/"analysis.tcl").write_text(analysis)
        result["stages"]["analysis"] = dict(seconds=execute([pre["tools"]["openroad"], "-exit", folder/"analysis.tcl"],
            folder, env, folder/"analysis.log", args.timeout), reused=False)
        analysis_log = (folder/"analysis.log").read_text()
        if "LUMAX_PPA_ANALYSIS_COMPLETE" not in analysis_log:
            raise FlowError("Pre-route placement analysis did not complete")
        for name, contents in re.findall(r"(?ms)^LUMAX_REPORT_BEGIN ([\w.]+)\n(.*?)^LUMAX_REPORT_END \1$", analysis_log):
            (folder/name).write_text(contents)
        result["area"], result["timing"], result["power"] = summarize(folder, args, bundle, metadata, libcells, par)
        tm, power = result["timing"], result["power"]
        # Routing DRC is unavailable, not failed or assumed clean. Preserve all
        # available pre-route checks and label their results as estimates.
        result["status"] = "COMPLETE" if all((tm["target_setup_met"], tm["hold_met"], not tm["constraint_warnings"],
            not tm["electrical_violations"], power["all_sram_power_models_present"])) else "REVIEW_REQUIRED"
        result["provenance"] = dict(design=str(design), design_key=fingerprint(design_spec),
            source_key=source_key, vcd_sha256=digest(args.vcd) if args.vcd else None)
    except KeyboardInterrupt:
        result["status"] = "INTERRUPTED"
        result["error"] = "Interrupted; incomplete stages will not be reused"
    except Exception as error:
        if result["status"] != "BLOCKED":
            result["status"] = "FAILED"
        result["error"] = str(error)
        say(result["status"] + ": " + str(error))
    finally:
        if original_hash is not None and digest(args.chipyard/"generators/LUMAX/src/main/scala/Config.scala") != original_hash:
            result["status"] = "SOURCE_CHANGED_DURING_RUN"
            result["error"] = "Config.scala changed externally during the run; results require review"
        if snapshot is not None and snapshot != source_snapshot(args.chipyard):
            result["status"] = "SOURCE_CHANGED_DURING_RUN"
            result["error"] = "RTL/build inputs changed externally during the run; results require review"
        result["elapsed_seconds"] = round(time.monotonic()-start, 3)
        report(folder, result)
        save(root/"latest.json", dict(folder=str(folder), pid=os.getpid(), status=result["status"]))
        say("Report: " + str(folder/"report.md"))
    return 0 if result["status"] in ("COMPLETE", "REVIEW_REQUIRED") else 1


def self_test():
    """Fast software checks only: no build, simulator or physical tools launched."""
    assert parse_power("Total 1.0e-3 2e-3 3e-4 3.3e-3 100%")['total_w'] == .0033
    assert tcl("a $b [c]") == "{a $b [c]}"
    with tempfile.TemporaryDirectory(prefix="lumax-ppa-test-") as temp:
        p = Path(temp)
        lib = p/"tiny.lib"
        lib.write_text('library (x) {cell (ff) {area: 4.2; ff(I,Q) {} internal_power() {} cell_leakage_power: 1;} cell(delay) {area:1.0;}}')
        parsed = liberty_cells(lib)
        assert parsed['ff']['register'] and parsed['ff']['power'] and not parsed['delay']['register']
        out = p/"out"
        calls = []
        def action():
            calls.append(1)
            out.write_text("valid")
        assert not cached_stage(p, "cache", dict(a=1), [out], action)['reused']
        assert cached_stage(p, "cache", dict(a=1), [out], action)['reused']
        out.write_text("corrupt")
        assert not cached_stage(p, "cache", dict(a=1), [out], action)['reused']
        assert not cached_stage(p, "cache", dict(a=2), [out], action)['reused']
        assert len(calls) == 3
    print("PASS: report parsing, Liberty groups, Tcl quoting, cache reuse/corruption/invalidation")
    return 0


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=("check", "run", "start", "status", "self-test"))
    p.add_argument("--chipyard", type=Path, default=DEFAULT_CHIPYARD)
    p.add_argument("--output", type=Path, default=DEFAULT_WORK_ROOT)
    for key, value in DEFAULTS.items():
        p.add_argument("--"+key.replace("_", "-"), type=type(value), default=value, help=f"default: {value}")
    p.add_argument("--macro-bundle", default=os.environ.get("LUMAX_SRAM_BUNDLE"))
    p.add_argument("--rebuild-chipyard", action='store_true', help='Force SBT instead of reusing a sufficiently recent Chipyard dependency jar')
    p.add_argument("--sram-catalog", type=Path,
                   default=Path(os.environ.get("LUMAX_SRAM_CATALOG", str(Path.home()/"sram22_sky130_macros"))))
    p.add_argument("--pdk", type=Path)
    for tool in ("yosys", "openroad", "firtool", "java", "sbt"):
        p.add_argument("--"+tool)
    p.add_argument("--vcd", type=Path)
    p.add_argument("--vcd-scope")
    p.add_argument("--workload-label", default="unspecified")
    p.add_argument("--matmul-cycles", type=int)
    return p


def main():
    p = parser()
    args = p.parse_args()
    args.sram_catalog = args.sram_catalog.expanduser().resolve()
    if args.command == "self-test":
        return self_test()
    if args.command == "status":
        latest = args.output/"latest.json"
        if not latest.is_file():
            print("No PPA run has been started.")
            return 0
        data = json.loads(latest.read_text())
        with (args.output/"runner.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                data["active"] = False
            except BlockingIOError:
                data["active"] = True
        print(json.dumps(data, indent=2))
        return 0
    if args.activations not in ("fp16", "int16"):
        p.error("--activations must be fp16 or int16")
    if any(getattr(args, k) <= 0 for k in ("b", "rf", "ys", "ids", "max_r", "max_k", "max_m", "period_ns", "threads", "timeout")):
        p.error("Hardware dimensions, clock period, threads and timeout must be positive")
    if args.max_r < args.ys:
        p.error("--max-r must be at least --ys")
    if not (0 < args.utilization < .8 and 0 <= args.activity <= 1 and 0 <= args.duty <= 1):
        p.error("Invalid utilization or activity/duty")
    if args.command == "check":
        pre, _ = preflight(args)
        print(json.dumps({k:v for k,v in pre.items() if k not in ("bundle", "catalog_hashes")}, indent=2))
        return 1 if pre["missing"] else 0
    if args.command == "start":
        args.output.mkdir(parents=True, exist_ok=True)
        # Reproduce the exact supplied arguments; only change the subcommand.
        argv = sys.argv[1:]
        argv[argv.index("start")] = "run"
        with (args.output/"runner.log").open("a") as log:
            proc = subprocess.Popen([sys.executable, "-u", "-B", str(SCRIPT), *argv],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True)
        print(f"Started PID {proc.pid}; log: {args.output/'runner.log'}")
        return 0
    def interrupt(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)
    return run_flow(args)


if __name__ == "__main__":
    if "--_hammer-driver" in sys.argv:
        hammer_entry()
    else:
        try:
            sys.exit(main())
        except (FlowError, OSError, ValueError) as error:
            print("ERROR:", error, file=sys.stderr)
            sys.exit(1)
