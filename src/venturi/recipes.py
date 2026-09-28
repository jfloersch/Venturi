"""Versioned scientific contract; thresholds are never supplied by a caller."""

from pathlib import Path

from .models import StudySpec, canonical_hash, file_hash


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


def freeze_study(study: StudySpec) -> dict:
    # Revalidate even models constructed through a bypass such as model_copy(update=...).
    study = StudySpec.model_validate(study.model_dump())
    recipe = pipe_recipe()
    return {
        "study": study.model_dump(),
        "study_hash": canonical_hash(study.model_dump()),
        "recipe": recipe,
        "recipe_hash": canonical_hash(recipe),
    }


def source_hashes() -> dict[str, str]:
    return {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
