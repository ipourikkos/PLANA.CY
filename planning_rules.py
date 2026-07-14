"""Structured Cyprus planning-rule layer for PLANA.CY.

The engine deliberately separates:
- deterministic calculations that can be made from DLS/known scenario inputs;
- conditional rules whose legal conditions can be expressed but not assumed; and
- discretionary powers that must never be treated as guaranteed capacity.

Authoritative calculation source: Ministerial Order 4/2026, effective 2026-05-11.
Interpretive source: ETEK concise planning-regulations guide, March 2026.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, Iterable, List


RULE_ENGINE_VERSION = "cy-planning-rules-4-2026-v2"
RULES_EFFECTIVE_DATE = "2026-05-11"

ORDER_4_2026_SOURCE: Dict[str, Any] = {
    "source_id": "ORDER_4_2026",
    "title": "Εντολή αρ. 4/2026 σύμφωνα με το άρθρο 6 του Νόμου",
    "publisher": "Υπουργείο Εσωτερικών, Κυπριακή Δημοκρατία",
    "document_type": "ministerial_order",
    "made_date": "2026-05-05",
    "effective_date": RULES_EFFECTIVE_DATE,
    "supersedes": "Εντολή 4/2024",
    "authority_role": "authoritative_rule_source",
}

ETEK_MARCH_2026_SOURCE: Dict[str, Any] = {
    "source_id": "ETEK_GUIDE_2026_03",
    "title": "Συνοπτικός Ηλεκτρονικός Οδηγός Πολεοδομικών Κανονισμών",
    "publisher": "Επιστημονικό Τεχνικό Επιμελητήριο Κύπρου (ΕΤΕΚ)",
    "edition": "March 2026",
    "document_type": "interpretive_guide",
    "scope": "Four major-city Local Plans: Nicosia, Limassol, Larnaca and Paphos",
    "scope_limited": True,
    "not_exhaustive": True,
    "tpo_review_status": "Content evaluated by and accepted by the Department of Town Planning and Housing, subject to official direction where interpretation differs or doubt remains.",
    "authority_role": "interpretive_context",
    "precedence_note": (
        "The guide is a concise, non-exhaustive interpretation aid with stated scope limited to the four major-city Local Plans. "
        "It predates the effective date of Order 4/2026 and frequently cites Order 4/2024. For topics governed by Order 4/2026, PLANA uses Order 4/2026."
    ),
}


def source_ref(
    source: Dict[str, Any],
    *,
    paragraph: str | None = None,
    page_number: int | None = None,
    section_title: str | None = None,
) -> Dict[str, Any]:
    ref = {
        "source_id": source["source_id"],
        "title": source["title"],
        "publisher": source.get("publisher"),
        "document_type": source.get("document_type"),
        "authority_role": source.get("authority_role"),
    }
    if paragraph:
        ref["paragraph"] = paragraph
    if page_number is not None:
        ref["page_number"] = page_number
    if section_title:
        ref["section_title"] = section_title
    return ref


# Core rules encoded for deterministic/conditional use. This is intentionally not a
# verbatim reproduction of the documents. Every item stores the legal effect and the
# inputs needed before PLANA may apply it.
RULE_CATALOG: List[Dict[str, Any]] = [
    {
        "rule_id": "order4_2026_current_source",
        "category": "source precedence",
        "title": "Order 4/2026 is the current calculation order",
        "effect_type": "source_precedence",
        "status": "always",
        "summary": "Order 4/2026 applies from 11 May 2026 and replaces Order 4/2024.",
        "required_inputs": [],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="6", page_number=36)],
    },
    {
        "rule_id": "calculation_net_parcel_area_basis",
        "category": "calculation basis",
        "title": "Density and coverage ultimately rely on clean/net parcel area",
        "effect_type": "baseline_dependency",
        "status": "always",
        "summary": (
            "The ETEK guide defines clean/net parcel area as the parcel area remaining after land commitments "
            "for development regulation, such as roads or other access and open public space. PLANA therefore "
            "treats the DLS registered parcel extent as a preliminary area basis until such commitments are confirmed."
        ),
        "required_inputs": ["net_parcel_area_m2_or_confirmed_land_commitments"],
        "source_refs": [
            source_ref(ETEK_MARCH_2026_SOURCE, page_number=6, section_title="Ορισμοί - καθαρό εμβαδόν τεμαχίου"),
            source_ref(ETEK_MARCH_2026_SOURCE, page_number=14, section_title="Οικοπεδοποίηση"),
        ],
    },
    {
        "rule_id": "density_basement_auxiliary_exclusion",
        "category": "density calculation",
        "title": "Qualifying basement support areas may be excluded from density",
        "effect_type": "exclude_from_density",
        "status": "conditional",
        "summary": (
            "A basement area may be omitted from the building-coefficient calculation when "
            "the Planning Authority considers its size, use and development type justified, "
            "it is functionally integral to the development, and it is used for the listed "
            "auxiliary/support purposes."
        ),
        "required_inputs": ["basement_area_m2", "basement_uses", "planning_authority_justification"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.1", page_number=3)],
    },
    {
        "rule_id": "density_pilotis_parking_exclusion",
        "category": "density calculation",
        "title": "Parking pilotis may be excluded from density",
        "effect_type": "exclude_from_density",
        "status": "conditional",
        "summary": (
            "A parking pilotis forming part of the main building may be excluded from density. "
            "Listed support spaces within it also remain excluded when their total area is about "
            "35% or less of the total pilotis area and the other conditions are met."
        ),
        "required_inputs": ["pilotis_total_area_m2", "pilotis_support_area_m2", "pilotis_use"],
        "formula": "support_area_ratio = pilotis_support_area_m2 / pilotis_total_area_m2; threshold ≈ 0.35",
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.7", page_number=8)],
    },
    {
        "rule_id": "density_ground_covered_parking_exclusion",
        "category": "density calculation",
        "title": "Qualifying ground-level covered parking may be excluded from density",
        "effect_type": "exclude_from_density",
        "status": "conditional",
        "summary": (
            "Ground-level covered vehicle parking that is an organic part of the main building "
            "and open on two sides may be excluded from density when the stated conditions are met. "
            "It still has to comply with the permitted coverage together with the main building."
        ),
        "required_inputs": ["covered_parking_area_m2", "covered_parking_open_sides", "covered_parking_design"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.9", page_number=10)],
    },
    {
        "rule_id": "density_parking_any_level_exclusion",
        "category": "density calculation",
        "title": "Parking serving the development may be excluded from density on any level",
        "effect_type": "exclude_from_density",
        "status": "conditional",
        "summary": (
            "Covered parking on any floor/level, or in a separate building on the same parcel, may "
            "be excluded from density when it serves the same development exclusively and forms an "
            "integral part of it; necessary accesses and listed support spaces are included in the rule."
        ),
        "required_inputs": ["parking_area_m2", "parking_serves_same_development", "parking_level"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.10", page_number=11)],
    },
    {
        "rule_id": "density_mechanical_floor_exclusion",
        "category": "density calculation",
        "title": "A qualifying mechanical floor may be excluded from density",
        "effect_type": "exclude_from_density",
        "status": "conditional",
        "summary": (
            "A covered floor with the clear height of a normal floor may be excluded from density when it is used exclusively "
            "for necessary electromechanical and hydraulic installations, including the stair serving it. More than one such "
            "floor requires supporting mechanical and alternative-space studies."
        ),
        "required_inputs": ["mechanical_floor_area_m2", "mechanical_floor_use", "mechanical_floor_count", "supporting_studies_if_multiple"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.12", page_number=12)],
    },
    {
        "rule_id": "density_attic_exclusion",
        "category": "density calculation",
        "title": "A qualifying auxiliary attic may be excluded from density",
        "effect_type": "exclude_from_density",
        "status": "conditional",
        "summary": (
            "An attic with average height 2.40 m may be excluded when its use is auxiliary to the main "
            "use, its area is not disproportionate and it integrates morphologically. If habitable rooms "
            "are created in the part above 2.40 m, the whole attic area counts in density."
        ),
        "required_inputs": ["attic_area_m2", "attic_average_height_m", "attic_use", "habitable_rooms_above_2_4m"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.14", page_number=12)],
    },
    {
        "rule_id": "density_balcony_veranda_allowance",
        "category": "density calculation",
        "title": "Balcony and covered-veranda allowance is calculated per floor",
        "effect_type": "partial_exclusion_from_density",
        "status": "conditional",
        "summary": (
            "Balconies and covered verandas may be excluded up to 25% of the remaining built area on "
            "the same floor, subject to the semi-open-character and use conditions. Excess covered-veranda "
            "area counts in density. The Planning Authority may allow up to 35% on selected floors with a "
            "corresponding reduction on other floors."
        ),
        "required_inputs": ["floor_built_area_m2", "balcony_and_covered_veranda_area_m2", "space_character_and_use"],
        "formula": "standard_exclusion_cap_m2 = 0.25 * remaining_built_area_same_floor",
        "source_refs": [
            source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.15", page_number=13),
            source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.15.2", page_number=13),
        ],
    },
    {
        "rule_id": "density_common_corridor_exclusion",
        "category": "density calculation",
        "title": "Part of a widened common corridor may be excluded from density",
        "effect_type": "partial_exclusion_from_density",
        "status": "conditional",
        "summary": (
            "For the described common corridor/lobby route, the width above 1.25 m and up to 1.50 m "
            "may be excluded from density. Width beyond 1.50 m does not increase the exclusion."
        ),
        "required_inputs": ["corridor_width_m", "corridor_length_m", "corridor_configuration"],
        "formula": "excluded_m2 = max(0, min(corridor_width_m, 1.50) - 1.25) * corridor_length_m",
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.17", page_number=14)],
    },
    {
        "rule_id": "density_roof_support_spaces",
        "category": "density calculation",
        "title": "Listed roof support spaces may be excluded from density",
        "effect_type": "exclude_from_density",
        "status": "conditional",
        "summary": (
            "Listed stair/lift, tank, ventilation, mechanical, storage, sanitary and reasonable roof-pool "
            "spaces may be excluded. In historic/traditional areas, settlement cores, special-character "
            "areas and historic centres, these spaces are capped at 30% of total roof area."
        ),
        "required_inputs": ["roof_area_m2", "roof_support_area_m2", "roof_support_uses", "historic_or_special_area"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.20", page_number=15)],
    },
    {
        "rule_id": "density_pergola_exclusion",
        "category": "density calculation",
        "title": "Space below qualifying pergolas is excluded from density",
        "effect_type": "exclude_from_density",
        "status": "conditional",
        "summary": (
            "Spaces below pergolas, including operable-louvre systems, sun-shading structures and open "
            "space frames, are listed as non-counting for density. For pergolas/canopies integrating PV "
            "panels, the rule states a 3.00 m distance from all parcel boundaries."
        ),
        "required_inputs": ["pergola_area_m2", "pergola_type", "integrates_pv", "pergola_boundary_distances_m"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.22", page_number=15)],
    },
    {
        "rule_id": "density_mezzanine_exclusion",
        "category": "density calculation",
        "title": "Non-residential mezzanine area may be excluded up to 50% of the ground-floor room",
        "effect_type": "partial_exclusion_from_density",
        "status": "conditional",
        "summary": (
            "For development other than residential development, mezzanine area may be excluded from density when it does not "
            "exceed 50% of the related ground-floor room. Area above the 50% threshold counts in density, subject to the special "
            "shop exception and other detailed conditions in the Order."
        ),
        "required_inputs": ["development_type", "ground_floor_room_area_m2", "mezzanine_area_m2", "mezzanine_use"],
        "formula": "standard_exclusion_cap_m2 = 0.50 * ground_floor_room_area_m2; residential development excluded",
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.24", page_number=16)],
    },
    {
        "rule_id": "density_entrance_lobby_exclusion",
        "category": "density calculation",
        "title": "A justified common entrance lobby may be excluded from density",
        "effect_type": "partial_exclusion_from_density",
        "status": "conditional",
        "summary": (
            "For most buildings, a common entrance/reception area that has a logical relationship to the "
            "building may be excluded up to 300 m² when justified by the Planning Authority; separate "
            "limits apply to specified hotel, healthcare and education/research uses."
        ),
        "required_inputs": ["development_type", "entrance_lobby_area_m2", "planning_authority_justification"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.25", page_number=16)],
    },
    {
        "rule_id": "density_staircase_treatment",
        "category": "density calculation",
        "title": "Staircase area has explicit floor-specific counting exceptions",
        "effect_type": "partial_exclusion_from_density",
        "status": "conditional",
        "summary": (
            "Staircase area generally counts in density, but Order 4/2026 lists specific exclusions, including the whole staircase "
            "to the first floor in buildings above two floors, subject to the under-stair use condition; roof/attic access in the stated "
            "cases; the final-floor stair when it terminates at that floor; specified pilotis and small private roof-garden stairs; and "
            "the rule that a staircase in a two-storey building is counted only once."
        ),
        "required_inputs": ["floor_count", "stair_configuration", "stair_area_by_level_m2", "under_stair_use", "roof_or_attic_uses"],
        "source_refs": [
            source_ref(ORDER_4_2026_SOURCE, paragraph="2.3.1-2.3.7", page_number=18),
            source_ref(ORDER_4_2026_SOURCE, paragraph="2.3.7-2.3.8", page_number=19),
        ],
    },
    {
        "rule_id": "density_fire_protection_exclusions",
        "category": "density calculation",
        "title": "Fire-safety spaces have separate density exclusions",
        "effect_type": "exclude_from_density",
        "status": "conditional",
        "summary": (
            "Balconies or covered verandas required for fire safety, specified additional fire-safety stairs, fire lobbies including "
            "an independent firefighting-lift lobby, wheelchair refuge area and required fire-safety landing area are separately listed "
            "as non-counting, subject to the Order's conditions."
        ),
        "required_inputs": ["fire_safety_design", "fire_authority_requirements", "qualifying_fire_safety_areas_m2"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.4", page_number=19)],
    },
    {
        "rule_id": "density_outdoor_parking_cover",
        "category": "density calculation",
        "title": "Qualifying lightweight covers over outdoor parking may be excluded from density",
        "effect_type": "exclude_from_density",
        "status": "conditional",
        "summary": (
            "For the listed public-facing or occupied development types, lightweight modern covers over outdoor vehicle parking may "
            "be excluded from density when amenity, the 3.00 m road-boundary distance and the maximum contact length at other boundaries "
            "are respected. Special-character areas and historic cores are excluded from this arrangement."
        ),
        "required_inputs": ["development_type", "outdoor_parking_cover_area_m2", "road_boundary_distance_m", "other_boundary_contact", "historic_or_special_area"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.5-2.5.1", page_number=19)],
    },
    {
        "rule_id": "density_civil_defence_shelter_incentive",
        "category": "density calculation",
        "title": "A qualifying civil-defence shelter can carry a 5% density incentive",
        "effect_type": "density_incentive",
        "status": "conditional",
        "summary": (
            "For a new apartment building with a basement, the Order requires a basement area identified by Civil Defence to be categorised "
            "as an emergency shelter. In those cases a 5% building-coefficient incentive is provided; when parcel area exceeds 1,000 m², "
            "the increase is calculated on a maximum area basis of 1,000 m²."
        ),
        "required_inputs": ["development_form", "has_basement", "civil_defence_shelter_required", "net_parcel_area_m2"],
        "formula": "indicative_additional_floor_area_m2 = 0.05 * min(net_parcel_area_m2, 1000)",
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.7-2.7.1", page_number=20)],
    },
    {
        "rule_id": "coverage_general_exclusions",
        "category": "coverage",
        "title": "Specified projections and lightweight elements are excluded from coverage",
        "effect_type": "exclude_from_coverage",
        "status": "conditional",
        "summary": (
            "The coverage calculation excludes the specified entrance canopies, non-walkable architectural "
            "projections, pergola/shading/open-space-frame areas, balconies or balcony parts up to 2.00 m "
            "from the building face, and PV-panel area, subject to the stated conditions."
        ),
        "required_inputs": ["design_elements_and_dimensions"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="3.1", page_number=21)],
    },
    {
        "rule_id": "coverage_uncovered_ground_terrace_fill",
        "category": "coverage",
        "title": "Raised uncovered ground terraces and fills can count in coverage",
        "effect_type": "partial_exclusion_from_coverage",
        "status": "conditional",
        "summary": (
            "Uncovered ground-floor terraces or fills higher than 1.50 m count in coverage, except for an area up to 30% of the building's "
            "ground-floor area. The excluded allowance may rise to 50% where justified by steep terrain and neighbouring amenity is protected; "
            "such raising must be at least 3.00 m from side boundaries."
        ),
        "required_inputs": ["raised_uncovered_terrace_or_fill_area_m2", "raised_height_m", "ground_floor_area_m2", "steep_terrain", "side_boundary_distance_m"],
        "formula": "standard_non_counting_cap_m2 = 0.30 * ground_floor_area_m2; steep-terrain discretionary cap = 0.50 * ground_floor_area_m2",
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="3.2", page_number=21)],
    },
    {
        "rule_id": "coverage_low_residential_zone_005",
        "category": "coverage",
        "title": "Low-coverage residential zones have a 0.05:1 covered-veranda/parking exclusion",
        "effect_type": "partial_exclusion_from_coverage",
        "status": "parcel_trigger",
        "summary": (
            "For development in a residential zone with permitted coverage of 0.35:1 or less, the Planning "
            "Authority does not count the portion of covered verandas and covered parking corresponding to "
            "up to 0.05:1 of parcel area in coverage, provided the area concerns those uses."
        ),
        "required_inputs": ["covered_veranda_and_covered_parking_area_m2"],
        "formula": "exclusion_cap_m2 = 0.05 * parcel_area_m2",
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="3.4", page_number=23)],
    },
    {
        "rule_id": "coverage_over_050_discretionary_reduction",
        "category": "coverage",
        "title": "Coverage above 0.50:1 may face a discretionary reduction on larger parcels",
        "effect_type": "discretionary_adjustment",
        "status": "parcel_trigger",
        "summary": (
            "For planning zones with permitted coverage above 0.50:1 and parcels of roughly more than 400 m², "
            "the Planning Authority may require coverage to be reduced to as low as 0.50:1 with a parallel "
            "adjustment to the maximum number of floors."
        ),
        "required_inputs": ["planning_authority_decision"],
        "source_refs": [
            source_ref(ORDER_4_2026_SOURCE, paragraph="3.3(ιγ)", page_number=22),
            source_ref(ORDER_4_2026_SOURCE, paragraph="4.1(ιγ)", page_number=26),
        ],
    },
    {
        "rule_id": "coverage_outdoor_parking_cover",
        "category": "coverage",
        "title": "Qualifying lightweight outdoor-parking covers may also be excluded from coverage",
        "effect_type": "exclude_from_coverage",
        "status": "conditional",
        "summary": (
            "For the listed development types, qualifying lightweight modern covers over outdoor vehicle parking may be excluded from coverage "
            "when the 3.00 m road-boundary distance, other-boundary contact limits and amenity conditions are met. Special-character areas and "
            "historic cores are excluded."
        ),
        "required_inputs": ["development_type", "outdoor_parking_cover_area_m2", "road_boundary_distance_m", "other_boundary_contact", "historic_or_special_area"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="3.9-3.9.1", page_number=24)],
    },
    {
        "rule_id": "height_sloping_land",
        "category": "height/floors",
        "title": "Steeply sloping land can affect floor and height treatment",
        "effect_type": "discretionary_adjustment",
        "status": "conditional",
        "summary": (
            "On steeply sloping land, the Planning Authority may allow part of a building to have one more "
            "floor than the area maximum when justified. The height measured from the lowest natural-ground "
            "point touching the building is limited to 1.80 m above the height calculated under the defined method."
        ),
        "required_inputs": ["terrain_profile", "building_section", "planning_authority_decision"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="4.2", page_number=26)],
    },
    {
        "rule_id": "height_office_commercial_mep_allowance",
        "category": "height/floors",
        "title": "Office, commercial and similar uses may receive 0.70 m extra height per floor for justified installations",
        "effect_type": "height_adjustment",
        "status": "conditional",
        "summary": (
            "For office, commercial or other developments where mechanical or other special installations justify it, building height is increased "
            "by 0.70 m per floor. The rule excludes settlement cores, special-character areas and historic centres."
        ),
        "required_inputs": ["development_type", "mechanical_or_special_installation_justification", "floor_count", "historic_or_special_area"],
        "formula": "height_allowance_m = 0.70 * floor_count",
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="4.1.1", page_number=26)],
    },
    {
        "rule_id": "height_roof_elements_35pct",
        "category": "height/floors",
        "title": "Reasonable roof elements may exceed zone height/floor limits",
        "effect_type": "height_floor_exception",
        "status": "conditional",
        "summary": (
            "Reasonable exceedance of maximum height and floor count is allowed for listed roof elements. "
            "The small covered roof spaces described in the rule are limited to 35% of total roof area; "
            "a roof swimming pool is listed with maximum height 1.40 m."
        ),
        "required_inputs": ["roof_area_m2", "roof_element_area_m2", "roof_element_types"],
        "formula": "roof_element_area_m2 <= 0.35 * roof_area_m2",
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="4.3", page_number=26)],
    },
    {
        "rule_id": "setbacks_baseline_external",
        "category": "setbacks",
        "title": "Baseline building setbacks come from the applicable development plan",
        "effect_type": "baseline_dependency",
        "status": "always",
        "summary": (
            "Order 4/2026 governs adjustments and special cases, but the base minimum building distances "
            "are those set by the General Policy Provisions of the applicable Local Plan or the Rural Policy Statement."
        ),
        "required_inputs": ["applicable_development_plan", "boundary_types", "baseline_setback_rules"],
        "source_refs": [source_ref(ETEK_MARCH_2026_SOURCE, page_number=39, section_title="Αποστάσεις οικοδομής από όρια τεμαχίου")],
    },
    {
        "rule_id": "setback_balcony_projection",
        "category": "setbacks",
        "title": "Balconies may project 1.50 m into specified public-boundary setbacks",
        "effect_type": "setback_projection",
        "status": "conditional",
        "summary": (
            "Balconies may project up to 1.50 m into the minimum distance between the main building and a "
            "public road, open public space or public pedestrian way, unless the Planning Authority considers "
            "this harmful to the road character or identity."
        ),
        "required_inputs": ["boundary_type", "balcony_projection_m"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.2", page_number=30)],
    },
    {
        "rule_id": "setback_entrance_canopy_projection",
        "category": "setbacks",
        "title": "Entrance canopies may project 1.20 m into minimum setbacks",
        "effect_type": "setback_projection",
        "status": "conditional",
        "summary": "Cantilevered entrance canopies may project up to 1.20 m into the applicable minimum building distance.",
        "required_inputs": ["canopy_projection_m", "applicable_baseline_setback_m"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.3", page_number=30)],
    },
    {
        "rule_id": "setback_architectural_projection",
        "category": "setbacks",
        "title": "Qualifying architectural projections may extend 1.20 m into setbacks",
        "effect_type": "setback_projection",
        "status": "conditional",
        "summary": (
            "Qualifying non-walkable architectural projections may extend into the minimum building distance "
            "when they improve the design/function, do not harm neighbouring amenity and project no more than 1.20 m."
        ),
        "required_inputs": ["projection_type", "projection_depth_m", "amenity_assessment"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.4", page_number=30)],
    },
    {
        "rule_id": "setback_fourth_floor_plus",
        "category": "setbacks",
        "title": "Fourth-floor-and-above setback distances can be adjusted",
        "effect_type": "discretionary_adjustment",
        "status": "conditional",
        "summary": (
            "The Planning Authority may reduce the distances specified for the fourth floor and above where "
            "those distances prevent full use of the parcel's building coefficient or create non-functional floors, "
            "provided neighbouring amenity and the surrounding built environment are not adversely affected."
        ),
        "required_inputs": ["proposed_floor_count", "applicable_development_plan", "building_envelope_test", "planning_authority_decision"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.8", page_number=31)],
    },
    {
        "rule_id": "setback_auxiliary_building",
        "category": "setbacks",
        "title": "Auxiliary buildings have explicit size, height and contact limits",
        "effect_type": "dimensional_limits",
        "status": "conditional",
        "summary": (
            "A qualifying auxiliary building may touch non-road parcel boundaries, but its area including relevant "
            "covered parking is limited to 25% of the main building area and 10% of net parcel area; common-boundary "
            "contact is generally limited to 35% of that boundary, maximum height is 3.50 m and minimum distance from "
            "the main building is 1.50 m, subject to the detailed exceptions."
        ),
        "required_inputs": ["auxiliary_building_area_m2", "main_building_area_m2", "net_parcel_area_m2", "boundary_contact_lengths_m", "auxiliary_height_m", "distance_to_main_building_m"],
        "formula": "aux_area <= min(0.25 * main_building_area_m2, 0.10 * net_parcel_area_m2)",
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.9", page_number=32)],
    },
    {
        "rule_id": "setback_pool",
        "category": "setbacks",
        "title": "Swimming pools may be as close as 1.50 m to non-road boundaries",
        "effect_type": "minimum_distance",
        "status": "conditional",
        "summary": "A swimming pool may be up to 1.50 m from parcel boundaries other than a road boundary.",
        "required_inputs": ["pool_boundary_distance_m", "boundary_type"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.10", page_number=34)],
    },
    {
        "rule_id": "setback_mechanical_rooms",
        "category": "setbacks",
        "title": "Mechanical/boiler rooms and similar elements have special boundary distances",
        "effect_type": "minimum_distance",
        "status": "conditional",
        "summary": (
            "Mechanical and boiler rooms for central heating, fuel tanks and grills/ovens may be as close as 1.80 m "
            "to non-road boundaries. A pool plant room of about 1.20 m height, underground water tank and underground "
            "water pump room may touch non-road boundaries, subject to the detailed provisions."
        ),
        "required_inputs": ["element_type", "element_height_m", "boundary_type", "boundary_distance_m"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.11", page_number=34)],
    },
    {
        "rule_id": "setback_pergola",
        "category": "setbacks",
        "title": "Permeable pergolas may sit within non-road setbacks",
        "effect_type": "setback_exception",
        "status": "conditional",
        "summary": (
            "Pergolas and similar lightweight permeable structures, including operable systems, may be built within "
            "minimum distances from boundaries other than road boundaries and are not counted in the maximum common-boundary "
            "contact length, provided neighbouring or area amenity is not affected."
        ),
        "required_inputs": ["pergola_type", "boundary_type", "amenity_assessment"],
        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.12", page_number=34)],
    },
    {
        "rule_id": "parking_residential_minimum",
        "category": "parking",
        "title": "Residential parking starts at one space per dwelling",
        "effect_type": "parking_requirement",
        "status": "conditional",
        "summary": (
            "The ETEK guide reproduces the residential parking standards under Order 1/2016: at least one parking "
            "space per dwelling, with visitor-space additions for larger horizontal housing schemes and apartment buildings, "
            "plus an additional space for qualifying dwellings above 150 m² outside specified dense/central areas."
        ),
        "required_inputs": ["residential_units", "development_form", "unit_usable_areas_m2", "location_density_context"],
        "source_refs": [source_ref(ETEK_MARCH_2026_SOURCE, page_number=45, section_title="Χώροι Στάθμευσης - Οικιστικές Αναπτύξεις"),
            source_ref(ETEK_MARCH_2026_SOURCE, page_number=46, section_title="Χώροι Στάθμευσης - Οικιστικές Αναπτύξεις"),
        ],
    },
]

RULES_BY_ID = {rule["rule_id"]: rule for rule in RULE_CATALOG}


def _normalise_text(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def _canonical_zone_code(value: Any) -> str:
    text = _normalise_text(value).replace(" ", "")
    greek_to_latin = str.maketrans({
        "κ": "k", "α": "a", "η": "h", "β": "b", "γ": "g", "δ": "d",
        "ε": "e", "ζ": "z", "θ": "th", "ι": "i", "λ": "l", "μ": "m",
        "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s",
        "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o",
    })
    return text.translate(greek_to_latin).upper()


def _is_residential_zone(zone_code: Any) -> bool:
    code = _canonical_zone_code(zone_code)
    return code.startswith("KA") or re.fullmatch(r"H\d+[A-Z]*", code) is not None


def _float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        return number
    except Exception:
        return None


def calculate_zoned_capacity(parcel_area_m2: Any, zones: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate baseline theoretical capacity without inventing missing zone overlaps."""
    area = _float(parcel_area_m2)
    zone_list = [dict(z) for z in zones or []]
    result: Dict[str, Any] = {
        "calculation_method": None,
        "parcel_area_basis_m2": round(area, 2) if area is not None else None,
        "area_basis_status": "registered_extent_not_net_area_confirmed",
        "calculation_authority_status": "preliminary_platform_calculation",
        "multi_zone_policy_status": None,
        "effective_density_percent": None,
        "effective_coverage_percent": None,
        "theoretical_max_floor_area_m2": None,
        "theoretical_max_ground_coverage_m2": None,
        "zone_overlap_total_percent": None,
        "zone_overlap_complete": None,
        "calculation_warnings": [],
    }
    if area is None or area <= 0 or not zone_list:
        result["calculation_warnings"].append("Parcel area or planning-zone data is missing.")
        return result

    if len(zone_list) == 1:
        zone = zone_list[0]
        density = _float(zone.get("density_percent"))
        coverage = _float(zone.get("coverage_percent"))
        result["calculation_method"] = "single_zone"
        result["effective_density_percent"] = round(density, 4) if density is not None else None
        result["effective_coverage_percent"] = round(coverage, 4) if coverage is not None else None
        if density is not None:
            result["theoretical_max_floor_area_m2"] = round(area * density / 100.0, 2)
        if coverage is not None:
            result["theoretical_max_ground_coverage_m2"] = round(area * coverage / 100.0, 2)
        return result

    overlaps = [_float(z.get("overlap_percent")) for z in zone_list]
    if any(value is None for value in overlaps):
        result["calculation_method"] = "multi_zone_incomplete"
        result["zone_overlap_complete"] = False
        result["calculation_warnings"].append(
            "Multiple planning zones were returned but at least one overlap percentage is missing; PLANA did not infer a weighted capacity."
        )
        return result

    overlap_total = sum(value or 0.0 for value in overlaps)
    result["zone_overlap_total_percent"] = round(overlap_total, 4)
    result["zone_overlap_complete"] = 99.0 <= overlap_total <= 101.0
    result["calculation_method"] = "weighted_zone_overlap"
    result["multi_zone_policy_status"] = "development_plan_applicability_not_confirmed"
    if not result["zone_overlap_complete"]:
        result["calculation_warnings"].append(
            f"Planning-zone overlaps total {overlap_total:.2f}% rather than approximately 100%; weighted capacity is withheld."
        )
        return result

    density_weighted = 0.0
    coverage_weighted = 0.0
    density_complete = True
    coverage_complete = True
    for zone, overlap in zip(zone_list, overlaps):
        weight = (overlap or 0.0) / overlap_total
        density = _float(zone.get("density_percent"))
        coverage = _float(zone.get("coverage_percent"))
        if density is None:
            density_complete = False
        else:
            density_weighted += density * weight
        if coverage is None:
            coverage_complete = False
        else:
            coverage_weighted += coverage * weight

    if density_complete:
        result["effective_density_percent"] = round(density_weighted, 4)
        result["theoretical_max_floor_area_m2"] = round(area * density_weighted / 100.0, 2)
    else:
        result["calculation_warnings"].append("A density coefficient is missing for one or more affected zones.")
    if coverage_complete:
        result["effective_coverage_percent"] = round(coverage_weighted, 4)
        result["theoretical_max_ground_coverage_m2"] = round(area * coverage_weighted / 100.0, 2)
    else:
        result["calculation_warnings"].append("A coverage coefficient is missing for one or more affected zones.")
    result["calculation_warnings"].append(
        "The weighted multi-zone result is a DLS-overlap mathematical calculation. Confirm that the applicable Development Plan and its General Policy Provisions permit the average-coefficient treatment before relying on it as a planning entitlement."
    )
    return result


