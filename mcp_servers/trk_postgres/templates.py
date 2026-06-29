"""Vetted parameterized SELECT templates for the trk PostgreSQL schema.

The MCP tool accepts a `query_type` from this catalog plus named parameters.
It does NOT accept arbitrary SQL — eliminating injection regardless of what
the LLM sends.

SQL conventions:
- Named psycopg params: %(name)s
- Schema-qualified: trk.<table> (search_path=trk also set at connection)
- Every query has a LIMIT clause (Sherlock is read-only diagnostic)
- device_event queries: always filter year + device_id (partition pruning)
- raw_device_event_info joins on (partition, id) — NOT just id
- No vendor_config (not in trk), no MSSQL syntax, no forbidden legacy tables
"""
from __future__ import annotations

import datetime


def _current_year() -> int:
    return datetime.datetime.now(datetime.timezone.utc).year


CATALOG: dict[str, dict] = {
    # ------------------------------------------------------------------
    # Q1: device_config
    # Full device config + feature flags for a device_id.
    # Replaces tapecfg_db + customer_cfg join.
    # ------------------------------------------------------------------
    "device_config": {
        "description": (
            "Full device config + feature flags for a device_id "
            "(replaces tapecfg_db + customer_cfg join)."
        ),
        "required": ["device_id"],
        "optional": [],
        "sql": """
SELECT
    d.device_id,
    d.qrcode,
    d.asset_barcode,
    d.status                        AS device_status,
    d.firmware,
    d.battery_level,
    d.displayname,
    d.facility_id,
    d.linked_at                     AS activation_date,
    d.fw_version,
    d.sw_version,
    d.hw_version,
    d.latest_events,
    acc.customer_id,
    acc.authorized_group,
    acc.id                          AS account_id,
    app.code                        AS application_code,
    app.id                          AS application_id,
    app.device_type,
    app.asset_type,
    app.classification,
    conf.data                       AS feature_configuration
FROM trk.device d
LEFT JOIN trk.account acc
    ON d.account_id = acc.id
LEFT JOIN trk.application app
    ON d.application_id = app.id
LEFT JOIN LATERAL (
    SELECT data FROM trk.configuration
    WHERE application_id = app.id
      AND type = 'FEATURE'
    LIMIT 1
) conf ON true
WHERE d.device_id = %(device_id)s
LIMIT 1
""",
    },

    # ------------------------------------------------------------------
    # Q2: location_history
    # Last N device_event rows for a device.
    # Replaces lookup_parcels SELECT TOP 20.
    # ------------------------------------------------------------------
    "location_history": {
        "description": (
            "Last N device_event rows for a device_id (replaces lookup_parcels). "
            "Always supply year to prune to 1-2 partitions; default is current UTC year."
        ),
        "required": ["device_id"],
        "optional": ["year", "limit"],
        "sql": """
SELECT
    device_id,
    parent_location     AS facility,
    location            AS milestone,
    ts,
    last_seen,
    scan_type           AS scantype,
    bat,
    lat,
    lon,
    facility_id,
    event_collation_flag
FROM trk.device_event
WHERE device_id = %(device_id)s
  AND year = %(year)s
ORDER BY last_seen DESC, id DESC
LIMIT %(limit)s
""",
    },

    # ------------------------------------------------------------------
    # Q3: device_events_recent
    # Last N raw_device_event rows for a device.
    # Replaces proximity_db.  Uses MATERIALIZED CTE to avoid 285M-row
    # cross-partition scan; joins raw_device_event_info on (partition, id).
    # Note: raw_device_event partitions by DOY (not year), but year is included
    # as an optional param for consistency with other event-related templates.
    # ------------------------------------------------------------------
    "device_events_recent": {
        "description": (
            "Last N raw_device_event rows for a device_id (replaces proximity_db). "
            "Uses MATERIALIZED CTE + partition-safe join on raw_device_event_info."
        ),
        "required": ["device_id"],
        "optional": ["limit", "year"],
        "sql": """
WITH top_r AS MATERIALIZED (
    SELECT partition, id, ts, scan_type, e0, morepids, payload
    FROM trk.raw_device_event
    WHERE device_id = %(device_id)s
    ORDER BY ts DESC
    LIMIT %(limit)s
)
SELECT
    r.id,
    r.ts,
    r.scan_type                         AS scantype,
    (r.payload->>'seqno')::int          AS seqno,
    (r.payload->>'glat')::float8        AS glat,
    (r.payload->>'glon')::float8        AS glon,
    r.payload->>'bat'                   AS bat,
    r.payload->>'pid1'                  AS pid1,
    r.payload->>'pid2'                  AS pid2,
    r.payload->>'pid3'                  AS pid3,
    r.payload->>'type'                  AS type,
    r.morepids,
    r.e0,
    i.chosen_lime,
    i.clat,
    i.clon,
    i.duped,
    i.skipped_flag
FROM top_r r
LEFT JOIN trk.raw_device_event_info i
    ON r.partition = i.partition
    AND r.id = i.id
ORDER BY r.ts DESC
LIMIT %(limit)s
""",
    },

    # ------------------------------------------------------------------
    # Q4: raw_events_check
    # Did raw events arrive for a device since a timestamp?
    # Note: raw_device_event partitions by DOY (not year), but year is included
    # as an optional param for consistency with other event-related templates.
    # ------------------------------------------------------------------
    "raw_events_check": {
        "description": (
            "Did raw_device_event rows arrive for a device_id since a timestamp? "
            "Params: device_id, since_ts (epoch seconds). Optional: limit, year."
        ),
        "required": ["device_id", "since_ts"],
        "optional": ["limit", "year"],
        "sql": """
SELECT
    id,
    device_id,
    ts,
    scan_type                       AS scantype,
    e0,
    morepids,
    (payload->>'seqno')::int        AS seqno,
    (payload->>'bat')               AS bat,
    (payload->>'glat')::float8      AS glat,
    (payload->>'glon')::float8      AS glon,
    payload->>'type'                AS type,
    created_at
FROM trk.raw_device_event
WHERE device_id = %(device_id)s
  AND ts > %(since_ts)s
ORDER BY ts DESC
LIMIT %(limit)s
""",
    },

    # ------------------------------------------------------------------
    # Q5: customer_config
    # All application configs for a customer.
    # Replaces customer_cfg SELECT all.
    # ------------------------------------------------------------------
    "customer_config": {
        "description": (
            "All application configs for a customer (replaces customer_cfg SELECT all). "
            "Params: customer_id, authorized_group."
        ),
        "required": ["customer_id", "authorized_group"],
        "optional": [],
        "sql": """
SELECT
    acc.customer_id,
    acc.authorized_group,
    app.id                          AS application_uuid,
    app.code                        AS application_id,
    app.name                        AS application_name,
    app.device_type                 AS tape_type,
    app.classification              AS personality,
    app.asset_type,
    conf.type                       AS config_type,
    conf.data                       AS configuration
FROM trk.account acc
JOIN trk.application app
    ON app.account_id = acc.id
LEFT JOIN trk.configuration conf
    ON conf.application_id = app.id
WHERE acc.customer_id = %(customer_id)s
  AND acc.authorized_group = %(authorized_group)s
ORDER BY app.code, conf.type
LIMIT 200
""",
    },

    # ------------------------------------------------------------------
    # Q6: feature_flags
    # Feature flag JSONB fields for a specific application (by UUID).
    # Replaces JSON_VALUE queries.
    # ------------------------------------------------------------------
    "feature_flags": {
        "description": (
            "Feature flag JSONB fields for an application UUID "
            "(replaces JSON_VALUE queries). Param: application_id (UUID). "
            "Derive UUID via customer_config or account_lookup first if you only have code."
        ),
        "required": ["application_id"],
        "optional": [],
        "sql": """
SELECT
    acc.customer_id,
    acc.authorized_group,
    app.code                                                    AS application_id,
    app.id                                                      AS application_uuid,
    (conf.data->>'lookup_event_insertion')                      AS lookup_event_insertion,
    (conf.data->>'enableCellularEvents')                        AS enable_cellular,
    (conf.data->>'limeMeshEvents')                              AS lime_mesh,
    (conf.data->>'cross_customer_mesh_allowed')::int            AS cross_mesh,
    (conf.data->>'cargoIQIntergation')                          AS cargo_iq,
    (conf.data->>'processHealth')::boolean                      AS process_health,
    (conf.data->>'enableEventCollation')::boolean               AS enable_event_collation,
    conf.data                                                   AS full_feature_config
FROM trk.configuration conf
JOIN trk.application app
    ON app.id = conf.application_id
JOIN trk.account acc
    ON acc.id = app.account_id
WHERE conf.application_id = %(application_id)s
  AND conf.type = 'FEATURE'
LIMIT 1
""",
    },

    # ------------------------------------------------------------------
    # Q7: facility_lookup
    # Facility metadata + parent (replaces facilities_db).
    # ------------------------------------------------------------------
    "facility_lookup": {
        "description": (
            "Facility metadata + parent hierarchy (replaces facilities_db). "
            "Param: facility_id (UUID)."
        ),
        "required": ["facility_id"],
        "optional": [],
        "sql": """
SELECT
    f.id                            AS facility_id,
    f.parent_id                     AS parent_facility_id,
    f.account_id,
    f.designation                   AS facility_designation,
    f.designation_description       AS facility_designation_description,
    f.type_id                       AS facility_type,
    ft.description                  AS facility_type_description,
    f.location_id,
    fl.description                  AS location_description,
    f.lat,
    f.lon,
    f.radius,
    f.timezone_name,
    f.scale_factor
FROM trk.facility f
LEFT JOIN trk.facility_type ft
    ON f.type_id = ft.id
LEFT JOIN trk.facility_location fl
    ON f.location_id = fl.id
WHERE f.id = %(facility_id)s
LIMIT 1
""",
    },

    # ------------------------------------------------------------------
    # Q8: duplicate_check
    # Find duplicate device rows by device_id OR qrcode.
    # Replaces tapecfg_db dupe query.
    # ------------------------------------------------------------------
    "duplicate_check": {
        "description": (
            "Find duplicate device rows by device_id OR qrcode (data integrity check). "
            "Params: device_id, qrcode."
        ),
        "required": ["device_id", "qrcode"],
        "optional": [],
        "sql": """
SELECT
    d.device_id,
    d.qrcode,
    d.asset_barcode,
    d.status                    AS device_status,
    d.linked_at                 AS activation_date,
    acc.customer_id,
    acc.authorized_group,
    acc.id                      AS account_id,
    COUNT(*) OVER ()            AS total_records
FROM trk.device d
LEFT JOIN trk.account acc
    ON d.account_id = acc.id
WHERE d.device_id = %(device_id)s
   OR d.qrcode = %(qrcode)s
ORDER BY d.id DESC
LIMIT 20
""",
    },

    # ------------------------------------------------------------------
    # Q9: device_health
    # Device health history + signal timeline (no MSSQL equivalent).
    # Params: account_id (UUID), qrcode.
    # Note: device_health_history uses account_id (UUID) as tenant filter
    # after migration 000032 dropped customer_id/authorized_group cols.
    # year is included as an optional param for consistency with event templates.
    # ------------------------------------------------------------------
    "device_health": {
        "description": (
            "Device health history for a device (trk.device_health_history). "
            "Params: account_id (UUID), qrcode. Optional: limit, year. "
            "Derive account_id via account_lookup or device_config if needed."
        ),
        "required": ["account_id", "qrcode"],
        "optional": ["limit", "year"],
        "sql": """
SELECT
    device_id,
    qrcode,
    facility_name           AS milestone,
    parent_facility_name    AS facility,
    last_ping,
    local_ts,
    signal,
    battery,
    battery_voltage,
    location_source,
    lat,
    lon,
    excursions,
    facility_id,
    application_id,
    scantype,
    location_xy
FROM trk.device_health_history
WHERE account_id = %(account_id)s
  AND qrcode = %(qrcode)s
ORDER BY last_ping DESC
LIMIT %(limit)s
""",
    },

    # ------------------------------------------------------------------
    # Q10: event_delivery_check
    # Did device_event rows land for a device since a timestamp?
    # Replaces lookup_parcels delivery check.
    # ------------------------------------------------------------------
    "event_delivery_check": {
        "description": (
            "Did device_event rows land for a device_id since a timestamp? "
            "(Event Grid delivery check — replaces lookup_parcels.) "
            "Params: device_id, year, since_ts (epoch). Optional: limit."
        ),
        "required": ["device_id", "since_ts"],
        "optional": ["year", "limit"],
        "sql": """
SELECT
    device_id,
    parent_location     AS facility,
    location            AS milestone,
    ts,
    last_seen,
    scan_type           AS scantype,
    bat,
    facility_id
FROM trk.device_event
WHERE device_id = %(device_id)s
  AND year = %(year)s
  AND ts > %(since_ts)s
ORDER BY ts DESC
LIMIT %(limit)s
""",
    },

    # ------------------------------------------------------------------
    # Q11: account_lookup
    # Resolve account_id UUID from customer_id + authorized_group.
    # Verifies the derivation matches the live row.
    # ------------------------------------------------------------------
    "account_lookup": {
        "description": (
            "Resolve account_id UUID from customer_id + authorized_group. "
            "Verifies deterministic MD5 derivation against live row. "
            "Params: customer_id, authorized_group."
        ),
        "required": ["customer_id", "authorized_group"],
        "optional": [],
        "sql": """
SELECT
    id              AS account_id,
    customer_id,
    authorized_group,
    customer_name,
    organization_id,
    parent_id,
    metadata
FROM trk.account
WHERE customer_id = %(customer_id)s
  AND authorized_group = %(authorized_group)s
LIMIT 1
""",
    },

    # ------------------------------------------------------------------
    # Q12: application_lookup
    # Look up application + config by customer_id + authorized_group + code.
    # ------------------------------------------------------------------
    "application_lookup": {
        "description": (
            "Look up a specific application + its FEATURE config by "
            "customer_id, authorized_group, and application_code. "
            "Params: customer_id, authorized_group, application_code."
        ),
        "required": ["customer_id", "authorized_group", "application_code"],
        "optional": [],
        "sql": """
SELECT
    acc.id                          AS account_id,
    acc.customer_id,
    acc.authorized_group,
    app.id                          AS application_id,
    app.code                        AS application_code,
    app.name                        AS application_name,
    app.device_type,
    app.asset_type,
    app.classification,
    app.register_in_lorawan,
    app.dynamic_join,
    conf.data                       AS feature_configuration
FROM trk.account acc
JOIN trk.application app
    ON app.account_id = acc.id
LEFT JOIN LATERAL (
    SELECT data FROM trk.configuration
    WHERE application_id = app.id
      AND type = 'FEATURE'
    LIMIT 1
) conf ON true
WHERE acc.customer_id = %(customer_id)s
  AND acc.authorized_group = %(authorized_group)s
  AND app.code = %(application_code)s
LIMIT 1
""",
    },
}
