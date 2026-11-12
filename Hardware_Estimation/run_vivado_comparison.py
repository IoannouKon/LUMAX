#!/usr/bin/env python3
"""Detached ZCU106 Vivado comparison. Run: start, status, resume, or stop."""

# ---------------------- SWEEP CONFIGURATION ----------------------
LUMAX_CONFIGURATIONS = [(4, 8, 1, 8), (8, 8, 1, 8), (16, 8, 1, 8),
                        (8, 8, 2, 8), (4, 8, 4, 8)]
GEMMINI_WEIGHT_BITS = (8, 4, 2)
MAX_RIN, MAX_CIN, MAX_COUT = 8, 1216, 1216
CLOCK_MHZ = 50
VIVADO_THREADS = 4
JOB_TIMEOUT_SECONDS = 12 * 3600
# One implementation at a time. The Verilator suite runs independently.
# ----------------------------------------------------------------

import argparse
from collections import Counter
from datetime import datetime
import csv
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from types import SimpleNamespace

sys.dont_write_bytecode = True
SELF = Path(__file__).resolve()
ROOT = SELF.parent / "runs"
LATEST = ROOT / "latest.json"
GEMMINI_ASSETS = SELF.parent.parent / "Baseline_Comparison/Gemmini"
GEMMINI_OPTIONS = SELF.parent / "gemmini_options.json"
STOP = False


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hw = load_module("vivado_hw", SELF.parent / "HW_resources_with_vivado_zcu106.py")


def now():
    return datetime.now().astimezone().isoformat()


