"""Smart contact import — accept whatever file a business exported from its
old system and figure out the columns automatically.

Nearly every CRM / field-service / booking tool exports **CSV**; phone and
Google contacts export **vCard (.vcf)**. The catch is the column headers are
all different: "First Name" vs "Given Name" vs "Customer", "Phone" vs "Mobile
Phone" vs "Phone 1 - Value". The old importer required Adapix's exact header
names, so a real export imported as blank rows. This maps any of them to our
canonical fields, splits a single full-name column, and pulls the first
non-empty value when a source has several phone/email columns (Google).

Public API:
  detect_mapping(columns)  -> {canonical_field: source_header}   (for preview)
  rows_from_upload(bytes, filename) -> (raw_rows, columns, kind)  ('csv'|'vcard')
  normalize_rows(raw_rows) -> list[dict] of canonical contact fields
"""
from __future__ import annotations

import csv as _csv
import io
import re

# Canonical field -> the normalized header aliases that map to it. Order in
# the phone/email lists is priority (mobile beats landline).
_ALIASES: dict[str, list[str]] = {
    "first_name": ["firstname", "first", "fname", "givenname", "given", "forename"],
    "last_name": ["lastname", "last", "lname", "surname", "familyname", "family"],
    "full_name": ["name", "fullname", "customername", "clientname", "contactname",
                  "customer", "client", "contact", "displayname", "patientname",
                  "companyname", "businessname"],
    "phone": ["mobilephone", "mobile", "mobilenumber", "cellphone", "cell",
              "cellnumber", "phone", "phonenumber", "primaryphone", "phone1value",
              "phone2value", "phone3value", "telephone", "tel", "contactnumber",
              "homephone", "workphone", "phone1", "phone2"],
    "email": ["emailaddress", "email", "email1value", "email2value", "primaryemail",
              "workemail", "personalemail", "emailaddress1", "email1", "email2"],
    "notes": ["notes", "note", "comments", "comment", "description", "memo", "remarks"],
    "service_type": ["servicetype", "service", "jobtype", "job", "treatmenttype",
                     "treatment", "category", "servicerequested", "tags", "tag",
                     "leadsource", "type"],
    "deal_value": ["dealvalue", "jobvalue", "quotevalue", "estimatevalue", "amount",
                   "value", "quote", "estimate", "total", "totalvalue", "price",
                   "invoiceamount", "invoicetotal", "amountdue"],
    "external_id": ["externalid", "customerid", "clientid", "contactid", "recordid",
                    "referenceid", "id", "accountnumber", "customernumber"],
}


def _norm(header: str) -> str:
    """Normalize a header for matching: lowercase, drop everything that isn't
    a letter or digit. "Phone 1 - Value" -> "phone1value", "E-mail" -> "email"."""
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


def detect_mapping(columns: list[str]) -> dict[str, str | list[str]]:
    """Map source headers to canonical fields. phone/email return a LIST of
    source columns (a file can have several — Google's Phone 1/2/3), in
    priority order; the others return a single best source header."""
    norm_to_orig: dict[str, str] = {}
    for c in columns:
        n = _norm(c)
        if n and n not in norm_to_orig:
            norm_to_orig[n] = c

    mapping: dict[str, str | list[str]] = {}
    for field, aliases in _ALIASES.items():
        if field in ("phone", "email"):
            hits = [norm_to_orig[a] for a in aliases if a in norm_to_orig]
            # also catch anything that merely CONTAINS the alias root (e.g.
            # "customermobilephone") without double-adding — but EXCLUDE the
            # sibling "…Type"/"…Label" columns Google/Outlook emit (they hold
            # "Mobile"/"Home", not a value), or we'd import the label as a phone.
            root = "phone" if field == "phone" else "email"
            for n, orig in norm_to_orig.items():
                if orig in hits:
                    continue
                if root in n and not (n.endswith("type") or n.endswith("label")):
                    hits.append(orig)
            if hits:
                mapping[field] = hits
        else:
            for a in aliases:
                if a in norm_to_orig:
                    mapping[field] = norm_to_orig[a]
                    break
    return mapping


def _first_nonempty(row: dict, sources: list[str]) -> str:
    for src in sources:
        v = (row.get(src) or "").strip()
        if v:
            return v
    return ""


