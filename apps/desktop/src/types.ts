export type Check = {
  name: string;
  status: "pass" | "fail" | "pending";
  detail: string;
};
export type Face = {
  id: string;
  index: number;
  area_m2: number;
  centroid_m: number[];
  surface_type: string;
  cells: number[];
  normal?: number[] | null;
  perimeter_m?: number;
};
export type Role = "inlet" | "outlet" | "wall";
export type Geometry = {
  geometry_hash: string;
  source_units: string[];
  bounds_m: number[];
  volume_m3: number;
  points: number[];
  faces: Face[];
  edges?: Edge[];
  selection: { geometry_hash: string; assignments: Record<string, Role> };
  source_name?: string;
  imported?: boolean;
};
export type Diagnostics = {
  protocol_version: string;
  platform: string;
  architecture: string;
  is_wsl: boolean;
  solver_ready: boolean;
  checks: Check[];
};
export type Result = {
  status: string;
  checks: Check[];
  pressure_drop_pa?: number;
  expected_pressure_drop_pa?: number;
  relative_error?: number;
  mass_imbalance?: number;
  iterations?: number;
  reynolds?: number;
  history?: {
    iteration: number;
    pressure_drop_pa: number;
    mass_imbalance: number;
  }[];
  limitations: string[];
  recipe?: string;
  study_hash?: string;
  recipe_hash?: string;
  inputs?: { name?: string; pipe?: Record<string, number> };
  mesh_hash?: string;
  mesh?: { cells: number; volume_m3: number; regions: number };
  volume_flow_in_m3_s?: number;
  outlets?: Record<
    string,
    {
      volume_flow_m3_s: number;
      flow_fraction: number;
      pressure_drop_pa: number;
      backflow_fraction: number;
    }
  >;
};
export type InternalStudy = {
  schema_version: "venturi.internal-study.v1" | "venturi.rans-study.v1";
  name: string;
  recipe: "laminar-internal/1" | "sst-straight-duct/1";
  turbulence_intensity?: number;
  turbulence_length_scale_m?: number;
  selection: Geometry["selection"];
  flow_rate_m3_s: number;
  density_kg_m3: number;
  dynamic_viscosity_pa_s: number;
  mesh: {
    cell_size_m: number;
    maximum_cells?: number;
    timeout_seconds?: number;
  };
  resources?: { wall_time_seconds: number; memory_mb: number; disk_mb: number };
  max_iterations?: number;
  timeout_seconds?: number;
};
export type Study =
  { name?: string; pipe?: Record<string, number> } | InternalStudy;
export type RunKind =
  "reference" | "cad_mesh" | "internal_mesh" | "internal_flow";
export type Run = {
  desktop_brief?: {
    id: string;
    revision: number;
    question: string;
    material_source: string;
    profile: string;
  };
  id: string;
  kind: RunKind;
  status: string;
  stage: string;
  created_at: string;
  error?: string;
  result?: Result;
  study_hash?: string;
  recipe_hash?: string;
  retry_of?: string;
  request?: {
    kind: RunKind;
    request_id: string;
    geometry_hash?: string | null;
    study?: Study | null;
    mesh_run_id?: string | null;
    approved_mesh_hash?: string | null;
    reason?: string;
    retry_of?: string | null;
    desktop_study?: { id: string; revision: number } | null;
  };
};

export type Camera = {
  position: number[];
  focal_point: number[];
  view_up: number[];
};
export type Edge = {
  id: string;
  index: number;
  length_m: number;
  centroid_m: number[];
  adjacent_faces: string[];
  points: number[];
  curve_type: string;
};
export type Annotation = {
  id: string;
  entity_id: string;
  label: string;
  text: string;
  position_m: number[];
  camera: Camera;
};
export type Notes = {
  geometry_hash: string;
  revision: number;
  annotations: Annotation[];
};
export type StudyRecord = {
  id: string;
  revision: number;
  study_hash: string;
  updated_at: string;
  study: InternalStudy;
  question: string;
  material_source: string;
  profile: "guided" | "collaborative" | "expert";
};
export type StudySave = Omit<
  StudyRecord,
  "id" | "revision" | "study_hash" | "updated_at"
>;
export type Api = (path: string, init?: RequestInit) => Promise<any>;
