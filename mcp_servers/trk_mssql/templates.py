"""Vetted parameterized SELECT templates for the trk schema (MSSQL PPE).

The MCP tool accepts a `query_type` from this catalog plus parameters. It does
NOT accept arbitrary SQL — eliminating injection regardless of what the LLM
sends.

If you need a new query, add it here, document the params, and the agent will
discover it automatically via list_tools metadata.
"""
from __future__ import annotations


QUERY_TEMPLATES: dict[str, dict] = {
    "device_config": {
        "params": ["tape_id"],
        "doc": "Full device config + customer feature_configuration for a tape_id.",
        "sql": """
            SELECT t.tape_id, t.device_status, t.customer_id, t.authorized_group,
                   t.application_id, t.tape_type, t.tape_personality, t.facility,
                   t.activation_date, t.batlevel, t.lastupdate, t.AssetBarCode,
                   c.feature_configuration, c.application_name
            FROM trk.tapecfg_db t
            LEFT JOIN trk.customer_cfg c
              ON t.customer_id = c.customer_id
             AND t.authorized_group = c.authorized_group
             AND t.application_id = c.application_id
            WHERE t.tape_id = %(tape_id)s
        """,
    },
    "location_history": {
        "params": ["tape_id"],
        "doc": "Last 20 lookup_parcels rows for a tape_id (location history).",
        "sql": """
            SELECT TOP 20 tape_id, facility, milestone, ts, last_seen, scantype,
                   bat, lat, lon, facility_id, event_collation_flag
            FROM trk.lookup_parcels
            WHERE tape_id = %(tape_id)s
            ORDER BY ts DESC
        """,
    },
    "device_events_recent": {
        "params": ["tape_id"],
        "doc": "Last 10 raw proximity_db events for a tape_id.",
        "sql": """
            SELECT TOP 10 central_id, ts, glat, glon, bat, scantype,
                   pid1, pid2, pid3, chosen_lime, morepids, rid, type
            FROM trk.proximity_db
            WHERE central_id = %(tape_id)s
            ORDER BY ts DESC
        """,
    },
    "customer_config": {
        "params": ["customer_id", "authorized_group"],
        "doc": "Customer's app configurations (all application_ids).",
        "sql": """
            SELECT customer_id, authorized_group, application_id,
                   application_name, tape_type, personality,
                   feature_configuration, configuration
            FROM trk.customer_cfg
            WHERE customer_id = %(customer_id)s
                  AND authorized_group = %(authorized_group)s
        """,
    },
    "facility_lookup": {
        "params": ["facility_id"],
        "doc": "Facility metadata + parent hierarchy.",
        "sql": """
            SELECT facility_id, facility_designation_description,
                   facility_type, parent_facility_id, lat, lon, radius
            FROM trk.facilities_db
            WHERE facility_id = %(facility_id)s
        """,
    },
    "feature_flags": {
        "params": ["customer_id", "authorized_group", "application_id"],
        "doc": "Common boolean feature flags surfaced from feature_configuration JSON.",
        "sql": """
            SELECT customer_id, authorized_group, application_id,
                   JSON_VALUE(feature_configuration, '$.lookup_event_insertion') AS lookup_event_insertion,
                   JSON_VALUE(feature_configuration, '$.enableCellularEvents') AS enable_cellular,
                   JSON_VALUE(feature_configuration, '$.limeMeshEvents') AS lime_mesh,
                   JSON_VALUE(feature_configuration, '$.cross_customer_mesh_allowed') AS cross_mesh,
                   JSON_VALUE(feature_configuration, '$.cargoIQIntergation') AS cargo_iq
            FROM trk.customer_cfg
            WHERE customer_id = %(customer_id)s
                  AND authorized_group = %(authorized_group)s
                  AND application_id = %(application_id)s
        """,
    },
    "duplicate_check": {
        "params": ["tape_id", "qrcode"],
        "doc": "Find duplicates of a device by tape_id OR qrcode (data integrity issue check).",
        "sql": """
            SELECT tape_id, qrcode, AssetBarCode, customer_id, authorized_group,
                   device_status, activation_date, COUNT(*) OVER() AS total_records
            FROM trk.tapecfg_db
            WHERE tape_id = %(tape_id)s OR qrcode = %(qrcode)s
        """,
    },
    "raw_events_check": {
        "params": ["tape_id", "since_ts"],
        "doc": "Recent raw proximity events for a tape since a timestamp (did events arrive at all?).",
        "sql": """
            SELECT TOP 10 central_id, ts, scantype, bat, glat, glon,
                   pid1, pid2, chosen_lime, morepids, rid, type, sensor_violations
            FROM trk.proximity_db
            WHERE central_id = %(tape_id)s AND ts > %(since_ts)s
            ORDER BY ts DESC
        """,
    },
    "event_delivery_check": {
        "params": ["tape_id", "since_ts"],
        "doc": "Did lookup_parcels rows land for a tape since a timestamp? (Event Grid delivery check.)",
        "sql": """
            SELECT TOP 10 tape_id, facility, milestone, ts, scantype, last_seen
            FROM trk.lookup_parcels
            WHERE tape_id = %(tape_id)s AND ts > %(since_ts)s
            ORDER BY ts DESC
        """,
    },
}
