from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

import requests


_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
_IKAKO_HOST_RE = re.compile(r"(^|//)(?:www\.)?ikako\.vip(?=/|$)", re.I)
_KAKOBUY_HOST_RE = re.compile(r"(^|//)(?:www\.)?kakobuy\.com(?=/|$)", re.I)


def _normalize_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (h or "").strip().lower())


_KEY_SYNONYMS: dict[str, set[str]] = {
    "title": {"title", "name", "product", "productname", "item", "itemname"},
    "price": {"price", "cost", "usd", "priceusd", "amount"},
    "img": {"img", "image", "imageurl", "photo", "photourl", "picture", "pic", "picurl"},
    "kakobuy": {"kakobuy", "ikako", "ikakolink", "link", "url", "producturl"},
    "category": {"category", "cat", "type", "group"},
}


def _map_headers(headers: list[str]) -> dict[str, int]:
    norm = [_normalize_header(h) for h in headers]
    idx: dict[str, int] = {}
    for key, syns in _KEY_SYNONYMS.items():
        for i, h in enumerate(norm):
            if h in syns:
                idx[key] = i
                break
    return idx


def _sheet_id_from_input(sheet: str) -> str:
    s = (sheet or "").strip()
    if not s:
        raise ValueError("Missing sheet URL/ID.")
    if "docs.google.com" in s:
        m = _SHEET_ID_RE.search(s)
        if not m:
            raise ValueError("Could not extract spreadsheet ID from URL.")
        return m.group(1)
    return s


def _sheet_name_from_url(sheet_url: str) -> str | None:
    try:
        parsed = urlparse(sheet_url)
        qs = parse_qs(parsed.query)
        # Sometimes `.../edit?gid=0` only; name not present.
        # If URL includes `.../edit#gid=0` there is no query param either.
        sheet = qs.get("sheet", [None])[0]
        if sheet:
            return str(sheet)
    except Exception:
        pass
    return None


def _public_csv_url(sheet_id: str, sheet_name: str | None) -> str:
    # gviz export is the most tolerant in practice.
    base = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
    if sheet_name:
        return base + f"&sheet={quote(sheet_name)}"
    return base


def _fetch_public_rows(sheet_id: str, sheet_name: str | None, timeout_s: int = 25) -> list[list[str]]:
    url = _public_csv_url(sheet_id, sheet_name)
    resp = requests.get(url, timeout=timeout_s)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Public CSV export failed ({resp.status_code}). "
            f"If this is a private sheet, use --auth service_account."
        )

    # Some sheets return UTF-8 with BOM.
    text = resp.content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    return [[c.strip() for c in row] for row in reader]


def _resolve_redirect(url: str, *, timeout_s: int = 20) -> str:
    """
    Follow redirects and return the final URL (best-effort).
    """
    u = (url or "").strip()
    if not u:
        return ""
    try:
        # GET is more reliable than HEAD for some shorteners.
        r = requests.get(u, allow_redirects=True, timeout=timeout_s)
        return str(r.url or u)
    except Exception:
        return u


def _set_query_param(url: str, key: str, value: str) -> str:
    try:
        p = urlparse(url)
        qs = parse_qs(p.query, keep_blank_values=True)
        qs[key] = [value]
        new_query = urlencode(qs, doseq=True)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, new_query, p.fragment))
    except Exception:
        return url


def normalize_kakobuy_link(url: str, *, affcode: str) -> str:
    """
    - If `url` is an ikako.vip short link, resolve redirect to kakobuy.com.
    - If `url` is kakobuy.com, force `affcode=<affcode>` in the query string.
    """
    u = (url or "").strip()
    if not u:
        return ""

    final = u
    if _IKAKO_HOST_RE.search(u):
        final = _resolve_redirect(u)

    if _KAKOBUY_HOST_RE.search(final) and affcode:
        final = _set_query_param(final, "affcode", affcode)

    return final


@dataclass(frozen=True)
class ServiceAccountConfig:
    credentials_path: str


def _fetch_private_rows_service_account(
    *,
    sheet_id: str,
    sheet_name: str | None,
    credentials_path: str,
) -> list[list[str]]:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

    meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = meta.get("sheets") or []
    if not sheets:
        raise RuntimeError("Spreadsheet has no tabs/sheets.")

    if sheet_name:
        target_title = sheet_name
    else:
        # default to first visible sheet
        target_title = (sheets[0].get("properties") or {}).get("title") or "Sheet1"

    # `range=SheetName` returns the used range on that sheet.
    values = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=target_title)
        .execute()
        .get("values")
        or []
    )

    # Normalize to a rectangular-ish list of strings (Google API returns ragged rows)
    out: list[list[str]] = []
    for row in values:
        if not isinstance(row, list):
            continue
        out.append([str(c).strip() for c in row])
    return out


