"""Deterministic evidence checks; provisional tolerances apply only to M0."""

import html
import math
import re
from pathlib import Path

import numpy as np

from .models import PipeSpec, file_hash, write_json


def check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "status": "pass" if passed else "fail", "detail": detail}


def read_series(path: Path) -> np.ndarray:
    if not path.is_file():
        raise ValueError(f"Missing evidence: {path.name}")
    rows = []
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            numbers = [float(x) for x in line.split()]
            if len(numbers) != 2 or not all(math.isfinite(x) for x in numbers):
                raise ValueError("Invalid or non-finite quantity history.")
            rows.append(numbers)
    if len(rows) < 20:
        raise ValueError("Fewer than 20 iterations of result history; convergence is unassessed.")
    data = np.array(rows)
    if np.any(np.diff(data[:, 0]) <= 0):
        raise ValueError("Result history has duplicate or out-of-order iterations.")
    return data


def reference_evidence(case: Path, spec: PipeSpec) -> dict:
    series = {}
    for patch in ("inlet", "outlet"):
        for field in ("p", "phi"):
            series[f"{patch}_{field}"] = read_series(
                case / "postProcessing" / f"{patch}_{field}" / "0/surfaceFieldValue.dat"
            )
    times = series["inlet_p"][:, 0]
    if any(not np.array_equal(values[:, 0], times) for values in series.values()):
        raise ValueError("Quantity histories do not refer to the same iterations.")
    # Both generated p fields and the solver output must declare kinematic pressure.
    final = case / f"{times[-1]:g}" / "p"
    if not final.exists() or not re.search(
        r"dimensions\s*\[0\s+2\s+-2\s+0\s+0\s+0\s+0\s*\]", final.read_text()
    ):
        raise ValueError("Pressure dimensions are missing or unexpected; refusing a Pa conversion.")
    dp = (series["inlet_p"][:, 1] - series["outlet_p"][:, 1]) * spec.density_kg_m3
    qi, qo = series["inlet_phi"][:, 1], series["outlet_phi"][:, 1]
    imbalance = np.abs(qi + qo) / np.maximum(np.maximum(np.abs(qi), np.abs(qo)), 1e-15)
    expected = spec.expected_pressure_drop_pa
    error = abs(dp[-1] - expected) / expected
    stability = float(np.ptp(dp[-20:]) / max(abs(dp[-1]), 1e-15))
    log = (case.parent / "logs/foamRun.log").read_text()
    residuals = re.findall(r"Solving for (\w+), Initial residual = ([\deE+.-]+)", log)
    last_residuals = {}
    for field, value in residuals:
        last_residuals[field] = float(value)
    required_residuals = {"p", "Ux", "Uy", "Uz"}
    residual_pass = required_residuals <= last_residuals.keys() and all(
        math.isfinite(last_residuals[x]) and last_residuals[x] <= 1e-5 for x in required_residuals
    )
    checks = [
        check(
            "Analytical pressure drop",
            error <= 0.05,
            f"{error:.3%} error; provisional pipe-fixture limit 5%",
        ),
        check(
            "Mass conservation",
            float(imbalance[-1]) <= 0.001,
            f"{imbalance[-1]:.4%} net flow imbalance; limit 0.1%",
        ),
        check(
            "Pressure-drop stability",
            stability <= 0.001,
            f"{stability:.4%} range over the last 20 iterations; limit 0.1%",
        ),
        check(
            "Equation residuals",
            residual_pass,
            f"Final initial residuals: {last_residuals}; limit 1e-5",
        ),
        check(
            "Prescribed flow",
            qi[-1] < 0 < qo[-1]
            and abs(-qi[-1] - math.pi * spec.radius_m**2 * spec.mean_velocity_m_s)
            / (math.pi * spec.radius_m**2 * spec.mean_velocity_m_s)
            <= 1e-5,
            "Outward-normal convention: inlet flux negative, outlet positive; compare to the declared flow.",
        ),
        check(
            "Solver completed",
            "End" in log and times[-1] == spec.max_iterations,
            f"Recorded iteration {times[-1]:g} of {spec.max_iterations}",
        ),
    ]
    return {
        "status": "passed" if all(c["status"] == "pass" for c in checks) else "failed",
        "pressure_drop_pa": float(dp[-1]),
        "expected_pressure_drop_pa": expected,
        "relative_error": float(error),
        "mass_imbalance": float(imbalance[-1]),
        "reynolds": spec.reynolds,
        "iterations": int(times[-1]),
        "checks": checks,
        "history": [
            {"iteration": int(t), "pressure_drop_pa": float(p), "mass_imbalance": float(m)}
            for t, p, m in zip(times, dp, imbalance, strict=True)
        ],
        "pressure_convention": "Area-averaged inlet minus outlet static gauge pressure. Solver kinematic p multiplied by declared density; outlet gauge pressure is 0 Pa.",
        "limitations": [
            "Numerical verification of one prescribed laminar pipe fixture; not validation against experiment.",
            "Provisional thresholds require independent CFD review before an engineering release.",
            "No mesh-independence claim: this run uses one grid.",
            "A normalized parabolic inlet profile is prescribed; this is not a uniform-inlet entrance-loss benchmark.",
        ],
    }


