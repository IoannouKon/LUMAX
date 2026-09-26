"""Rebuild the documentation CSV and plot from existing simulation logs.

This does not execute a simulator or collect new hardware measurements.
Requires matplotlib. Paths are resolved relative to this script.
"""
import csv
from pathlib import Path
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "LUMAX/software/tests/src/Log"
OUT = ROOT / "docs/assets/measurements"
STAGES = ["Load X", "Generate BRAMs", "Load W and Select", "Store O"]


def read_logs():
    rows = []
    for path in sorted(LOGS.rglob("*.txt")):
        config = re.fullmatch(r"XS=(\d+)_YS=(\d+)_Mem_row_factor=(\d+)", path.parent.name)
        shape = re.fullmatch(r"RIN=(\d+)_CIN=(\d+)_COUT=(\d+)_INBITS=(\d+)_WBITS=(\d+)\.txt", path.name)
        if not config or not shape:
            continue
        body = path.read_text()
        row = dict(zip(["XS", "YS", "RF", "Rin", "Cin", "Cout", "in_bits", "w_bits"],
                       map(int, config.groups() + shape.groups())))
        row["status"] = "FAIL" if "FAIL" in body else "PASS" if "PASS (All elements)" in body else "UNKNOWN"
        row["result_detail"] = (
            "correctness mismatch" if re.search(r"^FAIL \(", body, re.M)
            else "simulation failure" if row["status"] == "FAIL"
            else "correctness pass" if row["status"] == "PASS"
            else "no result"
        )
        for stage in STAGES + ["Total"]:
            match = re.search(r"^" + re.escape(stage) + r"\s*\|\s*(\d+)", body, re.M)
            row[stage] = int(match[1]) if match else None
        row["complete_counters"] = all(row[s] is not None for s in STAGES + ["Total"])
        if row["complete_counters"] and sum(row[stage] for stage in STAGES) != row["Total"]:
            raise ValueError(f"Stage sum differs from Total: {path}")
        for label in ["SW Mat-Mul", "HW Mat-Mul"]:
            match = re.search(re.escape(label) + r" (\d+) cycles", body)
            row[label] = int(match[1]) if match else None
        row["source"] = path.relative_to(ROOT).as_posix()
        rows.append(row)
    if not rows:
        raise ValueError("No saved measurements found")
    return rows


def main():
    rows = read_logs()
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "saved-simulation-cycles.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), constrained_layout=True)
    for axis, xs in zip(axes, [2, 4]):
        for bits, color in zip([8, 4, 2], ["#2563eb", "#d97706", "#059669"]):
            selected = sorted((r for r in rows if r["XS"] == xs and r["YS"] == 1
                               and r["RF"] == 8 and r["Rin"] == 1
                               and r["Cin"] == r["Cout"] and r["in_bits"] == 16
                               and r["w_bits"] == bits and r["complete_counters"]), key=lambda r: r["Cin"])
            passed = [r for r in selected if r["status"] == "PASS"]
            failed = [r for r in selected if r["status"] != "PASS"]
            axis.plot([r["Cin"] for r in passed], [r["Total"] for r in passed],
                      "o-", label=f"{bits}-bit weights", color=color)
            axis.scatter([r["Cin"] for r in failed], [r["Total"] for r in failed],
                         marker="x", color=color, s=65)
        axis.set(xscale="log", yscale="log", xlabel="K = M in (1 × K) × (K × M)",
                 ylabel="Accelerator stage-counter cycles", title=f"b = {xs}, n = 512 (RF = 8)")
        axis.grid(True, which="both", alpha=0.2)
        axis.legend()
    fig.suptitle("Saved Verilator logs • 16-bit activations • YS = 1\nCircles: correctness PASS; crosses: correctness FAIL (diagnostic only)")
    fig.savefig(OUT / "saved-simulation-cycles.png", dpi=180)
    plt.close(fig)
    print(f"Exported {len(rows)} logs ({sum(r['status'] == 'PASS' for r in rows)} PASS, "
          f"{sum(r['status'] == 'FAIL' for r in rows)} FAIL) to {OUT}")


if __name__ == "__main__":
    main()
