from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from lib.moai_writer import MoaiWriterConfig, load_moai_writer_config


FetchRows = Callable[[str, dict[str, str]], list[dict[str, Any]]]


@dataclass
class MoaiIdentityResolution:
    organization_id: str | None = None
    subject_person_id: str | None = None
    provider_person_id: str | None = None
    encounter_id: str | None = None
    physio_client_id: str | None = None
    status: str = "unresolved"
    methods: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "subject_person_id": self.subject_person_id,
            "provider_person_id": self.provider_person_id,
            "encounter_id": self.encounter_id,
            "physio_client_id": self.physio_client_id,
            "status": self.status,
            "methods": self.methods,
            "warnings": self.warnings,
        }


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _build_headers(config: MoaiWriterConfig) -> dict[str, str]:
    headers = {
        "apikey": config.api_key,
        "Accept": "application/json",
    }
    if config.auth_header:
        headers["Authorization"] = config.auth_header
    return headers


def _make_fetcher(config: MoaiWriterConfig) -> FetchRows:
    def fetch_rows(table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        response = requests.get(
            f"{config.base_url}/rest/v1/{table}",
            headers=_build_headers(config),
            params=params,
            timeout=config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    return fetch_rows


def _single_row(rows: list[dict[str, Any]], resolution: MoaiIdentityResolution, *, warning: str) -> dict[str, Any] | None:
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        resolution.warnings.append(warning)
    return None


def _resolve_client_by_org_client_id(
    *,
    physio_client_id: str,
    organization_id: str | None,
    fetch_rows: FetchRows,
    resolution: MoaiIdentityResolution,
) -> None:
    params = {
        "select": "id,person_id,organization_id,status",
        "id": f"eq.{physio_client_id}",
        "limit": "2",
    }
    if organization_id:
        params["organization_id"] = f"eq.{organization_id}"
    row = _single_row(
        fetch_rows("org_clients", params),
        resolution,
        warning="multiple org_clients matched physio_client_id; identity not auto-resolved",
    )
    if not row:
        return

    resolution.physio_client_id = str(row.get("id") or physio_client_id)
    resolution.subject_person_id = _clean(row.get("person_id")) or resolution.subject_person_id
    resolution.organization_id = _clean(row.get("organization_id")) or resolution.organization_id
    resolution.methods.append("org_clients.id")


def _resolve_client_by_person_id(
    *,
    subject_person_id: str,
    organization_id: str | None,
    fetch_rows: FetchRows,
    resolution: MoaiIdentityResolution,
) -> None:
    if not organization_id:
        return
    row = _single_row(
        fetch_rows(
            "org_clients",
            {
                "select": "id,person_id,organization_id,status",
                "person_id": f"eq.{subject_person_id}",
                "organization_id": f"eq.{organization_id}",
                "limit": "2",
            },
        ),
        resolution,
        warning="multiple org_clients matched subject_person_id; client membership not auto-selected",
    )
    if row:
        resolution.physio_client_id = _clean(row.get("id")) or resolution.physio_client_id
        resolution.methods.append("org_clients.person_id")


def _resolve_by_encounter_id(
    *,
    encounter_id: str,
    fetch_rows: FetchRows,
    resolution: MoaiIdentityResolution,
) -> None:
    row = _single_row(
        fetch_rows(
            "encounters",
            {
                "select": "id,organization_id,provider_person_id,subject_person_id",
                "id": f"eq.{encounter_id}",
                "limit": "2",
            },
        ),
        resolution,
        warning="multiple encounters matched encounter_id; identity not auto-resolved",
    )
    if not row:
        return
    resolution.encounter_id = _clean(row.get("id")) or resolution.encounter_id
    resolution.organization_id = _clean(row.get("organization_id")) or resolution.organization_id
    resolution.provider_person_id = _clean(row.get("provider_person_id")) or resolution.provider_person_id
    resolution.subject_person_id = _clean(row.get("subject_person_id")) or resolution.subject_person_id
    resolution.methods.append("encounters.id")


def _resolve_client_by_name(
    *,
    patient_name: str,
    organization_id: str | None,
    fetch_rows: FetchRows,
    resolution: MoaiIdentityResolution,
) -> None:
    if not organization_id:
        resolution.warnings.append("patient_name lookup skipped because organization_id is missing")
        return

    people = fetch_rows(
        "persons",
        {
            "select": "id,display_name,first_name,last_name,user_type,is_active",
            "display_name": f"eq.{patient_name}",
            "is_active": "is.true",
            "limit": "10",
        },
    )
    if not people:
        people = fetch_rows(
            "persons",
            {
                "select": "id,display_name,first_name,last_name,user_type,is_active",
                "first_name": f"eq.{patient_name}",
                "is_active": "is.true",
                "limit": "10",
            },
        )
    person_ids = [_clean(row.get("id")) for row in people]
    person_ids = [person_id for person_id in person_ids if person_id]
    if not person_ids:
        return

    id_list = ",".join(person_ids)
    client_rows = fetch_rows(
        "org_clients",
        {
            "select": "id,person_id,organization_id,status",
            "organization_id": f"eq.{organization_id}",
            "person_id": f"in.({id_list})",
            "limit": "2",
        },
    )
    row = _single_row(
        client_rows,
        resolution,
        warning="multiple org clients matched patient_name; identity not auto-resolved",
    )
    if not row:
        return

    resolution.physio_client_id = _clean(row.get("id")) or resolution.physio_client_id
    resolution.subject_person_id = _clean(row.get("person_id")) or resolution.subject_person_id
    resolution.organization_id = _clean(row.get("organization_id")) or resolution.organization_id
    resolution.methods.append("patient_name+org_clients")


def resolve_moai_identity(
    *,
    event: dict[str, Any],
    subject_person_id: str | None = None,
    provider_person_id: str | None = None,
    encounter_id: str | None = None,
    config: MoaiWriterConfig | None = None,
    fetch_rows: FetchRows | None = None,
) -> MoaiIdentityResolution:
    provided_encounter_id = _clean(encounter_id)
    resolution = MoaiIdentityResolution(
        organization_id=_clean(event.get("owner_org_id")),
        subject_person_id=_clean(subject_person_id),
        provider_person_id=_clean(provider_person_id) or _clean(event.get("owner_provider_person_id")),
        encounter_id=provided_encounter_id or _clean(event.get("physio_session_id")),
        physio_client_id=_clean(event.get("physio_client_id")),
    )

    if resolution.subject_person_id and resolution.organization_id:
        resolution.methods.append("provided_subject_person_id")
    if resolution.provider_person_id:
        resolution.methods.append("provided_provider_person_id")
    if resolution.encounter_id:
        resolution.methods.append("provided_encounter_id")

    if fetch_rows is None:
        config = config or load_moai_writer_config()
        if config is None:
            resolution.warnings.append("moai Supabase credentials are not configured; remote identity lookup skipped")
            resolution.status = "resolved" if resolution.subject_person_id else "unresolved"
            return resolution
        fetch_rows = _make_fetcher(config)

    try:
        if resolution.encounter_id and (
            bool(provided_encounter_id)
            or not resolution.subject_person_id
            or not resolution.organization_id
            or not resolution.provider_person_id
        ):
            _resolve_by_encounter_id(
                encounter_id=resolution.encounter_id,
                fetch_rows=fetch_rows,
                resolution=resolution,
            )

        if not resolution.subject_person_id and resolution.physio_client_id:
            _resolve_client_by_org_client_id(
                physio_client_id=resolution.physio_client_id,
                organization_id=resolution.organization_id,
                fetch_rows=fetch_rows,
                resolution=resolution,
            )

        if resolution.subject_person_id and not resolution.physio_client_id:
            _resolve_client_by_person_id(
                subject_person_id=resolution.subject_person_id,
                organization_id=resolution.organization_id,
                fetch_rows=fetch_rows,
                resolution=resolution,
            )

        if not resolution.subject_person_id and _clean(event.get("patient_name")):
            _resolve_client_by_name(
                patient_name=str(event.get("patient_name")).strip(),
                organization_id=resolution.organization_id,
                fetch_rows=fetch_rows,
                resolution=resolution,
            )
    except requests.HTTPError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        resolution.warnings.append(f"remote identity lookup failed: {detail}")
    except Exception as exc:
        resolution.warnings.append(f"remote identity lookup failed: {exc}")

    resolution.status = "resolved" if resolution.subject_person_id else "unresolved"
    return resolution