def calculate_residential_parking(
    residential_units: int,
    development_form: str,
    unit_usable_areas_m2: Iterable[Any] | None = None,
    outside_urban_core_historic_or_dense_area: bool | None = None,
    etek_guide_scope_confirmed: bool | None = None,
) -> Dict[str, Any]:
    """Structured reproduction of the residential formulas shown in the ETEK guide (Order 1/2016 context)."""
    units = max(int(residential_units or 0), 0)
    form = _normalise_text(development_form).replace(" ", "_")
    base = units
    visitor = 0
    if form in {"horizontal", "horizontal_housing", "houses", "οριζοντια"} and units > 5:
        visitor = math.ceil(units / 6)
    elif form in {"vertical", "apartment", "apartment_building", "apartments", "πολυκατοικια"} and units > 9:
        visitor = math.ceil(units / 10)

    large_unit_extra = 0
    areas = [_float(v) for v in (unit_usable_areas_m2 or [])]
    if outside_urban_core_historic_or_dense_area is True and areas:
        large_unit_extra = sum(1 for area in areas[:units] if area is not None and area > 150.0)

    missing_location_context = outside_urban_core_historic_or_dense_area is None and bool(areas)
    warnings = [
        "The parking formula is currently encoded from the March 2026 ETEK guide's reproduction of Order 1/2016; the direct Order 1/2016 text is not embedded in this structured rule layer."
    ]
    if etek_guide_scope_confirmed is not True:
        warnings.append(
            "The ETEK guide states that its concise guide scope is limited to the four major-city Local Plans. Confirm the applicable Development Plan/source before treating this calculation as final."
        )
    if missing_location_context:
        warnings.append(
            "Location context is required to decide whether the extra parking space for residential units above 150 m² applies."
        )

    return {
        "base_spaces": base,
        "visitor_spaces": visitor,
        "large_unit_extra_spaces": large_unit_extra,
        "minimum_spaces": base + visitor + large_unit_extra,
        "formula_status": (
            "provisional_source_scope_or_context_required"
            if etek_guide_scope_confirmed is not True or missing_location_context
            else "complete_for_supplied_inputs"
        ),
        "source_status": "secondary_interpretive_reproduction_of_order_1_2016",
        "warnings": warnings,
        "source_refs": RULES_BY_ID["parking_residential_minimum"]["source_refs"],
    }


