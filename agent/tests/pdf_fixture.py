"""Small deterministic text-layer PDFs; no rendering/OCR/test dependency."""
from __future__ import annotations


def pdf_bytes(pages=("First page",), *, catalog=b"", broken_page=None, compressed=False,
              image_pages=()):
    import zlib

    objects = [b"", b"", b""]
    mappings = sorted({ord(char) for text in pages for char in text})
    cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            b"/CMapName /Test def /CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n")
    cmap += f"{len(mappings)} beginbfchar\n".encode()
    cmap += b"".join(f"<{char:04x}> <{char:04x}>\n".encode() for char in mappings)
    cmap += b"endbfchar\nendcmap CMapName currentdict /CMap defineresource pop end end"
    objects[2] = b"<< /Type /Font /Subtype /Type0 /BaseFont /Test /Encoding /Identity-H /DescendantFonts [4 0 R] /ToUnicode 5 0 R >>"
    objects += [b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Test /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000 >>",
                b"<< /Length " + str(len(cmap)).encode() + b" >>\nstream\n" + cmap + b"\nendstream"]
    kids = []
    for index, text in enumerate(pages):
        page_id, content_id = len(objects) + 1, len(objects) + 2
        kids.append(f"{page_id} 0 R".encode())
        content = b"BT /F1 12 Tf 10 100 Td <" + text.encode("utf-16be").hex().encode() + b"> Tj ET"
        if compressed:
            content = zlib.compress(content)
        image_resource = b""
        if index in image_pages:
            content = b"q 1 0 0 1 0 0 cm /Im0 Do Q"
            image_resource = f"/XObject << /Im0 {content_id + 1} 0 R >>".encode()
        contents = b"999999 0 R" if index == broken_page else f"{content_id} 0 R".encode()
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 3 0 R >> " + image_resource + b" >> /Contents " + contents + b" >>")
        objects.append(b"<< /Length " + str(len(content)).encode() +
                       (b" /Filter /FlateDecode" if compressed and index not in image_pages else b"") + b" >>\nstream\n" + content + b"\nendstream")
        if index in image_pages:
            objects.append(b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\x00\nendstream")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R " + catalog + b" >>"
    objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count " + str(len(kids)).encode() + b" >>"
    data, offsets = b"%PDF-1.7\n", []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return data + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
