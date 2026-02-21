import os
import datetime
import io
import re
import asyncio
import logging
import sqlite3
import pandas as pd
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from dotenv import load_dotenv
import openai
import docx

# Импорты модулей
import database as db
from parse_hh import extract_vacancy_data, extract_resume_data, get_html
from pdf_resume_parser import extract_resume_data_from_pdf
from docx_resume_parser import extract_resume_data_from_docx

load_dotenv()

# Инициализация
bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
dp = Dispatcher()
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 1. Получаем реальный текущий год (2026)
current_year = datetime.datetime.now().year
# Состояния
class Form(StatesGroup):
    # Состояния для вакансии
    waiting_for_vacancy_type = State()
    waiting_for_vacancy_title = State()
    waiting_for_vacancy_data = State()  
    
    # Состояния для резюме
    waiting_for_resume_type = State()
    waiting_for_resume_data = State()
    
    # Состояния для сохранения в БД (новые)
    waiting_for_candidate_name = State()
    waiting_for_candidate_phone = State()

    waiting_for_multi_resumes = State() # Новое состояние для сбора группы резюме

VAC_GEN_PROMPT = """
Ты генератор описаний вакансий для банковского сектора.

ВАЖНО. Ты ОБЯЗАН строго соблюдать шаблон и правила ниже.

ПРАВИЛА:
1. Символы ### и двойные кавычки " являются СЛУЖЕБНЫМИ и используются ТОЛЬКО для понимания структуры.
2. В ИТОГОВОМ РЕЗУЛЬТАТЕ символов ### и " БЫТЬ НЕ ДОЛЖНО.
3. Весь текст, который в шаблоне заключён между ###, должен быть воспроизведён дословно, БЕЗ ИЗМЕНЕНИЙ, но БЕЗ символов ###.
4. Весь текст, который в шаблоне заключён в двойные кавычки ", должен быть сгенерирован тобой и выведен БЕЗ кавычек.
5. Запрещено:
   - менять порядок блоков
   - добавлять или удалять блоки
   - изменять формулировки фиксированного текста
6. Генерируемый текст должен быть:
   - профессиональным
   - соответствовать банковской вакансии
   - логически согласованным со всем текстом

Верни ТОЛЬКО итоговый текст вакансии. Без комментариев, пояснений и форматирования от себя.

ШАБЛОН (служебные символы НЕ выводить):

###ПЕРВОУРАЛЬСКБАНК— динамично развивающийся финансовый институт. Мы предлагаем нашим клиентам современные банковские решения, опираясь на передовые технологии и многолетний опыт работы на финансовом рынке. Мы помогаем нашим партнерам и клиентам решить задачи, связанные с международными платежами. Приглашаем на вакансию целеустремленного и активного### "..."

###Главная задача по данной вакансии:### "..."

###Обязанности:###
"..."

###Требования:###
"..."

###Мы предлагаем:
- Присоединиться к интересному и востребованному клиентами и партнерами направлению в банке.
- Официальное трудоустройство по ТК РФ
- Своевременную стабильную оплату труда, оклад+ премии по результатам работы.
- График работы 5/2 с гибким началом рабочего дня с 8-00 до 10-00
- Наш офис расположен в современном БЦ "Савеловский Сити", 5 мин. пеш. от м. Дмитровская, недалеко от авангардного арт-пространства Хлебзавод №9 и Дизайн-завода "Флакон".

Интересно!? Звоните! Пишите! Откликайтесь! Всегда готовы к обсуждению интересного и взаимовыгодного сотрудничества! ###
""".strip()

OCR_SYSTEM_PROMPT = """
Ты HR-ассистент и специалист по анализу резюме.

Тебе передан текст резюме, полученный с помощью OCR.
Текст может содержать ошибки распознавания, повторы строк и нарушенную структуру.

Твоя задача:
- восстановить структуру резюме
- исправить очевидные OCR-ошибки
- привести данные к логическому и читабельному виду

Верни результат строго в следующей структуре Markdown:

# ФИО: [Здесь ФИО]
**Телефон:** [Здесь номер телефона или "Не найдено"]
**Пол, возраст:** ...
**Местоположение:** ...
**Должность:** ...
**Статус:** ...

## Опыт работы

## Ключевые навыки

Если данные отсутствуют — укажи "Не найдено".
Не добавляй ничего от себя.
""".strip()

