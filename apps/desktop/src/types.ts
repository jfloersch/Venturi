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
};
export type Role = "inlet" | "outlet" | "wall";
export type Geometry = {
  geometry_hash: string;
  source_units: string[];
  bounds_m: number[];
  volume_m3: number;
  points: number[];
  faces: Face[];
  selection: { geometry_hash: string; assignments: Record<string, Role> };
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
  history?: {
    iteration: number;
    pressure_drop_pa: number;
    mass_imbalance: number;
  }[];
  limitations: string[];
};
export type Run = {
  id: string;
  kind: "reference" | "cad_mesh";
  status: string;
  stage: string;
  created_at: string;
  error?: string;
  result?: Result;
};
