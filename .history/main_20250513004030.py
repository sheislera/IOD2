from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import random
import os
import json
import plotly
import plotly.graph_objs as go
from functools import wraps
import hashlib

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # В реальному додатку використовуйте надійний секретний ключ

# Функція для хешування паролю
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# Функція-декоратор для перевірки прав адміністратора
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Доступ заборонено. Необхідні права адміністратора.')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        if password != confirm_password:
            flash('Паролі не співпадають')
            return redirect(url_for('register'))
        
        with sqlite3.connect("voting.db") as conn:
            cursor = conn.cursor()
            
            # Перевіряємо, чи існує вже такий експерт
            cursor.execute("SELECT 1 FROM experts WHERE name = ?", (username,))
            if cursor.fetchone():
                flash('Експерт з таким іменем вже існує')
                return redirect(url_for('register'))
            
            # Додаємо нового експерта з хешованим паролем
            cursor.execute("""
                INSERT INTO experts (name, password) 
                VALUES (?, ?)
            """, (username, hash_password(password)))
            conn.commit()
            
            flash('Реєстрація успішна! Тепер ви можете увійти.')
            return redirect(url_for('expert_login'))
    
    return render_template('register.html')

@app.route('/expert_login', methods=['GET', 'POST'])
def expert_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        with sqlite3.connect("voting.db") as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name 
                FROM experts 
                WHERE name = ? AND password = ?
            """, (username, hash_password(password)))
            expert = cursor.fetchone()
            
            if expert:
                session['expert_id'] = expert[0]
                session['expert_name'] = expert[1]
                flash('Ви успішно увійшли!')
                return redirect(url_for('vote'))
            else:
                flash('Неправильне ім\'я або пароль')
    
    return render_template('expert_login.html')

@app.route('/expert_logout')
def expert_logout():
    session.pop('expert_id', None)
    session.pop('expert_name', None)
    return redirect(url_for('vote'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        # В реальному додатку використовуйте безпечне зберігання паролів
        if username == 'admin' and password == 'admin':
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Неправильні дані для входу')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('vote'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    with sqlite3.connect("voting.db") as conn:
        cursor = conn.cursor()
        
        # Отримуємо всі результати для адміністратора
        cursor.execute("""
            SELECT e.name AS expert_name, 
                   p1.name AS choice1_name, 
                   p2.name AS choice2_name, 
                   p3.name AS choice3_name
            FROM votes v
            JOIN experts e ON v.expert_id = e.id
            JOIN performers p1 ON v.choice1 = p1.id
            JOIN performers p2 ON v.choice2 = p2.id
            JOIN performers p3 ON v.choice3 = p3.id
            ORDER BY e.name
        """)
        votes = cursor.fetchall()

        # Підрахунок загального рейтингу
        cursor.execute("""
            SELECT 
                p.name,
                COUNT(CASE WHEN v.choice1 = p.id THEN 1 END) as first_places,
                COUNT(CASE WHEN v.choice2 = p.id THEN 1 END) as second_places,
                COUNT(CASE WHEN v.choice3 = p.id THEN 1 END) as third_places
            FROM performers p
            LEFT JOIN votes v ON p.id = v.choice1 OR p.id = v.choice2 OR p.id = v.choice3
            GROUP BY p.id, p.name
            ORDER BY 
                first_places DESC,
                second_places DESC,
                third_places DESC,
                p.name ASC
        """)
        total_results = cursor.fetchall()

        # Отримуємо статистику по евристикам
        cursor.execute("""
            SELECT 
                h.name, 
                h.description,
                COUNT(DISTINCT hv.expert_id) as votes,
                ROUND(AVG(CASE WHEN hv.priority IS NOT NULL THEN hv.priority ELSE 0 END), 2) as avg_priority
            FROM heuristics h
            LEFT JOIN heuristic_votes hv ON h.id = hv.heuristic_id
            GROUP BY h.id, h.name, h.description
            ORDER BY votes DESC, avg_priority ASC
        """)
        
        heuristics_ranking = []
        for row in cursor.fetchall():
            heuristics_ranking.append({
                'name': row[0],
                'description': row[1],
                'votes': row[2],
                'avg_priority': row[3]
            })

        plot_json = create_heuristics_plot(cursor)
        
        return render_template('admin_dashboard.html',
                             votes=votes,
                             total_results=total_results,
                             heuristics_ranking=heuristics_ranking,
                             plot_json=plot_json)

@app.route('/', methods=['GET', 'POST'])
def vote():
    with sqlite3.connect("voting.db") as conn:
        cursor = conn.cursor()
        
        if request.method == 'POST':
            expert = request.form['expert']
            choice1 = int(request.form['choice1'])
            choice2 = int(request.form['choice2'])
            choice3 = int(request.form['choice3'])
            
            if len(set([choice1, choice2, choice3])) != 3:
                flash("Помилка: Ви не можете вибрати одного виконавця декілька разів")
                return redirect(url_for('vote'))
            
            cursor.execute("""
                INSERT OR REPLACE INTO votes (expert_id, choice1, choice2, choice3)
                SELECT e.id, ?, ?, ?
                FROM experts e
                WHERE e.name = ?
            """, (choice1, choice2, choice3, expert))
            
            conn.commit()
            flash("Дякуємо за ваш голос!")
            return redirect(url_for('vote_heuristics'))

        # Отримуємо виконавців
        cursor.execute("SELECT id, name FROM performers ORDER BY name")
        performers = cursor.fetchall()

        # Отримуємо експертів
        cursor.execute("SELECT name FROM experts ORDER BY name")
        experts = [row[0] for row in cursor.fetchall()]

        return render_template('vote.html',
                             performers=performers,
                             experts=experts)


# Ініціалізація бази даних
def init_db():
    with sqlite3.connect("voting.db") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS performers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS experts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expert_id INTEGER UNIQUE, 
                choice1 INTEGER,
                choice2 INTEGER,
                choice3 INTEGER,
                FOREIGN KEY(expert_id) REFERENCES experts(id),
                FOREIGN KEY(choice1) REFERENCES performers(id),
                FOREIGN KEY(choice2) REFERENCES performers(id),
                FOREIGN KEY(choice3) REFERENCES performers(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS heuristics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS heuristic_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expert_id INTEGER,
                heuristic_id INTEGER,
                priority INTEGER CHECK(priority IN (1, 2, 3)),
                FOREIGN KEY(expert_id) REFERENCES experts(id),
                FOREIGN KEY(heuristic_id) REFERENCES heuristics(id),
                UNIQUE(expert_id, priority)
            )
        """)
        conn.commit()