SYSTEM_PROMPT = """
Ты — эксперт HR. Сегодня {current_year} год. 
Проведи глубокий анализ кандидата на соответствие вакансии.
Сначала:
   - Выяви требуемый стаж из текста вакансии (например, 1 год, 3 года, 10 лет).

Твой ответ должен строго состоять из трех блоков:
1. АНАЛИЗ: Короткий анализ, который будет пояснять оценку.
2. КАЧЕСТВО РЕЗЮМЕ: Оцени, насколько понятно и структурно описаны задачи и достижения (0-10).
3. ОБЩЕЕ СООТВЕТСТВИЕ: Насколько кандидат подходит под требования вакансии (0-10).
4. РЕЗЮМЕ ЗА ТРЕБУЕМЫЙ ПЕРИОД: Если стаж указан перечисли компании, должности и ключевые результаты кандидата именно за это количество последних лет. Если стаж в вакансии не указан, проанализируй последние 3 года работы. Учти, что сейчас {current_year} год.

В самом конце ответа выведи оценки строго в этом формате:
Качество_резюме: X/10
Итоговый_результат: Y/10
""".strip()

# Промпт для создания вакансии на основе нескольких резюме
REVERSE_VACANCY_PROMPT = """
Ты — старший HR-методолог. Тебе прислали несколько резюме кандидатов. 
Твоя задача: проанализировать их функционал и составить идеальный список ОБЯЗАННОСТЕЙ для будущей вакансии.

Правила:
1. Выдели повторяющиеся задачи (базовый функционал).
2. Выдели уникальные компетенции, которые приносят пользу бизнесу.
3. Сформулируй обязанности профессиональным языком банковской сферы.
4. Структурируй результат по блокам задач.

ВАЖНО. Ты ОБЯЗАН строго соблюдать шаблон и правила ниже.

ПРАВИЛА:
1. Символы ### и двойные кавычки " являются СЛУЖЕБНЫМИ и используются ТОЛЬКО для понимания структуры.
2. В ИТОГОВОМ РЕЗУЛЬТАТЕ символов ### и " БЫТЬ НЕ ДОЛЖНО.
3. Весь текст, который в шаблоне заключён между ###, должен быть воспроизведён дословно, БЕЗ ИЗМЕНЕНИЙ, но БЕЗ символов ###.
4. Весь текст, который в шаблоне заключён в двойные кавычки ", должен быть сгенерирован тобой и выведен БЕЗ кавычек.
5. Запрещено:
   - менять порядок блоков
   - добавлять или удалять блоки
   - изменять формулировки фиксированного текста
6. Генерируемый текст должен быть:
   - профессиональным
   - соответствовать банковской вакансии
   - логически согласованным со всем текстом

Верни ТОЛЬКО итоговый текст вакансии. Без комментариев, пояснений и форматирования от себя.

ШАБЛОН (служебные символы НЕ выводить):

###Вакансия: ### "..."

###ПЕРВОУРАЛЬСКБАНК— динамично развивающийся финансовый институт. Мы предлагаем нашим клиентам современные банковские решения, опираясь на передовые технологии и многолетний опыт работы на финансовом рынке. Мы помогаем нашим партнерам и клиентам решить задачи, связанные с международными платежами. Приглашаем на вакансию целеустремленного и активного### "..."

###Главная задача по данной вакансии:### "..."

###Обязанности:###
"..."

###Требования:###
"..."

###Мы предлагаем:
- Присоединиться к интересному и востребованному клиентами и партнерами направлению в банке.
- Официальное трудоустройство по ТК РФ
- Своевременную стабильную оплату труда, оклад+ премии по результатам работы.
- График работы 5/2 с гибким началом рабочего дня с 8-00 до 10-00
- Наш офис расположен в современном БЦ "Савеловский Сити", 5 мин. пеш. от м. Дмитровская, недалеко от авангардного арт-пространства Хлебзавод №9 и Дизайн-завода "Флакон".

Интересно!? Звоните! Пишите! Откликайтесь! Всегда готовы к обсуждению интересного и взаимовыгодного сотрудничества! ###
""".strip()

