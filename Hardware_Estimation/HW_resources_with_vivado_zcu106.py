#!/usr/bin/env python3
"""Build LUMAXzcu106 RTL with Chipyard and report post-route FPGA PPA with Vivado."""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


CHIPYARD = Path(os.environ.get("LUMAX_CHIPYARD", "/home/kioannou/chipyard_1.13.0")).expanduser().resolve()
FPGA = CHIPYARD / "fpga"
TOP = "ZCU106FPGATestHarness"
MANIFEST = f"chipyard.fpga.zcu106.{TOP}.LUMAXzcu106.vsrcs.f"
COMMON_TCL = FPGA / "fpga-shells/xilinx/common/tcl"
REPORT_TCL = Path(__file__).resolve().with_name("lumax_zcu106_reports.tcl")
BOARD_TCL = FPGA / "src/main/resources/zcu106/board.tcl"
DEFAULT_OUTPUT = FPGA / "generated-src/lumax_zcu106"
INSTALLED_VIVADO = Path("/tools/Xilinx/Vivado/2023.2/bin/vivado")


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accelerator", choices=("lumax", "gemmini"), default="lumax")
    parser.add_argument("--weight-bits", type=int, choices=(8, 4, 2), default=8)
    parser.add_argument("--generator-jar", type=Path, help="Frozen private FPGA generator assembly")
    parser.add_argument("--b", type=positive, default=2)
    parser.add_argument("--rf", type=positive, default=8)
    parser.add_argument("--ys", type=positive, default=1)
    parser.add_argument("--ids", type=positive, default=2)
    parser.add_argument("--activation", choices=("int16", "fp16"), default="int16")
    parser.add_argument("--rin", type=positive, help="Maximum activation rows (default: Y_SLICE)")
    parser.add_argument("--cin", type=positive, default=1024, help="Maximum input columns")
    parser.add_argument("--cout", type=positive, default=1024, help="Maximum output columns")
    parser.add_argument("--clock-mhz", type=positive, default=100)
    parser.add_argument("--jobs", type=positive, default=4, help="Vivado thread limit (1–8)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--vivado", help="Path to the Vivado executable")
    parser.add_argument("--saif", type=Path, help="Optional workload switching activity")
    parser.add_argument("--saif-scope", help="SAIF hierarchy prefix to strip")
    parser.add_argument("--rtl-only", action="store_true", help="Stop after Chipyard RTL generation")
    parser.add_argument("--reimplement", action="store_true", help="Run Vivado implementation even if cached")
    args = parser.parse_args()
    args.rin = args.rin or args.ys
    if args.rin < args.ys:
        parser.error("--rin must be at least --ys")
    if args.jobs > 8:
        parser.error("--jobs must be between 1 and 8")
    if args.saif_scope and not args.saif:
        parser.error("--saif-scope requires --saif")
    if args.saif and not args.saif.is_file():
        parser.error(f"SAIF file does not exist: {args.saif}")
    if args.accelerator == "gemmini" and not args.generator_jar:
        parser.error("Gemmini requires the private generator produced by run_vivado_comparison.py")
    if args.generator_jar and not args.generator_jar.is_file():
        parser.error(f"Generator assembly does not exist: {args.generator_jar}")
    return args


def build_environment(args, variant):
    env = os.environ.copy()
    conda = CHIPYARD / ".conda-env"
    riscv = conda / "riscv-tools"
    if not (riscv / "bin/riscv64-unknown-elf-gcc").is_file():
        if not env.get("RISCV"):
            raise RuntimeError("Activate the Chipyard 1.13.0 environment to set RISCV.")
        riscv = Path(env["RISCV"]).resolve()
    java_home = conda / "lib/jvm"
    if (java_home / "bin/java").is_file():
        env["JAVA_HOME"] = str(java_home)
    paths = [str(riscv / "bin"), str(conda / "bin")]
    if env.get("JAVA_HOME"):
        paths.insert(0, str(Path(env["JAVA_HOME"]) / "bin"))
    env["PATH"] = os.pathsep.join([*paths, env.get("PATH", "")])
    env["RISCV"] = str(riscv)
    settings = dict(B=args.b, RF=args.rf, YS=args.ys, IDS=args.ids, RIN=args.rin,
                    CIN=args.cin, COUT=args.cout, CLOCK_MHZ=args.clock_mhz,
                    FP16=str(args.activation == "fp16").lower(), BOOT_DIR=variant / "boot")
    env.update({f"LUMAX_ZCU106_{key}": str(value) for key, value in settings.items()})
    env["LUMAX_VIVADO_JOBS"] = str(args.jobs)
    env["LUMAX_VIVADO_ACCELERATOR"] = args.accelerator
    env["LUMAX_ZCU106_CHIPYARD"] = str(CHIPYARD)
    return env


def run_logged(command, cwd, env, log):
    with log.open("w") as output:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=output,
                                stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} exited with status {result.returncode}. See {log}")