def _fetch_rows_api_key(
    *,
    sheet_id: str,
    sheet_name: str | None,
    api_key: str,
) -> list[list[dict[str, str]]]:
    """
    Returns rows as list[list[cell]] where cell is { "text": str, "hyperlink": str }.
    Uses Sheets API with a developer API key (works for public sheets and preserves hyperlinks).
    """
    from googleapiclient.discovery import build

    svc = build("sheets", "v4", developerKey=api_key, cache_discovery=False)
    meta = (
        svc.spreadsheets()
        .get(spreadsheetId=sheet_id, includeGridData=True)
        .execute()
    )
    sheets = meta.get("sheets") or []
    if not sheets:
        raise RuntimeError("Spreadsheet has no tabs/sheets.")

    if sheet_name:
        target = None
        for s in sheets:
            title = (s.get("properties") or {}).get("title")
            if title == sheet_name:
                target = s
                break
        if target is None:
            raise RuntimeError(f"Sheet tab not found: {sheet_name}")
    else:
        target = sheets[0]

    grid = (target.get("data") or [])
    if not grid:
        return []
    row_data = (grid[0].get("rowData") or [])

    def cell_text(c: dict[str, Any]) -> str:
        ev = c.get("effectiveValue") or {}
        if "stringValue" in ev:
            return str(ev.get("stringValue") or "").strip()
        if "numberValue" in ev:
            # preserve as shown-ish
            v = ev.get("numberValue")
            return "" if v is None else str(v)
        if "boolValue" in ev:
            return "true" if ev.get("boolValue") else "false"
        # fall back to formatted value
        fv = c.get("formattedValue")
        return str(fv).strip() if fv is not None else ""

    def cell_link(c: dict[str, Any]) -> str:
        hl = c.get("hyperlink")
        if hl:
            return str(hl).strip()
        # Sometimes hyperlink is stored in textFormatRuns (partial rich text)
        runs = c.get("textFormatRuns") or []
        for r in runs:
            fmt = (r.get("format") or {})
            link = (fmt.get("link") or {}).get("uri")
            if link:
                return str(link).strip()
        return ""

    out: list[list[dict[str, str]]] = []
    for r in row_data:
        vals = r.get("values") or []
        out.append([{"text": cell_text(c), "hyperlink": cell_link(c)} for c in vals])
    return out


def _fetch_rows_oauth(
    *,
    sheet_id: str,
    sheet_name: str | None,
    oauth_client_secret_path: str,
    token_path: str,
) -> list[list[dict[str, str]]]:
    """
    Returns rows as list[list[cell]] where cell is { "text": str, "hyperlink": str }.
    Uses OAuth user login (works for private sheets you can view).
    """
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds: Credentials | None = None

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, scopes=scopes)
        except Exception:
            creds = None

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(oauth_client_secret_path, scopes=scopes)
        creds = flow.run_local_server(port=0)
        os.makedirs(os.path.dirname(os.path.abspath(token_path)) or ".", exist_ok=True)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    meta = (
        svc.spreadsheets()
        .get(spreadsheetId=sheet_id, includeGridData=True)
        .execute()
    )
    sheets = meta.get("sheets") or []
    if not sheets:
        raise RuntimeError("Spreadsheet has no tabs/sheets.")

    if sheet_name:
        target = None
        for s in sheets:
            title = (s.get("properties") or {}).get("title")
            if title == sheet_name:
                target = s
                break
        if target is None:
            raise RuntimeError(f"Sheet tab not found: {sheet_name}")
    else:
        target = sheets[0]

    grid = (target.get("data") or [])
    if not grid:
        return []
    row_data = (grid[0].get("rowData") or [])

    def cell_text(c: dict[str, Any]) -> str:
        ev = c.get("effectiveValue") or {}
        if "stringValue" in ev:
            return str(ev.get("stringValue") or "").strip()
        if "numberValue" in ev:
            v = ev.get("numberValue")
            return "" if v is None else str(v)
        if "boolValue" in ev:
            return "true" if ev.get("boolValue") else "false"
        fv = c.get("formattedValue")
        return str(fv).strip() if fv is not None else ""

    def cell_link(c: dict[str, Any]) -> str:
        hl = c.get("hyperlink")
        if hl:
            return str(hl).strip()
        runs = c.get("textFormatRuns") or []
        for r in runs:
            fmt = (r.get("format") or {})
            link = (fmt.get("link") or {}).get("uri")
            if link:
                return str(link).strip()
        return ""

    out: list[list[dict[str, str]]] = []
    for r in row_data:
        vals = r.get("values") or []
        out.append([{"text": cell_text(c), "hyperlink": cell_link(c)} for c in vals])
    return out