def _split_full_name(full: str) -> tuple[str, str]:
    full = (full or "").strip()
    if not full:
        return "", ""
    if "," in full:  # "Last, First" convention
        last, _, first = full.partition(",")
        return first.strip(), last.strip()
    parts = full.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def normalize_rows(raw_rows: list[dict], columns: list[str] | None = None) -> list[dict]:
    """Turn raw CSV/vCard rows into canonical contact dicts using the detected
    column mapping. Rows with neither a phone nor an email nor a name are dropped."""
    cols = columns if columns is not None else (list(raw_rows[0].keys()) if raw_rows else [])
    m = detect_mapping(cols)
    out: list[dict] = []
    for row in raw_rows:
        first = (row.get(m["first_name"]) if isinstance(m.get("first_name"), str) else "") or ""
        last = (row.get(m["last_name"]) if isinstance(m.get("last_name"), str) else "") or ""
        first, last = first.strip(), last.strip()
        if not first and not last and isinstance(m.get("full_name"), str):
            first, last = _split_full_name(row.get(m["full_name"], ""))

        phone = _first_nonempty(row, m["phone"]) if isinstance(m.get("phone"), list) else ""
        email = _first_nonempty(row, m["email"]) if isinstance(m.get("email"), list) else ""

        def one(field):
            src = m.get(field)
            return (row.get(src) or "").strip() if isinstance(src, str) else ""

        rec = {
            "first_name": first,
            "last_name": last,
            "phone": phone,
            "email": email,
            "notes": one("notes"),
            "service_type": one("service_type"),
            "deal_value": one("deal_value"),
            "external_id": one("external_id"),
        }
        # Skip rows that are effectively empty (headers, spacer rows).
        if not (rec["first_name"] or rec["last_name"] or rec["phone"] or rec["email"]):
            continue
        out.append(rec)
    return out


def parse_csv(content: bytes) -> tuple[list[dict], list[str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1", errors="replace")
    reader = _csv.DictReader(io.StringIO(text))
    rows = [r for r in reader]
    return rows, list(reader.fieldnames or [])


def parse_vcard(content: bytes) -> tuple[list[dict], list[str]]:
    """Minimal vCard parser (Google Contacts / iPhone / Android export). Pulls
    FN (full name), N (structured), TEL, EMAIL, NOTE, ORG. Returns rows shaped
    with the same header names our alias table already understands."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1", errors="replace")
    rows: list[dict] = []
    cur: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.upper() == "BEGIN:VCARD":
            cur = {"full_name": "", "first_name": "", "last_name": "",
                   "phone": "", "email": "", "notes": ""}
        elif line.upper() == "END:VCARD":
            if cur is not None:
                rows.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, _, val = line.partition(":")
            key = key.split(";")[0].upper()  # strip TYPE params
            val = val.strip()
            if key == "FN" and not cur["full_name"]:
                cur["full_name"] = val
            elif key == "N" and not (cur["first_name"] or cur["last_name"]):
                # N: Last;First;Middle;Prefix;Suffix
                p = val.split(";")
                cur["last_name"] = (p[0] if len(p) > 0 else "").strip()
                cur["first_name"] = (p[1] if len(p) > 1 else "").strip()
            elif key == "TEL" and not cur["phone"]:
                cur["phone"] = val
            elif key == "EMAIL" and not cur["email"]:
                cur["email"] = val
            elif key == "NOTE" and not cur["notes"]:
                cur["notes"] = val
    cols = ["full_name", "first_name", "last_name", "phone", "email", "notes"]
    return rows, cols


def parse_xlsx(content: bytes) -> tuple[list[dict], list[str]]:
    """Parse an Excel .xlsx export (FieldEdge is Excel-only; Dynamics 365 and
    Vagaro export Excel by default). Uses the first sheet, first non-empty row
    as headers. Requires openpyxl (in requirements)."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = None
    data: list[dict] = []
    for raw in rows_iter:
        cells = ["" if c is None else str(c).strip() for c in raw]
        if header is None:
            # First row that actually has some text is the header (skip a
            # leading blank/logo row that pushes real headers down).
            if any(cells):
                header = [h or f"col{i}" for i, h in enumerate(cells)]
            continue
        if not any(cells):
            continue
        data.append({header[i]: (cells[i] if i < len(cells) else "") for i in range(len(header))})
    wb.close()
    return data, (header or [])


def rows_from_upload(content: bytes, filename: str) -> tuple[list[dict], list[str], str]:
    """Dispatch on file type. Returns (raw_rows, columns, kind)."""
    name = (filename or "").lower()
    if name.endswith(".vcf") or content[:15].upper().startswith(b"BEGIN:VCARD"):
        rows, cols = parse_vcard(content)
        return rows, cols, "vcard"
    # .xlsx is a zip; sniff the PK header too in case the extension is wrong.
    if name.endswith(".xlsx") or name.endswith(".xls") or content[:2] == b"PK":
        try:
            rows, cols = parse_xlsx(content)
            return rows, cols, "xlsx"
        except Exception:
            pass  # fall through to CSV attempt
    rows, cols = parse_csv(content)
    return rows, cols, "csv"
