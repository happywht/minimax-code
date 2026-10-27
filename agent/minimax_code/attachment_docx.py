"""DOCX body text only, imported exclusively after the child resource gate.

No extraction to disk, external relationship access, Office execution or OCR.
Every package XML/rels part is checked before python-docx sees the package.
"""
from __future__ import annotations

import binascii
import io
import posixpath
import struct
import unicodedata
import zipfile
import zlib

from .attachment_metadata import SCOPE, failure

MAX_MEMBER = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


class Reject(ValueError):
    def __init__(self, reason):
        self.reason = reason


def require(value, reason="invalid_package"):
    if not value:
        raise Reject(reason)


def safe_name(name):
    require(isinstance(name, str) and 0 < len(name.encode("utf8")) <= 512
            and not name.startswith("/") and not any(c in name for c in "\\:%?#")
            and not any(ord(c) < 32 or ord(c) == 127 for c in name)
            and name == unicodedata.normalize("NFC", name), "unsafe_package")
    require(all(part not in {"", ".", ".."} for part in name.rstrip("/").split("/")),
            "unsafe_package")


def package_parts(data):
    tail = data[-65557:]
    end = tail.rfind(b"PK\x05\x06")
    require(end >= 0 and len(tail) - end >= 22)
    _, disk, start, disk_count, count, size, offset, comment = struct.unpack(
        "<4s4H2IH", tail[end:end + 22])
    require(not disk and not start and disk_count == count and count < 65535
            and size != 0xffffffff and offset != 0xffffffff, "unsafe_package")
    require(count <= 2048 and size <= 1024 * 1024, "package_limit")
    require(len(tail) - end == 22 + comment
            and offset + size == len(data) - len(tail) + end)
    central, cursor = memoryview(data)[offset:offset + size], 0
    for _ in range(count):
        require(cursor + 46 <= len(central) and central[cursor:cursor + 4] == b"PK\x01\x02")
        name, extra, annotation, volume = struct.unpack_from("<4H", central, cursor + 28)
        require(name <= 512 and extra <= 1024 and annotation <= 1024 and not volume,
                "unsafe_package")
        cursor += 46 + name + extra + annotation
    require(cursor == len(central))
    parts, aliases, total, spans = {}, set(), 0, []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        require(len(archive.infolist()) == count)
        for info in archive.infolist():
            safe_name(info.filename)
            alias = info.filename.casefold().rstrip("/")
            require(alias not in aliases, "unsafe_package")
            aliases.add(alias)
            require(not info.flag_bits & ~0x808 and info.compress_type in {0, 8}
                    and info.file_size != 0xffffffff and info.compress_size != 0xffffffff,
                    "unsafe_package")
            # Reject ZIP64 and malformed extra records regardless of declared size.
            for extra in (info.extra,):
                cursor = 0
                while cursor < len(extra):
                    require(cursor + 4 <= len(extra))
                    kind, length = struct.unpack_from("<HH", extra, cursor)
                    require(kind != 1, "unsafe_package")
                    cursor += 4 + length
                require(cursor == len(extra))
            require(info.file_size <= MAX_MEMBER, "package_limit")
            local = info.header_offset
            require(local >= 0 and local + 30 <= offset and data[local:local + 4] == b"PK\x03\x04")
            flags, method = struct.unpack_from("<HH", data, local + 6)
            name_length, extra_length = struct.unpack_from("<HH", data, local + 26)
            begin = local + 30 + name_length + extra_length
            finish = begin + info.compress_size
            require(begin <= finish <= offset and flags == info.flag_bits and method == info.compress_type)
            if not flags & 8:
                crc_local, compressed_local, size_local = struct.unpack_from("<3I", data, local + 14)
                require((crc_local, compressed_local, size_local) ==
                        (info.CRC, info.compress_size, info.file_size))
            raw_name = data[local + 30:local + 30 + name_length]
            require(raw_name.decode("utf8" if flags & 0x800 else "cp437") == info.filename)
            local_extra = data[local + 30 + name_length:begin]
            cursor = 0
            while cursor < len(local_extra):
                require(cursor + 4 <= len(local_extra))
                kind, length = struct.unpack_from("<HH", local_extra, cursor)
                require(kind != 1, "unsafe_package")
                cursor += 4 + length
            require(cursor == len(local_extra))
            spans.append((local, finish))
            # Count actual decompression independently of zipfile's declared-size truncation.
            decoder = zlib.decompressobj(-15) if method == 8 else None
            chunks, actual, crc = [], 0, 0
            compressed = memoryview(data)[begin:finish]
            for pos in range(0, len(compressed), 65536):
                raw = compressed[pos:pos + 65536]
                while raw:
                    chunk = decoder.decompress(raw, 65536) if decoder else bytes(raw)
                    raw = decoder.unconsumed_tail if decoder else b""
                    actual += len(chunk)
                    total += len(chunk)
                    require(actual <= MAX_MEMBER and total <= MAX_TOTAL, "package_limit")
                    crc = binascii.crc32(chunk, crc)
                    chunks.append(chunk)
            require(decoder is None or decoder.eof and not decoder.unused_data)
            require(actual == info.file_size and crc & 0xffffffff == info.CRC)
            parts[info.filename] = b"".join(chunks)
    spans.sort()
    require(all(left[1] <= right[0] for left, right in zip(spans, spans[1:], strict=False)))
    return parts


