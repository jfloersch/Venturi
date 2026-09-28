"""Open CASCADE import, tessellation, and revision-bound face references.

Open CASCADE translates STEP to metres here. Original STEP bytes are immutable.
References are deliberately valid only for the identical geometry hash.
"""

import hashlib
import json
import math
import shutil
import tempfile
from pathlib import Path

from .models import BoundarySelection, file_hash, write_json


def make_pipe(path: Path, radius_m: float = 0.005, length_m: float = 0.1) -> None:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    # OCCT's STEP writer uses millimetres by default; the fixture dimensions are SI.
    shape = BRepPrimAPI_MakeCylinder(radius_m * 1000, length_m * 1000).Shape()
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    path.parent.mkdir(parents=True, exist_ok=True)
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise ValueError("Open CASCADE could not write the pipe fixture.")


def read_step_shape(path: Path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TColStd import TColStd_SequenceOfAsciiString

    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("STEP exceeds the 16 MiB supported import limit.")
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise ValueError("Could not read STEP. Supply a prepared, watertight fluid volume.")
    model = reader.WS().Model()
    if any(
        model.Check(i, True).NbFails() or model.Check(i, False).NbFails()
        for i in range(model.NbEntities() + 1)
    ):
        raise ValueError(
            "STEP contains invalid or incomplete entities or units; repair it before importing."
        )
    units = [TColStd_SequenceOfAsciiString() for _ in range(3)]
    reader.FileUnits(*units)
    unit_names = [units[0].Value(i).ToCString() for i in range(1, units[0].Length() + 1)]
    if not unit_names:
        raise ValueError("STEP has no declared length units; refusing to guess its scale.")
    reader.SetSystemLengthUnit(1000.0)  # 1000 mm per output unit: metres.
    if reader.TransferRoots() == 0:
        raise ValueError("STEP contains no transferable geometry.")
    return reader.OneShape(), unit_names


def inspect_step(path: Path) -> dict:
    from OCP.Bnd import Bnd_Box
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import (
        TopAbs_EDGE,
        TopAbs_FACE,
        TopAbs_REVERSED,
        TopAbs_SOLID,
        TopAbs_VERTEX,
        TopAbs_WIRE,
    )
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    shape, unit_names = read_step_shape(path)
    if not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError("Invalid CAD topology; repair the fluid volume before importing.")
    solids = TopExp_Explorer(shape, TopAbs_SOLID)
    count = 0
    solid = None
    while solids.More():
        solid = solids.Current()
        count += 1
        solids.Next()
    if count != 1:
        raise ValueError(f"Expected one connected solid fluid volume, found {count}.")
    for kind in (TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX):
        all_entities, solid_entities = TopTools_IndexedMapOfShape(), TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, kind, all_entities)
        TopExp.MapShapes_s(solid, kind, solid_entities)
        if all_entities.Extent() != solid_entities.Extent():
            raise ValueError(
                "Loose faces, edges or vertices outside the fluid solid are unsupported."
            )
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    if props.Mass() <= 0:
        raise ValueError("Fluid volume must have positive enclosed volume.")
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    bounds = list(box.Get())
    extent = max(bounds[i + 3] - bounds[i] for i in range(3))
    if not 1e-5 <= extent <= 100:
        raise ValueError(
            "Geometry scale is outside the supported range (10 µm to 100 m). Check units."
        )
    BRepMesh_IncrementalMesh(shape, extent * 0.0002, False, 0.15, True).Perform()
    geometry_hash = file_hash(path)
    faces = []
    points = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    index = 0
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        if index >= 256:
            raise ValueError("Prepared geometry is limited to 256 faces.")
        p = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, p)
        center = p.CentreOfMass()
        signature = {
            "index": index,
            "area_m2": round(p.Mass(), 14),
            "centroid_m": [round(center.X(), 12), round(center.Y(), 12), round(center.Z(), 12)],
            "surface_type": str(BRepAdaptor_Surface(face).GetType()).split(".")[-1],
        }
        face_id = (
            "face_"
            + hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:16]
        )
        perimeter = GProp_GProps()
        BRepGProp.LinearProperties_s(face, perimeter)
        adaptor = BRepAdaptor_Surface(face)
        normal = None
        if adaptor.GetType() == GeomAbs_Plane:
            direction = adaptor.Plane().Axis().Direction()
            sign = -1 if face.Orientation() == TopAbs_REVERSED else 1
            normal = [sign * direction.X(), sign * direction.Y(), sign * direction.Z()]
        wires = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(face, TopAbs_WIRE, wires)
        location = TopLoc_Location()
        triangles = BRep_Tool.Triangulation_s(face, location)
        if triangles is None or triangles.NbTriangles() == 0:
            raise ValueError(f"Face {index + 1} could not be tessellated.")
        offset = len(points) // 3
        for i in range(1, triangles.NbNodes() + 1):
            point = triangles.Node(i).Transformed(location.Transformation())
            points.extend([point.X(), point.Y(), point.Z()])
        cells = []
        for i in range(1, triangles.NbTriangles() + 1):
            a, b, c = triangles.Triangle(i).Get()
            if face.Orientation() == TopAbs_REVERSED:
                b, c = c, b
            cells.extend([3, offset + a - 1, offset + b - 1, offset + c - 1])
        faces.append(
            {
                "id": face_id,
                **signature,
                "cells": cells,
                "normal": normal,
                "perimeter_m": perimeter.Mass(),
                "wire_count": wires.Extent(),
            }
        )
        if len(points) > 600000:
            raise ValueError("CAD tessellation exceeds the 200,000-vertex import budget.")
        index += 1
        explorer.Next()
    # Faces belonging to shells outside the one solid are not supported either.
    if not faces:
        raise ValueError("STEP contains no selectable faces.")
    return {
        "schema_version": 1,
        "geometry_hash": geometry_hash,
        "source_units": unit_names,
        "units": "m",
        "bounds_m": bounds,
        "volume_m3": props.Mass(),
        "points": points,
        "faces": faces,
    }