# Додаємо список виконавців та експертів
def seed_data():
    performers = [
        "dity inzheneriv", "SadSvit", "badactress", "jockii druce", "Do Sliz", "Boombox", "Nikow", "МУР",
        "The Curly", "I Hate Myself Because", "NAZVA", "Ziferblat", "typeled", "Твій зайчик пише", "Luna Rozza",
        "Tartak", "Скажи щось погане", "СТРУКТУРА ЩАСТЯ", "Epolets", "The Unsleeping"
    ]

    experts = [
        "Артем", "Назар", "Настя", "Юля", "Коля", "Віра", "Надія", "Любов", "Олексій", "Олег",
        "Марія", "Лера", "Олена", "Іван", "Олександр", "Валя", "Даня", "Марина", "Злата", "Костя"
    ]

    with sqlite3.connect("voting.db") as conn:
        cursor = conn.cursor()
        for performer in performers:
            cursor.execute("INSERT OR IGNORE INTO performers (name) VALUES (?)", (performer,))
        
        # Додаємо експертів з хешованими паролями (за замовчуванням пароль = ім'я)
        for expert in experts:
            cursor.execute("""
                INSERT OR IGNORE INTO experts (name, password) 
                VALUES (?, ?)
            """, (expert, hash_password(expert)))
        conn.commit()


# Генеруємо рандомні голоси для експертів
def generate_random_votes():
    with sqlite3.connect("voting.db") as conn:
        cursor = conn.cursor()

        # Перевіряємо, чи є вже голоси в базі даних
        cursor.execute("SELECT COUNT(*) FROM votes")
        existing_votes = cursor.fetchone()[0]

        if existing_votes == 0:  # Якщо голосів немає, генеруємо нові
            cursor.execute("SELECT id FROM performers")
            performers = [row[0] for row in cursor.fetchall()]

            cursor.execute("SELECT id FROM experts")
            experts = [row[0] for row in cursor.fetchall()]

            for expert in experts:
                # Рандомно вибираємо 3 виконавців для голосування
                random_choices = random.sample(performers, 3)
                cursor.execute("INSERT INTO votes (expert_id, choice1, choice2, choice3) VALUES (?, ?, ?, ?)",
                             (expert, *random_choices))
            conn.commit()