def validate_xml(parts, fromstring):
    trees = {}
    for name, value in parts.items():
        low = name.lower()
        require(not any(token in low for token in ("vbaproject", "embeddings/", "activex/")),
                "unsupported_feature")
        if low.endswith((".xml", ".rels")):
            trees[name] = fromstring(value, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    require("[Content_Types].xml" in trees and "_rels/.rels" in trees
            and "word/document.xml" in trees)
    types = trees["[Content_Types].xml"]
    require(types.tag == f"{{{CT}}}Types")
    defaults, overrides = {}, {}
    for item in types:
        mime = item.get("ContentType", "")
        require(mime and not any(token in mime.lower() for token in
                ("macroenabled", "vbaproject", "oleobject", "activex", "officedocument.package")), "unsupported_feature")
        if item.tag == f"{{{CT}}}Override":
            name = item.get("PartName", "")
            require(name.startswith("/"))
            safe_name(name[1:])
            require(name[1:] in parts and name[1:] not in overrides)
            overrides[name[1:]] = mime
        elif item.tag == f"{{{CT}}}Default":
            ext = item.get("Extension", "")
            require(ext and ext not in defaults)
            defaults[ext] = mime
        else:
            require(False)
    require(overrides.get("word/document.xml") == MAIN)
    for name, value in parts.items():
        mime = overrides.get(name, defaults.get(name.rsplit(".", 1)[-1], ""))
        if name not in trees and (mime.endswith("+xml") or mime.endswith("/xml")):
            trees[name] = fromstring(value, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    main_relationship = False
    for name, tree in trees.items():
        if not name.endswith(".rels"):
            continue
        require(tree.tag == f"{{{REL}}}Relationships")
        if name == "_rels/.rels":
            source = ""
        else:
            parent, leaf = posixpath.split(name)
            require(posixpath.basename(parent) == "_rels" and leaf.endswith(".rels"))
            source = posixpath.join(posixpath.dirname(parent), leaf[:-5])
            require(source in parts)
        ids = set()
        for item in tree:
            require(item.tag == f"{{{REL}}}Relationship")
            identity, kind, target = item.get("Id"), item.get("Type", ""), item.get("Target", "")
            require(identity and identity not in ids and kind and target)
            ids.add(identity)
            require(not kind.lower().endswith("/package") and not any(token in kind.lower() for token in
                    ("oleobject", "vbaproject", "activex", "afchunk")), "unsupported_feature")
            if item.get("TargetMode") == "External":
                require(kind == OFFICE_REL + "hyperlink", "unsupported_feature")
                continue  # Metadata only. Never dereference any external target.
            require(item.get("TargetMode") in {None, "Internal"})
            require(not any(c in target for c in "\\:%?#") and not target.startswith("//"), "unsafe_package")
            resolved = (target[1:] if target.startswith("/") else
                        posixpath.normpath(posixpath.join(posixpath.dirname(source), target)))
            safe_name(resolved)
            require(resolved in parts)
            if source == "" and kind == OFFICE_REL + "officeDocument":
                require(resolved == "word/document.xml" and not main_relationship)
                main_relationship = True
    require(main_relationship)
    for name in parts:
        if name.endswith("/") or name == "[Content_Types].xml":
            continue
        require(name in overrides or name.rsplit(".", 1)[-1] in defaults)


def parse_docx(data):
    try:
        import docx
        from defusedxml.ElementTree import fromstring
        from docx.table import Table, _Cell
        from docx.text.paragraph import Paragraph
    except ImportError:
        return failure("missing_dependency", unavailable=True)
    try:
        require(hasattr(docx.document.Document, "iter_inner_content")
                and hasattr(_Cell, "iter_inner_content"), "unsupported_feature")
        parts = package_parts(data)
        validate_xml(parts, fromstring)
        document = docx.Document(io.BytesIO(data))
        output, size, lines, truncated, has_text = [], 0, 0, False, False

        def emit(label, text):
            nonlocal size, lines, truncated, has_text
            if not text:
                return
            has_text = True
            value = f"[{label}] {text}\n"
            remaining = max(0, 65536 - size)
            bounded = value.encode("utf8")[:remaining].decode("utf8", "ignore")
            bounded = "".join(bounded.splitlines(keepends=True)[:max(0, 2000 - lines)])
            truncated |= bounded != value
            output.append(bounded)
            size += len(bounded.encode("utf8"))
            lines += len(bounded.splitlines())

        def walk(container, position="body", depth=0):
            require(depth <= 8, "package_limit")
            for index, item in enumerate(container.iter_inner_content(), 1):
                label = f"{position}/{index}"
                if isinstance(item, Paragraph):
                    emit(label, item.text)
                elif isinstance(item, Table):
                    seen = set()
                    for row_index, row in enumerate(item.rows, 1):
                        for column_index, cell in enumerate(row.cells, 1):
                            # Merged cells can recur in the official grid API.
                            if cell._tc in seen:
                                continue
                            seen.add(cell._tc)
                            walk(cell, f"{label}/row{row_index}/cell{column_index}", depth + 1)

        walk(document)
        status = "empty" if not has_text else "truncated" if truncated else "parsed"
        return {"parser_status": status,
                "parser_reason": "no_extracted_body_text_no_ocr" if not has_text else
                    "preview_limit" if truncated else "ok",
                "parser_scope": SCOPE, "parser_version": 2, "preview_text": "".join(output),
                "preview_truncated": int(truncated), "quality_status": "unverified"}
    except Reject as exc:
        return failure(exc.reason)
    except Exception:
        return failure("invalid_package")  # Never return package XML, traces, paths or secrets.
