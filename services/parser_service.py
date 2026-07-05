# ──────────────────────────────────────────────────────────────
# services/parser_service.py — Text extraction from PDF/DOCX
# ──────────────────────────────────────────────────────────────

import io
import pypdf
import docx

def extract_text_from_pdf(stream: io.BytesIO) -> str:
    """
    Extract raw text from a PDF file stream using pypdf.
    """
    try:
        reader = pypdf.PdfReader(stream)
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        return "\n".join(text_parts)
    except Exception as e:
        raise ValueError(f"Failed to parse PDF file: {str(e)}")

def extract_text_from_docx(stream: io.BytesIO) -> str:
    """
    Extract raw text from a DOCX file stream using python-docx.
    Includes text from paragraphs and tables.
    """
    try:
        doc = docx.Document(stream)
        text_parts = []
        
        # Extract from paragraphs
        for paragraph in doc.paragraphs:
            if paragraph.text:
                text_parts.append(paragraph.text)
                
        # Extract from tables
        for table in doc.tables:
            for row in table.rows:
                row_cells = [cell.text for cell in row.cells if cell.text]
                if row_cells:
                    text_parts.append(" ".join(row_cells))
                    
        return "\n".join(text_parts)
    except Exception as e:
        raise ValueError(f"Failed to parse DOCX file: {str(e)}")