def clean_duplicates():
    with sqlite3.connect("voting.db") as conn:
        cursor = conn.cursor()
        
        # Спочатку видалимо дублікати голосів
        cursor.execute("""
            DELETE FROM votes 
            WHERE id NOT IN (
                SELECT MIN(v.id)
                FROM votes v
                JOIN experts e ON v.expert_id = e.id
                GROUP BY e.name
            )
        """)
        
        # Потім видалимо дублікати експертів, залишивши записи з найменшими id
        cursor.execute("""
            DELETE FROM experts 
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM experts
                GROUP BY name
            )
        """)
        
        # Видалимо голоси експертів, яких вже немає
        cursor.execute("""
            DELETE FROM votes 
            WHERE expert_id NOT IN (
                SELECT id FROM experts
            )
        """)
        
        conn.commit()


def apply_heuristics(cursor):
    """Застосовує вибрані евристики та повертає результати фільтрації"""
    results = []
    
    # Отримуємо всіх виконавців
    cursor.execute("SELECT id, name FROM performers")
    all_performers = {row[0]: row[1] for row in cursor.fetchall()}
    
    # Початковий набір ID виконавців
    current_set = set(all_performers.keys())
    
    # Отримуємо останнього експерта, який голосував за евристики
    cursor.execute("""
        SELECT expert_id
        FROM heuristic_votes
        ORDER BY id DESC
        LIMIT 1
    """)
    last_expert = cursor.fetchone()
    
    if not last_expert:
        return [], list(all_performers.values())
        
    expert_id = last_expert[0]
    
    # Отримуємо вибрані евристики поточного експерта, відсортовані за пріоритетом
    cursor.execute("""
        SELECT DISTINCT h.id, h.name, h.description, hv.priority
        FROM heuristics h
        JOIN heuristic_votes hv ON h.id = hv.heuristic_id
        WHERE hv.expert_id = ?
        ORDER BY hv.priority ASC
    """, (expert_id,))
    selected_heuristics = cursor.fetchall()
    
    if not selected_heuristics:
        return [], list(all_performers.values())  # Якщо евристики не вибрані, повертаємо всіх виконавців
    
    for h_id, h_name, h_desc, priority in selected_heuristics:
        excluded = set()
        
        if h_name == "E1":
            cursor.execute("""
                SELECT p.id, p.name
                FROM performers p
                JOIN votes v ON p.id = v.choice3
                GROUP BY p.id, p.name
                HAVING COUNT(*) = 1
            """)
            excluded = {row[0] for row in cursor.fetchall()}
        elif h_name == "E2":
            cursor.execute("""
                SELECT p.id, p.name
                FROM performers p
                JOIN votes v ON p.id = v.choice2
                GROUP BY p.id, p.name
                HAVING COUNT(*) = 1
            """)
            excluded = {row[0] for row in cursor.fetchall()}
        elif h_name == "E3":
            cursor.execute("""
                SELECT p.id, p.name
                FROM performers p
                JOIN votes v ON p.id = v.choice1
                GROUP BY p.id, p.name
                HAVING COUNT(*) = 1
            """)
            excluded = {row[0] for row in cursor.fetchall()}
        elif h_name == "E4":
            cursor.execute("""
                SELECT p.id, p.name
                FROM performers p
                JOIN votes v ON p.id = v.choice3
                GROUP BY p.id, p.name
                HAVING COUNT(*) = 2
            """)
            excluded = {row[0] for row in cursor.fetchall()}
        elif h_name == "E5":
            cursor.execute("""
                SELECT p.id, p.name
                FROM performers p
                JOIN votes v1 ON p.id = v1.choice3
                JOIN votes v2 ON p.id = v2.choice2
                GROUP BY p.id, p.name
                HAVING COUNT(DISTINCT CASE WHEN p.id = v1.choice3 THEN v1.id END) = 1
                AND COUNT(DISTINCT CASE WHEN p.id = v2.choice2 THEN v2.id END) = 1
            """)
            excluded = {row[0] for row in cursor.fetchall()}
        elif h_name == "E6":
            cursor.execute("""
                SELECT p.id, p.name
                FROM performers p
                LEFT JOIN votes v ON p.id IN (v.choice1, v.choice2, v.choice3)
                LEFT JOIN votes v1 ON p.id = v1.choice1
                GROUP BY p.id, p.name
                HAVING COUNT(DISTINCT v.id) >= 3 AND COUNT(v1.id) = 0
            """)
            excluded = {row[0] for row in cursor.fetchall()}
        elif h_name == "E7":
            cursor.execute("""
                SELECT p.id, p.name,
                    COUNT(CASE WHEN v.choice1 = p.id THEN 1 END) * 3 +
                    COUNT(CASE WHEN v.choice2 = p.id THEN 1 END) * 2 +
                    COUNT(CASE WHEN v.choice3 = p.id THEN 1 END) * 1 as total_points
                FROM performers p
                LEFT JOIN votes v ON p.id IN (v.choice1, v.choice2, v.choice3)
                GROUP BY p.id, p.name
                HAVING total_points < 2
            """)
            excluded = {row[0] for row in cursor.fetchall()}
        
        excluded = excluded & current_set  # Виключаємо тільки з поточного набору
        results.append((f"{h_name}: {h_desc}", [all_performers[pid] for pid in excluded]))
        current_set = current_set - excluded
    
    return results, [all_performers[pid] for pid in current_set]

