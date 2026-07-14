# PLANA.CY — Priority 2.5 structured planning rules

## Source hierarchy

1. **Ministerial Order 4/2026** is the authoritative structured source for the covered calculation and boundary-distance topics. It is treated as effective from 11 May 2026 and as superseding Order 4/2024 for those topics.
2. **ETEK March 2026 concise guide** is interpretive context. It is non-exhaustive and its own stated scope is the four major-city Local Plans. It does not override Order 4/2026.
3. **Supabase RAG** remains responsible for parcel-specific Development Plan policies, use restrictions, special provisions, exceptions and other sources outside the structured rule catalogue.

## Files

- `app.py` — FastAPI application, DLS integration, automatic planning intelligence, structured rule orchestration and UI.
- `planning_rules.py` — versioned rule catalogue and deterministic/provisional rule calculations.
- `requirements.txt` — existing Python dependencies.

## New API

`POST /api/parcel-rule-analysis`

Example request body:

```json
{
  "parcel_id": 123,
  "scenario": {
    "development_type": "residential",
    "development_form": "apartment_building",
    "residential_units": 12,
    "unit_usable_areas_m2": [80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80],
    "outside_urban_core_historic_or_dense_area": true,
    "etek_guide_scope_confirmed": true,
    "has_basement": true,
    "civil_defence_shelter_required": true,
    "net_parcel_area_m2": 1200
  }
}
```

The response separates:

- `applied_rules`
- `triggered_rules`
- `conditional_rules`
- `scenario_calculations`
- `checks_before_reliance`
- source metadata and rule-engine version

## Important limitation

The DLS registered parcel extent is still used as a preliminary base until PLANA has a confirmed clean/net development area after road, access, public-open-space or other land commitments. Baseline building setbacks also require the applicable Development Plan and its General Policy Provisions. The engine explicitly marks these cases rather than treating them as final planning entitlement.