def calculate_civil_defence_shelter_incentive(net_parcel_area_m2: Any) -> Dict[str, Any]:
    """Calculate the 0.05 parcel-area incentive described in Order 4/2026 paragraph 2.7.1."""
    area = _float(net_parcel_area_m2)
    if area is None or area <= 0:
        return {
            "status": "net_parcel_area_required",
            "additional_floor_area_m2": None,
            "calculation_basis_m2": None,
            "source_refs": RULES_BY_ID["density_civil_defence_shelter_incentive"]["source_refs"],
        }
    basis = min(area, 1000.0)
    return {
        "status": "calculated_for_qualifying_shelter_scenario",
        "additional_floor_area_m2": round(0.05 * basis, 2),
        "calculation_basis_m2": round(basis, 2),
        "coefficient_increase": 0.05,
        "source_refs": RULES_BY_ID["density_civil_defence_shelter_incentive"]["source_refs"],
    }


def _rule_view(rule_id: str, **extra: Any) -> Dict[str, Any]:
    rule = RULES_BY_ID[rule_id]
    value = {
        "rule_id": rule["rule_id"],
        "category": rule["category"],
        "title": rule["title"],
        "effect_type": rule["effect_type"],
        "summary": rule["summary"],
        "required_inputs": list(rule.get("required_inputs") or []),
        "source_refs": list(rule.get("source_refs") or []),
    }
    if rule.get("formula"):
        value["formula"] = rule["formula"]
    value.update(extra)
    return value