def create_heuristics_plot(cursor):
    """Створює графік популярності евристик"""
    # Отримуємо реальні дані про використання евристик
    cursor.execute("""
        SELECT h.name || ': ' || h.description as heuristic_name,
               COUNT(hv.id) as vote_count
        FROM heuristics h
        LEFT JOIN heuristic_votes hv ON h.id = hv.heuristic_id
        GROUP BY h.id, h.name, h.description
        ORDER BY h.id
    """)
    heuristics_data = cursor.fetchall()
    
    # Створюємо графік
    fig = go.Figure(data=[
        go.Bar(
            x=[h[0] for h in heuristics_data],
            y=[h[1] for h in heuristics_data],
            text=[h[1] for h in heuristics_data],
            textposition='auto',
        )
    ])
    
    fig.update_layout(
        title='Популярність евристик',
        xaxis_title='Евристики',
        yaxis_title='Кількість використань',
        template='plotly_white'
    )
    
    return json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)

@app.route('/add_expert', methods=['POST'])
def add_expert():
    with sqlite3.connect("voting.db") as conn:
        cursor = conn.cursor()
        expert_name = request.form['new_expert']
        choice1 = request.form['choice1']
        choice2 = request.form['choice2']
        choice3 = request.form['choice3']
        
        # Додаємо нового експерта
        cursor.execute("INSERT OR IGNORE INTO experts (name) VALUES (?)", (expert_name,))
        
        # Отримуємо ID нового експерта
        cursor.execute("SELECT id FROM experts WHERE name = ?", (expert_name,))
        expert_id = cursor.fetchone()[0]
        
        # Додаємо голоси нового експерта
        cursor.execute("""
            INSERT INTO votes (expert_id, choice1, choice2, choice3)
            VALUES (?, ?, ?, ?)
        """, (expert_id, choice1, choice2, choice3))
        
        conn.commit()
        
    return redirect(url_for('vote'))

@app.route('/expert/<expert_name>', methods=['GET', 'POST'])
def expert_vote(expert_name):
    with sqlite3.connect("voting.db") as conn:
        cursor = conn.cursor()
        
        if request.method == 'POST':
            choice1 = request.form['choice1']
            choice2 = request.form['choice2']
            choice3 = request.form['choice3']
            
            # Додаємо експерта, якщо його ще немає
            cursor.execute("""
                INSERT OR IGNORE INTO experts (name)
                VALUES (?)
            """, (expert_name,))
            
            # Отримуємо ID експерта
            cursor.execute("SELECT id FROM experts WHERE name = ?", (expert_name,))
            expert_id = cursor.fetchone()[0]
            
            # Оновлюємо голос
            cursor.execute("""
                INSERT OR REPLACE INTO votes (expert_id, choice1, choice2, choice3)
                VALUES (?, ?, ?, ?)
            """, (expert_id, choice1, choice2, choice3))
            
            conn.commit()
            return render_template('thank_you.html')

        # Отримуємо список виконавців для форми
        cursor.execute("SELECT id, name FROM performers")
        performers = cursor.fetchall()
        
        # Перевіряємо, чи експерт вже голосував
        cursor.execute("""
            SELECT 1 FROM votes v
            JOIN experts e ON v.expert_id = e.id
            WHERE e.name = ?
        """, (expert_name,))
        already_voted = cursor.fetchone() is not None
        
        return render_template('expert_vote.html', 
                             expert_name=expert_name,
                             performers=performers,
                             already_voted=already_voted)

