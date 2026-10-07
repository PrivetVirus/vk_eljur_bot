import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.upload import VkUpload
import requests
import sqlite3
import csv
import time
import re

# ================= НАСТРОЙКИ =================
VK_TOKEN = "YOUR_VK_TOKEN_HERE"
DEVKEY = "9235e26e80ac2c509c48fe62db23642c"
# =============================================

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'bot_database.db')
CSV_PATH = os.path.join(BASE_DIR, 'users_data.csv')
PHOTO_PATH = os.path.join(BASE_DIR, 'hint.png')

# --- 1. БАЗА ДАННЫХ И EXCEL ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            vk_user_id INTEGER PRIMARY KEY,
            step TEXT,
            vendor TEXT,
            login TEXT,
            password TEXT,
            student_id TEXT
        )
    ''')
    conn.commit()
    return conn

def export_to_excel(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    rows = cursor.fetchall()
    with open(CSV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(['ВК ID', 'Текущий шаг', 'Домен', 'Логин', 'Пароль', 'ID Ученика'])
        writer.writerows(rows)

def get_user(conn, user_id):
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE vk_user_id = ?', (user_id,))
    return cursor.fetchone()

def update_user(conn, user_id, step, vendor=None, login=None, password=None, student_id=None):
    cursor = conn.cursor()
    if get_user(conn, user_id) is None:
        cursor.execute('INSERT INTO users (vk_user_id, step) VALUES (?, ?)', (user_id, step))
    else:
        cursor.execute('UPDATE users SET step = ? WHERE vk_user_id = ?', (step, user_id))
        if vendor: cursor.execute('UPDATE users SET vendor = ? WHERE vk_user_id = ?', (vendor, user_id))
        if login: cursor.execute('UPDATE users SET login = ? WHERE vk_user_id = ?', (login, user_id))
        if password: cursor.execute('UPDATE users SET password = ? WHERE vk_user_id = ?', (password, user_id))
        if student_id: cursor.execute('UPDATE users SET student_id = ? WHERE vk_user_id = ?', (student_id, user_id))
    conn.commit()
    export_to_excel(conn)

# --- 2. КЛАВИАТУРЫ ВК ---
def get_main_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button('Статистика', color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button('Сброс', color=VkKeyboardColor.SECONDARY)
    return keyboard.get_keyboard()

def get_start_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button('Сброс', color=VkKeyboardColor.SECONDARY)
    return keyboard.get_keyboard()

def get_empty_keyboard():
    return VkKeyboard.get_empty_keyboard()

def get_schools_keyboard(options):
    """Генерирует ту самую панель с выбором найденных школ!"""
    keyboard = VkKeyboard(one_time=True) # Исчезнет после выбора
    count = 0
    for opt in options:
        if count >= 4: break # Максимум 4 кнопки, чтобы не ломать интерфейс ВК
        domain = opt['domain']
        keyboard.add_button(domain, color=VkKeyboardColor.PRIMARY)
        keyboard.add_line()
        count += 1
    
    # Добавляем кнопку "Сброс" в самый низ для безопасности
    keyboard.add_button('Сброс', color=VkKeyboardColor.SECONDARY)
    return keyboard.get_keyboard()


def auto_get_student_id(vendor, login, password):
    auth_url = f"https://{vendor}.eljur.ru/api/auth"
    auth_data = {'devkey': DEVKEY, 'vendor': vendor, 'login': login, 'password': password, 'out_format': 'json'}
    try:
        auth_response = requests.post(auth_url, json=auth_data).json()
        if 'error' in auth_response: return None
        token = auth_response['response']['result']['token']
        rules_url = f"https://{vendor}.eljur.ru/api/getrules"
        rules_res = requests.get(rules_url, params={'devkey': DEVKEY, 'vendor': vendor, 'auth_token': token, 'out_format': 'json'}).json()
        students = rules_res.get('response', {}).get('result', {}).get('relations', {}).get('students', {})
        if students: return list(students.keys())[0]
        return None
    except: return None

def calculate_fives(grades, target=4.6):
    valid_grades = [int(g) for g in grades if str(g).isdigit()]
    if not valid_grades: return 0, 0.0
    current_sum = sum(valid_grades)
    count = len(valid_grades)
    current_avg = current_sum / count
    if current_avg >= target: return 0, current_avg
    fives_needed = 0
    while (current_sum + fives_needed * 5) / (count + fives_needed) < target:
        fives_needed += 1
    return fives_needed, current_avg

def get_stats_message(vendor, login, password, student_id):
    auth_url = f"https://{vendor}.eljur.ru/api/auth"
    auth_data = {'devkey': DEVKEY, 'vendor': vendor, 'login': login, 'password': password, 'out_format': 'json'}
    try:
        auth_response = requests.post(auth_url, json=auth_data).json()
        if 'error' in auth_response: return "❌ Ошибка авторизации. Возможно, пароль был изменен."
        token = auth_response['response']['result']['token']
        
        marks_url = f"https://{vendor}.eljur.ru/api/getmarks"
        params = {'devkey': DEVKEY, 'vendor': vendor, 'auth_token': token, 'student': student_id, 'out_format': 'json', 'days': '20260101-20260531'}
        marks_response = requests.get(marks_url, params=params).json()
        
        real_grades = {}
        students_data = marks_response.get('response', {}).get('result', {}).get('students', {})
        student_info = students_data.get(str(student_id), {})
        
        for lesson in student_info.get('lessons', []):
            subject_name = lesson.get('name', 'Неизвестный предмет')
            valid_marks = [int(str(m.get('value'))) for m in lesson.get('marks', []) if str(m.get('value')).isdigit()]
            if valid_marks: real_grades[subject_name] = valid_marks
            
        if not real_grades: return "🤷‍♂️ Оценок пока нет."
        
        result_msg = "📊 Твоя статистика успеваемости:\n\n"
        for subject, grades in real_grades.items():
            fives, avg = calculate_fives(grades, target=4.6)
            result_msg += f"📚 {subject}:\nОценки: {grades}\nСредний балл: {avg:.2f}\n"
            if fives == 0: result_msg += "✅ Отлично, так держать!\n\n"
            else: result_msg += f"⚠️ До '5' нужно пятерок: {fives} шт.\n\n"
        return result_msg
    except Exception:
        return "❌ Произошла техническая ошибка при связи с ЭлЖуром."
# --- 4. БЕССМЕРТНЫЙ ЦИКЛ БОТА ---
def send_welcome_instruction(api, user_id, upload):
    """Отправляет красивую инструкцию с картинкой"""
    msg = ("Привет! 🎓\nДля настройки мне нужен твой домен (адрес) ЭлЖура.\n\n"
           "🔍 Посмотри на строку браузера, когда открываешь свой дневник. "
           "То, что стоит перед '.eljur.ru' — это и есть твой домен.\n"
           "Например, если сайт kip.eljur.ru, то просто напиши мне kip (как на фото ниже).\n\n"
           "Жду твой домен АНГЛИЙСКИМИ буквами без пробелов:")
    
    try:
        photo = upload.photo_messages(PHOTO_PATH)[0] # Убедись, что файл называется hint.png
        attachment = f"photo{photo['owner_id']}_{photo['id']}"
    except Exception as e:
        print(f"Не удалось загрузить картинку: {e}")
        attachment = None
        
    api.messages.send(user_id=user_id, message=msg, random_id=0, attachment=attachment, keyboard=get_empty_keyboard())

def main():
    db_conn = init_db()
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    api = vk_session.get_api()
    upload = VkUpload(vk_session) # Инициализируем загрузчик фото
    
    print("🚀 Бот 5.2 FINAL запущен!")
    
    while True:
        try:
            longpoll = VkLongPoll(vk_session)
            for event in longpoll.listen():
                if event.type == VkEventType.MESSAGE_NEW and event.to_me:
                    user_msg = event.text.strip()
                    user_msg_lower = user_msg.lower()
                    user_id = event.user_id
                    user_data = get_user(db_conn, user_id)
                    
                    # Обработка команды Сброс
                    if user_msg_lower in ["сброс", "заново", "очистить"]:
                        update_user(db_conn, user_id, step='wait_vendor')
                        api.messages.send(user_id=user_id, message="🔄 Данные сброшены.", random_id=0)
                        send_welcome_instruction(api, user_id, upload) # Шлем инструкцию с фото!
                        continue
                    
                    # НОВЫЙ ПОЛЬЗОВАТЕЛЬ -> ШЛЕМ ИНСТРУКЦИЮ С ФОТО
                    if user_data is None:
                        update_user(db_conn, user_id, step='wait_vendor')
                        send_welcome_instruction(api, user_id, upload)
                        continue
                        
                    step = user_data[1] 
                    
                    if step == 'wait_vendor':
                        # Очищаем от мусора
                        domain = user_msg_lower.replace('https://', '').replace('http://', '').replace('.eljur.ru', '').replace('/', '').strip()
                        
                        # ЖЕСТКАЯ ПРОВЕРКА: только английские буквы, цифры и дефис
                        if not re.match(r'^[a-z0-9\-]+$', domain):
                            api.messages.send(
                                user_id=user_id, 
                                message="❌ Ошибка! Домен может состоять ТОЛЬКО из английских букв и цифр без пробелов.\nПопробуй еще раз (например: kip):", 
                                random_id=0, 
                                keyboard=get_empty_keyboard()
                            )
                            continue # Прерываем и ждем новый ответ
                        
                        update_user(db_conn, user_id, step='wait_login', vendor=domain)
                        api.messages.send(user_id=user_id, message=f"✅ Домен [{domain}] принят!\nТеперь отправь свой ЛОГИН от ЭлЖура:", random_id=0, keyboard=get_start_keyboard())
                        
                    elif step == 'wait_login':
                        update_user(db_conn, user_id, step='wait_password', login=user_msg)
                        api.messages.send(user_id=user_id, message="Супер. И последнее: отправь ПАРОЛЬ:", random_id=0, keyboard=get_start_keyboard())
                        
                    elif step == 'wait_password':
                        password = user_msg
                        vendor = user_data[2]
                        login = user_data[3]
                        
                        api.messages.send(user_id=user_id, message="⏳ Подключаюсь к ЭлЖуру...", random_id=0, keyboard=get_empty_keyboard())
                        student_id = auto_get_student_id(vendor, login, password)
                        
                        if student_id:
                            update_user(db_conn, user_id, step='registered', password=password, student_id=student_id)
                            api.messages.send(user_id=user_id, message="✅ Успешно!\nТеперь просто жми на кнопку 'Статистика' 👇", random_id=0, keyboard=get_main_keyboard())
                        else:
                            api.messages.send(user_id=user_id, message="❌ Не удалось войти. Проверь логин/пароль. Нажми 'Сброс', чтобы попробовать снова.", random_id=0, keyboard=get_start_keyboard())

                    elif step == 'registered':
                        if user_msg_lower in ["статистика", "стата"]:
                            api.messages.setActivity(type='typing', peer_id=user_id)
                            reply = get_stats_message(user_data[2], user_data[3], user_data[4], user_data[5]) 
                            api.messages.send(user_id=user_id, message=reply, random_id=0, keyboard=get_main_keyboard())
                        else:
                            api.messages.send(user_id=user_id, message="Жми на кнопку 'Статистика' 📊\n(Или 'Сброс' для смены аккаунта)", random_id=0, keyboard=get_main_keyboard())

        except requests.exceptions.ConnectionError:
            print("❌ Потеряно соединение с интернетом. Переподключение через 10 сек...")
            time.sleep(10)
        except requests.exceptions.ReadTimeout:
            print("⚠️ Сервер ВК не ответил. Переподключение...")
            time.sleep(3)
        except Exception as e:
            print(f"🔥 Ошибка: {e}. Перезапускаюсь через 5 сек...")
            time.sleep(5)

if __name__ == '__main__':
    main()