def evaluate_parcel_rules(
    parcel_details: Dict[str, Any],
    scenario: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Evaluate the structured rule layer against known parcel/scenario facts."""
    scenario = scenario or {}
    parcel = parcel_details.get("parcel") or {}
    zones = parcel_details.get("planning_zones") or []
    capacity = calculate_zoned_capacity(parcel.get("parcel_extent_m2"), zones)
    parcel_area = _float(parcel.get("parcel_extent_m2"))

    applied_rules: List[Dict[str, Any]] = [
        _rule_view(
            "order4_2026_current_source",
            outcome="PLANA rule calculations use Order 4/2026 for covered topics; Order 4/2024 is treated as superseded from 11 May 2026.",
            applicability_status="applied_source_precedence",
        ),
        _rule_view(
            "calculation_net_parcel_area_basis",
            outcome=(
                f"The current baseline uses the DLS registered parcel extent ({parcel_area:,.2f} m²) because a confirmed clean/net development area is not yet available."
                if parcel_area is not None
                else "A confirmed clean/net development area is not available."
            ),
            applicability_status="calculation_basis_not_fully_confirmed",
        ),
    ]

    if capacity["calculation_method"] == "weighted_zone_overlap":
        applied_rules.append({
            "rule_id": "local_plan_multi_zone_weighting",
            "category": "density calculation",
            "title": "Multi-zone coefficients were mathematically weighted by DLS overlap shares",
            "effect_type": "provisional_deterministic_calculation",
            "summary": (
                "PLANA calculated an effective density and coverage from the area share of each affected DLS zone instead of selecting one zone coefficient. "
                "The ETEK guide illustrates this average-coefficient method for the Local Plan context and refers to the Local Plan appendices."
            ),
            "outcome": (
                f"Provisional effective density {capacity['effective_density_percent']:.2f}% and effective coverage "
                f"{capacity['effective_coverage_percent']:.2f}% across {len(zones)} zones. Confirm the applicable Development Plan before treating this as entitlement."
            ) if capacity.get("effective_density_percent") is not None and capacity.get("effective_coverage_percent") is not None else "Weighted calculation could not be completed.",
            "required_inputs": ["applicable_development_plan"],
            "applicability_status": "mathematical_result_policy_applicability_unconfirmed",
            "provisional": True,
            "source_refs": [source_ref(ETEK_MARCH_2026_SOURCE, page_number=16, section_title="Μέσος Συντελεστής Δόμησης, Μέσο Ποσοστό Κάλυψης")],
        })

    conditional_rules: List[Dict[str, Any]] = []
    triggered_rules: List[Dict[str, Any]] = []

    # Parcel-triggered coverage rules.
    low_coverage_zones = [
        zone for zone in zones
        if _is_residential_zone(zone.get("zone"))
        and (_float(zone.get("coverage_percent")) is not None)
        and (_float(zone.get("coverage_percent")) or 0) <= 35.0
    ]
    if low_coverage_zones and parcel_area is not None:
        cap = round(parcel_area * 0.05, 2)
        triggered_rules.append(_rule_view(
            "coverage_low_residential_zone_005",
            outcome=(
                f"The parcel has a residential-zone coverage coefficient of 35% or less. The rule's maximum "
                f"covered-veranda/covered-parking coverage exclusion cap is {cap:,.2f} m², but the actual excluded "
                "area depends on the proposed design and qualifying uses."
            ),
            calculated_values={"coverage_exclusion_cap_m2": cap},
            applicability_status="parcel_numeric_trigger_design_inputs_required",
        ))

    higher_coverage_zones = [
        zone for zone in zones
        if (_float(zone.get("coverage_percent")) or 0) > 50.0
    ]
    if higher_coverage_zones and parcel_area is not None and parcel_area > 400.0:
        triggered_rules.append(_rule_view(
            "coverage_over_050_discretionary_reduction",
            outcome=(
                "The parcel meets the numeric parts of the rule (coverage above 50% and parcel area above roughly 400 m²). "
                "The Order describes the affected zones as areas with a continuous building system; that context is not established from the current DLS payload. "
                "Treat this as a review signal, not an automatic reduction."
            ),
            discretion=True,
            applicability_status="partial_match_continuous_building_system_not_confirmed",
        ))

    max_zone_floors = max(
        [_float(zone.get("max_floors")) or 0 for zone in zones] or [0]
    )
    if max_zone_floors >= 4:
        triggered_rules.append(_rule_view(
            "setback_fourth_floor_plus",
            outcome=(
                f"At least one affected zone permits {int(max_zone_floors)} floors. Paragraph 5.8 becomes potentially relevant only if a proposal actually uses the fourth floor or above "
                "and the applicable General Policy Provision distances prevent full coefficient use or create non-functional floors. Any distance adjustment remains discretionary."
            ),
            discretion=True,
            applicability_status="review_signal_proposal_and_envelope_test_required",
        ))

    # Rules that Priority 3/design scenarios can calculate once assumptions exist.
    design_rule_ids = [
        "density_basement_auxiliary_exclusion",
        "density_pilotis_parking_exclusion",
        "density_ground_covered_parking_exclusion",
        "density_parking_any_level_exclusion",
        "density_mechanical_floor_exclusion",
        "density_attic_exclusion",
        "density_balcony_veranda_allowance",
        "density_common_corridor_exclusion",
        "density_roof_support_spaces",
        "density_pergola_exclusion",
        "density_mezzanine_exclusion",
        "density_entrance_lobby_exclusion",
        "density_staircase_treatment",
        "density_fire_protection_exclusions",
        "density_outdoor_parking_cover",
        "density_civil_defence_shelter_incentive",
        "coverage_general_exclusions",
        "coverage_uncovered_ground_terrace_fill",
        "coverage_outdoor_parking_cover",
        "height_sloping_land",
        "height_office_commercial_mep_allowance",
        "height_roof_elements_35pct",
        "setbacks_baseline_external",
        "setback_balcony_projection",
        "setback_entrance_canopy_projection",
        "setback_architectural_projection",
        "setback_auxiliary_building",
        "setback_pool",
        "setback_mechanical_rooms",
        "setback_pergola",
        "parking_residential_minimum",
    ]
    conditional_rules.extend(_rule_view(rule_id, outcome="Requires additional development/design inputs before calculation or application.") for rule_id in design_rule_ids)

    scenario_calculations: Dict[str, Any] = {}
    development_type = _normalise_text(scenario.get("development_type"))
    development_form = _normalise_text(scenario.get("development_form"))
    if development_type in {"residential", "οικιστικη", "housing"} and scenario.get("residential_units") is not None:
        scenario_calculations["residential_parking"] = calculate_residential_parking(
            int(scenario.get("residential_units") or 0),
            str(scenario.get("development_form") or "apartment_building"),
            scenario.get("unit_usable_areas_m2") or [],
            scenario.get("outside_urban_core_historic_or_dense_area"),
            scenario.get("etek_guide_scope_confirmed"),
        )

    apartment_forms = {"apartment", "apartment_building", "apartments", "πολυκατοικια"}
    if (
        development_type in {"residential", "οικιστικη", "housing"}
        and development_form in apartment_forms
        and scenario.get("has_basement") is True
        and scenario.get("civil_defence_shelter_required") is True
    ):
        scenario_calculations["civil_defence_shelter_density_incentive"] = calculate_civil_defence_shelter_incentive(
            scenario.get("net_parcel_area_m2")
        )

    checks = [
        "Confirm the clean/net parcel area after any road, access, public-open-space or other land commitments before treating the DLS parcel extent as the final density/coverage calculation basis.",
        "Identify the applicable Development Plan and its General Policy Provisions before calculating baseline building setbacks; Order 4/2026 mainly governs adjustments and special cases to those distances.",
        "Confirm the proposed development/use type before applying use-specific density exclusions, coverage exclusions, floor/height adjustments or parking standards.",
        "The March 2026 ETEK guide is concise, non-exhaustive and expressly scoped to the four major-city Local Plans; guide-only formulas are marked provisional until plan/source applicability is confirmed.",
    ]
    if capacity.get("calculation_warnings"):
        checks.extend(capacity["calculation_warnings"])
    if any(parcel.get(flag) for flag in ("is_preserved", "is_ancient")):
        checks.append("The DLS preservation/ancient-property flag requires parcel-specific policy and authority review before relying on generic dimensional rules.")

    return {
        "status": "complete",
        "rule_engine_version": RULE_ENGINE_VERSION,
        "rules_effective_date": RULES_EFFECTIVE_DATE,
        "authoritative_source": ORDER_4_2026_SOURCE,
        "interpretive_source": ETEK_MARCH_2026_SOURCE,
        "source_registry": [ORDER_4_2026_SOURCE, ETEK_MARCH_2026_SOURCE],
        "source_precedence": (
            "Order 4/2026 is authoritative for its covered calculation/setback topics and supersedes Order 4/2024 from 11 May 2026. "
            "The March 2026 ETEK guide is used for interpretation and diagrams, not to override Order 4/2026."
        ),
        "base_capacity": capacity,
        "applied_rules": applied_rules,
        "triggered_rules": triggered_rules,
        "conditional_rules": conditional_rules,
        "scenario_calculations": scenario_calculations,
        "checks_before_reliance": checks[:8],
        "catalog_rule_count": len(RULE_CATALOG),
        "application_summary": {
            "applied_rule_count": len(applied_rules),
            "parcel_trigger_count": len(triggered_rules),
            "design_dependent_rule_count": len(conditional_rules),
            "scenario_calculation_count": len(scenario_calculations),
        },
    }


def compact_rule_context(rule_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Return the compact structured context passed to the RAG analyst."""
    return {
        "rule_engine_version": rule_analysis.get("rule_engine_version"),
        "rules_effective_date": rule_analysis.get("rules_effective_date"),
        "source_precedence": rule_analysis.get("source_precedence"),
        "base_capacity": rule_analysis.get("base_capacity"),
        "applied_rules": rule_analysis.get("applied_rules"),
        "triggered_rules": rule_analysis.get("triggered_rules"),
        "checks_before_reliance": rule_analysis.get("checks_before_reliance"),
    }