# --- Клавиатуры ---
def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ Установить вакансию", callback_data="set_vacancy")],
        [InlineKeyboardButton(text="2️⃣ Загрузить резюме", callback_data="set_resume")],
        [InlineKeyboardButton(text="📊 Анализ и сохранение", callback_data="run_analysis")],
        [InlineKeyboardButton(text="📋 Список кандидатов", callback_data="view_candidates")],
        [InlineKeyboardButton(text="🗑 Закрыть вакансию", callback_data="close_vacancy")]
    ])

def vacancy_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍ По названию", callback_data="vac_gen")],
        [InlineKeyboardButton(text="📄 Текст", callback_data="vac_text")],
        [InlineKeyboardButton(text="🔗 HH.ru", callback_data="vac_hh")],
        [InlineKeyboardButton(text="📁 Из базы", callback_data="vac_db")],
        [InlineKeyboardButton(text="🪄 Вакансия из резюме", callback_data="reverse_vac")]
    ])

def resume_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📎 Файл (PDF / Word)", callback_data="res_pdf")],
        [InlineKeyboardButton(text="📝 Текст", callback_data="res_text")],
        [InlineKeyboardButton(text="🔗 HH.ru", callback_data="res_hh")]
    ])

# --- Вспомогательные функции ---

def extract_analysis_data(text):
    """
    Адресное извлечение данных. 
    Ищет цифры строго после определенных ключевых слов.
    """
    # Ищем качество резюме (первая оценка)
    quality_match = re.search(r'(?:Качество_резюме|КАЧЕСТВО_Р):\s*(\d+)', text, re.IGNORECASE)
    # Ищем итоговый результат (вторая оценка, которая нам нужна в базу)
    fit_match = re.search(r'(?:Итоговый_результат|ОБЩЕЕ_С):\s*(\d+)', text, re.IGNORECASE)
    # Ищем стаж
    exp_match = re.search(r'ОБЩИЙ_СТАЖ:\s*(\d+)', text, re.IGNORECASE)

    score_q = quality_match.group(1) if quality_match else "0"
    score_f = fit_match.group(1) if fit_match else "0"
    total_exp = exp_match.group(1) if exp_match else "Не определен"
    
    return score_q, score_f, total_exp

def extract_info(text, pattern):
    match = re.search(pattern, text)
    return match.group(1).strip() if match else "Не определено"

# --- Хендлеры ---

def escape_markdown(text):
    """Экранирует спецсимволы, чтобы Telegram не 'падал' при парсинге."""
    # Для Markdown V1 нужно экранировать как минимум эти символы:
    parse_chars = r'([_*`\[])'
    return re.sub(parse_chars, r'\\\1', text)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Привет! Я AI HR-ассистент.\nВыберите действие:", reply_markup=main_menu_kb())

@dp.callback_query(F.data == "start")
async def back_to_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    """Возвращает пользователя в главное меню и очищает состояние."""
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    await callback.answer()

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer("📖 Инструкция:\n1. Установите вакансию.\n2. Загрузите резюме.\n3. Нажмите анализ — данные сохранятся в базу автоматически.")

# --- Блок Вакансии ---

@dp.callback_query(F.data == "set_vacancy")
async def select_vac_method(callback: types.CallbackQuery):
    await callback.message.edit_text("Способ добавления вакансии:", reply_markup=vacancy_type_kb())