@app.route('/results')
def results():
    with sqlite3.connect("voting.db") as conn:
        cursor = conn.cursor()
        
        # Отримуємо результати застосування евристик
        filtering_results, winners = apply_heuristics(cursor)
        
        # Отримуємо статистику по евристикам з урахуванням всіх голосів
        cursor.execute("""
            SELECT 
                h.name, 
                h.description,
                COUNT(DISTINCT hv.expert_id) as votes,
                ROUND(AVG(CASE WHEN hv.priority IS NOT NULL THEN hv.priority ELSE 0 END), 2) as avg_priority
            FROM heuristics h
            LEFT JOIN heuristic_votes hv ON h.id = hv.heuristic_id
            GROUP BY h.id, h.name, h.description
            ORDER BY votes DESC, avg_priority ASC
        """)
        
        heuristics_ranking = []
        for row in cursor.fetchall():
            heuristics_ranking.append({
                'name': row[0],
                'description': row[1],
                'votes': row[2],
                'avg_priority': row[3]
            })
        
        # Створюємо графік
        plot_json = create_heuristics_plot(cursor)
        
        return render_template('results.html',
                             filtering_results=filtering_results,
                             winners=winners,
                             heuristics_ranking=heuristics_ranking,
                             plot_json=plot_json)

@app.route('/vote_heuristics', methods=['GET', 'POST'])
def vote_heuristics():
    with sqlite3.connect("voting.db") as conn:
        cursor = conn.cursor()
        
        if request.method == 'POST':
            expert_name = request.form['expert']
            heuristic1 = int(request.form['heuristic1'])
            heuristic2 = int(request.form.get('heuristic2', 0))
            heuristic3 = int(request.form.get('heuristic3', 0))
            
            # Отримуємо ID експерта
            cursor.execute("SELECT id FROM experts WHERE name = ?", (expert_name,))
            expert_id = cursor.fetchone()[0]
            
            # Видаляємо попередні голоси експерта
            cursor.execute("DELETE FROM heuristic_votes WHERE expert_id = ?", (expert_id,))
            
            # Додаємо нові голоси
            if heuristic1:
                cursor.execute("""
                    INSERT INTO heuristic_votes (expert_id, heuristic_id, priority)
                    VALUES (?, ?, 1)
                """, (expert_id, heuristic1))
            if heuristic2:
                cursor.execute("""
                    INSERT INTO heuristic_votes (expert_id, heuristic_id, priority)
                    VALUES (?, ?, 2)
                """, (expert_id, heuristic2))
            if heuristic3:
                cursor.execute("""
                    INSERT INTO heuristic_votes (expert_id, heuristic_id, priority)
                    VALUES (?, ?, 3)
                """, (expert_id, heuristic3))
            
            conn.commit()
            return redirect(url_for('results'))
        
        # Отримуємо список евристик
        cursor.execute("SELECT id, name, description FROM heuristics ORDER BY id")
        heuristics = cursor.fetchall()
        
        # Отримуємо список експертів
        cursor.execute("SELECT name FROM experts ORDER BY name")
        experts = [row[0] for row in cursor.fetchall()]
        
        return render_template('vote_heuristics.html',
                             heuristics=heuristics,
                             experts=experts)

def seed_heuristics():
    with sqlite3.connect("voting.db") as conn:
        cursor = conn.cursor()
        heuristics = [
            ("E1", "Об'єкт 1 раз був на 3 місці"),
            ("E2", "Об'єкт 1 раз був на 2 місці"),
            ("E3", "Об'єкт 1 раз був на 1 місці"),
            ("E4", "Об'єкт 2 рази на 3 місці"),
            ("E5", "1 раз на 3 і 1 раз на 2 місці"),
            ("E6", "Був у 3+ голосуваннях, але ніколи на 1 місці"),
            ("E7", "Має менше 2 балів загалом")
        ]
        cursor.execute("DELETE FROM heuristics")  # Очищаємо таблицю перед додаванням
        for name, description in heuristics:
            cursor.execute("""
                INSERT OR IGNORE INTO heuristics (name, description)
                VALUES (?, ?)
            """, (name, description))
        conn.commit()

if __name__ == '__main__':
    # Перевіряємо, чи існує база даних
    if not os.path.exists("voting.db"):
        init_db()
        seed_data()
        seed_heuristics()
        generate_random_votes()
    else:
        # Якщо база існує, просто очищаємо дублікати
        clean_duplicates()
    
    app.run()