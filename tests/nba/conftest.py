from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nba_reports"


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_text_pdf(pages: list[list[list[object]]]) -> bytes:
    page_count = len(pages)
    font_id = 3 + page_count * 2
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{' '.join(f'{3 + i * 2} 0 R' for i in range(page_count))}] "
            f"/Count {page_count} >>"
        ).encode(),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for index, items in enumerate(pages):
        page_id = 3 + index * 2
        content_id = page_id + 1
        commands = "\n".join(
            f"BT /F1 8 Tf 1 0 0 1 {x} {y} Tm ({_pdf_escape(str(value))}) Tj ET"
            for x, y, value in items
        ).encode("latin-1")
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
        objects[content_id] = (
            f"<< /Length {len(commands)} >>\nstream\n".encode() + commands + b"\nendstream"
        )

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id in range(1, font_id + 1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode())
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {font_id + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer << /Size {font_id + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


@pytest.fixture(
    params=[
        "legacy_category_v1",
        "legacy_reason_first_v1c",
        "legacy_current_previous_v1d",
        "legacy_status_history_v1b",
        "standard_v2",
        "compact_v3",
    ]
)
def nba_pdf_fixture(request: pytest.FixtureRequest) -> tuple[str, bytes]:
    name = str(request.param)
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return name, build_text_pdf(data["pages"])


@pytest.fixture
def nba_pdf_loader():
    def load(name: str) -> bytes:
        data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        return build_text_pdf(data["pages"])

    return load


@pytest.fixture
def nba_pdf_builder():
    return build_text_pdf