@dp.callback_query(F.data == "vac_db")
async def list_vacancies_from_db(callback: types.CallbackQuery):
    vacs = db.get_vacancies() # Получаем список имен из БД
    if not vacs:
        await callback.answer("База вакансий пуста.", show_alert=True); return
    btns = [[InlineKeyboardButton(text=v, callback_data=f"selvac_{v[:20]}")] for v in vacs]
    await callback.message.edit_text("Выберите сохраненную вакансию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("selvac_"))
async def process_vac_selection(callback: types.CallbackQuery, state: FSMContext):
    part = callback.data.replace("selvac_", "")
    conn = sqlite3.connect('hr_assistant.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name, description FROM vacancies WHERE name LIKE ?', (part + '%',))
    res = cursor.fetchone()
    conn.close()
    if res:
        await state.update_data(job_title=res[0], job_text=res[1])
        await callback.message.answer(f"✅ Выбрана вакансия: **{res[0]}**", reply_markup=main_menu_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith("vac_"))
async def process_vac_method(callback: types.CallbackQuery, state: FSMContext):
    method = callback.data
    await state.update_data(vac_method=method)
    if method == "vac_text":
        await callback.message.answer("Введите **название** вакансии:")
        await state.set_state(Form.waiting_for_vacancy_title)
    else:
        await callback.message.answer("Введите название для генерации или ссылку HH:")
        await state.set_state(Form.waiting_for_vacancy_data)
    await callback.answer()

@dp.message(Form.waiting_for_vacancy_title)
async def process_manual_title(message: types.Message, state: FSMContext):
    await state.update_data(job_title=message.text)
    await message.answer(f"✅ Название принято. Теперь пришлите **текст** вакансии:")
    await state.set_state(Form.waiting_for_vacancy_data)

@dp.message(Form.waiting_for_vacancy_data)
async def handle_vacancy_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    method = data.get("vac_method")
    try:
        title, text = "", ""
        if method == "vac_gen":
            title = message.text
            res = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"system","content":VAC_GEN_PROMPT},{"role":"user","content":title}])
            text = res.choices[0].message.content
            await message.answer(f"Черновик:\n`{text}`", parse_mode="Markdown")
            await message.answer("Пришлите итоговый вариант текста вакансии:")
            await state.update_data(vac_method="vac_text", job_title=title)
            return
        elif method == "vac_text":
            title = data.get("job_title")
            text = message.text
        elif method == "vac_hh":
            html = get_html(message.text)
            text = extract_vacancy_data(html)
            title = text.split('\n')[0].replace('#', '').strip()
        
        db.save_vacancy(title, text) # Сохраняем в таблицу vacancies
        await state.update_data(job_title=title, job_text=text)
        await message.answer(f"🎯 Вакансия '{title}' сохранена!", reply_markup=main_menu_kb())
        await state.set_state(None)
    except Exception as e: await message.answer(f"❌ Ошибка: {e}")

# --- Блок Резюме ---

@dp.callback_query(F.data == "set_resume")
async def select_res_method(callback: types.CallbackQuery):
    await callback.message.edit_text("Способ добавления резюме:", reply_markup=resume_type_kb())

@dp.callback_query(F.data.startswith("res_"))
async def process_res_method(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(res_method=callback.data)
    await callback.message.answer("Пришлите файл, текст или ссылку:")
    await state.set_state(Form.waiting_for_resume_data)
    await callback.answer()

@dp.message(Form.waiting_for_resume_data)
async def handle_resume_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    method = data.get("res_method")
    try:
        resume_text, resume_url = "", "Загружено вручную"
        
        # Обработка файлов (PDF или DOCX)
        if message.document:
            file_name = message.document.file_name.lower()
            file_content = await bot.download(message.document)
            file_bytes = file_content.read()

            if file_name.endswith('.pdf'):
                # Ваш существующий парсер для PDF
                resume_text = extract_resume_data_from_pdf(file_bytes, client, OCR_SYSTEM_PROMPT)
            
            elif file_name.endswith('.docx') or file_name.endswith('.doc'):
                # Читаем текст из Word
                from docx_resume_parser import extract_resume_data_from_docx
                raw_docx_text = extract_resume_data_from_docx(file_bytes)
                
                # Просим ИИ привести "сырой" текст из Word к нужному нам формату
                # Это гарантирует, что в тексте появятся метки # ФИО и **Телефон**
                res = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": OCR_SYSTEM_PROMPT},
                        {"role": "user", "content": raw_docx_text}
                    ]
                )
                resume_text = res.choices[0].message.content
            else:
                await message.answer("❌ Формат не поддерживается. Пришлите PDF или DOCX.")
                return

        elif method == "res_text":
            resume_text = message.text
        
        elif method == "res_hh":
            resume_url = message.text
            resume_text = extract_resume_data(resume_url)

        if resume_text:
            await state.update_data(resume_text=resume_text, resume_url=resume_url)
            await message.answer("✅ Резюме (Word/PDF) успешно обработано!", reply_markup=main_menu_kb())
        else:
            await message.answer("⚠️ Не удалось извлечь данные. Попробуйте другой файл.")

    except Exception as e: 
        await message.answer(f"❌ Ошибка при обработке: {e}")
    
    await state.set_state(None)

