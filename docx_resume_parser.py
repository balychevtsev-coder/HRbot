import docx

def extract_resume_data_from_docx(docx_bytes):
    """Извлекает текст из Word-файла."""
    from io import BytesIO
    doc = docx.Document(BytesIO(docx_bytes))
    full_text = []
    for para in doc.paragraphs:
        full_text.append(para.text)
    return "\n".join(full_text)