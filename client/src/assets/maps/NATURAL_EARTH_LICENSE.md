# Natural Earth world map

- File: `world-countries.geo.json` (feature properties: `code` = ISO 3166-1 alpha-2, `name`).
- Source: Natural Earth 1:110m Cultural Vectors, Admin 0 – Countries
  (`ne_110m_admin_0_countries`), https://www.naturalearthdata.com/downloads/110m-cultural-vectors/
  fetched via https://github.com/nvkelso/natural-earth-vector (`geojson/ne_110m_admin_0_countries.geojson`) on 2026-11-21.
- Modifications: kept only `ISO_A2_EH` (fallback `ISO_A2`) as `code` and `NAME` as `name`; features without a
  valid ISO-2 code dropped; coordinates rounded to 2 decimals.
- License: Public domain. "Made with Natural Earth. Free vector and raster map data @ naturalearthdata.com."
  Terms: https://www.naturalearthdata.com/about/terms-of-use/ (no permission or attribution required).
- Boundaries are as drawn by Natural Earth and imply no political position.

# Country marker fallback

- File: `country-markers.json` (`{ "<ISO-2>": [lon, lat, "<capital name>"] }`).
- Source: Natural Earth 1:10m Cultural Vectors, Populated Places (simple), `ne_10m_populated_places_simple`,
  https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-populated-places/
  via https://github.com/nvkelso/natural-earth-vector, fetched 2026-11-21.
- Modifications: only features with `adm0cap = 1` and a valid `iso_a2`; first capital per code kept; coordinates
  rounded to 2 decimals; only code, lon, lat, name retained.
- License: Public domain (same Natural Earth terms as above).
- Used to draw a marker for countries that have listens but no polygon in the 110m map (e.g. SG, MT, BH).
- Supplement (2026-11-21): 35 further codes without a capital in the populated-places file or a 110m polygon
  (e.g. HK, MO, GI, AW, FO, GU) were added from `ne_10m_admin_0_countries`
  (https://github.com/nvkelso/natural-earth-vector, `geojson/ne_10m_admin_0_countries.geojson`) using
  `ISO_A2_EH` (fallback `ISO_A2`), `LABEL_X`/`LABEL_Y` (label point, not a capital) and `NAME`; only these
  fields kept, coordinates rounded to 2 decimals. Public domain. Geometry not bundled.
- Second supplement (2026-11-21): BQ, BV, CC, CX, GF, GP, MQ, RE, SJ, TK, YT from
  `ne_10m_admin_0_map_units` (`geojson/ne_10m_admin_0_map_units.geojson`; map units such as Réunion are not
  separate countries in admin_0_countries) using `ISO_A2_EH`/`ISO_A2`, `LABEL_X`/`LABEL_Y`, `NAME`. Public domain.
- Coverage check: every officially assigned ISO 3166-1 alpha-2 code known to ICU region names (checked with
  `Intl.DisplayNames`) has a polygon (110m) or marker. Not covered, by design: transitional/exceptionally
  reserved or deprecated codes (AC, AN, BU, CP, CQ, CS, DD, DG, DY, EA, EU, EZ, FX, HV, IC, NH, QO, RH, SU, TA,
  TP, UK, UN, VD, XA, XB, YD, YU, ZR, ZZ). The unofficial XK (Kosovo) is a polygon if Natural Earth supplies it.
