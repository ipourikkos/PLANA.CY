PLANA.CY FINAL FAST BUILD — v1.4.0

Critical-path changes
- Parcel highlight remains immediate after /api/parcel-basic.
- /api/parcel-details now bundles deterministic initial proposals.
- Development options no longer wait for AI, market analysis, or GIS extras.
- Market/opportunity analysis enriches value independently and bundles market-enriched proposals.
- Automatic planning AI is background/idle work and refines options later.
- Critical GIS checks run independently and no longer block the UI.

Backend performance
- Shared keep-alive httpx.AsyncClient for DLS and Nominatim.
- Parcel point cache: 5 minutes.
- Canonical parcel details cache: 30 minutes.
- Market analysis cache: 15 minutes.
- Site-extra cache: 15 minutes.
- Planning analysis cache: 2 hours.
- Market query maximum reduced from 5,000 rows to 1,500 rows per pool.
- Market queries now request only schema-valid columns.
- GZip middleware enabled for larger API payloads.
- Automatic DLS extras reduced to Buildings, Coast Protection Zone, and State Land.
- DLS map overlay limited to parcel layer 0 over OpenStreetMap.

Bug fixes
- Corrected market-observation selected columns to match MARKET_SCHEMA.sql.
- Corrected site-extra spatial-check payload to match frontend warning logic.
- Parcel AI skips generic query expansion and LLM reranking; structured parcel context narrows retrieval.

Validation
- Python compilation passed.
- Site browser JavaScript passed node --check.
- App import with dependency stubs passed.
- 36 structured planning rules loaded.
- 39 market sources registered.
- Deterministic sample capacity test passed: 1,713.14 m2 floor area / 713.81 m2 coverage.
- Initial proposal bundling passed.
- Site-extra spatial flag response and cache passed.
- MARKET_SCHEMA.sql column compatibility check passed.

Live external DLS, Supabase, and OpenAI production credentials were not used in the final automated tests.