# --- Анализ и база ---

@dp.callback_query(F.data == "run_analysis")
async def run_analysis(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    job, resume, title = data.get("job_text"), data.get("resume_text"), data.get("job_title")
    url = data.get("resume_url", "Нет ссылки")
    
    if not job or not resume:
        await callback.answer("⚠️ Нет данных для анализа!", show_alert=True); return
    
    await callback.message.answer("⌛ Анализирую...")
    try:
        res = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":f"В:{job}\nР:{resume}"}])
        analysis = res.choices[0].message.content
        name = extract_info(resume, r"# ФИО:\s*(.*)")
        phone = extract_info(resume, r"\*\*Телефон:\*\*\s*(.*)")
        score_q, score_f, total_exp = extract_analysis_data(analysis)
        
        db.add_candidate(name, phone, title, f"{score_f}/10", f"{score_q}/10", total_exp, analysis, url)
        safe_analysis = escape_markdown(analysis) # Экранируем текст от ИИ
        await callback.message.answer(f"📊 **Анализ {name}:**\n\n{safe_analysis}", parse_mode="Markdown")
        await callback.message.answer("✅ Результат сохранен.", reply_markup=main_menu_kb())
    except Exception as e: await callback.message.answer(f"❌ Ошибка анализа: {e}")
    await callback.answer()

@dp.callback_query(F.data == "view_candidates")
async def show_vac_list(callback: types.CallbackQuery):
    vacs = db.get_vacancies()
    if not vacs: await callback.answer("База пуста", show_alert=True); return
    btns = [[InlineKeyboardButton(text=v, callback_data=f"list_{v[:20]}")] for v in vacs]
    await callback.message.edit_text("Список кандидатов по вакансии:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("list_"))
async def show_cands(callback: types.CallbackQuery):
    part = callback.data.replace("list_", "")
    conn = sqlite3.connect('hr_assistant.db')
    cursor = conn.cursor()
    cursor.execute('SELECT full_name, phone, score, resume_url FROM candidates WHERE vacancy_name LIKE ?', (part + '%',))
    cands = cursor.fetchall()
    conn.close()
    
    if not cands: await callback.answer("Кандидатов нет."); return
    
    text = "👥 **Результаты анализа:**\n\n" + "\n".join([f"👤 {c[0]} ({c[2]})\n📞 {c[1]}\n🔗 {c[3]}\n---" for c in cands])
    
    # Новая клавиатура со скачиванием
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать Excel", callback_data=f"excel_{part}")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="start")]
    ])
    
    await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("excel_"))