def fixture_selection(geometry: dict) -> BoundarySelection:
    zmin, zmax = geometry["bounds_m"][2], geometry["bounds_m"][5]
    assignments = {}
    for face in geometry["faces"]:
        z = face["centroid_m"][2]
        role = "inlet" if abs(z - zmin) < 1e-8 else "outlet" if abs(z - zmax) < 1e-8 else "wall"
        assignments[face["id"]] = role
    return BoundarySelection(geometry_hash=geometry["geometry_hash"], assignments=assignments)


def validate_selection(
    geometry: dict, selection: BoundarySelection, *, internal: bool = False
) -> None:
    if selection.geometry_hash != geometry["geometry_hash"]:
        raise ValueError("Geometry changed. Boundary selections require explicit remapping.")
    ids = {face["id"] for face in geometry["faces"]}
    if set(selection.assignments) != ids:
        raise ValueError(
            "Assign every face exactly once; unknown or missing face references found."
        )
    roles = list(selection.assignments.values())
    if internal:
        if roles.count("inlet") != 1 or not 1 <= roles.count("outlet") <= 4 or "wall" not in roles:
            raise ValueError("Select exactly one inlet, one to four outlets, and walls.")
        for face in geometry["faces"]:
            if selection.assignments[face["id"]] != "wall":
                if face.get("normal") is None or face.get("wire_count") != 1:
                    raise ValueError("Ports must be planar faces with one closed boundary wire.")
        return
    if roles.count("inlet") != 1 or roles.count("outlet") != 1 or "wall" not in roles:
        raise ValueError(
            "M0 supports exactly one inlet face, one outlet face, and at least one wall."
        )


