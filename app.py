from __future__ import annotations
# ============================================================
# PLANA.CY COMPACT BUILD
# Embedded internal engines keep the GitHub project to 3 files.
# Run the web app with: uvicorn app:app --host 0.0.0.0 --port 8000
# Run market ingestion with: python app.py market-ingest [options]
# ============================================================

import sys as _plana_sys
import types as _plana_types

def _plana_install_embedded_module(name: str, source: str):
    module = _plana_types.ModuleType(name)
    module.__file__ = f"<embedded:{name}>"
    module.__package__ = ""
    _plana_sys.modules[name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module

# ---- embedded planning_rules.py ----
_plana_install_embedded_module('planning_rules', '"""Structured Cyprus planning-rule layer for PLANA.CY.\n\nThe engine deliberately separates:\n- deterministic calculations that can be made from DLS/known scenario inputs;\n- conditional rules whose legal conditions can be expressed but not assumed; and\n- discretionary powers that must never be treated as guaranteed capacity.\n\nAuthoritative calculation source: Ministerial Order 4/2026, effective 2026-05-11.\nInterpretive source: ETEK concise planning-regulations guide, March 2026.\n"""\n\nfrom __future__ import annotations\n\nimport math\nimport re\nimport unicodedata\nfrom typing import Any, Dict, Iterable, List\n\n\nRULE_ENGINE_VERSION = "cy-planning-rules-4-2026-v2"\nRULES_EFFECTIVE_DATE = "2026-05-11"\n\nORDER_4_2026_SOURCE: Dict[str, Any] = {\n    "source_id": "ORDER_4_2026",\n    "title": "Εντολή αρ. 4/2026 σύμφωνα με το άρθρο 6 του Νόμου",\n    "publisher": "Υπουργείο Εσωτερικών, Κυπριακή Δημοκρατία",\n    "document_type": "ministerial_order",\n    "made_date": "2026-05-05",\n    "effective_date": RULES_EFFECTIVE_DATE,\n    "supersedes": "Εντολή 4/2024",\n    "authority_role": "authoritative_rule_source",\n}\n\nETEK_MARCH_2026_SOURCE: Dict[str, Any] = {\n    "source_id": "ETEK_GUIDE_2026_03",\n    "title": "Συνοπτικός Ηλεκτρονικός Οδηγός Πολεοδομικών Κανονισμών",\n    "publisher": "Επιστημονικό Τεχνικό Επιμελητήριο Κύπρου (ΕΤΕΚ)",\n    "edition": "March 2026",\n    "document_type": "interpretive_guide",\n    "scope": "Four major-city Local Plans: Nicosia, Limassol, Larnaca and Paphos",\n    "scope_limited": True,\n    "not_exhaustive": True,\n    "tpo_review_status": "Content evaluated by and accepted by the Department of Town Planning and Housing, subject to official direction where interpretation differs or doubt remains.",\n    "authority_role": "interpretive_context",\n    "precedence_note": (\n        "The guide is a concise, non-exhaustive interpretation aid with stated scope limited to the four major-city Local Plans. "\n        "It predates the effective date of Order 4/2026 and frequently cites Order 4/2024. For topics governed by Order 4/2026, PLANA uses Order 4/2026."\n    ),\n}\n\n\ndef source_ref(\n    source: Dict[str, Any],\n    *,\n    paragraph: str | None = None,\n    page_number: int | None = None,\n    section_title: str | None = None,\n) -> Dict[str, Any]:\n    ref = {\n        "source_id": source["source_id"],\n        "title": source["title"],\n        "publisher": source.get("publisher"),\n        "document_type": source.get("document_type"),\n        "authority_role": source.get("authority_role"),\n    }\n    if paragraph:\n        ref["paragraph"] = paragraph\n    if page_number is not None:\n        ref["page_number"] = page_number\n    if section_title:\n        ref["section_title"] = section_title\n    return ref\n\n\n# Core rules encoded for deterministic/conditional use. This is intentionally not a\n# verbatim reproduction of the documents. Every item stores the legal effect and the\n# inputs needed before PLANA may apply it.\nRULE_CATALOG: List[Dict[str, Any]] = [\n    {\n        "rule_id": "order4_2026_current_source",\n        "category": "source precedence",\n        "title": "Order 4/2026 is the current calculation order",\n        "effect_type": "source_precedence",\n        "status": "always",\n        "summary": "Order 4/2026 applies from 11 May 2026 and replaces Order 4/2024.",\n        "required_inputs": [],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="6", page_number=36)],\n    },\n    {\n        "rule_id": "calculation_net_parcel_area_basis",\n        "category": "calculation basis",\n        "title": "Density and coverage ultimately rely on clean/net parcel area",\n        "effect_type": "baseline_dependency",\n        "status": "always",\n        "summary": (\n            "The ETEK guide defines clean/net parcel area as the parcel area remaining after land commitments "\n            "for development regulation, such as roads or other access and open public space. PLANA therefore "\n            "treats the DLS registered parcel extent as a preliminary area basis until such commitments are confirmed."\n        ),\n        "required_inputs": ["net_parcel_area_m2_or_confirmed_land_commitments"],\n        "source_refs": [\n            source_ref(ETEK_MARCH_2026_SOURCE, page_number=6, section_title="Ορισμοί - καθαρό εμβαδόν τεμαχίου"),\n            source_ref(ETEK_MARCH_2026_SOURCE, page_number=14, section_title="Οικοπεδοποίηση"),\n        ],\n    },\n    {\n        "rule_id": "density_basement_auxiliary_exclusion",\n        "category": "density calculation",\n        "title": "Qualifying basement support areas may be excluded from density",\n        "effect_type": "exclude_from_density",\n        "status": "conditional",\n        "summary": (\n            "A basement area may be omitted from the building-coefficient calculation when "\n            "the Planning Authority considers its size, use and development type justified, "\n            "it is functionally integral to the development, and it is used for the listed "\n            "auxiliary/support purposes."\n        ),\n        "required_inputs": ["basement_area_m2", "basement_uses", "planning_authority_justification"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.1", page_number=3)],\n    },\n    {\n        "rule_id": "density_pilotis_parking_exclusion",\n        "category": "density calculation",\n        "title": "Parking pilotis may be excluded from density",\n        "effect_type": "exclude_from_density",\n        "status": "conditional",\n        "summary": (\n            "A parking pilotis forming part of the main building may be excluded from density. "\n            "Listed support spaces within it also remain excluded when their total area is about "\n            "35% or less of the total pilotis area and the other conditions are met."\n        ),\n        "required_inputs": ["pilotis_total_area_m2", "pilotis_support_area_m2", "pilotis_use"],\n        "formula": "support_area_ratio = pilotis_support_area_m2 / pilotis_total_area_m2; threshold ≈ 0.35",\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.7", page_number=8)],\n    },\n    {\n        "rule_id": "density_ground_covered_parking_exclusion",\n        "category": "density calculation",\n        "title": "Qualifying ground-level covered parking may be excluded from density",\n        "effect_type": "exclude_from_density",\n        "status": "conditional",\n        "summary": (\n            "Ground-level covered vehicle parking that is an organic part of the main building "\n            "and open on two sides may be excluded from density when the stated conditions are met. "\n            "It still has to comply with the permitted coverage together with the main building."\n        ),\n        "required_inputs": ["covered_parking_area_m2", "covered_parking_open_sides", "covered_parking_design"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.9", page_number=10)],\n    },\n    {\n        "rule_id": "density_parking_any_level_exclusion",\n        "category": "density calculation",\n        "title": "Parking serving the development may be excluded from density on any level",\n        "effect_type": "exclude_from_density",\n        "status": "conditional",\n        "summary": (\n            "Covered parking on any floor/level, or in a separate building on the same parcel, may "\n            "be excluded from density when it serves the same development exclusively and forms an "\n            "integral part of it; necessary accesses and listed support spaces are included in the rule."\n        ),\n        "required_inputs": ["parking_area_m2", "parking_serves_same_development", "parking_level"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.10", page_number=11)],\n    },\n    {\n        "rule_id": "density_mechanical_floor_exclusion",\n        "category": "density calculation",\n        "title": "A qualifying mechanical floor may be excluded from density",\n        "effect_type": "exclude_from_density",\n        "status": "conditional",\n        "summary": (\n            "A covered floor with the clear height of a normal floor may be excluded from density when it is used exclusively "\n            "for necessary electromechanical and hydraulic installations, including the stair serving it. More than one such "\n            "floor requires supporting mechanical and alternative-space studies."\n        ),\n        "required_inputs": ["mechanical_floor_area_m2", "mechanical_floor_use", "mechanical_floor_count", "supporting_studies_if_multiple"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.12", page_number=12)],\n    },\n    {\n        "rule_id": "density_attic_exclusion",\n        "category": "density calculation",\n        "title": "A qualifying auxiliary attic may be excluded from density",\n        "effect_type": "exclude_from_density",\n        "status": "conditional",\n        "summary": (\n            "An attic with average height 2.40 m may be excluded when its use is auxiliary to the main "\n            "use, its area is not disproportionate and it integrates morphologically. If habitable rooms "\n            "are created in the part above 2.40 m, the whole attic area counts in density."\n        ),\n        "required_inputs": ["attic_area_m2", "attic_average_height_m", "attic_use", "habitable_rooms_above_2_4m"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.14", page_number=12)],\n    },\n    {\n        "rule_id": "density_balcony_veranda_allowance",\n        "category": "density calculation",\n        "title": "Balcony and covered-veranda allowance is calculated per floor",\n        "effect_type": "partial_exclusion_from_density",\n        "status": "conditional",\n        "summary": (\n            "Balconies and covered verandas may be excluded up to 25% of the remaining built area on "\n            "the same floor, subject to the semi-open-character and use conditions. Excess covered-veranda "\n            "area counts in density. The Planning Authority may allow up to 35% on selected floors with a "\n            "corresponding reduction on other floors."\n        ),\n        "required_inputs": ["floor_built_area_m2", "balcony_and_covered_veranda_area_m2", "space_character_and_use"],\n        "formula": "standard_exclusion_cap_m2 = 0.25 * remaining_built_area_same_floor",\n        "source_refs": [\n            source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.15", page_number=13),\n            source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.15.2", page_number=13),\n        ],\n    },\n    {\n        "rule_id": "density_common_corridor_exclusion",\n        "category": "density calculation",\n        "title": "Part of a widened common corridor may be excluded from density",\n        "effect_type": "partial_exclusion_from_density",\n        "status": "conditional",\n        "summary": (\n            "For the described common corridor/lobby route, the width above 1.25 m and up to 1.50 m "\n            "may be excluded from density. Width beyond 1.50 m does not increase the exclusion."\n        ),\n        "required_inputs": ["corridor_width_m", "corridor_length_m", "corridor_configuration"],\n        "formula": "excluded_m2 = max(0, min(corridor_width_m, 1.50) - 1.25) * corridor_length_m",\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.17", page_number=14)],\n    },\n    {\n        "rule_id": "density_roof_support_spaces",\n        "category": "density calculation",\n        "title": "Listed roof support spaces may be excluded from density",\n        "effect_type": "exclude_from_density",\n        "status": "conditional",\n        "summary": (\n            "Listed stair/lift, tank, ventilation, mechanical, storage, sanitary and reasonable roof-pool "\n            "spaces may be excluded. In historic/traditional areas, settlement cores, special-character "\n            "areas and historic centres, these spaces are capped at 30% of total roof area."\n        ),\n        "required_inputs": ["roof_area_m2", "roof_support_area_m2", "roof_support_uses", "historic_or_special_area"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.20", page_number=15)],\n    },\n    {\n        "rule_id": "density_pergola_exclusion",\n        "category": "density calculation",\n        "title": "Space below qualifying pergolas is excluded from density",\n        "effect_type": "exclude_from_density",\n        "status": "conditional",\n        "summary": (\n            "Spaces below pergolas, including operable-louvre systems, sun-shading structures and open "\n            "space frames, are listed as non-counting for density. For pergolas/canopies integrating PV "\n            "panels, the rule states a 3.00 m distance from all parcel boundaries."\n        ),\n        "required_inputs": ["pergola_area_m2", "pergola_type", "integrates_pv", "pergola_boundary_distances_m"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.22", page_number=15)],\n    },\n    {\n        "rule_id": "density_mezzanine_exclusion",\n        "category": "density calculation",\n        "title": "Non-residential mezzanine area may be excluded up to 50% of the ground-floor room",\n        "effect_type": "partial_exclusion_from_density",\n        "status": "conditional",\n        "summary": (\n            "For development other than residential development, mezzanine area may be excluded from density when it does not "\n            "exceed 50% of the related ground-floor room. Area above the 50% threshold counts in density, subject to the special "\n            "shop exception and other detailed conditions in the Order."\n        ),\n        "required_inputs": ["development_type", "ground_floor_room_area_m2", "mezzanine_area_m2", "mezzanine_use"],\n        "formula": "standard_exclusion_cap_m2 = 0.50 * ground_floor_room_area_m2; residential development excluded",\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.24", page_number=16)],\n    },\n    {\n        "rule_id": "density_entrance_lobby_exclusion",\n        "category": "density calculation",\n        "title": "A justified common entrance lobby may be excluded from density",\n        "effect_type": "partial_exclusion_from_density",\n        "status": "conditional",\n        "summary": (\n            "For most buildings, a common entrance/reception area that has a logical relationship to the "\n            "building may be excluded up to 300 m² when justified by the Planning Authority; separate "\n            "limits apply to specified hotel, healthcare and education/research uses."\n        ),\n        "required_inputs": ["development_type", "entrance_lobby_area_m2", "planning_authority_justification"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.2.25", page_number=16)],\n    },\n    {\n        "rule_id": "density_staircase_treatment",\n        "category": "density calculation",\n        "title": "Staircase area has explicit floor-specific counting exceptions",\n        "effect_type": "partial_exclusion_from_density",\n        "status": "conditional",\n        "summary": (\n            "Staircase area generally counts in density, but Order 4/2026 lists specific exclusions, including the whole staircase "\n            "to the first floor in buildings above two floors, subject to the under-stair use condition; roof/attic access in the stated "\n            "cases; the final-floor stair when it terminates at that floor; specified pilotis and small private roof-garden stairs; and "\n            "the rule that a staircase in a two-storey building is counted only once."\n        ),\n        "required_inputs": ["floor_count", "stair_configuration", "stair_area_by_level_m2", "under_stair_use", "roof_or_attic_uses"],\n        "source_refs": [\n            source_ref(ORDER_4_2026_SOURCE, paragraph="2.3.1-2.3.7", page_number=18),\n            source_ref(ORDER_4_2026_SOURCE, paragraph="2.3.7-2.3.8", page_number=19),\n        ],\n    },\n    {\n        "rule_id": "density_fire_protection_exclusions",\n        "category": "density calculation",\n        "title": "Fire-safety spaces have separate density exclusions",\n        "effect_type": "exclude_from_density",\n        "status": "conditional",\n        "summary": (\n            "Balconies or covered verandas required for fire safety, specified additional fire-safety stairs, fire lobbies including "\n            "an independent firefighting-lift lobby, wheelchair refuge area and required fire-safety landing area are separately listed "\n            "as non-counting, subject to the Order\'s conditions."\n        ),\n        "required_inputs": ["fire_safety_design", "fire_authority_requirements", "qualifying_fire_safety_areas_m2"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.4", page_number=19)],\n    },\n    {\n        "rule_id": "density_outdoor_parking_cover",\n        "category": "density calculation",\n        "title": "Qualifying lightweight covers over outdoor parking may be excluded from density",\n        "effect_type": "exclude_from_density",\n        "status": "conditional",\n        "summary": (\n            "For the listed public-facing or occupied development types, lightweight modern covers over outdoor vehicle parking may "\n            "be excluded from density when amenity, the 3.00 m road-boundary distance and the maximum contact length at other boundaries "\n            "are respected. Special-character areas and historic cores are excluded from this arrangement."\n        ),\n        "required_inputs": ["development_type", "outdoor_parking_cover_area_m2", "road_boundary_distance_m", "other_boundary_contact", "historic_or_special_area"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.5-2.5.1", page_number=19)],\n    },\n    {\n        "rule_id": "density_civil_defence_shelter_incentive",\n        "category": "density calculation",\n        "title": "A qualifying civil-defence shelter can carry a 5% density incentive",\n        "effect_type": "density_incentive",\n        "status": "conditional",\n        "summary": (\n            "For a new apartment building with a basement, the Order requires a basement area identified by Civil Defence to be categorised "\n            "as an emergency shelter. In those cases a 5% building-coefficient incentive is provided; when parcel area exceeds 1,000 m², "\n            "the increase is calculated on a maximum area basis of 1,000 m²."\n        ),\n        "required_inputs": ["development_form", "has_basement", "civil_defence_shelter_required", "net_parcel_area_m2"],\n        "formula": "indicative_additional_floor_area_m2 = 0.05 * min(net_parcel_area_m2, 1000)",\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="2.7-2.7.1", page_number=20)],\n    },\n    {\n        "rule_id": "coverage_general_exclusions",\n        "category": "coverage",\n        "title": "Specified projections and lightweight elements are excluded from coverage",\n        "effect_type": "exclude_from_coverage",\n        "status": "conditional",\n        "summary": (\n            "The coverage calculation excludes the specified entrance canopies, non-walkable architectural "\n            "projections, pergola/shading/open-space-frame areas, balconies or balcony parts up to 2.00 m "\n            "from the building face, and PV-panel area, subject to the stated conditions."\n        ),\n        "required_inputs": ["design_elements_and_dimensions"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="3.1", page_number=21)],\n    },\n    {\n        "rule_id": "coverage_uncovered_ground_terrace_fill",\n        "category": "coverage",\n        "title": "Raised uncovered ground terraces and fills can count in coverage",\n        "effect_type": "partial_exclusion_from_coverage",\n        "status": "conditional",\n        "summary": (\n            "Uncovered ground-floor terraces or fills higher than 1.50 m count in coverage, except for an area up to 30% of the building\'s "\n            "ground-floor area. The excluded allowance may rise to 50% where justified by steep terrain and neighbouring amenity is protected; "\n            "such raising must be at least 3.00 m from side boundaries."\n        ),\n        "required_inputs": ["raised_uncovered_terrace_or_fill_area_m2", "raised_height_m", "ground_floor_area_m2", "steep_terrain", "side_boundary_distance_m"],\n        "formula": "standard_non_counting_cap_m2 = 0.30 * ground_floor_area_m2; steep-terrain discretionary cap = 0.50 * ground_floor_area_m2",\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="3.2", page_number=21)],\n    },\n    {\n        "rule_id": "coverage_low_residential_zone_005",\n        "category": "coverage",\n        "title": "Low-coverage residential zones have a 0.05:1 covered-veranda/parking exclusion",\n        "effect_type": "partial_exclusion_from_coverage",\n        "status": "parcel_trigger",\n        "summary": (\n            "For development in a residential zone with permitted coverage of 0.35:1 or less, the Planning "\n            "Authority does not count the portion of covered verandas and covered parking corresponding to "\n            "up to 0.05:1 of parcel area in coverage, provided the area concerns those uses."\n        ),\n        "required_inputs": ["covered_veranda_and_covered_parking_area_m2"],\n        "formula": "exclusion_cap_m2 = 0.05 * parcel_area_m2",\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="3.4", page_number=23)],\n    },\n    {\n        "rule_id": "coverage_over_050_discretionary_reduction",\n        "category": "coverage",\n        "title": "Coverage above 0.50:1 may face a discretionary reduction on larger parcels",\n        "effect_type": "discretionary_adjustment",\n        "status": "parcel_trigger",\n        "summary": (\n            "For planning zones with permitted coverage above 0.50:1 and parcels of roughly more than 400 m², "\n            "the Planning Authority may require coverage to be reduced to as low as 0.50:1 with a parallel "\n            "adjustment to the maximum number of floors."\n        ),\n        "required_inputs": ["planning_authority_decision"],\n        "source_refs": [\n            source_ref(ORDER_4_2026_SOURCE, paragraph="3.3(ιγ)", page_number=22),\n            source_ref(ORDER_4_2026_SOURCE, paragraph="4.1(ιγ)", page_number=26),\n        ],\n    },\n    {\n        "rule_id": "coverage_outdoor_parking_cover",\n        "category": "coverage",\n        "title": "Qualifying lightweight outdoor-parking covers may also be excluded from coverage",\n        "effect_type": "exclude_from_coverage",\n        "status": "conditional",\n        "summary": (\n            "For the listed development types, qualifying lightweight modern covers over outdoor vehicle parking may be excluded from coverage "\n            "when the 3.00 m road-boundary distance, other-boundary contact limits and amenity conditions are met. Special-character areas and "\n            "historic cores are excluded."\n        ),\n        "required_inputs": ["development_type", "outdoor_parking_cover_area_m2", "road_boundary_distance_m", "other_boundary_contact", "historic_or_special_area"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="3.9-3.9.1", page_number=24)],\n    },\n    {\n        "rule_id": "height_sloping_land",\n        "category": "height/floors",\n        "title": "Steeply sloping land can affect floor and height treatment",\n        "effect_type": "discretionary_adjustment",\n        "status": "conditional",\n        "summary": (\n            "On steeply sloping land, the Planning Authority may allow part of a building to have one more "\n            "floor than the area maximum when justified. The height measured from the lowest natural-ground "\n            "point touching the building is limited to 1.80 m above the height calculated under the defined method."\n        ),\n        "required_inputs": ["terrain_profile", "building_section", "planning_authority_decision"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="4.2", page_number=26)],\n    },\n    {\n        "rule_id": "height_office_commercial_mep_allowance",\n        "category": "height/floors",\n        "title": "Office, commercial and similar uses may receive 0.70 m extra height per floor for justified installations",\n        "effect_type": "height_adjustment",\n        "status": "conditional",\n        "summary": (\n            "For office, commercial or other developments where mechanical or other special installations justify it, building height is increased "\n            "by 0.70 m per floor. The rule excludes settlement cores, special-character areas and historic centres."\n        ),\n        "required_inputs": ["development_type", "mechanical_or_special_installation_justification", "floor_count", "historic_or_special_area"],\n        "formula": "height_allowance_m = 0.70 * floor_count",\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="4.1.1", page_number=26)],\n    },\n    {\n        "rule_id": "height_roof_elements_35pct",\n        "category": "height/floors",\n        "title": "Reasonable roof elements may exceed zone height/floor limits",\n        "effect_type": "height_floor_exception",\n        "status": "conditional",\n        "summary": (\n            "Reasonable exceedance of maximum height and floor count is allowed for listed roof elements. "\n            "The small covered roof spaces described in the rule are limited to 35% of total roof area; "\n            "a roof swimming pool is listed with maximum height 1.40 m."\n        ),\n        "required_inputs": ["roof_area_m2", "roof_element_area_m2", "roof_element_types"],\n        "formula": "roof_element_area_m2 <= 0.35 * roof_area_m2",\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="4.3", page_number=26)],\n    },\n    {\n        "rule_id": "setbacks_baseline_external",\n        "category": "setbacks",\n        "title": "Baseline building setbacks come from the applicable development plan",\n        "effect_type": "baseline_dependency",\n        "status": "always",\n        "summary": (\n            "Order 4/2026 governs adjustments and special cases, but the base minimum building distances "\n            "are those set by the General Policy Provisions of the applicable Local Plan or the Rural Policy Statement."\n        ),\n        "required_inputs": ["applicable_development_plan", "boundary_types", "baseline_setback_rules"],\n        "source_refs": [source_ref(ETEK_MARCH_2026_SOURCE, page_number=39, section_title="Αποστάσεις οικοδομής από όρια τεμαχίου")],\n    },\n    {\n        "rule_id": "setback_balcony_projection",\n        "category": "setbacks",\n        "title": "Balconies may project 1.50 m into specified public-boundary setbacks",\n        "effect_type": "setback_projection",\n        "status": "conditional",\n        "summary": (\n            "Balconies may project up to 1.50 m into the minimum distance between the main building and a "\n            "public road, open public space or public pedestrian way, unless the Planning Authority considers "\n            "this harmful to the road character or identity."\n        ),\n        "required_inputs": ["boundary_type", "balcony_projection_m"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.2", page_number=30)],\n    },\n    {\n        "rule_id": "setback_entrance_canopy_projection",\n        "category": "setbacks",\n        "title": "Entrance canopies may project 1.20 m into minimum setbacks",\n        "effect_type": "setback_projection",\n        "status": "conditional",\n        "summary": "Cantilevered entrance canopies may project up to 1.20 m into the applicable minimum building distance.",\n        "required_inputs": ["canopy_projection_m", "applicable_baseline_setback_m"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.3", page_number=30)],\n    },\n    {\n        "rule_id": "setback_architectural_projection",\n        "category": "setbacks",\n        "title": "Qualifying architectural projections may extend 1.20 m into setbacks",\n        "effect_type": "setback_projection",\n        "status": "conditional",\n        "summary": (\n            "Qualifying non-walkable architectural projections may extend into the minimum building distance "\n            "when they improve the design/function, do not harm neighbouring amenity and project no more than 1.20 m."\n        ),\n        "required_inputs": ["projection_type", "projection_depth_m", "amenity_assessment"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.4", page_number=30)],\n    },\n    {\n        "rule_id": "setback_fourth_floor_plus",\n        "category": "setbacks",\n        "title": "Fourth-floor-and-above setback distances can be adjusted",\n        "effect_type": "discretionary_adjustment",\n        "status": "conditional",\n        "summary": (\n            "The Planning Authority may reduce the distances specified for the fourth floor and above where "\n            "those distances prevent full use of the parcel\'s building coefficient or create non-functional floors, "\n            "provided neighbouring amenity and the surrounding built environment are not adversely affected."\n        ),\n        "required_inputs": ["proposed_floor_count", "applicable_development_plan", "building_envelope_test", "planning_authority_decision"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.8", page_number=31)],\n    },\n    {\n        "rule_id": "setback_auxiliary_building",\n        "category": "setbacks",\n        "title": "Auxiliary buildings have explicit size, height and contact limits",\n        "effect_type": "dimensional_limits",\n        "status": "conditional",\n        "summary": (\n            "A qualifying auxiliary building may touch non-road parcel boundaries, but its area including relevant "\n            "covered parking is limited to 25% of the main building area and 10% of net parcel area; common-boundary "\n            "contact is generally limited to 35% of that boundary, maximum height is 3.50 m and minimum distance from "\n            "the main building is 1.50 m, subject to the detailed exceptions."\n        ),\n        "required_inputs": ["auxiliary_building_area_m2", "main_building_area_m2", "net_parcel_area_m2", "boundary_contact_lengths_m", "auxiliary_height_m", "distance_to_main_building_m"],\n        "formula": "aux_area <= min(0.25 * main_building_area_m2, 0.10 * net_parcel_area_m2)",\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.9", page_number=32)],\n    },\n    {\n        "rule_id": "setback_pool",\n        "category": "setbacks",\n        "title": "Swimming pools may be as close as 1.50 m to non-road boundaries",\n        "effect_type": "minimum_distance",\n        "status": "conditional",\n        "summary": "A swimming pool may be up to 1.50 m from parcel boundaries other than a road boundary.",\n        "required_inputs": ["pool_boundary_distance_m", "boundary_type"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.10", page_number=34)],\n    },\n    {\n        "rule_id": "setback_mechanical_rooms",\n        "category": "setbacks",\n        "title": "Mechanical/boiler rooms and similar elements have special boundary distances",\n        "effect_type": "minimum_distance",\n        "status": "conditional",\n        "summary": (\n            "Mechanical and boiler rooms for central heating, fuel tanks and grills/ovens may be as close as 1.80 m "\n            "to non-road boundaries. A pool plant room of about 1.20 m height, underground water tank and underground "\n            "water pump room may touch non-road boundaries, subject to the detailed provisions."\n        ),\n        "required_inputs": ["element_type", "element_height_m", "boundary_type", "boundary_distance_m"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.11", page_number=34)],\n    },\n    {\n        "rule_id": "setback_pergola",\n        "category": "setbacks",\n        "title": "Permeable pergolas may sit within non-road setbacks",\n        "effect_type": "setback_exception",\n        "status": "conditional",\n        "summary": (\n            "Pergolas and similar lightweight permeable structures, including operable systems, may be built within "\n            "minimum distances from boundaries other than road boundaries and are not counted in the maximum common-boundary "\n            "contact length, provided neighbouring or area amenity is not affected."\n        ),\n        "required_inputs": ["pergola_type", "boundary_type", "amenity_assessment"],\n        "source_refs": [source_ref(ORDER_4_2026_SOURCE, paragraph="5.12", page_number=34)],\n    },\n    {\n        "rule_id": "parking_residential_minimum",\n        "category": "parking",\n        "title": "Residential parking starts at one space per dwelling",\n        "effect_type": "parking_requirement",\n        "status": "conditional",\n        "summary": (\n            "The ETEK guide reproduces the residential parking standards under Order 1/2016: at least one parking "\n            "space per dwelling, with visitor-space additions for larger horizontal housing schemes and apartment buildings, "\n            "plus an additional space for qualifying dwellings above 150 m² outside specified dense/central areas."\n        ),\n        "required_inputs": ["residential_units", "development_form", "unit_usable_areas_m2", "location_density_context"],\n        "source_refs": [source_ref(ETEK_MARCH_2026_SOURCE, page_number=45, section_title="Χώροι Στάθμευσης - Οικιστικές Αναπτύξεις"),\n            source_ref(ETEK_MARCH_2026_SOURCE, page_number=46, section_title="Χώροι Στάθμευσης - Οικιστικές Αναπτύξεις"),\n        ],\n    },\n]\n\nRULES_BY_ID = {rule["rule_id"]: rule for rule in RULE_CATALOG}\n\n\ndef _normalise_text(value: Any) -> str:\n    text = unicodedata.normalize("NFD", str(value or "").casefold())\n    text = "".join(ch for ch in text if not unicodedata.combining(ch))\n    return re.sub(r"\\s+", " ", text).strip()\n\n\ndef _canonical_zone_code(value: Any) -> str:\n    text = _normalise_text(value).replace(" ", "")\n    greek_to_latin = str.maketrans({\n        "κ": "k", "α": "a", "η": "h", "β": "b", "γ": "g", "δ": "d",\n        "ε": "e", "ζ": "z", "θ": "th", "ι": "i", "λ": "l", "μ": "m",\n        "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s",\n        "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o",\n    })\n    return text.translate(greek_to_latin).upper()\n\n\ndef _is_residential_zone(zone_code: Any) -> bool:\n    code = _canonical_zone_code(zone_code)\n    return code.startswith("KA") or re.fullmatch(r"H\\d+[A-Z]*", code) is not None\n\n\ndef _float(value: Any) -> float | None:\n    try:\n        if value in (None, ""):\n            return None\n        number = float(value)\n        if not math.isfinite(number):\n            return None\n        return number\n    except Exception:\n        return None\n\n\ndef calculate_zoned_capacity(parcel_area_m2: Any, zones: Iterable[Dict[str, Any]]) -> Dict[str, Any]:\n    """Calculate baseline theoretical capacity without inventing missing zone overlaps."""\n    area = _float(parcel_area_m2)\n    zone_list = [dict(z) for z in zones or []]\n    result: Dict[str, Any] = {\n        "calculation_method": None,\n        "parcel_area_basis_m2": round(area, 2) if area is not None else None,\n        "area_basis_status": "registered_extent_not_net_area_confirmed",\n        "calculation_authority_status": "preliminary_platform_calculation",\n        "multi_zone_policy_status": None,\n        "effective_density_percent": None,\n        "effective_coverage_percent": None,\n        "theoretical_max_floor_area_m2": None,\n        "theoretical_max_ground_coverage_m2": None,\n        "zone_overlap_total_percent": None,\n        "zone_overlap_complete": None,\n        "calculation_warnings": [],\n    }\n    if area is None or area <= 0 or not zone_list:\n        result["calculation_warnings"].append("Parcel area or planning-zone data is missing.")\n        return result\n\n    if len(zone_list) == 1:\n        zone = zone_list[0]\n        density = _float(zone.get("density_percent"))\n        coverage = _float(zone.get("coverage_percent"))\n        result["calculation_method"] = "single_zone"\n        result["effective_density_percent"] = round(density, 4) if density is not None else None\n        result["effective_coverage_percent"] = round(coverage, 4) if coverage is not None else None\n        if density is not None:\n            result["theoretical_max_floor_area_m2"] = round(area * density / 100.0, 2)\n        if coverage is not None:\n            result["theoretical_max_ground_coverage_m2"] = round(area * coverage / 100.0, 2)\n        return result\n\n    overlaps = [_float(z.get("overlap_percent")) for z in zone_list]\n    if any(value is None for value in overlaps):\n        result["calculation_method"] = "multi_zone_incomplete"\n        result["zone_overlap_complete"] = False\n        result["calculation_warnings"].append(\n            "Multiple planning zones were returned but at least one overlap percentage is missing; PLANA did not infer a weighted capacity."\n        )\n        return result\n\n    overlap_total = sum(value or 0.0 for value in overlaps)\n    result["zone_overlap_total_percent"] = round(overlap_total, 4)\n    result["zone_overlap_complete"] = 99.0 <= overlap_total <= 101.0\n    result["calculation_method"] = "weighted_zone_overlap"\n    result["multi_zone_policy_status"] = "development_plan_applicability_not_confirmed"\n    if not result["zone_overlap_complete"]:\n        result["calculation_warnings"].append(\n            f"Planning-zone overlaps total {overlap_total:.2f}% rather than approximately 100%; weighted capacity is withheld."\n        )\n        return result\n\n    density_weighted = 0.0\n    coverage_weighted = 0.0\n    density_complete = True\n    coverage_complete = True\n    for zone, overlap in zip(zone_list, overlaps):\n        weight = (overlap or 0.0) / overlap_total\n        density = _float(zone.get("density_percent"))\n        coverage = _float(zone.get("coverage_percent"))\n        if density is None:\n            density_complete = False\n        else:\n            density_weighted += density * weight\n        if coverage is None:\n            coverage_complete = False\n        else:\n            coverage_weighted += coverage * weight\n\n    if density_complete:\n        result["effective_density_percent"] = round(density_weighted, 4)\n        result["theoretical_max_floor_area_m2"] = round(area * density_weighted / 100.0, 2)\n    else:\n        result["calculation_warnings"].append("A density coefficient is missing for one or more affected zones.")\n    if coverage_complete:\n        result["effective_coverage_percent"] = round(coverage_weighted, 4)\n        result["theoretical_max_ground_coverage_m2"] = round(area * coverage_weighted / 100.0, 2)\n    else:\n        result["calculation_warnings"].append("A coverage coefficient is missing for one or more affected zones.")\n    result["calculation_warnings"].append(\n        "The weighted multi-zone result is a DLS-overlap mathematical calculation. Confirm that the applicable Development Plan and its General Policy Provisions permit the average-coefficient treatment before relying on it as a planning entitlement."\n    )\n    return result\n\n\ndef calculate_residential_parking(\n    residential_units: int,\n    development_form: str,\n    unit_usable_areas_m2: Iterable[Any] | None = None,\n    outside_urban_core_historic_or_dense_area: bool | None = None,\n    etek_guide_scope_confirmed: bool | None = None,\n) -> Dict[str, Any]:\n    """Structured reproduction of the residential formulas shown in the ETEK guide (Order 1/2016 context)."""\n    units = max(int(residential_units or 0), 0)\n    form = _normalise_text(development_form).replace(" ", "_")\n    base = units\n    visitor = 0\n    if form in {"horizontal", "horizontal_housing", "houses", "οριζοντια"} and units > 5:\n        visitor = math.ceil(units / 6)\n    elif form in {"vertical", "apartment", "apartment_building", "apartments", "πολυκατοικια"} and units > 9:\n        visitor = math.ceil(units / 10)\n\n    large_unit_extra = 0\n    areas = [_float(v) for v in (unit_usable_areas_m2 or [])]\n    if outside_urban_core_historic_or_dense_area is True and areas:\n        large_unit_extra = sum(1 for area in areas[:units] if area is not None and area > 150.0)\n\n    missing_location_context = outside_urban_core_historic_or_dense_area is None and bool(areas)\n    warnings = [\n        "The parking formula is currently encoded from the March 2026 ETEK guide\'s reproduction of Order 1/2016; the direct Order 1/2016 text is not embedded in this structured rule layer."\n    ]\n    if etek_guide_scope_confirmed is not True:\n        warnings.append(\n            "The ETEK guide states that its concise guide scope is limited to the four major-city Local Plans. Confirm the applicable Development Plan/source before treating this calculation as final."\n        )\n    if missing_location_context:\n        warnings.append(\n            "Location context is required to decide whether the extra parking space for residential units above 150 m² applies."\n        )\n\n    return {\n        "base_spaces": base,\n        "visitor_spaces": visitor,\n        "large_unit_extra_spaces": large_unit_extra,\n        "minimum_spaces": base + visitor + large_unit_extra,\n        "formula_status": (\n            "provisional_source_scope_or_context_required"\n            if etek_guide_scope_confirmed is not True or missing_location_context\n            else "complete_for_supplied_inputs"\n        ),\n        "source_status": "secondary_interpretive_reproduction_of_order_1_2016",\n        "warnings": warnings,\n        "source_refs": RULES_BY_ID["parking_residential_minimum"]["source_refs"],\n    }\n\n\ndef calculate_civil_defence_shelter_incentive(net_parcel_area_m2: Any) -> Dict[str, Any]:\n    """Calculate the 0.05 parcel-area incentive described in Order 4/2026 paragraph 2.7.1."""\n    area = _float(net_parcel_area_m2)\n    if area is None or area <= 0:\n        return {\n            "status": "net_parcel_area_required",\n            "additional_floor_area_m2": None,\n            "calculation_basis_m2": None,\n            "source_refs": RULES_BY_ID["density_civil_defence_shelter_incentive"]["source_refs"],\n        }\n    basis = min(area, 1000.0)\n    return {\n        "status": "calculated_for_qualifying_shelter_scenario",\n        "additional_floor_area_m2": round(0.05 * basis, 2),\n        "calculation_basis_m2": round(basis, 2),\n        "coefficient_increase": 0.05,\n        "source_refs": RULES_BY_ID["density_civil_defence_shelter_incentive"]["source_refs"],\n    }\n\n\ndef _rule_view(rule_id: str, **extra: Any) -> Dict[str, Any]:\n    rule = RULES_BY_ID[rule_id]\n    value = {\n        "rule_id": rule["rule_id"],\n        "category": rule["category"],\n        "title": rule["title"],\n        "effect_type": rule["effect_type"],\n        "summary": rule["summary"],\n        "required_inputs": list(rule.get("required_inputs") or []),\n        "source_refs": list(rule.get("source_refs") or []),\n    }\n    if rule.get("formula"):\n        value["formula"] = rule["formula"]\n    value.update(extra)\n    return value\n\n\ndef evaluate_parcel_rules(\n    parcel_details: Dict[str, Any],\n    scenario: Dict[str, Any] | None = None,\n) -> Dict[str, Any]:\n    """Evaluate the structured rule layer against known parcel/scenario facts."""\n    scenario = scenario or {}\n    parcel = parcel_details.get("parcel") or {}\n    zones = parcel_details.get("planning_zones") or []\n    capacity = calculate_zoned_capacity(parcel.get("parcel_extent_m2"), zones)\n    parcel_area = _float(parcel.get("parcel_extent_m2"))\n\n    applied_rules: List[Dict[str, Any]] = [\n        _rule_view(\n            "order4_2026_current_source",\n            outcome="PLANA rule calculations use Order 4/2026 for covered topics; Order 4/2024 is treated as superseded from 11 May 2026.",\n            applicability_status="applied_source_precedence",\n        ),\n        _rule_view(\n            "calculation_net_parcel_area_basis",\n            outcome=(\n                f"The current baseline uses the DLS registered parcel extent ({parcel_area:,.2f} m²) because a confirmed clean/net development area is not yet available."\n                if parcel_area is not None\n                else "A confirmed clean/net development area is not available."\n            ),\n            applicability_status="calculation_basis_not_fully_confirmed",\n        ),\n    ]\n\n    if capacity["calculation_method"] == "weighted_zone_overlap":\n        applied_rules.append({\n            "rule_id": "local_plan_multi_zone_weighting",\n            "category": "density calculation",\n            "title": "Multi-zone coefficients were mathematically weighted by DLS overlap shares",\n            "effect_type": "provisional_deterministic_calculation",\n            "summary": (\n                "PLANA calculated an effective density and coverage from the area share of each affected DLS zone instead of selecting one zone coefficient. "\n                "The ETEK guide illustrates this average-coefficient method for the Local Plan context and refers to the Local Plan appendices."\n            ),\n            "outcome": (\n                f"Provisional effective density {capacity[\'effective_density_percent\']:.2f}% and effective coverage "\n                f"{capacity[\'effective_coverage_percent\']:.2f}% across {len(zones)} zones. Confirm the applicable Development Plan before treating this as entitlement."\n            ) if capacity.get("effective_density_percent") is not None and capacity.get("effective_coverage_percent") is not None else "Weighted calculation could not be completed.",\n            "required_inputs": ["applicable_development_plan"],\n            "applicability_status": "mathematical_result_policy_applicability_unconfirmed",\n            "provisional": True,\n            "source_refs": [source_ref(ETEK_MARCH_2026_SOURCE, page_number=16, section_title="Μέσος Συντελεστής Δόμησης, Μέσο Ποσοστό Κάλυψης")],\n        })\n\n    conditional_rules: List[Dict[str, Any]] = []\n    triggered_rules: List[Dict[str, Any]] = []\n\n    # Parcel-triggered coverage rules.\n    low_coverage_zones = [\n        zone for zone in zones\n        if _is_residential_zone(zone.get("zone"))\n        and (_float(zone.get("coverage_percent")) is not None)\n        and (_float(zone.get("coverage_percent")) or 0) <= 35.0\n    ]\n    if low_coverage_zones and parcel_area is not None:\n        cap = round(parcel_area * 0.05, 2)\n        triggered_rules.append(_rule_view(\n            "coverage_low_residential_zone_005",\n            outcome=(\n                f"The parcel has a residential-zone coverage coefficient of 35% or less. The rule\'s maximum "\n                f"covered-veranda/covered-parking coverage exclusion cap is {cap:,.2f} m², but the actual excluded "\n                "area depends on the proposed design and qualifying uses."\n            ),\n            calculated_values={"coverage_exclusion_cap_m2": cap},\n            applicability_status="parcel_numeric_trigger_design_inputs_required",\n        ))\n\n    higher_coverage_zones = [\n        zone for zone in zones\n        if (_float(zone.get("coverage_percent")) or 0) > 50.0\n    ]\n    if higher_coverage_zones and parcel_area is not None and parcel_area > 400.0:\n        triggered_rules.append(_rule_view(\n            "coverage_over_050_discretionary_reduction",\n            outcome=(\n                "The parcel meets the numeric parts of the rule (coverage above 50% and parcel area above roughly 400 m²). "\n                "The Order describes the affected zones as areas with a continuous building system; that context is not established from the current DLS payload. "\n                "Treat this as a review signal, not an automatic reduction."\n            ),\n            discretion=True,\n            applicability_status="partial_match_continuous_building_system_not_confirmed",\n        ))\n\n    max_zone_floors = max(\n        [_float(zone.get("max_floors")) or 0 for zone in zones] or [0]\n    )\n    if max_zone_floors >= 4:\n        triggered_rules.append(_rule_view(\n            "setback_fourth_floor_plus",\n            outcome=(\n                f"At least one affected zone permits {int(max_zone_floors)} floors. Paragraph 5.8 becomes potentially relevant only if a proposal actually uses the fourth floor or above "\n                "and the applicable General Policy Provision distances prevent full coefficient use or create non-functional floors. Any distance adjustment remains discretionary."\n            ),\n            discretion=True,\n            applicability_status="review_signal_proposal_and_envelope_test_required",\n        ))\n\n    # Rules that Priority 3/design scenarios can calculate once assumptions exist.\n    design_rule_ids = [\n        "density_basement_auxiliary_exclusion",\n        "density_pilotis_parking_exclusion",\n        "density_ground_covered_parking_exclusion",\n        "density_parking_any_level_exclusion",\n        "density_mechanical_floor_exclusion",\n        "density_attic_exclusion",\n        "density_balcony_veranda_allowance",\n        "density_common_corridor_exclusion",\n        "density_roof_support_spaces",\n        "density_pergola_exclusion",\n        "density_mezzanine_exclusion",\n        "density_entrance_lobby_exclusion",\n        "density_staircase_treatment",\n        "density_fire_protection_exclusions",\n        "density_outdoor_parking_cover",\n        "density_civil_defence_shelter_incentive",\n        "coverage_general_exclusions",\n        "coverage_uncovered_ground_terrace_fill",\n        "coverage_outdoor_parking_cover",\n        "height_sloping_land",\n        "height_office_commercial_mep_allowance",\n        "height_roof_elements_35pct",\n        "setbacks_baseline_external",\n        "setback_balcony_projection",\n        "setback_entrance_canopy_projection",\n        "setback_architectural_projection",\n        "setback_auxiliary_building",\n        "setback_pool",\n        "setback_mechanical_rooms",\n        "setback_pergola",\n        "parking_residential_minimum",\n    ]\n    conditional_rules.extend(_rule_view(rule_id, outcome="Requires additional development/design inputs before calculation or application.") for rule_id in design_rule_ids)\n\n    scenario_calculations: Dict[str, Any] = {}\n    development_type = _normalise_text(scenario.get("development_type"))\n    development_form = _normalise_text(scenario.get("development_form"))\n    if development_type in {"residential", "οικιστικη", "housing"} and scenario.get("residential_units") is not None:\n        scenario_calculations["residential_parking"] = calculate_residential_parking(\n            int(scenario.get("residential_units") or 0),\n            str(scenario.get("development_form") or "apartment_building"),\n            scenario.get("unit_usable_areas_m2") or [],\n            scenario.get("outside_urban_core_historic_or_dense_area"),\n            scenario.get("etek_guide_scope_confirmed"),\n        )\n\n    apartment_forms = {"apartment", "apartment_building", "apartments", "πολυκατοικια"}\n    if (\n        development_type in {"residential", "οικιστικη", "housing"}\n        and development_form in apartment_forms\n        and scenario.get("has_basement") is True\n        and scenario.get("civil_defence_shelter_required") is True\n    ):\n        scenario_calculations["civil_defence_shelter_density_incentive"] = calculate_civil_defence_shelter_incentive(\n            scenario.get("net_parcel_area_m2")\n        )\n\n    checks = [\n        "Confirm the clean/net parcel area after any road, access, public-open-space or other land commitments before treating the DLS parcel extent as the final density/coverage calculation basis.",\n        "Identify the applicable Development Plan and its General Policy Provisions before calculating baseline building setbacks; Order 4/2026 mainly governs adjustments and special cases to those distances.",\n        "Confirm the proposed development/use type before applying use-specific density exclusions, coverage exclusions, floor/height adjustments or parking standards.",\n        "The March 2026 ETEK guide is concise, non-exhaustive and expressly scoped to the four major-city Local Plans; guide-only formulas are marked provisional until plan/source applicability is confirmed.",\n    ]\n    if capacity.get("calculation_warnings"):\n        checks.extend(capacity["calculation_warnings"])\n    if any(parcel.get(flag) for flag in ("is_preserved", "is_ancient")):\n        checks.append("The DLS preservation/ancient-property flag requires parcel-specific policy and authority review before relying on generic dimensional rules.")\n\n    return {\n        "status": "complete",\n        "rule_engine_version": RULE_ENGINE_VERSION,\n        "rules_effective_date": RULES_EFFECTIVE_DATE,\n        "authoritative_source": ORDER_4_2026_SOURCE,\n        "interpretive_source": ETEK_MARCH_2026_SOURCE,\n        "source_registry": [ORDER_4_2026_SOURCE, ETEK_MARCH_2026_SOURCE],\n        "source_precedence": (\n            "Order 4/2026 is authoritative for its covered calculation/setback topics and supersedes Order 4/2024 from 11 May 2026. "\n            "The March 2026 ETEK guide is used for interpretation and diagrams, not to override Order 4/2026."\n        ),\n        "base_capacity": capacity,\n        "applied_rules": applied_rules,\n        "triggered_rules": triggered_rules,\n        "conditional_rules": conditional_rules,\n        "scenario_calculations": scenario_calculations,\n        "checks_before_reliance": checks[:8],\n        "catalog_rule_count": len(RULE_CATALOG),\n        "application_summary": {\n            "applied_rule_count": len(applied_rules),\n            "parcel_trigger_count": len(triggered_rules),\n            "design_dependent_rule_count": len(conditional_rules),\n            "scenario_calculation_count": len(scenario_calculations),\n        },\n    }\n\n\ndef compact_rule_context(rule_analysis: Dict[str, Any]) -> Dict[str, Any]:\n    """Return the compact structured context passed to the RAG analyst."""\n    return {\n        "rule_engine_version": rule_analysis.get("rule_engine_version"),\n        "rules_effective_date": rule_analysis.get("rules_effective_date"),\n        "source_precedence": rule_analysis.get("source_precedence"),\n        "base_capacity": rule_analysis.get("base_capacity"),\n        "applied_rules": rule_analysis.get("applied_rules"),\n        "triggered_rules": rule_analysis.get("triggered_rules"),\n        "checks_before_reliance": rule_analysis.get("checks_before_reliance"),\n    }\n')

# ---- embedded opportunity_engine.py ----
_plana_install_embedded_module('opportunity_engine', '"""Deterministic development-capacity and opportunity analysis for PLANA.CY.\n\nThis module deliberately separates planning capacity from market evidence and\nfinancial assumptions. It does not invent live market comparables. Where market\nor cost inputs are absent, the engine still returns programme-capacity scenarios\nbut withholds profit/return outputs.\n"""\n\nfrom __future__ import annotations\n\nimport math\nfrom typing import Any, Dict, Iterable, List, Mapping\n\nfrom planning_rules import calculate_residential_parking\n\n\nOPPORTUNITY_ENGINE_VERSION = "cy-opportunity-v1"\nASSUMPTION_SET_VERSION = "residential-capacity-v1"\n\nDEFAULT_CAPACITY_ASSUMPTIONS: Dict[str, Any] = {\n    "efficiency_low_percent": 80.0,\n    "efficiency_high_percent": 85.0,\n    "unit_sizes_m2": {\n        "one_bed": 55.0,\n        "two_bed": 85.0,\n        "three_bed": 115.0,\n    },\n    "programme_mixes": {\n        "one_bed_focused": {\n            "label": "1-bed focused",\n            "mix": {"one_bed": 0.70, "two_bed": 0.25, "three_bed": 0.05},\n        },\n        "balanced": {\n            "label": "Balanced mix",\n            "mix": {"one_bed": 0.30, "two_bed": 0.50, "three_bed": 0.20},\n        },\n        "three_bed_focused": {\n            "label": "3-bed focused",\n            "mix": {"one_bed": 0.10, "two_bed": 0.30, "three_bed": 0.60},\n        },\n    },\n}\n\nDEFAULT_FINANCIAL_ASSUMPTIONS: Dict[str, Any] = {\n    # These are explicit platform defaults, not Cyprus market observations.\n    "professional_fees_percent": 10.0,\n    "contingency_percent": 7.5,\n    "finance_percent": 6.0,\n    "sales_cost_percent": 3.0,\n    "costed_built_area_factor": 1.0,\n    "other_costs_eur": 0.0,\n}\n\n\ndef _number(value: Any) -> float | None:\n    if value in (None, ""):\n        return None\n    try:\n        number = float(value)\n    except (TypeError, ValueError):\n        return None\n    if not math.isfinite(number):\n        return None\n    return number\n\n\ndef _non_negative(value: Any, default: float | None = None) -> float | None:\n    number = _number(value)\n    if number is None:\n        return default\n    return max(number, 0.0)\n\n\ndef _bounded_percent(value: Any, default: float) -> float:\n    number = _number(value)\n    if number is None:\n        number = default\n    return min(max(number, 0.0), 100.0)\n\n\ndef _ordered_pair(low: float | None, high: float | None) -> tuple[float | None, float | None]:\n    if low is not None and high is not None and low > high:\n        return high, low\n    return low, high\n\n\ndef _merge_unit_sizes(raw: Mapping[str, Any] | None) -> Dict[str, float]:\n    defaults = DEFAULT_CAPACITY_ASSUMPTIONS["unit_sizes_m2"]\n    raw = raw or {}\n    sizes: Dict[str, float] = {}\n    for key, fallback in defaults.items():\n        value = _non_negative(raw.get(key), fallback)\n        sizes[key] = round(max(value or fallback, 1.0), 2)\n    return sizes\n\n\ndef normalise_assumptions(assumptions: Mapping[str, Any] | None = None) -> Dict[str, Any]:\n    raw = dict(assumptions or {})\n    efficiency_low = _bounded_percent(\n        raw.get("efficiency_low_percent"),\n        DEFAULT_CAPACITY_ASSUMPTIONS["efficiency_low_percent"],\n    )\n    efficiency_high = _bounded_percent(\n        raw.get("efficiency_high_percent"),\n        DEFAULT_CAPACITY_ASSUMPTIONS["efficiency_high_percent"],\n    )\n    if efficiency_low > efficiency_high:\n        efficiency_low, efficiency_high = efficiency_high, efficiency_low\n\n    market = dict(raw.get("market") or {})\n    costs = dict(raw.get("costs") or {})\n    sale_low, sale_high = _ordered_pair(\n        _non_negative(market.get("sale_price_low_eur_per_m2")),\n        _non_negative(market.get("sale_price_high_eur_per_m2")),\n    )\n    rent_low, rent_high = _ordered_pair(\n        _non_negative(market.get("rent_low_eur_per_m2_month")),\n        _non_negative(market.get("rent_high_eur_per_m2_month")),\n    )\n    construction_low, construction_high = _ordered_pair(\n        _non_negative(costs.get("construction_cost_low_eur_per_m2")),\n        _non_negative(costs.get("construction_cost_high_eur_per_m2")),\n    )\n\n    return {\n        "assumption_set_version": ASSUMPTION_SET_VERSION,\n        "efficiency_low_percent": round(efficiency_low, 2),\n        "efficiency_high_percent": round(efficiency_high, 2),\n        "unit_sizes_m2": _merge_unit_sizes(raw.get("unit_sizes_m2")),\n        "selected_programme": str(raw.get("selected_programme") or "balanced"),\n        "market": {\n            "sale_price_low_eur_per_m2": sale_low,\n            "sale_price_high_eur_per_m2": sale_high,\n            "rent_low_eur_per_m2_month": rent_low,\n            "rent_high_eur_per_m2_month": rent_high,\n            "source_label": str(market.get("source_label") or "").strip() or None,\n            "source_date": str(market.get("source_date") or "").strip() or None,\n            "confidence": str(market.get("confidence") or "assumption").strip().lower(),\n        },\n        "costs": {\n            "construction_cost_low_eur_per_m2": construction_low,\n            "construction_cost_high_eur_per_m2": construction_high,\n            "professional_fees_percent": _bounded_percent(\n                costs.get("professional_fees_percent"),\n                DEFAULT_FINANCIAL_ASSUMPTIONS["professional_fees_percent"],\n            ),\n            "contingency_percent": _bounded_percent(\n                costs.get("contingency_percent"),\n                DEFAULT_FINANCIAL_ASSUMPTIONS["contingency_percent"],\n            ),\n            "finance_percent": _bounded_percent(\n                costs.get("finance_percent"),\n                DEFAULT_FINANCIAL_ASSUMPTIONS["finance_percent"],\n            ),\n            "sales_cost_percent": _bounded_percent(\n                costs.get("sales_cost_percent"),\n                DEFAULT_FINANCIAL_ASSUMPTIONS["sales_cost_percent"],\n            ),\n            "costed_built_area_factor": round(\n                max(\n                    _non_negative(\n                        costs.get("costed_built_area_factor"),\n                        DEFAULT_FINANCIAL_ASSUMPTIONS["costed_built_area_factor"],\n                    ) or 1.0,\n                    0.01,\n                ),\n                3,\n            ),\n            "land_cost_eur": _non_negative(costs.get("land_cost_eur")),\n            "other_costs_eur": _non_negative(\n                costs.get("other_costs_eur"),\n                DEFAULT_FINANCIAL_ASSUMPTIONS["other_costs_eur"],\n            ) or 0.0,\n        },\n    }\n\n\ndef _normalise_mix(mix: Mapping[str, Any]) -> Dict[str, float]:\n    keys = ("one_bed", "two_bed", "three_bed")\n    values = {key: max(_number(mix.get(key)) or 0.0, 0.0) for key in keys}\n    total = sum(values.values())\n    if total <= 0:\n        return {key: 1.0 / len(keys) for key in keys}\n    return {key: value / total for key, value in values.items()}\n\n\ndef _allocate_unit_mix(net_area_m2: float, unit_sizes: Mapping[str, float], mix: Mapping[str, Any]) -> Dict[str, Any]:\n    normalised_mix = _normalise_mix(mix)\n    weighted_size = sum(normalised_mix[key] * unit_sizes[key] for key in normalised_mix)\n    target_units = max(int(math.floor(net_area_m2 / weighted_size)), 0) if weighted_size > 0 else 0\n\n    raw_counts = {key: target_units * normalised_mix[key] for key in normalised_mix}\n    counts = {key: int(math.floor(value)) for key, value in raw_counts.items()}\n    remaining = target_units - sum(counts.values())\n    remainder_order = sorted(\n        normalised_mix,\n        key=lambda key: raw_counts[key] - counts[key],\n        reverse=True,\n    )\n    for key in remainder_order[:remaining]:\n        counts[key] += 1\n\n    def used_area() -> float:\n        return sum(counts[key] * unit_sizes[key] for key in counts)\n\n    # Discrete rounding can exceed the area even when the weighted average fitted.\n    while used_area() > net_area_m2 and sum(counts.values()) > 0:\n        removable = [key for key, count in counts.items() if count > 0]\n        key = max(removable, key=lambda item: unit_sizes[item])\n        counts[key] -= 1\n\n    used = used_area()\n    unit_areas: List[float] = []\n    for key in ("one_bed", "two_bed", "three_bed"):\n        unit_areas.extend([unit_sizes[key]] * counts[key])\n\n    return {\n        "total_units": sum(counts.values()),\n        "unit_counts": counts,\n        "unit_sizes_m2": dict(unit_sizes),\n        "weighted_target_unit_size_m2": round(weighted_size, 2),\n        "allocated_unit_area_m2": round(used, 2),\n        "unallocated_net_area_m2": round(max(net_area_m2 - used, 0.0), 2),\n        "unit_usable_areas_m2": unit_areas,\n    }\n\n\ndef _capacity_confidence(development_potential: Mapping[str, Any]) -> str:\n    if development_potential.get("theoretical_max_floor_area_m2") in (None, 0):\n        return "low"\n    if development_potential.get("area_basis_status") != "net_area_confirmed":\n        return "low"\n    if development_potential.get("calculation_method") == "weighted_zone_overlap" and development_potential.get("multi_zone_policy_status") != "confirmed":\n        return "low"\n    return "medium"\n\n\ndef build_programme_scenarios(\n    gross_density_capacity_m2: Any,\n    assumptions: Mapping[str, Any],\n    *,\n    etek_guide_scope_confirmed: bool | None = None,\n) -> List[Dict[str, Any]]:\n    gross = _non_negative(gross_density_capacity_m2)\n    if gross is None or gross <= 0:\n        return []\n\n    efficiency_low = float(assumptions["efficiency_low_percent"]) / 100.0\n    efficiency_high = float(assumptions["efficiency_high_percent"]) / 100.0\n    unit_sizes = assumptions["unit_sizes_m2"]\n    results: List[Dict[str, Any]] = []\n\n    for scenario_id, definition in DEFAULT_CAPACITY_ASSUMPTIONS["programme_mixes"].items():\n        low = _allocate_unit_mix(gross * efficiency_low, unit_sizes, definition["mix"])\n        high = _allocate_unit_mix(gross * efficiency_high, unit_sizes, definition["mix"])\n        parking_low = calculate_residential_parking(\n            low["total_units"],\n            "apartment_building",\n            low["unit_usable_areas_m2"],\n            outside_urban_core_historic_or_dense_area=None,\n            etek_guide_scope_confirmed=etek_guide_scope_confirmed,\n        )\n        parking_high = calculate_residential_parking(\n            high["total_units"],\n            "apartment_building",\n            high["unit_usable_areas_m2"],\n            outside_urban_core_historic_or_dense_area=None,\n            etek_guide_scope_confirmed=etek_guide_scope_confirmed,\n        )\n        results.append({\n            "scenario_id": scenario_id,\n            "label": definition["label"],\n            "mix": definition["mix"],\n            "gross_density_capacity_m2": round(gross, 2),\n            "net_saleable_area_low_m2": round(gross * efficiency_low, 2),\n            "net_saleable_area_high_m2": round(gross * efficiency_high, 2),\n            "unit_count_low": low["total_units"],\n            "unit_count_high": high["total_units"],\n            "unit_counts_low": low["unit_counts"],\n            "unit_counts_high": high["unit_counts"],\n            "allocated_unit_area_low_m2": low["allocated_unit_area_m2"],\n            "allocated_unit_area_high_m2": high["allocated_unit_area_m2"],\n            "parking_spaces_low": parking_low["minimum_spaces"],\n            "parking_spaces_high": parking_high["minimum_spaces"],\n            "parking_status": parking_high["formula_status"],\n            "parking_source_status": parking_high["source_status"],\n            "parking_warnings": parking_high["warnings"],\n        })\n    return results\n\n\ndef _market_context(parcel: Mapping[str, Any], assumptions: Mapping[str, Any]) -> Dict[str, Any]:\n    area = _non_negative(parcel.get("parcel_extent_m2"))\n    value_2021 = _non_negative(parcel.get("price_2021"))\n    value_2018 = _non_negative(parcel.get("price_2018"))\n    market = assumptions["market"]\n    source_label = market.get("source_label")\n    source_date = market.get("source_date")\n\n    provided = all(\n        market.get(key) is not None\n        for key in ("sale_price_low_eur_per_m2", "sale_price_high_eur_per_m2")\n    )\n    confidence = "low"\n    if provided and source_label and source_date:\n        confidence = "medium"\n    elif provided:\n        confidence = "low"\n\n    warnings = [\n        "No live comparable-sales or rental dataset is connected in this build. User-entered market ranges are treated as explicit assumptions, not observed PLANA market evidence.",\n        "DLS General Valuation is shown as official valuation context only and is not used as a proxy for achievable apartment sale price or rent.",\n    ]\n\n    return {\n        "status": "assumption_range_supplied" if provided else "market_inputs_required",\n        "evidence_status": "no_live_market_dataset_connected",\n        "sale_price_low_eur_per_m2": market.get("sale_price_low_eur_per_m2"),\n        "sale_price_high_eur_per_m2": market.get("sale_price_high_eur_per_m2"),\n        "rent_low_eur_per_m2_month": market.get("rent_low_eur_per_m2_month"),\n        "rent_high_eur_per_m2_month": market.get("rent_high_eur_per_m2_month"),\n        "source_label": source_label,\n        "source_date": source_date,\n        "confidence": confidence,\n        "dls_general_valuation_2021_eur": value_2021,\n        "dls_general_valuation_2018_eur": value_2018,\n        "dls_general_valuation_2021_eur_per_parcel_m2": round(value_2021 / area, 2) if value_2021 is not None and area else None,\n        "dls_general_valuation_2018_eur_per_parcel_m2": round(value_2018 / area, 2) if value_2018 is not None and area else None,\n        "warnings": warnings,\n    }\n\n\ndef _case_financials(\n    *,\n    case_id: str,\n    label: str,\n    net_saleable_area_m2: float,\n    gross_density_capacity_m2: float,\n    sale_price_eur_per_m2: float,\n    construction_cost_eur_per_m2: float,\n    costs: Mapping[str, Any],\n    rent_eur_per_m2_month: float | None,\n) -> Dict[str, Any]:\n    costed_area = gross_density_capacity_m2 * float(costs["costed_built_area_factor"])\n    construction_cost = costed_area * construction_cost_eur_per_m2\n    professional_fees = construction_cost * float(costs["professional_fees_percent"]) / 100.0\n    contingency = construction_cost * float(costs["contingency_percent"]) / 100.0\n    land_cost = float(costs["land_cost_eur"] or 0.0)\n    other_costs = float(costs["other_costs_eur"] or 0.0)\n    pre_finance_cost = construction_cost + professional_fees + contingency + land_cost + other_costs\n    finance_cost = pre_finance_cost * float(costs["finance_percent"]) / 100.0\n    revenue = net_saleable_area_m2 * sale_price_eur_per_m2\n    sales_cost = revenue * float(costs["sales_cost_percent"]) / 100.0\n    total_cost = pre_finance_cost + finance_cost + sales_cost\n    profit = revenue - total_cost\n    profit_on_cost = (profit / total_cost * 100.0) if total_cost > 0 else None\n    invested_cost = pre_finance_cost + finance_cost + sales_cost\n    roi = (profit / invested_cost * 100.0) if invested_cost > 0 else None\n\n    fixed_cost_before_sales = pre_finance_cost + finance_cost\n    sales_fraction = float(costs["sales_cost_percent"]) / 100.0\n    break_even_sale_price = None\n    if net_saleable_area_m2 > 0 and sales_fraction < 1.0:\n        break_even_sale_price = fixed_cost_before_sales / ((1.0 - sales_fraction) * net_saleable_area_m2)\n\n    annual_rent = None\n    gross_yield_on_cost = None\n    if rent_eur_per_m2_month is not None:\n        annual_rent = net_saleable_area_m2 * rent_eur_per_m2_month * 12.0\n        if total_cost > 0:\n            gross_yield_on_cost = annual_rent / total_cost * 100.0\n\n    return {\n        "case_id": case_id,\n        "label": label,\n        "net_saleable_area_m2": round(net_saleable_area_m2, 2),\n        "costed_built_area_m2": round(costed_area, 2),\n        "sale_price_eur_per_m2": round(sale_price_eur_per_m2, 2),\n        "construction_cost_eur_per_m2": round(construction_cost_eur_per_m2, 2),\n        "estimated_revenue_eur": round(revenue, 2),\n        "construction_cost_eur": round(construction_cost, 2),\n        "professional_fees_eur": round(professional_fees, 2),\n        "contingency_eur": round(contingency, 2),\n        "finance_cost_eur": round(finance_cost, 2),\n        "sales_cost_eur": round(sales_cost, 2),\n        "land_cost_eur": round(land_cost, 2),\n        "other_costs_eur": round(other_costs, 2),\n        "total_development_cost_eur": round(total_cost, 2),\n        "estimated_profit_eur": round(profit, 2),\n        "profit_on_cost_percent": round(profit_on_cost, 2) if profit_on_cost is not None else None,\n        "roi_percent": round(roi, 2) if roi is not None else None,\n        "break_even_sale_price_eur_per_m2": round(break_even_sale_price, 2) if break_even_sale_price is not None else None,\n        "annual_gross_rent_eur": round(annual_rent, 2) if annual_rent is not None else None,\n        "gross_yield_on_cost_percent": round(gross_yield_on_cost, 2) if gross_yield_on_cost is not None else None,\n    }\n\n\ndef build_financial_analysis(\n    gross_density_capacity_m2: Any,\n    programme_scenarios: Iterable[Mapping[str, Any]],\n    assumptions: Mapping[str, Any],\n) -> Dict[str, Any]:\n    gross = _non_negative(gross_density_capacity_m2)\n    selected_id = assumptions.get("selected_programme") or "balanced"\n    scenarios = list(programme_scenarios)\n    selected = next((item for item in scenarios if item.get("scenario_id") == selected_id), None)\n    if selected is None and scenarios:\n        selected = scenarios[0]\n        selected_id = selected.get("scenario_id")\n\n    market = assumptions["market"]\n    costs = assumptions["costs"]\n    required = {\n        "sale_price_low_eur_per_m2": market.get("sale_price_low_eur_per_m2"),\n        "sale_price_high_eur_per_m2": market.get("sale_price_high_eur_per_m2"),\n        "construction_cost_low_eur_per_m2": costs.get("construction_cost_low_eur_per_m2"),\n        "construction_cost_high_eur_per_m2": costs.get("construction_cost_high_eur_per_m2"),\n    }\n    missing = [key for key, value in required.items() if value is None]\n    if gross is None or gross <= 0 or selected is None:\n        missing.append("gross_density_capacity_m2")\n\n    if costs.get("land_cost_eur") is None:\n        land_status = "excluded_land_cost_not_supplied"\n    else:\n        land_status = "included"\n\n    if missing:\n        return {\n            "status": "inputs_required",\n            "selected_programme": selected_id,\n            "missing_inputs": sorted(set(missing)),\n            "land_cost_status": land_status,\n            "cases": [],\n            "warnings": [\n                "Financial outputs are withheld until both sale-price and construction-cost ranges are supplied.",\n                "Land cost is excluded unless explicitly entered. A zero or missing land cost can materially overstate profit and returns.",\n                "The V1 finance percentage is a simplified cost allowance, not a monthly cash-flow or IRR model.",\n            ],\n        }\n\n    sale_low = float(market["sale_price_low_eur_per_m2"])\n    sale_high = float(market["sale_price_high_eur_per_m2"])\n    if sale_low > sale_high:\n        sale_low, sale_high = sale_high, sale_low\n    construction_low = float(costs["construction_cost_low_eur_per_m2"])\n    construction_high = float(costs["construction_cost_high_eur_per_m2"])\n    if construction_low > construction_high:\n        construction_low, construction_high = construction_high, construction_low\n\n    net_low = float(selected["allocated_unit_area_low_m2"])\n    net_high = float(selected["allocated_unit_area_high_m2"])\n    net_mid = (net_low + net_high) / 2.0\n    sale_mid = (sale_low + sale_high) / 2.0\n    construction_mid = (construction_low + construction_high) / 2.0\n    rent_low = market.get("rent_low_eur_per_m2_month")\n    rent_high = market.get("rent_high_eur_per_m2_month")\n    rent_mid = None\n    if rent_low is not None and rent_high is not None:\n        rent_mid = (float(rent_low) + float(rent_high)) / 2.0\n\n    cases = [\n        _case_financials(\n            case_id="conservative",\n            label="Conservative",\n            net_saleable_area_m2=net_low,\n            gross_density_capacity_m2=float(gross),\n            sale_price_eur_per_m2=sale_low,\n            construction_cost_eur_per_m2=construction_high,\n            costs=costs,\n            rent_eur_per_m2_month=float(rent_low) if rent_low is not None else None,\n        ),\n        _case_financials(\n            case_id="base",\n            label="Base",\n            net_saleable_area_m2=net_mid,\n            gross_density_capacity_m2=float(gross),\n            sale_price_eur_per_m2=sale_mid,\n            construction_cost_eur_per_m2=construction_mid,\n            costs=costs,\n            rent_eur_per_m2_month=rent_mid,\n        ),\n        _case_financials(\n            case_id="upside",\n            label="Upside",\n            net_saleable_area_m2=net_high,\n            gross_density_capacity_m2=float(gross),\n            sale_price_eur_per_m2=sale_high,\n            construction_cost_eur_per_m2=construction_low,\n            costs=costs,\n            rent_eur_per_m2_month=float(rent_high) if rent_high is not None else None,\n        ),\n    ]\n\n    return {\n        "status": "calculated_from_explicit_assumptions",\n        "selected_programme": selected_id,\n        "selected_programme_label": selected.get("label"),\n        "land_cost_status": land_status,\n        "cases": cases,\n        "profit_range_eur": [\n            min(case["estimated_profit_eur"] for case in cases),\n            max(case["estimated_profit_eur"] for case in cases),\n        ],\n        "profit_on_cost_range_percent": [\n            min(case["profit_on_cost_percent"] for case in cases if case["profit_on_cost_percent"] is not None),\n            max(case["profit_on_cost_percent"] for case in cases if case["profit_on_cost_percent"] is not None),\n        ],\n        "warnings": [\n            "This is a deterministic feasibility model based on explicit assumptions, not a valuation, quantity-surveyor estimate or investment recommendation.",\n            "Costed built area is calculated as density-counted gross capacity multiplied by the entered built-area factor. Basements, parking and other excluded areas are not costed correctly unless that factor is adjusted to the proposed design.",\n            "The V1 finance percentage is a simplified cost allowance, not a monthly cash-flow or IRR model.",\n        ],\n    }\n\n\ndef analyse_parcel_opportunity(\n    parcel_details: Mapping[str, Any],\n    assumptions: Mapping[str, Any] | None = None,\n) -> Dict[str, Any]:\n    normalised = normalise_assumptions(assumptions)\n    parcel = parcel_details.get("parcel") or {}\n    development_potential = parcel_details.get("development_potential") or {}\n    gross = development_potential.get("theoretical_max_floor_area_m2")\n\n    # The current DLS payload does not identify the governing Development Plan.\n    # Municipality/district names are not sufficient to prove ETEK guide scope.\n    etek_scope_confirmed = None\n\n    programmes = build_programme_scenarios(\n        gross,\n        normalised,\n        etek_guide_scope_confirmed=etek_scope_confirmed,\n    )\n    market_context = _market_context(parcel, normalised)\n    financial = build_financial_analysis(gross, programmes, normalised)\n    capacity_confidence = _capacity_confidence(development_potential)\n    overall_confidence = "low"\n    if capacity_confidence == "medium" and market_context["confidence"] == "medium" and financial["status"] == "calculated_from_explicit_assumptions":\n        overall_confidence = "medium"\n\n    capacity_warnings = list(development_potential.get("calculation_warnings") or [])\n    capacity_warnings.extend([\n        "Programme scenarios convert preliminary density-counted floor capacity into indicative net saleable area using explicit efficiency assumptions; they do not prove that the unit mix physically fits the parcel.",\n        "Parking counts are a planning check signal. Access, ramp geometry, stall layout, setbacks, fire strategy and other design constraints can reduce practical unit capacity.",\n    ])\n\n    return {\n        "status": "complete",\n        "opportunity_engine_version": OPPORTUNITY_ENGINE_VERSION,\n        "assumption_set_version": ASSUMPTION_SET_VERSION,\n        "parcel_id": parcel.get("parcel_id"),\n        "capacity": {\n            "gross_density_capacity_m2": gross,\n            "ground_coverage_capacity_m2": development_potential.get("theoretical_max_ground_coverage_m2"),\n            "area_basis_status": development_potential.get("area_basis_status"),\n            "calculation_method": development_potential.get("calculation_method"),\n            "confidence": capacity_confidence,\n            "efficiency_low_percent": normalised["efficiency_low_percent"],\n            "efficiency_high_percent": normalised["efficiency_high_percent"],\n            "net_saleable_area_low_m2": round(float(gross) * normalised["efficiency_low_percent"] / 100.0, 2) if _number(gross) else None,\n            "net_saleable_area_high_m2": round(float(gross) * normalised["efficiency_high_percent"] / 100.0, 2) if _number(gross) else None,\n            "warnings": capacity_warnings,\n        },\n        "programme_scenarios": programmes,\n        "market": market_context,\n        "financial": financial,\n        "assumptions": normalised,\n        "overall_confidence": overall_confidence,\n        "provenance": {\n            "planning_capacity_source": "canonical DLS parcel details + PLANA structured planning-rule layer",\n            "programme_source": "PLANA explicit programme assumptions",\n            "market_source": market_context["source_label"] or "user assumption / no live PLANA market dataset",\n            "financial_source": "PLANA deterministic opportunity engine",\n        },\n    }\n\n\nif __name__ == "__main__":\n    sample = {\n        "parcel": {\n            "parcel_id": 1,\n            "parcel_extent_m2": 1427.616,\n            "price_2021": 500000,\n            "price_2018": 440000,\n            "district": "Nicosia",\n            "municipality": "Nicosia",\n        },\n        "planning_zones": [{"zone": "Ka4", "density_percent": 120, "coverage_percent": 50}],\n        "development_potential": {\n            "theoretical_max_floor_area_m2": 1713.14,\n            "theoretical_max_ground_coverage_m2": 713.81,\n            "area_basis_status": "registered_extent_not_net_area_confirmed",\n            "calculation_method": "single_zone",\n            "calculation_warnings": [],\n        },\n    }\n    result = analyse_parcel_opportunity(sample, {\n        "market": {\n            "sale_price_low_eur_per_m2": 3200,\n            "sale_price_high_eur_per_m2": 3600,\n            "rent_low_eur_per_m2_month": 14,\n            "rent_high_eur_per_m2_month": 16,\n            "source_label": "Example analyst assumption",\n            "source_date": "2026-07-14",\n        },\n        "costs": {\n            "construction_cost_low_eur_per_m2": 1600,\n            "construction_cost_high_eur_per_m2": 1900,\n            "land_cost_eur": 700000,\n            "costed_built_area_factor": 1.15,\n        },\n    })\n    assert len(result["programme_scenarios"]) == 3\n    assert result["financial"]["status"] == "calculated_from_explicit_assumptions"\n    assert len(result["financial"]["cases"]) == 3\n    print("opportunity_engine self-check passed")\n')

# ---- embedded market_sources.py ----
_plana_install_embedded_module('market_sources', '"""PLANA.CY market-source registry and permission-gated source adapters.\n\nThe module deliberately does *not* turn every public property website into an\nunattended scraper. Several major Cyprus portals expressly restrict automated or\ncommercial reuse. PLANA therefore separates:\n\n1. source adapters (technical capability),\n2. operator enablement, and\n3. licence / written-permission attestation.\n\nA permission-gated source is fetched only when BOTH environment variables are\ntrue:\n\n    PLANA_MARKET_<SOURCE_ID>_ENABLED=true\n    PLANA_MARKET_<SOURCE_ID>_LICENSED=true\n\nThis lets PLANA wire BuySell and many other sources now without silently\nviolating source terms in production.\n"""\n\nfrom __future__ import annotations\n\nimport csv\nimport io\nimport json\nimport os\nimport re\nfrom dataclasses import asdict, dataclass\nfrom datetime import date, datetime, timezone\nfrom html import unescape\nfrom html.parser import HTMLParser\nfrom typing import Any, Iterable, Mapping, Sequence\nfrom urllib.parse import urljoin, urlparse\n\nimport httpx\n\n\nMARKET_SOURCE_ENGINE_VERSION = "cy-market-sources-v2"\nDEFAULT_USER_AGENT = "PLANA.CY market research adapter/1.0 (+operator contact required)"\n\n\n@dataclass(frozen=True)\nclass MarketSource:\n    source_id: str\n    name: str\n    source_class: str\n    base_url: str\n    access_mode: str = "licensed_html"\n    permission_required: bool = True\n    terms_url: str | None = None\n    policy_note: str | None = None\n    adapter: str = "generic"\n    seed_urls: tuple[str, ...] = ()\n    detail_path_patterns: tuple[str, ...] = ()\n\n    @property\n    def env_prefix(self) -> str:\n        return "PLANA_MARKET_" + re.sub(r"[^A-Z0-9]+", "_", self.source_id.upper())\n\n\n# Portal / portal-like sources -------------------------------------------------\n# The registry intentionally includes the user\'s broad source universe. Sources\n# with restrictive or unverified commercial reuse terms remain permission-gated.\nSOURCE_REGISTRY: dict[str, MarketSource] = {\n    "buysell": MarketSource(\n        "buysell", "BuySell Cyprus", "portal", "https://www.buysellcyprus.com",\n        terms_url="https://www.buysellcyprus.com/terms-and-conditions",\n        policy_note="Commercial copying/reuse requires prior written consent under published terms.",\n        adapter="buysell",\n        seed_urls=(\n            "https://www.buysellcyprus.com/properties-for-sale/location-limassol/page-1",\n            "https://www.buysellcyprus.com/properties-for-sale/location-nicosia/page-1",\n            "https://www.buysellcyprus.com/properties-for-sale/location-larnaca/page-1",\n            "https://www.buysellcyprus.com/properties-for-sale/location-paphos/page-1",\n            "https://www.buysellcyprus.com/properties-for-sale/location-famagusta/page-1",\n        ),\n        detail_path_patterns=(r"/property-for-sale/.*\\.html$", r"/property-to-rent/.*\\.html$"),\n    ),\n    "bazaraki": MarketSource(\n        "bazaraki", "Bazaraki", "portal", "https://www.bazaraki.com",\n        terms_url="https://www.bazaraki.com/about/rules/",\n        policy_note="Published terms prohibit automated scraping/data extraction without prior written permission.",\n        adapter="generic",\n    ),\n    "index": MarketSource(\n        "index", "INDEX.cy", "portal", "https://index.cy",\n        terms_url="https://index.cy/terms-and-conditions/",\n        policy_note="Published terms prohibit systematic or automated data collection without prior written permission.",\n        adapter="index",\n        seed_urls=(\n            "https://index.cy/for-sale/apartments-flats/nicosia/",\n            "https://index.cy/for-sale/apartments-flats/limassol/",\n            "https://index.cy/for-sale/apartments-flats/larnaca/",\n            "https://index.cy/for-sale/apartments-flats/paphos/",\n            "https://index.cy/for-sale/apartments-flats/famagusta/",\n        ),\n        detail_path_patterns=(r"/sale/\\d+-", r"/rent/\\d+-"),\n    ),\n    "home": MarketSource(\n        "home", "Home.cy", "portal", "https://home.cy",\n        terms_url="https://home.cy/legal/terms",\n        policy_note="Published terms prohibit commercial screen scraping without a written licence agreement.",\n    ),\n    "spitogatos": MarketSource(\n        "spitogatos", "Spitogatos Cyprus", "portal", "https://www.spitogatos.com.cy",\n        policy_note="Commercial reuse permission should be confirmed before enabling automated collection.",\n    ),\n    "dom": MarketSource(\n        "dom", "DOM", "portal", "https://dom.com.cy/en/",\n        policy_note="Commercial reuse permission should be confirmed before enabling automated collection.",\n    ),\n\n    # Limassol / premium developers\n    "bbf": MarketSource("bbf", "BBF", "developer", "https://bbf.com"),\n    "dta": MarketSource("dta", "DTA Group", "developer", "https://dtagroup.com"),\n    "pafilia": MarketSource("pafilia", "Pafilia", "developer", "https://pafilia.com"),\n    "cybarco": MarketSource("cybarco", "Cybarco", "developer", "https://cybarco.com"),\n    "property_gallery": MarketSource("property_gallery", "Property Gallery", "developer", "https://cypruspropertygallery.com"),\n    "askanis": MarketSource("askanis", "Askanis Group", "developer", "https://askanis.com"),\n    "imperio": MarketSource("imperio", "Imperio Properties", "developer", "https://imperioproperties.com"),\n    "crona": MarketSource("crona", "Crona Group", "developer", "https://cronagroup.com"),\n    "zavos": MarketSource("zavos", "D. Zavos Group", "developer", "https://zavos.com"),\n    "ccs_stylianides": MarketSource("ccs_stylianides", "CCS Stylianides Group", "developer", "https://stylianidesgroup.com"),\n\n    # Paphos / West Coast developers\n    "leptos": MarketSource("leptos", "Leptos Estates", "developer", "https://leptosestates.com"),\n    "aristo": MarketSource("aristo", "Aristo Developers", "developer", "https://aristodevelopers.com"),\n    "domenica": MarketSource("domenica", "Domenica Group", "developer", "https://domenicagroup.com"),\n    "korantina": MarketSource("korantina", "Korantina Homes", "developer", "https://korantinahomes.com"),\n    "dnp": MarketSource("dnp", "DNP Property Group", "developer", "https://dnpgroup.com"),\n    "island_blue": MarketSource("island_blue", "Island Blue", "developer", "https://islandbluecyprus.com"),\n\n    # Larnaca / East Coast developers\n    "quality_group": MarketSource("quality_group", "Quality Group", "developer", "https://qualitygroupcyprus.com"),\n    "africanos": MarketSource("africanos", "Africanos Property Developers", "developer", "https://africanosproperties.com"),\n    "livadiotis": MarketSource("livadiotis", "Livadiotis Group", "developer", "https://livadiotis.com"),\n    "plus_properties": MarketSource("plus_properties", "Plus Properties", "developer", "https://pluspropertiescyprus.com"),\n    "karma": MarketSource("karma", "Karma Developers", "developer", "https://karmadevelopers.com.cy"),\n    "giovani": MarketSource("giovani", "Giovani Homes", "developer", "https://giovani.com.cy"),\n    "oikos": MarketSource("oikos", "Oikos Group", "developer", "https://oikos-group.com"),\n\n    # Nicosia developers\n    "cyfield": MarketSource("cyfield", "Cyfield Group", "developer", "https://cyfieldgroup.com"),\n    "rotos": MarketSource("rotos", "Rotos Group", "developer", "https://rotosgroup.com"),\n\n    # Institutional / distressed\n    "altamira": MarketSource("altamira", "Altamira Real Estate", "institutional", "https://altamirarealestate.com.cy"),\n    "gogordian": MarketSource(\n        "gogordian", "GoGordian", "institutional", "https://gogordian.com",\n        terms_url="https://gogordian.com/terms-conditions/",\n        policy_note="Published terms restrict copying/storing site material without prior written consent.",\n        adapter="gogordian",\n        seed_urls=(\n            "https://gogordian.com/properties-for-sale/nicosia/",\n            "https://gogordian.com/properties-for-sale/limassol/",\n            "https://gogordian.com/properties-for-sale/larnaca/",\n            "https://gogordian.com/properties-for-sale/paphos/",\n            "https://gogordian.com/properties-for-sale/famagusta/",\n        ),\n    ),\n    "remu": MarketSource("remu", "REMU Bank of Cyprus", "institutional", "https://remu.bankofcyprus.com"),\n    "aps": MarketSource("aps", "APS Real Estate", "institutional", "https://apsestates.com"),\n\n    # Agencies / valuation-led market evidence\n    "fox": MarketSource("fox", "FOX Smart Estate Agency", "agency", "https://foxrealty.com.cy"),\n    "remax": MarketSource("remax", "RE/MAX Cyprus", "agency", "https://remax.com.cy"),\n    "antonis_loizou": MarketSource("antonis_loizou", "Antonis Loizou & Associates", "agency", "https://aloizou.com.cy"),\n    "danos": MarketSource("danos", "Danos", "agency", "https://danos.com.cy"),\n}\n\n\nTRUE_VALUES = {"1", "true", "yes", "on", "y"}\n\n\ndef _env_true(name: str, default: bool = False) -> bool:\n    value = os.getenv(name)\n    if value is None:\n        return default\n    return value.strip().casefold() in TRUE_VALUES\n\n\ndef source_runtime_status(source: MarketSource) -> dict[str, Any]:\n    enabled = _env_true(f"{source.env_prefix}_ENABLED")\n    licensed = _env_true(f"{source.env_prefix}_LICENSED", default=not source.permission_required)\n    runnable = enabled and (licensed or not source.permission_required)\n    return {\n        "source_id": source.source_id,\n        "name": source.name,\n        "source_class": source.source_class,\n        "base_url": source.base_url,\n        "adapter": source.adapter,\n        "access_mode": source.access_mode,\n        "permission_required": source.permission_required,\n        "enabled": enabled,\n        "licensed_or_permission_confirmed": licensed,\n        "runnable": runnable,\n        "terms_url": source.terms_url,\n        "policy_note": source.policy_note,\n        "enable_env": f"{source.env_prefix}_ENABLED",\n        "licence_env": f"{source.env_prefix}_LICENSED",\n    }\n\n\ndef all_source_statuses() -> list[dict[str, Any]]:\n    return [source_runtime_status(source) for source in SOURCE_REGISTRY.values()]\n\n\ndef require_source_runnable(source_id: str) -> MarketSource:\n    source = SOURCE_REGISTRY.get(source_id)\n    if not source:\n        raise ValueError(f"Unknown market source: {source_id}")\n    status = source_runtime_status(source)\n    if not status["runnable"]:\n        reason = (\n            f"{source.name} is permission-gated. Set {status[\'enable_env\']}=true and "\n            f"{status[\'licence_env\']}=true only after PLANA has the required written "\n            "permission/licence or other lawful data-access agreement."\n        )\n        raise PermissionError(reason)\n    return source\n\n\nclass _TextAndLinksParser(HTMLParser):\n    def __init__(self) -> None:\n        super().__init__(convert_charrefs=True)\n        self.parts: list[str] = []\n        self.links: list[str] = []\n        self._script_type: str | None = None\n        self._script_parts: list[str] = []\n        self.jsonld_blocks: list[str] = []\n\n    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:\n        attrs_dict = dict(attrs)\n        if tag == "a" and attrs_dict.get("href"):\n            self.links.append(attrs_dict["href"] or "")\n        if tag == "script":\n            self._script_type = (attrs_dict.get("type") or "").casefold()\n            self._script_parts = []\n        if tag in {"br", "p", "li", "h1", "h2", "h3", "h4", "div", "section", "article"}:\n            self.parts.append("\\n")\n\n    def handle_endtag(self, tag: str) -> None:\n        if tag == "script":\n            if self._script_type == "application/ld+json" and self._script_parts:\n                self.jsonld_blocks.append("".join(self._script_parts).strip())\n            self._script_type = None\n            self._script_parts = []\n        if tag in {"p", "li", "h1", "h2", "h3", "h4", "div", "section", "article"}:\n            self.parts.append("\\n")\n\n    def handle_data(self, data: str) -> None:\n        if self._script_type is not None:\n            self._script_parts.append(data)\n        else:\n            value = unescape(data).strip()\n            if value:\n                self.parts.append(value)\n\n    @property\n    def text(self) -> str:\n        text = " ".join(self.parts)\n        text = re.sub(r"[ \\t\\f\\v]+", " ", text)\n        text = re.sub(r"\\s*\\n\\s*", "\\n", text)\n        return text.strip()\n\n\ndef parse_html_document(html: str) -> tuple[str, list[str], list[Any]]:\n    parser = _TextAndLinksParser()\n    parser.feed(html or "")\n    jsonld: list[Any] = []\n    for block in parser.jsonld_blocks:\n        try:\n            value = json.loads(block)\n        except Exception:\n            continue\n        jsonld.append(value)\n    return parser.text, parser.links, jsonld\n\n\ndef _number(value: Any) -> float | None:\n    if value in (None, ""):\n        return None\n    if isinstance(value, (int, float)):\n        return float(value)\n    text = str(value).replace("\\xa0", " ")\n    text = re.sub(r"[^0-9,.-]", "", text)\n    if not text:\n        return None\n    # Cyprus portal prices generally use spaces/commas as grouping separators.\n    if text.count(",") == 1 and "." not in text and len(text.split(",")[-1]) <= 2:\n        text = text.replace(",", ".")\n    else:\n        text = text.replace(",", "")\n    try:\n        return float(text)\n    except ValueError:\n        return None\n\n\ndef _first_match(patterns: Sequence[str], text: str, flags: int = re.I | re.S) -> str | None:\n    for pattern in patterns:\n        match = re.search(pattern, text, flags)\n        if match:\n            return match.group(1).strip()\n    return None\n\n\ndef _source_listing_id_from_url(url: str) -> str | None:\n    for pattern in (r"-(\\d{5,})\\.html(?:$|\\?)", r"/(\\d{5,})-", r"(?:id|ref)[=/:-](\\d{4,})"):\n        match = re.search(pattern, url, re.I)\n        if match:\n            return match.group(1)\n    return None\n\n\ndef _transaction_from_url(url: str) -> str | None:\n    path = urlparse(url).path.casefold()\n    if any(token in path for token in ("for-sale", "/sale/", "property-for-sale")):\n        return "sale"\n    if any(token in path for token in ("to-rent", "for-rent", "/rent/", "property-to-rent")):\n        return "rent"\n    return None\n\n\ndef _normalise_property_type(value: str | None) -> str | None:\n    if not value:\n        return None\n    text = value.casefold()\n    if any(x in text for x in ("apartment", "flat", "penthouse", "studio")):\n        return "apartment"\n    if any(x in text for x in ("house", "villa", "bungalow", "maisonette")):\n        return "house"\n    if any(x in text for x in ("plot", "land", "field")):\n        return "land"\n    if "office" in text:\n        return "office"\n    if "shop" in text or "retail" in text:\n        return "shop"\n    if "building" in text:\n        return "building"\n    if "industrial" in text or "warehouse" in text:\n        return "industrial"\n    return text.strip()[:80]\n\n\ndef _development_status(text: str) -> str | None:\n    t = text.casefold()\n    if any(x in t for x in ("newly built", "brand new", "new home", "under construction", "off-plan", "off plan")):\n        return "new_build"\n    if "resale" in t or "used" in t:\n        return "resale"\n    if "incomplete" in t:\n        return "incomplete"\n    return None\n\n\ndef _split_location(value: str | None) -> tuple[str | None, str | None]:\n    if not value:\n        return None, None\n    parts = [part.strip() for part in value.split(",") if part.strip()]\n    if not parts:\n        return None, None\n    locality = parts[0]\n    district = parts[1] if len(parts) > 1 else None\n    return locality, district\n\n\ndef _district_location_line(text: str) -> str | None:\n    candidates = [\n        match.strip()\n        for match in re.findall(\n            r"(?:(?<=\\n)|^)([^\\n]+,\\s*(?:Limassol|Nicosia|Larnaca|Paphos|Famagusta))(?=\\n|$)",\n            text,\n            re.I,\n        )\n    ]\n    if not candidates:\n        return None\n    property_tokens = ("apartment", "flat", "house", "villa", "penthouse", "plot", "land", "for sale", "for rent")\n    clean = [candidate for candidate in candidates if not any(token in candidate.casefold() for token in property_tokens)]\n    return min(clean or candidates, key=len)\n\n\ndef parse_buysell_detail(text: str, url: str) -> dict[str, Any] | None:\n    listing_id = _first_match((r"Listing ID:\\s*(\\d+)", r"\\(ID:\\s*(\\d+)\\)"), text) or _source_listing_id_from_url(url)\n    title = _first_match((r"(?:^|\\n)([^\\n]{0,180}?(?:Apartment|Penthouse|Studio|House|Villa|Land|Plot|Building)[^\\n]{0,180})(?:\\n|$)",), text)\n    location = _district_location_line(text)\n    price = _number(_first_match((r"€\\s*([0-9][0-9\\s,\\.]*)",), text))\n    bedrooms = _number(_first_match((r"Bedrooms:\\s*([0-9]+)", r"^(\\d+)\\s+Bedroom"), text))\n    bathrooms = _number(_first_match((r"Bathrooms:\\s*([0-9]+)",), text))\n    covered_area = _number(_first_match((r"Total covered area:\\s*([0-9.,]+)\\s*sqm", r"Total covered area:\\s*([0-9.,]+)\\s*m2"), text))\n    internal_area = _number(_first_match((r"Internal area\\s*([0-9.,]+)\\s*(?:sq\\.?m\\.?|m2)",), text))\n    area = covered_area or internal_area\n    locality, district = _split_location(location)\n    if not listing_id and not price and not area:\n        return None\n    return {\n        "source_listing_id": listing_id,\n        "transaction_type": _transaction_from_url(url),\n        "property_type": _normalise_property_type(title),\n        "development_status": _development_status(text),\n        "bedrooms": int(bedrooms) if bedrooms is not None else None,\n        "bathrooms": int(bathrooms) if bathrooms is not None else None,\n        "covered_area_m2": area,\n        "asking_price_eur": price,\n        "locality": locality,\n        "district": district,\n        "title": title,\n        "source_url": url,\n    }\n\n\ndef parse_index_detail(text: str, url: str) -> dict[str, Any] | None:\n    listing_id = _first_match((r"\\bID:\\s*(\\d+)",), text) or _source_listing_id_from_url(url)\n    title = _first_match((r"(?:^|\\n)([^\\n]{0,180}?(?:Apartment|Flat|Penthouse|Studio|House|Villa|Land|Plot|Building)[^\\n]{0,180})(?:\\n|$)",), text)\n    area = _number(_first_match((r"\\n([0-9.,]+)\\s*m²(?:\\n|$)", r"Covered area[^0-9]*([0-9.,]+)"), text))\n    price = _number(_first_match((r"€\\s*([0-9][0-9\\s,\\.]*)",), text))\n    price_per_m2 = _number(_first_match((r"Price per m²\\s*€\\s*([0-9][0-9\\s,\\.]*)\\s*/?m²", r"€\\s*([0-9][0-9\\s,\\.]*)\\s*/m(?:²|2)"), text))\n    bedrooms = _number(_first_match((r"^(\\d+) Bedroom", r"Bedrooms[^0-9]*([0-9]+)"), text))\n    location = _first_match((r"for Sale in ([^\\n]+)", r"for Rent in ([^\\n]+)"), title or text)\n    locality, district = _split_location(location)\n    if not listing_id and not price and not area:\n        return None\n    return {\n        "source_listing_id": listing_id,\n        "transaction_type": _transaction_from_url(url),\n        "property_type": _normalise_property_type(title),\n        "development_status": _development_status(text),\n        "bedrooms": int(bedrooms) if bedrooms is not None else None,\n        "covered_area_m2": area,\n        "asking_price_eur": price,\n        "price_per_m2_eur": price_per_m2,\n        "locality": locality,\n        "district": district,\n        "title": title,\n        "source_url": url,\n    }\n\n\ndef parse_gogordian_detail(text: str, url: str) -> dict[str, Any] | None:\n    title = _first_match((r"(?:^|\\n)([^\\n]{0,180}?(?:Apartment|House|Land|Plot|Building|Office|Shop|Industrial)[^\\n]{0,180})(?:\\n|$)",), text)\n    price = _number(_first_match((r"€\\s*([0-9][0-9\\s,\\.]*)",), text))\n    area = _number(_first_match((r"([0-9.,]+)\\s*m\\s*2", r"([0-9.,]+)\\s*m²"), text))\n    reference = _first_match((r"Reference\\s*[:#]?\\s*([A-Z0-9-]+)",), text)\n    district = _first_match((r"\\b(Nicosia|Limassol|Larnaca|Paphos|Famagusta)\\b",), text)\n    if not reference and not price and not area:\n        return None\n    return {\n        "source_listing_id": reference or _source_listing_id_from_url(url),\n        "transaction_type": "sale",\n        "property_type": _normalise_property_type(title),\n        "development_status": _development_status(text),\n        "covered_area_m2": area if _normalise_property_type(title) != "land" else None,\n        "plot_area_m2": area if _normalise_property_type(title) == "land" else None,\n        "asking_price_eur": price,\n        "district": district,\n        "title": title,\n        "source_url": url,\n    }\n\n\ndef parse_generic_property_text(text: str, url: str) -> dict[str, Any] | None:\n    """Best-effort parser for permissioned source detail pages.\n\n    This is deliberately conservative: it requires a recognisable property type\n    plus a price/rent or measurable area. It exists so source-provided detail URLs\n    from the broad registry can be normalised without a new parser per website.\n    Site-specific adapters remain preferred where available.\n    """\n    transaction = _transaction_from_url(url) or _first_match((\n        r"\\b(for sale|sale)\\b", r"\\b(to rent|for rent|rent)\\b",\n    ), text)\n    if transaction:\n        transaction = "rent" if "rent" in transaction.casefold() else "sale"\n\n    title = _first_match((\n        r"(?:^|\\n)([^\\n]{0,160}?(?:Apartment|Flat|Penthouse|Studio|House|Villa|Bungalow|Maisonette|Land|Plot|Field|Office|Shop|Building|Warehouse)[^\\n]{0,160})(?:\\n|$)",\n    ), text)\n    property_type = _normalise_property_type(title or _first_match((\n        r"Property\\s*type\\s*[:\\-]?\\s*([^\\n]{2,80})",\n        r"Type\\s*[:\\-]?\\s*([^\\n]{2,80})",\n    ), text))\n    if not property_type:\n        return None\n\n    price = _number(_first_match((\n        r"(?:Price|Asking price|Sale price)\\s*[:\\-]?\\s*€\\s*([0-9][0-9\\s,\\.]*)",\n        r"€\\s*([0-9][0-9\\s,\\.]*)",\n    ), text))\n    monthly_rent = _number(_first_match((\n        r"(?:Monthly rent|Rent|Rental price)\\s*[:\\-]?\\s*€\\s*([0-9][0-9\\s,\\.]*)",\n        r"€\\s*([0-9][0-9\\s,\\.]*)\\s*(?:/|per)\\s*(?:month|pcm)",\n    ), text))\n    if transaction == "rent" and monthly_rent is None:\n        monthly_rent, price = price, None\n\n    covered_area = _number(_first_match((\n        r"(?:Total covered area|Covered area|Internal area|Living area|Area|Covered)\\s*[:\\-]?\\s*([0-9][0-9.,]*)\\s*(?:m²|m2|sqm|sq\\.?\\s*m)",\n    ), text))\n    plot_area = _number(_first_match((\n        r"(?:Plot area|Land area|Plot size|Land size)\\s*[:\\-]?\\s*([0-9][0-9.,]*)\\s*(?:m²|m2|sqm|sq\\.?\\s*m)",\n    ), text))\n    bedrooms = _number(_first_match((\n        r"Bedrooms?\\s*[:\\-]?\\s*([0-9]+)", r"\\b([0-9]+)\\s*(?:bed|bedroom)\\b",\n    ), text))\n    bathrooms = _number(_first_match((\n        r"Bathrooms?\\s*[:\\-]?\\s*([0-9]+)", r"\\b([0-9]+)\\s*(?:bath|bathroom)\\b",\n    ), text))\n    reference = _first_match((\n        r"(?:Listing ID|Property ID|Reference|Ref\\.?|Property code)\\s*[:#\\-]?\\s*([A-Z0-9_-]{3,40})",\n    ), text) or _source_listing_id_from_url(url)\n    location = _first_match((\n        r"(?:Location|Area|District)\\s*[:\\-]?\\s*([^\\n]{2,120})",\n        r"\\b([^\\n,]{2,80},\\s*(?:Limassol|Nicosia|Larnaca|Paphos|Famagusta))\\b",\n    ), text)\n    locality, district = _split_location(location)\n    if not district:\n        district = _first_match((r"\\b(Nicosia|Limassol|Larnaca|Paphos|Famagusta)\\b",), text)\n\n    measurable_area = plot_area if property_type == "land" else covered_area\n    if price is None and monthly_rent is None and measurable_area is None:\n        return None\n    return {\n        "source_listing_id": reference,\n        "transaction_type": transaction,\n        "property_type": property_type,\n        "development_status": _development_status(text),\n        "bedrooms": int(bedrooms) if bedrooms is not None else None,\n        "bathrooms": int(bathrooms) if bathrooms is not None else None,\n        "covered_area_m2": covered_area if property_type != "land" else None,\n        "plot_area_m2": plot_area or (covered_area if property_type == "land" else None),\n        "asking_price_eur": price,\n        "asking_rent_monthly_eur": monthly_rent,\n        "locality": locality,\n        "district": district,\n        "title": title,\n        "source_url": url,\n        "confidence": "generic_text_extraction",\n    }\n\n\ndef _flatten_jsonld(value: Any) -> Iterable[Mapping[str, Any]]:\n    if isinstance(value, Mapping):\n        yield value\n        graph = value.get("@graph")\n        if isinstance(graph, list):\n            for item in graph:\n                yield from _flatten_jsonld(item)\n    elif isinstance(value, list):\n        for item in value:\n            yield from _flatten_jsonld(item)\n\n\ndef _jsonld_offer_price(record: Mapping[str, Any]) -> tuple[float | None, str | None]:\n    offers = record.get("offers")\n    if isinstance(offers, list) and offers:\n        offers = offers[0]\n    if not isinstance(offers, Mapping):\n        return None, None\n    price = _number(offers.get("price") or offers.get("lowPrice"))\n    currency = str(offers.get("priceCurrency") or "EUR").upper()\n    if price is None or currency not in {"EUR", "€"}:\n        return None, currency\n    return price, "EUR"\n\n\ndef parse_jsonld_observations(jsonld_values: Sequence[Any], url: str) -> list[dict[str, Any]]:\n    observations: list[dict[str, Any]] = []\n    for value in jsonld_values:\n        for record in _flatten_jsonld(value):\n            record_type = record.get("@type")\n            types = {str(x).casefold() for x in (record_type if isinstance(record_type, list) else [record_type]) if x}\n            if not types.intersection({"product", "apartment", "house", "residence", "realestatelisting", "accommodation"}):\n                continue\n            price, currency = _jsonld_offer_price(record)\n            floor_size = record.get("floorSize")\n            if isinstance(floor_size, Mapping):\n                floor_size = floor_size.get("value")\n            address = record.get("address")\n            locality = district = None\n            if isinstance(address, Mapping):\n                locality = address.get("addressLocality")\n                district = address.get("addressRegion")\n            geo = record.get("geo")\n            lat = lon = None\n            if isinstance(geo, Mapping):\n                lat = _number(geo.get("latitude"))\n                lon = _number(geo.get("longitude"))\n            name = str(record.get("name") or "").strip() or None\n            observations.append({\n                "source_listing_id": str(record.get("sku") or record.get("productID") or record.get("identifier") or "").strip() or _source_listing_id_from_url(url),\n                "transaction_type": _transaction_from_url(url),\n                "property_type": _normalise_property_type(name or next(iter(types), None)),\n                "covered_area_m2": _number(floor_size),\n                "asking_price_eur": price if currency == "EUR" else None,\n                "locality": locality,\n                "district": district,\n                "latitude": lat,\n                "longitude": lon,\n                "title": name,\n                "source_url": str(record.get("url") or url),\n            })\n    return observations\n\n\ndef parse_source_html(source_id: str, html: str, url: str) -> list[dict[str, Any]]:\n    source = SOURCE_REGISTRY.get(source_id)\n    if not source:\n        raise ValueError(f"Unknown market source: {source_id}")\n    text, _, jsonld = parse_html_document(html)\n    observations = parse_jsonld_observations(jsonld, url)\n    specific: dict[str, Any] | None = None\n    if source.adapter == "buysell":\n        specific = parse_buysell_detail(text, url)\n    elif source.adapter == "index":\n        specific = parse_index_detail(text, url)\n    elif source.adapter == "gogordian":\n        specific = parse_gogordian_detail(text, url)\n    if specific:\n        observations.append(specific)\n    if source.adapter == "generic":\n        generic = parse_generic_property_text(text, url)\n        if generic:\n            observations.append(generic)\n    elif not observations:\n        generic = parse_generic_property_text(text, url)\n        if generic:\n            observations.append(generic)\n    return [enrich_source_metadata(source, item) for item in observations]\n\n\ndef enrich_source_metadata(source: MarketSource, observation: Mapping[str, Any]) -> dict[str, Any]:\n    result = dict(observation)\n    result.update({\n        "source": source.name,\n        "source_id": source.source_id,\n        "source_class": source.source_class,\n        "retrieved_at": datetime.now(timezone.utc).isoformat(),\n        "source_terms_url": source.terms_url,\n        "source_adapter": source.adapter,\n        "source_engine_version": MARKET_SOURCE_ENGINE_VERSION,\n    })\n    return result\n\n\ndef discover_detail_urls(source_id: str, html: str, page_url: str) -> list[str]:\n    source = SOURCE_REGISTRY.get(source_id)\n    if not source:\n        raise ValueError(f"Unknown market source: {source_id}")\n    _, links, _ = parse_html_document(html)\n    fallback_patterns = (\n        r"/property(?:/|[-_])", r"/properties(?:/|[-_])", r"/listing(?:/|[-_])",\n        r"/sale/", r"/rent/", r"/real-estate/", r"/development(?:/|[-_])", r"/project(?:/|[-_])",\n    )\n    patterns = [re.compile(pattern, re.I) for pattern in (source.detail_path_patterns or fallback_patterns)]\n    discovered: list[str] = []\n    seen: set[str] = set()\n    base_host = urlparse(source.base_url).netloc.casefold()\n    for href in links:\n        absolute = urljoin(page_url, href)\n        parsed = urlparse(absolute)\n        if parsed.netloc.casefold() != base_host:\n            continue\n        if patterns and not any(pattern.search(parsed.path) for pattern in patterns):\n            continue\n        clean = absolute.split("#", 1)[0]\n        if clean not in seen:\n            seen.add(clean)\n            discovered.append(clean)\n    return discovered\n\n\nasync def fetch_source_url(source_id: str, url: str, *, timeout_seconds: float = 30.0) -> str:\n    source = require_source_runnable(source_id)\n    parsed = urlparse(url)\n    if parsed.netloc.casefold() != urlparse(source.base_url).netloc.casefold():\n        raise ValueError(f"URL host does not match {source.name}: {url}")\n    headers = {\n        "User-Agent": os.getenv("PLANA_MARKET_USER_AGENT", DEFAULT_USER_AGENT),\n        "Accept": "text/html,application/xhtml+xml",\n        "Accept-Language": "en,el;q=0.8",\n    }\n    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True, headers=headers) as client:\n        response = await client.get(url)\n    response.raise_for_status()\n    return response.text\n\n\nasync def collect_urls(source_id: str, urls: Sequence[str], *, delay_seconds: float = 1.0) -> list[dict[str, Any]]:\n    import asyncio\n\n    require_source_runnable(source_id)\n    observations: list[dict[str, Any]] = []\n    for index, url in enumerate(urls):\n        html = await fetch_source_url(source_id, url)\n        observations.extend(parse_source_html(source_id, html, url))\n        if delay_seconds > 0 and index < len(urls) - 1:\n            await asyncio.sleep(delay_seconds)\n    return observations\n\n\nasync def collect_seed_pages(\n    source_id: str,\n    *,\n    max_detail_pages: int = 50,\n    delay_seconds: float = 1.0,\n) -> list[dict[str, Any]]:\n    import asyncio\n\n    source = require_source_runnable(source_id)\n    detail_urls: list[str] = []\n    seen: set[str] = set()\n    for seed in source.seed_urls:\n        html = await fetch_source_url(source_id, seed)\n        for url in discover_detail_urls(source_id, html, seed):\n            if url not in seen:\n                seen.add(url)\n                detail_urls.append(url)\n                if len(detail_urls) >= max_detail_pages:\n                    break\n        if len(detail_urls) >= max_detail_pages:\n            break\n        if delay_seconds > 0:\n            await asyncio.sleep(delay_seconds)\n    return await collect_urls(source_id, detail_urls[:max_detail_pages], delay_seconds=delay_seconds)\n\n\ndef parse_csv_feed(source_id: str, content: str) -> list[dict[str, Any]]:\n    source = SOURCE_REGISTRY.get(source_id)\n    if not source:\n        raise ValueError(f"Unknown market source: {source_id}")\n    rows = csv.DictReader(io.StringIO(content))\n    return [enrich_source_metadata(source, row) for row in rows]\n\n\ndef parse_json_feed(source_id: str, content: str) -> list[dict[str, Any]]:\n    source = SOURCE_REGISTRY.get(source_id)\n    if not source:\n        raise ValueError(f"Unknown market source: {source_id}")\n    value = json.loads(content)\n    if isinstance(value, Mapping):\n        value = value.get("results") or value.get("items") or value.get("properties") or [value]\n    if not isinstance(value, list):\n        raise ValueError("JSON feed must be a list or contain results/items/properties.")\n    return [enrich_source_metadata(source, row) for row in value if isinstance(row, Mapping)]\n\n\ndef source_registry_payload() -> dict[str, Any]:\n    statuses = all_source_statuses()\n    return {\n        "engine_version": MARKET_SOURCE_ENGINE_VERSION,\n        "source_count": len(statuses),\n        "runnable_count": sum(1 for row in statuses if row["runnable"]),\n        "sources": statuses,\n    }\n\n\nif __name__ == "__main__":\n    payload = source_registry_payload()\n    print(json.dumps(payload, indent=2, ensure_ascii=False))\n')

# ---- embedded market_engine.py ----
_plana_install_embedded_module('market_engine', '"""Market observation normalisation, comparable selection and range analysis."""\n\nfrom __future__ import annotations\n\nimport hashlib\nimport json\nimport math\nimport statistics\nfrom datetime import datetime, timezone\nfrom typing import Any, Iterable, Mapping, Sequence\n\nfrom market_sources import MARKET_SOURCE_ENGINE_VERSION, SOURCE_REGISTRY, source_registry_payload\n\n\nMARKET_ENGINE_VERSION = "cy-market-analysis-v4"\n\n\ndef _number(value: Any) -> float | None:\n    if value in (None, ""):\n        return None\n    try:\n        number = float(value)\n    except (TypeError, ValueError):\n        text = str(value).replace("\\xa0", " ")\n        import re\n        text = re.sub(r"[^0-9,.-]", "", text)\n        if not text:\n            return None\n        text = text.replace(",", "")\n        try:\n            number = float(text)\n        except ValueError:\n            return None\n    if not math.isfinite(number):\n        return None\n    return number\n\n\ndef _text(value: Any) -> str | None:\n    if value is None:\n        return None\n    value = str(value).strip()\n    return value or None\n\n\ndef _date_text(value: Any) -> str | None:\n    if value in (None, ""):\n        return None\n    if isinstance(value, datetime):\n        value = value.astimezone(timezone.utc).isoformat()\n    return str(value)\n\n\ndef _age_days(value: Any, *, now: datetime | None = None) -> float | None:\n    text = _date_text(value)\n    if not text:\n        return None\n    try:\n        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))\n    except ValueError:\n        return None\n    if parsed.tzinfo is None:\n        parsed = parsed.replace(tzinfo=timezone.utc)\n    current = now or datetime.now(timezone.utc)\n    return max(0.0, (current - parsed.astimezone(timezone.utc)).total_seconds() / 86400.0)\n\n\ndef _normalise_transaction(value: Any) -> str | None:\n    text = (_text(value) or "").casefold()\n    if any(token in text for token in ("sale", "sell", "buy", "for sale")):\n        return "sale"\n    if any(token in text for token in ("rent", "lease", "to let")):\n        return "rent"\n    return text or None\n\n\ndef _normalise_property_type(value: Any) -> str | None:\n    text = (_text(value) or "").casefold()\n    if not text:\n        return None\n    if any(x in text for x in ("apartment", "flat", "penthouse", "studio")):\n        return "apartment"\n    if any(x in text for x in ("house", "villa", "bungalow", "maisonette")):\n        return "house"\n    if any(x in text for x in ("plot", "land", "field")):\n        return "land"\n    if "office" in text:\n        return "office"\n    if "shop" in text or "retail" in text:\n        return "shop"\n    if "building" in text:\n        return "building"\n    if "industrial" in text or "warehouse" in text:\n        return "industrial"\n    return text[:80]\n\n\ndef normalise_market_observation(raw: Mapping[str, Any]) -> dict[str, Any] | None:\n    source_id = (_text(raw.get("source_id")) or "unknown").casefold()\n    source = SOURCE_REGISTRY.get(source_id)\n    source_name = _text(raw.get("source")) or (source.name if source else source_id)\n    source_class = _text(raw.get("source_class")) or (source.source_class if source else "unknown")\n    transaction = _normalise_transaction(raw.get("transaction_type") or raw.get("listing_type"))\n    property_type = _normalise_property_type(raw.get("property_type") or raw.get("type") or raw.get("title"))\n\n    asking_price = _number(raw.get("asking_price_eur") or raw.get("price_eur") or raw.get("price"))\n    asking_rent = _number(raw.get("asking_rent_monthly_eur") or raw.get("rent_eur_monthly") or raw.get("monthly_rent"))\n    covered_area = _number(raw.get("covered_area_m2") or raw.get("area_m2") or raw.get("covered_area"))\n    plot_area = _number(raw.get("plot_area_m2") or raw.get("land_area_m2") or raw.get("plot_area"))\n    price_per_m2 = _number(raw.get("price_per_m2_eur") or raw.get("asking_price_per_m2_eur"))\n    rent_per_m2 = _number(raw.get("rent_per_m2_month_eur"))\n\n    if transaction == "rent" and asking_rent is None and asking_price is not None:\n        asking_rent, asking_price = asking_price, None\n    if transaction == "sale" and asking_price is None and asking_rent is not None:\n        asking_price, asking_rent = asking_rent, None\n\n    area_for_rate = covered_area if property_type != "land" else plot_area\n    if price_per_m2 is None and asking_price is not None and area_for_rate and area_for_rate > 0:\n        price_per_m2 = asking_price / area_for_rate\n    if rent_per_m2 is None and asking_rent is not None and covered_area and covered_area > 0:\n        rent_per_m2 = asking_rent / covered_area\n\n    listing_id = _text(raw.get("source_listing_id") or raw.get("listing_id") or raw.get("reference"))\n    source_url = _text(raw.get("source_url") or raw.get("url"))\n    latitude = _number(raw.get("latitude") or raw.get("lat"))\n    longitude = _number(raw.get("longitude") or raw.get("lon") or raw.get("lng"))\n    bedrooms = _number(raw.get("bedrooms"))\n    bathrooms = _number(raw.get("bathrooms"))\n\n    observation = {\n        "source": source_name,\n        "source_id": source_id,\n        "source_class": source_class,\n        "source_listing_id": listing_id,\n        "source_url": source_url,\n        "transaction_type": transaction,\n        "property_type": property_type,\n        "development_status": _text(raw.get("development_status") or raw.get("condition")),\n        "bedrooms": int(bedrooms) if bedrooms is not None else None,\n        "bathrooms": int(bathrooms) if bathrooms is not None else None,\n        "covered_area_m2": round(covered_area, 2) if covered_area is not None else None,\n        "plot_area_m2": round(plot_area, 2) if plot_area is not None else None,\n        "asking_price_eur": round(asking_price, 2) if asking_price is not None else None,\n        "asking_rent_monthly_eur": round(asking_rent, 2) if asking_rent is not None else None,\n        "price_per_m2_eur": round(price_per_m2, 2) if price_per_m2 is not None else None,\n        "rent_per_m2_month_eur": round(rent_per_m2, 2) if rent_per_m2 is not None else None,\n        "latitude": latitude,\n        "longitude": longitude,\n        "district": _text(raw.get("district")),\n        "municipality": _text(raw.get("municipality")),\n        "locality": _text(raw.get("locality") or raw.get("location")),\n        "planning_zone": _text(raw.get("planning_zone")),\n        "title": _text(raw.get("title")),\n        "first_seen_at": _date_text(raw.get("first_seen_at") or raw.get("retrieved_at")),\n        "last_seen_at": _date_text(raw.get("last_seen_at") or raw.get("retrieved_at")),\n        "price_changed_at": _date_text(raw.get("price_changed_at")),\n        "original_price_eur": _number(raw.get("original_price_eur")),\n        "current_price_eur": _number(raw.get("current_price_eur") or asking_price),\n        "confidence": (_text(raw.get("confidence")) or "observed_asking_price").casefold(),\n        "source_adapter": _text(raw.get("source_adapter")),\n        "source_engine_version": _text(raw.get("source_engine_version")) or MARKET_SOURCE_ENGINE_VERSION,\n    }\n    if not any((listing_id, source_url, asking_price, asking_rent)):\n        return None\n    observation["observation_key"] = observation_key(observation)\n    return observation\n\n\ndef observation_key(observation: Mapping[str, Any]) -> str:\n    source_id = _text(observation.get("source_id")) or "unknown"\n    listing_id = _text(observation.get("source_listing_id"))\n    if listing_id:\n        transaction = _normalise_transaction(observation.get("transaction_type")) or "unknown"\n        return f"{source_id}:{transaction}:{listing_id}"\n    payload = {\n        "source_id": source_id,\n        "url": _text(observation.get("source_url")),\n        "transaction": _normalise_transaction(observation.get("transaction_type")),\n        "type": _normalise_property_type(observation.get("property_type")),\n        "price": round(_number(observation.get("asking_price_eur")) or _number(observation.get("asking_rent_monthly_eur")) or 0, -2),\n        "area": round(_number(observation.get("covered_area_m2")) or _number(observation.get("plot_area_m2")) or 0, 0),\n        "beds": _number(observation.get("bedrooms")),\n        "locality": (_text(observation.get("locality")) or "").casefold(),\n    }\n    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]\n    return f"{source_id}:hash:{digest}"\n\n\ndef _cross_source_duplicate_signature(observation: Mapping[str, Any]) -> tuple[Any, ...]:\n    price = _number(observation.get("asking_price_eur")) or _number(observation.get("asking_rent_monthly_eur"))\n    area = _number(observation.get("covered_area_m2")) or _number(observation.get("plot_area_m2"))\n    locality = (_text(observation.get("locality")) or _text(observation.get("municipality")) or "").casefold()\n    return (\n        _normalise_transaction(observation.get("transaction_type")),\n        _normalise_property_type(observation.get("property_type")),\n        round(price or 0, -3),\n        round(area or 0, 0),\n        int(_number(observation.get("bedrooms")) or 0),\n        locality,\n    )\n\n\ndef dedupe_observations(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:\n    by_key: dict[str, dict[str, Any]] = {}\n    for row in rows:\n        normalised = normalise_market_observation(row)\n        if normalised:\n            by_key[normalised["observation_key"]] = normalised\n\n    # Cross-source dedupe suppresses likely syndicated copies. Keep the record\n    # with the richest data rather than counting the same property repeatedly.\n    by_signature: dict[tuple[Any, ...], dict[str, Any]] = {}\n    for row in by_key.values():\n        signature = _cross_source_duplicate_signature(row)\n        existing = by_signature.get(signature)\n        if existing is None:\n            by_signature[signature] = row\n            continue\n        richness = sum(value not in (None, "") for value in row.values())\n        existing_richness = sum(value not in (None, "") for value in existing.values())\n        if richness > existing_richness:\n            by_signature[signature] = row\n    return list(by_signature.values())\n\n\ndef haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:\n    radius = 6371.0088\n    phi1, phi2 = math.radians(lat1), math.radians(lat2)\n    dphi = math.radians(lat2 - lat1)\n    dlambda = math.radians(lon2 - lon1)\n    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2\n    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))\n\n\ndef _percentile(values: Sequence[float], p: float) -> float | None:\n    if not values:\n        return None\n    ordered = sorted(values)\n    if len(ordered) == 1:\n        return ordered[0]\n    rank = (len(ordered) - 1) * p\n    lower = math.floor(rank)\n    upper = math.ceil(rank)\n    if lower == upper:\n        return ordered[lower]\n    weight = rank - lower\n    return ordered[lower] * (1 - weight) + ordered[upper] * weight\n\n\ndef _mad_filter(values: Sequence[float], threshold: float = 3.5) -> list[float]:\n    if len(values) < 5:\n        return list(values)\n    median = statistics.median(values)\n    deviations = [abs(value - median) for value in values]\n    mad = statistics.median(deviations)\n    if mad == 0:\n        return list(values)\n    return [value for value in values if 0.6745 * abs(value - median) / mad <= threshold]\n\n\ndef _segment_label(row: Mapping[str, Any]) -> str:\n    source_class = (_text(row.get("source_class")) or "unknown").casefold()\n    status = (_text(row.get("development_status")) or "").casefold()\n    if source_class == "developer":\n        return "developer_new_build"\n    if source_class == "institutional":\n        return "institutional"\n    if source_class == "agency":\n        return "agency"\n    if status == "new_build":\n        return "portal_new_build"\n    if status == "resale":\n        return "portal_resale"\n    return "portal_general"\n\n\ndef _range_summary(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, Any] | None:\n    values = [float(value) for row in rows if (value := _number(row.get(field))) is not None and value > 0]\n    values = _mad_filter(values)\n    if not values:\n        return None\n    low = _percentile(values, 0.20)\n    high = _percentile(values, 0.80)\n    median = statistics.median(values)\n    return {\n        "low": round(low or median, 2),\n        "high": round(high or median, 2),\n        "median": round(median, 2),\n        "count": len(values),\n        "method": "MAD outlier filter + 20th/80th percentile range",\n    }\n\n\ndef _confidence(comp_count: int, source_count: int, median_distance_km: float | None, geo_count: int) -> str:\n    if comp_count >= 30 and source_count >= 3 and (median_distance_km is None or median_distance_km <= 3) and geo_count >= min(10, comp_count):\n        return "high"\n    if comp_count >= 10 and source_count >= 2:\n        return "medium"\n    return "low"\n\n\ndef _select_primary_market_rows(\n    rows: Sequence[Mapping[str, Any]],\n    *,\n    transaction_type: str,\n) -> tuple[list[dict[str, Any]], str]:\n    """Choose the evidence used for PLANA\'s primary range.\n\n    Portal/agency observations describe the normal asking market. Developer stock\n    remains a separate premium/new-build segment unless normal-market evidence is\n    too thin. Institutional/distressed observations never enter the primary\n    apartment asking-price range; they remain a separate market signal.\n    """\n    metric = "price_per_m2_eur" if transaction_type == "sale" else "rent_per_m2_month_eur"\n    usable = [\n        dict(row)\n        for row in rows\n        if row.get("transaction_type") == transaction_type and _number(row.get(metric))\n    ]\n    normal_market = [\n        row for row in usable\n        if (_text(row.get("source_class")) or "").casefold() in {"portal", "agency"}\n    ]\n    developer = [\n        row for row in usable\n        if (_text(row.get("source_class")) or "").casefold() == "developer"\n    ]\n\n    if len(normal_market) >= 5:\n        return normal_market, "portal_agency"\n    if normal_market and developer:\n        return normal_market + developer, "blended_normal_and_developer"\n    if normal_market:\n        return normal_market, "thin_portal_agency"\n    if developer:\n        return developer, "developer_only"\n\n    # Unknown/other sources may still be source-provided feeds. Institutional\n    # stock is intentionally excluded from the primary range.\n    other = [\n        row for row in usable\n        if (_text(row.get("source_class")) or "").casefold() != "institutional"\n    ]\n    return other, "other_non_institutional" if other else "no_primary_evidence"\n\n\ndef analyse_market_observations(\n    parcel_details: Mapping[str, Any],\n    observations: Iterable[Mapping[str, Any]],\n    *,\n    centroid_lat: float | None = None,\n    centroid_lon: float | None = None,\n    property_type: str = "apartment",\n    max_distance_km: float = 8.0,\n    max_age_days: int = 180,\n) -> dict[str, Any]:\n    parcel = parcel_details.get("parcel") or {}\n    district = (_text(parcel.get("district")) or "").casefold()\n    municipality = (_text(parcel.get("municipality")) or "").casefold()\n    locality = (_text(parcel.get("quarter")) or "").casefold()\n    rows = dedupe_observations(observations)\n    now = datetime.now(timezone.utc)\n    stale_observation_count = 0\n    unknown_recency_count = 0\n\n    relevant: list[dict[str, Any]] = []\n    for row in rows:\n        row_status = (_text(row.get("status")) or "active").casefold()\n        if row_status not in {"active", "available", "for sale", "for rent"}:\n            continue\n        age_days = _age_days(row.get("last_seen_at") or row.get("first_seen_at"), now=now)\n        if age_days is not None and age_days > max_age_days:\n            stale_observation_count += 1\n            continue\n        if age_days is None:\n            unknown_recency_count += 1\n        if _normalise_property_type(row.get("property_type")) == "apartment":\n            bedrooms = _number(row.get("bedrooms"))\n            if bedrooms is not None and not (1 <= bedrooms <= 3):\n                continue\n        if _normalise_property_type(row.get("property_type")) != _normalise_property_type(property_type):\n            continue\n        row_district = (_text(row.get("district")) or "").casefold()\n        row_municipality = (_text(row.get("municipality")) or "").casefold()\n        row_locality = (_text(row.get("locality")) or "").casefold()\n\n        distance = None\n        lat, lon = _number(row.get("latitude")), _number(row.get("longitude"))\n        if centroid_lat is not None and centroid_lon is not None and lat is not None and lon is not None:\n            distance = haversine_km(centroid_lat, centroid_lon, lat, lon)\n            if distance > max_distance_km:\n                continue\n        else:\n            # Fallback location gate for non-geocoded observations.\n            locality_match = bool(locality and row_locality and locality in row_locality)\n            municipality_match = bool(municipality and (municipality in row_municipality or municipality in row_locality))\n            district_match = bool(district and district in row_district)\n            if not (locality_match or municipality_match or district_match):\n                continue\n        item = dict(row)\n        item["distance_km"] = round(distance, 3) if distance is not None else None\n        item["age_days"] = round(age_days, 1) if age_days is not None else None\n        item["segment"] = _segment_label(item)\n        relevant.append(item)\n\n    sale_rows = [row for row in relevant if row.get("transaction_type") == "sale" and _number(row.get("price_per_m2_eur"))]\n    rent_rows = [row for row in relevant if row.get("transaction_type") == "rent" and _number(row.get("rent_per_m2_month_eur"))]\n    primary_sale_rows, sale_range_basis = _select_primary_market_rows(relevant, transaction_type="sale")\n    primary_rent_rows, rent_range_basis = _select_primary_market_rows(relevant, transaction_type="rent")\n\n    sale_range = _range_summary(primary_sale_rows, "price_per_m2_eur")\n    rent_range = _range_summary(primary_rent_rows, "rent_per_m2_month_eur")\n    primary_rows = primary_sale_rows or primary_rent_rows\n    distances = [float(row["distance_km"]) for row in primary_rows if row.get("distance_km") is not None]\n    median_distance = round(statistics.median(distances), 2) if distances else None\n    source_ids = sorted({row.get("source_id") for row in relevant if row.get("source_id")})\n    primary_source_ids = sorted({row.get("source_id") for row in primary_rows if row.get("source_id")})\n    confidence = _confidence(len(primary_sale_rows), len(primary_source_ids), median_distance, len(distances))\n\n    segments: dict[str, dict[str, Any]] = {}\n    for segment in sorted({row["segment"] for row in relevant}):\n        segment_rows = [row for row in relevant if row["segment"] == segment]\n        segments[segment] = {\n            "observation_count": len(segment_rows),\n            "sale_price_per_m2": _range_summary([row for row in segment_rows if row.get("transaction_type") == "sale"], "price_per_m2_eur"),\n            "rent_per_m2_month": _range_summary([row for row in segment_rows if row.get("transaction_type") == "rent"], "rent_per_m2_month_eur"),\n            "sources": sorted({row.get("source") for row in segment_rows if row.get("source")}),\n        }\n\n    top_comparables = sorted(\n        primary_sale_rows,\n        key=lambda row: (\n            row.get("distance_km") is None,\n            row.get("distance_km") if row.get("distance_km") is not None else 9999,\n            abs((_number(row.get("bedrooms")) or 2) - 2),\n        ),\n    )[:12]\n    compact_comps = [\n        {\n            "source": row.get("source"),\n            "source_id": row.get("source_id"),\n            "source_class": row.get("source_class"),\n            "source_listing_id": row.get("source_listing_id"),\n            "source_url": row.get("source_url"),\n            "property_type": row.get("property_type"),\n            "development_status": row.get("development_status"),\n            "bedrooms": row.get("bedrooms"),\n            "covered_area_m2": row.get("covered_area_m2"),\n            "asking_price_eur": row.get("asking_price_eur"),\n            "price_per_m2_eur": row.get("price_per_m2_eur"),\n            "locality": row.get("locality"),\n            "municipality": row.get("municipality"),\n            "district": row.get("district"),\n            "distance_km": row.get("distance_km"),\n            "age_days": row.get("age_days"),\n            "segment": row.get("segment"),\n        }\n        for row in top_comparables\n    ]\n\n    warnings: list[str] = []\n    if not sale_range:\n        warnings.append(f"No sufficient {_normalise_property_type(property_type) or \'property\'} sale observations matched this parcel context.")\n    if sale_range and sale_range["count"] < 10:\n        warnings.append("Sale range is based on fewer than 10 usable asking-price observations.")\n    if sale_range_basis == "blended_normal_and_developer":\n        warnings.append("Normal portal/agency evidence was thin, so developer new-build observations were blended into the primary sale range. Developer stock is still shown separately by segment.")\n    elif sale_range_basis == "developer_only":\n        warnings.append("The primary sale range is developer-only because no usable portal/agency asking-price observations matched the parcel context.")\n    if sale_range:\n        warnings.append("Market ranges use asking-price observations, not completed DLS transaction prices.")\n    if len(primary_source_ids) < 2 and primary_rows:\n        warnings.append("Primary comparable evidence is concentrated in a single source and may contain source-specific bias.")\n    if primary_rows and not distances:\n        warnings.append("Most primary matched observations are locality/district matched because precise listing coordinates were unavailable.")\n    if stale_observation_count:\n        warnings.append(f"{stale_observation_count} observations older than {max_age_days} days were excluded from the market analysis.")\n    if unknown_recency_count and relevant:\n        warnings.append(f"{unknown_recency_count} loaded observations had no usable last-seen date; refresh cadence could not be verified for those records.")\n\n    return {\n        "engine_version": MARKET_ENGINE_VERSION,\n        "source_engine_version": MARKET_SOURCE_ENGINE_VERSION,\n        "evidence_status": "automatic_market_observations" if relevant else "no_matching_market_observations",\n        "property_type": _normalise_property_type(property_type),\n        "parcel_context": {\n            "district": parcel.get("district"),\n            "municipality": parcel.get("municipality"),\n            "locality": parcel.get("quarter"),\n            "centroid_lat": centroid_lat,\n            "centroid_lon": centroid_lon,\n            "max_distance_km": max_distance_km,\n            "max_age_days": max_age_days,\n        },\n        "observation_count_loaded": len(rows),\n        "stale_observation_count_excluded": stale_observation_count,\n        "unknown_recency_count": unknown_recency_count,\n        "relevant_observation_count": len(relevant),\n        "sale_observation_count": len(sale_rows),\n        "rent_observation_count": len(rent_rows),\n        "primary_sale_observation_count": len(primary_sale_rows),\n        "primary_rent_observation_count": len(primary_rent_rows),\n        "source_count": len(source_ids),\n        "source_ids": source_ids,\n        "primary_source_count": len(primary_source_ids),\n        "primary_source_ids": primary_source_ids,\n        "median_distance_km": median_distance,\n        "confidence": confidence,\n        "sale_range_basis": sale_range_basis,\n        "rent_range_basis": rent_range_basis,\n        "sale_price_per_m2": sale_range,\n        "rent_per_m2_month": rent_range,\n        "segments": segments,\n        "top_comparables": compact_comps,\n        "warnings": warnings,\n    }\n\n\ndef merge_automatic_market_assumptions(\n    assumptions: Mapping[str, Any] | None,\n    market_analysis: Mapping[str, Any],\n) -> dict[str, Any]:\n    merged = json.loads(json.dumps(dict(assumptions or {}), default=str))\n    market = dict(merged.get("market") or {})\n    sale_range = market_analysis.get("sale_price_per_m2") or {}\n    rent_range = market_analysis.get("rent_per_m2_month") or {}\n\n    if market.get("sale_price_low_eur_per_m2") in (None, "") and sale_range.get("low") is not None:\n        market["sale_price_low_eur_per_m2"] = sale_range.get("low")\n    if market.get("sale_price_high_eur_per_m2") in (None, "") and sale_range.get("high") is not None:\n        market["sale_price_high_eur_per_m2"] = sale_range.get("high")\n    if market.get("rent_low_eur_per_m2_month") in (None, "") and rent_range.get("low") is not None:\n        market["rent_low_eur_per_m2_month"] = rent_range.get("low")\n    if market.get("rent_high_eur_per_m2_month") in (None, "") and rent_range.get("high") is not None:\n        market["rent_high_eur_per_m2_month"] = rent_range.get("high")\n    if not market.get("source_label") and market_analysis.get("relevant_observation_count"):\n        primary_count = market_analysis.get("primary_sale_observation_count") or market_analysis.get("primary_rent_observation_count") or 0\n        primary_sources = market_analysis.get("primary_source_count") or 0\n        basis = str(market_analysis.get("sale_range_basis") or market_analysis.get("rent_range_basis") or "market_evidence").replace("_", " ")\n        market["source_label"] = (\n            f"PLANA automatic asking-market range · {primary_count} primary comparables · "\n            f"{primary_sources} primary sources · {basis}"\n        )\n    if not market.get("source_date") and market_analysis.get("relevant_observation_count"):\n        market["source_date"] = datetime.now(timezone.utc).date().isoformat()\n    if market_analysis.get("relevant_observation_count"):\n        market["confidence"] = market_analysis.get("confidence") or "low"\n    merged["market"] = market\n    return merged\n\n\ndef source_status_summary() -> dict[str, Any]:\n    return source_registry_payload()\n\n\nif __name__ == "__main__":\n    # Small deterministic smoke test.\n    parcel = {"parcel": {"district": "Limassol", "municipality": "Germasogeia", "quarter": "Potamos Germasogeias"}}\n    rows = [\n        {"source_id": "buysell", "source": "BuySell Cyprus", "source_class": "portal", "source_listing_id": "1", "transaction_type": "sale", "property_type": "apartment", "covered_area_m2": 100, "asking_price_eur": 500000, "district": "Limassol", "locality": "Potamos Germasogeias"},\n        {"source_id": "index", "source": "INDEX.cy", "source_class": "portal", "source_listing_id": "2", "transaction_type": "sale", "property_type": "apartment", "covered_area_m2": 80, "asking_price_eur": 360000, "district": "Limassol", "locality": "Germasogeia"},\n    ]\n    result = analyse_market_observations(parcel, rows)\n    assert result["sale_price_per_m2"]\n    assert result["sale_observation_count"] == 2\n    print(json.dumps(result, indent=2, ensure_ascii=False))\n')

# ---- embedded market_ingest.py ----
_plana_install_embedded_module('market_ingest', '"""CLI market-observation ingestion for PLANA.CY.\n\nExamples:\n\n    python market_ingest.py --source buysell --seed --max-pages 50 --dry-run\n    python market_ingest.py --source buysell --urls-file buysell_urls.txt\n    python market_ingest.py --source bbf --csv bbf_feed.csv\n    python market_ingest.py --source index --json index_feed.json\n    python market_ingest.py --source pafilia --discover-url https://example-permissioned-list-page --max-pages 50\n\nWebsite collection is permission-gated by market_sources.py. CSV/JSON feed import is\navailable for licensed exports/data partnerships and does not fetch the website.\n"""\n\nfrom __future__ import annotations\n\nimport argparse\nimport asyncio\nimport json\nimport os\nfrom datetime import datetime, timezone\nfrom pathlib import Path\nfrom typing import Any, Iterable, Mapping\n\nfrom dotenv import load_dotenv\nfrom supabase import create_client\n\nfrom market_engine import normalise_market_observation\nfrom market_sources import (\n    SOURCE_REGISTRY,\n    collect_seed_pages,\n    collect_urls,\n    discover_detail_urls,\n    fetch_source_url,\n    parse_csv_feed,\n    parse_json_feed,\n    source_registry_payload,\n)\n\n\ndef _load_file(path: str) -> str:\n    return Path(path).read_text(encoding="utf-8-sig")\n\n\ndef _normalise_many(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:\n    output: list[dict[str, Any]] = []\n    for row in rows:\n        normalised = normalise_market_observation(row)\n        if normalised:\n            output.append(normalised)\n    return output\n\n\ndef _merge_observation_history(client: Any, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:\n    """Preserve first-seen state and record asking-price changes on repeated ingestion."""\n    now = datetime.now(timezone.utc).isoformat()\n    keys = [row["observation_key"] for row in batch if row.get("observation_key")]\n    existing_rows: list[dict[str, Any]] = []\n    if keys:\n        response = (\n            client.table("market_observations")\n            .select("observation_key,first_seen_at,last_seen_at,price_changed_at,original_price_eur,current_price_eur,asking_price_eur")\n            .in_("observation_key", keys)\n            .execute()\n        )\n        existing_rows = response.data or []\n    existing_by_key = {row.get("observation_key"): row for row in existing_rows}\n\n    merged: list[dict[str, Any]] = []\n    for input_row in batch:\n        row = dict(input_row)\n        existing = existing_by_key.get(row.get("observation_key")) or {}\n        row["first_seen_at"] = existing.get("first_seen_at") or row.get("first_seen_at") or now\n        row["last_seen_at"] = now\n        row["updated_at"] = now\n\n        current_sale_price = row.get("asking_price_eur")\n        previous_sale_price = existing.get("current_price_eur") or existing.get("asking_price_eur")\n        if current_sale_price not in (None, ""):\n            row["original_price_eur"] = existing.get("original_price_eur") or previous_sale_price or current_sale_price\n            row["current_price_eur"] = current_sale_price\n            try:\n                changed = previous_sale_price not in (None, "") and float(previous_sale_price) != float(current_sale_price)\n            except (TypeError, ValueError):\n                changed = str(previous_sale_price) != str(current_sale_price)\n            row["price_changed_at"] = now if changed else existing.get("price_changed_at") or row.get("price_changed_at")\n        merged.append(row)\n    return merged\n\n\ndef _upsert_supabase(rows: list[dict[str, Any]]) -> int:\n    if not rows:\n        return 0\n    raise RuntimeError(\n        "Direct market writes are disabled in production V8. "\n        "Use PLANA DATA so observation/history writes are atomic and lease-fenced. "\n        "The market-ingest CLI remains available with --dry-run for feed validation."\n    )\n\n\nasync def _run(args: argparse.Namespace) -> int:\n    if args.list_sources:\n        print(json.dumps(source_registry_payload(), indent=2, ensure_ascii=False))\n        return 0\n\n    if args.source not in SOURCE_REGISTRY:\n        raise SystemExit(f"Unknown source {args.source!r}. Use --list-sources.")\n\n    rows: list[dict[str, Any]] = []\n    if args.csv:\n        rows.extend(parse_csv_feed(args.source, _load_file(args.csv)))\n    if args.json:\n        rows.extend(parse_json_feed(args.source, _load_file(args.json)))\n    if args.url:\n        rows.extend(await collect_urls(args.source, args.url, delay_seconds=args.delay))\n    if args.urls_file:\n        urls = [line.strip() for line in _load_file(args.urls_file).splitlines() if line.strip() and not line.lstrip().startswith("#")]\n        rows.extend(await collect_urls(args.source, urls, delay_seconds=args.delay))\n    if args.discover_url:\n        discovered: list[str] = []\n        seen: set[str] = set()\n        for page_url in args.discover_url:\n            html = await fetch_source_url(args.source, page_url)\n            for detail_url in discover_detail_urls(args.source, html, page_url):\n                if detail_url not in seen:\n                    seen.add(detail_url)\n                    discovered.append(detail_url)\n                    if len(discovered) >= args.max_pages:\n                        break\n            if len(discovered) >= args.max_pages:\n                break\n        rows.extend(await collect_urls(args.source, discovered[:args.max_pages], delay_seconds=args.delay))\n    if args.seed:\n        rows.extend(await collect_seed_pages(args.source, max_detail_pages=args.max_pages, delay_seconds=args.delay))\n\n    normalised = _normalise_many(rows)\n    print(json.dumps({\n        "source": args.source,\n        "parsed_rows": len(rows),\n        "normalised_rows": len(normalised),\n        "sample": normalised[:5],\n    }, indent=2, ensure_ascii=False, default=str))\n\n    if args.dry_run:\n        return 0\n    saved = _upsert_supabase(normalised)\n    print(f"Saved/upserted {saved} market observations.")\n    return 0\n\n\ndef main() -> int:\n    load_dotenv()\n    parser = argparse.ArgumentParser(description="Ingest licensed market observations into PLANA.CY")\n    parser.add_argument("--source", default="buysell", help="Market source id")\n    parser.add_argument("--url", action="append", help="Fetch and parse one licensed source detail URL; repeatable")\n    parser.add_argument("--urls-file", help="Text file containing licensed source detail URLs")\n    parser.add_argument("--discover-url", action="append", help="Permissioned listing/index page to scan for likely detail URLs; repeatable")\n    parser.add_argument("--seed", action="store_true", help="Discover detail URLs from the source\'s configured seed pages")\n    parser.add_argument("--max-pages", type=int, default=50, help="Maximum detail pages when --seed is used")\n    parser.add_argument("--delay", type=float, default=1.0, help="Delay between website requests in seconds")\n    parser.add_argument("--csv", help="Import a source-provided/licensed CSV feed")\n    parser.add_argument("--json", help="Import a source-provided/licensed JSON feed")\n    parser.add_argument("--dry-run", action="store_true", help="Parse and print without writing to Supabase")\n    parser.add_argument("--list-sources", action="store_true", help="Show all configured sources and runtime status")\n    args = parser.parse_args()\n    return asyncio.run(_run(args))\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n')

del _plana_install_embedded_module
del _plana_types

# ============================================================
# MAIN PLANA.CY APPLICATION
# ============================================================

import json
import asyncio
import time
import math
import os
import re
import sys
import unicodedata
import httpx
from collections import Counter, OrderedDict
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client

from planning_rules import (
    RULE_CATALOG,
    RULE_ENGINE_VERSION,
    calculate_zoned_capacity,
    compact_rule_context,
    evaluate_parcel_rules,
)
from opportunity_engine import (
    ASSUMPTION_SET_VERSION,
    OPPORTUNITY_ENGINE_VERSION,
    analyse_parcel_opportunity,
)
from market_engine import (
    MARKET_ENGINE_VERSION,
    analyse_market_observations,
    merge_automatic_market_assumptions,
    source_status_summary,
)
from market_sources import MARKET_SOURCE_ENGINE_VERSION


EMBEDDING_MODEL = "text-embedding-3-small"
ANSWER_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-5.6-terra")
QUERY_EXPANSION_MODEL = os.getenv("OPENAI_QUERY_EXPANSION_MODEL", ANSWER_MODEL)
VERIFY_MODEL = os.getenv("OPENAI_VERIFY_MODEL", ANSWER_MODEL)

SEMANTIC_THRESHOLD = 0.18
SEMANTIC_MATCH_COUNT_PER_QUERY = 16
FINAL_HITS = 40
LEXICAL_CANDIDATES = 40
ADJACENT_EXPANSION_TOP_N = 5
MAX_CONTEXT_CHARS = 26000
RERANK_CANDIDATES = 40
DIRECT_RULE_CANDIDATES = 12
RERANK_TOP_N = 10
RERANK_SNIPPET_CHARS = 1400
RERANK_MODEL = os.getenv("OPENAI_RERANK_MODEL", ANSWER_MODEL)
PLANNING_ANALYSIS_MODEL = os.getenv("OPENAI_PLANNING_ANALYSIS_MODEL", ANSWER_MODEL)

SEMANTIC_WEIGHT = 0.64
LEXICAL_WEIGHT = 0.26
RECENCY_WEIGHT = 0.06
PRIORITY_WEIGHT = 0.04


GREEK_STOPWORDS = {
    "και", "ή", "η", "ο", "οι", "το", "τα", "του", "της", "των", "τον", "την",
    "σε", "στο", "στη", "στην", "στον", "στα", "στις", "στους", "με", "από",
    "για", "ως", "που", "ποιο", "ποια", "ποιος", "ποιες", "ποιοι", "τι", "πως",
    "πώς", "είναι", "ισχύει", "ισχυει", "ένα", "μια", "ένας", "αν", "να", "θα",
    "δεν", "τουλάχιστον", "μέχρι", "πάνω", "κάτω", "μεταξύ", "πρέπει",
}

ENGLISH_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "does", "do", "what", "how", "can", "must", "should", "from",
}


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing {name} in .env")
    return value


def normalize_text(text: str) -> str:
    text = text or ""
    text = unicodedata.normalize("NFD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tokenize(text: str) -> List[str]:
    normalized = normalize_text(text)
    return re.findall(r"[0-9a-zα-ω]+", normalized)


def meaningful_terms(question: str) -> List[str]:
    terms = []
    seen = set()

    for token in tokenize(question):
        if token in GREEK_STOPWORDS or token in ENGLISH_STOPWORDS:
            continue
        if len(token) < 3:
            continue
        if token not in seen:
            terms.append(token)
            seen.add(token)

    return terms


def token_root(token: str) -> str:
    """
    Light inflection-tolerant prefix matching.
    This is intentionally conservative: never shorter than 5 characters.
    """
    if len(token) >= 10:
        return token[:-3]
    if len(token) >= 7:
        return token[:-2]
    if len(token) >= 6:
        return token[:-1]
    return token


def parse_year(value: Any) -> int:
    if not value:
        return 0
    try:
        return int(str(value)[:4])
    except Exception:
        return 0


def recency_score(row: Dict[str, Any]) -> float:
    year = parse_year(row.get("publication_date"))
    if year >= 2025:
        return 1.0
    if year >= 2024:
        return 0.8
    if year >= 2020:
        return 0.5
    if year > 0:
        return 0.2
    return 0.0


def priority_score(row: Dict[str, Any]) -> float:
    try:
        return min(float(row.get("authority_priority") or 0.0), 100.0) / 100.0
    except Exception:
        return 0.0


def embed_text(text: str, openai_client: OpenAI) -> List[float]:
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def run_semantic_search(
    query_text: str,
    openai_client: OpenAI,
    supabase: Any,
) -> List[Dict[str, Any]]:
    query_embedding = embed_text(query_text, openai_client)

    response = supabase.rpc(
        "match_kb_chunks",
        {
            "query_embedding": query_embedding,
            "match_threshold": SEMANTIC_THRESHOLD,
            "match_count": SEMANTIC_MATCH_COUNT_PER_QUERY,
        },
    ).execute()

    return response.data or []


def batch_semantic_candidates(
    query_texts: List[str],
    openai_client: OpenAI,
    supabase: Any,
) -> List[Dict[str, Any]]:
    """Run one batched embedding request, then semantic search for each query."""
    queries = [q.strip() for q in query_texts if q and q.strip()]
    if not queries:
        return []

    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=queries,
    )
    embeddings = [item.embedding for item in response.data]

    groups: List[List[Dict[str, Any]]] = []
    for embedding in embeddings:
        result = supabase.rpc(
            "match_kb_chunks",
            {
                "query_embedding": embedding,
                "match_threshold": SEMANTIC_THRESHOLD,
                "match_count": SEMANTIC_MATCH_COUNT_PER_QUERY,
            },
        ).execute()
        groups.append(result.data or [])

    return merge_unique_rows(groups)



def contains_greek(text: str) -> bool:
    return bool(re.search(r"[Α-ΩΆΈΉΊΌΎΏα-ωάέήίϊΐόύϋΰώ]", text or ""))


def generate_greek_search_query(
    question: str,
    openai_client: OpenAI,
) -> str:
    """
    Convert a non-Greek user question into a concise Greek planning-regulation
    search query. This is for retrieval only, not for answering the user.
    """
    if contains_greek(question):
        return question

    instructions = """
You translate user questions into concise Greek search queries for a Cyprus
planning-regulations knowledge base.

Rules:
1. Do NOT answer the question.
2. Preserve the exact technical meaning.
3. Use Cyprus planning terminology where appropriate.
4. Prefer terms likely to appear in Greek planning documents, for example:
   - building coefficient -> συντελεστής δόμησης
   - coverage -> ποσοστό κάλυψης
   - basement -> υπόγειο
   - auxiliary building -> βοηθητική οικοδομή
   - setback / boundary distance -> απόσταση από τα σύνορα
   - parking space -> χώρος στάθμευσης
5. Return ONLY the Greek search query, with no quotation marks or explanation.
"""

    response = openai_client.responses.create(
        model=QUERY_EXPANSION_MODEL,
        instructions=instructions.strip(),
        input=question.strip(),
    )

    greek_query = (response.output_text or "").strip()

    if not greek_query:
        return question

    return greek_query


def merge_unique_rows(
    row_groups: List[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[Any, Any, str], Dict[str, Any]] = {}

    for rows in row_groups:
        for row in rows:
            key = (
                row.get("document_id"),
                row.get("page_number"),
                row.get("content") or "",
            )

            existing = merged.get(key)
            if not existing:
                merged[key] = row
                continue

            # Keep the strongest values seen across original-language and
            # Greek-expanded retrieval runs.
            for field in (
                "similarity",
                "lexical_score",
                "raw_lexical_score",
                "direct_rule_score",
                "direct_score",
            ):
                new_value = float(row.get(field) or 0.0)
                old_value = float(existing.get(field) or 0.0)
                if new_value > old_value:
                    existing[field] = row.get(field)

    return list(merged.values())


def semantic_candidates(
    question: str,
    openai_client: OpenAI,
    supabase: Any,
) -> List[Dict[str, Any]]:
    queries = [
        question,
        (
            f"{question}\n"
            "Εξαιρέσεις, προϋποθέσεις, ειδικές περιπτώσεις, "
            "δεν προσμετράται, εξαιρείται, μερική προσμέτρηση, ανάλογα με τη χρήση."
        ),
        (
            f"{question}\n"
            "Ισχύουσες νεότερες πρόνοιες 2026, Εντολή 4/2026, "
            "τρέχοντες κανόνες, μεταβατικές ή καταργημένες πρόνοιες και ειδικές εξαιρέσεις."
        ),
    ]

    merged: Dict[Tuple[Any, Any, str], Dict[str, Any]] = {}

    for query_text in queries:
        for row in run_semantic_search(query_text, openai_client, supabase):
            key = (
                row.get("document_id"),
                row.get("page_number"),
                row.get("content") or "",
            )
            existing = merged.get(key)
            if not existing or float(row.get("similarity") or 0.0) > float(existing.get("similarity") or 0.0):
                merged[key] = row

    return list(merged.values())



def fetch_all_chunks_with_metadata(supabase: Any) -> List[Dict[str, Any]]:
    """Page KB rows explicitly so PostgREST result limits cannot silently truncate retrieval."""
    page_size = max(100, min(1000, int(os.getenv("PLANA_KB_PAGE_SIZE", "1000"))))
    max_chunks = max(page_size, int(os.getenv("PLANA_KB_MAX_CHUNKS", "50000")))

    def paged(table: str, columns: str, max_rows: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while offset < max_rows:
            response = (
                supabase.table(table)
                .select(columns)
                .range(offset, min(offset + page_size - 1, max_rows - 1))
                .execute()
            )
            batch = response.data or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += len(batch)
        return rows

    docs = {
        row["id"]: row
        for row in paged(
            "kb_documents",
            "id,title,publisher,publication_date,version,authority_priority",
            max_chunks,
        )
    }
    chunks = paged(
        "kb_chunks",
        "id,document_id,page_number,section_title,content",
        max_chunks,
    )
    if len(chunks) >= max_chunks:
        print(f"PLANA KB warning: hit PLANA_KB_MAX_CHUNKS={max_chunks}; lexical retrieval is capped", file=sys.stderr)
    return [
        {**chunk, **docs.get(chunk.get("document_id"), {}), "document_id": chunk.get("document_id")}
        for chunk in chunks
    ]



def lexical_candidates(
    question: str,
    all_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    terms = meaningful_terms(question)
    if not terms:
        return []

    roots = {term: token_root(term) for term in terms}

    tokenized_rows = []
    document_frequency = Counter()

    for row in all_rows:
        section_text = row.get("section_title") or ""
        combined_text = f"{section_text}\n{row.get('content') or ''}"
        content_tokens = tokenize(combined_text)
        token_set = set(content_tokens)

        matched_terms = set()
        for term in terms:
            root = roots[term]
            if term in token_set or any(tok.startswith(root) for tok in token_set):
                matched_terms.add(term)

        for term in matched_terms:
            document_frequency[term] += 1

        tokenized_rows.append((row, content_tokens, token_set))

    total_docs = max(len(all_rows), 1)
    normalized_question = normalize_text(question)

    scored = []

    for row, content_tokens, token_set in tokenized_rows:
        score = 0.0
        exact_matches = 0
        root_matches = 0

        for term in terms:
            root = roots[term]
            df = document_frequency.get(term, 0)
            idf = math.log((total_docs + 1) / (df + 1)) + 1.0

            if term in token_set:
                score += 1.0 * idf
                exact_matches += 1
            elif any(tok.startswith(root) for tok in token_set):
                score += 0.72 * idf
                root_matches += 1

        normalized_content = normalize_text(row.get("content") or "")
        normalized_section = normalize_text(row.get("section_title") or "")

        # Strong bonus when the query term appears in the detected section title.
        section_tokens = set(tokenize(row.get("section_title") or ""))
        section_match_count = 0
        for term in terms:
            root = roots[term]
            if term in section_tokens or any(tok.startswith(root) for tok in section_tokens):
                section_match_count += 1
                score += 3.5

        # Phrase bonus when several important question words occur near each other.
        matched_count = exact_matches + root_matches
        coverage = matched_count / max(len(terms), 1)
        score += coverage * 2.0

        if section_match_count:
            score += min(section_match_count, 3) * 1.5

        # Small exact-phrase bonus.
        if len(normalized_question) >= 8 and normalized_question in normalized_content:
            score += 4.0

        if score > 0:
            scored.append({**row, "raw_lexical_score": score})

    scored.sort(key=lambda r: float(r.get("raw_lexical_score") or 0.0), reverse=True)

    if not scored:
        return []

    max_score = float(scored[0]["raw_lexical_score"]) or 1.0
    for row in scored:
        row["lexical_score"] = float(row["raw_lexical_score"]) / max_score

    return scored[:LEXICAL_CANDIDATES]



DOMAIN_RELATION_EXPANSIONS = {
    # Questions like "Μετρά ... στον συντελεστή δόμησης;"
    "μετρ": [
        "υπολογισ", "λογιζ", "προσμετρ", "συνυπολογ", "εξαιρ",
    ],
    "λογιζ": [
        "υπολογισ", "μετρ", "προσμετρ", "συνυπολογ", "εξαιρ",
    ],
    "προσμετρ": [
        "υπολογισ", "λογιζ", "μετρ", "συνυπολογ", "εξαιρ",
    ],
    "εξαιρ": [
        "υπολογισ", "λογιζ", "μετρ", "προσμετρ", "συνυπολογ",
    ],
}


def rootify(token: str) -> str:
    token = normalize_text(token)
    if len(token) >= 10:
        return token[:-3]
    if len(token) >= 7:
        return token[:-2]
    if len(token) >= 6:
        return token[:-1]
    return token


def direct_rule_candidates(
    question: str,
    all_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    High-precision rule matching over the entire KB.

    This is designed to catch passages that literally encode the asked legal
    relationship, even when vector similarity or recency boosts rank them lower.

    Example:
    "Μετρά το υπόγειο στον συντελεστή δόμησης;"
    should strongly favor a chunk containing:
    "ΥΠΟΓΕΙΟ ... Εξαιρείται από τον υπολογισμό του συντελεστή δόμησης ..."
    """
    q_tokens = meaningful_terms(question)
    q_roots = [rootify(t) for t in q_tokens]

    # Expand relational verbs into legal-document wording.
    relation_roots = set()
    for root in q_roots:
        for trigger, expansions in DOMAIN_RELATION_EXPANSIONS.items():
            if root.startswith(trigger) or trigger.startswith(root):
                relation_roots.update(expansions)

    # Important concept roots are the non-stopword roots from the question.
    concept_roots = [r for r in q_roots if len(r) >= 4]

    scored = []

    for row in all_rows:
        combined = normalize_text(
            f"{row.get('section_title') or ''}\n{row.get('content') or ''}"
        )

        # Root-level concept matches.
        concept_hits = sum(1 for root in concept_roots if root in combined)
        relation_hits = sum(1 for root in relation_roots if root in combined)

        # Strong phrase/concept bonuses for planning-coefficient questions.
        building_coeff_bonus = 0.0
        if "συντελεστ" in combined and "δομησ" in combined:
            building_coeff_bonus = 4.0

        section_bonus = 0.0
        section_norm = normalize_text(row.get("section_title") or "")
        if any(root in section_norm for root in concept_roots):
            section_bonus = 3.0

        # Require at least meaningful concept overlap.
        if concept_hits == 0:
            continue

        score = (
            concept_hits * 2.2
            + relation_hits * 2.5
            + building_coeff_bonus
            + section_bonus
        )

        # Big bonus when multiple question concepts co-occur with a legal relation.
        if concept_hits >= 2 and relation_hits >= 1:
            score += 7.0
        if concept_hits >= 3 and relation_hits >= 1:
            score += 4.0

        # Direct exclusion/counting language is especially valuable.
        if "εξαιρ" in combined and "υπολογισ" in combined:
            score += 4.0
        if "λογιζ" in combined or "προσμετρ" in combined or "συνυπολογ" in combined:
            score += 2.0

        if score > 0:
            scored.append({**row, "direct_rule_score": score})

    scored.sort(
        key=lambda r: float(r.get("direct_rule_score") or 0.0),
        reverse=True,
    )

    return scored[:DIRECT_RULE_CANDIDATES]


def merge_and_rerank(
    semantic_rows: List[Dict[str, Any]],
    lexical_rows: List[Dict[str, Any]],
    direct_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[Any, Any, str], Dict[str, Any]] = {}

    def key_for(row: Dict[str, Any]) -> Tuple[Any, Any, str]:
        return (
            row.get("document_id"),
            row.get("page_number"),
            row.get("content") or "",
        )

    for row in semantic_rows:
        key = key_for(row)
        merged[key] = {
            **row,
            "semantic_score": float(row.get("similarity") or 0.0),
            "lexical_score": 0.0,
        }

    for row in lexical_rows:
        key = key_for(row)
        if key in merged:
            merged[key]["lexical_score"] = float(row.get("lexical_score") or 0.0)
        else:
            merged[key] = {
                **row,
                "similarity": 0.0,
                "semantic_score": 0.0,
                "lexical_score": float(row.get("lexical_score") or 0.0),
            }

    # Force direct rule matches into the candidate pool.
    max_direct = max(
        [float(r.get("direct_rule_score") or 0.0) for r in direct_rows] or [1.0]
    )
    for row in direct_rows:
        key = key_for(row)
        normalized_direct = float(row.get("direct_rule_score") or 0.0) / max_direct
        if key in merged:
            merged[key]["direct_rule_score"] = float(row.get("direct_rule_score") or 0.0)
            merged[key]["direct_score"] = normalized_direct
        else:
            merged[key] = {
                **row,
                "similarity": 0.0,
                "semantic_score": 0.0,
                "lexical_score": 0.0,
                "direct_score": normalized_direct,
            }

    rows = list(merged.values())

    for row in rows:
        semantic = float(row.get("semantic_score") or 0.0)
        lexical = float(row.get("lexical_score") or 0.0)
        direct = float(row.get("direct_score") or 0.0)

        row["hybrid_score"] = (
            0.50 * semantic
            + 0.20 * lexical
            + 0.22 * direct
            + RECENCY_WEIGHT * recency_score(row)
            + PRIORITY_WEIGHT * priority_score(row)
        )

    rows.sort(
        key=lambda r: (
            float(r.get("hybrid_score") or 0.0),
            float(r.get("semantic_score") or 0.0),
            float(r.get("lexical_score") or 0.0),
        ),
        reverse=True,
    )

    return rows[:FINAL_HITS]



def llm_rerank_candidates(
    question: str,
    candidates: List[Dict[str, Any]],
    openai_client: OpenAI,
) -> List[Dict[str, Any]]:
    """
    Second-stage semantic/legal reranker.

    Hybrid retrieval is good at recall, but can still rank a merely related newer
    passage above an older passage that directly states the rule. This reranker
    sees the actual candidate text and prioritizes direct answerability first.
    """
    pool = candidates[:RERANK_CANDIDATES]
    if not pool:
        return []

    blocks = []
    for i, row in enumerate(pool, start=1):
        content = (row.get("content") or "").strip()
        if len(content) > RERANK_SNIPPET_CHARS:
            content = content[:RERANK_SNIPPET_CHARS] + "…"

        blocks.append(
            f"CANDIDATE {i}\n"
            f"Document: {row.get('title')}\n"
            f"Publication date: {row.get('publication_date')}\n"
            f"Page: {row.get('page_number')}\n"
            f"Section: {row.get('section_title') or 'Unknown'}\n"
            f"Hybrid score: {float(row.get('hybrid_score') or 0.0):.4f}\n"
            f"Text:\n{content}\n"
        )

    instructions = """
You rerank source excerpts for a Cyprus planning-regulations question.

Rank by DIRECT ANSWERABILITY first:
1. A passage that explicitly states the rule asked about ranks above a passage that is merely related.
2. A passage containing the exact legal relationship in the question ranks highly even if it is older.
3. Newer sources matter for current applicability, but do not bury an older passage that directly states the rule; include both when the newer source may qualify it.
4. Prefer passages containing conditions, exceptions, exclusions, and definitions that materially affect the answer.
5. Do not answer the user's question. Only rank candidate indices.

Return ONLY valid JSON in this exact shape:
{"ranked_indices":[1,2,3,4,5,6,7,8,9,10]}

Use at most 10 indices. Do not include indices that are not useful.
"""

    prompt = (
        f"QUESTION:\n{question}\n\n"
        "CANDIDATES:\n\n"
        + "\n\n".join(blocks)
    )

    try:
        response = openai_client.responses.create(
            model=RERANK_MODEL,
            instructions=instructions.strip(),
            input=prompt.strip(),
        )
        text = response.output_text.strip()

        # Be tolerant if the model accidentally wraps JSON in prose/code fences.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return pool[:RERANK_TOP_N]

        data = json.loads(text[start:end + 1])
        indices = data.get("ranked_indices", [])

        reranked = []
        seen = set()
        for idx in indices:
            try:
                pos = int(idx) - 1
            except Exception:
                continue
            if 0 <= pos < len(pool) and pos not in seen:
                reranked.append(pool[pos])
                seen.add(pos)
            if len(reranked) >= RERANK_TOP_N:
                break

        # Fill any remaining slots from the original hybrid order.
        for pos, row in enumerate(pool):
            if pos not in seen:
                reranked.append(row)
                seen.add(pos)
            if len(reranked) >= RERANK_TOP_N:
                break

        return reranked

    except Exception as exc:
        print(f"Reranker warning: {exc}")
        print("Falling back to hybrid ranking.")
        return pool[:RERANK_TOP_N]


def expand_with_adjacent_pages(
    rows: List[Dict[str, Any]],
    supabase: Any,
) -> List[Dict[str, Any]]:
    expanded: List[Dict[str, Any]] = []
    seen: set[Tuple[Any, Any, str]] = set()

    for row in rows:
        key = (
            row.get("document_id"),
            row.get("page_number"),
            row.get("content") or "",
        )
        if key not in seen:
            expanded.append({**row, "context_type": "hybrid_hit"})
            seen.add(key)

    for hit in rows[:ADJACENT_EXPANSION_TOP_N]:
        document_id = hit.get("document_id")
        page = hit.get("page_number")
        if not document_id or not page:
            continue

        start_page = max(1, int(page) - 1)
        end_page = int(page) + 1

        response = (
            supabase.table("kb_chunks")
            .select("document_id,page_number,section_title,content")
            .eq("document_id", document_id)
            .gte("page_number", start_page)
            .lte("page_number", end_page)
            .order("page_number")
            .execute()
        )

        for neighbor in response.data or []:
            key = (
                neighbor.get("document_id"),
                neighbor.get("page_number"),
                neighbor.get("content") or "",
            )
            if key in seen:
                continue

            expanded.append(
                {
                    **neighbor,
                    "title": hit.get("title"),
                    "publisher": hit.get("publisher"),
                    "publication_date": hit.get("publication_date"),
                    "version": hit.get("version"),
                    "authority_priority": hit.get("authority_priority"),
                    "similarity": None,
                    "semantic_score": None,
                    "lexical_score": None,
                    "hybrid_score": None,
                    "context_type": "adjacent_page_context",
                }
            )
            seen.add(key)

    return expanded


def expand_with_adjacent_pages_local(
    rows: List[Dict[str, Any]],
    all_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Add neighboring pages from the in-memory KB instead of extra Supabase calls."""
    expanded: List[Dict[str, Any]] = []
    seen: set[Tuple[Any, Any, str]] = set()

    def add_row(row: Dict[str, Any], context_type: str) -> None:
        key = (
            row.get("document_id"),
            row.get("page_number"),
            row.get("content") or "",
        )
        if key in seen:
            return
        expanded.append({**row, "context_type": context_type})
        seen.add(key)

    for row in rows:
        add_row(row, "hybrid_hit")

    by_document: Dict[Any, List[Dict[str, Any]]] = {}
    for row in all_rows:
        by_document.setdefault(row.get("document_id"), []).append(row)

    for hit in rows[:ADJACENT_EXPANSION_TOP_N]:
        document_id = hit.get("document_id")
        page = hit.get("page_number")
        if not document_id or not page:
            continue
        try:
            page_number = int(page)
        except Exception:
            continue

        for neighbor in by_document.get(document_id, []):
            try:
                neighbor_page = int(neighbor.get("page_number") or 0)
            except Exception:
                continue
            if page_number - 1 <= neighbor_page <= page_number + 1:
                add_row(neighbor, "adjacent_page_context")

    return expanded


def greek_zone_variant(zone_code: str) -> str:
    """Add a Greek-script search variant for DLS zone codes such as Ka4 -> Κα4."""
    char_map = {
        "a": "α", "b": "β", "g": "γ", "d": "δ", "e": "ε",
        "z": "ζ", "h": "η", "i": "ι", "k": "κ", "l": "λ",
        "m": "μ", "n": "ν", "x": "ξ", "o": "ο", "p": "π",
        "r": "ρ", "s": "σ", "t": "τ", "y": "υ", "u": "υ",
        "f": "φ", "c": "χ", "w": "ω",
    }
    result = []
    for char in zone_code:
        lower = char.casefold()
        greek = char_map.get(lower)
        if greek is None:
            result.append(char)
        elif char.isupper():
            result.append(greek.upper())
        else:
            result.append(greek)
    return "".join(result)


def build_parcel_planning_queries(parcel_details: Dict[str, Any]) -> List[str]:
    parcel = parcel_details.get("parcel") or {}
    zones = parcel_details.get("planning_zones") or []
    zone_codes = [str(z.get("zone")).strip() for z in zones if z.get("zone")]
    zone_terms = []
    for code in zone_codes:
        greek_variant = greek_zone_variant(code)
        zone_terms.append(
            f"{code} / {greek_variant}" if greek_variant != code else code
        )
    zone_text = ", ".join(zone_terms) or "μη καθορισμένη πολεοδομική ζώνη"
    municipality = parcel.get("municipality") or parcel.get("district") or "Κύπρο"

    base = (
        f"Γήπεδο στην {municipality}, πολεοδομική ζώνη {zone_text}. "
        "Ποιες ισχύουσες πολεοδομικές πρόνοιες, ειδικές ρυθμίσεις, εξαιρέσεις "
        "και προϋποθέσεις επηρεάζουν ουσιωδώς την αναπτυξιακή δυνατότητα του τεμαχίου;"
    )
    capacity = (
        f"Πολεοδομική ζώνη {zone_text}: Εντολή 4/2026, συντελεστής δόμησης, ποσοστό κάλυψης, "
        "μέγιστο ύψος και όροφοι, χώροι που δεν προσμετρώνται ή προσμετρώνται μερικώς, "
        "υπόγεια, βοηθητικές οικοδομές, κίνητρα, εξαιρέσεις και ειδικές πρόνοιες."
    )
    practical = (
        f"Πολεοδομική ζώνη {zone_text}: ισχύουσες πρόνοιες μετά την Εντολή 4/2026, "
        "επιτρεπόμενες χρήσεις, οικιστική ανάπτυξη, απαιτήσεις χώρων στάθμευσης, "
        "βασικές αποστάσεις από σύνορα από το εφαρμοστέο Σχέδιο Ανάπτυξης, πρόσβαση και άλλοι κανόνες "
        "που μπορούν να μειώσουν την πρακτικά αξιοποιήσιμη ανάπτυξη ενός τεμαχίου."
    )
    return [base, capacity, practical]


def build_numbered_planning_context(
    rows: List[Dict[str, Any]],
) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    primary = [r for r in rows if r.get("context_type") == "hybrid_hit"]
    adjacent = [r for r in rows if r.get("context_type") == "adjacent_page_context"]
    adjacent.sort(
        key=lambda r: (
            str(r.get("title") or ""),
            int(r.get("page_number") or 0),
        )
    )
    ordered = primary + adjacent

    blocks: List[str] = []
    source_map: Dict[str, Dict[str, Any]] = {}
    total_chars = 0
    for index, row in enumerate(ordered, start=1):
        source_id = f"S{index}"
        title = row.get("title") or "Unknown document"
        page = row.get("page_number") or "?"
        content = (row.get("content") or "").strip()
        block = (
            f"[{source_id}]\n"
            f"Document: {title}\n"
            f"Page: {page}\n"
            f"Publication date: {row.get('publication_date') or 'unknown'}\n"
            f"Section: {row.get('section_title') or 'Μη καθορισμένη'}\n"
            f"Context type: {row.get('context_type') or 'source'}\n"
            f"Text:\n{content}\n"
        )
        if total_chars + len(block) > MAX_CONTEXT_CHARS:
            break
        blocks.append(block)
        total_chars += len(block)
        source_map[source_id] = {
            "source_id": source_id,
            "title": title,
            "page_number": row.get("page_number"),
            "section_title": row.get("section_title"),
            "publication_date": (
                str(row.get("publication_date"))
                if row.get("publication_date")
                else None
            ),
            "version": row.get("version"),
            "publisher": row.get("publisher"),
        }

    return "\n".join(blocks), source_map


def parse_json_object(text: str) -> Dict[str, Any]:
    value = (text or "").strip()
    start = value.find("{")
    end = value.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model did not return a JSON object.")
    parsed = json.loads(value[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Model JSON response must be an object.")
    return parsed


def normalise_planning_analysis(
    raw: Dict[str, Any],
    source_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    valid_confidence = {"high", "medium", "low"}
    provisions = []
    used_source_ids: set[str] = set()

    for item in raw.get("material_provisions") or []:
        if not isinstance(item, dict):
            continue
        source_ids = []
        for source_id in item.get("source_ids") or []:
            source_id = str(source_id).upper().strip()
            if source_id in source_map and source_id not in source_ids:
                source_ids.append(source_id)
                used_source_ids.add(source_id)
        finding = str(item.get("finding") or "").strip()
        if not finding or not source_ids:
            continue
        confidence = str(item.get("confidence") or "medium").lower()
        if confidence not in valid_confidence:
            confidence = "medium"
        provisions.append({
            "category": str(item.get("category") or "other").strip(),
            "title": str(item.get("title") or "Material planning provision").strip(),
            "finding": finding,
            "development_impact": str(item.get("development_impact") or "").strip(),
            "confidence": confidence,
            "source_ids": source_ids,
            "source_refs": [source_map[sid] for sid in source_ids],
        })
        if len(provisions) >= 6:
            break

    model_confidence = str(raw.get("confidence") or "medium").lower()
    if model_confidence not in valid_confidence:
        model_confidence = "medium"

    if not provisions:
        confidence = "low"
    elif model_confidence == "high" and len(used_source_ids) < 3:
        confidence = "medium"
    else:
        confidence = model_confidence

    def clean_strings(values: Any, limit: int) -> List[str]:
        result = []
        for value in values or []:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    allowed_options = []
    for item in raw.get("allowed_development_options") or []:
        if not isinstance(item, dict):
            continue
        option_type = str(item.get("type") or "").strip().lower()
        if option_type not in {"house", "apartments", "mixed_use", "commercial", "tourist", "industrial"}:
            continue
        source_ids = []
        for source_id in item.get("source_ids") or []:
            source_id = str(source_id).upper().strip()
            if source_id in source_map and source_id not in source_ids:
                source_ids.append(source_id)
                used_source_ids.add(source_id)
        status = str(item.get("status") or "conditional").strip().lower()
        if status not in {"allowed", "conditional", "not_supported"}:
            status = "conditional"
        if status == "allowed" and not source_ids:
            status = "conditional"
        allowed_options.append({
            "type": option_type,
            "status": status,
            "reason": str(item.get("reason") or "").strip(),
            "source_ids": source_ids,
            "source_refs": [source_map[sid] for sid in source_ids],
        })

    proposal_inputs = raw.get("proposal_inputs") if isinstance(raw.get("proposal_inputs"), dict) else {}
    def clean_distance(name: str):
        value = proposal_inputs.get(name)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return round(value, 2) if value >= 0 else None

    return {
        "summary": str(raw.get("summary") or "").strip(),
        "confidence": confidence,
        "material_provisions": provisions,
        "capacity_caveats": clean_strings(raw.get("capacity_caveats"), 5),
        "checks_before_reliance": clean_strings(raw.get("checks_before_reliance"), 5),
        "allowed_development_options": allowed_options,
        "proposal_inputs": {
            "front_setback_m": clean_distance("front_setback_m"),
            "side_setback_m": clean_distance("side_setback_m"),
            "rear_setback_m": clean_distance("rear_setback_m"),
            "setback_status": str(proposal_inputs.get("setback_status") or "unconfirmed").strip().lower(),
            "setback_note": str(proposal_inputs.get("setback_note") or "").strip(),
        },
        "sources": [
            source_map[sid]
            for sid in sorted(used_source_ids, key=lambda x: int(x[1:]))
        ],
    }


def retrieve_parcel_planning_context(
    parcel_details: Dict[str, Any],
    openai_client: OpenAI,
    supabase: Any,
    all_rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int], str]:
    queries = build_parcel_planning_queries(parcel_details)
    primary_question = queries[0]

    semantic_rows = batch_semantic_candidates(queries, openai_client, supabase)
    lexical_rows = merge_unique_rows([
        lexical_candidates(query, all_rows) for query in queries
    ])
    direct_rows = merge_unique_rows([
        direct_rule_candidates(query, all_rows) for query in queries
    ])
    hybrid_rows = merge_and_rerank(semantic_rows, lexical_rows, direct_rows)
    reranked_rows = llm_rerank_candidates(
        primary_question,
        hybrid_rows,
        openai_client,
    )
    context_rows = expand_with_adjacent_pages_local(reranked_rows, all_rows)

    metrics = {
        "semantic_candidates": len(semantic_rows),
        "lexical_candidates": len(lexical_rows),
        "direct_rule_candidates": len(direct_rows),
        "hybrid_candidates": len(hybrid_rows),
        "reranked_hits": len(reranked_rows),
        "context_chunks": len(context_rows),
    }
    return context_rows, metrics, primary_question


def generate_parcel_planning_analysis(
    parcel_details: Dict[str, Any],
    openai_client: OpenAI,
    supabase: Any,
    all_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    started = time.perf_counter()
    if not parcel_details.get("planning_zones"):
        return {
            "status": "insufficient_parcel_context",
            "summary": "No planning-zone data was returned for this parcel, so PLANA.CY did not infer parcel-specific planning provisions.",
            "confidence": "low",
            "material_provisions": [],
            "capacity_caveats": [],
            "checks_before_reliance": ["Confirm the applicable planning zone before relying on a parcel-specific planning analysis."],
            "sources": [],
            "retrieval": {},
            "analysis_engine_version": "planning-auto-v2-rules-4-2026",
            "model_passes": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    context_rows, retrieval_metrics, primary_question = retrieve_parcel_planning_context(
        parcel_details,
        openai_client,
        supabase,
        all_rows,
    )

    if not context_rows:
        return {
            "status": "insufficient_sources",
            "summary": (
                "The planning knowledge base did not return enough directly relevant "
                "material for an automatic parcel analysis."
            ),
            "confidence": "low",
            "material_provisions": [],
            "capacity_caveats": [],
            "checks_before_reliance": [
                "Review the applicable planning documents or ask PLANA.CY a narrower planning question."
            ],
            "sources": [],
            "retrieval": retrieval_metrics,
            "analysis_engine_version": "planning-auto-v2-rules-4-2026",
            "model_passes": 1 if retrieval_metrics.get("hybrid_candidates") else 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    context, source_map = build_numbered_planning_context(context_rows)
    structured_rules = parcel_details.get("structured_rule_analysis") or evaluate_parcel_rules(parcel_details)
    parcel_context = {
        "parcel": parcel_details.get("parcel"),
        "planning_zones": parcel_details.get("planning_zones"),
        "development_potential": parcel_details.get("development_potential"),
        "structured_rule_analysis": compact_rule_context(structured_rules),
        "warnings": parcel_details.get("warnings"),
    }

    instructions = """
You are PLANA.CY's automatic Cyprus parcel-planning intelligence analyst.

Return ONLY valid JSON. Do not use markdown or prose outside the JSON object.

Your job is not to restate the DLS zone coefficients or re-derive rules already encoded in STRUCTURED_RULE_ANALYSIS.
Identify only additional parcel-specific planning provisions, exceptions, use restrictions, special policies, or applicability issues that could materially change, qualify, reduce, condition, or require confirmation before relying on the theoretical development capacity.

Source precedence and evidence rules:
1. The structured rule layer is the deterministic baseline for topics it covers. It uses Ministerial Order 4/2026 from 11 May 2026.
2. Order 4/2026 supersedes Order 4/2024 for its covered calculation/setback topics. Do not revive a 4/2024 rule when the structured layer or a 4/2026 excerpt states the current rule.
3. The March 2026 ETEK guide is interpretive context and predates the effective date of Order 4/2026; it must not override Order 4/2026.
4. Use only the supplied source excerpts for any additional planning/legal claims.
5. Never assume a general rule applies to the parcel merely because it is common in Cyprus.
6. A provision is a material_provision only when the excerpts directly support the rule and its relevance to the stated zone/use context.
7. If a source is relevant but parcel applicability depends on missing facts, put the issue in checks_before_reliance instead of claiming it applies.
8. Keep general rules, exceptions, discretionary powers, and special cases separate.
9. Do not combine conditions from separate provisions.
10. Prefer newer directly applicable material, but retain an older directly stated rule when newer material does not replace it.
11. Each material_provision must cite one or more exact source IDs such as S1 or S4.
12. Do not cite a source ID that does not support the finding.
13. Maximum 6 material provisions. Include fewer when the evidence is weak.
14. Also identify only development types directly supported by the supplied excerpts. Never mark a use as allowed without a supporting source ID.
15. Extract numeric front/side/rear building distances only when the supplied excerpts directly establish the applicable baseline for this parcel context. Otherwise return null and unconfirmed.
16. Write the user-facing text in English. Keep Greek document titles only in source metadata; do not insert citation text inside finding fields.
15. confidence means confidence in the evidence coverage of this automated analysis, not legal certainty.

Return this exact shape:
{
  "summary": "2-4 sentence evidence-grounded summary",
  "confidence": "high|medium|low",
  "material_provisions": [
    {
      "category": "parking|density calculation|coverage|height/floors|use|setbacks|special provision|other",
      "title": "short title",
      "finding": "what the retrieved rules establish",
      "development_impact": "why it matters to practical development capacity",
      "confidence": "high|medium|low",
      "source_ids": ["S1"]
    }
  ],
  "capacity_caveats": ["specific caveat to the theoretical capacity"],
  "checks_before_reliance": ["specific missing fact or applicability check"],
  "allowed_development_options": [
    {
      "type": "house|apartments|mixed_use|commercial|tourist|industrial",
      "status": "allowed|conditional|not_supported",
      "reason": "short evidence-grounded reason",
      "source_ids": ["S1"]
    }
  ],
  "proposal_inputs": {
    "front_setback_m": null,
    "side_setback_m": null,
    "rear_setback_m": null,
    "setback_status": "confirmed|partially_confirmed|unconfirmed",
    "setback_note": "short note on which source establishes the baseline distances or what remains missing"
  }
}
"""

    prompt = f"""
AUTOMATIC INVESTIGATION QUESTION:
{primary_question}

TRUSTED DLS / PLATFORM PARCEL CONTEXT:
{json.dumps(parcel_context, ensure_ascii=False, indent=2)}

RETRIEVED PLANNING SOURCE EXCERPTS:
{context}

Return the structured parcel-planning analysis JSON only.
"""

    response = openai_client.responses.create(
        model=PLANNING_ANALYSIS_MODEL,
        instructions=instructions.strip(),
        input=prompt.strip(),
    )
    raw = parse_json_object(response.output_text)

    verifier_instructions = """
You are the final evidence verifier for PLANA.CY automatic parcel-planning intelligence.
Return ONLY valid JSON in exactly the same shape as the draft JSON.

Check every material_provision against the supplied source excerpts and the source_ids it cites.
- Treat STRUCTURED_RULE_ANALYSIS as the current deterministic baseline for topics it covers.
- Order 4/2026 supersedes Order 4/2024 for its covered calculation/setback topics from 11 May 2026.
- The March 2026 ETEK guide is interpretive and must not override Order 4/2026.
- Remove a provision if the cited excerpts do not directly support the finding.
- Correct source_ids when another supplied excerpt directly supports the finding.
- Do not infer that a rule applies to the parcel when applicability depends on a missing use, development type, location category, threshold, or discretionary decision.
- Move such unresolved applicability issues into checks_before_reliance.
- Do not combine separate provisions into cumulative conditions.
- Keep general rules, exceptions, discretionary powers, and special cases separate.
- Do not restate the DLS zone coefficients as a material provision unless a source qualifies or changes how they can be relied upon.
- Keep no more than 6 material provisions.
- Each retained material_provision must have at least one exact valid source ID.
- Write user-facing text in English.
- confidence is evidence-coverage confidence, not legal certainty.
Do not add facts that are absent from the source excerpts.
"""
    verifier_prompt = f"""
TRUSTED DLS / PLATFORM PARCEL CONTEXT:
{json.dumps(parcel_context, ensure_ascii=False, indent=2)}

SOURCE EXCERPTS:
{context}

DRAFT STRUCTURED ANALYSIS:
{json.dumps(raw, ensure_ascii=False, indent=2)}

Return the corrected structured analysis JSON only.
"""
    verified_response = openai_client.responses.create(
        model=VERIFY_MODEL,
        instructions=verifier_instructions.strip(),
        input=verifier_prompt.strip(),
    )
    verified_raw = parse_json_object(verified_response.output_text)
    result = normalise_planning_analysis(verified_raw, source_map)
    result.update({
        "status": "complete" if result["material_provisions"] else "limited",
        "retrieval": retrieval_metrics,
        "analysis_engine_version": "planning-auto-v2-rules-4-2026",
        "model_passes": 3,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    })
    return result


def build_context(rows: List[Dict[str, Any]]) -> str:
    primary = [r for r in rows if r.get("context_type") == "hybrid_hit"]
    adjacent = [r for r in rows if r.get("context_type") == "adjacent_page_context"]

    adjacent.sort(
        key=lambda r: (
            str(r.get("title") or ""),
            int(r.get("page_number") or 0),
        )
    )

    ordered = primary + adjacent

    blocks = []
    total_chars = 0

    for i, row in enumerate(ordered, start=1):
        title = row.get("title") or "Unknown document"
        page = row.get("page_number") or "?"
        pub_date = row.get("publication_date") or "unknown"
        priority = row.get("authority_priority") or 0
        context_type = row.get("context_type") or "source"
        section_title = row.get("section_title") or "Μη καθορισμένη"
        content = (row.get("content") or "").strip()

        block = (
            f"[SOURCE {i}]\n"
            f"Document: {title}\n"
            f"Page: {page}\n"
            f"Publication date: {pub_date}\n"
            f"Internal source priority: {priority}\n"
            f"Context type: {context_type}\n"
            f"Section: {section_title}\n"
            f"Text:\n{content}\n"
        )

        if total_chars + len(block) > MAX_CONTEXT_CHARS:
            break

        blocks.append(block)
        total_chars += len(block)

    return "\n".join(blocks)



def output_language_for_question(question: str) -> str:
    return "Greek" if contains_greek(question) else "English"


def answer_body_language_mismatch(text: str, target_language: str) -> bool:
    """
    Ignore bracketed citations because Greek document titles may legitimately
    appear inside an otherwise English answer.
    """
    body = re.sub(r"\[[^\]]+\]", " ", text or "")
    greek_letters = len(re.findall(r"[Α-ΩΆΈΉΊΌΎΏα-ωάέήίϊΐόύϋΰώ]", body))
    latin_letters = len(re.findall(r"[A-Za-z]", body))

    if target_language == "English":
        return greek_letters > max(30, latin_letters * 0.35)

    return latin_letters > max(60, greek_letters * 0.80)


def answer_question(
    question: str,
    rows: List[Dict[str, Any]],
    openai_client: OpenAI,
) -> str:
    if not rows:
        return (
            "Δεν βρέθηκαν επαρκώς σχετικά αποσπάσματα στη βάση γνώσης. "
            "Δοκίμασε να διατυπώσεις διαφορετικά την ερώτηση."
        )

    context = build_context(rows)
    target_language = output_language_for_question(question)
    required_note = (
        "Σημείωση: Η απάντηση βασίζεται στα διαθέσιμα έγγραφα της βάσης γνώσης και δεν υποκαθιστά επίσημη νομική ή πολεοδομική γνωμάτευση."
        if target_language == "Greek"
        else "Note: This answer is based on the available documents in the knowledge base and does not replace official legal or planning advice."
    )

    instructions = f"""
You are a Cyprus planning-regulations research assistant for architects.

OUTPUT LANGUAGE: {target_language}
The source excerpts may be in Greek. Ignore the source language when choosing the output language.
You must write the answer in {target_language} because that is the language of the user's question.

You must answer ONLY from the supplied retrieved source excerpts.

SOURCE PRECEDENCE:
- First identify the newest directly applicable source.
- For calculation of building coefficient, coverage, floors/height and boundary-distance topics covered by Ministerial Order 4/2026, treat Order 4/2026 as current from 11 May 2026 and Order 4/2024 as superseded.
- The March 2026 ETEK concise guide is interpretive context, is expressly non-exhaustive and states a scope limited to the four major-city Local Plans; it must not override the later-effective Order 4/2026.
- When newer and older sources differ, do not silently follow the older source.
- Use an older source only when it is consistent with newer material or when no newer applicable material is available.
- The internal source-priority number is only a retrieval hint, not a legal hierarchy.

LEGAL-READING RULES:
1. Never invent a regulation, number, exception, interpretation, or citation.
2. Never give a universal "yes" or "no" when the excerpts show that the answer depends on use, conditions, exceptions, discretion, or a category of space.
3. Before answering any yes/no question, explicitly check the supplied excerpts for:
   - exceptions
   - exclusions
   - partial counting
   - conditions
   - distinctions by use
   - newer rules that qualify older guidance
4. If the correct answer is conditional, start with "Εξαρτάται" in Greek or "It depends" in English.
5. Read neighboring page excerpts as continuous context across page breaks.
6. Resolve pronouns from preceding context before stating what an exception applies to.
7. Never generalize an exception from a specific object or use to a broader category.
8. NEVER combine conditions from separate provisions into one cumulative condition unless the source explicitly says they all apply together.
9. Treat the following as separate legal categories unless the source explicitly joins them:
   - general rule
   - definition
   - mandatory conditions
   - exception
   - special fire-safety provision
   - discretionary power of the Competent Authority
10. A special fire-safety rule must never be presented as a condition of the ordinary/general rule unless the source explicitly says so.
11. If the excerpts are insufficient or ambiguous, say so clearly.
12. Distinguish the general rule from exceptions and discretionary powers.
13. Answer in the same language as the user's question.
14. Be concise but practically useful to an architect.
15. Cite factual claims inline using:
    [Document title, p. X]
    or [Document title, pp. X–Y]
16. Every citation must include the FULL document title. Never shorten a citation to [p. X], [pp. X–Y], [σ. X], or [σσ. X–Y].
17. Do not cite SOURCE numbers.
18. End with exactly this note:
    {required_note}
"""

    prompt = f"""
USER QUESTION:
{question}

HYBRID-RETRIEVED SOURCE EXCERPTS:
{context}

Before drafting the answer, internally build a small legal rule map:
- GENERAL RULE
- DEFINITIONS
- MANDATORY CONDITIONS
- EXCEPTIONS
- SPECIAL CASES
- DISCRETIONARY POWERS
- SOURCE FOR EACH PROPOSITION

Do not show this internal map to the user.

Then:
- Identify the newest directly applicable source.
- Check exact keyword matches as well as semantic context.
- Check whether the answer has exceptions or depends on the type/use of space.
- Check whether any older source is qualified by newer material.
- Do not turn separate exceptions or special cases into extra conditions of the general rule.
- Do not join two source statements with "and", "provided that", or equivalent wording unless the source itself makes them cumulative.
- ALWAYS state the most directly applicable general rule first when the sources provide one.
- Do not replace an explicit general rule with a broad opening such as "it depends" or "there is no single rule".
- Put exceptions, limitations, unusual scenarios, and special zones after the general rule.
- If the question is broad, answer the ordinary/common case first, then explain when a different rule may apply.

Then write only the draft evidence-grounded answer.
"""

    response = openai_client.responses.create(
        model=ANSWER_MODEL,
        instructions=instructions.strip(),
        input=prompt.strip(),
    )

    draft_answer = response.output_text.strip()

    verifier_instructions = f"""
You are the final legal-consistency verifier for a Cyprus planning-regulations assistant.

You receive:
1. the user's question,
2. the exact retrieved source excerpts,
3. a draft answer.

OUTPUT LANGUAGE: {target_language}
Your job is to return a corrected final answer in {target_language}.
The source excerpts may be Greek. Do NOT switch to Greek merely because the source material is Greek.

Check especially for SOURCE PRECEDENCE AND SYNTHESIS ERRORS:
- For topics covered by Ministerial Order 4/2026, did the draft revive a superseded Order 4/2024 rule or allow the March 2026 ETEK guide to override Order 4/2026?
- Did the draft combine separate provisions into one cumulative condition?
- Did it turn an exception into a condition of the general rule?
- Did it turn a special fire-safety provision into a general requirement?
- Did it generalize a discretionary power?
- Did it merge facts from different source passages using "and", "provided that", or similar wording when the sources do not make them cumulative?
- Did it state something stronger than the excerpts support?
- Are general rule, conditions, exceptions, special cases, and discretionary powers clearly separated?
- If the sources contain a directly applicable general rule, is it stated first?
- Did the draft incorrectly open with "it depends" or "there is no single rule" even though a general rule is available?
- Are citations attached to the claims they actually support?

Rules:
1. Correct any such error.
2. Preserve useful, accurate content.
3. Do not add facts that are not in the excerpts.
4. For Order 4/2026 covered topics, prefer Order 4/2026 from 11 May 2026 over superseded Order 4/2024; use the March 2026 ETEK guide as interpretive context, not as an override.
5. Write the prose in {target_language}.
6. Every citation must include the FULL document title, for example:
   [Document title, p. X] or [Document title, pp. X–Y]
   Never shorten citations to [p. X], [pp. X–Y], [σ. X], or [σσ. X–Y].
7. Preserve Greek document titles inside citations even when the answer is in English.
8. Do not mention that you reviewed or corrected a draft.
9. End with exactly this note:
   {required_note}
10. Return ONLY the final answer to the user.
"""

    verifier_prompt = f"""
TARGET OUTPUT LANGUAGE:
{target_language}

USER QUESTION:
{question}

SOURCE EXCERPTS:
{context}

DRAFT ANSWER:
{draft_answer}

Return the corrected final answer only.
"""

    verified = openai_client.responses.create(
        model=VERIFY_MODEL,
        instructions=verifier_instructions.strip(),
        input=verifier_prompt.strip(),
    )

    final_answer = verified.output_text.strip()

    # Deterministic safeguard: if the verifier still switches language because
    # the source excerpts are Greek, rewrite only the prose language while
    # preserving meaning and full citations.
    if answer_body_language_mismatch(final_answer, target_language):
        language_fix_instructions = f"""
Rewrite the supplied answer in {target_language}.

Rules:
1. Preserve the legal meaning exactly.
2. Do not add or remove substantive claims.
3. Preserve every citation and its full Greek document title.
4. Every citation must remain in the form [Document title, p. X] or [Document title, pp. X–Y].
5. Never use bare citations such as [p. X], [σ. X], or [σσ. X–Y].
6. End with exactly this note:
   {required_note}
7. Return ONLY the rewritten final answer.
"""
        language_fixed = openai_client.responses.create(
            model=VERIFY_MODEL,
            instructions=language_fix_instructions.strip(),
            input=final_answer,
        )
        final_answer = language_fixed.output_text.strip()

    return final_answer


def main() -> None:
    load_dotenv()

    supabase_url = require_env("SUPABASE_URL")
    supabase_secret_key = require_env("SUPABASE_SECRET_KEY")
    openai_api_key = require_env("OPENAI_API_KEY")

    supabase = create_client(supabase_url, supabase_secret_key)
    openai_client = OpenAI(api_key=openai_api_key)

    print(f"PLANA.CY v11 — model: {ANSWER_MODEL}")
    print("Bilingual retrieval + hybrid search + legal verification + output-language guard are ON.")
    print("General-rule-first answers + condition/exception separation + full citations are ON.")
    print("Type 'exit' to quit.\n")

    # Only 424 chunks currently, so loading all rows for local lexical scoring is cheap.
    all_rows = fetch_all_chunks_with_metadata(supabase)
    print(f"Loaded {len(all_rows)} knowledge-base chunks for lexical search.\n")

    while True:
        question = input("Ask a planning question:\n> ").strip()

        if not question:
            continue

        if question.lower() in {"exit", "quit"}:
            break

        try:
            greek_search_query = generate_greek_search_query(question, openai_client)

            if greek_search_query != question:
                print(f"Greek retrieval query: {greek_search_query}")

            semantic_rows = merge_unique_rows([
                semantic_candidates(question, openai_client, supabase),
                semantic_candidates(greek_search_query, openai_client, supabase),
            ])

            lexical_rows = merge_unique_rows([
                lexical_candidates(question, all_rows),
                lexical_candidates(greek_search_query, all_rows),
            ])

            direct_rows = merge_unique_rows([
                direct_rule_candidates(question, all_rows),
                direct_rule_candidates(greek_search_query, all_rows),
            ])

            hybrid_rows = merge_and_rerank(semantic_rows, lexical_rows, direct_rows)
            reranked_rows = llm_rerank_candidates(question, hybrid_rows, openai_client)
            context_rows = expand_with_adjacent_pages(reranked_rows, supabase)

            print(
                f"\nSemantic candidates: {len(semantic_rows)} | "
                f"Lexical candidates: {len(lexical_rows)} | "
                f"Direct-rule candidates: {len(direct_rows)} | "
                f"Hybrid pool: {len(hybrid_rows)} | "
                f"LLM-reranked hits: {len(reranked_rows)} | "
                f"Context chunks: {len(context_rows)}"
            )
            print("Generating answer...\n")

            answer = answer_question(question, context_rows, openai_client)
            print(answer)

            print("\nTop LLM-reranked retrieval hits:")
            for i, row in enumerate(reranked_rows[:10], start=1):
                print(
                    f"{i}. {row.get('title')} — p. {row.get('page_number')} "
                    f"(semantic {float(row.get('semantic_score') or 0.0):.3f}, "
                    f"lexical {float(row.get('lexical_score') or 0.0):.3f}, "
                    f"direct {float(row.get('direct_score') or 0.0):.3f}, "
                    f"hybrid {float(row.get('hybrid_score') or 0.0):.3f})"
                )

            print("\n" + "=" * 90 + "\n")

        except Exception as exc:
            print(f"\nERROR: {exc}\n")
            print(
                "If this is a model-access error, set OPENAI_CHAT_MODEL in .env "
                "to a model available to your API project."
            )
            print()

# =========================
# WEB APP / API LAYER
# =========================

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


state: dict[str, Any] = {}


def unique_sources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int | None]] = set()
    sources: list[dict[str, Any]] = []

    for row in rows:
        title = row.get("title") or "Unknown document"
        page_number = row.get("page_number")
        key = (title, page_number)

        if key in seen:
            continue

        seen.add(key)
        sources.append(
            {
                "title": title,
                "page_number": page_number,
                "section_title": row.get("section_title"),
                "publication_date": (
                    str(row.get("publication_date"))
                    if row.get("publication_date")
                    else None
                ),
                "version": row.get("version"),
                "publisher": row.get("publisher"),
            }
        )

    return sources


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()

    supabase_url = require_env("SUPABASE_URL")
    supabase_secret_key = require_env("SUPABASE_SECRET_KEY")
    openai_api_key = require_env("OPENAI_API_KEY")

    state["supabase"] = create_client(supabase_url, supabase_secret_key)
    state["openai"] = OpenAI(api_key=openai_api_key)
    state["http"] = httpx.AsyncClient(
        timeout=httpx.Timeout(12.0, connect=5.0),
        limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        follow_redirects=True,
        headers={"User-Agent": "PLANA.CY/1.4"},
    )
    state["all_rows"] = fetch_all_chunks_with_metadata(state["supabase"])

    print(
        f"PLANA.CY ready — loaded "
        f"{len(state['all_rows'])} knowledge-base chunks."
    )

    yield
    http_client = state.get("http")
    if http_client:
        await http_client.aclose()
    state.clear()


app = FastAPI(
    title="PLANA.CY",
    version="4.1.0",
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=700)


@app.exception_handler(Exception)
async def plana_unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Never send FastAPI/Render plain-text "Internal Server Error" to the browser.
    # The UI expects a JSON error envelope and can therefore show a useful message.
    print(f"PLANA unhandled error on {request.method} {request.url.path}: {exc!r}", file=sys.stderr)
    return JSONResponse(
        status_code=500,
        content={"detail": "PLANA could not complete this request. Please retry; slow external DLS or market services may be temporarily unavailable."},
    )


@app.get("/health")
def health() -> dict[str, Any]:
    rows = state.get("all_rows", [])
    normalized_titles = [normalize_text(row.get("title") or "") for row in rows]
    source_summary = source_status_summary()
    return {
        "status": "ok",
        "chunks_loaded": len(rows),
        "model": ANSWER_MODEL,
        "rule_engine_version": RULE_ENGINE_VERSION,
        "structured_rule_count": len(RULE_CATALOG),
        "opportunity_engine_version": OPPORTUNITY_ENGINE_VERSION,
        "opportunity_assumption_set_version": ASSUMPTION_SET_VERSION,
        "market_engine_version": MARKET_ENGINE_VERSION,
        "market_source_engine_version": MARKET_SOURCE_ENGINE_VERSION,
        "market_source_registry": {
            "source_count": source_summary.get("source_count"),
            "runnable_count": source_summary.get("runnable_count"),
        },
        "structured_rule_sources_embedded": {
            "order_4_2026": True,
            "etek_march_2026_guide": True,
        },
        "knowledge_base_rule_sources": {
            "order_4_2026_detected": any("4/2026" in title for title in normalized_titles),
            "etek_march_2026_detected": any("συνοπτικ" in title and "πολεοδομ" in title for title in normalized_titles),
        },
    }


@app.post("/api/chat")
def chat(payload: ChatRequest) -> dict[str, Any]:
    question = payload.question.strip()

    supabase = state.get("supabase")
    openai_client = state.get("openai")
    all_rows = state.get("all_rows")

    if not supabase or not openai_client or all_rows is None:
        raise HTTPException(status_code=503, detail="Service is not ready yet.")

    try:
        greek_search_query = generate_greek_search_query(question, openai_client)

        semantic_rows = merge_unique_rows([
            semantic_candidates(question, openai_client, supabase),
            semantic_candidates(greek_search_query, openai_client, supabase),
        ])

        lexical_rows = merge_unique_rows([
            lexical_candidates(question, all_rows),
            lexical_candidates(greek_search_query, all_rows),
        ])

        direct_rows = merge_unique_rows([
            direct_rule_candidates(question, all_rows),
            direct_rule_candidates(greek_search_query, all_rows),
        ])

        hybrid_rows = merge_and_rerank(
            semantic_rows,
            lexical_rows,
            direct_rows,
        )

        reranked_rows = llm_rerank_candidates(
            question,
            hybrid_rows,
            openai_client,
        )

        context_rows = expand_with_adjacent_pages(
            reranked_rows,
            supabase,
        )

        answer = answer_question(
            question,
            context_rows,
            openai_client,
        )

        return {
            "question": question,
            "answer": answer,
            "language": output_language_for_question(question),
            "greek_search_query": greek_search_query,
            "sources": unique_sources(reranked_rows),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"PLANA.CY request failed: {exc}",
        ) from exc




# ==================== DLS SITE EXPLORER ====================
DLS_MAPSERVER = "https://eservices.dls.moi.gov.cy/arcgis/rest/services/National/CadastralMap_EN/MapServer"
PARCEL_QUERY = f"{DLS_MAPSERVER}/0/query"
GENERAL_IDENTIFY = "https://eservices.dls.moi.gov.cy/Services/Rest/Info/GeneralParcelIdentify"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
GOOGLE_PLACES_AUTOCOMPLETE = "https://places.googleapis.com/v1/places:autocomplete"
GOOGLE_PLACE_DETAILS = "https://places.googleapis.com/v1/places"
OVERPASS_API = os.getenv("PLANA_OVERPASS_URL", "https://overpass-api.de/api/interpreter")
OPEN_METEO_ELEVATION = "https://api.open-meteo.com/v1/elevation"


class BoundedCache:
    """Tiny LRU container used to bound long-running web-process memory."""
    def __init__(self, max_entries: int):
        self.max_entries = max(16, int(max_entries))
        self._data: OrderedDict[Any, Any] = OrderedDict()

    def get(self, key: Any, default: Any = None) -> Any:
        if key not in self._data:
            return default
        value = self._data.pop(key)
        self._data[key] = value
        return value

    def __setitem__(self, key: Any, value: Any) -> None:
        self._data.pop(key, None)
        self._data[key] = value
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def __contains__(self, key: Any) -> bool:
        return key in self._data


WEB_CACHE_MAX_ENTRIES = max(64, int(os.getenv("PLANA_WEB_CACHE_MAX_ENTRIES", "2000")))
GEOCODE_CACHE = BoundedCache(WEB_CACHE_MAX_ENTRIES)
PLACE_DETAILS_CACHE = BoundedCache(max(128, WEB_CACHE_MAX_ENTRIES // 2))
LOCATION_INTELLIGENCE_CACHE = BoundedCache(max(128, WEB_CACHE_MAX_ENTRIES // 2))
LOCATION_INTELLIGENCE_CACHE_TTL_SECONDS = 21600
SITE_CACHE = BoundedCache(WEB_CACHE_MAX_ENTRIES)
SITE_CACHE_TTL_SECONDS = 1800
PARCEL_POINT_CACHE = BoundedCache(WEB_CACHE_MAX_ENTRIES)
PARCEL_POINT_CACHE_TTL_SECONDS = 300
MARKET_ANALYSIS_CACHE = BoundedCache(max(128, WEB_CACHE_MAX_ENTRIES // 2))
MARKET_ANALYSIS_CACHE_TTL_SECONDS = 900
SITE_EXTRA_CACHE = BoundedCache(max(128, WEB_CACHE_MAX_ENTRIES // 2))
SITE_EXTRA_CACHE_TTL_SECONDS = 900
PARCEL_PLANNING_CACHE = BoundedCache(max(128, WEB_CACHE_MAX_ENTRIES // 2))
PARCEL_PLANNING_CACHE_TTL_SECONDS = 7200

# Confirmed / observed DLS map layers from the official viewer.
# Critical parcel-screening layers only. Contours and surveyed-parcel checks are
# intentionally excluded from the automatic click path because they added DLS
# round-trips without changing the primary feasibility result.
SPECIAL_LAYERS = {
    28: "Buildings",
    31: "Coast Protection Zone",
    32: "State Land",
    37: "White Zones",
}




@app.get("/api/geocode")
async def geocode(
    q: str = Query(min_length=2, max_length=200),
    session_token: str | None = Query(default=None, max_length=120),
):
    """Type-ahead location search with optional Google Places and OSM fallback."""
    query = q.strip()
    key = (query.casefold(), session_token or "", bool(os.getenv("GOOGLE_MAPS_API_KEY")))
    cached = GEOCODE_CACHE.get(key)
    if cached is not None:
        return {"results": cached}

    client = state.get("http")
    if not client:
        raise HTTPException(status_code=503, detail="HTTP service is not ready yet.")

    google_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if google_key:
        try:
            body: dict[str, Any] = {
                "input": query,
                "includedRegionCodes": ["cy"],
                "languageCode": "en",
                "regionCode": "CY",
                "locationBias": {
                    "rectangle": {
                        "low": {"latitude": 34.35, "longitude": 31.95},
                        "high": {"latitude": 35.85, "longitude": 34.90},
                    }
                },
            }
            if session_token:
                body["sessionToken"] = session_token
            response = await client.post(
                GOOGLE_PLACES_AUTOCOMPLETE,
                json=body,
                headers={
                    "X-Goog-Api-Key": google_key,
                    "X-Goog-FieldMask": (
                        "suggestions.placePrediction.placeId,"
                        "suggestions.placePrediction.text,"
                        "suggestions.placePrediction.structuredFormat,"
                        "suggestions.placePrediction.primaryType"
                    ),
                },
                timeout=10.0,
            )
            if response.status_code == 200:
                results: list[dict[str, Any]] = []
                for item in response.json().get("suggestions") or []:
                    prediction = item.get("placePrediction") or {}
                    place_id = prediction.get("placeId")
                    text = (prediction.get("text") or {}).get("text")
                    if not place_id or not text:
                        continue
                    structured = prediction.get("structuredFormat") or {}
                    results.append({
                        "display_name": text,
                        "main_text": ((structured.get("mainText") or {}).get("text") or text),
                        "secondary_text": ((structured.get("secondaryText") or {}).get("text") or ""),
                        "place_id": place_id,
                        "primary_type": prediction.get("primaryType"),
                        "provider": "google",
                    })
                if results:
                    GEOCODE_CACHE[key] = results[:10]
                    return {"results": results[:10]}
        except Exception as exc:
            print(f"PLANA Google autocomplete fallback: {exc!r}", file=sys.stderr)

    params = {
        "q": f"{query}, Cyprus",
        "format": "jsonv2",
        "limit": 10,
        "countrycodes": "cy",
        "addressdetails": 1,
        "dedupe": 1,
    }
    headers = {
        "User-Agent": "PLANA.CY/1.5",
        "Accept-Language": "en,el;q=0.8",
    }
    response = await client.get(NOMINATIM, params=params, headers=headers, timeout=12.0)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Location search failed.")

    results = []
    for item in response.json():
        if not item.get("lat") or not item.get("lon"):
            continue
        address = item.get("address") or {}
        main_text = (
            address.get("road")
            or address.get("neighbourhood")
            or address.get("suburb")
            or address.get("village")
            or address.get("town")
            or address.get("city")
            or item.get("name")
            or str(item.get("display_name") or "").split(",")[0]
        )
        results.append({
            "display_name": item.get("display_name"),
            "main_text": main_text,
            "secondary_text": ", ".join(str(item.get("display_name") or "").split(",")[1:4]).strip(),
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
            "primary_type": item.get("type"),
            "provider": "openstreetmap",
        })
    GEOCODE_CACHE[key] = results
    return {"results": results}


@app.get("/api/geocode-place")
async def geocode_place(
    place_id: str = Query(min_length=3, max_length=300),
    session_token: str | None = Query(default=None, max_length=120),
):
    google_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not google_key:
        raise HTTPException(status_code=400, detail="Google Places is not configured.")
    clean_id = place_id.removeprefix("places/")
    cache_key = (clean_id, session_token or "")
    cached = PLACE_DETAILS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    client = state.get("http")
    if not client:
        raise HTTPException(status_code=503, detail="HTTP service is not ready yet.")
    params = {"sessionToken": session_token} if session_token else None
    response = await client.get(
        f"{GOOGLE_PLACE_DETAILS}/{clean_id}",
        params=params,
        headers={
            "X-Goog-Api-Key": google_key,
            "X-Goog-FieldMask": "id,displayName,formattedAddress,location,primaryType",
        },
        timeout=10.0,
    )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not open that location.")
    item = response.json()
    location = item.get("location") or {}
    result = {
        "display_name": item.get("formattedAddress") or (item.get("displayName") or {}).get("text"),
        "lat": location.get("latitude"),
        "lon": location.get("longitude"),
        "primary_type": item.get("primaryType"),
        "provider": "google",
    }
    if result["lat"] is None or result["lon"] is None:
        raise HTTPException(status_code=502, detail="Location coordinates were not returned.")
    PLACE_DETAILS_CACHE[cache_key] = result
    return result


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    value = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(max(1 - value, 0)))


def _bearing_label(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return labels[int((bearing + 22.5) // 45) % 8]


def _overpass_point(item: dict[str, Any]) -> tuple[float, float] | None:
    if item.get("lat") is not None and item.get("lon") is not None:
        return float(item["lat"]), float(item["lon"])
    center = item.get("center") or {}
    if center.get("lat") is not None and center.get("lon") is not None:
        return float(center["lat"]), float(center["lon"])
    return None


def _amenity_category(tags: dict[str, Any]) -> str | None:
    amenity = str(tags.get("amenity") or "")
    shop = str(tags.get("shop") or "")
    leisure = str(tags.get("leisure") or "")
    natural = str(tags.get("natural") or "")
    tourism = str(tags.get("tourism") or "")
    if shop in {"supermarket", "convenience", "greengrocer"}:
        return "supermarkets"
    if amenity in {"restaurant", "cafe", "fast_food", "bar", "pub", "food_court"}:
        return "restaurants"
    if shop:
        return "shops"
    if amenity in {"school", "college", "university", "kindergarten"}:
        return "education"
    if amenity in {"pharmacy", "hospital", "clinic", "doctors", "dentist"}:
        return "health"
    if amenity == "place_of_worship":
        return "churches"
    if leisure in {"park", "garden", "playground", "nature_reserve"}:
        return "parks"
    if leisure in {"fitness_centre", "sports_centre", "stadium", "swimming_pool"}:
        return "fitness"
    if tags.get("public_transport") or tags.get("highway") == "bus_stop" or amenity == "bus_station":
        return "transport"
    if natural == "beach":
        return "beaches"
    if tourism in {"hotel", "attraction", "museum"}:
        return "attractions"
    return None


@app.get("/api/location-intelligence")
async def location_intelligence(
    lat: float = Query(ge=34.0, le=36.0),
    lon: float = Query(ge=31.0, le=35.0),
) -> dict[str, Any]:
    cache_key = (round(lat, 4), round(lon, 4))
    cached = LOCATION_INTELLIGENCE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < LOCATION_INTELLIGENCE_CACHE_TTL_SECONDS:
        return {**cached[1], "cached": True}

    client = state.get("http")
    if not client:
        raise HTTPException(status_code=503, detail="HTTP service is not ready yet.")

    query = f"""
[out:json][timeout:14];
(
  nwr(around:3000,{lat},{lon})["shop"];
  nwr(around:3000,{lat},{lon})["amenity"~"restaurant|cafe|fast_food|bar|pub|food_court|school|college|university|kindergarten|pharmacy|hospital|clinic|doctors|dentist|place_of_worship|bus_station"];
  nwr(around:3000,{lat},{lon})["leisure"~"park|garden|playground|nature_reserve|fitness_centre|sports_centre|stadium|swimming_pool"];
  nwr(around:3000,{lat},{lon})["tourism"~"hotel|attraction|museum"];
  nwr(around:5000,{lat},{lon})["natural"="beach"];
  nwr(around:3000,{lat},{lon})["public_transport"];
  node(around:3000,{lat},{lon})["highway"="bus_stop"];
  way(around:20000,{lat},{lon})["natural"="coastline"];
  nwr(around:180,{lat},{lon})["building"];
);
out center 1400;
""".strip()

    async def fetch_overpass() -> dict[str, Any]:
        try:
            response = await client.post(OVERPASS_API, content=query, timeout=18.0)
            return response.json() if response.status_code == 200 else {"elements": []}
        except Exception as exc:
            print(f"PLANA amenities fallback: {exc!r}", file=sys.stderr)
            return {"elements": []}

    async def fetch_elevation() -> float | None:
        try:
            response = await client.get(
                OPEN_METEO_ELEVATION,
                params={"latitude": lat, "longitude": lon},
                timeout=8.0,
            )
            if response.status_code != 200:
                return None
            value = response.json().get("elevation")
            if isinstance(value, list):
                value = value[0] if value else None
            number = _num(value)
            return round(number, 1) if number is not None else None
        except Exception:
            return None

    overpass, elevation = await asyncio.gather(fetch_overpass(), fetch_elevation())
    category_rows: dict[str, list[dict[str, Any]]] = {}
    coastline: list[tuple[float, float, float]] = []
    nearby_building_count = 0

    for item in overpass.get("elements") or []:
        tags = item.get("tags") or {}
        point = _overpass_point(item)
        if not point:
            continue
        item_lat, item_lon = point
        distance_km = _haversine_km(lat, lon, item_lat, item_lon)
        if tags.get("natural") == "coastline":
            coastline.append((distance_km, item_lat, item_lon))
            continue
        if tags.get("building") and distance_km <= 0.18:
            nearby_building_count += 1
            continue
        category = _amenity_category(tags)
        if not category:
            continue
        name = tags.get("name:en") or tags.get("name") or tags.get("brand") or category.replace("_", " ").title()
        category_rows.setdefault(category, []).append({
            "name": clean_text(name),
            "distance_m": round(distance_km * 1000),
            "lat": item_lat,
            "lon": item_lon,
        })

    labels = {
        "supermarkets": "Supermarkets",
        "restaurants": "Restaurants & cafés",
        "shops": "Shops",
        "education": "Schools & education",
        "health": "Health & pharmacies",
        "churches": "Churches",
        "parks": "Parks",
        "fitness": "Gyms & sport",
        "transport": "Public transport",
        "beaches": "Beaches",
        "attractions": "Hotels & attractions",
    }
    order = list(labels)
    amenities = []
    for category in order:
        rows = sorted(category_rows.get(category, []), key=lambda item: item["distance_m"])
        if not rows:
            continue
        amenities.append({
            "category": category,
            "label": labels[category],
            "count": len(rows),
            "nearest_distance_m": rows[0]["distance_m"],
            "nearest_name": rows[0]["name"],
            "places": rows[:20],
        })

    view_items: list[dict[str, Any]] = []
    if elevation is not None:
        view_items.append({"label": "Elevation", "value": f"{elevation:,.0f} m"})
    if coastline:
        coast_distance, coast_lat, coast_lon = min(coastline, key=lambda item: item[0])
        direction = _bearing_label(lat, lon, coast_lat, coast_lon)
        if coast_distance <= 2.5:
            sea_value = "Coastal view potential"
        elif coast_distance <= 7.5 and (elevation or 0) >= 20:
            sea_value = "Possible from upper floors"
        elif coast_distance <= 12 and (elevation or 0) >= 70:
            sea_value = "Long-distance potential"
        else:
            sea_value = "Coast in the wider outlook"
        view_items.append({
            "label": "Sea",
            "value": sea_value,
            "detail": f"{direction} · {coast_distance:.1f} km",
        })
    if nearby_building_count <= 8:
        outlook = "Open surroundings"
    elif nearby_building_count <= 24:
        outlook = "Mixed urban outlook"
    else:
        outlook = "Dense urban outlook"
    view_items.append({"label": "Outlook", "value": outlook})
    park_rows = sorted(category_rows.get("parks", []), key=lambda item: item["distance_m"])
    if park_rows and park_rows[0]["distance_m"] <= 500:
        view_items.append({"label": "Green space", "value": f"{park_rows[0]['nearest_name'] if 'nearest_name' in park_rows[0] else park_rows[0]['name']} · {park_rows[0]['distance_m']} m"})

    result = {
        "amenities": amenities,
        "view_potential": view_items,
        "nearby_building_count": nearby_building_count,
        "cached": False,
    }
    LOCATION_INTELLIGENCE_CACHE[cache_key] = (time.time(), result)
    return result



def indexed_parcel_geometry(row: dict[str, Any]) -> dict[str, Any] | None:
    geom = row.get("geom")
    if isinstance(geom, dict) and geom.get("type") and geom.get("coordinates"):
        return {"type": "Feature", "properties": {}, "geometry": geom}
    return None



def canonical_details_from_index(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw_summary") or {}
    raw_zones = raw.get("zones") if isinstance(raw.get("zones"), list) else []
    zones: list[dict[str, Any]] = []
    for item in raw_zones:
        if not isinstance(item, dict) or not item.get("zone"):
            continue
        zones.append({
            "zone": item.get("zone"),
            "description": item.get("description"),
            "density_percent": item.get("density_percent"),
            "coverage_percent": item.get("coverage_percent"),
            "max_floors": item.get("max_floors"),
            "max_height_m": item.get("max_height_m"),
            "overlap_percent": item.get("overlap_percent"),
            "remarks": item.get("remarks"),
        })
    if not zones and row.get("planning_zone"):
        zones = [{
            "zone": row.get("planning_zone"),
            "density_percent": row.get("density_percent"),
            "coverage_percent": row.get("coverage_percent"),
            "max_floors": row.get("max_floors"),
            "max_height_m": row.get("max_height_m"),
            "overlap_percent": raw.get("primary_zone_overlap_percent"),
            "remarks": "Legacy index row: per-zone coefficients were not retained.",
        }]

    warnings = []
    if raw.get("white_zone_overlap"):
        warnings.append("White-zone overlap is recorded in the PLANA index.")
    if raw.get("coast_protection_overlap"):
        warnings.append("Coast-protection overlap is recorded in the PLANA index.")
    if raw.get("state_land_overlap"):
        warnings.append("State-land overlap is recorded in the PLANA index.")
    overlap_total = raw.get("zone_overlap_total_percent")
    if overlap_total is None:
        values = [_num(z.get("overlap_percent")) for z in zones]
        values = [value for value in values if value is not None]
        overlap_total = sum(values) if values else None

    result = {
        "parcel": {
            "parcel_id": row.get("parcel_id"), "parcel_number": row.get("parcel_number"), "registration_number": None,
            "district": row.get("district"), "municipality": row.get("municipality"), "quarter": row.get("quarter"),
            "sheet": raw.get("sheet"), "plan": raw.get("plan"), "block": raw.get("block"), "scale": None,
            "postal_code": raw.get("postal_code"), "house_no": None, "parcel_extent_m2": row.get("parcel_area_m2"),
            "price_2021": None, "price_2018": None, "price_1980": None, "valuation_change_percent": None,
            "is_preserved": bool(raw.get("is_preserved", False)), "is_ancient": bool(raw.get("is_ancient", False)),
            "is_common_property": bool(raw.get("is_common_property", False)),
        },
        "planning_zones": zones,
        "development_potential": {
            "theoretical_max_floor_area_m2": row.get("floor_capacity_m2"),
            "theoretical_max_ground_coverage_m2": row.get("coverage_capacity_m2"),
            "effective_density_percent": row.get("density_percent"), "effective_coverage_percent": row.get("coverage_percent"),
            "calculation_method": "PLANA precomputed parcel index",
            "area_basis_status": "indexed_official_geometry", "calculation_authority_status": "preliminary_screening",
            "multi_zone_policy_status": "weighted_indexed_zones" if len(zones) > 1 else "single_indexed_zone",
            "zone_overlap_total_percent": overlap_total,
            "zone_overlap_complete": overlap_total is not None and 99.0 <= float(overlap_total) <= 101.0,
            "calculation_warnings": [],
        },
        "registration_summary": {"total_related_records": 0, "by_type": {}, "total_enclosed_extent_m2": row.get("existing_enclosed_m2"), "total_covered_extent_m2": None, "total_uncovered_extent_m2": None},
        "related_properties": [], "warnings": warnings,
        "building_summary": {"count": row.get("building_count") or 0, "features": [], "footprint_m2": row.get("building_footprint_m2")},
        "contour_summary": {"terrain_range_m": row.get("terrain_range_m")},
        "spatial_checks": {"coast_protection": bool(raw.get("coast_protection_overlap", False)), "state_land": bool(raw.get("state_land_overlap", False)), "white_zone": bool(raw.get("white_zone_overlap", False))},
        "index_metadata": {"source": "plana_parcels", "score_version": row.get("score_version"), "data_confidence": row.get("data_confidence"), "indexed_at": row.get("indexed_at"), "dls_refreshed_at": row.get("dls_refreshed_at")},
    }
    result["structured_rule_analysis"] = evaluate_parcel_rules(result)
    return result



async def get_indexed_parcel_at_point(lat: float, lon: float) -> dict[str, Any] | None:
    supabase = state.get("supabase")
    if not supabase:
        return None
    try:
        response = await asyncio.to_thread(
            lambda: supabase.rpc(
                "find_plana_parcel_at_point",
                {"p_lon": lon, "p_lat": lat},
            ).execute()
        )
        rows = response.data or []
        return rows[0] if rows else None
    except Exception as exc:
        print(f"PLANA point-index fallback: {exc!r}", file=sys.stderr)
        return None


async def get_indexed_parcel_by_id(parcel_id: int) -> dict[str, Any] | None:
    supabase = state.get("supabase")
    if not supabase:
        return None
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table("plana_parcels")
            .select("*")
            .eq("parcel_id", parcel_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None
    except Exception as exc:
        print(f"PLANA parcel-index detail fallback: {exc!r}", file=sys.stderr)
        return None


async def get_parcel_at_point(lat: float, lon: float):
    cache_key = (round(lat, 6), round(lon, 6))
    cached = PARCEL_POINT_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < PARCEL_POINT_CACHE_TTL_SECONDS:
        return cached[1]

    params = {
        "f": "geojson",
        "where": "1=1",
        "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "resultRecordCount": 5,
    }

    client = state.get("http")
    if not client:
        raise HTTPException(status_code=503, detail="HTTP service is not ready yet.")
    r = await client.get(PARCEL_QUERY, params=params, timeout=12.0)

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="DLS parcel query failed.")

    data = r.json()
    features = data.get("features", [])
    if not features:
        raise HTTPException(status_code=404, detail="No DLS parcel found at that point.")
    feature = features[0]
    PARCEL_POINT_CACHE[cache_key] = (time.time(), feature)
    return feature


async def get_general_identify(subproperty_id: int):
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://eservices.dls.moi.gov.cy/",
        "User-Agent": "Mozilla/5.0 PLANA.CY/1.0",
    }

    client = state.get("http")
    if not client:
        raise HTTPException(status_code=503, detail="HTTP service is not ready yet.")
    r = await client.get(
        GENERAL_IDENTIFY,
        params={"subPropertyId": subproperty_id},
        headers=headers,
        timeout=15.0,
    )

    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"DLS GeneralParcelIdentify failed ({r.status_code}).",
        )

    try:
        return r.json()
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="DLS GeneralParcelIdentify returned invalid JSON.",
        )


def clean_text(v):
    return v.strip() if isinstance(v, str) else v


def as_percent(v):
    if v in (None, ""):
        return None
    try:
        x = float(v)
        return round(x * 100, 2) if abs(x) <= 5 else round(x, 2)
    except Exception:
        return v


def pick_parcel_record(records, parcel_id):
    for x in records:
        if x.get("PrParcelId") == parcel_id and x.get("PropertyTypeName") == "Parcel":
            return x
    for x in records:
        if x.get("PropertyTypeName") == "Parcel":
            return x
    return records[0] if records else None


def parse_zone(z, link=None):
    if not z:
        return None

    affected = link.get("PrAffectedExtent") if link else None
    total = link.get("PrTotalExtent") if link else None
    overlap = None
    try:
        if affected is not None and total not in (None, 0):
            overlap = round(float(affected) / float(total) * 100, 2)
    except Exception:
        pass

    return {
        "zone": clean_text(z.get("PrName")),
        "density_percent": as_percent(z.get("PrDensityRateQty")),
        "coverage_percent": as_percent(z.get("PrCoverageRate")),
        "max_floors": z.get("PrStoreyNoQty"),
        "max_height_m": z.get("PrHeightMSR"),
        "remarks": clean_text(z.get("PrRemarkDesc")),
        "description_en": clean_text(z.get("PrNameEn")),
        "description_gr": clean_text(z.get("PrNameGr")),
        "affected_extent": affected,
        "total_extent": total,
        "overlap_percent": overlap,
    }


def haversine_m(lon1, lat1, lon2, lat2):
    r = 6371008.8
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def polygon_geometry_metrics(feature):
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []

    if geom.get("type") != "Polygon" or not coords:
        return {}

    outer = max(coords, key=len)
    if len(outer) < 2:
        return {}

    edge_lengths = []
    perimeter = 0.0
    for a, b in zip(outer, outer[1:]):
        d = haversine_m(a[0], a[1], b[0], b[1])
        edge_lengths.append(d)
        perimeter += d

    lons = [p[0] for p in outer]
    lats = [p[1] for p in outer]

    longest = max(edge_lengths) if edge_lengths else None
    shortest = min(edge_lengths) if edge_lengths else None

    orientation_deg = None
    orientation_label = None
    if edge_lengths:
        idx = edge_lengths.index(longest)
        a = outer[idx]
        b = outer[idx + 1]
        y = math.sin(math.radians(b[0] - a[0])) * math.cos(math.radians(b[1]))
        x = (
            math.cos(math.radians(a[1])) * math.sin(math.radians(b[1]))
            - math.sin(math.radians(a[1]))
            * math.cos(math.radians(b[1]))
            * math.cos(math.radians(b[0] - a[0]))
        )
        bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        orientation_deg = round(bearing, 1)
        orientation_label = dirs[int((bearing + 22.5) // 45) % 8]

    return {
        "approx_perimeter_m": round(perimeter, 2),
        "longest_edge_m": round(longest, 2) if longest is not None else None,
        "shortest_edge_m": round(shortest, 2) if shortest is not None else None,
        "centroid_lat": round(sum(lats) / len(lats), 7),
        "centroid_lon": round(sum(lons) / len(lons), 7),
        "longest_edge_orientation_deg": orientation_deg,
        "longest_edge_orientation": orientation_label,
    }


def geojson_to_esri_polygon(feature):
    geom = feature.get("geometry") or {}
    if geom.get("type") != "Polygon":
        return None
    return {
        "rings": geom.get("coordinates") or [],
        "spatialReference": {"wkid": 4326},
    }


async def query_layer_intersections(layer_id: int, parcel_feature: dict):
    esri_geom = geojson_to_esri_polygon(parcel_feature)
    if not esri_geom:
        return {"ok": False, "error": "Unsupported parcel geometry"}

    url = f"{DLS_MAPSERVER}/{layer_id}/query"
    params = {
        "f": "json",
        "where": "1=1",
        "geometry": json.dumps(esri_geom),
        "geometryType": "esriGeometryPolygon",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "resultRecordCount": 1000,
    }

    try:
        client = state.get("http")
        if not client:
            return {"ok": False, "error": "HTTP service is not ready"}
        r = await client.get(url, params=params, timeout=8.0)

        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}

        data = r.json()
        if "error" in data:
            return {"ok": False, "error": data["error"]}

        return {
            "ok": True,
            "features": data.get("features", []),
            "exceeded_transfer_limit": data.get("exceededTransferLimit", False),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}



@app.get("/api/parcel-basic")
async def parcel_basic(
    lat: float = Query(ge=34.0, le=36.0),
    lon: float = Query(ge=31.0, le=35.0),
) -> dict[str, Any]:
    indexed = await get_indexed_parcel_at_point(lat, lon)
    if indexed:
        parcel_feature = indexed_parcel_geometry(indexed)
        if parcel_feature:
            parcel_feature["properties"] = {
                "SBPI_ID_NO": indexed.get("parcel_id"),
                "PARCEL_NBR": indexed.get("parcel_number"),
            }
            return {
                "parcel_feature": parcel_feature,
                "parcel_id": indexed.get("parcel_id"),
                "parcel_number": indexed.get("parcel_number"),
                "sheet": (indexed.get("raw_summary") or {}).get("sheet"),
                "plan": (indexed.get("raw_summary") or {}).get("plan"),
                "block": (indexed.get("raw_summary") or {}).get("block"),
                "map_geometry_extent_m2": indexed.get("parcel_area_m2"),
                "geometry_metrics": {
                    "approx_area_m2": indexed.get("parcel_area_m2"),
                    "perimeter_m": indexed.get("perimeter_m"),
                    "longest_edge_m": indexed.get("longest_edge_m"),
                    "shortest_edge_m": indexed.get("shortest_edge_m"),
                    "centroid_lat": indexed.get("centroid_lat"),
                    "centroid_lon": indexed.get("centroid_lon"),
                },
                "map_layer_attributes": parcel_feature["properties"],
                "source": "plana_index",
            }

    parcel_feature = await get_parcel_at_point(lat, lon)
    props = parcel_feature.get("properties", {})
    sbpi = props.get("SBPI_ID_NO")
    if sbpi is None:
        raise HTTPException(status_code=502, detail="DLS parcel did not return SBPI_ID_NO.")
    try:
        sbpi = int(sbpi)
    except Exception:
        raise HTTPException(status_code=502, detail=f"Unexpected SBPI_ID_NO: {sbpi}")

    return {
        "parcel_feature": parcel_feature,
        "parcel_id": sbpi,
        "parcel_number": props.get("PARCEL_NBR") or props.get("Parcel Number"),
        "sheet": props.get("SHEET") or props.get("Sheet"),
        "plan": props.get("PLAN_NBR") or props.get("Plan"),
        "block": props.get("BLCK_CODE") or props.get("Block Code"),
        "map_geometry_extent_m2": props.get("Parcel Extend") or props.get("SHAPE.STArea()"),
        "geometry_metrics": polygon_geometry_metrics(parcel_feature),
        "map_layer_attributes": props,
        "source": "live_dls_fallback",
    }


def safe_sum(values: list[Any]) -> float | None:
    nums = []
    for value in values:
        try:
            if value not in (None, ""):
                nums.append(float(value))
        except Exception:
            pass
    return round(sum(nums), 2) if nums else None


def normalize_parcel_details(
    records: list[dict[str, Any]],
    parcel_id: int,
) -> dict[str, Any]:
    """Convert DLS GeneralParcelIdentify records into PLANA's canonical parcel payload."""
    parcel = pick_parcel_record(records, parcel_id)
    if not parcel:
        raise HTTPException(status_code=502, detail="Main parcel record could not be identified.")

    zones = []
    for link in parcel.get("ParcelPlanZones") or []:
        parsed = parse_zone(link.get("PrPlanningZone"), link)
        if parsed:
            zones.append(parsed)
    if not zones:
        parsed = parse_zone(parcel.get("PrPlanningZone"))
        if parsed:
            zones.append(parsed)

    related = []
    type_counter = Counter()
    enclosed_vals, covered_vals, uncovered_vals = [], [], []

    for rec in records:
        if rec is parcel:
            continue
        subitems = rec.get("PrPropertySubproperty") or []
        sub = subitems[0] if subitems else {}

        kind = clean_text(rec.get("SubPropertyKindName"))
        prop_type = clean_text(rec.get("PropertyTypeName"))
        type_counter[kind or prop_type or "Other"] += 1

        enclosed = sub.get("PrEnclosedExtent")
        covered = sub.get("PrCoveredExtent")
        uncovered = sub.get("PrUncoveredExtent")
        enclosed_vals.append(enclosed)
        covered_vals.append(covered)
        uncovered_vals.append(uncovered)

        related.append({
            "property_type": prop_type,
            "kind": kind,
            "registration_block": rec.get("PrRegistrationBlock"),
            "registration_no": clean_text(rec.get("PrRegistrationNo")),
            "price_2021": rec.get("PrPriceBase2"),
            "price_2018": rec.get("PrPriceBase1"),
            "price_1980": rec.get("PrPriceBase3"),
            "unit_floor_no": sub.get("UnitFloorNo"),
            "plan_no": clean_text(sub.get("PlanNo")),
            "enclosed_extent": enclosed,
            "covered_extent": covered,
            "uncovered_extent": uncovered,
            "is_legal": sub.get("PrIsLegal"),
        })

    parcel_area = parcel.get("PrParcelExtent")
    capacity_result = calculate_zoned_capacity(parcel_area, zones)
    max_floor_area = capacity_result.get("theoretical_max_floor_area_m2")
    max_ground_coverage = capacity_result.get("theoretical_max_ground_coverage_m2")

    value_2021 = parcel.get("PrPriceBase2")
    value_2018 = parcel.get("PrPriceBase1")
    valuation_change_percent = None
    try:
        if value_2021 is not None and value_2018 not in (None, 0):
            valuation_change_percent = round(
                (float(value_2021) - float(value_2018)) / float(value_2018) * 100,
                2,
            )
    except Exception:
        pass

    warnings = []
    if len(zones) > 1:
        warnings.append("Parcel is affected by multiple planning zones.")
    if any(zone.get("remarks") for zone in zones):
        warnings.append("One or more planning-zone remarks apply.")
    if related:
        warnings.append(f"Parcel has {len(related)} related registered properties or units.")
    if bool(parcel.get("PrIsPreserved")):
        warnings.append("Property is marked as preserved.")
    if bool(parcel.get("PrIsAncient")):
        warnings.append("Property is marked as ancient.")
    if bool(parcel.get("PrIsCommonProperty")):
        warnings.append("Property is marked as common property.")

    parcel_summary = {
        "parcel_id": parcel.get("PrParcelId") or parcel_id,
        "parcel_number": clean_text(parcel.get("PrParcelNo")),
        "registration_number": clean_text(parcel.get("PrRegistrationNo")),
        "district": clean_text(parcel.get("PrDistrictNameEn") or parcel.get("DistrictName")),
        "municipality": clean_text(parcel.get("PrMunicipalityNameEn") or parcel.get("MunicipalityName")),
        "quarter": clean_text(parcel.get("PrQuarterNameEn") or parcel.get("QuarterName")),
        "sheet": clean_text(parcel.get("PrSheetValue")),
        "plan": clean_text(parcel.get("PrPlanValue")),
        "block": clean_text(parcel.get("PrBlockValue")),
        "scale": clean_text(parcel.get("PrScaleValue")),
        "postal_code": clean_text(parcel.get("PrPostalCode")),
        "house_no": parcel.get("PrHouseNo"),
        "parcel_extent_m2": parcel_area,
        "price_2021": value_2021,
        "price_2018": value_2018,
        "price_1980": parcel.get("PrPriceBase3"),
        "valuation_change_percent": valuation_change_percent,
        "is_preserved": bool(parcel.get("PrIsPreserved")),
        "is_ancient": bool(parcel.get("PrIsAncient")),
        "is_common_property": bool(parcel.get("PrIsCommonProperty")),
    }

    result = {
        "parcel": parcel_summary,
        "planning_zones": zones,
        "development_potential": {
            "theoretical_max_floor_area_m2": max_floor_area,
            "theoretical_max_ground_coverage_m2": max_ground_coverage,
            "effective_density_percent": capacity_result.get("effective_density_percent"),
            "effective_coverage_percent": capacity_result.get("effective_coverage_percent"),
            "calculation_method": capacity_result.get("calculation_method"),
            "area_basis_status": capacity_result.get("area_basis_status"),
            "calculation_authority_status": capacity_result.get("calculation_authority_status"),
            "multi_zone_policy_status": capacity_result.get("multi_zone_policy_status"),
            "zone_overlap_total_percent": capacity_result.get("zone_overlap_total_percent"),
            "zone_overlap_complete": capacity_result.get("zone_overlap_complete"),
            "calculation_warnings": capacity_result.get("calculation_warnings") or [],
        },
        "registration_summary": {
            "total_related_records": len(related),
            "by_type": dict(type_counter),
            "total_enclosed_extent_m2": safe_sum(enclosed_vals),
            "total_covered_extent_m2": safe_sum(covered_vals),
            "total_uncovered_extent_m2": safe_sum(uncovered_vals),
        },
        "related_properties": related,
        "warnings": warnings,
        "building_summary": {"count": 0, "features": []},
        "contour_summary": {},
        "spatial_checks": {},
    }
    result["structured_rule_analysis"] = evaluate_parcel_rules(result)
    return result



async def get_arcgis_parcel_feature_by_id(parcel_id: int) -> dict[str, Any] | None:
    """Load a parcel directly from the official DLS ArcGIS parcel layer."""
    client = state.get("http")
    if not client:
        return None
    params = {
        "f": "geojson",
        "where": f"SBPI_ID_NO={int(parcel_id)}",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": 1,
    }
    try:
        r = await client.get(PARCEL_QUERY, params=params, timeout=10.0)
        if r.status_code != 200:
            return None
        data = r.json()
        features = data.get("features") or []
        return features[0] if features else None
    except Exception as exc:
        print(f"PLANA ArcGIS parcel fallback failed for {parcel_id}: {exc!r}", file=sys.stderr)
        return None


async def canonical_details_from_arcgis(parcel_id: int) -> dict[str, Any] | None:
    """Build a usable canonical parcel payload without GeneralParcelIdentify.

    This keeps parcel selection alive when the legacy DLS identify endpoint is
    unavailable. ArcGIS exposes parcel geometry, planning-zone names and
    administrative boundaries, but not the full registration/valuation record.
    """
    feature = await get_arcgis_parcel_feature_by_id(parcel_id)
    if not feature:
        return None

    props = feature.get("properties") or {}
    metrics = polygon_geometry_metrics(feature)

    layer_ids = (12, 15, 16, 17)
    zone_result, district_result, municipality_result, quarter_result = await asyncio.gather(
        *(query_layer_intersections(layer_id, feature) for layer_id in layer_ids),
        return_exceptions=False,
    )

    def attrs(result: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(result, dict) or not result.get("ok"):
            return []
        return [
            (f.get("attributes") or {})
            for f in (result.get("features") or [])
            if isinstance(f, dict)
        ]

    zones = []
    seen_zones = set()
    for a in attrs(zone_result):
        name = clean_text(a.get("PLNZNT_NAME"))
        if not name or name in seen_zones:
            continue
        seen_zones.add(name)
        zones.append({
            "zone": name,
            "density_percent": None,
            "coverage_percent": None,
            "max_floors": None,
            "max_height_m": None,
            "remarks": clean_text(a.get("PLNZNT_DESC")),
            "description_en": clean_text(a.get("PLNZNT_DESC")),
            "description_gr": None,
            "affected_extent": None,
            "total_extent": None,
            "overlap_percent": None,
        })

    district_attrs = attrs(district_result)
    municipality_attrs = attrs(municipality_result)
    quarter_attrs = attrs(quarter_result)

    parcel_area = (
        props.get("SHAPE.STArea()")
        or props.get("Parcel Extend")
        or metrics.get("approx_area_m2")
    )
    capacity_result = calculate_zoned_capacity(parcel_area, zones)

    warnings = [
        "Parcel loaded from the official DLS ArcGIS cadastral layers because the detailed DLS identify service is unavailable.",
        "Registration, valuation and exact planning coefficients are pending index enrichment.",
    ]
    if len(zones) > 1:
        warnings.append("Parcel intersects multiple planning zones.")

    result = {
        "parcel": {
            "parcel_id": parcel_id,
            "parcel_number": props.get("PARCEL_NBR"),
            "registration_number": None,
            "district": clean_text(district_attrs[0].get("DIST_NM_E")) if district_attrs else None,
            "municipality": clean_text(municipality_attrs[0].get("VIL_NM_E")) if municipality_attrs else None,
            "quarter": clean_text(quarter_attrs[0].get("QRTR_NM_E")) if quarter_attrs else None,
            "sheet": clean_text(props.get("SHEET")),
            "plan": clean_text(props.get("PLAN_NBR")),
            "block": props.get("BLCK_CODE"),
            "scale": props.get("SRC_SL_CODE"),
            "postal_code": None,
            "house_no": None,
            "parcel_extent_m2": parcel_area,
            "price_2021": None,
            "price_2018": None,
            "price_1980": None,
            "valuation_change_percent": None,
            "is_preserved": False,
            "is_ancient": False,
            "is_common_property": False,
        },
        "planning_zones": zones,
        "development_potential": {
            "theoretical_max_floor_area_m2": capacity_result.get("theoretical_max_floor_area_m2"),
            "theoretical_max_ground_coverage_m2": capacity_result.get("theoretical_max_ground_coverage_m2"),
            "effective_density_percent": capacity_result.get("effective_density_percent"),
            "effective_coverage_percent": capacity_result.get("effective_coverage_percent"),
            "calculation_method": "official_dls_arcgis_fallback",
            "area_basis_status": "official_cadastral_geometry",
            "calculation_authority_status": "screening_pending_zone_coefficient_enrichment",
            "multi_zone_policy_status": capacity_result.get("multi_zone_policy_status"),
            "zone_overlap_total_percent": None,
            "zone_overlap_complete": False,
            "calculation_warnings": list(capacity_result.get("calculation_warnings") or []) + [
                "Planning-zone coefficients are not exposed by the ArcGIS planning-zone layer and remain pending enrichment."
            ],
        },
        "registration_summary": {
            "total_related_records": 0,
            "by_type": {},
            "total_enclosed_extent_m2": None,
            "total_covered_extent_m2": None,
            "total_uncovered_extent_m2": None,
        },
        "related_properties": [],
        "warnings": warnings,
        "building_summary": {"count": 0, "features": []},
        "contour_summary": {},
        "spatial_checks": {},
        "fallback_metadata": {
            "source": "official_dls_arcgis",
            "detailed_identify_used": False,
        },
    }
    result["structured_rule_analysis"] = evaluate_parcel_rules(result)
    return result


async def get_canonical_parcel_details(parcel_id: int) -> dict[str, Any]:
    cached = SITE_CACHE.get(parcel_id)
    now = time.time()
    if cached and now - cached[0] < SITE_CACHE_TTL_SECONDS:
        return cached[1]

    indexed = await get_indexed_parcel_by_id(parcel_id)
    if indexed:
        result = canonical_details_from_index(indexed)
        SITE_CACHE[parcel_id] = (time.time(), result)
        return result

    # Keep the app operational even when GeneralParcelIdentify is 404/509.
    # The official ArcGIS parcel/planning/admin layers are sufficient for a
    # usable preliminary parcel payload and do not require the legacy endpoint.
    result = await canonical_details_from_arcgis(parcel_id)
    if result:
        SITE_CACHE[parcel_id] = (time.time(), result)
        return result

    # Last-resort compatibility path for any parcel not exposed by ArcGIS.
    records = await get_general_identify(parcel_id)
    if not isinstance(records, list) or not records:
        raise HTTPException(status_code=502, detail="Official DLS parcel services are temporarily unavailable.")

    result = normalize_parcel_details(records, parcel_id)
    SITE_CACHE[parcel_id] = (time.time(), result)
    return result


def parcel_planning_cache_key(parcel_details: dict[str, Any]) -> str:
    parcel = parcel_details.get("parcel") or {}
    zones = parcel_details.get("planning_zones") or []
    fingerprint = {
        "parcel_id": parcel.get("parcel_id"),
        "municipality": parcel.get("municipality"),
        "district": parcel.get("district"),
        "zones": [
            {
                "zone": z.get("zone"),
                "density_percent": z.get("density_percent"),
                "coverage_percent": z.get("coverage_percent"),
                "max_floors": z.get("max_floors"),
                "max_height_m": z.get("max_height_m"),
                "overlap_percent": z.get("overlap_percent"),
                "remarks": z.get("remarks"),
            }
            for z in zones
        ],
        "rule_engine_version": RULE_ENGINE_VERSION,
    }
    return json.dumps(
        fingerprint,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


@app.get("/api/parcel-details")
async def parcel_details(parcel_id: int = Query(gt=0)) -> dict[str, Any]:
    details = await get_canonical_parcel_details(parcel_id)
    # Pure deterministic calculation: include it in the detail response so the
    # browser can render options without another API round-trip.
    return {
        **details,
        "initial_proposals": build_viable_development_options(details, None, None),
    }


class ParcelRuleRequest(BaseModel):
    parcel_id: int = Field(gt=0)
    scenario: dict[str, Any] | None = None


@app.post("/api/parcel-rule-analysis")
async def parcel_rule_analysis(payload: ParcelRuleRequest) -> dict[str, Any]:
    parcel_details = await get_canonical_parcel_details(payload.parcel_id)
    return evaluate_parcel_rules(parcel_details, payload.scenario or {})



def fetch_market_observations_for_parcel(
    supabase: Any,
    parcel_details: dict[str, Any],
    *,
    centroid_lat: float | None = None,
    centroid_lon: float | None = None,
    max_rows: int = 1500,
) -> tuple[list[dict[str, Any]], str | None]:
    """Load bounded newest-first geo and administrative market pools."""
    parcel = parcel_details.get("parcel") or {}
    columns = "observation_key,source,source_id,source_class,source_listing_id,source_url,transaction_type,property_type,development_status,bedrooms,bathrooms,covered_area_m2,plot_area_m2,asking_price_eur,asking_rent_monthly_eur,price_per_m2_eur,rent_per_m2_month_eur,latitude,longitude,district,municipality,locality,planning_zone,title,status,first_seen_at,last_seen_at,price_changed_at,original_price_eur,current_price_eur,confidence,source_adapter,source_engine_version"
    collected: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        if centroid_lat is not None and centroid_lon is not None:
            try:
                response = (
                    supabase.table("market_observations").select(columns)
                    .gte("latitude", centroid_lat - 0.14).lte("latitude", centroid_lat + 0.14)
                    .gte("longitude", centroid_lon - 0.17).lte("longitude", centroid_lon + 0.17)
                    .order("last_seen_at", desc=True).limit(max_rows).execute()
                )
                collected.extend(response.data or [])
            except Exception as exc:
                errors.append(f"geo market query failed: {exc}")

        district = parcel.get("district")
        municipality = parcel.get("municipality")
        try:
            query = supabase.table("market_observations").select(columns)
            if district:
                query = query.eq("district", district)
            elif municipality:
                query = query.eq("municipality", municipality)
            else:
                query = None
            if query is not None:
                response = query.order("last_seen_at", desc=True).limit(max_rows).execute()
                collected.extend(response.data or [])
        except Exception as exc:
            errors.append(f"location market query failed: {exc}")

        if not collected and not district and not municipality and centroid_lat is None:
            return [], "Parcel does not contain enough location context to query market observations."
        return collected, "; ".join(errors) if errors else None
    except Exception as exc:
        return [], f"market_observations unavailable: {exc}"




def fetch_official_data_context(supabase: Any) -> dict[str, Any]:
    """Expose official-dataset freshness/provenance without inventing a normalized valuation signal."""
    try:
        response = (
            supabase.table("official_market_datasets")
            .select("source,dataset_key,dataset_name,row_count,synced_at")
            .order("synced_at", desc=True)
            .limit(100)
            .execute()
        )
        rows = response.data or []
        return {
            "status": "available" if rows else "no_imported_datasets",
            "dataset_count": len(rows),
            "latest_synced_at": max((row.get("synced_at") for row in rows if row.get("synced_at")), default=None),
            "sources": sorted({row.get("source") for row in rows if row.get("source")}),
            "datasets": rows,
            "valuation_signal_status": "not_normalized",
            "note": "Official rows are preserved as provenance/raw context; PLANA does not infer sale prices, rents, or construction costs from generic row_data without a dataset-specific normalized signal schema.",
        }
    except Exception as exc:
        return {"status": "unavailable", "dataset_count": 0, "valuation_signal_status": "not_normalized", "error": str(exc)}



def build_parcel_market_analysis(
    parcel_details: dict[str, Any],
    *,
    centroid_lat: float | None = None,
    centroid_lon: float | None = None,
) -> dict[str, Any]:
    parcel = parcel_details.get("parcel") or {}
    cache_key = json.dumps({
        "parcel_id": parcel.get("parcel_id"), "district": parcel.get("district"), "municipality": parcel.get("municipality"),
        "lat": round(centroid_lat, 4) if centroid_lat is not None else None,
        "lon": round(centroid_lon, 4) if centroid_lon is not None else None,
        "engine": MARKET_ENGINE_VERSION,
    }, sort_keys=True)
    cached = MARKET_ANALYSIS_CACHE.get(cache_key)
    now_ts = time.time()
    if cached and now_ts - cached[0] < MARKET_ANALYSIS_CACHE_TTL_SECONDS:
        return {**cached[1], "cached": True}

    supabase = state.get("supabase")
    if not supabase:
        return {"engine_version": MARKET_ENGINE_VERSION, "evidence_status": "market_store_unavailable", "relevant_observation_count": 0, "sale_price_per_m2": None, "rent_per_m2_month": None, "confidence": "low", "warnings": ["Market datastore is not ready."], "source_registry": source_status_summary(), "property_analyses": {}, "official_data_context": {"status": "unavailable"}}

    rows, load_error = fetch_market_observations_for_parcel(supabase, parcel_details, centroid_lat=centroid_lat, centroid_lon=centroid_lon)
    property_analyses = {
        property_type: analyse_market_observations(parcel_details, rows, centroid_lat=centroid_lat, centroid_lon=centroid_lon, property_type=property_type)
        for property_type in ("apartment", "house", "land", "office", "shop")
    }
    result = dict(property_analyses["apartment"])
    result["property_analyses"] = property_analyses
    result["official_data_context"] = fetch_official_data_context(supabase)
    if load_error:
        result.setdefault("warnings", []).insert(0, load_error)
        if not rows:
            result["evidence_status"] = "market_store_unavailable"
    result["source_registry"] = source_status_summary()
    result["cached"] = False
    MARKET_ANALYSIS_CACHE[cache_key] = (time.time(), result)
    return result



class ParcelMarketRequest(BaseModel):
    parcel_id: int = Field(gt=0)
    centroid_lat: float | None = Field(default=None, ge=34.0, le=36.0)
    centroid_lon: float | None = Field(default=None, ge=31.0, le=35.0)


@app.get("/api/market-sources")
def market_sources() -> dict[str, Any]:
    return source_status_summary()


@app.post("/api/parcel-market-analysis")
async def parcel_market_analysis(payload: ParcelMarketRequest) -> dict[str, Any]:
    parcel_details = await get_canonical_parcel_details(payload.parcel_id)
    return await asyncio.to_thread(
        build_parcel_market_analysis,
        parcel_details,
        centroid_lat=payload.centroid_lat,
        centroid_lon=payload.centroid_lon,
    )


class ParcelOpportunityRequest(BaseModel):
    parcel_id: int = Field(gt=0)
    assumptions: dict[str, Any] | None = None
    centroid_lat: float | None = Field(default=None, ge=34.0, le=36.0)
    centroid_lon: float | None = Field(default=None, ge=31.0, le=35.0)


@app.post("/api/parcel-opportunity-analysis")
async def parcel_opportunity_analysis(payload: ParcelOpportunityRequest) -> dict[str, Any]:
    """Capacity + automatic market evidence + deterministic opportunity analysis."""
    parcel_details = await get_canonical_parcel_details(payload.parcel_id)
    market_analysis = await asyncio.to_thread(
        build_parcel_market_analysis,
        parcel_details,
        centroid_lat=payload.centroid_lat,
        centroid_lon=payload.centroid_lon,
    )
    assumptions = merge_automatic_market_assumptions(payload.assumptions or {}, market_analysis)
    result = analyse_parcel_opportunity(parcel_details, assumptions)
    result["market_analysis"] = market_analysis
    result["market"]["evidence_status"] = market_analysis.get("evidence_status")
    result["market"]["automatic_confidence"] = market_analysis.get("confidence")
    result["market"]["automatic_observation_count"] = market_analysis.get("relevant_observation_count")
    result["market"]["automatic_source_count"] = market_analysis.get("source_count")
    result["market"]["automatic_primary_sale_count"] = market_analysis.get("primary_sale_observation_count")
    result["market"]["automatic_primary_source_count"] = market_analysis.get("primary_source_count")
    result["market"]["sale_range_basis"] = market_analysis.get("sale_range_basis")
    result["market"]["rent_range_basis"] = market_analysis.get("rent_range_basis")
    result["market"]["automatic_segments"] = market_analysis.get("segments") or {}
    result["market"]["top_comparables"] = market_analysis.get("top_comparables") or []
    result["market"].setdefault("warnings", [])
    if market_analysis.get("evidence_status") == "automatic_market_observations":
        result["market"]["status"] = "automatic_market_range"
        result["market"]["confidence"] = market_analysis.get("confidence") or "low"
        result["market"]["warnings"] = [
            warning
            for warning in result["market"]["warnings"]
            if not str(warning).startswith("No live comparable-sales or rental dataset")
        ]
    result["market"]["warnings"].extend(market_analysis.get("warnings") or [])
    if market_analysis.get("evidence_status") == "automatic_market_observations":
        result["overall_confidence"] = (
            "medium"
            if result.get("capacity", {}).get("confidence") == "medium"
            and market_analysis.get("confidence") in {"medium", "high"}
            and result.get("financial", {}).get("status") == "calculated_from_explicit_assumptions"
            else "low"
        )
    # Bundle the market-enriched proposals to avoid a second browser round-trip.
    result["proposals"] = build_viable_development_options(parcel_details, None, result)
    return result




def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _zone_option_fallback(parcel_details: dict[str, Any]) -> list[dict[str, Any]]:
    """Fast zone-family use screen used until the deeper planning analysis returns."""
    codes = [str(z.get("zone") or "").strip().casefold() for z in parcel_details.get("planning_zones") or []]
    options: list[str] = []
    if any(code.startswith(("ka", "κα", "h", "η")) for code in codes):
        options.extend(["house", "apartments"])
    if any(code.startswith(("eb", "εβ", "em", "εμ")) for code in codes):
        options.extend(["office", "shop", "mixed_use"])
    if any(code.startswith(("t", "τ")) for code in codes):
        options.extend(["house", "apartments", "tourist"])
    if any(code.startswith(("b", "β")) for code in codes):
        options.append("industrial")
    if not options:
        options = ["house", "apartments"]
    return [
        {"type": item, "status": "conditional", "reason": "Zone-family use screen."}
        for item in dict.fromkeys(options)
    ]


def build_viable_development_options(
    parcel_details: dict[str, Any],
    planning_analysis: dict[str, Any] | None,
    opportunity: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build concise use, programme and investment options for the prototype UI."""
    planning_analysis = planning_analysis or {}
    opportunity = opportunity or analyse_parcel_opportunity(parcel_details, {})
    parcel = parcel_details.get("parcel") or {}
    potential = parcel_details.get("development_potential") or {}
    geometry = parcel_details.get("geometry_metrics") or {}
    pinputs = planning_analysis.get("proposal_inputs") or {}

    front = _num(pinputs.get("front_setback_m"))
    side = _num(pinputs.get("side_setback_m"))
    rear = _num(pinputs.get("rear_setback_m"))
    working_setback = False
    if front is None:
        front, working_setback = 3.0, True
    if side is None:
        side, working_setback = 3.0, True
    if rear is None:
        rear, working_setback = 3.0, True

    longest = _num(geometry.get("longest_edge_m"))
    shortest = _num(geometry.get("shortest_edge_m"))
    envelope_w = max((shortest or 0) - side * 2, 0) if shortest else None
    envelope_l = max((longest or 0) - front - rear, 0) if longest else None
    geometric_envelope = envelope_w * envelope_l if envelope_w is not None and envelope_l is not None else None
    coverage_cap = _num(potential.get("theoretical_max_ground_coverage_m2"))
    footprint_values = [value for value in (geometric_envelope, coverage_cap) if value is not None]
    footprint_cap = min(footprint_values) if footprint_values else None
    gross = _num(potential.get("theoretical_max_floor_area_m2"))
    floors = max([int(_num(z.get("max_floors")) or 0) for z in parcel_details.get("planning_zones") or []] or [0]) or None
    max_height = max([_num(z.get("max_height_m")) or 0 for z in parcel_details.get("planning_zones") or []] or [0]) or None
    per_floor_density = gross / floors if gross and floors else None
    covered_balcony_allowance = round(gross * 0.25, 2) if gross else None
    parcel_area = _num(parcel.get("parcel_extent_m2"))
    open_ground = max(parcel_area - (coverage_cap or 0), 0) if parcel_area is not None and coverage_cap is not None else None

    raw_allowed = planning_analysis.get("allowed_development_options") or _zone_option_fallback(parcel_details)
    allowed_map: dict[str, dict[str, Any]] = {}
    for item in raw_allowed:
        if not isinstance(item, dict) or item.get("status") == "not_supported":
            continue
        kind = str(item.get("type") or "").strip().lower()
        if kind == "commercial":
            for expanded in ("office", "shop"):
                allowed_map.setdefault(expanded, {**item, "type": expanded})
        else:
            allowed_map.setdefault(kind, item)

    assumptions = opportunity.get("assumptions") or {}
    efficiency_low = (_num(assumptions.get("efficiency_low_percent")) or 80.0) / 100.0
    efficiency_high = (_num(assumptions.get("efficiency_high_percent")) or 85.0) / 100.0
    unit_sizes = assumptions.get("unit_sizes_m2") or {"one_bed": 55.0, "two_bed": 85.0, "three_bed": 115.0}
    market_root = opportunity.get("market_analysis") or {}
    property_analyses = market_root.get("property_analyses") or {"apartment": market_root}

    def status_label(status: str) -> str:
        return "allowed" if status == "allowed" else "potentially_allowed"

    def economics_for(
        property_type: str | None,
        net_low: float | None,
        net_high: float | None,
        count_low: float | None,
        count_high: float | None,
    ) -> dict[str, Any]:
        analysis = property_analyses.get(property_type) if property_type else None
        if not analysis or not gross or net_low is None or net_high is None:
            return {"status": "insufficient_market_data", "property_type": property_type}
        sale = analysis.get("sale_price_per_m2") or {}
        rent = analysis.get("rent_per_m2_month") or {}
        sale_low, sale_high = _num(sale.get("low")), _num(sale.get("high"))
        rent_low, rent_high = _num(rent.get("low")), _num(rent.get("high"))
        if sale_low is None or sale_high is None:
            return {
                "status": "insufficient_market_data",
                "property_type": property_type,
                "observation_count": analysis.get("relevant_observation_count"),
            }
        costed_area = gross * SCREENING_BUILT_AREA_FACTOR
        hard_low = costed_area * SCREENING_COST_LOW_EUR_M2
        hard_high = costed_area * SCREENING_COST_HIGH_EUR_M2
        gdv_low, gdv_high = net_low * sale_low, net_high * sale_high
        non_land_low = hard_low * (1 + SCREENING_SOFT_COST_PERCENT / 100.0) + gdv_low * SCREENING_SALES_COST_PERCENT / 100.0
        non_land_high = hard_high * (1 + SCREENING_SOFT_COST_PERCENT / 100.0) + gdv_high * SCREENING_SALES_COST_PERCENT / 100.0
        count_l = max(1.0, count_low or count_high or 1.0)
        count_h = max(count_l, count_high or count_l)
        monthly_low = net_low * rent_low if rent_low is not None else None
        monthly_high = net_high * rent_high if rent_high is not None else None
        residual_low = _screening_residual(net_low, gross, sale_low, SCREENING_COST_HIGH_EUR_M2, 20.0)
        residual_high = _screening_residual(net_high, gross, sale_high, SCREENING_COST_LOW_EUR_M2, 20.0)
        return {
            "status": "calculated",
            "property_type": property_type,
            "sale_price_eur_m2_range": {"low": round(sale_low), "high": round(sale_high)},
            "rent_eur_m2_month_range": ({"low": round(rent_low, 2), "high": round(rent_high, 2)} if rent_low is not None and rent_high is not None else None),
            "net_saleable_m2_range": {"low": round(net_low, 1), "high": round(net_high, 1)},
            "construction_cost_eur_m2_range": {"low": SCREENING_COST_LOW_EUR_M2, "high": SCREENING_COST_HIGH_EUR_M2},
            "costed_built_area_m2": round(costed_area, 1),
            "build_cost_range_eur": {"low": round(hard_low), "high": round(hard_high)},
            "non_land_development_cost_range_eur": {"low": round(non_land_low), "high": round(non_land_high)},
            "gdv_range_eur": {"low": round(gdv_low), "high": round(gdv_high)},
            "sale_per_unit_range_eur": {"low": round(gdv_low / count_h), "high": round(gdv_high / count_l)},
            "whole_scheme_monthly_rent_range_eur": ({"low": round(monthly_low), "high": round(monthly_high)} if monthly_low is not None and monthly_high is not None else None),
            "whole_scheme_annual_rent_range_eur": ({"low": round(monthly_low * 12), "high": round(monthly_high * 12)} if monthly_low is not None and monthly_high is not None else None),
            "rent_per_unit_month_range_eur": ({"low": round(monthly_low / count_h), "high": round(monthly_high / count_l)} if monthly_low is not None and monthly_high is not None else None),
            "residual_land_value_at_20pct_target_poc_eur": {"low": round(min(residual_low, residual_high)), "high": round(max(residual_low, residual_high))},
        }

    cards: list[dict[str, Any]] = []

    house_item = allowed_map.get("house")
    if house_item and gross:
        status = status_label(str(house_item.get("status") or "conditional"))
        net_low, net_high = gross * efficiency_low, gross * efficiency_high
        cards.append({
            "id": "detached_house",
            "type": "house",
            "label": "Detached house",
            "status": status,
            "headline": f"1 house · up to {floors or '—'} floors",
            "metrics": [
                {"label": "Internal area", "value": f"{round(net_low):,}–{round(net_high):,}", "unit": "m²"},
                {"label": "Verandas", "value": round(covered_balcony_allowance or 0), "unit": "m²"},
                {"label": "Parking", "value": "2–4", "unit": "spaces"},
            ],
            "unit_count_low": 1,
            "unit_count_high": 1,
            "parking_low": 2,
            "parking_high": 4,
            "internal_area_low_m2": round(net_low, 1),
            "internal_area_high_m2": round(net_high, 1),
            "veranda_area_m2": covered_balcony_allowance,
            "economics": economics_for("house", net_low, net_high, 1, 1),
        })
        possible_count = min(
            max(1, int(gross // 190)),
            max(1, int((parcel_area or 0) // 300)) if parcel_area else 1,
            4,
        )
        if possible_count >= 2:
            multi_net_low = net_low
            multi_net_high = net_high
            cards.append({
                "id": "multiple_houses",
                "type": "house",
                "label": "Multiple houses",
                "status": status,
                "headline": f"{possible_count} houses",
                "metrics": [
                    {"label": "Average internal", "value": f"{round(multi_net_low / possible_count):,}–{round(multi_net_high / possible_count):,}", "unit": "m² each"},
                    {"label": "Verandas", "value": round((covered_balcony_allowance or 0) / possible_count), "unit": "m² each"},
                    {"label": "Parking", "value": possible_count * 2, "unit": "spaces"},
                ],
                "unit_count_low": possible_count,
                "unit_count_high": possible_count,
                "parking_low": possible_count * 2,
                "parking_high": possible_count * 2,
                "internal_area_low_m2": round(multi_net_low, 1),
                "internal_area_high_m2": round(multi_net_high, 1),
                "veranda_area_m2": covered_balcony_allowance,
                "economics": economics_for("house", multi_net_low, multi_net_high, possible_count, possible_count),
            })

    apartments_item = allowed_map.get("apartments")
    if apartments_item and gross:
        status = status_label(str(apartments_item.get("status") or "conditional"))
        apartment_definitions = [
            ("one_bed", "1-bedroom apartments"),
            ("two_bed", "2-bedroom apartments"),
            ("three_bed", "3-bedroom apartments"),
        ]
        for key, label in apartment_definitions:
            unit_size = max(_num(unit_sizes.get(key)) or 1.0, 1.0)
            count_low = max(int(math.floor(gross * efficiency_low / unit_size)), 0)
            count_high = max(int(math.floor(gross * efficiency_high / unit_size)), count_low)
            if count_high < 1:
                continue
            count_low = max(count_low, 1)
            net_low = count_low * unit_size
            net_high = count_high * unit_size
            visitor_low = math.ceil(count_low / 10) if count_low > 9 else 0
            visitor_high = math.ceil(count_high / 10) if count_high > 9 else 0
            parking_low = count_low + visitor_low
            parking_high = count_high + visitor_high
            cards.append({
                "id": key,
                "type": "apartments",
                "label": label,
                "status": status,
                "headline": f"{count_low}–{count_high} apartments" if count_low != count_high else f"{count_high} apartments",
                "metrics": [
                    {"label": "Apartment size", "value": round(unit_size), "unit": "m²"},
                    {"label": "Total internal", "value": f"{round(net_low):,}–{round(net_high):,}", "unit": "m²"},
                    {"label": "Parking", "value": f"{parking_low}–{parking_high}" if parking_low != parking_high else parking_high, "unit": "spaces"},
                ],
                "unit_count_low": count_low,
                "unit_count_high": count_high,
                "unit_size_m2": round(unit_size, 1),
                "parking_low": parking_low,
                "parking_high": parking_high,
                "internal_area_low_m2": round(net_low, 1),
                "internal_area_high_m2": round(net_high, 1),
                "veranda_area_m2": covered_balcony_allowance,
                "economics": economics_for("apartment", net_low, net_high, count_low, count_high),
            })

    for kind, label in (("office", "Office development"), ("shop", "Shop development")):
        item = allowed_map.get(kind)
        if not item or not gross:
            continue
        net_low, net_high = gross * 0.80, gross * 0.88
        cards.append({
            "id": kind,
            "type": kind,
            "label": label,
            "status": status_label(str(item.get("status") or "conditional")),
            "headline": f"{round(net_low):,}–{round(net_high):,} m² usable area",
            "metrics": [
                {"label": "Floors", "value": floors, "unit": "maximum"},
                {"label": "Footprint", "value": round(footprint_cap or 0), "unit": "m²"},
                {"label": "Parking", "value": "Layout dependent", "unit": ""},
            ],
            "unit_count_low": 1,
            "unit_count_high": 1,
            "parking_low": None,
            "parking_high": None,
            "internal_area_low_m2": round(net_low, 1),
            "internal_area_high_m2": round(net_high, 1),
            "veranda_area_m2": covered_balcony_allowance,
            "economics": economics_for(kind, net_low, net_high, 1, 1),
        })

    mixed_item = allowed_map.get("mixed_use")
    if mixed_item and gross:
        cards.append({
            "id": "mixed_use",
            "type": "mixed_use",
            "label": "Mixed-use development",
            "status": status_label(str(mixed_item.get("status") or "conditional")),
            "headline": "Retail or office ground floor with residential above",
            "metrics": [
                {"label": "Commercial test", "value": round(gross * 0.20), "unit": "m²"},
                {"label": "Residential test", "value": round(gross * 0.80), "unit": "m²"},
                {"label": "Floors", "value": floors, "unit": "maximum"},
            ],
            "unit_count_low": None,
            "unit_count_high": None,
            "internal_area_low_m2": round(gross * 0.80, 1),
            "internal_area_high_m2": round(gross * 0.85, 1),
            "veranda_area_m2": covered_balcony_allowance,
            "economics": {"status": "insufficient_commercial_market_data", "property_type": "mixed_use"},
        })

    allowed_uses = []
    use_labels = {
        "house": "House",
        "apartments": "Apartments",
        "office": "Office",
        "shop": "Shop",
        "mixed_use": "Mixed use",
        "tourist": "Tourist use",
        "industrial": "Industrial use",
    }
    for kind, item in allowed_map.items():
        if kind not in use_labels:
            continue
        allowed_uses.append({
            "type": kind,
            "label": use_labels[kind],
            "status": status_label(str(item.get("status") or "conditional")),
        })

    land_analysis = property_analyses.get("land") or {}
    land_sale = land_analysis.get("sale_price_per_m2") or {}
    land_low_m2, land_high_m2 = _num(land_sale.get("low")), _num(land_sale.get("high"))
    dls_value = _num(parcel.get("price_2021"))
    value_estimate: dict[str, Any] | None = None
    if parcel_area and land_low_m2 is not None and land_high_m2 is not None:
        value_estimate = {
            "low_eur": round(parcel_area * land_low_m2),
            "high_eur": round(parcel_area * land_high_m2),
            "method": "nearby_land_listings",
        }
    elif dls_value is not None:
        value_estimate = {
            "low_eur": round(dls_value * 0.90),
            "high_eur": round(dls_value * 1.10),
            "method": "dls_2021_value_context",
        }
    else:
        residuals = [
            card.get("economics", {}).get("residual_land_value_at_20pct_target_poc_eur")
            for card in cards
        ]
        residuals = [item for item in residuals if isinstance(item, dict) and item.get("low") is not None and item.get("high") is not None]
        if residuals:
            value_estimate = {
                "low_eur": min(int(item["low"]) for item in residuals),
                "high_eur": max(int(item["high"]) for item in residuals),
                "method": "development_residual",
            }

    if value_estimate:
        plot_low = float(value_estimate["low_eur"])
        plot_high = float(value_estimate["high_eur"])
        for card in cards:
            economics = card.get("economics") or {}
            non_land = economics.get("non_land_development_cost_range_eur") or {}
            gdv = economics.get("gdv_range_eur") or {}
            non_land_low, non_land_high = _num(non_land.get("low")), _num(non_land.get("high"))
            gdv_low, gdv_high = _num(gdv.get("low")), _num(gdv.get("high"))
            if None in (non_land_low, non_land_high, gdv_low, gdv_high):
                continue
            total_low = plot_low + non_land_low
            total_high = plot_high + non_land_high
            profit_low = gdv_low - total_high
            profit_high = gdv_high - total_low
            roi_low = profit_low / total_high * 100 if total_high > 0 else None
            roi_high = profit_high / total_low * 100 if total_low > 0 else None
            annual = economics.get("whole_scheme_annual_rent_range_eur") or {}
            annual_low, annual_high = _num(annual.get("low")), _num(annual.get("high"))
            economics["plot_price_range_eur"] = {"low": round(plot_low), "high": round(plot_high)}
            economics["total_investment_range_eur"] = {"low": round(total_low), "high": round(total_high)}
            economics["estimated_profit_range_eur"] = {"low": round(profit_low), "high": round(profit_high)}
            economics["roi_range_percent"] = ({"low": round(roi_low, 1), "high": round(roi_high, 1)} if roi_low is not None and roi_high is not None else None)
            if annual_low is not None and annual_high is not None:
                economics["gross_rental_yield_range_percent"] = {
                    "low": round(annual_low / total_high * 100, 2),
                    "high": round(annual_high / total_low * 100, 2),
                }

    def midpoint(value: dict[str, Any] | None) -> float | None:
        if not value:
            return None
        low, high = _num(value.get("low")), _num(value.get("high"))
        return (low + high) / 2 if low is not None and high is not None else None

    financially_modelled = [card for card in cards if midpoint((card.get("economics") or {}).get("total_investment_range_eur")) is not None]
    best_profit = max(financially_modelled, key=lambda card: midpoint((card.get("economics") or {}).get("estimated_profit_range_eur")) or -math.inf, default=None)
    best_rent = max(financially_modelled, key=lambda card: midpoint((card.get("economics") or {}).get("gross_rental_yield_range_percent")) or -math.inf, default=None)
    lowest_cost = min(financially_modelled, key=lambda card: midpoint((card.get("economics") or {}).get("total_investment_range_eur")) or math.inf, default=None)

    return {
        "engine_version": "plana-investment-prototype-v1",
        "allowed_uses": allowed_uses,
        "setbacks": {
            "front_m": front,
            "side_m": side,
            "rear_m": rear,
            "status": "working" if working_setback else str(pinputs.get("setback_status") or "confirmed"),
            "envelope_width_m": round(envelope_w, 2) if envelope_w is not None else None,
            "envelope_length_m": round(envelope_l, 2) if envelope_l is not None else None,
            "working_footprint_cap_m2": round(footprint_cap, 2) if footprint_cap is not None else None,
        },
        "space_strategy": {
            "density_counted_floor_area_m2": round(gross, 2) if gross is not None else None,
            "ground_coverage_m2": round(coverage_cap, 2) if coverage_cap is not None else None,
            "planning_max_floors": floors,
            "planning_max_height_m": round(max_height, 2) if max_height is not None else None,
            "indicative_density_area_per_floor_m2": round(per_floor_density, 2) if per_floor_density is not None else None,
            "indicative_covered_balcony_veranda_allowance_m2": covered_balcony_allowance,
            "indicative_total_constructed_area_low_m2": round((gross or 0) * 1.15, 1) if gross else None,
            "indicative_total_constructed_area_high_m2": round((gross or 0) * 1.28, 1) if gross else None,
            "remaining_open_ground_m2": round(open_ground, 2) if open_ground is not None else None,
        },
        "development_options": cards,
        "investment_summary": {
            "best_resale_option_id": best_profit.get("id") if best_profit else None,
            "best_rental_option_id": best_rent.get("id") if best_rent else None,
            "lowest_investment_option_id": lowest_cost.get("id") if lowest_cost else None,
        },
        "value_estimate": value_estimate,
        "dls_2021_value_eur": dls_value,
        "official_data_context": market_root.get("official_data_context"),
    }




# ============================================================
# PLANA DECISION INTELLIGENCE V2
# ============================================================

DECISION_ENGINE_VERSION = "plana-decision-v3"
SCREENING_COST_LOW_EUR_M2 = float(os.getenv("PLANA_SCREENING_COST_LOW_EUR_M2", "1500"))
SCREENING_COST_HIGH_EUR_M2 = float(os.getenv("PLANA_SCREENING_COST_HIGH_EUR_M2", "1900"))
SCREENING_BUILT_AREA_FACTOR = float(os.getenv("PLANA_SCREENING_BUILT_AREA_FACTOR", "1.15"))
SCREENING_SOFT_COST_PERCENT = float(os.getenv("PLANA_SCREENING_SOFT_COST_PERCENT", "18"))
SCREENING_SALES_COST_PERCENT = float(os.getenv("PLANA_SCREENING_SALES_COST_PERCENT", "3"))

def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))

def _option_numeric(option: dict[str, Any], label: str) -> float | None:
    for item in option.get("metrics") or []:
        if item.get("label") == label:
            value = item.get("value")
            if isinstance(value, (int, float)):
                return float(value)
            match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
            return float(match.group(0)) if match else None
    return None

def _screening_residual(
    net_saleable_m2: float,
    gross_density_m2: float,
    sale_price_m2: float,
    construction_cost_m2: float,
    target_poc_percent: float,
) -> float:
    revenue = max(net_saleable_m2, 0) * max(sale_price_m2, 0)
    hard_cost = max(gross_density_m2, 0) * SCREENING_BUILT_AREA_FACTOR * max(construction_cost_m2, 0)
    non_land_before_sales = hard_cost * (1 + SCREENING_SOFT_COST_PERCENT / 100.0)
    sales_cost = revenue * SCREENING_SALES_COST_PERCENT / 100.0
    target = max(target_poc_percent, 0) / 100.0
    # revenue = (non-land + land) * (1 + target) + sales cost
    return max((revenue - sales_cost) / (1 + target) - non_land_before_sales, 0.0)

def _evidence_weighted_score(
    components: dict[str, float | None],
    weights: dict[str, float],
) -> tuple[int, list[str]]:
    """Unknown evidence is omitted rather than scored as bad evidence."""
    active = {key: value for key, value in components.items() if value is not None and key in weights}
    if not active:
        return 0, []
    denominator = sum(weights[key] for key in active) or 1.0
    score = sum(float(active[key]) * weights[key] for key in active) / denominator
    return int(round(_clamp(score))), list(active.keys())



def build_decision_intelligence(
    parcel_details: dict[str, Any],
    proposals: dict[str, Any],
    opportunity: dict[str, Any] | None,
    *,
    asking_price_eur: float | None = None,
    target_profit_on_cost_percent: float = 20.0,
) -> dict[str, Any]:
    opportunity = opportunity or {}
    potential = parcel_details.get("development_potential") or {}
    gross = _num(potential.get("theoretical_max_floor_area_m2")) or 0.0
    coverage = _num(potential.get("theoretical_max_ground_coverage_m2")) or 0.0
    parcel_area = _num((parcel_details.get("parcel") or {}).get("parcel_extent_m2")) or 0.0
    market_root = opportunity.get("market_analysis") or {}
    property_analyses = market_root.get("property_analyses") or {"apartment": market_root}

    planning_rubric = {"allowed": 85.0, "conditional": 60.0, "preliminary_capacity_fit": 55.0, "lower_density_screen": 48.0, "conditional_use_screen": 50.0, "screening_pending_zone_coefficients": 35.0}
    confidence_rubric = {"low": 40.0, "medium": 65.0, "high": 85.0}

    def use_analysis(kind: str) -> dict[str, Any] | None:
        return property_analyses.get("apartment" if kind == "apartments" else ("house" if kind == "house" else ""))

    def evidence_market_score(analysis: dict[str, Any] | None) -> float | None:
        if not analysis or int(analysis.get("relevant_observation_count") or 0) <= 0:
            return None
        confidence = str(analysis.get("confidence") or "low").lower()
        score = confidence_rubric.get(confidence, 40.0)
        primary = int(analysis.get("primary_sale_observation_count") or analysis.get("primary_rent_observation_count") or 0)
        sources = int(analysis.get("primary_source_count") or 0)
        score += min(primary, 20) / 20.0 * 8.0
        score += min(sources, 3) / 3.0 * 5.0
        distance = _num(analysis.get("median_distance_km"))
        if distance is None:
            score -= 5.0
        elif distance > 10:
            score -= min(12.0, distance - 10.0)
        return _clamp(score, 20, 98)

    def financial_screen_score(economics: dict[str, Any]) -> float | None:
        poc = economics.get("pre_land_profit_on_cost_range_percent") or {}
        low, high = _num(poc.get("low")), _num(poc.get("high"))
        if low is None or high is None:
            return None
        midpoint = (low + high) / 2.0
        return _clamp(30.0 + midpoint * 2.0, 5, 95)

    option_scores: list[dict[str, Any]] = []
    for option in proposals.get("development_options") or []:
        kind, status = str(option.get("type") or ""), str(option.get("status") or "conditional")
        planning_score = planning_rubric.get(status, 45.0)
        density_efficiency = _clamp((gross / parcel_area * 60.0) if parcel_area else 50.0, 25, 96)
        analysis = use_analysis(kind)
        market_score = evidence_market_score(analysis)
        financial_score = financial_screen_score(option.get("economics") or {})
        parking_score = 68.0
        if kind == "apartments":
            parking_low = _option_numeric(option, "Parking signal")
            footprint = _num((proposals.get("setbacks") or {}).get("working_footprint_cap_m2")) or coverage
            parking_capacity_high = int(footprint / 30.0) if footprint else 0
            if parking_low and parking_capacity_high:
                parking_score = _clamp(100 * parking_capacity_high / parking_low, 35, 96)
        elif kind == "mixed_use":
            market_score = None
            financial_score = None

        component_values = {"financial": financial_score, "market": market_score, "development": density_efficiency, "planning": planning_score, "site": parking_score}
        score, scored_components = _evidence_weighted_score(component_values, {"financial": .25, "market": .20, "development": .20, "planning": .20, "site": .15})
        option_scores.append({"type": kind, "label": option.get("label"), "score": score, "planning_score": round(planning_score), "market_score": round(market_score) if market_score is not None else None, "development_efficiency_score": round(density_efficiency), "financial_score": round(financial_score) if financial_score is not None else None, "site_constraint_score": round(parking_score), "scored_components": scored_components, "score_evidence_status": "use_specific_market_enriched" if market_score is not None else "capacity_led", "status": status, "scoring_basis": {"planning": "explicit status rubric", "market": "market-engine confidence + primary comparable/source coverage + distance", "financial": "pre-land POC screening range; excludes acquisition cost" if financial_score is not None else "withheld"}})
    option_scores.sort(key=lambda x: x["score"], reverse=True)
    best = option_scores[0] if option_scores else None

    apartment = next((x for x in proposals.get("development_options") or [] if x.get("type") == "apartments"), None)
    parking_test = None
    if apartment:
        footprint = _num((proposals.get("setbacks") or {}).get("working_footprint_cap_m2")) or coverage
        parking_required_low = _option_numeric(apartment, "Parking signal")
        parking_text = next((str(m.get("value")) for m in apartment.get("metrics") or [] if m.get("label") == "Parking signal"), "")
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", parking_text)]
        parking_required_high = max(nums) if nums else parking_required_low
        basement_low, basement_high = (int(footprint / 35.0), int(footprint / 30.0)) if footprint else (None, None)
        viable = basement_high is not None and parking_required_high is not None and basement_high >= parking_required_high
        parking_test = {"required_low": round(parking_required_low) if parking_required_low is not None else None, "required_high": round(parking_required_high) if parking_required_high is not None else None, "basement_capacity_low": basement_low, "basement_capacity_high": basement_high, "preliminary_status": "likely_viable" if viable else "constraint_risk", "recommended_strategy": "Test basement parking first" if viable else "Parking geometry may constrain the unit scenario", "method": "Preliminary geometric screening at 30–35 m² gross basement area per stall, including circulation/ramp inefficiency."}

    space = proposals.get("space_strategy") or {}
    max_strategy = {"density_counted_floor_area_m2": space.get("density_counted_floor_area_m2"), "covered_balcony_veranda_allowance_m2": space.get("indicative_covered_balcony_veranda_allowance_m2"), "indicative_total_built_area_m2": round((_num(space.get("density_counted_floor_area_m2")) or 0) + (_num(space.get("indicative_covered_balcony_veranda_allowance_m2")) or 0)) or None, "parking_strategy": (parking_test or {}).get("recommended_strategy"), "actions": ["Test qualifying basement parking before sacrificing ground coverage to surface parking.", "Allocate qualifying covered balconies/verandas within the Order 4/2026 exclusion framework.", "Test entrance, circulation and support-space exclusions only against their specific rule conditions."]}

    best_option = next((x for x in proposals.get("development_options") or [] if best and x.get("type") == best.get("type")), None)
    acquisition = None
    if best_option and isinstance(best_option.get("economics"), dict):
        residual = best_option["economics"].get("residual_land_value_at_20pct_target_poc_eur")
        if residual:
            acquisition = {"target_profit_on_cost_percent": 20.0, "maximum_land_price_range": {"low_eur": residual.get("low"), "high_eur": residual.get("high")}, "status": "screening_estimate", "basis": best_option["economics"].get("basis")}
            if asking_price_eur is not None:
                low, high = _num(residual.get("low")), _num(residual.get("high"))
                if low is not None and high is not None:
                    acquisition["asking_price_test"] = {"asking_price_eur": round(asking_price_eur), "verdict": "Worth further investigation" if asking_price_eur <= low else ("Within indicative range" if asking_price_eur <= high else "Above indicative development range"), "band": "below_range" if asking_price_eur <= low else ("within_range" if asking_price_eur <= high else "above_range"), "difference_to_midpoint_eur": round(asking_price_eur - ((low + high) / 2.0))}

    best_analysis = use_analysis(best.get("type") if best else "") or {}
    sale_range = best_analysis.get("sale_price_per_m2") or {}
    market_signal = None
    if sale_range.get("low") is not None and sale_range.get("high") is not None:
        market_signal = {"property_type": best_analysis.get("property_type"), "sale_low_eur_m2": sale_range.get("low"), "sale_high_eur_m2": sale_range.get("high"), "observation_count": best_analysis.get("relevant_observation_count"), "primary_source_count": best_analysis.get("primary_source_count"), "median_distance_km": best_analysis.get("median_distance_km"), "confidence": best_analysis.get("confidence"), "comparables": (best_analysis.get("top_comparables") or [])[:5], "segments": best_analysis.get("segments") or {}, "basis": best_analysis.get("sale_range_basis")}

    score = best["score"] if best else 0
    label = "Standout opportunity" if score >= 85 else ("Strong opportunity" if score >= 75 else ("Promising opportunity" if score >= 65 else ("Review opportunity" if score >= 52 else "High-uncertainty opportunity")))
    return {"engine_version": DECISION_ENGINE_VERSION, "plana_score": score, "score_label": label, "score_components": {"financial_opportunity": best.get("financial_score") if best else None, "market_strength": best.get("market_score") if best else None, "development_efficiency": best.get("development_efficiency_score") if best else None, "planning_certainty": best.get("planning_score") if best else None, "site_constraints": best.get("site_constraint_score") if best else None}, "option_scores": option_scores, "best_use": best, "acquisition": acquisition, "parking_test": parking_test, "maximisation_strategy": max_strategy, "market_signal": market_signal, "official_data_context": market_root.get("official_data_context"), "disclaimer": "PLANA Score is an evidence-weighted preliminary screen. Financial scoring uses a pre-land POC screen and excludes acquisition cost; acquisition outputs are residual-land screens, not valuations, planning approvals or investment recommendations."}



# ============================================================
# GUIDED OPPORTUNITY FINDER
# ============================================================

OPPORTUNITY_SCAN_LIMIT = int(os.getenv("PLANA_OPPORTUNITY_SCAN_LIMIT", "320"))
OPPORTUNITY_DETAIL_LIMIT = int(os.getenv("PLANA_OPPORTUNITY_DETAIL_LIMIT", "28"))
OPPORTUNITY_SCAN_TIME_BUDGET_SECONDS = float(os.getenv("PLANA_OPPORTUNITY_SCAN_TIME_BUDGET_SECONDS", "10.5"))
OPPORTUNITY_CANDIDATE_TIMEOUT_SECONDS = float(os.getenv("PLANA_OPPORTUNITY_CANDIDATE_TIMEOUT_SECONDS", "5.5"))
OPPORTUNITY_SCAN_CACHE = BoundedCache(max(64, WEB_CACHE_MAX_ENTRIES // 4))
OPPORTUNITY_SCAN_CACHE_TTL_SECONDS = 900

class OpportunityFinderRequest(BaseModel):
    west: float = Field(ge=31.0, le=35.0)
    south: float = Field(ge=34.0, le=36.0)
    east: float = Field(ge=31.0, le=35.0)
    north: float = Field(ge=34.0, le=36.0)
    development: str = Field(default="apartments")
    scale: str = Field(default="any")
    budget: str = Field(default="flexible")
    budget_mode: str = Field(default="preferred")
    priorities: list[str] = Field(default_factory=lambda: ["return", "market"])
    max_results: int = Field(default=20, ge=3, le=30)

def _finder_weights(priorities: list[str]) -> dict[str, float]:
    weights = {"financial": .25, "market": .20, "development": .20, "planning": .20, "site": .15}
    mapping = {
        "return": {"financial": .42, "market": .20, "development": .20, "planning": .10, "site": .08},
        "planning": {"financial": .10, "market": .15, "development": .10, "planning": .40, "site": .25},
        "market": {"financial": .20, "market": .40, "development": .15, "planning": .15, "site": .10},
        "development": {"financial": .15, "market": .10, "development": .45, "planning": .15, "site": .15},
        "underused": {"financial": .15, "market": .10, "development": .45, "planning": .15, "site": .15},
        "quick": {"financial": .15, "market": .15, "development": .10, "planning": .30, "site": .30},
    }
    chosen = [mapping[x] for x in priorities[:2] if x in mapping]
    if not chosen:
        return weights
    averaged = {k: sum(x[k] for x in chosen) / len(chosen) for k in weights}
    total = sum(averaged.values()) or 1
    return {k: v / total for k, v in averaged.items()}

def _finder_budget_cap(budget: str) -> float | None:
    return {"under500": 500_000, "500to1m": 1_000_000, "1to2m": 2_000_000}.get(budget)

def _finder_scale_fit(units: float | None, scale: str) -> float:
    if units is None:
        return 45.0
    if scale == "small":
        return 95.0 if units <= 10 else _clamp(95 - (units - 10) * 5, 25, 95)
    if scale == "medium":
        return 95.0 if 10 <= units <= 25 else _clamp(95 - min(abs(units - 10), abs(units - 25)) * 5, 25, 95)
    if scale == "large":
        return 95.0 if units >= 25 else _clamp(35 + units * 2.4, 25, 95)
    return 80.0

async def _query_parcels_in_bbox(payload: OpportunityFinderRequest) -> list[dict[str, Any]]:
    """Read parcels across a 4x4 grid so ArcGIS response ordering cannot cluster the finder."""
    client = state.get("http")
    if not client:
        raise HTTPException(status_code=503, detail="HTTP service is not ready yet.")

    cols = rows = 4
    dx = (payload.east - payload.west) / cols
    dy = (payload.north - payload.south) / rows
    per_tile_limit = max(12, min(24, math.ceil(OPPORTUNITY_SCAN_LIMIT / (cols * rows))))

    async def query_tile(col: int, row: int) -> list[dict[str, Any]]:
        west = payload.west + col * dx
        east = payload.east if col == cols - 1 else west + dx
        south = payload.south + row * dy
        north = payload.north if row == rows - 1 else south + dy
        params = {
            "f": "geojson",
            "where": "1=1",
            "geometry": json.dumps({
                "xmin": west, "ymin": south, "xmax": east, "ymax": north,
                "spatialReference": {"wkid": 4326},
            }),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "SBPI_ID_NO,PARCEL_NBR,SHEET,PLAN_NBR,BLCK_CODE",
            "returnGeometry": "true",
            "resultRecordCount": per_tile_limit,
        }
        try:
            response = await client.get(PARCEL_QUERY, params=params, timeout=8.0)
            if response.status_code != 200:
                return []
            data = response.json()
            return [] if data.get("error") else (data.get("features") or [])
        except Exception:
            return []

    tile_results = await asyncio.gather(
        *[query_tile(col, row) for row in range(rows) for col in range(cols)],
        return_exceptions=True,
    )
    deduped: dict[int, dict[str, Any]] = {}
    for result in tile_results:
        if not isinstance(result, list):
            continue
        for feature in result:
            try:
                parcel_id = int((feature.get("properties") or {}).get("SBPI_ID_NO"))
            except Exception:
                continue
            deduped.setdefault(parcel_id, feature)
            if len(deduped) >= OPPORTUNITY_SCAN_LIMIT:
                break
    return list(deduped.values())

async def _finder_candidate(feature: dict[str, Any], payload: OpportunityFinderRequest, semaphore: asyncio.Semaphore) -> dict[str, Any] | None:
    props = feature.get("properties") or {}
    parcel_id = props.get("SBPI_ID_NO")
    try:
        parcel_id = int(parcel_id)
    except Exception:
        return None
    async with semaphore:
        try:
            details = await get_canonical_parcel_details(parcel_id)
        except Exception:
            return None
    opportunity = analyse_parcel_opportunity(details, {})
    proposals = build_viable_development_options(details, None, opportunity)
    decision = build_decision_intelligence(details, proposals, opportunity)
    options = decision.get("option_scores") or []
    wanted = payload.development
    wanted_type = {"apartments": "apartments", "houses": "house", "mixed": "mixed_use", "any": None}.get(wanted, wanted)
    option = next((x for x in options if wanted_type is None or x.get("type") == wanted_type), None)
    if option is None:
        return None
    proposal = next((x for x in proposals.get("development_options") or [] if x.get("type") == option.get("type")), {})
    unit_text = str(proposal.get("headline") or "")
    unit_nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", unit_text)]
    units_mid = sum(unit_nums[:2]) / min(len(unit_nums), 2) if unit_nums else None
    scale_fit = _finder_scale_fit(units_mid, payload.scale)
    weights = _finder_weights(payload.priorities)
    components: dict[str, float | None] = {
        "financial": _num(option.get("financial_score")),
        "market": _num(option.get("market_score")),
        "development": ((_num(option.get("development_efficiency_score")) or 55.0) + scale_fit) / 2,
        "planning": _num(option.get("planning_score")) or 60.0,
        "site": _num(option.get("site_constraint_score")) or 60.0,
    }
    personalised, scored_components = _evidence_weighted_score(components, weights)
    parcel = details.get("parcel") or {}
    potential = details.get("development_potential") or {}
    existing = _num((details.get("registration_summary") or {}).get("total_enclosed_extent_m2")) or 0
    capacity = _num(potential.get("theoretical_max_floor_area_m2")) or 0
    gap = max(capacity - existing, 0)
    underused_percent = round(gap / capacity * 100) if capacity > 0 else None
    if "underused" in payload.priorities and underused_percent is not None:
        personalised = round(_clamp(.72 * personalised + .28 * underused_percent))
    geom_metrics = polygon_geometry_metrics(feature)
    return {
        "parcel_id": parcel_id,
        "parcel_number": parcel.get("parcel_number") or props.get("PARCEL_NBR"),
        "district": parcel.get("district"),
        "municipality": parcel.get("municipality"),
        "quarter": parcel.get("quarter"),
        "area_m2": parcel.get("parcel_extent_m2"),
        "zone": " / ".join(str(z.get("zone")) for z in details.get("planning_zones") or [] if z.get("zone")),
        "floor_capacity_m2": potential.get("theoretical_max_floor_area_m2"),
        "units_low": int(min(unit_nums)) if unit_nums else None,
        "units_high": int(max(unit_nums)) if unit_nums else None,
        "development_gap_m2": round(gap, 1) if capacity else None,
        "underused_percent": underused_percent,
        "score": int(_clamp(personalised)),
        "score_label": "Standout" if personalised >= 85 else ("Strong" if personalised >= 75 else ("Promising" if personalised >= 65 else "Worth a look")),
        "score_evidence_status": option.get("score_evidence_status") or "capacity_led",
        "scored_components": scored_components,
        "best_use": option.get("label"),
        "planning_status": option.get("status"),
        "centroid_lat": geom_metrics.get("centroid_lat"),
        "centroid_lon": geom_metrics.get("centroid_lon"),
        "feature": feature,
        "why": [
            x for x in [
                f"{underused_percent}% preliminary development gap" if underused_percent is not None and underused_percent >= 65 else None,
                "Strong preliminary development efficiency" if components["development"] >= 72 else None,
                "Higher planning certainty in the current evidence" if components["planning"] >= 75 else None,
                f"Fits the selected {payload.scale} opportunity scale" if scale_fit >= 85 and payload.scale != "any" else None,
            ] if x
        ][:3],
    }

def _indexed_scale_fit(row: dict[str, Any], scale: str) -> float:
    low = _num(row.get("apartment_units_low"))
    high = _num(row.get("apartment_units_high"))
    units = ((low + high) / 2) if low is not None and high is not None else (low or high)
    return _finder_scale_fit(units, scale)



def _indexed_candidate(row: dict[str, Any], payload: OpportunityFinderRequest) -> dict[str, Any] | None:
    wanted = payload.development
    accepted = {"allowed", "conditional", "preliminary_capacity_fit", "lower_density_screen", "conditional_use_screen"}
    status_field = "apartment_status" if wanted == "apartments" else ("house_status" if wanted == "houses" else "mixed_use_status")
    planning_status = row.get(status_field)
    if planning_status not in accepted:
        return None

    scale_fit = _indexed_scale_fit(row, payload.scale)
    weights = _finder_weights(payload.priorities)
    components = {"financial": _num(row.get("financial_score")), "market": _num(row.get("market_score")), "development": ((_num(row.get("development_score")) or 55.0) + scale_fit) / 2, "planning": _num(row.get("planning_score")), "site": _num(row.get("site_score"))}
    score, scored_components = _evidence_weighted_score(components, weights)
    gap_pct = _num(row.get("development_gap_percent"))
    if "underused" in payload.priorities and gap_pct is not None:
        score = round(_clamp(.72 * score + .28 * gap_pct))
    confidence = _num(row.get("data_confidence")) or 0
    risk_adjusted = round(score * (.72 + .28 * confidence / 100))
    final_score = max(0, min(100, risk_adjusted))
    best_use = row.get("best_use") or "Development opportunity"
    return {"parcel_id": row.get("parcel_id"), "parcel_number": row.get("parcel_number"), "district": row.get("district"), "municipality": row.get("municipality"), "quarter": row.get("quarter"), "area_m2": row.get("parcel_area_m2"), "zone": row.get("planning_zone"), "floor_capacity_m2": row.get("floor_capacity_m2"), "units_low": row.get("house_units_low") if wanted == "houses" else row.get("apartment_units_low"), "units_high": row.get("house_units_high") if wanted == "houses" else row.get("apartment_units_high"), "development_gap_m2": row.get("development_gap_m2"), "underused_percent": gap_pct, "score": final_score, "raw_opportunity_score": score, "confidence": round(confidence), "score_label": "Standout" if final_score >= 85 else ("Strong" if final_score >= 75 else ("Promising" if final_score >= 65 else "Worth a look")), "best_use": best_use, "planning_status": planning_status, "centroid_lat": row.get("centroid_lat"), "centroid_lon": row.get("centroid_lon"), "score_evidence_status": "market_enriched" if row.get("market_score") is not None else "capacity_led", "scored_components": scored_components, "why": [x for x in [f"{round(gap_pct)}% preliminary development gap" if gap_pct is not None and gap_pct >= 65 else None, "Strong development efficiency" if (_num(row.get("development_score")) or 0) >= 72 else None, "Higher planning certainty" if (_num(row.get("planning_score")) or 0) >= 75 else None, f"{round(confidence)}% indexed data confidence" if confidence >= 70 else None] if x][:3]}



async def _find_indexed_opportunities(payload: OpportunityFinderRequest) -> dict[str, Any] | None:
    supabase = state.get("supabase")
    if not supabase:
        return None
    try:
        response = await asyncio.to_thread(
            lambda: supabase.rpc("find_plana_parcels", {
                "p_west": payload.west,
                "p_south": payload.south,
                "p_east": payload.east,
                "p_north": payload.north,
                "p_limit": 750,
            }).execute()
        )
        rows = response.data or []
    except Exception as exc:
        print(f"PLANA index finder fallback: {exc!r}", file=sys.stderr)
        return None
    if not rows:
        return None
    candidates = [x for row in rows if (x := _indexed_candidate(row, payload))]
    candidates.sort(key=lambda x: (x["score"], x.get("confidence") or 0), reverse=True)
    # During initial database population, indexed rows can exist before their
    # development-status fields are complete. In that state, fall back to live DLS
    # instead of returning an empty or nearly empty opportunity search.
    minimum_index_candidates = min(5, payload.max_results)
    if len(candidates) < minimum_index_candidates:
        known_ids = {x.get("parcel_id") for x in candidates}
        for row in rows:
            if row.get("parcel_id") in known_ids:
                continue
            candidates.append({
                "parcel_id": row.get("parcel_id"),
                "parcel_number": row.get("parcel_number"),
                "district": row.get("district"),
                "municipality": row.get("municipality"),
                "quarter": row.get("quarter"),
                "area_m2": row.get("parcel_area_m2"),
                "zone": row.get("planning_zone"),
                "floor_capacity_m2": row.get("floor_capacity_m2"),
                "units_low": None,
                "units_high": None,
                "development_gap_m2": row.get("development_gap_m2"),
                "underused_percent": row.get("development_gap_percent"),
                "score": round(_num(row.get("risk_adjusted_score")) or _num(row.get("opportunity_score")) or 50),
                "score_label": "Screen",
                "best_use": row.get("best_use") or "Open parcel for full analysis",
                "planning_status": "indexed_pending_enrichment",
                "centroid_lat": row.get("centroid_lat"),
                "centroid_lon": row.get("centroid_lon"),
                "score_evidence_status": "indexed_discovery",
                "scored_components": [],
                "why": ["Indexed Cyprus parcel", "Detailed enrichment is still building"],
            })
            if len(candidates) >= payload.max_results:
                break
        candidates.sort(key=lambda x: (x["score"], x.get("confidence") or 0), reverse=True)
    budget_cap = _finder_budget_cap(payload.budget)
    return {
        "source": "plana_index_v1",
        "scanned_parcels": len(rows),
        "completed_parcels": len(rows),
        "candidate_parcels": len(candidates),
        "standout_count": sum(1 for x in candidates if x["score"] >= 85),
        "partial_results": False,
        "results": candidates[:payload.max_results],
        "weights": _finder_weights(payload.priorities),
        "brief": payload.model_dump(exclude={"west", "south", "east", "north", "max_results"}),
        "budget_cap_eur": budget_cap,
        "budget_status": "tested_on_open" if budget_cap else "flexible",
        "note": f"{len(rows)} precomputed PLANA Index parcels were screened. Detailed market and acquisition evidence is refreshed when a parcel is opened.",
    }


@app.post("/api/find-opportunities")
async def find_opportunities(payload: OpportunityFinderRequest) -> dict[str, Any]:
    if payload.east <= payload.west or payload.north <= payload.south:
        raise HTTPException(status_code=400, detail="Invalid map area.")
    indexed_result = await _find_indexed_opportunities(payload)
    if indexed_result is not None:
        return indexed_result
    # Keep discovery intentionally local: users search the visible map area, not all Cyprus in one blocking request.
    if (payload.east - payload.west) > 0.18 or (payload.north - payload.south) > 0.14:
        raise HTTPException(status_code=400, detail="Zoom in to a town or neighbourhood before scanning opportunities.")
    cache_key = json.dumps(payload.model_dump(), sort_keys=True)
    cached = OPPORTUNITY_SCAN_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < OPPORTUNITY_SCAN_CACHE_TTL_SECONDS:
        return cached[1]
    features = await _query_parcels_in_bbox(payload)
    # Rank by approximate scale/shape, but sample across the whole visible area.
    # A single global sort caused the old finder to pick 2–3 local clusters.
    def geometry_rank(feature: dict[str, Any]) -> tuple[float, float]:
        metrics = polygon_geometry_metrics(feature)
        longest = _num(metrics.get("longest_edge_m")) or 0.0
        shortest = _num(metrics.get("shortest_edge_m")) or 0.0
        proxy = longest * shortest
        target = {"small": 700.0, "medium": 1600.0, "large": 3500.0}.get(payload.scale)
        scale_distance = abs(proxy - target) if target else 0.0
        shape_penalty = 1.0 - min(shortest / longest, 1.0) if longest > 0 else 1.0
        return (scale_distance, shape_penalty)

    grid_cols = grid_rows = 5
    spatial_buckets: dict[tuple[int, int], list[dict[str, Any]]] = {}
    width = max(payload.east - payload.west, 1e-9)
    height = max(payload.north - payload.south, 1e-9)
    for feature in features:
        metrics = polygon_geometry_metrics(feature)
        lon = _num(metrics.get("centroid_lon"))
        lat = _num(metrics.get("centroid_lat"))
        if lon is None or lat is None:
            continue
        col = min(grid_cols - 1, max(0, int((lon - payload.west) / width * grid_cols)))
        row = min(grid_rows - 1, max(0, int((lat - payload.south) / height * grid_rows)))
        spatial_buckets.setdefault((col, row), []).append(feature)

    for bucket in spatial_buckets.values():
        bucket.sort(key=geometry_rank)

    selected: list[dict[str, Any]] = []
    round_index = 0
    ordered_cells = sorted(spatial_buckets)
    while len(selected) < OPPORTUNITY_DETAIL_LIMIT and ordered_cells:
        progress = False
        for cell in ordered_cells:
            bucket = spatial_buckets[cell]
            if round_index < len(bucket):
                selected.append(bucket[round_index])
                progress = True
                if len(selected) >= OPPORTUNITY_DETAIL_LIMIT:
                    break
        if not progress:
            break
        round_index += 1
    semaphore = asyncio.Semaphore(min(14, max(1, len(selected))))
    tasks = [
        asyncio.create_task(
            asyncio.wait_for(
                _finder_candidate(feature, payload, semaphore),
                timeout=OPPORTUNITY_CANDIDATE_TIMEOUT_SECONDS,
            )
        )
        for feature in selected
    ]
    candidates: list[dict[str, Any]] = []
    timed_out = 0
    failed = 0
    deadline = time.monotonic() + OPPORTUNITY_SCAN_TIME_BUDGET_SECONDS
    try:
        for task in asyncio.as_completed(tasks, timeout=OPPORTUNITY_SCAN_TIME_BUDGET_SECONDS):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                row = await task
                if row:
                    candidates.append(row)
            except asyncio.TimeoutError:
                timed_out += 1
            except Exception:
                failed += 1
    except asyncio.TimeoutError:
        pass
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    if not candidates and selected:
        # Last-resort discovery cards from official DLS parcel geometry.
        # These are deliberately low-confidence and open the normal live parcel analysis.
        for feature in selected[:payload.max_results]:
            props = feature.get("properties") or {}
            metrics = polygon_geometry_metrics(feature)
            parcel_id = props.get("SBPI_ID_NO")
            if parcel_id is None or metrics.get("centroid_lat") is None or metrics.get("centroid_lon") is None:
                continue
            candidates.append({
                "parcel_id": parcel_id,
                "parcel_number": props.get("PARCEL_NBR"),
                "district": None,
                "municipality": None,
                "quarter": None,
                "area_m2": metrics.get("approx_area_m2"),
                "zone": None,
                "floor_capacity_m2": None,
                "units_low": None,
                "units_high": None,
                "development_gap_m2": None,
                "underused_percent": None,
                "score": 50,
                "score_label": "Screen",
                "best_use": "Open parcel for full analysis",
                "planning_status": "pending",
                "centroid_lat": metrics.get("centroid_lat"),
                "centroid_lon": metrics.get("centroid_lon"),
                "score_evidence_status": "discovery_only",
                "scored_components": [],
                "why": ["Official DLS parcel discovered", "Full PLANA analysis runs when opened"],
            })
    budget_cap = _finder_budget_cap(payload.budget)
    # Budget cannot be hard-filtered without market evidence. It is reported as a requested preference,
    # then tested precisely when the user opens a parcel and market evidence loads.
    result = {
        "scanned_parcels": len(selected),
        "completed_parcels": len(candidates),
        "timed_out_parcels": timed_out,
        "failed_parcels": failed,
        "partial_results": (timed_out + failed + max(len(selected) - len(candidates) - timed_out - failed, 0)) > 0,
        "candidate_parcels": len(candidates),
        "standout_count": sum(1 for x in candidates if x["score"] >= 82),
        "results": candidates[:payload.max_results],
        "weights": _finder_weights(payload.priorities),
        "brief": payload.model_dump(exclude={"west", "south", "east", "north", "max_results"}),
        "budget_cap_eur": budget_cap,
        "budget_status": "tested_on_open" if budget_cap else "flexible",
        "note": "Opportunity ranking is a preliminary DLS-based screening. Slow parcel checks are skipped so completed results return within the scan time budget. Market and acquisition budget are verified when a parcel is opened.",
    }
    OPPORTUNITY_SCAN_CACHE[cache_key] = (time.time(), result)
    return result


class ParcelDecisionRequest(BaseModel):
    parcel_id: int = Field(gt=0)
    asking_price_eur: float | None = Field(default=None, ge=0)
    target_profit_on_cost_percent: float = Field(default=20.0, ge=0, le=100)
    planning_analysis: dict[str, Any] | None = None
    opportunity_analysis: dict[str, Any] | None = None


@app.post("/api/parcel-decision")
async def parcel_decision(payload: ParcelDecisionRequest) -> dict[str, Any]:
    details = await get_canonical_parcel_details(payload.parcel_id)
    opportunity = payload.opportunity_analysis
    if opportunity is None:
        opportunity = analyse_parcel_opportunity(details, {})
    proposals = build_viable_development_options(details, payload.planning_analysis, opportunity)
    return build_decision_intelligence(
        details,
        proposals,
        opportunity,
        asking_price_eur=payload.asking_price_eur,
        target_profit_on_cost_percent=payload.target_profit_on_cost_percent,
    )


class ParcelProposalRequest(BaseModel):
    parcel_id: int = Field(gt=0)
    planning_analysis: dict[str, Any] | None = None
    opportunity_analysis: dict[str, Any] | None = None


@app.post("/api/parcel-proposals")
async def parcel_proposals(payload: ParcelProposalRequest) -> dict[str, Any]:
    details = await get_canonical_parcel_details(payload.parcel_id)
    return build_viable_development_options(details, payload.planning_analysis, payload.opportunity_analysis)


class ParcelPlanningRequest(BaseModel):
    parcel_id: int = Field(gt=0)


@app.post("/api/parcel-planning-analysis")
async def parcel_planning_analysis(payload: ParcelPlanningRequest) -> dict[str, Any]:
    parcel_details = await get_canonical_parcel_details(payload.parcel_id)
    cache_key = parcel_planning_cache_key(parcel_details)
    cached = PARCEL_PLANNING_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < PARCEL_PLANNING_CACHE_TTL_SECONDS:
        return {**cached[1], "cached": True}

    supabase = state.get("supabase")
    openai_client = state.get("openai")
    all_rows = state.get("all_rows")
    if not supabase or not openai_client or all_rows is None:
        raise HTTPException(
            status_code=503,
            detail="Planning intelligence is not ready yet.",
        )

    try:
        result = await asyncio.to_thread(
            generate_parcel_planning_analysis,
            parcel_details,
            openai_client,
            supabase,
            all_rows,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Automatic planning analysis failed: {exc}",
        ) from exc

    result = {
        **result,
        "parcel_id": payload.parcel_id,
        "generated_at": time.time(),
        "cached": False,
    }
    PARCEL_PLANNING_CACHE[cache_key] = (time.time(), result)
    return result


@app.get("/api/site")
async def site(
    lat: float = Query(ge=34.0, le=36.0),
    lon: float = Query(ge=31.0, le=35.0),
) -> dict[str, Any]:
    """Backward-compatible composed site payload built from the canonical parcel details."""
    parcel_feature = await get_parcel_at_point(lat, lon)
    map_props = parcel_feature.get("properties", {})
    sbpi = map_props.get("SBPI_ID_NO")
    if sbpi is None:
        raise HTTPException(status_code=502, detail="DLS parcel did not return SBPI_ID_NO.")
    try:
        parcel_id = int(sbpi)
    except Exception:
        raise HTTPException(status_code=502, detail=f"Unexpected SBPI_ID_NO: {sbpi}")

    details = await get_canonical_parcel_details(parcel_id)
    return {
        **details,
        "parcel_feature": parcel_feature,
        "parcel": {
            **details["parcel"],
            "map_geometry_extent_m2": map_props.get("Parcel Extend") or map_props.get("SHAPE.STArea()"),
        },
        "geometry_metrics": polygon_geometry_metrics(parcel_feature),
    }


class ParcelAIRequest(BaseModel):
    question: str
    parcel_context: dict[str, Any]
    scenario: dict[str, Any] | None = None


@app.post("/api/parcel-ai")
def parcel_ai(payload: ParcelAIRequest) -> dict[str, Any]:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    context = payload.parcel_context or {}
    scenario = payload.scenario or {}

    parcel_prompt = f"""
You are answering about a specific Cyprus parcel.

TRUSTED PARCEL CONTEXT FROM DLS / PLATFORM:
{json.dumps(context, ensure_ascii=False, indent=2)}

USER DEVELOPMENT SCENARIO:
{json.dumps(scenario, ensure_ascii=False, indent=2)}

USER QUESTION:
{question}

Instructions:
- Treat the parcel facts above as trusted structured context.
- Treat structured_rule_analysis as the current deterministic baseline for topics it covers.
- For calculation/setback topics covered by Order 4/2026, do not substitute a superseded Order 4/2024 rule.
- The March 2026 ETEK guide is interpretive context and does not override Order 4/2026.
- Do not invent missing parcel facts.
- Use the planning-regulation knowledge base for additional legal/planning rules, exceptions and parcel-specific policies.
- Distinguish official DLS facts, platform calculations, structured rule calculations, user assumptions, and planning interpretation.
- Where the answer depends on missing facts, say exactly what is missing.
""".strip()

    supabase = state.get("supabase")
    openai_client = state.get("openai")
    all_rows = state.get("all_rows")
    if not supabase or not openai_client or all_rows is None:
        raise HTTPException(status_code=503, detail="Planning AI is not ready yet.")

    try:
        # Parcel chat avoids the slower generic bilingual query-expansion and LLM
        # reranking stages. Structured parcel context already narrows the problem.
        semantic_rows = semantic_candidates(question, openai_client, supabase)
        lexical_rows = lexical_candidates(question, all_rows)
        direct_rows = direct_rule_candidates(question, all_rows)
        hybrid_rows = merge_and_rerank(semantic_rows, lexical_rows, direct_rows)
        context_rows = expand_with_adjacent_pages_local(hybrid_rows[:8], all_rows)
        answer = answer_question(parcel_prompt, context_rows, openai_client)
        return {
            "answer": answer,
            "sources": unique_sources(hybrid_rows[:8]),
            "language": output_language_for_question(question),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Parcel AI failed: {exc}") from exc


@app.post("/api/site-extra")
async def site_extra(payload: dict[str, Any]) -> dict[str, Any]:
    parcel_feature = payload.get("parcel_feature") or {}
    if not parcel_feature:
        raise HTTPException(status_code=400, detail="Missing parcel geometry.")
    geometry = parcel_feature.get("geometry") or {}
    cache_key = json.dumps(geometry, sort_keys=True, separators=(",", ":"))
    cached = SITE_EXTRA_CACHE.get(cache_key)
    now_ts = time.time()
    if cached and now_ts - cached[0] < SITE_EXTRA_CACHE_TTL_SECONDS:
        return {**cached[1], "cached": True}

    spatial_checks: dict[str, Any] = {}
    stored_complete = False
    supabase = state.get("supabase")
    if supabase:
        try:
            response = await asyncio.to_thread(lambda: supabase.rpc("get_plana_dls_support_for_geometry", {"p_geometry": geometry, "p_layer_ids": list(SPECIAL_LAYERS)}).execute())
            stored = response.data or {}
            if isinstance(stored, dict) and stored.get("complete"):
                stored_complete = True
                for layer_id, layer_name in SPECIAL_LAYERS.items():
                    layer = (stored.get("layers") or {}).get(str(layer_id)) or {}
                    features = [{"attributes": item.get("properties") or {}, "source_object_id": item.get("source_object_id")} for item in layer.get("features") or []]
                    spatial_checks[str(layer_id)] = {"layer_name": layer_name, "ok": True, "features": features, "source": "plana_dls_completed_cycle", "completed_cycle_key": layer.get("completed_cycle_key")}
        except Exception as exc:
            print(f"PLANA stored DLS support fallback: {exc!r}", file=sys.stderr)

    if not stored_complete:
        layer_items = list(SPECIAL_LAYERS.items())
        layer_results = await asyncio.gather(*(query_layer_intersections(layer_id, parcel_feature) for layer_id, _ in layer_items), return_exceptions=True)
        for (layer_id, layer_name), result in zip(layer_items, layer_results):
            if isinstance(result, Exception):
                result = {"ok": False, "error": str(result)}
            spatial_checks[str(layer_id)] = {"layer_name": layer_name, **result, "source": "live_dls_fallback"}

    buildings = []
    bcheck = spatial_checks.get("28", {})
    if bcheck.get("ok"):
        for feature in bcheck.get("features", []):
            attributes = feature.get("attributes", {})
            buildings.append({"object_id": attributes.get("Object ID") or attributes.get("OBJECTID") or feature.get("source_object_id"), "building_code": attributes.get("BLDG_CODE"), "building_description": clean_text(attributes.get("BLDG_DESC"))})

    flags = []
    if buildings:
        flags.append(f"{len(buildings)} mapped building feature(s)")
    for lid, label in (("31", "Coast protection overlap"), ("32", "State land overlap"), ("37", "White-zone overlap")):
        check = spatial_checks.get(lid, {})
        if check.get("ok") and check.get("features"):
            flags.append(label)

    result = {"geometry_metrics": polygon_geometry_metrics(parcel_feature), "spatial_checks": {"coast_protection": bool(spatial_checks.get("31", {}).get("features")), "state_land": bool(spatial_checks.get("32", {}).get("features")), "white_zone": bool(spatial_checks.get("37", {}).get("features")), "all": spatial_checks}, "building_summary": {"count": len(buildings), "features": buildings}, "contour_summary": {"count": 0, "min_elevation_m": None, "max_elevation_m": None, "elevation_range_m": None, "values_m": []}, "flags": flags, "enrichment_source": "plana_dls_completed_cycle" if stored_complete else "live_dls_fallback", "cached": False}
    SITE_EXTRA_CACHE[cache_key] = (time.time(), result)
    return result



SITE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>PLANA.CY</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
:root{--ink:#142019;--muted:#6f7c74;--line:#e3e9e5;--bg:#f5f7f5;--card:#fff;--green:#173f2b;--green2:#2f6d4c;--soft:#edf4ef;--amber:#8b5e18;--amberbg:#fff7e7;--red:#9a3733;--shadow:0 12px 34px rgba(18,38,25,.10)}
*{box-sizing:border-box}html,body{height:100%;margin:0}body{font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink);background:var(--bg)}button,input{font:inherit}.app{height:100%;display:grid;grid-template-columns:minmax(0,1.55fr) minmax(390px,.65fr)}
.map-shell{position:relative;min-width:0;background:#dfe5e1}#map{height:100%;width:100%}.topbar{position:absolute;z-index:1000;left:16px;top:16px;display:flex;align-items:center;gap:9px;width:min(720px,calc(100% - 32px))}.brand{height:48px;display:flex;align-items:center;padding:0 15px;border:1px solid var(--line);border-radius:14px;background:rgba(255,255,255,.97);box-shadow:var(--shadow);font-weight:900;letter-spacing:-.03em;white-space:nowrap}.search-wrap{position:relative;flex:1;min-width:0}.search-box{height:48px;display:flex;align-items:center;background:rgba(255,255,255,.98);border:1px solid var(--line);border-radius:14px;padding:5px;box-shadow:var(--shadow)}.search-box input{flex:1;min-width:0;border:0;outline:0;padding:10px 11px;background:transparent}.find-btn{height:38px;border:0;border-radius:10px;background:var(--green);color:#fff;padding:0 15px;font-weight:850;cursor:pointer;white-space:nowrap}.suggestions{position:absolute;left:0;right:0;top:54px;background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);overflow:hidden;max-height:380px;overflow-y:auto}.suggestion{width:100%;border:0;border-bottom:1px solid var(--line);background:#fff;padding:11px 13px;text-align:left;cursor:pointer}.suggestion:last-child{border-bottom:0}.suggestion:hover{background:var(--soft)}.suggestion b{display:block;font-size:13px}.suggestion span{display:block;margin-top:3px;color:var(--muted);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.map-note{position:absolute;z-index:800;left:16px;bottom:16px;background:rgba(255,255,255,.94);border:1px solid var(--line);border-radius:11px;padding:9px 12px;font-size:11px;color:var(--muted);box-shadow:0 5px 18px rgba(20,35,26,.08)}
.panel{height:100%;overflow-y:auto;border-left:1px solid var(--line);background:#fafbfa}.empty{height:100%;display:grid;place-items:center;text-align:center;padding:48px;color:var(--muted)}.empty-icon{width:58px;height:58px;border-radius:18px;display:grid;place-items:center;background:var(--soft);color:var(--green);font-size:25px;margin:0 auto 16px}.empty h1{font-size:25px;margin:0 0 8px;color:var(--ink);letter-spacing:-.04em}.empty p{margin:0;max-width:280px;line-height:1.5}.content{padding:18px}.parcel-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:13px}.eyebrow{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);font-weight:850}.parcel-title{font-size:25px;font-weight:900;letter-spacing:-.04em;margin-top:3px}.parcel-place{font-size:12px;color:var(--muted);margin-top:4px}.status-line{font-size:10px;color:var(--muted);display:flex;align-items:center;gap:6px;margin-top:7px}.pulse{width:7px;height:7px;border-radius:50%;background:#5c9d72;animation:pulse 1.4s infinite}@keyframes pulse{70%{box-shadow:0 0 0 7px rgba(92,157,114,0)}}
.tabs{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;background:#eef1ef;border-radius:12px;padding:4px;margin-bottom:12px;position:sticky;top:0;z-index:20}.tab{border:0;background:transparent;border-radius:9px;padding:9px 4px;font-size:10px;font-weight:850;color:var(--muted);cursor:pointer}.tab.active{background:#fff;color:var(--ink);box-shadow:0 2px 8px rgba(20,35,26,.07)}.tab-page{display:none}.tab-page.active{display:block}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px;margin-bottom:10px}.card h2{font-size:14px;margin:0 0 11px;letter-spacing:-.02em}.hero{background:var(--green);color:#fff;border:0}.hero .muted{color:rgba(255,255,255,.68)}.metric-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}.metric{background:var(--bg);border-radius:11px;padding:11px}.hero .metric{background:rgba(255,255,255,.09)}.metric .k{font-size:10px;color:var(--muted)}.hero .metric .k{color:rgba(255,255,255,.68)}.metric .v{font-size:19px;font-weight:900;letter-spacing:-.03em;margin-top:3px}.metric .u{font-size:10px;color:var(--muted);margin-top:2px}.hero .metric .u{color:rgba(255,255,255,.62)}.wide-metric{display:flex;justify-content:space-between;gap:12px;align-items:center;border-top:1px solid var(--line);padding:9px 0;font-size:12px}.wide-metric:first-child{border-top:0;padding-top:0}.wide-metric:last-child{padding-bottom:0}.wide-metric b{font-size:13px;text-align:right}.section-title{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:10px}.section-title h2{margin:0}.link-btn{border:0;background:transparent;color:var(--green2);font-size:10px;font-weight:850;cursor:pointer}.chips{display:flex;gap:6px;flex-wrap:wrap}.chip{font-size:10px;font-weight:800;border-radius:999px;padding:7px 9px;background:var(--soft);color:var(--green)}.chip.maybe{background:var(--amberbg);color:var(--amber)}.summary-three{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.summary-box{border:1px solid var(--line);border-radius:11px;padding:9px}.summary-box span{display:block;color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.06em}.summary-box b{display:block;font-size:12px;margin-top:5px;line-height:1.25}.amenity-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}.amenity{border:1px solid var(--line);border-radius:11px;padding:10px;background:#fff;cursor:pointer;text-align:left}.amenity:hover{border-color:#a8b8ac}.amenity b{display:block;font-size:12px}.amenity strong{display:block;font-size:17px;margin-top:5px}.amenity span{display:block;font-size:10px;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.view-list{display:grid;gap:7px}.view-row{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding-bottom:7px;font-size:11px}.view-row:last-child{border-bottom:0;padding-bottom:0}.view-row span{color:var(--muted)}.view-row b{text-align:right}.option-card{border:1px solid var(--line);border-radius:14px;background:#fff;margin-bottom:8px;overflow:hidden}.option-card.best{border-color:#6d9b7b;box-shadow:inset 0 0 0 1px #6d9b7b}.option-top{padding:12px;cursor:pointer}.option-title{display:flex;justify-content:space-between;gap:10px}.option-title h3{font-size:14px;margin:0}.option-title .badges{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end}.badge{font-size:8px;font-weight:900;text-transform:uppercase;letter-spacing:.05em;border-radius:999px;padding:5px 7px;background:var(--soft);color:var(--green)}.option-headline{font-size:11px;color:var(--muted);margin-top:4px}.option-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:10px}.option-mini{background:var(--bg);border-radius:9px;padding:8px}.option-mini span{display:block;font-size:8px;color:var(--muted)}.option-mini b{display:block;font-size:11px;margin-top:3px}.option-details{display:none;border-top:1px solid var(--line);padding:12px}.option-card.open .option-details{display:block}.finance-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px}.finance-item{background:var(--bg);border-radius:10px;padding:9px}.finance-item span{display:block;font-size:9px;color:var(--muted)}.finance-item b{display:block;font-size:13px;margin-top:4px}.empty-result{font-size:12px;color:var(--muted);line-height:1.5;padding:5px 0}.price-input{display:flex;gap:7px;margin-top:10px}.price-input input{flex:1;min-width:0;border:1px solid var(--line);border-radius:10px;padding:10px;outline:0}.price-input button{border:0;border-radius:10px;background:var(--green);color:#fff;padding:0 12px;font-weight:850}.verdict{margin-top:8px;border-radius:10px;padding:10px;font-size:11px;font-weight:800;background:var(--soft);color:var(--green)}.verdict.high{background:#fff0ee;color:var(--red)}.comparables{display:grid;gap:6px}.comparable{border-top:1px solid var(--line);padding-top:8px}.comparable:first-child{border-top:0;padding-top:0}.comparable-top{display:flex;justify-content:space-between;gap:10px;font-size:11px}.comparable-top b:last-child{white-space:nowrap}.comparable-sub{font-size:9px;color:var(--muted);margin-top:3px}.details summary{cursor:pointer;font-size:11px;font-weight:800;color:var(--green2)}.details-body{font-size:10px;color:var(--muted);line-height:1.55;padding-top:8px}.loading{height:12px;border-radius:6px;background:linear-gradient(90deg,#eef1ee 25%,#fafbfa 50%,#eef1ee 75%);background-size:200% 100%;animation:shimmer 1.2s infinite;margin:7px 0}@keyframes shimmer{to{background-position:-200% 0}}.hidden{display:none!important}
.ai-launch{position:sticky;bottom:10px;display:flex;justify-content:flex-end;pointer-events:none;margin-top:12px}.ai-launch button{pointer-events:auto;border:0;border-radius:999px;background:var(--green);color:#fff;padding:11px 15px;font-weight:850;box-shadow:var(--shadow);cursor:pointer}.ai-box{position:sticky;bottom:10px;background:#fff;border:1px solid var(--line);border-radius:14px;padding:9px;box-shadow:var(--shadow);margin-top:10px}.ai-row{display:flex;gap:7px}.ai-row input{flex:1;min-width:0;border:0;outline:0;padding:8px}.ai-row button{border:0;background:var(--green);color:#fff;border-radius:9px;padding:8px 11px;font-weight:850}.ai-answer{font-size:11px;line-height:1.5;padding:9px 5px 3px;white-space:pre-wrap}.close-ai{border:0;background:transparent;float:right;cursor:pointer;color:var(--muted)}
@media(max-width:940px){.app{grid-template-columns:1fr;grid-template-rows:46vh auto;height:auto;min-height:100%}.map-shell{height:46vh}.panel{height:auto;overflow:visible;border-left:0;border-top:1px solid var(--line)}.content{padding:13px}.topbar{left:10px;top:10px;width:calc(100% - 20px)}.brand{display:none}.map-note{left:10px;bottom:10px}.tabs{top:0}.summary-three{grid-template-columns:1fr}.option-metrics{grid-template-columns:repeat(3,1fr)}}
@media(max-width:520px){.find-btn{padding:0 11px}.metric-grid,.amenity-grid,.finance-grid{grid-template-columns:1fr 1fr}.tabs{overflow-x:auto;display:flex}.tab{min-width:70px}.option-metrics{grid-template-columns:1fr 1fr}.map-note{display:none}}
</style>
</head>
<body>
<div class="app">
  <div class="map-shell">
    <div id="map"></div>
    <div class="topbar">
      <div class="brand">PLANA.CY</div>
      <div class="search-wrap">
        <div class="search-box">
          <input id="searchInput" autocomplete="off" placeholder="Search address, area or landmark">
          <button id="findSiteBtn" class="find-btn">Find Site</button>
        </div>
        <div id="suggestions" class="suggestions hidden"></div>
      </div>
    </div>
    <div class="map-note">Search or click a plot on the map</div>
  </div>

  <aside class="panel">
    <div id="empty" class="empty">
      <div><div class="empty-icon">⌂</div><h1>Find an investment site</h1><p>Select a plot to see what can be built and compare investment options.</p></div>
    </div>

    <div id="content" class="content hidden">
      <div class="parcel-head">
        <div><div class="eyebrow">Selected plot</div><div id="parcelTitle" class="parcel-title">—</div><div id="parcelPlace" class="parcel-place">—</div><div id="loadStatus" class="status-line hidden"><i class="pulse"></i><span>Calculating options…</span></div></div>
      </div>

      <nav class="tabs">
        <button class="tab active" data-tab="overview">Overview</button>
        <button class="tab" data-tab="build">Build</button>
        <button class="tab" data-tab="options">Options</button>
        <button class="tab" data-tab="investment">Investment</button>
        <button class="tab" data-tab="nearby">Nearby</button>
      </nav>

      <section id="overview" class="tab-page active"></section>
      <section id="build" class="tab-page"></section>
      <section id="options" class="tab-page"></section>
      <section id="investment" class="tab-page"></section>
      <section id="nearby" class="tab-page"></section>

      <div id="aiLaunch" class="ai-launch"><button>Ask about this plot</button></div>
      <div id="aiBox" class="ai-box hidden">
        <button id="closeAi" class="close-ai">×</button>
        <div class="eyebrow">Ask PLANA</div>
        <div class="ai-row"><input id="aiQuestion" placeholder="Could I build eight apartments?"><button id="askAiBtn">Ask</button></div>
        <div id="aiAnswer" class="ai-answer hidden"></div>
      </div>
    </div>
  </aside>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const $=id=>document.getElementById(id);
const esc=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const num=value=>{const n=Number(value);return Number.isFinite(n)?n:null};
const fmt=(value,digits=0)=>{const n=num(value);return n==null?"—":n.toLocaleString(undefined,{maximumFractionDigits:digits,minimumFractionDigits:digits})};
const money=value=>{const n=num(value);return n==null?"—":"€"+Math.round(n).toLocaleString()};
const rangeMoney=value=>value&&num(value.low)!=null&&num(value.high)!=null?(num(value.low)===num(value.high)?money(value.low):`${money(value.low)}–${money(value.high)}`):"—";
const rangePct=value=>value&&num(value.low)!=null&&num(value.high)!=null?(num(value.low)===num(value.high)?`${fmt(value.low,1)}%`:`${fmt(value.low,1)}–${fmt(value.high,1)}%`):"—";
const midpoint=value=>value&&num(value.low)!=null&&num(value.high)!=null?(num(value.low)+num(value.high))/2:null;

async function request(url,options={},timeout=30000){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),timeout);try{const response=await fetch(url,{...options,signal:controller.signal});const raw=await response.text();let data={};if(raw){try{data=JSON.parse(raw)}catch{data={detail:`Invalid server response (${response.status})`}}}if(!response.ok)throw new Error(data.detail||`Request failed (${response.status})`);return data}finally{clearTimeout(timer)}}
const get=(url,timeout)=>request(url,{},timeout);
const post=(url,body,timeout)=>request(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)},timeout);

const map=L.map("map",{zoomControl:true,preferCanvas:true}).setView([35.1264,33.4299],9);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:20,attribution:"© OpenStreetMap"}).addTo(map);
let parcelLayer=null,amenityLayer=L.layerGroup().addTo(map),current=null,planning=null,opportunity=null,proposals=null,locationInfo=null,siteExtra=null,selectionId=0,searchTimer=null,searchResults=[],searchSession=crypto.randomUUID?crypto.randomUUID():String(Date.now());

function setLoading(active,text="Calculating options…"){$("loadStatus").classList.toggle("hidden",!active);$("loadStatus").querySelector("span").textContent=text}
function locationLabel(parcel){return [parcel.quarter,parcel.municipality,parcel.district].filter(Boolean).join(" · ")||"Cyprus"}
function zones(){return current?.planning_zones||[]}
function zoneText(){return zones().map(zone=>zone.zone).filter(Boolean).join(" / ")||"—"}
function maxZone(key){const values=zones().map(zone=>num(zone[key])).filter(value=>value!=null);return values.length?Math.max(...values):null}
function firstZone(key){const values=zones().map(zone=>num(zone[key])).filter(value=>value!=null);return values.length?values[0]:null}
function metric(label,value,unit=""){return `<div class="metric"><div class="k">${esc(label)}</div><div class="v">${esc(value)}</div>${unit?`<div class="u">${esc(unit)}</div>`:""}</div>`}
function wide(label,value){return `<div class="wide-metric"><span>${esc(label)}</span><b>${esc(value)}</b></div>`}
function loadingCard(title){return `<div class="card"><h2>${esc(title)}</h2><div class="loading"></div><div class="loading" style="width:70%"></div></div>`}
function optionById(id){return (proposals?.development_options||[]).find(option=>option.id===id)}
function bestName(id){return optionById(id)?.label||"—"}

function renderOverview(){if(!current)return;const parcel=current.parcel||{},potential=current.development_potential||{},summary=proposals?.investment_summary||{},amenities=locationInfo?.amenities||[],views=locationInfo?.view_potential||[];const topAmenities=amenities.slice(0,4);$("overview").innerHTML=`
<div class="card hero"><div class="metric-grid">${metric("Area",fmt(parcel.parcel_extent_m2,0),"m²")}${metric("Zone",zoneText())}${metric("Density",firstZone("density_percent")!=null?fmt(firstZone("density_percent"),0)+"%":"—")}${metric("Coverage",firstZone("coverage_percent")!=null?fmt(firstZone("coverage_percent"),0)+"%":"—")}</div></div>
<div class="card"><h2>What can be built</h2><div class="metric-grid">${metric("Buildable area",fmt(potential.theoretical_max_floor_area_m2,0),"m²")}${metric("Ground coverage",fmt(potential.theoretical_max_ground_coverage_m2,0),"m²")}${metric("Floors",fmt(maxZone("max_floors"),0))}${metric("Height",fmt(maxZone("max_height_m"),1),"m")}</div></div>
${proposals?`<div class="card"><h2>Best investment options</h2><div class="summary-three"><div class="summary-box"><span>Highest resale profit</span><b>${esc(bestName(summary.best_resale_option_id))}</b></div><div class="summary-box"><span>Highest rental return</span><b>${esc(bestName(summary.best_rental_option_id))}</b></div><div class="summary-box"><span>Lowest investment</span><b>${esc(bestName(summary.lowest_investment_option_id))}</b></div></div></div>`:loadingCard("Best investment options")}
${topAmenities.length?`<div class="card"><div class="section-title"><h2>Nearby</h2><button class="link-btn" data-go="nearby">See all</button></div><div class="amenity-grid">${topAmenities.map(amenity=>amenityButton(amenity)).join("")}</div></div>`:loadingCard("Nearby")}
${views.length?`<div class="card"><h2>View potential</h2><div class="view-list">${views.map(view=>`<div class="view-row"><span>${esc(view.label)}</span><b>${esc(view.value)}${view.detail?`<br><small>${esc(view.detail)}</small>`:""}</b></div>`).join("")}</div></div>`:loadingCard("View potential")}`;bindDynamic()}

function allowedUseChips(){const uses=proposals?.allowed_uses||[];if(!uses.length)return `<div class="empty-result">Use options are loading.</div>`;return `<div class="chips">${uses.map(use=>`<span class="chip ${use.status==='allowed'?'':'maybe'}">${esc(use.label)} · ${use.status==='allowed'?'Allowed':'Potentially allowed'}</span>`).join("")}</div>`}
function renderBuild(){if(!current)return;const space=proposals?.space_strategy||{},potential=current.development_potential||{};$("build").innerHTML=`
<div class="card"><h2>Maximum capacity</h2>${wide("Buildable internal area",fmt(space.density_counted_floor_area_m2??potential.theoretical_max_floor_area_m2,0)+" m²")}${wide("Maximum footprint",fmt(space.ground_coverage_m2??potential.theoretical_max_ground_coverage_m2,0)+" m²")}${wide("Covered balconies & verandas",fmt(space.indicative_covered_balcony_veranda_allowance_m2,0)+" m²")}${wide("Total constructed area",space.indicative_total_constructed_area_low_m2?`${fmt(space.indicative_total_constructed_area_low_m2,0)}–${fmt(space.indicative_total_constructed_area_high_m2,0)} m²`:"—")}${wide("Floors",fmt(space.planning_max_floors??maxZone("max_floors"),0))}${wide("Height",fmt(space.planning_max_height_m??maxZone("max_height_m"),1)+" m")}</div>
<div class="card"><h2>Potential uses</h2>${allowedUseChips()}</div>
<div class="card"><h2>Parking fit</h2><div class="empty-result">Parking is included inside each development option and adjusts with the unit count.</div></div>
<details class="card details"><summary>How calculated</summary><div class="details-body">PLANA combines the selected plot area, planning-zone coefficients, floor and height limits, unit-size assumptions, market listings and construction-cost settings. Final design fit and authority approval still depend on the exact proposal.</div></details>`}

function badgesFor(option){const summary=proposals?.investment_summary||{},badges=[];if(summary.best_resale_option_id===option.id)badges.push("Best resale");if(summary.best_rental_option_id===option.id)badges.push("Best rent");if(summary.lowest_investment_option_id===option.id)badges.push("Lowest cost");return badges}
function optionCard(option){const e=option.economics||{},badges=badgesFor(option),topMetrics=(option.metrics||[]).slice(0,3);return `<article class="option-card ${badges.length?'best':''}" data-option="${esc(option.id)}"><div class="option-top"><div class="option-title"><h3>${esc(option.label)}</h3><div class="badges">${badges.map(b=>`<span class="badge">${esc(b)}</span>`).join("")}</div></div><div class="option-headline">${esc(option.headline)}</div><div class="option-metrics">${topMetrics.map(item=>`<div class="option-mini"><span>${esc(item.label)}</span><b>${esc(item.value??"—")} ${esc(item.unit||"")}</b></div>`).join("")}</div></div><div class="option-details">${e.status==='calculated'?`<div class="finance-grid"><div class="finance-item"><span>Build cost</span><b>${rangeMoney(e.build_cost_range_eur)}</b></div><div class="finance-item"><span>Total investment</span><b>${rangeMoney(e.total_investment_range_eur)}</b></div><div class="finance-item"><span>Completed sale value</span><b>${rangeMoney(e.gdv_range_eur)}</b></div><div class="finance-item"><span>Profit</span><b>${rangeMoney(e.estimated_profit_range_eur)}</b></div><div class="finance-item"><span>Sale ROI</span><b>${rangePct(e.roi_range_percent)}</b></div><div class="finance-item"><span>Rental yield</span><b>${rangePct(e.gross_rental_yield_range_percent)}</b></div><div class="finance-item"><span>Monthly rent</span><b>${rangeMoney(e.whole_scheme_monthly_rent_range_eur)}</b></div><div class="finance-item"><span>Sale per unit</span><b>${rangeMoney(e.sale_per_unit_range_eur)}</b></div></div>`:`<div class="empty-result">Market data for this use is not available yet.</div>`}</div></article>`}
function renderOptions(){const list=proposals?.development_options||[];$("options").innerHTML=list.length?`<div class="card"><h2>Development options</h2><div class="empty-result" style="padding-top:0">Select an option to see its investment figures.</div></div>${list.map(optionCard).join("")}`:loadingCard("Development options");document.querySelectorAll(".option-card").forEach(card=>card.querySelector(".option-top").onclick=()=>card.classList.toggle("open"))}

function renderInvestment(){if(!current)return;const estimate=proposals?.value_estimate,area=num(current.parcel?.parcel_extent_m2),buildable=num(proposals?.space_strategy?.density_counted_floor_area_m2??current.development_potential?.theoretical_max_floor_area_m2);const rows=(proposals?.development_options||[]).filter(option=>option.economics?.status==='calculated');$("investment").innerHTML=`
<div class="card"><h2>Estimated plot price</h2><div class="big" style="font-size:28px;font-weight:900;letter-spacing:-.04em">${estimate?`${money(estimate.low_eur)}–${money(estimate.high_eur)}`:"—"}</div><div class="metric-grid" style="margin-top:10px">${metric("Per plot m²",estimate&&area?money(((estimate.low_eur+estimate.high_eur)/2)/area):"—")}${metric("Per buildable m²",estimate&&buildable?money(((estimate.low_eur+estimate.high_eur)/2)/buildable):"—")}</div><div class="price-input"><input id="askingPrice" inputmode="numeric" placeholder="Owner asking price"><button id="testPrice">Compare</button></div><div id="priceVerdict"></div></div>
<div class="card"><h2>Investment comparison</h2>${rows.length?rows.map(option=>{const e=option.economics;return `<div class="wide-metric"><span>${esc(option.label)}</span><b>${rangeMoney(e.total_investment_range_eur)}<br><small>${rangePct(e.roi_range_percent)} ROI</small></b></div>`}).join(""):`<div class="empty-result">Investment figures will appear when market evidence is available.</div>`}</div>
<div class="card"><h2>Rental comparison</h2>${rows.length?rows.map(option=>`<div class="wide-metric"><span>${esc(option.label)}</span><b>${rangeMoney(option.economics.whole_scheme_monthly_rent_range_eur)} / month<br><small>${rangePct(option.economics.gross_rental_yield_range_percent)} yield</small></b></div>`).join(""):`<div class="empty-result">Rental figures are not available yet.</div>`}</div>`;const button=$("testPrice");if(button)button.onclick=comparePrice}
function comparePrice(){const value=num($("askingPrice")?.value?.replace(/[^0-9.]/g,"")),estimate=proposals?.value_estimate,box=$("priceVerdict");if(!box||value==null||!estimate){if(box)box.innerHTML="";return}let text,cls="";if(value<estimate.low_eur)text=`${money(estimate.low_eur-value)} below the estimated range`;else if(value>estimate.high_eur){text=`${money(value-estimate.high_eur)} above the estimated range`;cls="high"}else text="Within the estimated range";box.innerHTML=`<div class="verdict ${cls}">${esc(text)}</div>`}

function amenityButton(amenity){return `<button class="amenity" data-amenity="${esc(amenity.category)}"><b>${esc(amenity.label)}</b><strong>${esc(amenity.count)}</strong><span>${fmt(amenity.nearest_distance_m,0)} m · ${esc(amenity.nearest_name)}</span></button>`}
function renderNearby(){const amenities=locationInfo?.amenities||[],market=opportunity?.market_analysis||{},property=market.property_analyses||{},comparables=(market.top_comparables||property.apartment?.top_comparables||[]).slice(0,6);const marketCards=[["Apartments",property.apartment],["Houses",property.house],["Offices",property.office],["Shops",property.shop],["Land",property.land]].filter(([,value])=>value);$("nearby").innerHTML=`
<div class="card"><h2>Amenities</h2>${amenities.length?`<div class="amenity-grid">${amenities.map(amenityButton).join("")}</div>`:`<div class="empty-result">Nearby places are loading.</div>`}</div>
<div class="card"><h2>Average market prices</h2>${marketCards.length?marketCards.map(([label,value])=>`<div class="wide-metric"><span>${label}</span><b>${value.sale_price_per_m2?`${money(value.sale_price_per_m2.low)}–${money(value.sale_price_per_m2.high)} / m²`:"—"}${value.rent_per_m2_month?`<br><small>€${fmt(value.rent_per_m2_month.low,1)}–€${fmt(value.rent_per_m2_month.high,1)} rent / m²</small>`:""}</b></div>`).join(""):`<div class="empty-result">Market prices are loading.</div>`}</div>
<div class="card"><h2>Nearby listings</h2>${comparables.length?`<div class="comparables">${comparables.map(item=>`<div class="comparable"><div class="comparable-top"><b>${esc(item.title||item.property_type||"Property")}</b><b>${money(item.asking_price_eur||item.asking_rent_monthly_eur)}</b></div><div class="comparable-sub">${[item.covered_area_m2?fmt(item.covered_area_m2,0)+" m²":null,item.bedrooms?item.bedrooms+" bed":null,item.distance_km!=null?fmt(item.distance_km,1)+" km":null,item.source].filter(Boolean).map(esc).join(" · ")}</div></div>`).join("")}</div>`:`<div class="empty-result">No nearby listings to display yet.</div>`}</div>`;bindDynamic()}

function bindDynamic(){document.querySelectorAll("[data-go]").forEach(button=>button.onclick=()=>activateTab(button.dataset.go));document.querySelectorAll("[data-amenity]").forEach(button=>button.onclick=()=>showAmenity(button.dataset.amenity))}
function renderAll(){renderOverview();renderBuild();renderOptions();renderInvestment();renderNearby()}
function activateTab(name){document.querySelectorAll(".tab").forEach(tab=>tab.classList.toggle("active",tab.dataset.tab===name));document.querySelectorAll(".tab-page").forEach(page=>page.classList.toggle("active",page.id===name))}
document.querySelectorAll(".tab").forEach(tab=>tab.onclick=()=>activateTab(tab.dataset.tab));

function showAmenity(category){amenityLayer.clearLayers();const group=(locationInfo?.amenities||[]).find(item=>item.category===category);if(!group)return;(group.places||[]).slice(0,20).forEach(place=>L.marker([place.lat,place.lon]).bindPopup(`<b>${esc(place.name)}</b><br>${fmt(place.distance_m,0)} m`).addTo(amenityLayer));if(group.places?.length){const bounds=L.latLngBounds(group.places.slice(0,20).map(place=>[place.lat,place.lon]));if(bounds.isValid())map.fitBounds(bounds.pad(.18),{maxZoom:16})}}

async function selectParcel(lat,lon){const id=++selectionId;amenityLayer.clearLayers();setLoading(true,"Opening plot…");try{const basic=await get(`/api/parcel-basic?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`,16000);if(id!==selectionId)return;if(parcelLayer)map.removeLayer(parcelLayer);parcelLayer=L.geoJSON(basic.parcel_feature,{style:{color:"#173f2b",weight:3,fillColor:"#6fa482",fillOpacity:.22}}).addTo(map);try{map.fitBounds(parcelLayer.getBounds().pad(.22),{maxZoom:19})}catch{}const details=await get(`/api/parcel-details?parcel_id=${basic.parcel_id}`,18000);if(id!==selectionId)return;current={...details,parcel_feature:basic.parcel_feature,geometry_metrics:basic.geometry_metrics||details.geometry_metrics||{}};planning=null;opportunity=null;proposals=details.initial_proposals||null;locationInfo=null;siteExtra=null;$("empty").classList.add("hidden");$("content").classList.remove("hidden");$("parcelTitle").textContent=`Plot ${current.parcel?.parcel_number||current.parcel?.parcel_id||"—"}`;$("parcelPlace").textContent=locationLabel(current.parcel||{});renderAll();setLoading(true,"Calculating investment options…");const centroidLat=num(current.geometry_metrics?.centroid_lat)??lat,centroidLon=num(current.geometry_metrics?.centroid_lon)??lon;const oppPromise=post("/api/parcel-opportunity-analysis",{parcel_id:current.parcel.parcel_id,assumptions:{},centroid_lat:centroidLat,centroid_lon:centroidLon},40000).then(data=>{if(id!==selectionId)return;opportunity=data;proposals=data.proposals||proposals;renderAll()}).catch(error=>console.warn(error));const planningPromise=post("/api/parcel-planning-analysis",{parcel_id:current.parcel.parcel_id},50000).then(data=>{if(id!==selectionId)return;planning=data;renderAll()}).catch(error=>console.warn(error));const locationPromise=get(`/api/location-intelligence?lat=${centroidLat}&lon=${centroidLon}`,25000).then(data=>{if(id!==selectionId)return;locationInfo=data;renderAll()}).catch(error=>console.warn(error));const extraPromise=post("/api/site-extra",{parcel_feature:basic.parcel_feature},18000).then(data=>{if(id!==selectionId)return;siteExtra=data}).catch(error=>console.warn(error));await Promise.allSettled([oppPromise,planningPromise,locationPromise,extraPromise]);if(id!==selectionId)return;if(opportunity||planning){try{proposals=await post("/api/parcel-proposals",{parcel_id:current.parcel.parcel_id,planning_analysis:planning,opportunity_analysis:opportunity},25000);if(id!==selectionId)return;renderAll()}catch(error){console.warn(error)}}setLoading(false)}catch(error){if(id!==selectionId)return;setLoading(false);alert(error.message||String(error))}}
map.on("click",event=>selectParcel(event.latlng.lat,event.latlng.lng));

async function loadSuggestions(){const query=$("searchInput").value.trim();if(query.length<2){$("suggestions").classList.add("hidden");return}try{const data=await get(`/api/geocode?q=${encodeURIComponent(query)}&session_token=${encodeURIComponent(searchSession)}`,12000);searchResults=data.results||[];$("suggestions").innerHTML=searchResults.length?searchResults.map((result,index)=>`<button class="suggestion" data-index="${index}"><b>${esc(result.main_text||result.display_name)}</b><span>${esc(result.secondary_text||result.display_name)}</span></button>`).join(""):`<div class="suggestion"><b>No locations found</b></div>`;$("suggestions").classList.remove("hidden");document.querySelectorAll(".suggestion[data-index]").forEach(button=>button.onclick=()=>chooseSuggestion(searchResults[Number(button.dataset.index)]))}catch(error){$("suggestions").innerHTML=`<div class="suggestion"><b>${esc(error.message||error)}</b></div>`;$("suggestions").classList.remove("hidden")}}
async function chooseSuggestion(result){try{let selected=result;if(result.provider==="google"&&result.place_id)selected=await get(`/api/geocode-place?place_id=${encodeURIComponent(result.place_id)}&session_token=${encodeURIComponent(searchSession)}`,12000);$("searchInput").value=selected.display_name||result.display_name||"";$("suggestions").classList.add("hidden");searchSession=crypto.randomUUID?crypto.randomUUID():String(Date.now());map.setView([selected.lat,selected.lon],18);await selectParcel(selected.lat,selected.lon)}catch(error){alert(error.message||String(error))}}
$("searchInput").addEventListener("input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(loadSuggestions,260)});$("searchInput").addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();$("findSiteBtn").click()}if(event.key==="Escape")$("suggestions").classList.add("hidden")});$("findSiteBtn").onclick=async()=>{if(searchResults.length)return chooseSuggestion(searchResults[0]);await loadSuggestions();if(searchResults.length)chooseSuggestion(searchResults[0])};document.addEventListener("click",event=>{if(!event.target.closest(".search-wrap"))$("suggestions").classList.add("hidden")});

$("aiLaunch").querySelector("button").onclick=()=>{$("aiLaunch").classList.add("hidden");$("aiBox").classList.remove("hidden");$("aiQuestion").focus()};$("closeAi").onclick=()=>{$("aiBox").classList.add("hidden");$("aiLaunch").classList.remove("hidden")};$("askAiBtn").onclick=async()=>{const question=$("aiQuestion").value.trim();if(!question||!current)return;const box=$("aiAnswer");box.classList.remove("hidden");box.textContent="Thinking…";try{const data=await post("/api/parcel-ai",{question,parcel_context:{parcel:current.parcel,planning_zones:current.planning_zones,development_potential:current.development_potential,automatic_planning_analysis:planning,development_options:proposals?.development_options,investment_summary:proposals?.investment_summary,amenities:locationInfo?.amenities,view_potential:locationInfo?.view_potential},scenario:{}},50000);box.textContent=data.answer||"No answer returned."}catch(error){box.textContent=`Could not answer: ${error.message||error}`}};$("aiQuestion").addEventListener("keydown",event=>{if(event.key==="Enter")$("askAiBtn").click()});
</script>
</body>
</html>"""

CHAT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PLANA.CY</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f3f4f2;color:#17211b}
header{height:78px;padding:15px 26px;border-bottom:1px solid #dfe4e0;background:#fff;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0}
.eyebrow{font-size:11px;letter-spacing:.14em;font-weight:700;color:#68726c}.title{font-size:22px;font-weight:750;margin-top:4px}
.status{font-size:13px;color:#68726c}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#2f8f5b;margin-right:7px}
main{width:min(940px,100%);margin:auto;padding:42px 20px 180px}.welcome{text-align:center;max-width:700px;margin:40px auto}.icon{width:50px;height:50px;border-radius:14px;background:#173f2b;color:#fff;display:grid;place-items:center;margin:0 auto 18px;font-size:23px}
h1{font-size:31px;margin:0 0 10px}.muted{color:#68726c;line-height:1.6}.examples{display:grid;gap:10px;margin-top:26px}
.example{border:1px solid #dfe4e0;background:#fff;border-radius:14px;padding:14px 16px;text-align:left;cursor:pointer}
.row{display:flex;margin:22px 0}.user{justify-content:flex-end}.bubble{max-width:72%;background:#173f2b;color:#fff;border-radius:18px 18px 4px 18px;padding:13px 16px;line-height:1.5}
.card{width:100%;background:#fff;border:1px solid #dfe4e0;border-radius:18px;padding:22px;box-shadow:0 8px 28px rgba(21,44,30,.05)}
.label{color:#173f2b;font-size:11px;font-weight:800;letter-spacing:.12em;margin-bottom:14px}.answer{line-height:1.7;font-size:15.5px;white-space:pre-wrap}
details{margin-top:18px;border-top:1px solid #dfe4e0;padding-top:14px}summary{cursor:pointer;color:#68726c;font-weight:600}.source{background:#e9f0eb;border:1px solid #d7e2da;border-radius:12px;padding:12px 14px;margin-top:10px}.source-title{font-weight:700}.source-meta{font-size:12px;color:#68726c;margin-top:5px}
.composer-wrap{position:fixed;left:0;right:0;bottom:0;padding:18px 20px 14px;background:linear-gradient(to top,#f3f4f2 72%,rgba(243,244,242,0))}
form{width:min(900px,calc(100% - 36px));margin:auto;background:#fff;border:1px solid #cfd8d1;border-radius:18px;padding:10px 10px 10px 16px;display:flex;gap:10px;align-items:flex-end;box-shadow:0 12px 34px rgba(20,42,28,.1)}
textarea{flex:1;border:0;resize:none;outline:none;min-height:42px;max-height:180px;padding:10px 2px;font:inherit;line-height:1.45}
button.send{border:0;background:#173f2b;color:#fff;border-radius:12px;padding:11px 18px;font-weight:700;cursor:pointer}button:disabled{opacity:.55}
.note{width:min(900px,calc(100% - 36px));margin:8px auto 0;text-align:center;font-size:11px;color:#68726c}
.error{color:#9b2c2c}
@media(max-width:700px){header{padding:14px 16px}.bubble{max-width:88%}.card{padding:17px}}
</style>
</head>
<body>
<header>
  <div><div class="eyebrow">PLANA.CY</div><div class="title">PLANA.CY</div></div>
  <div style="display:flex;align-items:center;gap:14px"><a href="/" style="text-decoration:none;color:#173f2b;font-weight:700">Site Explorer</a><div class="status"><span class="dot"></span><span id="statusText">Checking…</span></div></div>
</header>

<main id="messages">
  <section class="welcome" id="welcome">
    <div class="icon">⌂</div>
    <h1>Ask a planning question</h1>
    <p class="muted">Ask in English or Greek. Answers are grounded in the planning documents loaded in the knowledge base.</p>
    <div class="examples">
      <button class="example">Does a basement count toward the building coefficient?</button>
      <button class="example">How many parking spaces are required for a house?</button>
      <button class="example">Πώς μετριέται το ύψος σε επικλινές έδαφος;</button>
    </div>
  </section>
</main>

<div class="composer-wrap">
  <form id="form">
    <textarea id="input" rows="1" placeholder="Ask about Cyprus planning regulations…"></textarea>
    <button class="send" id="send" type="submit">Ask</button>
  </form>
  <div class="note">Research assistant only. Verify critical decisions against the official applicable planning instruments.</div>
</div>

<script>
const messages=document.getElementById("messages");
const form=document.getElementById("form");
const input=document.getElementById("input");
const send=document.getElementById("send");

function addUser(text){
  const row=document.createElement("div");row.className="row user";
  const bubble=document.createElement("div");bubble.className="bubble";bubble.textContent=text;
  row.appendChild(bubble);messages.appendChild(row);
}

function addLoading(){
  const row=document.createElement("div");row.className="row";
  row.innerHTML='<div class="card"><div class="label">PLANNING AI</div><div class="answer">Searching planning sources and checking the answer…</div></div>';
  messages.appendChild(row);return row;
}

function addAssistant(data){
  const row=document.createElement("div");row.className="row";
  const card=document.createElement("div");card.className="card";
  const label=document.createElement("div");label.className="label";label.textContent="PLANNING AI";
  const answer=document.createElement("div");answer.className="answer";answer.textContent=data.answer;
  card.append(label,answer);

  if(data.sources && data.sources.length){
    const details=document.createElement("details");
    const summary=document.createElement("summary");summary.textContent="Sources used";
    details.appendChild(summary);
    const seen=new Set();
    data.sources.forEach(s=>{
      const key=s.title+"|"+s.page_number;if(seen.has(key))return;seen.add(key);
      const box=document.createElement("div");box.className="source";
      const t=document.createElement("div");t.className="source-title";t.textContent=s.title;
      const m=document.createElement("div");m.className="source-meta";
      const parts=[];if(s.page_number!=null)parts.push("PDF page "+s.page_number);if(s.section_title)parts.push(s.section_title);if(s.publication_date)parts.push(s.publication_date);
      m.textContent=parts.join(" · ");box.append(t,m);details.appendChild(box);
    });
    card.appendChild(details);
  }
  row.appendChild(card);messages.appendChild(row);
}

async function ask(q){
  q=q.trim();if(!q)return;
  const welcome=document.getElementById("welcome");if(welcome)welcome.remove();
  addUser(q);input.value="";send.disabled=true;
  const loading=addLoading();window.scrollTo({top:document.body.scrollHeight,behavior:"smooth"});
  try{
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),60000);
    const r=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:q}),signal:controller.signal});
    clearTimeout(timer);
    const raw=await r.text();let data={};
    if(raw){try{data=JSON.parse(raw)}catch{data={detail:raw.trim().startsWith("Internal Server Error")?"The server timed out. Please retry the planning question.":`Server returned an invalid response (${r.status}).`}}}
    loading.remove();
    if(!r.ok)throw new Error(data.detail||"Request failed");
    addAssistant(data);
  }catch(e){
    loading.remove();
    const row=document.createElement("div");row.className="row";
    row.innerHTML='<div class="card error">Could not get an answer: '+String(e.message)+'</div>';
    messages.appendChild(row);
  }finally{
    send.disabled=false;input.focus();window.scrollTo({top:document.body.scrollHeight,behavior:"smooth"});
  }
}

form.addEventListener("submit",e=>{e.preventDefault();ask(input.value)});
input.addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();form.requestSubmit()}});
document.querySelectorAll(".example").forEach(b=>b.addEventListener("click",()=>ask(b.textContent)));

fetch("/health").then(async r=>{const raw=await r.text();if(!r.ok)throw new Error();return JSON.parse(raw)}).then(d=>document.getElementById("statusText").textContent="Online · "+d.chunks_loaded+" chunks").catch(()=>document.getElementById("statusText").textContent="Offline");
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def homepage() -> HTMLResponse:
    return HTMLResponse(SITE_HTML)


@app.get("/chat", response_class=HTMLResponse)
def chat_page() -> HTMLResponse:
    return HTMLResponse(CHAT_HTML)

# ============================================================
# COMPACT CLI DISPATCH
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "market-ingest":
        import market_ingest as _plana_market_ingest
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        raise SystemExit(_plana_market_ingest.main())
    main()
