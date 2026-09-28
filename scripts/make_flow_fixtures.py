#!/usr/bin/env python3
"""Generate declared development fluid volumes; no fixture logic lives in the compiler."""

import argparse
import math
from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeTorus
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

from venturi.geometry import inspect_step
from venturi.models import BoundarySelection, InternalFlowStudy, InternalMeshSpec, write_json


def cylinder(start, direction, radius, length):
    return BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(*start), gp_Dir(*direction)), radius, length
    ).Shape()


def make(output: Path, held_out: bool = False):
    output.mkdir(parents=True, exist_ok=True)
    if held_out:
        # Different dimensions, orientation and origin, generated after the contract.
        shape = BRepPrimAPI_MakeBox(11, 7, 73).Shape()
        transform = gp_Trsf()
        transform.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 2, 3)), 0.47)
        transform.SetTranslationPart(gp_Vec(140, -90, 45))
        shape = BRepBuilderAPI_Transform(shape, transform, True).Shape()
        inlet = gp_Pnt(5.5, 3.5, 0).Transformed(transform)
        outlet = gp_Pnt(5.5, 3.5, 73).Transformed(transform)
        cases = {
            "held-out-rotated-duct": (
                shape,
                [(inlet.X(), inlet.Y(), inlet.Z())],
                [(outlet.X(), outlet.Y(), outlet.Z())],
                0.00065,
                8e-8,
            )
        }
        # A second unseen variant was declared after adding explicit edge snapping.
        transform2 = gp_Trsf()
        transform2.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(2, 1, 3)), 0.61)
        transform2.SetTranslationPart(gp_Vec(170, 60, -45))
        shape2 = BRepBuilderAPI_Transform(
            BRepPrimAPI_MakeBox(11.7, 6.8, 79).Shape(), transform2, True
        ).Shape()
        port2 = [gp_Pnt(5.85, 3.4, z).Transformed(transform2) for z in (0, 79)]
        cases["held-out-rotated-duct-2"] = (
            shape2,
            [(port2[0].X(), port2[0].Y(), port2[0].Z())],
            [(port2[1].X(), port2[1].Y(), port2[1].Z())],
            0.0006,
            8e-8,
        )
    else:
        stem = cylinder((0, 0, 0), (0, 0, 1), 5, 50)
        cross = cylinder((-30, 0, 50), (1, 0, 0), 5, 60)
        tee = BRepAlgoAPI_Fuse(stem, cross)
        tee.Build()
        if not tee.IsDone():
            raise RuntimeError("Fixture fusion failed")
        cases = {
            "pipe": (
                cylinder((0, 0, 0), (0, 0, 1), 5, 100),
                [(0, 0, 0)],
                [(0, 0, 100)],
                0.0008,
                math.pi * 0.005**2 * 0.002,
            ),
            "bend": (
                BRepPrimAPI_MakeTorus(30, 5, math.pi / 2).Shape(),
                [(30, 0, 0)],
                [(0, 30, 0)],
                0.0008,
                math.pi * 0.005**2 * 0.002,
            ),
            "manifold": (
                tee.Shape(),
                [(0, 0, 0)],
                [(-30, 0, 50), (30, 0, 50)],
                0.000625,
                math.pi * 0.005**2 * 0.002,
            ),
            "rectangular": (
                BRepPrimAPI_MakeBox(10, 8, 80).Shape(),
                [(5, 4, 0)],
                [(5, 4, 80)],
                0.0007,
                8e-8,
            ),
        }
    for name, (shape, ins, outs, h, flow) in cases.items():
        file = output / f"{name}.step"
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        if writer.Write(str(file)) != IFSelect_RetDone:
            raise RuntimeError("STEP write failed")
        g = inspect_step(file)
        assignments = {f["id"]: "wall" for f in g["faces"]}
        for role, centers in [("inlet", ins), ("outlet", outs)]:
            for center in centers:
                matches = [
                    f
                    for f in g["faces"]
                    if f["normal"] is not None
                    and max(abs(f["centroid_m"][i] - center[i] / 1000) for i in range(3)) < 1e-7
                ]
                if len(matches) != 1:
                    raise RuntimeError(
                        f"{name}: expected one {role} at {center}, got {len(matches)}"
                    )
                assignments[matches[0]["id"]] = role
        study = InternalFlowStudy(
            name=f"Development fixture: {name}",
            selection=BoundarySelection(geometry_hash=g["geometry_hash"], assignments=assignments),
            flow_rate_m3_s=flow,
            mesh=InternalMeshSpec(cell_size_m=h),
        )
        write_json(output / f"{name}.study.json", study.model_dump())
        print(name, len(g["faces"]), g["volume_m3"], flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--held-out", action="store_true")
    args = p.parse_args()
    make(args.output, args.held_out)
