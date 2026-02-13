import sqlite3
import pandas as pd

def init_db():
    conn = sqlite3.connect('hr_assistant.db')
    cursor = conn.cursor()
    
    # 1. Таблица вакансий (храним название и текст описания)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vacancies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            description TEXT
        )
    ''')
    
    # 2. Таблица кандидатов (добавлена колонка resume_url)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT,
            phone TEXT,
            vacancy_name TEXT,
            score TEXT,
            analysis_text TEXT,
            resume_url TEXT,
            FOREIGN KEY (vacancy_name) REFERENCES vacancies (name)
        )
    ''')
    conn.commit()
    conn.close()

def save_vacancy(name, description):
    """Сохраняет или обновляет текст вакансии."""
    conn = sqlite3.connect('hr_assistant.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO vacancies (name, description) VALUES (?, ?)', (name, description))
    conn.commit()
    conn.close()

def get_vacancies():
    """Возвращает список имен всех вакансий."""
    conn = sqlite3.connect('hr_assistant.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM vacancies')
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def add_candidate(full_name, phone, vacancy_name, score_fit, score_quality, total_exp, analysis_text, resume_url):
    conn = sqlite3.connect('hr_assistant.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO candidates (full_name, phone, vacancy_name, score, score_quality, total_experience, analysis_text, resume_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (full_name, phone, vacancy_name, score_fit, score_quality, total_exp, analysis_text, resume_url))
    conn.commit()
    conn.close()

def get_candidates_df(vacancy_name_part):
    """Получает данные кандидатов и возвращает их в виде DataFrame."""
    conn = sqlite3.connect('hr_assistant.db')
    
    # Используем SQL-запрос для получения данных по маске названия
    query = '''
        SELECT full_name as "ФИО", 
               phone as "Телефон", 
               total_experience as "Общий стаж (лет)",
               score as "Соответствие (0-10)", 
               score_quality as "Качество резюме (0-10)",
               resume_url as "Ссылка",
               analysis_text as "Анализ ИИ"
        FROM candidates 
        WHERE vacancy_name LIKE ?
    '''
    df = pd.read_sql_query(query, conn, params=(vacancy_name_part + '%',))
    conn.close()
    return df

def delete_vacancy_and_candidates(vacancy_name_part):
    """Удаляет вакансию и всех привязанных к ней кандидатов по части названия."""
    conn = sqlite3.connect('hr_assistant.db')
    cursor = conn.cursor()
    # Находим полное имя вакансии
    cursor.execute('SELECT name FROM vacancies WHERE name LIKE ?', (vacancy_name_part + '%',))
    res = cursor.fetchone()
    
    if res:
        full_name = res[0]
        # Удаляем кандидатов
        cursor.execute('DELETE FROM candidates WHERE vacancy_name = ?', (full_name,))
        # Удаляем саму вакансию
        cursor.execute('DELETE FROM vacancies WHERE name = ?', (full_name,))
        
    conn.commit()
    conn.close()