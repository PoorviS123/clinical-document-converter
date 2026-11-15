"""Print the V10 -> V11 mapping that TransCelerate stores as Word comments in
CPT_CoreBWE-v011_M11-Mapping-Reference.docx, as TSV: v11 number, v11 style, v11 text, v10 mapping."""

import re
import sys
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HEADING_LEVELS = {"01Heading1": 1, "02Heading2": 2, "03Heading3": 3, "04Heading4": 4}
MAX_DEPTH = 4


def paragraph_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(W + "t")).strip()


def comment_text(comment: ET.Element) -> str:
    parts = [paragraph_text(p) for p in comment.iter(W + "p")]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def main(docx_path: str) -> None:
    archive = zipfile.ZipFile(docx_path)
    comments_root = ET.fromstring(archive.read("word/comments.xml"))
    comments = {c.get(W + "id"): comment_text(c) for c in comments_root.iter(W + "comment")}

    document = ET.fromstring(archive.read("word/document.xml"))
    counters = [0] * MAX_DEPTH
    print("v11_number\tv11_style\tv11_text\tv10_mapping")
    for paragraph in document.iter(W + "p"):
        style_node = paragraph.find(f"{W}pPr/{W}pStyle")
        style = style_node.get(W + "val") if style_node is not None else ""
        text = paragraph_text(paragraph)
        number = ""
        if style in HEADING_LEVELS and text:
            level = HEADING_LEVELS[style]
            counters[level - 1] += 1
            counters[level:] = [0] * (MAX_DEPTH - level)
            number = ".".join(str(c) for c in counters[:level])
            text = re.sub(r"^\d+(\.\d+)*\s*", "", text)
        for reference in paragraph.iter(W + "commentReference"):
            mapping = comments.get(reference.get(W + "id"), "")
            print(f"{number}\t{style}\t{text[:90]}\t{mapping}")


if __name__ == "__main__":
    main(sys.argv[1])
