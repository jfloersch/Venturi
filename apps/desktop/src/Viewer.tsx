import { useEffect, useRef, useState } from "react";
import "@kitware/vtk.js/Rendering/Profiles/Geometry";
import vtkGenericRenderWindow from "@kitware/vtk.js/Rendering/Misc/GenericRenderWindow";
import vtkActor from "@kitware/vtk.js/Rendering/Core/Actor";
import vtkMapper from "@kitware/vtk.js/Rendering/Core/Mapper";
import vtkPolyData from "@kitware/vtk.js/Common/DataModel/PolyData";
import vtkCellPicker from "@kitware/vtk.js/Rendering/Core/CellPicker";
import vtkPlane from "@kitware/vtk.js/Common/DataModel/Plane";
import { Crosshair, Layers3, RotateCcw } from "lucide-react";
import type { Camera, Geometry, Role } from "./types";

const colors: Record<Role, [number, number, number]> = {
  inlet: [0.15, 0.76, 0.65],
  outlet: [0.97, 0.68, 0.3],
  wall: [0.45, 0.58, 0.59],
};

export function Viewer({
  geometry,
  assignments,
  selected,
  onSelect,
  mesh = false,
  onRenderReady,
  onCamera,
  restoreCamera,
}: {
  geometry: Geometry;
  assignments: Record<string, Role>;
  selected: string | null;
  onSelect: (id: string) => void;
  mesh?: boolean;
  onRenderReady?: (ready: boolean) => void;
  onCamera?: (camera: Camera) => void;
  restoreCamera?: Camera | null;
}) {
  const container = useRef<HTMLDivElement>(null);
  const scene = useRef<{
    render: () => void;
    reset: () => void;
    actors: {
      id: string;
      actor: ReturnType<typeof vtkActor.newInstance>;
      mapper: ReturnType<typeof vtkMapper.newInstance>;
    }[];
    plane: ReturnType<typeof vtkPlane.newInstance>;
    camera: ReturnType<
      ReturnType<typeof vtkGenericRenderWindow.newInstance>["getRenderer"]
    >["getActiveCamera"] extends () => infer T
      ? T
      : never;
  } | null>(null);
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;
  const readyRef = useRef(onRenderReady);
  readyRef.current = onRenderReady;
  const [clip, setClip] = useState(false);
  const [transparent, setTransparent] = useState(false);
  const [isolate, setIsolate] = useState(false);
  const [showEdges, setShowEdges] = useState(false);
  const [axis, setAxis] = useState(0);
  const [cut, setCut] = useState(50);
  const cameraRef = useRef(onCamera);
  cameraRef.current = onCamera;
  const [error, setError] = useState("");

  useEffect(() => {
    if (!container.current) return;
    readyRef.current?.(false);
    let cleanup = () => {};
    try {
      const window = vtkGenericRenderWindow.newInstance({
        background: [0.074, 0.108, 0.113],
      });
      window.setContainer(container.current);
      const renderer = window.getRenderer();
      const renderWindow = window.getRenderWindow();
      const plane = vtkPlane.newInstance({
        normal: [1, 0, 0],
        origin: [0, 1, 2].map(
          (i) => (geometry.bounds_m[i] + geometry.bounds_m[i + 3]) / 2,
        ) as [number, number, number],
      });
      const actors = geometry.faces.map((face) => {
        const data = vtkPolyData.newInstance();
        data.getPoints().setData(Float32Array.from(geometry.points), 3);
        data.getPolys().setData(Uint32Array.from(face.cells));
        const mapper = vtkMapper.newInstance({ scalarVisibility: false });
        mapper.setInputData(data);
        const actor = vtkActor.newInstance();
        actor.setMapper(mapper);
        actor.getProperty().setColor(...colors[assignments[face.id] || "wall"]);
        actor.getProperty().setAmbient(0.28);
        actor.getProperty().setDiffuse(0.72);
        actor.getProperty().setEdgeVisibility(mesh);
        actor.getProperty().setEdgeColor(0.08, 0.15, 0.16);
        renderer.addActor(actor);
        return { id: face.id, actor, mapper, data };
      });
      for (const edge of geometry.edges || []) {
        const data = vtkPolyData.newInstance();
        data.getPoints().setData(Float32Array.from(edge.points), 3);
        const count = edge.points.length / 3;
        data
          .getLines()
          .setData(
            Uint32Array.from([
              count,
              ...Array.from({ length: count }, (_, i) => i),
            ]),
          );
        const mapper = vtkMapper.newInstance({ scalarVisibility: false });
        mapper.setInputData(data);
        const actor = vtkActor.newInstance();
        actor.setMapper(mapper);
        actor.getProperty().setColor(1, 0.85, 0.45);
        actor.getProperty().setLineWidth(3);
        actor.setVisibility(false);
        renderer.addActor(actor);
        actors.push({ id: edge.id, actor, mapper, data });
      }
      const camera = renderer.getActiveCamera();
      const cameraSubscription = camera.onModified(() =>
        cameraRef.current?.({
          position: [...camera.getPosition()],
          focal_point: [...camera.getFocalPoint()],
          view_up: [...camera.getViewUp()],
        }),
      );
      const reset = () => {
        const camera = renderer.getActiveCamera();
        const center = [0, 1, 2].map(
          (i) => (geometry.bounds_m[i] + geometry.bounds_m[i + 3]) / 2,
        );
        const length = Math.max(
          ...[0, 1, 2].map(
            (i) => geometry.bounds_m[i + 3] - geometry.bounds_m[i],
          ),
        );
        camera.setPosition(
          center[0] + length * 1.3,
          center[1] - length * 0.8,
          center[2] - length * 0.95,
        );
        camera.setFocalPoint(...(center as [number, number, number]));
        camera.setViewUp(0, 0, 1);
        renderer.resetCamera();
        window.resize();
        renderWindow.render();
      };
      const picker = vtkCellPicker.newInstance({ tolerance: 0.002 });
      const subscription = window.getInteractor().onLeftButtonPress((event) => {
        picker.pick([event.position.x, event.position.y, 0], renderer);
        const picked = picker.getActors()[0];
        const match = actors.find((a) => a.actor === picked);
        if (match) selectRef.current(match.id);
      });
      const resize = new ResizeObserver(() => window.resize());
      resize.observe(container.current);
      scene.current = {
        render: () => renderWindow.render(),
        reset,
        actors,
        plane,
        camera,
      };
      cleanup = () => {
        scene.current = null;
        resize.disconnect();
        subscription.unsubscribe();
        cameraSubscription.unsubscribe();
        picker.delete();
        actors.forEach(({ actor, mapper, data }) => {
          renderer.removeActor(actor);
          actor.delete();
          mapper.delete();
          data.delete();
        });
        plane.delete();
        window.delete();
      };
      setError("");
      reset();
      const view = window.getApiSpecificRenderWindow();
      if (!("get3DContext" in view))
        throw new Error("Expected the WebGL renderer.");
      const gl = view.get3DContext({});
      if (!gl)
        throw new Error("WebGL 2 is not available in this desktop renderer.");
      const pixels = new Uint8Array(
        gl.drawingBufferWidth * gl.drawingBufferHeight * 4,
      );
      gl.readPixels(
        0,
        0,
        gl.drawingBufferWidth,
        gl.drawingBufferHeight,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        pixels,
      );
      let visiblePixels = 0;
      for (let i = 0; i < pixels.length; i += 4)
        if (pixels[i] > 45 || pixels[i + 1] > 55 || pixels[i + 2] > 55)
          visiblePixels++;
      const diagnostic = `Geometry renderer: ${gl.getParameter(gl.VERSION)}, ${gl.drawingBufferWidth}x${gl.drawingBufferHeight}, ${visiblePixels} visible pixels; GL error ${gl.getError()}`;
      container.current.dataset.renderedPixels = String(visiblePixels);
      readyRef.current?.(visiblePixels > 0);
      if ("__TAURI_INTERNALS__" in globalThis.window)
        import("@tauri-apps/api/core")
          .then(({ invoke }) =>
            invoke("client_diagnostic", { message: diagnostic }),
          )
          .catch(() => {});
      if (visiblePixels === 0)
        setError(
          "The graphics context opened but did not draw the geometry. Try the browser workbench and include the desktop graphics log in your test report.",
        );
    } catch (e) {
      setError(
        `3D rendering is unavailable: ${String(e)}. Boundary selection remains available in the face list.`,
      );
    }
    return cleanup;
  }, [geometry]); // Rebuild only when the immutable geometry revision changes.

  useEffect(() => {
    scene.current?.actors.forEach(({ id, actor, mapper }) => {
      const role = assignments[id] || "wall";
      const edge = id.startsWith("edge_");
      actor.setVisibility(
        edge
          ? showEdges || id === selected
          : !isolate ||
              id === selected ||
              !!geometry.edges
                ?.find((e) => e.id === selected)
                ?.adjacent_faces.includes(id),
      );
      const [red, green, blue] = colors[role];
      const highlight = id === selected ? 0.2 : 0;
      actor
        .getProperty()
        .setColor(
          red + highlight * (1 - red),
          green + highlight * (1 - green),
          blue + highlight * (1 - blue),
        );
      actor.getProperty().setOpacity(transparent && role === "wall" ? 0.24 : 1);
      if (edge) {
        actor.getProperty().setColor(id === selected ? 1 : 0.8, 0.8, 0.2);
        actor.getProperty().setOpacity(1);
      }
      mapper.removeAllClippingPlanes();
      if (clip && scene.current) {
        const normal: [number, number, number] = [0, 0, 0];
        normal[axis] = 1;
        scene.current.plane.setNormal(...normal);
        const origin = [0, 1, 2].map(
          (i) =>
            geometry.bounds_m[i] +
            (geometry.bounds_m[i + 3] - geometry.bounds_m[i]) *
              (i === axis ? cut / 100 : 0.5),
        );
        scene.current.plane.setOrigin(...(origin as [number, number, number]));
        mapper.addClippingPlane(scene.current.plane);
      }
    });
    scene.current?.render();
  }, [
    geometry,
    assignments,
    selected,
    clip,
    transparent,
    isolate,
    showEdges,
    axis,
    cut,
  ]);

  useEffect(() => {
    if (!restoreCamera || !scene.current) return;
    const camera = scene.current.camera;
    camera.setPosition(...(restoreCamera.position as [number, number, number]));
    camera.setFocalPoint(
      ...(restoreCamera.focal_point as [number, number, number]),
    );
    camera.setViewUp(...(restoreCamera.view_up as [number, number, number]));
    scene.current.render();
  }, [restoreCamera]);

  return (
    <div className="viewer-wrap">
      <div className="viewer" ref={container} data-testid="geometry-viewer" />
      <div className="viewer-label">
        <span className="live-dot" /> {mesh ? "MESH BOUNDARY" : "FLUID VOLUME"}{" "}
        <span className="viewer-sub">
          {mesh ? "Cell edges / metres" : "STEP / metres"}
        </span>
      </div>
      <div className="viewer-tools">
        <button
          className={isolate ? "active" : ""}
          disabled={!selected}
          onClick={() => setIsolate(!isolate)}
          aria-label="Isolate selected reference"
          title="Isolate selected reference"
        >
          I
        </button>
        {!!geometry.edges?.length && (
          <button
            className={showEdges ? "active" : ""}
            onClick={() => setShowEdges(!showEdges)}
            aria-label="Show CAD edges"
            title="Show CAD edges"
          >
            E
          </button>
        )}
        <button
          className={transparent ? "active" : ""}
          onClick={() => setTransparent(!transparent)}
          title="Toggle wall transparency"
          aria-label="Toggle wall transparency"
        >
          <Layers3 size={17} />
        </button>
        <button
          className={clip ? "active" : ""}
          onClick={() => setClip(!clip)}
          title="Toggle section cut"
          aria-label="Toggle section cut"
        >
          <Crosshair size={17} />
        </button>
        <button
          onClick={() => scene.current?.reset()}
          title="Reset camera"
          aria-label="Reset camera"
        >
          <RotateCcw size={17} />
        </button>
      </div>
      {clip && (
        <div className="clip-controls">
          <label>
            Cut axis{" "}
            <select
              value={axis}
              onChange={(e) => setAxis(Number(e.target.value))}
            >
              <option value={0}>X</option>
              <option value={1}>Y</option>
              <option value={2}>Z</option>
            </select>
          </label>
          <label>
            Cut position{" "}
            <input
              type="range"
              min={0}
              max={100}
              value={cut}
              onChange={(e) => setCut(Number(e.target.value))}
            />
          </label>
        </div>
      )}
      <div className="viewer-legend">
        <span>
          <i style={{ background: "#26c2a6" }} />
          Inlet
        </span>
        <span>
          <i style={{ background: "#f7ad4d" }} />
          Outlet
        </span>
        <span>
          <i style={{ background: "#739497" }} />
          Wall
        </span>
      </div>
      <div className="viewer-hint">
        Drag to orbit · Scroll to zoom · Click a face to inspect
      </div>
      {error && (
        <div className="render-error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
