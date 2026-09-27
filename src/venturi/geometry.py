"""Open CASCADE import, tessellation, and revision-bound face references.

Open CASCADE translates STEP to metres here. Original STEP bytes are immutable.
References are deliberately valid only for the identical geometry hash.
"""

import hashlib
import json
import math
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


def inspect_step(path: Path) -> dict:
    from OCP.Bnd import Bnd_Box
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TColStd import TColStd_SequenceOfAsciiString
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise ValueError("Could not read STEP. Supply a prepared, watertight fluid volume.")
    units = [TColStd_SequenceOfAsciiString() for _ in range(3)]
    reader.FileUnits(*units)
    unit_names = [units[0].Value(i).ToCString() for i in range(1, units[0].Length() + 1)]
    if not unit_names:
        raise ValueError("STEP has no declared length units; refusing to guess its scale.")
    # SetSystemLengthUnit is expressed in millimetres: 1000 means one output unit is 1 m.
    reader.SetSystemLengthUnit(1000.0)
    if reader.TransferRoots() == 0:
        raise ValueError("STEP contains no transferable geometry.")
    shape = reader.OneShape()
    if not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError("Invalid CAD topology; repair the fluid volume before importing.")
    solids = TopExp_Explorer(shape, TopAbs_SOLID)
    count = 0
    while solids.More():
        count += 1
        solids.Next()
    if count != 1:
        raise ValueError(f"Expected one connected solid fluid volume, found {count}.")
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    if props.Mass() <= 0:
        raise ValueError("Fluid volume must have positive enclosed volume.")
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    bounds = list(box.Get())
    extent = max(bounds[i + 3] - bounds[i] for i in range(3))
    if not 1e-5 <= extent <= 100:
        raise ValueError("Geometry scale is outside the M0 range (10 µm to 100 m). Check units.")
    BRepMesh_IncrementalMesh(shape, extent * 0.0002, False, 0.15, True).Perform()
    geometry_hash = file_hash(path)
    faces = []
    points = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    index = 0
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
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
        faces.append({"id": face_id, **signature, "cells": cells})
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


def validate_selection(geometry: dict, selection: BoundarySelection) -> None:
    if selection.geometry_hash != geometry["geometry_hash"]:
        raise ValueError("Geometry changed. Boundary selections require explicit remapping.")
    ids = {face["id"] for face in geometry["faces"]}
    if set(selection.assignments) != ids:
        raise ValueError(
            "Assign every face exactly once; unknown or missing face references found."
        )
    roles = list(selection.assignments.values())
    if roles.count("inlet") != 1 or roles.count("outlet") != 1 or "wall" not in roles:
        raise ValueError(
            "M0 supports exactly one inlet face, one outlet face, and at least one wall."
        )


def export_surfaces(geometry: dict, selection: BoundarySelection, folder: Path) -> dict:
    """Write one region per role and keep a manifest back to the exact CAD faces."""
    validate_selection(geometry, selection)
    folder.mkdir(parents=True, exist_ok=True)
    points = geometry["points"]
    mapping = {}
    for role in ("inlet", "outlet", "wall"):
        chosen = [f for f in geometry["faces"] if selection.assignments[f["id"]] == role]
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
