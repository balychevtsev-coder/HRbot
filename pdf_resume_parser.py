import pdfplumber
import pytesseract
from PIL import Image
import io
import re

def extract_resume_data_from_pdf(
    pdf_bytes,  # streamlit uploaded_file или путь к файлу
    client,
    system_prompt: str,
    model: str = "gpt-4o-mini"
) -> str:
    """
    Извлекает резюме из PDF через pdfplumber (текст) и прогоняет через GPT для структуры.

    :param pdf_file: файл PDF (Streamlit uploaded_file или путь к файлу)
    :param client: openai.OpenAI client
    :param system_prompt: system prompt для GPT
    :param model: модель GPT
    :return: структурированное резюме в markdown
    """
    full_text = []

    # Открываем PDF через pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text.append(text)
                
    # Если текст не найден на страницах → можно пробовать OCR
    if not full_text:
        # конвертируем PDF в изображения через PIL (pytesseract работает с изображениями)
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(pdf_bytes, dpi=300)
        for img in images:
            text = pytesseract.image_to_string(
                img,
                lang="rus+eng",
                config="--psm 6"
            )
            if text:
                full_text.append(text)

    raw_text = "\n".join(full_text)
    raw_text = re.sub(r'\n{3,}', '\n\n', raw_text).strip()

    # Прогоняем через GPT для структурирования и markdown
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Текст резюме:\n\n{raw_text}"}
        ],
        temperature=0,
        max_tokens=1200
    )

    return response.choices[0].message.content.strip()