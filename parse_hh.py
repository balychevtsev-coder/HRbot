import json
import requests
from bs4 import BeautifulSoup

# Заголовки для имитации реального браузера
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

def load_cookies_to_session(session, cookies_path='cookies.json'):
    """
    Загружает куки работодателя из JSON-файла в сессию requests.
    """
    try:
        with open(cookies_path, 'r', encoding='utf-8') as f:
            cookies_list = json.load(f)
            for cookie in cookies_list:
                session.cookies.set(
                    cookie['name'], 
                    cookie['value'], 
                    domain=cookie.get('domain', '.hh.ru')
                )
        return True
    except FileNotFoundError:
        print(f"Файл {cookies_path} не найден.")
        return False
    except Exception as e:
        print(f"Ошибка загрузки куки: {e}")
        return False

def get_html(url, use_auth=False):
    """
    Получает HTML-код страницы. Если use_auth=True, использует куки из cookies.json.
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    
    if use_auth:
        load_cookies_to_session(session)
        
    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"Ошибка при запросе к HH.ru: {e}")
        return None

def extract_vacancy_data(html):
    """
    Парсит публичные данные вакансии.
    Использует data-qa атрибуты для стабильности.
    """
    if not html:
        return "Не найдено"

    soup = BeautifulSoup(html, 'html.parser')
    
    # Поиск заголовка (название вакансии)
    title_el = (
        soup.find('h1', {'data-qa': 'vacancy-title'}) or 
        soup.find('span', {'data-qa': 'vacancy-title'}) or
        soup.find('h1')
    )
    title = title_el.text.strip() if title_el else "Название не определено"
    
    # Поиск компании
    company = soup.find('a', {'data-qa': 'vacancy-company-name'})
    company_text = company.text.strip() if company else "Компания не указана"
    
    # Поиск описания
    description = soup.find('div', {'data-qa': 'vacancy-description'})
    desc_text = description.get_text(separator="\n").strip() if description else "Описание не найдено"

    # Формируем текст (первая строка — заголовок для БД)
    return f"# {title}\n\n**Компания:** {company_text}\n\n## Описание\n{desc_text}"

def extract_resume_data(url):
    """
    Парсит данные резюме через аккаунт работодателя.
    """
    html = get_html(url, use_auth=True)
    if not html:
        return "Ошибка доступа к резюме. Проверьте cookies.json."

    soup = BeautifulSoup(html, 'html.parser')
    
    # 1. Извлекаем ФИО (доступно только при наличии кук работодателя)
    name_el = (
        soup.find('h2', {'data-qa': 'resume-personal-name'}) or 
        soup.find('span', {'data-qa': 'resume-personal-name'})
    )
    name = name_el.text.strip() if name_el else "ФИО скрыто"
    
    # 2. Извлекаем Контакты
    phone_el = soup.find('span', {'data-qa': 'resume-contacts-phone'})
    phone = phone_el.text.strip() if phone_el else "Телефон не найден"
    
    # 3. Извлекаем основной текст резюме для ИИ-анализа
    # Собираем опыт и навыки
    main_content = soup.find('div', {'id': 'resume-main-content'})
    resume_body = main_content.get_text(separator=" ").strip() if main_content else soup.get_text()

    # Ограничиваем длину для GPT, чтобы не переплачивать за токены
    markdown = f"# ФИО: {name}\n"
    markdown += f"**Телефон:** {phone}\n\n"
    markdown += f"## Данные для анализа\n{resume_body[:4000]}"
    
    return markdown