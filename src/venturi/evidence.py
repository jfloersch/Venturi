"""Deterministic evidence checks attached to the versioned pipe recipe."""

import html
import math
import re
from pathlib import Path

import numpy as np

from .models import CheckResult, PipeSpec, file_hash, write_json
from .recipes import pipe_recipe


def check(name: str, passed: bool, detail: str) -> dict:
    return CheckResult(name=name, status="pass" if passed else "fail", detail=detail).model_dump()


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


def field_values(
    text: str, key: str, components: int, expected_count: int | None = None
) -> np.ndarray:
    field = re.search(rf"\b{key}\s+(uniform|nonuniform)\s+(.+?);", text, re.S)
    if not field:
        raise ValueError(f"Missing {key} field values.")
    if field[1] == "uniform":
        values = [float(x) for x in field[2].strip().strip("()").split()]
        if len(values) != components:
            raise ValueError("Malformed uniform field.")
        values = values * (expected_count if expected_count is not None else 1)
        count = len(values)
    else:
        body = re.fullmatch(r"List<(scalar|vector)>\s+(\d+)\s*\((.*)\)\s*", field[2], re.S)
        if not body or body[1] != ("scalar" if components == 1 else "vector"):
            raise ValueError("Malformed nonuniform field.")
        if expected_count is not None and int(body[2]) != expected_count:
            raise ValueError("Field count does not match the executed mesh.")
        values = [float(x) for x in body[3].replace("(", " ").replace(")", " ").split()]
        count = int(body[2]) * components
    if (
        len(values) != count
        or (not values and expected_count != 0)
        or not all(math.isfinite(x) for x in values)
    ):
        raise ValueError("Incomplete or non-finite field values.")
    return np.array(values).reshape(-1, components)


def validate_field(
    path: Path, dimensions: str, components: int, expected_count: int | None = None
) -> None:
    if not path.is_file():
        raise ValueError(f"Missing final field: {path.name}")
    text = path.read_text()
    found = re.search(r"dimensions\s*\[([^\]]*)\]", text)
    actual_dimensions = (" ".join(found[1].split()) or "0 0 0 0 0 0 0") if found else None
    if actual_dimensions != dimensions:
        raise ValueError(f"{path.name} field dimensions are missing or unexpected.")
    if re.search(r"(?<!\w)[+-]?(?:nan|inf(?:inity)?)(?!\w)", text, re.I):
        raise ValueError(f"Non-finite final field: {path.name}")
    tokens = re.findall(r"(?<![\w.])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?!\w)", text)
    if any(not math.isfinite(float(value)) for value in tokens):
        raise ValueError(f"Non-finite final field: {path.name}")
    field_values(text, "internalField", components, expected_count)