async def export_to_excel(callback: types.CallbackQuery):
    part = callback.data.replace("excel_", "")
    await callback.answer("⏳ Генерирую файл...")
    
    conn = sqlite3.connect('hr_assistant.db')
    query = '''
        SELECT full_name as "ФИО", 
               phone as "Телефон", 
               vacancy_name as "Вакансия", 
               score as "Оценка", 
               resume_url as "Ссылка",
               analysis_text as "Анализ ИИ"
        FROM candidates 
        WHERE vacancy_name LIKE ?
    '''
    df = pd.read_sql_query(query, conn, params=(part + '%',))
    conn.close()
    
    if df.empty:
        await callback.message.answer("❌ Данные не найдены."); return

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Кандидаты')
    output.seek(0)
    
    file_name = f"Candidates_{part[:15]}.xlsx"
    input_file = BufferedInputFile(output.read(), filename=file_name)
    
    await callback.message.answer_document(
        document=input_file,
        caption=f"📊 Выгружен список по вакансии: **{df['Вакансия'].iloc[0]}**",
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "close_vacancy")
async def show_del_list(callback: types.CallbackQuery):
    vacs = db.get_vacancies()
    if not vacs: await callback.answer("Нет вакансий", show_alert=True); return
    btns = [[InlineKeyboardButton(text=f"🗑 {v}", callback_data=f"del_{v[:20]}")] for v in vacs]
    await callback.message.edit_text("Выберите вакансию для УДАЛЕНИЯ:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("del_"))
async def process_delete(callback: types.CallbackQuery):
    part = callback.data.replace("del_", "")
    db.delete_vacancy_and_candidates(part)
    await callback.answer("✅ Вакансия и кандидаты удалены.", show_alert=True)
    await callback.message.edit_text("Готово. Что дальше?", reply_markup=main_menu_kb())

@dp.callback_query(F.data == "reverse_vac")
async def start_reverse_vac(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_for_multi_resumes)
    await state.update_data(temp_resumes=[]) # Создаем пустой список для сбора
    await callback.message.answer(
        "📥 Пришлите несколько резюме (PDF или текст) по очереди.\n\n"
        "Когда закончите, нажмите кнопку ниже 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✨ Сформировать обязанности", callback_data="generate_reverse_vac")]
        ])
    )
    await callback.answer()

# 2. Сбор файлов и текста (хендлер для состояния waiting_for_multi_resumes)
@dp.message(Form.waiting_for_multi_resumes)
async def collect_resumes(message: types.Message, state: FSMContext):
    data = await state.get_data()
    temp_resumes = data.get("temp_resumes", [])
    
    text_to_add = ""
    
    if message.document and message.document.mime_type == 'application/pdf':
        file_content = await bot.download(message.document)
        text_to_add = extract_resume_data_from_pdf(file_content.read(), client, OCR_SYSTEM_PROMPT)
        await message.answer(f"✅ Файл '{message.document.file_name}' добавлен.")
    elif message.text:
        text_to_add = message.text
        await message.answer("✅ Текст добавлен.")
    
    if text_to_add:
        temp_resumes.append(text_to_add)
        await state.update_data(temp_resumes=temp_resumes)
    
    await message.answer(f"В списке уже {len(temp_resumes)} резюме. Пришлите еще или нажмите кнопку выше для генерации.")

# 3. Финальная генерация
@dp.callback_query(F.data == "generate_reverse_vac")
async def generate_reverse_vac(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    resumes = data.get("temp_resumes", [])
    
    if len(resumes) < 2:
        await callback.answer("⚠️ Пришлите хотя бы 2 резюме для анализа!", show_alert=True)
        return

    await callback.message.answer("⌛ Нейросеть изучает опыт кандидатов и формирует список задач...")
    
    combined_text = "\n\n--- СЛЕДУЮЩЕЕ РЕЗЮМЕ ---\n\n".join(resumes)
    
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": REVERSE_VACANCY_PROMPT},
                {"role": "user", "content": f"Вот резюме для анализа:\n{combined_text}"}
            ]
        )
        final_duties = res.choices[0].message.content
        
        # Сохраняем черновик в FSM, чтобы пользователь мог его потом сохранить как вакансию
        await state.update_data(last_gen_vac=final_duties)
        
        await callback.message.answer(f"📋 **Сформированные обязанности:**\n\n{final_duties}")
        await callback.message.answer(
            "Вы можете скопировать этот текст или использовать его для создания новой вакансии.",
            reply_markup=main_menu_kb()
        )
        await state.set_state(None)
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка генерации: {e}")
    
    await callback.answer()

async def main():
    db.init_db()
    await bot.set_my_commands([types.BotCommand(command="start", description="Меню"), types.BotCommand(command="help", description="Помощь")])
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())