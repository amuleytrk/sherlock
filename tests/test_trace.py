"""Unit tests for the cross-service trace pipeline + stitcher.

Network-free: the stitcher is a pure function over canned ServiceLogs.
Pipeline detection is a pure regex routine."""
from __future__ import annotations

from apps.api.trace.log_fetcher import ServiceLogs
from apps.api.trace.mermaid import render_mermaid
from apps.api.trace.pipeline import detect_identifier, discover_pipeline
from apps.api.trace.stitcher import stitch


def test_detect_identifier_qrcode():
    assert detect_identifier("9E-070524-N29401") == "qrcode"


def test_detect_identifier_tape_id():
    assert detect_identifier("D18C6EDE8E62") == "tape_id"
    assert detect_identifier("aabbccddeeff") == "tape_id"


def test_detect_identifier_correlation_uuid():
    assert detect_identifier("8680093d-b639-4083-bcc9-82068552716c") == "correlation_id"


def test_detect_identifier_unknown():
    assert detect_identifier("not an identifier") == "unknown"
    assert detect_identifier("") == "unknown"


def test_pipeline_qrcode_picks_milestone_flow():
    p = discover_pipeline("9E-070524-N29401")
    assert "external-service" in p.services
    assert "ingress-service" in p.services


def test_pipeline_tape_id_picks_device_event():
    p = discover_pipeline("D18C6EDE8E62")
    # device-event flow includes ingress + preprocessor; explicit hint
    # would force milestone, no hint → device-event.
    assert "ingress-service" in p.services


def test_pipeline_hint_overrides_default():
    p = discover_pipeline("D18C6EDE8E62", hint="milestone")
    assert p.flow_label.startswith("Milestone")
    assert "external-service" in p.services


def _line_with_corr(ts: str, corr: str, msg: str) -> str:
    return f'{ts} {{"correlation_id":"{corr}","level":"info","message":"{msg}"}}'


def test_stitcher_matches_identifier_and_propagated_corr():
    """external-service has the qrcode and correlation A; ingress-service
    has correlation A in a downstream log line that doesn't repeat the qrcode.
    The stitcher should pull both into the timeline."""
    qrcode = "9E-070524-N29401"
    services = [
        ServiceLogs(
            service="external-service", label_selector="app=external-service-stage", pod_count=1,
            raw_log="\n".join([
                _line_with_corr(
                    "2026-04-29T17:06:11.647000Z", "A1B2C3",
                    f"processMessage qrcode={qrcode}",
                ),
                _line_with_corr(
                    "2026-04-29T17:06:12.000000Z", "A1B2C3",
                    "sendToEventGrid done",
                ),
            ]),
        ),
        ServiceLogs(
            service="ingress-service", label_selector="app=ingress-service-stage", pod_count=1,
            raw_log="\n".join([
                _line_with_corr(
                    "2026-04-29T17:06:13.500000Z", "A1B2C3",
                    "postMilestone received eventgrid payload",
                ),
            ]),
        ),
    ]
    trace = stitch(qrcode, services)
    assert len(trace.events) == 3
    services_seen = trace.services_seen
    assert services_seen == ["external-service", "ingress-service"]
    assert "A1B2C3" in trace.correlation_ids


def test_stitcher_detects_first_error_event():
    qrcode = "9E-070524-N29401"
    services = [
        ServiceLogs(
            service="ingress-service", label_selector="app=ingress-service-stage", pod_count=1,
            raw_log="\n".join([
                f'2026-04-29T17:06:11.647000Z {{"correlation_id":"X","level":"info","message":"insert qrcode={qrcode}"}}',
                f'2026-04-29T17:06:11.700000Z {{"correlation_id":"X","level":"error","message":"insertMilestoneLookup :: Error converting nvarchar to bigint"}}',
            ]),
        ),
    ]
    trace = stitch(qrcode, services)
    assert trace.failure_event_idx is not None
    assert trace.events[trace.failure_event_idx].is_error


def test_stitcher_returns_empty_when_no_matches():
    trace = stitch("absent-id", [
        ServiceLogs(service="ingress-service", label_selector="x", pod_count=1,
                    raw_log='2026-04-29T17:00:00Z {"level":"info","message":"unrelated"}'),
    ])
    assert trace.events == []
    assert "No log lines" in trace.summary


def test_mermaid_renders_for_empty_trace():
    """Even with no events, the diagram should be valid Mermaid (UI never
    breaks). Just verify it starts with 'sequenceDiagram'."""
    trace = stitch("absent", [])
    out = render_mermaid(trace)
    assert out.startswith("sequenceDiagram")


def test_mermaid_includes_all_services_seen():
    qrcode = "9E-070524-N29401"
    services = [
        ServiceLogs(service="external-service", label_selector="x", pod_count=1,
                    raw_log=_line_with_corr("2026-04-29T17:06:11.000Z", "C1",
                                            f"processMessage qrcode={qrcode}")),
        ServiceLogs(service="ingress-service", label_selector="x", pod_count=1,
                    raw_log=_line_with_corr("2026-04-29T17:06:12.000Z", "C1",
                                            "postMilestone received")),
    ]
    trace = stitch(qrcode, services)
    out = render_mermaid(trace)
    assert "external-service" in out
    assert "ingress-service" in out