def checkpoint_key(manifest, vivado_version, jobs):
    rtl = manifest.parent
    paths = {manifest, REPORT_TCL, BOARD_TCL, COMMON_TCL / "init.tcl", COMMON_TCL / "util.tcl"}
    for line in manifest.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            paths.add(rtl / line)
    paths.update(rtl.glob("*.vivado.tcl"))
    paths.update(rtl.glob("*.sdc"))
    paths.update(rtl.glob("*.xdc"))
    for pattern in ("*.h", "*.vh", "*.svh", "*.mem", "*.hex", "*.bin"):
        paths.update((rtl / "gen-collateral").rglob(pattern))
    digest = hashlib.sha256(f"{vivado_version}\nthreads={jobs}".encode())
    for path in sorted(paths):
        digest.update(str(path).encode() + b"\0")
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()[:20]


def main():
    args = parse_args()
    name = (f"b{args.b}_rf{args.rf}_ys{args.ys}_id{args.ids}_{args.activation}"
            f"_r{args.rin}_k{args.cin}_n{args.cout}_{args.clock_mhz}mhz")
    config = "LUMAXzcu106"
    if args.accelerator == "gemmini":
        config = f"GemminiW{args.weight_bits}zcu106"
        name = f"gemmini_w{args.weight_bits}_{args.clock_mhz}mhz"
    variant = args.output_dir.resolve() / name
    rtl = variant / "rtl"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    run = variant / "runs" / stamp
    run.mkdir(parents=True)
    env = build_environment(args, variant)
    long_name = f"chipyard.fpga.zcu106.{TOP}.{config}"
    manifest = rtl / f"{long_name}.vsrcs.f"
    print(f"Run directory: {run}", flush=True)
    with (variant / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # The FPGA Scala assembly is shared across hardware configurations.
        lock_path = DEFAULT_OUTPUT / ".chipyard.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as build_lock, tempfile.TemporaryDirectory(prefix="lumax-sbt-") as runtime:
            fcntl.flock(build_lock, fcntl.LOCK_EX)
            print(f"Chipyard RTL generation: {run / 'chipyard.log'}", flush=True)
            make_args = ["make", "SUB_PROJECT=LUMAXzcu106", f"CONFIG={config}", f"build_dir={rtl}"]
            if args.generator_jar:
                assembly = args.generator_jar.resolve()
                digest = hashlib.sha256(assembly.read_bytes())
                digest.update(json.dumps({key: value for key, value in env.items()
                    if key.startswith("LUMAX_ZCU106_")}, sort_keys=True).encode())
                digest.update(config.encode())
                marker = rtl / "generation.sha256"
                generation = digest.hexdigest()
                if not marker.is_file() or marker.read_text().strip() != generation:
                    rtl.mkdir(parents=True, exist_ok=True)
                    run_logged([str(Path(env["JAVA_HOME"]) / "bin/java"), "-Xmx8G", "-Xss8M",
                                "-cp", str(assembly), "chipyard.Generator",
                                "--target-dir", str(rtl), "--name", long_name,
                                "--top-module", f"chipyard.fpga.zcu106.{TOP}",
                                "--legacy-configs", f"chipyard.fpga.zcu106:{config}"],
                               rtl, env, run / "elaboration.log")
                    (rtl / f"{long_name}.chisel.log").touch()
                    marker.write_text(generation + "\n")
                make_args += [f"GENERATOR_CLASSPATH={assembly}", "-o", str(assembly),
                              "-o", str(CHIPYARD / ".classpath_cache/tapeout.jar")]
            run_logged([*make_args, "fpga-rtl"], FPGA, dict(env, XDG_RUNTIME_DIR=runtime),
                       run / "chipyard.log")
        if not manifest.is_file():
            raise RuntimeError(f"Chipyard did not produce {manifest}")
        if args.rtl_only:
            print(f"RTL manifest: {manifest}")
            return

        vivado = args.vivado
        if not vivado:
            vivado = str(INSTALLED_VIVADO) if INSTALLED_VIVADO.is_file() else shutil.which(
                "vivado", path=env["PATH"])
        if not vivado:
            raise RuntimeError("Vivado is unavailable. Pass --vivado with its executable path.")
        vivado = str(Path(vivado).resolve())
        version = subprocess.check_output([vivado, "-version"], cwd=run, env=env, text=True)
        (run / "vivado_version.txt").write_text(version)
        key = checkpoint_key(manifest, version, args.jobs)
        cache = variant / "checkpoints" / key
        cache.mkdir(parents=True, exist_ok=True)
        cached = cache / "post_route.dcp"
        checkpoint = cache / f"post_route_{stamp}.dcp" if args.reimplement else cached
        stage = "reporting from cached route" if checkpoint.is_file() else "synthesis and implementation"
        print(f"Vivado {stage}: {run / 'vivado.log'}", flush=True)
        run_logged([vivado, "-mode", "batch", "-nojournal", "-nolog", "-source", str(REPORT_TCL),
                    "-tclargs", TOP, str(manifest), str(COMMON_TCL), str(BOARD_TCL),
                    str(checkpoint), str(args.saif.resolve()) if args.saif else "",
                    args.saif_scope or ""], run, env, run / "vivado.log")
        if checkpoint != cached:
            checkpoint.replace(cached)
        print(f"Reports: {run / 'reports'}")
        print(f"Routed checkpoint: {cached}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
