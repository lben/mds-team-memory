from pathlib import Path


def extract_passages(path: Path, extension: str) -> list[dict]:
    """Extract searchable passages with exact locators from an uploaded file.

    Returns [{"text": ..., "locator": ...}]. Raises ValueError when the file
    cannot be parsed as its claimed type.
    """
    if extension == ".pdf":
        return _extract_pdf(path)
    if extension == ".docx":
        return _extract_docx(path)
    return _extract_text(path)


def _extract_pdf(path: Path) -> list[dict]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(path)
        passages = []
        for page_no, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                passages.append({"text": text, "locator": f"Page {page_no}"})
        return passages
    except Exception as exc:
        raise ValueError(f"Could not read PDF: {exc}") from exc


def _extract_docx(path: Path) -> list[dict]:
    import docx

    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise ValueError(f"Could not read DOCX: {exc}") from exc
    passages = []
    for idx, para in enumerate(document.paragraphs, start=1):
        text = para.text.strip()
        if text:
            passages.append({"text": text, "locator": f"Paragraph {idx}"})
    return passages


def _extract_text(path: Path) -> list[dict]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"Could not read file: {exc}") from exc
    passages = []
    lines = content.splitlines()
    start = None
    block: list[str] = []
    for idx, line in enumerate(lines, start=1):
        if line.strip():
            if start is None:
                start = idx
            block.append(line)
        elif block:
            passages.append(_text_block(block, start, idx - 1))
            block, start = [], None
    if block:
        passages.append(_text_block(block, start, len(lines)))
    return passages


def _text_block(block: list[str], start: int, end: int) -> dict:
    locator = f"Line {start}" if start == end else f"Lines {start}-{end}"
    return {"text": "\n".join(block).strip(), "locator": locator}