def _rows_to_items(
    rows: list[list[str]],
    *,
    affcode: str | None,
    resolve_affiliate_links: bool,
) -> list[dict[str, str]]:
    if not rows:
        return []
    headers = rows[0]
    idx = _map_headers(headers)

    items: list[dict[str, str]] = []
    for row in rows[1:]:
        if not any((c or "").strip() for c in row):
            continue

        def get(key: str) -> str:
            i = idx.get(key)
            if i is None:
                return ""
            if i >= len(row):
                return ""
            return (row[i] or "").strip()

        item = {
            "title": get("title"),
            "price": get("price"),
            "img": get("img"),
            "kakobuy": get("kakobuy"),
            "category": get("category"),
        }

        if resolve_affiliate_links and affcode:
            item["kakobuy"] = normalize_kakobuy_link(item["kakobuy"], affcode=affcode)

        # Drop completely empty rows after mapping
        if not any(v for v in item.values()):
            continue

        items.append(item)

    return items


def _normalize_header_from_cell(cell: dict[str, str]) -> str:
    return _normalize_header(cell.get("text", ""))


def _grid_to_items(
    grid_rows: list[list[dict[str, str]]],
    *,
    affcode: str | None,
    resolve_affiliate_links: bool,
) -> list[dict[str, str]]:
    """
    Like _rows_to_items, but for Sheets API grid data where hyperlinks are preserved.
    Handles repeated column groups (PRODUCT/KAKOBUY LINK/PRICE/IMAGE) by extracting all groups per row.
    """
    if not grid_rows:
        return []

    header = grid_rows[0]
    norm_headers = [_normalize_header_from_cell(c) for c in header]

    # Find repeated groups by locating "product" columns.
    groups: list[dict[str, int]] = []
    for i, h in enumerate(norm_headers):
        if h != "product":
            continue
        # Look ahead in the next ~6 cells for kakobuy link / price / image
        window = norm_headers[i : i + 8]
        g: dict[str, int] = {"title": i}
        for j, wh in enumerate(window):
            idx = i + j
            if wh in {"kakobuylink", "kakobuylink", "kakobuy", "link", "url"} and "kakobuy" not in g:
                g["kakobuy"] = idx
            if wh in {"price"} and "price" not in g:
                g["price"] = idx
            if wh in {"image", "img", "imageurl", "photourl", "photo", "pic"} and "img" not in g:
                g["img"] = idx
        if "kakobuy" in g or "price" in g or "img" in g:
            groups.append(g)

    # Fallback: try the simple header mapping if no groups found.
    if not groups:
        headers = [c.get("text", "") for c in header]
        idx = _map_headers(headers)
        groups = [idx]

    items: list[dict[str, str]] = []

    for row in grid_rows[1:]:
        for g in groups:
            def get_text(key: str) -> str:
                i = g.get(key)
                if i is None or i >= len(row):
                    return ""
                return (row[i].get("text") or "").strip()

            def get_link_or_text(key: str) -> str:
                i = g.get(key)
                if i is None or i >= len(row):
                    return ""
                c = row[i]
                return (c.get("hyperlink") or c.get("text") or "").strip()

            title = get_text("title") or get_text("product") or get_text("name")
            price = get_text("price")
            img = get_link_or_text("img")
            kakobuy = get_link_or_text("kakobuy")

            item = {
                "title": title,
                "price": price,
                "img": img,
                "kakobuy": kakobuy,
                "category": "",
            }

            if resolve_affiliate_links and affcode:
                item["kakobuy"] = normalize_kakobuy_link(item["kakobuy"], affcode=affcode)

            if not any(v for v in item.values()):
                continue

            # Skip obvious non-item rows
            if item["title"].strip().lower() in {"product"}:
                continue

            # If the "kakobuy" cell is literal "LINK" but had no hyperlink, treat as empty
            if item["kakobuy"].strip().lower() == "link":
                item["kakobuy"] = ""

            # If title is missing but there is a link/image/price, still allow? Usually no.
            if not item["title"] and not item["kakobuy"] and not item["img"] and not item["price"]:
                continue

            items.append(item)

    # Drop rows with no title (usually filler)
    items = [it for it in items if it.get("title")]
    return items


