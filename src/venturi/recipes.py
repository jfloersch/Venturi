"""Versioned scientific contract; thresholds are never supplied by a caller."""

from pathlib import Path

from .models import (
    InternalFlowStudy,
    RANSFlowStudy,
    StudySpec,
    canonical_hash,
    file_hash,
    parse_study,
)


def pipe_recipe() -> dict:
    return {
        "schema_version": "venturi.recipe.v1",
        "id": "laminar-pipe/1",
        "solver": "OpenFOAM Foundation 14 / incompressibleFluid",
        "compiler": "venturi.foam/1",
        "applicability": {
            "geometry": "straight circular fluid pipe along +z",
            "physics": "steady, incompressible, Newtonian, isothermal, laminar",
            "maximum_reynolds": 500,
            "minimum_length_diameters": 5,
            "maximum_cells": 250000,
        },
        "boundaries": {
            "inlet": "normalized parabolic velocity; pressure zeroGradient",
            "outlet": "zero gauge kinematic pressure; velocity zeroGradient",
            "wall": "stationary no-slip; pressure zeroGradient",
        },
        "quantity": {
            "name": "pressure_drop_pa",
            "units": "Pa",
            "derivation": "density * (areaAverage(inlet,p) - areaAverage(outlet,p))",
            "analytical": "8 * dynamic_viscosity * length * mean_velocity / radius**2",
        },
        "criteria": {
            "pressure_relative_error": 0.05,
            "mass_imbalance": 0.001,
            "pressure_stability": 0.001,
            "stability_window": 20,
            "equation_residual": 1e-5,
            "prescribed_flow_relative_error": 1e-5,
            "reproduction_relative_difference": 1e-6,
        },
        "qualification": "provisional numerical verification; independent CFD review pending",
    }


def internal_recipe() -> dict:
    return {
        "schema_version": "venturi.recipe.v1",
        "id": "laminar-internal/1",
        "solver": "OpenFOAM Foundation 14 / incompressibleFluid",
        "compiler": "venturi.internal/1",
        "applicability": {
            "geometry": "one prepared, closed solid fluid volume; planar simply connected ports",
            "maximum_outlets": 4,
            "maximum_port_reynolds_full_flow": 200,
            "minimum_cells_per_port_diameter": 10,
            "maximum_background_cells": 800000,
            "maximum_cells": 400000,
            "physics": "steady, incompressible, Newtonian, isothermal, laminar; no gravity",
        },
        "boundaries": {
            "inlet": "uniform normal velocity integrated to the declared volumetric flow; pressure zeroGradient",
            "outlets": "equal zero-gauge static pressure; velocity zeroGradient; backflow checked",
            "walls": "stationary no-slip; pressure zeroGradient",
        },
        "quantity": {
            "pressure_drop_pa": "inlet area-mean static pressure minus outlet-flow-weighted area-mean static pressure; kinematic p multiplied by density",
            "outlets": "individual pressure differences and signed outward volume flows; split normalized by sum of outlet flows",
        },
        "criteria": {
            "mesh_area_relative_error": 0.08,
            "mesh_volume_relative_error": 0.05,
            "mesh_centroid_extent_fraction": 0.01,
            "port_normal_dot": 0.98,
            "mass_imbalance": 0.001,
            "pressure_stability": 0.001,
            "flow_stability": 0.001,
            "stability_window": 30,
            "equation_residual": 1e-5,
            "prescribed_flow_relative_error": 1e-5,
            "outlet_backflow_fraction": 0.001,
            "wall_leakage_fraction": 1e-7,
            "field_history_flow_fraction": 1e-7,
            "reproduction_relative_difference": 1e-6,
        },
        "qualification": "provisional numerical checks; independent CFD review and mesh independence not assessed",
    }


def rans_recipe() -> dict:
    recipe = internal_recipe()
    recipe.update(
        id="sst-straight-duct/1",
        compiler="venturi.rans/1",
        qualification="experimental; independent CFD review pending; not qualified for engineering acceptance",
    )
    recipe["applicability"].update(
        geometry="one straight smooth circular duct; two equal circular planar ports; one cylindrical wall",
        physics="steady incompressible Newtonian isothermal kOmegaSST with wall functions",
        maximum_outlets=1,
        minimum_port_reynolds_full_flow=4000,
        maximum_port_reynolds_full_flow=100000,
        minimum_length_diameters=5,
    )
    recipe["criteria"].update(
        wall_yplus_minimum=30, wall_yplus_maximum=300, turbulence_equation_residual=1e-5
    )
    recipe["boundaries"].update(
        turbulence="explicit I and length scale; k=1.5*(U*I)^2; omega=sqrt(k)/(0.09^0.25*L); kqRWallFunction/omegaWallFunction/nutkWallFunction"
    )
    return recipe


def recipe_for(study: StudySpec | InternalFlowStudy) -> dict:
    if isinstance(study, RANSFlowStudy):
        return rans_recipe()
    return internal_recipe() if isinstance(study, InternalFlowStudy) else pipe_recipe()


def freeze_study(study: StudySpec | InternalFlowStudy) -> dict:
    # Revalidate even models constructed through a bypass such as model_copy(update=...).
    study = parse_study(study.model_dump())
    recipe = recipe_for(study)
    return {
        "study": study.model_dump(),
        "study_hash": canonical_hash(study.model_dump()),
        "recipe": recipe,
        "recipe_hash": canonical_hash(recipe),
    }


def source_hashes() -> dict[str, str]:
    return {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