def mesh_quality(log: Path) -> dict:
    text = log.read_text()
    valid = "Mesh OK." in text and "Failed" not in text
    return check(
        "Mesh quality",
        valid,
        "OpenFOAM checkMesh -allTopology -allGeometry: "
        + ("Mesh OK." if valid else "inspect logs/checkMesh.log"),
    )


def write_report(folder: Path, result: dict, spec: dict, environment: dict) -> None:
    """Portable HTML/JSON report with no network resources or hidden model calls."""
    result["schema_version"] = 1
    result["environment"] = environment
    result["inputs"] = spec
    artifacts = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name not in {"result.json", "report.html", "status.json"}:
            artifacts[path.relative_to(folder).as_posix()] = file_hash(path)
    result["artifact_hashes"] = artifacts
    if "history" in result:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 3.6), layout="constrained")
        ax.plot(
            [x["iteration"] for x in result["history"]],
            [x["pressure_drop_pa"] for x in result["history"]],
            color="#087f70",
            label="OpenFOAM",
        )
        ax.axhline(
            result["expected_pressure_drop_pa"], color="#b46d21", linestyle="--", label="Analytical"
        )
        ax.set(
            xlabel="Iteration",
            ylabel="Static pressure difference (Pa)",
            title="Pipe reference · area-averaged inlet minus outlet",
        )
        ax.legend()
        ax.grid(alpha=0.15)
        fig.savefig(folder / "pressure-history.svg")
        plt.close(fig)
        artifacts["pressure-history.svg"] = file_hash(folder / "pressure-history.svg")
        csv = (
            "iteration,pressure_drop_pa,mass_imbalance\n"
            + "\n".join(
                f"{r['iteration']},{r['pressure_drop_pa']:.12g},{r['mass_imbalance']:.12g}"
                for r in result["history"]
            )
            + "\n"
        )
        (folder / "metrics.csv").write_text(csv)
        artifacts["metrics.csv"] = file_hash(folder / "metrics.csv")
    write_json(folder / "result.json", result)
    esc = html.escape
    rows = "".join(
        f"<tr><td>{esc(c['name'])}</td><td>{esc(c['status'])}</td><td>{esc(c['detail'])}</td></tr>"
        for c in result["checks"]
    )
    limitations = "".join(f"<li>{esc(x)}</li>" for x in result.get("limitations", []))
    chart = (
        '<img src="pressure-history.svg" alt="Pressure convergence history">'
        if "history" in result
        else ""
    )
    (
        folder / "report.html"
    ).write_text(f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Venturi milestone 0 evidence</title>
    <style>body{{font:16px system-ui;max-width:1000px;margin:50px auto;padding:24px;color:#18312e}}table{{border-collapse:collapse;width:100%}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #ddd}}img{{max-width:100%}}code{{overflow-wrap:anywhere}}</style>
    <h1>Venturi · milestone 0 evidence</h1><p>Run <code>{esc(folder.name)}</code> · {esc(result["status"])}</p>
    <p>This is a compatibility benchmark with provisional criteria, not a qualified engineering result.</p>
    {chart}<table><tr><th>Check</th><th>Outcome</th><th>Evidence</th></tr>{rows}</table>
    <h2>Limitations</h2><ul>{limitations}</ul><p>Complete metrics, inputs, environment, and artifact hashes: <a href="result.json">result.json</a>.</p></html>""")