def scrape_sheet(
    *,
    sheet: str,
    sheet_name: str | None,
    auth: str,
    credentials_path: str | None,
    api_key: str | None,
    oauth_client_secret_path: str | None,
    oauth_token_path: str | None,
    affcode: str | None,
    resolve_affiliate_links: bool,
) -> list[dict[str, str]]:
    sheet_id = _sheet_id_from_input(sheet)

    if sheet_name is None and "docs.google.com" in sheet:
        sheet_name = _sheet_name_from_url(sheet)

    if auth == "public":
        rows = _fetch_public_rows(sheet_id, sheet_name)
        return _rows_to_items(rows, affcode=affcode, resolve_affiliate_links=resolve_affiliate_links)

    if auth == "api_key":
        if not api_key:
            raise ValueError("--api-key is required for --auth api_key")
        grid = _fetch_rows_api_key(sheet_id=sheet_id, sheet_name=sheet_name, api_key=api_key)
        return _grid_to_items(grid, affcode=affcode, resolve_affiliate_links=resolve_affiliate_links)

    if auth == "oauth":
        if not oauth_client_secret_path:
            raise ValueError("--oauth-client-secret is required for --auth oauth")
        token_path = oauth_token_path or os.path.join(os.path.dirname(__file__), "token.json")
        grid = _fetch_rows_oauth(
            sheet_id=sheet_id,
            sheet_name=sheet_name,
            oauth_client_secret_path=oauth_client_secret_path,
            token_path=token_path,
        )
        return _grid_to_items(grid, affcode=affcode, resolve_affiliate_links=resolve_affiliate_links)

    if auth == "service_account":
        if not credentials_path:
            raise ValueError("--credentials is required for --auth service_account")
        rows = _fetch_private_rows_service_account(
            sheet_id=sheet_id,
            sheet_name=sheet_name,
            credentials_path=credentials_path,
        )
        return _rows_to_items(rows, affcode=affcode, resolve_affiliate_links=resolve_affiliate_links)

    raise ValueError(f"Unknown auth mode: {auth}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Scrape a Google Sheet (public or private) into JSON items with keys: "
            "title, price, img, kakobuy, category."
        )
    )
    p.add_argument("sheet", help="Google Sheets URL or spreadsheet ID.")
    p.add_argument(
        "--sheet-name",
        dest="sheet_name",
        default=None,
        help="Tab name to scrape (defaults to first sheet).",
    )
    p.add_argument(
        "--auth",
        choices=["public", "api_key", "oauth", "service_account"],
        default="public",
        help="How to access the sheet. Use service_account for private sheets.",
    )
    p.add_argument(
        "--api-key",
        default=None,
        help="Google API key (required for --auth api_key). Use this to preserve hyperlinks on public sheets.",
    )
    p.add_argument(
        "--credentials",
        default=None,
        help="Path to a Google service-account JSON key (required for --auth service_account).",
    )
    p.add_argument(
        "--oauth-client-secret",
        default=None,
        help="Path to Google OAuth client secret JSON (required for --auth oauth).",
    )
    p.add_argument(
        "--oauth-token",
        default=None,
        help="Path to store OAuth token JSON (default: gsheet_scraper/token.json).",
    )
    p.add_argument(
        "--out",
        default="",
        help="Write JSON output to this file (default: stdout).",
    )
    p.add_argument(
        "--affcode",
        default="7hjf5",
        help="Affiliate code to enforce on kakobuy.com links (default: 7hjf5).",
    )
    p.add_argument(
        "--no-affcode",
        action="store_true",
        help="Do not resolve ikako.vip redirects or set affcode.",
    )

    args = p.parse_args(argv)

    items = scrape_sheet(
        sheet=args.sheet,
        sheet_name=args.sheet_name,
        auth=args.auth,
        credentials_path=args.credentials,
        api_key=args.api_key,
        oauth_client_secret_path=args.oauth_client_secret,
        oauth_token_path=args.oauth_token,
        affcode=(None if args.no_affcode else args.affcode),
        resolve_affiliate_links=(not args.no_affcode),
    )

    payload = {"items": items}
    text = json.dumps(payload, indent=2, ensure_ascii=False)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")
    else:
        sys.stdout.write(text + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