def atomic(path, obj):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(obj, indent=2) + "\n")
    temporary.replace(path)


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def active(folder):
    with (folder / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def plan():
    cases = [dict(name=f"gemmini_w{w}", accelerator="gemmini", wb=w, status="PENDING")
             for w in GEMMINI_WEIGHT_BITS]
    cases += [dict(name=f"lumax_b{b}_rf{rf}_ys{ys}_id{ids}", accelerator="lumax",
                   b=b, rf=rf, ys=ys, ids=ids, status="PENDING")
              for b, rf, ys, ids in LUMAX_CONFIGURATIONS]
    return cases


class Worker:
    def __init__(self, folder):
        self.folder = folder
        self.state = json.loads((folder / "state.json").read_text())
        self.settings = self.state["settings"]
        self.work = Path(self.state["work"])
        self.work.mkdir(parents=True, exist_ok=True)
        options = SimpleNamespace(b=2, rf=4, ys=1, ids=8, rin=MAX_RIN, cin=MAX_CIN,
                                  cout=MAX_COUT, clock_mhz=CLOCK_MHZ,
                                  activation="fp16", jobs=VIVADO_THREADS, accelerator="lumax")
        self.env = hw.build_environment(options, self.work / "platform")
        self.state["pid"] = os.getpid()
        self.state["status"] = "RUNNING"
        self.state.pop("reason", None)

    def log(self, message):
        print(f"[{now()}] {message}", flush=True)

    def save(self, phase=None):
        if phase:
            self.state["phase"] = phase
        self.state["updated"] = now()
        atomic(self.folder / "state.json", self.state)
        self.write_reports()

    def command(self, args, cwd, log, timeout=JOB_TIMEOUT_SECONDS):
        self.log("Starting " + str(log))
        started = time.monotonic()
        with log.open("w") as stream:
            proc = subprocess.Popen([str(a) for a in args], cwd=cwd, env=self.env,
                                    stdin=subprocess.DEVNULL, stdout=stream,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            self.state["child_pid"] = proc.pid
            self.save()
            try:
                while proc.poll() is None:
                    if STOP or (self.folder / "stop.request").exists():
                        raise InterruptedError("Stop requested")
                    if time.monotonic() - started > timeout:
                        raise TimeoutError(f"Command exceeded {timeout} seconds")
                    time.sleep(1)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
            self.state.pop("child_pid", None)
        if proc.returncode:
            raise RuntimeError(f"Command exited with status {proc.returncode}. See {log}")

    def prepare(self):
        self.save("Preparing frozen source assembly")
        snapshot = self.folder / "sources"
        options_path = snapshot / "gemmini_options.json"
        baseline_path = self.state.get("baseline_run")
        if baseline_path and not (snapshot / "manifest.json").exists():
            baseline = Path(baseline_path)
            saved = json.loads((baseline / "state.json").read_text())
            manifest = json.loads((baseline / "sources/manifest.json").read_text())
            for relative, digest in manifest.items():
                if sha(baseline / "sources" / relative) != digest:
                    raise RuntimeError(f"Baseline source changed: {relative}")
                if relative.startswith("lumax/"):
                    current = hw.CHIPYARD / "generators/LUMAX/src/main/scala" / relative[6:]
                    if sha(current) != digest:
                        raise RuntimeError(f"Current LUMAX differs from the saved FPGA source: {current}")
            shutil.copytree(baseline / "sources", snapshot, dirs_exist_ok=True)
            previous_jar = Path(saved["work"]) / "chipyard-fpga-comparison.jar"
            if previous_jar.is_file() and sha(previous_jar) == saved["generator_sha256"]:
                overlay = self.work / "chipyard-fpga-comparison.jar"
                shutil.copyfile(previous_jar, overlay)
                (self.work / "overlay.sha256").write_text(saved["generator_sha256"] + "\n")
                self.state["generator_sha256"] = saved["generator_sha256"]
        if not options_path.exists() or not (snapshot / "manifest.json").exists():
            gem_options = json.loads(GEMMINI_OPTIONS.read_text())
            for relative, digest in gem_options["source_sha256"].items():
                source = GEMMINI_ASSETS / "fpint/src/main/scala" / relative
                if sha(source) != digest:
                    raise RuntimeError(f"Tested Gemmini source changed: {source}")
            snapshot.mkdir(exist_ok=True)
            shutil.copytree(GEMMINI_ASSETS / "fpint/src/main/scala", snapshot / "gemmini", dirs_exist_ok=True)
            shutil.copytree(hw.CHIPYARD / "generators/LUMAX/src/main/scala", snapshot / "lumax",
                            dirs_exist_ok=True)
            benchmark = load_module("gemmini_llm_config", GEMMINI_ASSETS / "verification/TEST_LLMS/run_test_llms.py")
            (snapshot / "GemminiTestSafeConfig.scala").write_text(benchmark.scala_config(gem_options["config"]))
            (snapshot / "GemminiZCU106Configs.scala").write_text(
                "package chipyard.fpga.zcu106\n"
                "import org.chipsalliance.cde.config.Config\n" +
                "\n".join(
                    f"class GemminiW{w}zcu106 extends Config("
                    "new WithZCU106Platform ++ "
                    f"new gemmini.testsafe.WithTestSafeGemmini({w}) ++ "
                    "new freechips.rocketchip.subsystem.WithoutTLMonitors ++ "
                    "new freechips.rocketchip.rocket.WithNMedCores(1) ++ "
                    "new chipyard.config.AbstractConfig)\n"
                    for w in GEMMINI_WEIGHT_BITS))
            atomic(options_path, gem_options)
            atomic(snapshot / "manifest.json", {
                str(p.relative_to(snapshot)): sha(p)
                for p in snapshot.rglob("*") if p.is_file() and p.name != "manifest.json"})
        manifest = json.loads((snapshot / "manifest.json").read_text())
        for relative, digest in manifest.items():
            if sha(snapshot / relative) != digest:
                raise RuntimeError(f"Frozen source changed: {relative}")

        overlay = self.work / "chipyard-fpga-comparison.jar"
        marker = self.work / "overlay.sha256"
        if overlay.is_file() and marker.is_file() and marker.read_text().strip() == sha(overlay):
            self.log("Reusing frozen FPGA assembly")
            return overlay

        hw.DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=True)
        with (hw.DEFAULT_OUTPUT / ".chipyard.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with tempfile.TemporaryDirectory(prefix="vivado-sweep-sbt-") as runtime:
                self.env["XDG_RUNTIME_DIR"] = runtime
                self.command(["make", "SUB_PROJECT=LUMAXzcu106", "fpga-assembly"], hw.FPGA,
                             self.folder / "assembly.log", 7200)
                self.env.pop("XDG_RUNTIME_DIR", None)
            base = self.work / "chipyard-fpga-base.jar"
            shutil.copyfile(hw.CHIPYARD / ".classpath_cache/chipyard_fpga.jar", base)

        jars = json.loads(options_path.read_text())["compiler_jars"]
        compiler = ":".join([jars["scala-compiler-2.13.12.jar"],
                             jars["scala-reflect-2.13.12.jar"], str(base)])
        classes = self.work / "classes"
        if classes.exists():
            shutil.rmtree(classes)
        classes.mkdir()
        java_bin = Path(self.env["JAVA_HOME"]) / "bin"
        self.command([java_bin / "java", "-Xmx8G", "-cp", compiler, "scala.tools.nsc.Main",
                      "-classpath", base,
                      "-Xplugin:" + jars["chisel-plugin_2.13.12-6.5.0.jar"],
                      "-language:reflectiveCalls", "-d", classes,
                      *sorted(snapshot.rglob("*.scala"))],
                     self.work, self.folder / "overlay_compile.log", 7200)
        shutil.copyfile(base, overlay)
        self.command([java_bin / "jar", "uf", overlay, "-C", classes, "."],
                     self.work, self.folder / "overlay_jar.log", 600)
        marker.write_text(sha(overlay) + "\n")
        self.state["generator_sha256"] = sha(overlay)
        self.save()
        return overlay

    def collect(self, case):
        folder = self.folder / case["name"]
        driver = folder / "driver.log"
        match = re.search(r"^Run directory: (.+)$", driver.read_text(), re.MULTILINE)
        if not match:
            return
        run = Path(match[1])
        case["work_run"] = str(run)
        for name in ("chipyard.log", "elaboration.log", "vivado.log", "vivado_version.txt"):
            if (run / name).is_file():
                shutil.copyfile(run / name, folder / name)
        reports = run / "reports"
        if reports.exists():
            shutil.copytree(reports, folder / "reports", dirs_exist_ok=True)
        accelerator = case["accelerator"]
        routed_report = reports / f"{accelerator}_utilization.rpt"
        synthesized_report = reports / f"synthesis_{accelerator}_utilization.rpt"
        utilization = routed_report if routed_report.exists() else synthesized_report
        if utilization.exists():
            text = utilization.read_text()
            for key, label in (("luts", "CLB LUTs"), ("registers", "CLB Registers"),
                               ("bram_tiles", "Block RAM Tile"), ("uram", "URAM"), ("dsps", "DSPs")):
                found = re.search(r"^\|\s*" + re.escape(label) + r"\s*\*?\s*\|\s*([\d,.]+)", text, re.MULTILINE)
                if found:
                    case[key] = float(found[1].replace(",", ""))
            case["resources_stage"] = "post_route" if routed_report.exists() else "post_synthesis"
        summary = reports / "summary.tsv"
        if summary.exists():
            with summary.open() as stream:
                metrics = {r["metric"]: r["value"] for r in csv.DictReader(stream, delimiter="\t")}
            case["metrics"] = metrics
            key = f"{accelerator}_internal_estimated_fmax_mhz"
            if key in metrics:
                case["estimated_internal_fmax_mhz"] = float(metrics[key])
            instance = metrics.get(f"{accelerator}_instance", "").split("/")[-1]
            power = (reports / "power.rpt").read_text()
            found = re.search(r"^\|\s*" + re.escape(instance) + r"\s*\|\s*([\d.]+)", power, re.MULTILINE)
            if found:
                case["dynamic_power_w"] = float(found[1])
            for key, label in (("system_total_power_w", "Total On-Chip Power (W)"),
                               ("system_dynamic_power_w", "Dynamic (W)")):
                found = re.search(r"^\|\s*" + re.escape(label) + r"\s*\|\s*([\d.]+)", power, re.MULTILINE)
                if found:
                    case[key] = float(found[1])
            found = re.search(r"^\|\s*Confidence Level\s*\|\s*(\w+)", power, re.MULTILINE)
            case["power_confidence"] = found[1] if found else "unknown"
            route = (reports / "routing.rpt").read_text()
            case["routed"] = bool(re.search(r"nets with routing errors\.+\s*:\s*0\s*:", route))
            slacks = [float(metrics[key]) for key in ("system_setup_slack_ns", "system_hold_slack_ns")
                      if key in metrics]
            case["timing_met"] = len(slacks) == 2 and min(slacks) >= 0
            case["status"] = "PASS" if case["routed"] and case["timing_met"] else "TIMING_FAILED"
            required = ("luts", "registers", "bram_tiles", "dsps", "dynamic_power_w",
                        "estimated_internal_fmax_mhz")
            if any(key not in case for key in required):
                case["status"] = "REPORT_INCOMPLETE"

    def write_reports(self):
        fields = ("name", "accelerator", "wb", "b", "rf", "ys", "ids", "status", "resources_stage",
                  "luts", "registers", "bram_tiles", "uram", "dsps", "dynamic_power_w",
                  "system_total_power_w", "system_dynamic_power_w", "power_confidence",
                  "estimated_internal_fmax_mhz", "routed", "timing_met", "elapsed_seconds", "reason")
        with (self.folder / "summary.csv.tmp").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.state["cases"])
        (self.folder / "summary.csv.tmp").replace(self.folder / "summary.csv")
        baselines = {c["wb"]: c for c in self.state["cases"] if c["accelerator"] == "gemmini"}
        comparison = []
        for case in self.state["cases"]:
            if case["accelerator"] != "lumax":
                continue
            for wb in GEMMINI_WEIGHT_BITS:
                row = dict(lumax=case["name"], gemmini=f"gemmini_w{wb}", wb=wb)
                base = baselines[wb]
                for key in ("luts", "registers", "bram_tiles", "dsps", "dynamic_power_w",
                            "estimated_internal_fmax_mhz"):
                    if key in ("luts", "registers", "bram_tiles", "dsps") and (
                        case.get("resources_stage") != base.get("resources_stage")):
                        continue
                    if key in case and base.get(key, 0):
                        row[key + "_ratio"] = round(case[key] / base[key], 6)
                comparison.append(row)
        with (self.folder / "comparison.csv").open("w", newline="") as stream:
            keys = ["lumax", "gemmini", "wb"] + [
                k + "_ratio" for k in ("luts", "registers", "bram_tiles", "dsps",
                                       "dynamic_power_w", "estimated_internal_fmax_mhz")]
            writer = csv.DictWriter(stream, fieldnames=keys)
            writer.writeheader()
            writer.writerows(comparison)
        def display(case, key):
            value = case.get(key)
            return f"{value:,.3f}".rstrip("0").rstrip(".") if isinstance(value, (int, float)) else str(value or "—")
        lines = ["# ZCU106 LUMAX versus edited Gemmini", "",
                 "All designs use the same medium Rocket core, FPGA shell and 50 MHz target.",
                 "LUMAX uses FP16, Rin=8, Cin=Cout=1216. Gemmini matches the tested 4x4 WS configuration,",
                 "32 KiB scratchpad, 4 KiB accumulator and eight IDs per read/write pool.",
                 "LUMAX uses eight shared DMA IDs. Scaling and bias follow the tested settings.", "",
                 "Resources refer to the accelerator hierarchy. Each design folder also contains full-system reports.",
                 "Power uses vectorless activity. Vivado confidence is reported for each design.",
                 "The internal Fmax estimate is derived from the current routed implementation.", "",
                 "| Design | Status | Resource stage | LUTs | Registers | BRAM tiles | DSPs |",
                 "|---|---|---|---:|---:|---:|---:|"]
        for case in self.state["cases"]:
            lines.append("| " + " | ".join([case["name"], case["status"], case.get("resources_stage", "—"),
                          *[display(case, k) for k in ("luts", "registers", "bram_tiles", "dsps")]]) + " |")
        lines += ["", "| Design | Dynamic power W | Power confidence | Internal Fmax MHz | Setup slack ns | Hold slack ns |",
                  "|---|---:|---|---:|---:|---:|"]
        for case in self.state["cases"]:
            metrics = case.get("metrics", {})
            lines.append("| " + " | ".join([case["name"], display(case, "dynamic_power_w"),
                          case.get("power_confidence", "—"), display(case, "estimated_internal_fmax_mhz"),
                          metrics.get("system_setup_slack_ns", "—"),
                          metrics.get("system_hold_slack_ns", "—")]) + " |")
        lines += ["", "Ratios below are LUMAX / edited Gemmini. Resource and power ratios below 1 use less.",
                  "Frequency ratios above 1 indicate a higher internal frequency estimate.", "",
                  "| LUMAX | Gemmini | LUT ratio | Register ratio | BRAM ratio | DSP ratio | Dynamic power ratio | Fmax ratio |",
                  "|---|---|---:|---:|---:|---:|---:|---:|"]
        for row in comparison:
            lines.append("| " + " | ".join([row["lumax"], row["gemmini"], *[
                f"{row[k]:.3f}×" if k in row else "—" for k in keys[3:]]]) + " |")
        (self.folder / "comparison.md").write_text("\n".join(lines) + "\n")

    def run(self):
        self.save("Starting")
        try:
            overlay = self.prepare()
            for case in self.state["cases"]:
                if case["status"] not in ("PENDING", "RUNNING"):
                    continue
                if STOP or (self.folder / "stop.request").exists():
                    raise InterruptedError("Stop requested")
                case.update(status="RUNNING", started=now())
                self.state["current"] = case["name"]
                self.save("Vivado implementation")
                started = time.monotonic()
                folder = self.folder / case["name"]
                folder.mkdir(exist_ok=True)
                args = [sys.executable, "-u", hw.__file__, "--generator-jar", overlay,
                        "--accelerator", case["accelerator"], "--activation", "fp16",
                        "--rin", self.settings["rin"], "--cin", self.settings["cin"],
                        "--cout", self.settings["cout"], "--clock-mhz", self.settings["clock_mhz"],
                        "--jobs", self.settings["vivado_threads"],
                        "--output-dir", self.work / "builds"]
                if case["accelerator"] == "gemmini":
                    args += ["--weight-bits", case["wb"]]
                else:
                    for key in ("b", "rf", "ys", "ids"):
                        args += ["--" + key, case[key]]
                try:
                    self.command(args, hw.CHIPYARD, folder / "driver.log")
                    self.collect(case)
                    if case["status"] == "RUNNING":
                        raise RuntimeError("Implementation completed without the expected reports")
                except InterruptedError:
                    case.update(status="PENDING", reason="Interrupted; resumable")
                    raise
                except Exception as exc:
                    case.update(status="FAILED", reason=str(exc))
                    self.log(traceback.format_exc())
                    try:
                        self.collect(case)
                    except Exception:
                        self.log(traceback.format_exc())
                    case["status"] = "FAILED"
                case["elapsed_seconds"] = round(time.monotonic() - started, 1)
                self.log(f'{case["name"]}: {case["status"]}')
                self.save()
            self.state["status"] = "COMPLETE"
            self.save("Finished")
        except InterruptedError as exc:
            self.state.update(status="STOPPED", reason=str(exc))
            self.save("Stopped")
        except Exception as exc:
            self.log(traceback.format_exc())
            self.state.update(status="FAILED", reason=str(exc))
            self.save("Preparation failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "status", "resume", "stop", "worker"))
    parser.add_argument("--run", type=Path)
    parser.add_argument("--baseline-run", type=Path,
                        help="Reuse completed Gemmini reports and the matching frozen FPGA assembly")
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    if args.command == "worker":
        global STOP
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        def request_stop(*_):
            global STOP
            STOP = True
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        with (args.run / "worker.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            Worker(args.run).run()
        return
    with (ROOT / "start.lock").open("a") as start_lock:
        fcntl.flock(start_lock, fcntl.LOCK_EX)
        current = Path(json.loads(LATEST.read_text())["run"]) if LATEST.exists() else None
        if args.command in ("status", "stop"):
            if not current:
                print("No sweep has started.")
                return
            running = active(current)
            if args.command == "stop" and running:
                (current / "stop.request").touch()
            state = json.loads((current / "state.json").read_text())
            if not running and state["status"] in ("RUNNING", "STARTING"):
                state["status"] = "INTERRUPTED"
            print("Worker active." if running else "Worker inactive.")
            print(f'Reports: {current}')
            print(f'Status: {state["status"]}; phase: {state.get("phase", "")}; current: {state.get("current", "")}')
            for case in state["cases"]:
                print(f'{case["name"]}: {case["status"]}')
            if running and state.get("current"):
                driver = current / state["current"] / "driver.log"
                if driver.exists():
                    print("\n".join(driver.read_text().splitlines()[-3:]))
            return
        if current and active(current):
            print(f"Already running. Reports: {current}")
            return
        if args.command == "start":
            reused = {}
            if args.baseline_run:
                baseline = args.baseline_run.resolve()
                saved = json.loads((baseline / "state.json").read_text())
                if active(baseline):
                    raise RuntimeError("The baseline sweep must be inactive before reusing its reports")
                expected = dict(rin=MAX_RIN, cin=MAX_CIN, cout=MAX_COUT,
                                clock_mhz=CLOCK_MHZ, vivado_threads=VIVADO_THREADS)
                if saved["settings"] != expected:
                    raise RuntimeError("Baseline FPGA settings differ from this sweep")
                reused = {c["name"]: c for c in saved["cases"] if c["accelerator"] == "gemmini"}
                if any(reused.get(f"gemmini_w{w}", {}).get("status") != "PASS"
                       for w in GEMMINI_WEIGHT_BITS):
                    raise RuntimeError("The baseline needs completed W8, W4 and W2 Gemmini reports")
            stamp = datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
            current = ROOT / stamp
            current.mkdir()
            work = Path(tempfile.gettempdir()) / f"lumax-vivado-comparison-{os.getuid()}" / stamp
            cases = plan()
            for index, case in enumerate(cases):
                if case["name"] in reused:
                    cases[index] = copy.deepcopy(reused[case["name"]])
                    cases[index]["reused_from"] = str(baseline)
                    shutil.copytree(baseline / case["name"], current / case["name"])
            state = dict(status="STARTING", started=now(),
                   work=str(work), cases=cases, phase="Starting", settings=dict(
                   rin=MAX_RIN, cin=MAX_CIN, cout=MAX_COUT, clock_mhz=CLOCK_MHZ,
                   vivado_threads=VIVADO_THREADS))
            if reused:
                state["baseline_run"] = str(baseline)
            atomic(current / "state.json", state)
            atomic(LATEST, dict(run=str(current)))
        elif current is None:
            raise RuntimeError("No sweep to resume")
        (current / "stop.request").unlink(missing_ok=True)
        with (current / "runner.log").open("a") as output:
            proc = subprocess.Popen([sys.executable, "-u", "-B", str(SELF), "worker", "--run", str(current)],
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError(f"Worker exited. See {current / 'runner.log'}")
            if active(current):
                break
            time.sleep(.1)
        print(f"Started detached worker PID {proc.pid}.")
        print(f"Reports: {current}")
        print(f"Progress: {current / 'runner.log'}")


if __name__ == "__main__":
    main()