def export_surfaces(
    geometry: dict, selection: BoundarySelection, folder: Path, *, internal: bool = False
) -> dict:
    """Write one region per role and keep a manifest back to the exact CAD faces."""
    validate_selection(geometry, selection, internal=internal)
    folder.mkdir(parents=True, exist_ok=True)
    points = geometry["points"]
    mapping = {}
    groups = {
        role: [f for f in geometry["faces"] if selection.assignments[f["id"]] == role]
        for role in ("inlet", "outlet", "wall")
    }
    if internal:
        outlets = groups.pop("outlet")
        groups.update({f"outlet_{i + 1}": [face] for i, face in enumerate(outlets)})
    for role, chosen in groups.items():
        lines = [f"solid {role}"]
        for face in chosen:
            cells = face["cells"]
            for i in range(0, len(cells), 4):
                vertices = [points[j * 3 : j * 3 + 3] for j in cells[i + 1 : i + 4]]
                u = [vertices[1][j] - vertices[0][j] for j in range(3)]
                v = [vertices[2][j] - vertices[0][j] for j in range(3)]
                n = [
                    u[1] * v[2] - u[2] * v[1],
                    u[2] * v[0] - u[0] * v[2],
                    u[0] * v[1] - u[1] * v[0],
                ]
                length = math.sqrt(sum(x * x for x in n))
                if length == 0:
                    raise ValueError("Degenerate CAD triangle; cannot export a mesh surface.")
                lines += ["facet normal " + " ".join(f"{x / length:.12g}" for x in n), "outer loop"]
                lines += ["vertex " + " ".join(f"{x:.12g}" for x in vtx) for vtx in vertices]
                lines += ["endloop", "endfacet"]
        lines.append(f"endsolid {role}")
        path = folder / f"{role}.stl"
        path.write_text("\n".join(lines) + "\n")
        mapping[role] = {
            "face_ids": [f["id"] for f in chosen],
            "area_m2": sum(f["area_m2"] for f in chosen),
            "sha256": file_hash(path),
        }
    write_json(
        folder / "boundary-map.json",
        {"geometry_hash": geometry["geometry_hash"], "patches": mapping},
    )
    return mapping


def import_geometry(source: Path, storage: Path, *, name: str | None = None) -> dict:
    """Copy before parsing, retain source bytes, and publish only a validated revision."""
    root = storage / "geometry"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".import-", dir=root) as temp:
        folder = Path(temp)
        shutil.copyfile(source, folder / "source.step")
        geometry = inspect_step(folder / "source.step")
        geometry["source_name"] = Path(name or source.name).name[:160]
        geometry["imported"] = True
        write_json(folder / "geometry.json", geometry)
        write_json(
            folder / "selection.json",
            BoundarySelection(
                geometry_hash=geometry["geometry_hash"],
                assignments={f["id"]: "wall" for f in geometry["faces"]},
            ).model_dump(),
        )
        destination = root / geometry["geometry_hash"]
        if not destination.exists():
            # Same-filesystem rename publishes a complete immutable revision.
            try:
                folder.rename(destination)
            except FileExistsError:
                pass
        if file_hash(destination / "source.step") != geometry["geometry_hash"]:
            raise ValueError("Stored geometry was modified; restore the original source bytes.")
        previous = json.loads((destination / "geometry.json").read_text(encoding="utf-8"))
        if not previous.get("imported"):
            write_json(
                destination / "selection.json",
                BoundarySelection(
                    geometry_hash=geometry["geometry_hash"],
                    assignments={f["id"]: "wall" for f in geometry["faces"]},
                ).model_dump(),
            )
        write_json(destination / "geometry.json", geometry)
        write_json(
            storage / "active-geometry.json",
            {"geometry_hash": geometry["geometry_hash"], "imported": True},
        )
        return {
            **geometry,
            "selection": json.loads((destination / "selection.json").read_text(encoding="utf-8")),
        }


def interior_point(source: Path, geometry: dict, h: float, low: list[float]) -> list[float]:
    """A deterministic CAD-classified point, safely away from background cell planes."""
    from itertools import product

    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    shape, _ = read_step_shape(source)
    classifier = BRepClass3d_SolidClassifier(shape)
    bounds = geometry["bounds_m"]
    widths = [bounds[i + 3] - bounds[i] for i in range(3)]
    candidates = [[(bounds[i] + bounds[i + 3]) / 2 for i in range(3)]]
    for face in geometry["faces"]:
        if face.get("normal") is not None:
            candidates.append(
                [face["centroid_m"][i] - face["normal"][i] * h * 0.37 for i in range(3)]
            )
    for n in (5, 11, 19):
        candidates.extend(
            [
                [bounds[i] + widths[i] * (v[i] + 0.371) / n for i in range(3)]
                for v in product(range(n), repeat=3)
            ]
        )
    for p in candidates:
        p = [low[i] + (math.floor((p[i] - low[i]) / h) + 0.371) * h for i in range(3)]
        classifier.Perform(gp_Pnt(*p), min(h * 1e-5, 1e-8))
        if classifier.State() == TopAbs_IN:
            return p
    raise ValueError(
        "No supported interior mesh point found. Refine the declared mesh or repair the fluid volume."
    )
