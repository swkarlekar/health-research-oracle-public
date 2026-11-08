#!/usr/bin/env python3
"""Build a text-only, provenance-preserving corpus from an acquisition manifest.

No OCR, transcription, model calls, legal-status inference, or generated summaries.
All quotation offsets are Python Unicode code-point offsets into canonical extracted
text, not PDF byte offsets or JavaScript UTF-16 offsets. Raw source files stay intact.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import re
import sqlite3
import signal
import threading
import time
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

SCHEMA_VERSION = "1.0"
PROCESSOR_VERSION = "1.2.1"
ACQUIRED_STATUSES = {"downloaded", "web_extracted"}
SECTION_SEPARATOR = "\n\n"
REJECT_TAGS = {"script", "style", "noscript", "template", "form", "button", "input", "select", "svg", "iframe", "nav", "aside"}
BLOCK_TAGS = {"p", "div", "section", "article", "main", "header", "footer", "ul", "ol", "li", "blockquote", "pre", "table", "h1", "h2", "h3", "h4", "h5", "h6", "dl", "dt", "dd", "figure", "figcaption", "hr", "address", "text", "notes"}
DATE_PATTERN = re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b", re.I)
RELATION_PATTERN = re.compile(r"\b(?:supersed(?:es|ed|ing)|replac(?:es|ed|ing)|amend(?:s|ed|ment|ments)?|rescind(?:s|ed)?|effective\s+(?:date|on|as\s+of)|expires?\s+(?:on|as\s+of))\b", re.I)
ANTIBOT_TITLE = re.compile(r"(?:just a moment|access denied|request rejected|403 forbidden|attention required|verify (?:you are|you're) human|security verification|captcha)", re.I)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def stable_id(value: str) -> str:
    return "src_" + sha256_text(value)[:16]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def jsonl_write(path: Path, rows: Iterable[dict]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(canonical_json(row) + "\n")
    temp.replace(path)


@dataclass
class ExtractedSection:
    text: str
    heading_path: list[str] = field(default_factory=list)
    page: int | None = None
    slide: int | None = None
    kind: str = "text"
    source_locator: str | None = None
    elements: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)


@dataclass
class Extraction:
    sections: list[ExtractedSection]
    method: str
    title: str | None = None
    metadata: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def text_inline(node) -> str:
    pieces = [node.text or ""]
    for child in node:
        tag = child.tag.lower() if isinstance(child.tag, str) else ""
        if tag in REJECT_TAGS:
            pieces.append(child.tail or "")
            continue
        pieces.append("\n" if tag == "br" else text_inline(child))
        pieces.append(child.tail or "")
    return "".join(pieces)


def normalize_html_text(text: str, preserve: bool = False) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if preserve:
        return text.strip("\n")
    # HTML collapses ordinary whitespace in rendered text. Nonbreaking spaces are
    # retained so quote text is never silently rewritten to a different character.
    return re.sub(r"[ \t\r\n\f\v]+", " ", text).strip(" \t\n")


def table_representation(rows: list[list[str]], cells: list[list[dict]] | None = None) -> tuple[str, dict]:
    """Keep every source cell and explicit spans; never infer a semantic relation."""
    pieces, row_spans, header_cells, occupied = [], [], [], {}
    offset = 0
    for row_index, values in enumerate(rows):
        if pieces:
            offset += 1
        start = offset
        row_text = "\t".join(values)
        pieces.append(row_text)
        source_cells = cells[row_index] if cells is not None else [{} for _ in values]
        cell_records = []
        col_index, cell_offset = 0, start
        for value, original in zip(values, source_cells):
            while (row_index, col_index) in occupied:
                col_index += 1
            try:
                rowspan = max(1, int(original.get("rowspan", 1)))
                colspan = max(1, int(original.get("colspan", 1)))
            except (TypeError, ValueError):
                rowspan = colspan = 1
            cell = {**original, "text": value, "row_index": row_index,
                    "column_start": col_index, "column_end": col_index + colspan,
                    "rowspan": rowspan, "colspan": colspan,
                    "start_char": cell_offset, "end_char": cell_offset + len(value)}
            cell_records.append(cell)
            if original.get("tag") == "th":
                header_cells.append(cell)
            for rr in range(row_index, row_index + rowspan):
                for cc in range(col_index, col_index + colspan):
                    occupied[(rr, cc)] = (row_index, col_index)
            col_index += colspan
            cell_offset += len(value) + 1
        offset += len(row_text)
        row_spans.append({"row_index": row_index, "start_char": start,
                          "end_char": offset, "cells": cell_records})
    return "\n".join(pieces), {"rows": rows, "row_spans": row_spans,
        "header_cells": header_cells,
        "headers": [rows[i] for i, row in enumerate(cells or []) if any(c.get("tag") == "th" for c in row)],
        "column_count": max((cc + 1 for rr, cc in occupied), default=0),
        "header_binding_policy": "Source header tags/scopes and cell coordinates retained; semantic header assignments not inferred."}


def decode_html_details(data: bytes, content_type: str = "") -> tuple[str, dict]:
    """Preserve valid UTF-8 runs; record every fallback for malformed source bytes."""
    http_match = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", content_type, re.I)
    html_match = re.search(br"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", data[:4096], re.I)
    http_encoding = http_match.group(1) if http_match else None
    html_encoding = html_match.group(1).decode("ascii") if html_match else None
    declared = http_encoding or html_encoding
    metadata = {"declared_http_charset": http_encoding, "declared_html_charset": html_encoding,
        "selected_encoding": None, "encoding_uncertain": False, "declared_charset_decoding_failed": False,
        "fallback_byte_count": 0, "fallback_bytes": [], "fallback_offset_unit": "raw_bytes",
        "valid_utf8_sequences_preserved": True, "bom_removed_from_text": data.startswith(codecs.BOM_UTF8)}
    try:
        text = data.decode("utf-8-sig")
        metadata["selected_encoding"] = "utf-8"
        return text, metadata
    except UnicodeDecodeError as exc:
        metadata["first_invalid_utf8_byte_offset"] = exc.start
    if declared:
        try:
            normalized = codecs.lookup(declared).name
            # Retrying a failed declared UTF-8 decoder adds no information. A
            # successfully declared legacy encoding can be used as declared.
            if normalized not in {"utf-8", "utf-8-sig"}:
                text = data.decode(declared)
                metadata.update({"selected_encoding": normalized, "valid_utf8_sequences_preserved": None})
                return text, metadata
            metadata["declared_charset_decoding_failed"] = True
        except (LookupError, UnicodeDecodeError):
            metadata["declared_charset_decoding_failed"] = True
    # Some publisher pages mix valid UTF-8 with isolated legacy punctuation.
    # Whole-file CP1252 decoding would corrupt valid UTF-8 and can itself fail
    # on valid UTF-8 continuation bytes. Decode only the escaped invalid bytes.
    escaped = data.decode("utf-8", errors="surrogateescape")
    if escaped.encode("utf-8", errors="surrogateescape") != data:
        raise ValueError("Lossless HTML byte staging failed")
    pieces, byte_offset, used_latin1 = [], 0, False
    for char in escaped:
        codepoint = ord(char)
        if 0xDC80 <= codepoint <= 0xDCFF:
            value = codepoint - 0xDC00
            try:
                decoded = bytes([value]).decode("windows-1252")
                mapping = "windows-1252"
            except UnicodeDecodeError:
                # Preserve an undefined legacy control byte, never silently
                # replace it with a question mark or the replacement character.
                decoded = bytes([value]).decode("latin-1")
                mapping, used_latin1 = "latin-1", True
            pieces.append(decoded)
            metadata["fallback_bytes"].append({"byte_offset": byte_offset, "byte_hex": f"{value:02x}",
                "decoded_codepoint": f"U+{ord(decoded):04X}", "mapping": mapping})
            byte_offset += 1
        else:
            pieces.append(char)
            byte_offset += len(char.encode("utf-8"))
    text = "".join(pieces)
    if metadata["bom_removed_from_text"]:
        text = text[1:]
    metadata.update({"selected_encoding": "utf-8_with_windows-1252_byte_fallback" + ("_and_latin-1_controls" if used_latin1 else ""),
        "encoding_uncertain": True, "fallback_byte_count": len(metadata["fallback_bytes"]),
        "lossless_raw_byte_staging_verified": True})
    return text, metadata


def decode_html(data: bytes, content_type: str = "") -> tuple[str, str]:
    """Compatibility wrapper; extraction uses the complete decoder provenance."""
    text, metadata = decode_html_details(data, content_type)
    return text, metadata["selected_encoding"]


def extract_nymc_program_materials(decoded: str, source_url: str | None) -> Extraction | None:
    """Read this exact public page's embedded JSON; never execute JavaScript.

    The downloaded page has a Loading DOM while its three main text components
    are present in window.INITIAL_STATE. Other pages and sidebar components in
    that object are deliberately outside this source's extracted content.
    """
    url = urlsplit(source_url or "")
    if url.hostname not in {"www.nymedicaidchoice.com", "nymedicaidchoice.com"} or url.path.rstrip("/") not in {"/program-materials", "/en/program-materials"}:
        return None
    marker = re.search(r"window\.INITIAL_STATE\s*=\s*", decoded)
    if not marker:
        return None
    state, _ = json.JSONDecoder().raw_decode(decoded[marker.end():].lstrip())
    page = state["aemPages"]["program-materials"]
    if page.get("authenticationRequired"):
        raise ValueError("Embedded page declares authentication required; do not use it as public source content")
    main = page[":items"]["root"][":items"]["main-par"]
    items, order = main[":items"], main[":itemsOrder"]
    text_keys = {key for key, item in items.items() if item.get(":type") == "maximus-base/components/content/text"}
    if not text_keys or not text_keys.issubset(set(order)):
        raise ValueError("Embedded page text components have an incomplete declared order")
    sections, components, links, flags = [], [], [], []
    from lxml import html
    for key in order:
        if key not in text_keys:
            continue
        component = items[key]
        source_html = component["text"]
        if not isinstance(source_html, str):
            raise ValueError("Embedded text component is not a string")
        pointer = "/aemPages/program-materials/:items/root/:items/main-par/:items/" + key + "/text"
        result = extract_html(("<html><body><main>" + source_html + "</main></body></html>").encode("utf-8"))
        for section in result.sections:
            section.source_locator = "window.INITIAL_STATE#" + pointer + "::" + (section.source_locator or "")
            for element in section.elements:
                element["source_locator"] = "window.INITIAL_STATE#" + pointer + "::" + element.get("source_locator", "")
            for table in section.tables:
                table["source_locator"] = "window.INITIAL_STATE#" + pointer + "::" + table.get("source_locator", "")
            sections.append(section)
        fragment = html.fragment_fromstring(source_html, create_parent="div")
        for link in fragment.xpath('.//a[@href]'):
            links.append({"json_pointer": pointer, "href_exact": link.get("href"), "label": normalize_html_text(link.text_content()), "downloaded_as_child_resource": False})
        components.append({"json_pointer": pointer, "source_html_sha256": sha256_text(source_html), "source_html_characters": len(source_html)})
        flags.extend(result.flags)
    return Extraction(sections, "embedded_public_page_json_html_text", title=page.get("title"),
        metadata={"content_profile": "nymc_program_materials_embedded_json", "json_assignment": "window.INITIAL_STATE",
                  "page_key": "program-materials", "page_title": page.get("title"), "page_last_updated_raw": page.get("lastUpdated"),
                  "components": components, "observed_child_links": links, "child_resources_downloaded": False,
                  "excluded_components": [key for key in order if key not in text_keys]},
        flags=sorted(set(flags + ["embedded_page_text_recovered_child_resources_not_downloaded"])),
        notes=["Only the exact requested page's declared main text components were decoded from already downloaded public HTML.",
               "JSON was decoded without executing JavaScript. Other page objects and the sidebar were excluded.",
               "Observed brochure and plan-list links are retained as metadata; their files have not been added to the corpus."])


def extract_html(data: bytes, content_type: str = "", source_url: str | None = None) -> Extraction:
    try:
        from lxml import html
    except ImportError as exc:
        raise RuntimeError("HTML extraction requires lxml; install lxml in the project environment") from exc
    decoded, decoding = decode_html_details(data, content_type)
    embedded = extract_nymc_program_materials(decoded, source_url)
    if embedded is not None:
        embedded.metadata.update(decoded_encoding=decoding["selected_encoding"], decoding=decoding)
        return embedded
    encoding = decoding["selected_encoding"]
    tree = html.fromstring(decoded)
    title_nodes = tree.xpath("//title")
    title = normalize_html_text(text_inline(title_nodes[0])) if title_nodes else None
    revision_markers = [{"text": normalize_html_text(text_inline(node)), "source_locator": tree.getroottree().getpath(node), "interpretation": None}
        for node in tree.xpath('//*[@id="reviseddate"]')]
    meta_dates = [{"name": node.get("name") or node.get("property"), "content": node.get("content"), "interpretation": None}
        for node in tree.xpath('//meta[@content]')
        if re.search(r"(?:date|modified|published|updated)", node.get("name", "") + " " + node.get("property", ""), re.I)]
    # LII places its comparison-interface panels inside the content container.
    # Keep the actual regulation and source notes; retain the update notice as
    # metadata without mixing it into legal evidence text.
    lii_body_nodes = tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," statereg-text ")]')
    lii_notes_nodes = tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," statereg-notes ")]')
    is_lii_regulation = bool(lii_body_nodes or lii_notes_nodes)
    is_lii_uscode = bool(tree.xpath('//*[@id="tab_default_1"]/text'))
    findlaw_content = tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," sidebar-with-breadcrumbs-layout__content ")]') if tree.xpath('//main[contains(concat(" ",normalize-space(@class)," ")," codes-page ")]') else []
    is_findlaw_codes = bool(findlaw_content)
    source_update_notices = [{"text": normalize_html_text(text_inline(node)),
        "source_locator": tree.getroottree().getpath(node), "interpretation": None}
        for node in tree.xpath('//*[@id="tab_point_in-time"]//p')] if is_lii_regulation else []
    # Publisher-specific text containers precede generic roots, avoiding eCFR's
    # full-page TOC/navigation and NY Senate's account/share controls.
    ecfr_parts = tree.xpath('//div[starts-with(@id,"part-") and contains(concat(" ",normalize-space(@class)," ")," part ")]')
    ecfr_subparts = tree.xpath('//div[starts-with(@id,"subpart-") and contains(concat(" ",normalize-space(@class)," ")," subpart ")]')
    ecfr_sections = [node for node in tree.xpath('//div[@id and contains(concat(" ",normalize-space(@class)," ")," section ")]')
        if re.fullmatch(r"\d+[A-Za-z]?\.[\dA-Za-z.-]+", node.get("id", ""))]
    ecfr_level = "part" if ecfr_parts else "subpart" if ecfr_subparts else "section" if ecfr_sections else None
    ecfr_nodes = ecfr_parts or ecfr_subparts or ecfr_sections
    ecfr_roots = ecfr_nodes[:1]
    if len(ecfr_nodes) > 1:
        # Retain every visible regulatory container; never silently choose the
        # first part/section when a page actually contains a broader collection.
        for ancestor in [ecfr_nodes[0], *ecfr_nodes[0].iterancestors()]:
            if all(node is ancestor or ancestor in node.iterancestors() for node in ecfr_nodes):
                ecfr_roots = [ancestor]
                break
    candidates = (findlaw_content
        or ecfr_roots
        or tree.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," nys-openleg-content-container ")]')
        or tree.xpath('//*[@id="content"]')
        or tree.xpath("//main") or tree.xpath("//article") or tree.xpath("//body"))
    root = candidates[0] if candidates else tree
    units: list[dict] = []
    flags: list[str] = []
    if decoding["encoding_uncertain"]:
        flags.append("encoding_uncertain")
    if len(ecfr_nodes) > 1:
        flags.append("multiple_regulatory_parts_require_scope_review" if ecfr_level == "part" else "multiple_regulatory_containers_require_scope_review")
    roottree = tree.getroottree()
    publisher_version_notices = []
    if is_findlaw_codes:
        for node in root.xpath('.//p'):
            value = normalize_html_text(text_inline(node))
            if value.startswith("Current as of ") or value.startswith("FindLaw Codes may not reflect"):
                publisher_version_notices.append({"text": value, "source_locator": roottree.getpath(node), "interpretation": None})
        if publisher_version_notices:
            flags.append("publisher_version_notice_requires_review")
    rule_body_present = any(normalize_html_text(text_inline(node)) for node in lii_body_nodes) if is_lii_regulation else None
    status_observations = []
    if is_lii_regulation and not rule_body_present:
        flags.append("publisher_page_has_no_rule_body")
        for node in tree.xpath('//h1'):
            label = normalize_html_text(text_inline(node))
            match = re.search(r"\b(?:repealed|reserved|renumbered|omitted)\b", label, re.I)
            if match:
                status_observations.append({"text": label, "matched_term": match.group(),
                    "source_locator": roottree.getpath(node), "interpretation": None})
        note_texts = [normalize_html_text(text_inline(node))
            for container in lii_notes_nodes for node in container.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," statereg-note ")]')]
        citation_only = re.compile(r"^N\.?Y\.?\s+Comp\.?\s+Codes\s+R\.?\s*&\s*Regs\.?\s+Tit\.?\s*\d+\s*§{1,2}\s*\d+(?:\.\d+)*[A-Za-z]?(?:\s*(?:to|and|[-–—,])\s*§*\s*\d+(?:\.\d+)*[A-Za-z]?)*\.?$", re.I)
        substantive_notes = [note for note in note_texts if note and not citation_only.fullmatch(note)]
        if status_observations:
            flags.append("publisher_labeled_status_stub_requires_review")
        elif substantive_notes:
            flags.append("publisher_notes_only_no_rule_body_requires_review")
        else:
            flags.append("no_rule_body_or_substantive_note_placeholder")

    def emit(text: str, node, kind: str = "text", **extra):
        value = normalize_html_text(text, preserve=kind == "pre")
        if value:
            units.append({"text": value, "kind": kind, "locator": roottree.getpath(node), **extra})

    def excluded_interface(node):
        classes = set((node.get("class") or "").split())
        if is_lii_regulation and (node.get("id") in {"tab_default_2", "tab_point_in-time"} or "nav-tabs" in classes):
            return True
        if is_lii_uscode and (node.get("id") in {"titleHelp", "prevnext", "tab_default_3"} or "nav-tabs" in classes):
            return True
        if is_findlaw_codes and classes.intersection({"codes-controls", "cite-this-article", "wasThisHelpful"}):
            return True
        return False

    def walk(node):
        tag = node.tag.lower() if isinstance(node.tag, str) else ""
        if not tag or tag in REJECT_TAGS or excluded_interface(node):
            return
        # In a body fallback, remove global site headers/footers. Article/main
        # headers can contain real document headings and therefore remain.
        if root.tag.lower() == "body" and tag in {"header", "footer"} and node.getparent() is root:
            return
        if re.fullmatch(r"h[1-6]", tag):
            emit(text_inline(node), node, "heading", level=int(tag[1]))
            return
        if tag == "table":
            rows = []
            cells_structure = []
            header_rows = []
            for row in node.xpath(".//tr"):
                # Avoid duplicating nested table rows into the parent table.
                ancestors = row.xpath("ancestor::table")
                if ancestors and ancestors[-1] is not node:
                    continue
                cells = row.xpath("./th|./td")
                if not cells:
                    continue
                values = [normalize_html_text(text_inline(cell)) for cell in cells]
                rows.append(values)
                structure = [{"text": value, "tag": cell.tag.lower(), "rowspan": cell.get("rowspan", "1"), "colspan": cell.get("colspan", "1"), "scope": cell.get("scope"), "id": cell.get("id"), "headers_attribute": cell.get("headers")} for value, cell in zip(values, cells)]
                cells_structure.append(structure)
                if any(cell.tag.lower() == "th" for cell in cells):
                    header_rows.append(values)
                if any(cell.get("rowspan", "1") != "1" or cell.get("colspan", "1") != "1" for cell in cells):
                    flags.append("complex_table_spans_require_review")
            if rows:
                table_text, table_data = table_representation(rows, cells_structure)
                emit(table_text, node, "table", table=table_data)
                # Preserve row and column separators after the prose emit path.
                units[-1]["text"] = table_text
            return
        if tag == "pre":
            emit(text_inline(node), node, "pre")
            return
        buffer = node.text or ""
        for child in node:
            child_tag = child.tag.lower() if isinstance(child.tag, str) else ""
            if child_tag in REJECT_TAGS or not child_tag or excluded_interface(child):
                buffer += child.tail or ""
                continue
            if child_tag in BLOCK_TAGS or (is_lii_uscode and child_tag == "span" and "chapeau" in (child.get("class") or "").split()):
                emit(buffer, node, "list_item" if tag == "li" else "text")
                buffer = ""
                walk(child)
                buffer += child.tail or ""
            else:
                buffer += ("\n" if child_tag == "br" else text_inline(child)) + (child.tail or "")
        emit(buffer, node, "list_item" if tag == "li" else "text")

    walk(root)
    sections: list[ExtractedSection] = []
    current: list[dict] = []
    headings: list[tuple[int, str]] = []
    current_headings: list[str] = []

    def flush():
        if not current:
            return
        pieces, elements, tables = [], [], []
        cursor = 0
        for unit in current:
            if pieces:
                cursor += len(SECTION_SEPARATOR)
            start = cursor
            pieces.append(unit["text"])
            cursor += len(unit["text"])
            element = {"kind": unit["kind"], "start_char": start, "end_char": cursor, "source_locator": unit["locator"]}
            elements.append(element)
            if "table" in unit:
                table = {**unit["table"], "start_char": start, "end_char": cursor, "source_locator": unit["locator"]}
                table["row_spans"] = [{**row, "start_char": row["start_char"] + start, "end_char": row["end_char"] + start,
                    "cells": [{**cell, "start_char": cell["start_char"] + start, "end_char": cell["end_char"] + start} for cell in row["cells"]]}
                    for row in table.get("row_spans", [])]
                table["header_cells"] = [{**cell, "start_char": cell["start_char"] + start, "end_char": cell["end_char"] + start} for cell in table.get("header_cells", [])]
                tables.append(table)
        sections.append(ExtractedSection(text=SECTION_SEPARATOR.join(pieces), heading_path=list(current_headings), kind="html_section", source_locator=current[0]["locator"], elements=elements, tables=tables))
        current.clear()

    for unit in units:
        if unit["kind"] == "heading":
            flush()
            level = unit["level"]
            headings[:] = [(rank, label) for rank, label in headings if rank < level]
            headings.append((level, unit["text"]))
            current_headings = [label for _, label in headings]
        current.append(unit)
    flush()
    full = SECTION_SEPARATOR.join(section.text for section in sections)
    if title and ANTIBOT_TITLE.search(title) and len(full) < 12000:
        flags.append("access_or_bot_challenge_page")
    elif len(full) < 3000 and re.search(r"(?:enable javascript and cookies|verify (?:that )?you are human|checking your browser|cf-chl-|captcha)", full[:2000], re.I):
        flags.append("access_or_bot_challenge_page")
    profile = "cornell_uscode" if is_lii_uscode else "findlaw_codes" if is_findlaw_codes else "cornell_state_regulations" if is_lii_regulation else f"ecfr_{ecfr_level}" if ecfr_level else "generic_document"
    return Extraction(sections, "lxml_dom_text_and_tables", title=title, metadata={"decoded_encoding": encoding, "decoding": decoding, "content_profile": profile, "content_root": roottree.getpath(root), "page_revision_markers": revision_markers, "html_date_metadata": meta_dates, "source_update_notices": source_update_notices, "publisher_version_notices": publisher_version_notices, "rule_body_present": rule_body_present, "publisher_status_observations": status_observations}, flags=sorted(set(flags)), notes=["HTML whitespace collapsed in prose; source table cells retained with tab/newline separators.", "Scripts, forms, navigation, and other non-document controls excluded; original HTML retained for audit.", "Publisher block containers, including Cornell USC text/notes containers and statutory chapeaux, are traversed as document blocks rather than flattened inline."])


def find_pdf_tables(page, drawings, timeout_seconds: int = 5):
    """Bound expensive geometric analysis; page text is preserved on timeout."""
    can_alarm = hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()
    if not can_alarm:
        return page.find_tables(paths=drawings)
    def expired(signum, frame):
        raise TimeoutError(f"PDF table geometry exceeded {timeout_seconds}s; source page review required")
    old_handler = signal.signal(signal.SIGALRM, expired)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    began = time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        return page.find_tables(paths=drawings)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, max(0.001, old_timer[0] - (time.monotonic() - began)), old_timer[1])


def extract_pdf(data: bytes) -> Extraction:
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    sections, flags = [], []
    page_counts = []
    table_count = 0
    table_failures = []
    if doc.needs_pass:
        raise ValueError("Encrypted PDF requires a password; no extraction performed")
    for index, page in enumerate(doc, 1):
        text = page.get_text("text", sort=True)
        page_counts.append(len(text.strip()))
        if len(text.strip()) < 40:
            flags.append("pdf_page_with_little_or_no_extractable_text")
        if text.strip():
            sections.append(ExtractedSection(text=text, page=index, kind="pdf_page", source_locator=f"page={index}"))
        # Geometry/text-based table extraction only. This is an alternate text
        # representation, not OCR, and never replaces or discards page text.
        drawings = page.get_drawings() if hasattr(page, "find_tables") else []
        if drawings:
            try:
                found = find_pdf_tables(page, drawings)
                for table_index, table in enumerate(found.tables, 1):
                    if table.row_count < 2 or table.col_count < 2:
                        continue
                    rows = [[cell if cell is not None else "" for cell in row] for row in table.extract()]
                    table_text, table_data = table_representation(rows)
                    header = getattr(table, "header", None)
                    names = list(header.names) if header else []
                    table_data.update({"headers": [names] if any(names) else [],
                        "header_detection": "pymupdf_geometric_heuristic_requires_review",
                        "header_external": bool(header.external) if header else None,
                        "bbox": list(table.bbox), "start_char": 0, "end_char": len(table_text),
                        "source_locator": f"page={index}&table={table_index}"})
                    sections.append(ExtractedSection(table_text, page=index,
                        kind="pdf_table_alternate_representation", source_locator=table_data["source_locator"],
                        tables=[table_data]))
                    table_count += 1
            except Exception as exc:
                table_failures.append({"page": index, "error": f"{type(exc).__name__}: {exc}"})
    metadata = dict(doc.metadata or {})
    metadata.update({"page_count": len(doc), "page_text_char_counts": page_counts,
        "pdf_modification_date_raw": metadata.get("modDate") or None,
        "pdf_creation_date_raw": metadata.get("creationDate") or None,
        "geometry_table_count": table_count, "table_detection_strategy": "ruled_tables_from_vector_lines", "table_geometry_timeout_seconds": 5,
        "table_detection_limits": "Borderless tables may remain only in page-flow text and require source review.",
        "table_extraction_failures": table_failures})
    doc.close()
    flags.append("pdf_layout_and_any_borderless_tables_require_review")
    if not metadata["pdf_modification_date_raw"]:
        flags.append("pdf_modification_date_missing")
    if table_count:
        flags.append("pdf_table_geometry_and_headers_require_review")
    if table_failures:
        flags.append("pdf_table_extraction_failed_on_some_pages")
    if not sections:
        flags.append("no_extractable_text_no_ocr_attempted")
    return Extraction(sections, "pymupdf_text_and_geometric_tables_no_ocr", title=metadata.get("title"), metadata=metadata, flags=sorted(set(flags)), notes=["PDF page text is extracted without OCR or multimodal processing. Reading order and table columns require source inspection when material.", "Detected tables also receive separate cell-based representations with geometry-derived headers flagged for review; the original page text remains available.", "PDF metadata dates are retained as signals and are never promoted to legal effective dates."])


def extract_docx(path: Path) -> Extraction:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    doc = Document(path)
    parts = []
    tables = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            value = Paragraph(child, doc).text
            if value:
                parts.append(value)
        elif tag == "tbl":
            table = Table(child, doc)
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            value = "\n".join("\t".join(row) for row in rows)
            start = len(SECTION_SEPARATOR.join(parts)) + (len(SECTION_SEPARATOR) if parts else 0)
            parts.append(value)
            tables.append({"rows": rows, "start_char": start, "end_char": start + len(value), "headers": [], "header_inference": "not_attempted"})
    core = doc.core_properties
    metadata = {"created_raw": core.created.isoformat() if core.created else None, "modified_raw": core.modified.isoformat() if core.modified else None}
    return Extraction([ExtractedSection(SECTION_SEPARATOR.join(parts), kind="docx_body", tables=tables)], "python_docx_body_order", title=core.title, metadata=metadata, flags=["docx_textboxes_headers_footers_and_footnotes_not_extracted"], notes=["Only text and tables in document body extracted; raw DOCX retained."])


def extract_pptx(path: Path) -> Extraction:
    from pptx import Presentation
    presentation = Presentation(path)
    sections = []
    for index, slide in enumerate(presentation.slides, 1):
        parts, tables = [], []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text:
                parts.append(shape.text)
            if shape.has_table:
                rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
                value = "\n".join("\t".join(row) for row in rows)
                start = len(SECTION_SEPARATOR.join(parts)) + (len(SECTION_SEPARATOR) if parts else 0)
                parts.append(value)
                tables.append({"rows": rows, "start_char": start, "end_char": start + len(value), "headers": [], "header_inference": "not_attempted"})
        if parts:
            sections.append(ExtractedSection(SECTION_SEPARATOR.join(parts), slide=index, kind="pptx_slide", source_locator=f"slide={index}", tables=tables))
    return Extraction(sections, "python_pptx_text_and_tables", flags=["pptx_images_charts_grouped_shapes_and_speaker_notes_not_extracted"], notes=["Slide text/table shapes extracted in shape order. No images or media processed."])


def extract_web_json(data: bytes) -> Extraction:
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("web_extracted JSON must be an object")
    sections = []
    items = payload.get("sections")
    if items is None:
        items = [{"text": payload.get("text", payload.get("content", ""))}]
    for index, item in enumerate(items, 1):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("Every web-extracted section must contain a text string")
        if item["text"].strip():
            sections.append(ExtractedSection(item["text"], heading_path=item.get("heading_path", []), page=item.get("page"), kind="web_extracted_text", source_locator=item.get("source_locator", f"section={index}")))
    return Extraction(sections, "provided_web_text_snapshot", title=payload.get("title"), metadata={"capture_reference": payload.get("capture_reference"), "extraction_provider": payload.get("extraction_provider"), "capture_scope": payload.get("capture_scope", "unknown")}, flags=["original_source_bytes_not_downloaded", "web_capture_completeness_requires_review"], notes=["This is a captured text representation, not original document bytes. No model paraphrases may be supplied as source text."])


def extract_file(path: Path, record: dict) -> Extraction:
    data = path.read_bytes()
    media = str(record.get("media_type", "")).split(";", 1)[0].lower()
    suffix = path.suffix.lower()
    if record.get("status") == "web_extracted":
        return extract_web_json(data)
    if data.startswith(b"%PDF-") or media == "application/pdf" or suffix == ".pdf":
        return extract_pdf(data)
    if media == "text/html" or suffix in {".html", ".htm"} or re.match(br"\s*(?:<!doctype html|<html)", data, re.I):
        headers = record.get("http_headers") or (record.get("metadata") or {}).get("headers") or {}
        return extract_html(data, headers.get("Content-Type", headers.get("content-type", record.get("media_type", ""))), record.get("source_url"))
    if suffix == ".docx" or media == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return extract_docx(path)
    if suffix == ".pptx" or media == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        return extract_pptx(path)
    if suffix in {".txt", ".md", ".csv", ".tsv", ".xml", ".json"} or media.startswith("text/"):
        text = data.decode("utf-8-sig")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        flags = ["plain_text_structure_not_interpreted"] if suffix in {".xml", ".json", ".csv", ".tsv"} else []
        return Extraction([ExtractedSection(text, kind="plain_text")], "utf8_text_line_endings", flags=flags)
    raise ValueError(f"Unsupported text-only source format: {media or suffix or 'unknown'}")


def chunk_spans(text: str, max_chars: int = 3200, overlap: int = 400) -> Iterable[tuple[int, int]]:
    if max_chars < 128 or overlap < 0 or overlap >= max_chars // 2:
        raise ValueError("max_chars must be >=128; overlap must be nonnegative and less than half max_chars")
    start = 0
    length = len(text)
    while start < length:
        end = min(length, start + max_chars)
        if end < length:
            min_end = start + max_chars // 2
            for separator in ("\n\n", "\n", " "):
                boundary = text.rfind(separator, min_end, end)
                if boundary >= min_end:
                    end = boundary + len(separator)
                    break
        yield start, end
        if end == length:
            return
        next_start = end - overlap if overlap else end
        if overlap:
            # Start at a nearby word/paragraph boundary inside the overlap.
            match = re.search(r"\s+", text[next_start:min(end, next_start + 80)])
            if match:
                next_start += match.end()
        start = max(start + 1, min(end, next_start))


def evidence_signals(section: dict) -> tuple[list[dict], list[dict]]:
    text = section["text"]
    dates, relations = [], []
    for pattern, target in ((DATE_PATTERN, dates), (RELATION_PATTERN, relations)):
        for match in pattern.finditer(text):
            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 160)
            target.append({"section_id": section["section_id"], "matched_text": match.group(), "match_start_char": match.start(), "match_end_char": match.end(), "context": text[start:end], "context_start_char": start, "context_end_char": end, "status": "unreviewed_text_signal", "interpretation": None})
    return dates, relations


def process_record(record: dict, project_root: Path, max_chars: int, overlap: int) -> tuple[dict, list[dict], list[dict]]:
    source_id = str(record.get("source_id") or stable_id(str(record.get("source_url") or record.get("raw_path") or canonical_json(record))))
    metadata = {**(record.get("metadata") or {})}
    for key in ("source_role", "source_kind", "source_host_role", "publisher", "brief_reference_ids",
                "customer_reference_ids", "customer_inventory_status", "customer_listed_url",
                "customer_pdf_sha256", "target_resolution_status", "version_resolution_status",
                "brief_page", "reference_text", "source_list_match", "match_basis", "match_confidence",
                "scope_note", "scope_resolution", "requires_human_review", "notes", "parent_source_id",
                "parent_url", "parent_source_url", "immediate_parent_url", "resolution_basis",
                "edition_year", "edition_status", "review_flags"):
        if key in record:
            metadata[key] = record[key]
    doc = {"schema_version": SCHEMA_VERSION, "document_id": source_id, "title": record.get("title"), "source_url": record.get("source_url"), "resolved_url": record.get("resolved_url"), "raw_path": record.get("raw_path"), "media_type": record.get("media_type"), "retrieved_at": record.get("retrieved_at"), "source_origin": record.get("source_origin"), "acquisition_status": record.get("status"), "acquisition_metadata": metadata, "acquisition_record": record, "source_role": metadata.get("source_role") or ("collection_index" if metadata.get("source_kind") in {"collection_landing", "collection_index"} else "unspecified"), "retrieval_included": False, "legal_status": "not_determined", "status_resolution": "Only explicit document evidence and user review may establish relationships or applicability.", "uncertainty_flags": [], "text": "", "section_count": 0, "chunk_count": 0, "offset_unit": "unicode_code_points", "date_signals": {"pdf_modification_date_raw": None, "pdf_creation_date_raw": None, "http_last_modified_raw": metadata.get("http_last_modified") or metadata.get("last_modified") or (metadata.get("headers") or {}).get("Last-Modified") or (metadata.get("headers") or {}).get("last-modified"), "text_mentions": []}, "relationship_signals": []}
    for container in (record, metadata):
        for key in ("uncertainty_flags", "review_flags"):
            values = container.get(key) or []
            if isinstance(values, str):
                values = [values]
            doc["uncertainty_flags"].extend(flag for flag in values if isinstance(flag, str))
    doc["customer_reference_ids"] = metadata.get("customer_reference_ids") or []
    doc["customer_inventory_status"] = metadata.get("customer_inventory_status", "not_recorded")
    explicitly_listed = record.get("source_origin") == "customer_pdf_link" and doc["customer_inventory_status"] == "explicitly_listed"
    doc["inventory_reconciliation"] = {
        "status": "explicitly_listed_in_customer_pdf" if explicitly_listed else doc["customer_inventory_status"],
        "customer_reference_ids": doc["customer_reference_ids"],
        "scope": "Customer source selection only; document identity, version, currentness, and applicability remain separate review questions."}
    # These qualifications come from the acquisition registry. No domain name,
    # date comparison, or model judgment is used to establish authority/currentness.
    if doc["source_role"] == "regulatory_text_secondary_host" or metadata.get("source_host_role") in {"secondary", "secondary_mirror", "secondary_host"}:
        doc["uncertainty_flags"].append("secondary_host_requires_primary_source_verification")
    if "historical" in str(metadata.get("edition_status", "")).lower():
        year = str(metadata.get("edition_year", ""))
        year_label = f"_{year}" if re.fullmatch(r"\d{4}", year) else ""
        doc["uncertainty_flags"].append(f"historical_edition{year_label}_currentness_not_verified")
    scope = metadata.get("scope_resolution") or {}
    scope_status = scope.get("status", "") if isinstance(scope, dict) else str(scope)
    if "provisional" in scope_status.lower():
        doc["uncertainty_flags"].append("provisional_collection_topic_match_requires_human_review")
    if metadata.get("requires_human_review"):
        doc["uncertainty_flags"].append("source_requires_human_review")
    if metadata.get("match_confidence") in {"low", "medium"}:
        doc["uncertainty_flags"].append(f"source_match_confidence_{metadata['match_confidence']}_requires_review")
    if not explicitly_listed and ("pending" in str(record.get("source_list_match", "")) or "pending" in str(metadata.get("source_list_reconciliation", ""))):
        doc["uncertainty_flags"].append("original_resource_inventory_not_reconciled")
    if explicitly_listed:
        # A provided inventory resolves the inventory-presence question only.
        # Keep unresolved-target, alternate-version, access, and legal flags.
        obsolete_inventory_flags = {"original_resource_inventory_not_reconciled", "original_inventory_missing"}
        doc["uncertainty_flags"] = [flag for flag in doc["uncertainty_flags"] if flag not in obsolete_inventory_flags]
        for key in ("uncertainty_flags", "review_flags"):
            values = metadata.get(key)
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                metadata[key] = [flag for flag in values if flag not in obsolete_inventory_flags]
    if doc["source_role"] == "collection_index":
        doc["uncertainty_flags"].append("collection_index_excluded_from_evidence_retrieval")
    doc["uncertainty_flags"] = list(dict.fromkeys(doc["uncertainty_flags"]))
    if record.get("status") not in ACQUIRED_STATUSES:
        doc["processing_status"] = "not_acquired"
        doc["uncertainty_flags"].append("source_not_acquired")
        return doc, [], []
    if not record.get("raw_path"):
        doc["processing_status"] = "extraction_failed"
        doc["error"] = "Acquired manifest entry has no raw_path"
        return doc, [], []
    path = Path(record["raw_path"])
    if not path.is_absolute():
        path = project_root / path
    try:
        path = path.resolve(strict=True)
        path.relative_to(project_root.resolve())
    except (ValueError, FileNotFoundError) as exc:
        doc["processing_status"] = "extraction_failed"
        doc["error"] = f"raw_path must exist beneath project root: {exc}"
        return doc, [], []
    try:
        doc["raw_sha256"] = sha256_bytes(path.read_bytes())
        if record.get("sha256") and record["sha256"] != doc["raw_sha256"]:
            raise ValueError("Raw bytes do not match acquisition SHA256; reacquire or reconcile the manifest before use")
        extraction = extract_file(path, record)
        doc["title"] = doc["title"] or extraction.title or source_id
        doc["extracted_title"] = extraction.title
        doc["extraction"] = {"method": extraction.method, "processor_version": PROCESSOR_VERSION, "metadata": extraction.metadata, "notes": extraction.notes, "flags": extraction.flags, "text_only": True, "ocr_used": False, "model_used": False}
        doc["uncertainty_flags"].extend(extraction.flags)
        doc["date_signals"].update({"pdf_modification_date_raw": extraction.metadata.get("pdf_modification_date_raw"), "pdf_creation_date_raw": extraction.metadata.get("pdf_creation_date_raw"), "html_page_revision_markers": extraction.metadata.get("page_revision_markers", []), "html_date_metadata": extraction.metadata.get("html_date_metadata", [])})
        if "access_or_bot_challenge_page" in extraction.flags:
            doc["processing_status"] = "excluded_access_page"
            return doc, [], []
        sections, chunks, text_parts = [], [], []
        cursor = 0
        for index, extracted in enumerate(extraction.sections, 1):
            if not extracted.text.strip():
                continue
            if text_parts:
                cursor += len(SECTION_SEPARATOR)
            start = cursor
            text_parts.append(extracted.text)
            cursor += len(extracted.text)
            section_id = f"{source_id}__s{index:05d}"
            section = {"schema_version": SCHEMA_VERSION, "section_id": section_id, "document_id": source_id, "section_index": index, "title": doc["title"], "source_url": doc["source_url"], "resolved_url": doc["resolved_url"], "raw_path": doc["raw_path"], "raw_sha256": doc["raw_sha256"], "source_locator": extracted.source_locator, "page": extracted.page, "slide": extracted.slide, "heading_path": extracted.heading_path, "kind": extracted.kind, "document_start_char": start, "document_end_char": cursor, "text": extracted.text, "text_sha256": sha256_text(extracted.text), "elements": extracted.elements, "tables": extracted.tables, "offset_unit": "unicode_code_points", "extraction_method": extraction.method, "source_origin": doc["source_origin"], "source_role": doc["source_role"], "customer_reference_ids": doc["customer_reference_ids"], "customer_inventory_status": doc["customer_inventory_status"], "uncertainty_flags": list(doc["uncertainty_flags"])}
            date_signals, relations = evidence_signals(section)
            doc["date_signals"]["text_mentions"].extend(date_signals)
            doc["relationship_signals"].extend(relations)
            sections.append(section)
            for chunk_index, (left, right) in enumerate(chunk_spans(extracted.text, max_chars, overlap), 1):
                text = extracted.text[left:right]
                if not text.strip():
                    continue
                intersecting_tables = []
                for table in extracted.tables:
                    if not (table["start_char"] < right and table["end_char"] > left):
                        continue
                    rows = [row for row in table.get("row_spans", []) if row["start_char"] < right and row["end_char"] > left]
                    intersecting_tables.append({"table_start_char": table["start_char"], "table_end_char": table["end_char"],
                        "headers": table["headers"], "header_cells": table.get("header_cells", []),
                        "rows_intersecting_chunk": rows, "column_count": table.get("column_count"),
                        "header_binding_policy": table.get("header_binding_policy"),
                        "header_detection": table.get("header_detection"),
                        "source_locator": table.get("source_locator"), "offsets_relative_to": "section",
                        "context_only": True})
                chunks.append({"schema_version": SCHEMA_VERSION, "chunk_id": f"{section_id}__c{chunk_index:04d}", "document_id": source_id, "section_id": section_id, "title": doc["title"], "source_url": doc["source_url"], "resolved_url": doc["resolved_url"], "raw_path": doc["raw_path"], "raw_sha256": doc["raw_sha256"], "page": extracted.page, "slide": extracted.slide, "source_locator": extracted.source_locator, "heading_path": extracted.heading_path, "section_start_char": left, "section_end_char": right, "document_start_char": start + left, "document_end_char": start + right, "text": text, "text_sha256": sha256_text(text), "char_count": len(text), "offset_unit": "unicode_code_points", "table_context": intersecting_tables, "extraction_method": extraction.method, "source_origin": doc["source_origin"], "source_role": doc["source_role"], "customer_reference_ids": doc["customer_reference_ids"], "customer_inventory_status": doc["customer_inventory_status"], "uncertainty_flags": list(doc["uncertainty_flags"]), "legal_status": "not_determined"})
        doc["text"] = SECTION_SEPARATOR.join(text_parts)
        doc["text_sha256"] = sha256_text(doc["text"])
        doc["char_count"] = len(doc["text"])
        doc["section_count"] = len(sections)
        doc["chunk_count"] = len(chunks)
        doc["processing_status"] = "processed" if doc["text"].strip() else "no_extractable_text"
        if doc["source_role"] == "collection_index":
            doc["processing_status"] = "index_only"
        elif "no_rule_body_or_substantive_note_placeholder" in extraction.flags:
            doc["processing_status"] = "excluded_no_rule_body_placeholder"
        doc["retrieval_included"] = doc["processing_status"] == "processed"
        if not doc["text"].strip():
            doc["uncertainty_flags"].append("no_extractable_text")
        elif len(doc["text"].strip()) < 100:
            doc["uncertainty_flags"].append("very_short_extraction_requires_review")
        # This fails closed if an implementation change corrupts quotation offsets.
        validate_record(doc, sections, chunks)
        return doc, sections, chunks
    except Exception as exc:
        doc["processing_status"] = "extraction_failed"
        doc["error"] = f"{type(exc).__name__}: {exc}"
        doc["text"] = ""
        doc["section_count"] = doc["chunk_count"] = 0
        return doc, [], []


def validate_record(doc: dict, sections: list[dict], chunks: list[dict], require_coverage: bool = True) -> None:
    if doc.get("text_sha256"):
        assert sha256_text(doc["text"]) == doc["text_sha256"], "document text hash mismatch"
    section_map = {section["section_id"]: section for section in sections}
    for section in sections:
        assert doc["text"][section["document_start_char"]:section["document_end_char"]] == section["text"], "section/document offset mismatch"
        assert sha256_text(section["text"]) == section["text_sha256"]
        for table in section.get("tables", []):
            for row in table.get("row_spans", []):
                for cell in row.get("cells", []):
                    assert section["text"][cell["start_char"]:cell["end_char"]] == cell["text"], "table cell/section offset mismatch"
    by_section: dict[str, list[dict]] = {}
    for chunk in chunks:
        section = section_map[chunk["section_id"]]
        assert section["text"][chunk["section_start_char"]:chunk["section_end_char"]] == chunk["text"], "chunk/section offset mismatch"
        assert doc["text"][chunk["document_start_char"]:chunk["document_end_char"]] == chunk["text"], "chunk/document offset mismatch"
        assert sha256_text(chunk["text"]) == chunk["text_sha256"]
        by_section.setdefault(chunk["section_id"], []).append(chunk)
    for section in sections if require_coverage else []:
        coverage_end = 0
        for chunk in sorted(by_section.get(section["section_id"], []), key=lambda item: item["section_start_char"]):
            gap = section["text"][coverage_end:chunk["section_start_char"]]
            assert not gap.strip(), "non-whitespace text omitted by chunking"
            coverage_end = max(coverage_end, chunk["section_end_char"])
        assert not section["text"][coverage_end:].strip(), "section tail omitted by chunking"


def build_sqlite(path: Path, documents: list[dict], chunks: list[dict]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    if temp.exists():
        temp.unlink()
    con = sqlite3.connect(temp)
    try:
        con.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, title TEXT, source_url TEXT, processing_status TEXT, metadata_json TEXT)")
        con.execute("CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, document_id TEXT, section_id TEXT, page INTEGER, document_start_char INTEGER, document_end_char INTEGER, source_url TEXT, metadata_json TEXT)")
        con.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, title, heading_path, text, tokenize='unicode61')")
        for doc in documents:
            meta = {key: value for key, value in doc.items() if key != "text"}
            con.execute("INSERT INTO documents VALUES (?,?,?,?,?)", (doc["document_id"], doc["title"], doc["source_url"], doc["processing_status"], canonical_json(meta)))
        for chunk in chunks:
            meta = {key: value for key, value in chunk.items() if key != "text"}
            con.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)", (chunk["chunk_id"], chunk["document_id"], chunk["section_id"], chunk["page"], chunk["document_start_char"], chunk["document_end_char"], chunk["source_url"], canonical_json(meta)))
            con.execute("INSERT INTO chunks_fts VALUES (?,?,?,?)", (chunk["chunk_id"], chunk["title"] or "", " > ".join(chunk["heading_path"]), chunk["text"]))
        con.commit()
    finally:
        con.close()
    temp.replace(path)


def repair_failed_records(records: list[dict], project_root: Path, output: Path,
                          max_chars: int, overlap: int, progress: bool = False) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Reuse verified canonical records and re-extract only failed source entries.

    This intentionally refuses changed nonfailed acquisition records, changed
    document sets, or changed chunk settings: those require a full rebuild.
    Existing PDF extraction results are reused without opening their raw PDFs.
    """
    required = [output / name for name in ("documents.jsonl", "sections.jsonl", "chunks.jsonl", "processing_report.json")]
    if not all(path.is_file() for path in required):
        raise ValueError("--repair-failed requires existing canonical JSONL files and processing_report.json")
    prior_report = json.loads(required[3].read_text(encoding="utf-8"))
    chunking = prior_report.get("chunking") or {}
    if chunking.get("max_chars") != max_chars or chunking.get("overlap_chars") != overlap:
        raise ValueError("Incremental repair cannot change chunk settings; use the original settings or rebuild")
    documents, sections, chunks = [[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] for path in required[:3]]
    manifest = {str(record.get("source_id") or stable_id(str(record.get("source_url") or record.get("raw_path") or canonical_json(record)))): record for record in records}
    previous = {doc["document_id"]: doc for doc in documents}
    if len(previous) != len(documents) or set(previous) != set(manifest):
        raise ValueError("Incremental repair requires the same unique document IDs as the existing corpus; rebuild after source-list changes")
    repair_ids = {doc["document_id"] for doc in documents if doc.get("processing_status") == "extraction_failed"}
    sections_by_doc, chunks_by_doc = {}, {}
    for section in sections:
        if section["document_id"] not in previous:
            raise ValueError("Canonical sections contain an unknown document")
        sections_by_doc.setdefault(section["document_id"], []).append(section)
    for chunk in chunks:
        if chunk["document_id"] not in previous:
            raise ValueError("Canonical chunks contain an unknown document")
        chunks_by_doc.setdefault(chunk["document_id"], []).append(chunk)
    for doc in documents:
        source_id = doc["document_id"]
        if source_id in repair_ids:
            continue
        if canonical_json(doc.get("acquisition_record")) != canonical_json(manifest[source_id]):
            raise ValueError(f"Acquisition metadata changed for nonfailed source {source_id}; full rebuild required")
        doc_sections, doc_chunks = sections_by_doc.get(source_id, []), chunks_by_doc.get(source_id, [])
        if len(doc_sections) != doc.get("section_count", 0):
            raise ValueError(f"Canonical section count mismatch for {source_id}")
        if doc.get("retrieval_included") and len(doc_chunks) != doc.get("chunk_count", 0):
            raise ValueError(f"Canonical chunk count mismatch for {source_id}")
        validate_record(doc, doc_sections, doc_chunks, require_coverage=bool(doc.get("retrieval_included")))
    before_hashes = {path.name: sha256_bytes(path.read_bytes()) for path in required}
    sections = [section for section in sections if section["document_id"] not in repair_ids]
    chunks = [chunk for chunk in chunks if chunk["document_id"] not in repair_ids]
    for index, old_doc in enumerate(documents):
        source_id = old_doc["document_id"]
        if source_id not in repair_ids:
            continue
        doc, doc_sections, doc_chunks = process_record(manifest[source_id], project_root, max_chars, overlap)
        documents[index] = doc
        sections.extend(doc_sections)
        chunks.extend(doc_chunks)
        if progress:
            print(canonical_json({"source_id": source_id, "processing_status": doc["processing_status"],
                "characters": len(doc["text"]), "chunks": len(doc_chunks), "mode": "failed_source_repair"}), file=sys.stderr, flush=True)
    rank = {doc["document_id"]: index for index, doc in enumerate(documents)}
    sections.sort(key=lambda item: (rank[item["document_id"]], item["section_index"]))
    chunks.sort(key=lambda item: (rank[item["document_id"]], item["section_id"], item["section_start_char"]))
    provenance = {"processing_mode": "incremental_failed_source_repair", "incremental_repair": {
        "reprocessed_source_ids": sorted(repair_ids), "reused_document_count": len(documents) - len(repair_ids),
        "previous_report_generated_at": prior_report.get("generated_at"),
        "previous_report_processor_version": prior_report.get("processor_version"),
        "previous_artifact_sha256": before_hashes,
        "reused_canonical_records_validated": True, "reused_raw_sources_reextracted": False,
        "raw_integrity_scope": "Repaired source hashes verified against acquisition records; unchanged canonical hashes and offsets revalidated without reopening their raw files."}}
    return documents, sections, chunks, provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--max-chars", type=int, default=3200)
    parser.add_argument("--overlap", type=int, default=400)
    parser.add_argument("--no-sqlite", action="store_true")
    parser.add_argument("--progress", action="store_true", help="Write a compact status after each source to stderr")
    parser.add_argument("--repair-failed", action="store_true", help="Re-extract only existing extraction_failed sources; validate and reuse all other canonical records, then rebuild JSONL/FTS")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    manifest = args.manifest or root / "manifests/acquisition.jsonl"
    output = args.output or root / "processed"
    output.mkdir(parents=True, exist_ok=True)
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [str(record.get("source_id") or stable_id(str(record.get("source_url") or record.get("raw_path") or canonical_json(record)))) for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Manifest source_id values must be unique; reconcile acquisition attempts before processing")
    if args.repair_failed:
        documents, sections, chunks, run_provenance = repair_failed_records(records, root, output, args.max_chars, args.overlap, args.progress)
    else:
        documents, sections, chunks = [], [], []
        run_provenance = {"processing_mode": "full_rebuild"}
        for record in records:
            doc, doc_sections, doc_chunks = process_record(record, root, args.max_chars, args.overlap)
            documents.append(doc)
            sections.extend(doc_sections)
            chunks.extend(doc_chunks)
            if args.progress:
                print(canonical_json({"source_id": doc["document_id"], "processing_status": doc["processing_status"], "characters": len(doc["text"]), "chunks": len(doc_chunks)}), file=sys.stderr, flush=True)
    # Exact duplicate files remain explicit in the inventory but do not duplicate
    # retrieval chunks. Their canonical document is recorded for traceability.
    seen_hashes = {}
    duplicate_ids = set()
    for doc in documents:
        digest = doc.get("raw_sha256")
        if doc["processing_status"] != "processed" or not digest:
            continue
        if digest in seen_hashes:
            doc["duplicate_of"] = seen_hashes[digest]
            doc["retrieval_included"] = False
            duplicate_ids.add(doc["document_id"])
        else:
            seen_hashes[digest] = doc["document_id"]
            doc["duplicate_of"] = None
            doc["retrieval_included"] = True
    included_ids = {doc["document_id"] for doc in documents if doc.get("retrieval_included")}
    retrieval_chunks = [chunk for chunk in chunks if chunk["document_id"] in included_ids]
    jsonl_write(output / "documents.jsonl", documents)
    jsonl_write(output / "sections.jsonl", sections)
    jsonl_write(output / "chunks.jsonl", retrieval_chunks)
    if not args.no_sqlite:
        build_sqlite(output / "corpus.sqlite", documents, retrieval_chunks)
    counts = {}
    for doc in documents:
        counts[doc["processing_status"]] = counts.get(doc["processing_status"], 0) + 1
    stats = {"schema_version": SCHEMA_VERSION, "processor_version": PROCESSOR_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), "manifest_records": len(documents), "processing_status_counts": counts, "section_count": len(sections), "chunk_count": len(retrieval_chunks), "duplicate_source_count": len(duplicate_ids), "extracted_char_count": sum(len(doc["text"]) for doc in documents), "retrieval_char_count_with_overlap": sum(len(chunk["text"]) for chunk in retrieval_chunks), "chunking": {"max_chars": args.max_chars, "overlap_chars": args.overlap, "offset_unit": "unicode_code_points"}, "text_only": True, "ocr_used": False, "model_used": False, "quote_offset_validation": "passed", "legal_relationship_resolution": "not_performed", "warnings": [{"document_id": doc["document_id"], "processing_status": doc["processing_status"], "flags": doc["uncertainty_flags"], "error": doc.get("error")} for doc in documents if doc["uncertainty_flags"] or doc.get("error")]}
    versions = {}
    for doc in documents:
        version = (doc.get("extraction") or {}).get("processor_version", "not_extracted")
        versions[version] = versions.get(version, 0) + 1
    stats.update(run_provenance)
    stats["document_processor_version_counts"] = versions
    (output / "processing_report.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in stats.items() if key != "warnings"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