def reference_evidence(case: Path, spec: PipeSpec) -> dict:
    criteria = pipe_recipe()["criteria"]
    series = {}
    for patch in ("inlet", "outlet"):
        for field in ("p", "phi"):
            series[f"{patch}_{field}"] = read_series(
                case / "postProcessing" / f"{patch}_{field}" / "0/surfaceFieldValue.dat"
            )
    times = series["inlet_p"][:, 0]
    if any(not np.array_equal(values[:, 0], times) for values in series.values()):
        raise ValueError("Quantity histories do not refer to the same iterations.")
    if times[0] not in {0, 1} or not np.array_equal(
        times, np.arange(times[0], times[0] + len(times))
    ):
        raise ValueError("Quantity history contains missing or non-integral iterations.")
    # Both generated p fields and the solver output must declare kinematic pressure.
    final = case / f"{times[-1]:g}" / "p"
    if not final.exists() or not re.search(
        r"dimensions\s*\[0\s+2\s+-2\s+0\s+0\s+0\s+0\s*\]", final.read_text()
    ):
        raise ValueError("Pressure dimensions are missing or unexpected; refusing a Pa conversion.")
    validate_field(final, "0 2 -2 0 0 0 0", 1)
    validate_field(final.with_name("U"), "0 1 -1 0 0 0 0", 3)
    dp = (series["inlet_p"][:, 1] - series["outlet_p"][:, 1]) * spec.density_kg_m3
    if not np.all(np.isfinite(dp)):
        raise ValueError("Non-finite derived pressure history.")
    qi, qo = series["inlet_phi"][:, 1], series["outlet_phi"][:, 1]
    imbalance = np.abs(qi + qo) / np.maximum(np.maximum(np.abs(qi), np.abs(qo)), 1e-15)
    expected = spec.expected_pressure_drop_pa
    error = abs(dp[-1] - expected) / expected
    window = criteria["stability_window"]
    stability = float(np.ptp(dp[-window:]) / max(abs(dp[-1]), 1e-15))
    log = (case.parent / "logs/foamRun.log").read_text()
    residuals = re.findall(r"Solving for (\w+), Initial residual = ([^,\s]+)", log)
    last_residuals = {}
    for field, value in residuals:
        last_residuals[field] = float(value)
    required_residuals = {"p", "Ux", "Uy", "Uz"}
    residual_pass = required_residuals <= last_residuals.keys() and all(
        math.isfinite(last_residuals[x]) and 0 <= last_residuals[x] <= criteria["equation_residual"]
        for x in required_residuals
    )
    checks = [
        check(
            "Analytical pressure drop",
            math.isfinite(error) and error <= criteria["pressure_relative_error"],
            f"{error:.3%} error; provisional pipe-fixture limit 5%",
        ),
        check(
            "Mass conservation",
            float(imbalance[-1]) <= criteria["mass_imbalance"],
            f"{imbalance[-1]:.4%} net flow imbalance; limit 0.1%",
        ),
        check(
            "Pressure-drop stability",
            stability <= criteria["pressure_stability"],
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
            <= criteria["prescribed_flow_relative_error"],
            "Outward-normal convention: inlet flux negative, outlet positive; compare to the declared flow.",
        ),
        check(
            "Solver completed",
            bool(re.search(r"^End\s*$", log, re.M)) and times[-1] == spec.max_iterations,
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
        "pressure_stability": stability,
        "residuals": {
            key: value if math.isfinite(value) else None for key, value in last_residuals.items()
        },
        "volume_flow_in_m3_s": float(-qi[-1]),
        "volume_flow_out_m3_s": float(qo[-1]),
        "criteria": criteria,
        "quantities": {
            "pressure_drop_pa": {
                "units": "Pa",
                "region": "inlet minus outlet",
                "reduction": "difference of area means",
                "field": "p",
                "conversion": "multiply kinematic pressure by declared density",
            },
            "mass_imbalance": {
                "units": "1",
                "region": "inlet and outlet",
                "reduction": "abs(Qin + Qout) / max(abs(Qin), abs(Qout), 1e-15 m3/s)",
            },
            "volume_flow_in_m3_s": {
                "units": "m3/s",
                "region": "inlet",
                "field": "phi",
                "reduction": "negative sum of outward flux",
            },
            "volume_flow_out_m3_s": {
                "units": "m3/s",
                "region": "outlet",
                "field": "phi",
                "reduction": "sum of outward flux",
            },
        },
        "assessments": {
            "numerical_verification": "assessed",
            "experimental_validation": "not_assessed",
            "mesh_independence": "not_assessed",
            "independent_cfd_review": "pending",
        },
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
    result["schema_version"] = "venturi.result.v1"
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
        if "expected_pressure_drop_pa" in result:
            ax.axhline(
                result["expected_pressure_drop_pa"],
                color="#b46d21",
                linestyle="--",
                label="Analytical",
            )
        ax.set(
            xlabel="Iteration",
            ylabel="Static pressure difference (Pa)",
            title="STEP internal flow · inlet to outlets"
            if "outlets" in result
            else "Pipe reference · area-averaged inlet minus outlet",
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
        if "outlets" in result:
            names = list(result["outlets"])
            (folder / "outlet-flows.csv").write_text(
                "iteration,"
                + ",".join(f"{n}_m3_s" for n in names)
                + "\n"
                + "\n".join(
                    str(row["iteration"])
                    + ","
                    + ",".join(f"{row['outlet_flows_m3_s'][n]:.12g}" for n in names)
                    for row in result["history"]
                )
                + "\n"
            )
            artifacts["outlet-flows.csv"] = file_hash(folder / "outlet-flows.csv")
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
    summary = ""
    if "pressure_drop_pa" in result:
        summary = f"<p><strong>Pressure drop: {result['pressure_drop_pa']:.8g} Pa</strong>"
        if "expected_pressure_drop_pa" in result:
            summary += f" · analytical {result['expected_pressure_drop_pa']:.8g} Pa · relative difference {result['relative_error']:.3%}"
        summary += "</p>"
    if "outlets" in result:
        summary += (
            "<p>"
            + esc(result["pressure_convention"])
            + "</p><table><tr><th>Outlet</th><th>Outward flow (m³/s)</th><th>Flow share</th><th>Pressure drop (Pa)</th></tr>"
        )
        summary += (
            "".join(
                f"<tr><td>{esc(n)}</td><td>{v['volume_flow_m3_s']:.8g}</td><td>{v['flow_fraction']:.3%}</td><td>{v['pressure_drop_pa']:.8g}</td></tr>"
                for n, v in result["outlets"].items()
            )
            + "</table>"
        )
    if "mesh_hash" in result:
        summary += f"<p>Mesh <code>{esc(result['mesh_hash'])}</code> · {result['mesh']['cells']} cells · <a href='source.step'>Source STEP</a> · <a href='selection.json'>Confirmed ports</a> · <a href='mesh-audit.json'>Mesh checks</a></p>"
    identity = ""
    if "study_hash" in result:
        identity = f'<p>Recipe <code>{esc(result["recipe"])}</code> · study <code>{esc(result["study_hash"])}</code></p><p><a href="study.json">Frozen study</a> · <a href="recipe.json">Recipe and criteria</a> · <a href="provenance.json">Provenance</a></p>'
        if (folder / "native-inputs.json").exists():
            identity += '<p><a href="native-inputs.json">Native input hashes</a></p>'
    title = (
        "STEP internal-flow evidence"
        if result.get("workflow", "").startswith("internal_")
        else "reference evidence"
    )
    (folder / "report.html").write_text(
        f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Venturi {title}</title>
    <style>body{{font:16px system-ui;max-width:1000px;margin:50px auto;padding:24px;color:#18312e}}table{{border-collapse:collapse;width:100%}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #ddd}}img{{max-width:100%}}code{{overflow-wrap:anywhere}}</style>
    <h1>Venturi · {title}</h1><p>Run <code>{esc(folder.name)}</code> · {esc(result["status"])}</p>
    <p>This uses provisional numerical criteria, not a qualified engineering result.</p>
    {summary}{identity}{chart}<table><tr><th>Check</th><th>Outcome</th><th>Evidence</th></tr>{rows}</table>
    <h2>Limitations</h2><ul>{limitations}</ul><p>Complete metrics, inputs, environment, and artifact hashes: <a href="result.json">result.json</a>.</p></html>""",
        encoding="utf-8",
    )
