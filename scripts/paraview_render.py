"""Run with pinned pvpython to render the actual reference velocity slice."""

import json
import os
import platform
import sys
from pathlib import Path

if "microsoft" in platform.release().lower() and os.getenv("VENTURI_HARDWARE_RENDERING") != "1":
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    os.environ.setdefault("GALLIUM_DRIVER", "llvmpipe")

from paraview.simple import (
    ColorBy,
    CreateView,
    GetColorTransferFunction,
    GetParaViewVersion,
    GetScalarBar,
    LegacyVTKReader,
    SaveScreenshot,
    Show,
    Slice,
    Text,
)

case = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
source = case / "VTK/case_500.vtk"
if not source.is_file():
    raise SystemExit("Expected the completed 500-iteration reference VTK export.")
output.mkdir(parents=True, exist_ok=True)
reader = LegacyVTKReader(FileNames=[str(source)])
reader.UpdatePipeline()
section = Slice(Input=reader)
section.SliceType = "Plane"
section.SliceType.Origin = [0, 0, 0.05]
section.SliceType.Normal = [1, 0, 0]
section.UpdatePipeline()
if section.GetDataInformation().GetNumberOfCells() == 0:
    raise SystemExit("The requested slice has no cells; refusing to render an empty result.")
view = CreateView("RenderView")
view.ViewSize = [1200, 500]
view.Background = [0.96, 0.97, 0.95]
view.UseColorPaletteForBackground = 0
display = Show(section, view)
ColorBy(display, ("POINTS", "U", "Magnitude"))
display.SetScalarBarVisibility(view, True)
lut = GetColorTransferFunction("U")
lut.RescaleTransferFunction(0, 0.02)
view.CameraPosition = [0.2, 0, 0.05]
view.CameraFocalPoint = [0, 0, 0.05]
view.CameraViewUp = [0, 1, 0]
view.CameraParallelProjection = 1
view.CameraParallelScale = 0.024
view.GetActiveCamera().SetClippingRange(0.001, 1)
bar = GetScalarBar(lut, view)
bar.Title = "Speed (m/s)"
bar.ComponentTitle = ""
bar.TitleColor = [0.12, 0.22, 0.18]
bar.LabelColor = [0.12, 0.22, 0.18]
annotation = Text(
    Text="Venturi M0 · laminar pipe · Re 100 · iteration 500\nVelocity magnitude · centre plane x = 0 m"
)
caption = Show(annotation, view)
caption.Color = [0.12, 0.22, 0.18]
caption.FontSize = 13
SaveScreenshot(str(output / "velocity-slice.png"), view, ImageResolution=[1200, 500])
(output / "render.json").write_text(
    json.dumps(
        {
            "paraview_version": str(GetParaViewVersion()),
            "source": "case/VTK/case_500.vtk",
            "field": "U magnitude",
            "units": "m/s",
            "range": [0, 0.02],
            "plane": "x=0",
            "camera": {
                "position": [0.2, 0, 0.05],
                "focal_point": [0, 0, 0.05],
                "view_up": [0, 1, 0],
            },
            "status": "rendered",
        },
        indent=2,
    )
    + "\n"
)
print("Rendered actual solver fields:", output / "velocity-slice.png")
