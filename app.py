import os, io, time, json, sqlite3, smtplib
import pandas as pd
from contextlib import contextmanager, nullcontext
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, send_file, session, redirect, url_for, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

# Sunucu üzerinde kalıcı veri klasörü (app.py ile aynı dizinde 'data' klasörü)
BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(BASE_DIR, exist_ok=True)
DB_PATH = os.getenv('DATABASE_PATH', os.path.join(BASE_DIR, 'mim_reklam_satis_sistemi.db'))

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db(connection=None, seed=True):
    with (nullcontext(connection) if connection is not None else get_db()) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            company TEXT,
            title TEXT,
            emails TEXT,
            phone TEXT,
            category TEXT DEFAULT 'Potansiyel',
            city TEXT,
            tax_info TEXT,
            notes TEXT,
            created_at TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            type TEXT,
            summary TEXT,
            staff_name TEXT,
            created_at TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_no TEXT,
            customer_id INTEGER,
            manual_customer_name TEXT,
            manual_customer_contact TEXT,
            version INTEGER DEFAULT 1,
            items_json TEXT,
            tax_rate REAL DEFAULT 20,
            total_amount REAL,
            footer_note TEXT,
            status TEXT DEFAULT 'Gönderildi',
            created_at TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            customer_id INTEGER,
            assigned_to TEXT,
            assigned_by TEXT,
            due_date TEXT,
            due_time TEXT DEFAULT '09:00',
            remind_before_mins INTEGER DEFAULT 30,
            reminded INTEGER DEFAULT 0,
            priority TEXT,
            status TEXT DEFAULT 'Bekliyor',
            note TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            title TEXT,
            email TEXT,
            smtp_pass TEXT,
            role TEXT,
            signature TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS mail_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            subject TEXT,
            body TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS mail_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_name TEXT,
            recipient_email TEXT,
            subject TEXT,
            sender_email TEXT,
            status TEXT,
            error_msg TEXT,
            sent_at TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            detail TEXT,
            created_at TEXT
        )''')

        for table, columns in {
            'quotes': {'manual_customer_name': 'TEXT', 'manual_customer_contact': 'TEXT'},
            'tasks': {'due_time': "TEXT DEFAULT '09:00'", 'remind_before_mins': 'INTEGER DEFAULT 30', 'reminded': 'INTEGER DEFAULT 0'},
        }.items():
            existing = {row[1] for row in c.execute(f'PRAGMA table_info("{table}")')}
            for column, definition in columns.items():
                if column not in existing:
                    c.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
        if not seed:
            return

        if c.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 0:
            c.execute("INSERT INTO staff (name, title, email, smtp_pass, role, signature) VALUES (?,?,?,?,?,?)",
                      ("Ahmet Yılmaz", "Müşteri İlişkileri Direktörü", "satis@mimreklam.com", "", "Yönetici",
                       "Saygılarımla,\nAhmet Yılmaz | Müşteri İlişkileri Direktörü\nMim Reklam Ajansı | www.mimreklam.com"))
        if c.execute("SELECT COUNT(*) FROM mail_templates").fetchone()[0] == 0:
            c.execute("INSERT INTO mail_templates (name, subject, body) VALUES (?,?,?)",
                      ("Tanışma & Sunum", "{firma_adi} - Kreatif & Reklam Çözüm Önerimiz",
                       "Merhaba {musteri_adi},\n\n{firma_adi} markanızın görünürlüğünü ve satışlarını artıracak reklam ve medya çözümlerimizi paylaşmak isteriz.\n\nUygun olduğunuz bir vakitte 15 dakikalık kısa bir görüşme planlayabilir miyiz?"))
            c.execute("INSERT INTO mail_templates (name, subject, body) VALUES (?,?,?)",
                      ("Teklif Sunumu", "{firma_adi} Özel Reklam & Prodüksiyon Teklifimiz",
                       "Merhaba {musteri_adi},\n\nGörüşmemize istinaden hazırladığımız özel fiyat teklifimiz ekte yer almaktadır. Sorularınız için dilediğiniz zaman ulaşabilirsiniz."))
        if c.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
            company_info = json.dumps({
                "name": "Mim Reklam Ajansı Ltd. Şti.",
                "address": "Çankaya / Ankara",
                "phone": "+90 312 000 00 00",
                "email": "info@mimreklam.com",
                "tax": "Çankaya V.D. / 1234567890",
                "logo": ""
            })
            c.execute("INSERT INTO settings (key, value) VALUES ('company', ?)", (company_info,))


app = Flask(__name__)
app.secret_key = os.environ['SECRET_KEY']
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')


@app.before_request
def require_login():
    if request.endpoint != 'login' and not session.get('user_id'):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Oturum açmanız gerekiyor.'}), 401
        return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = ''
    if request.method == 'POST':
        with get_db() as conn:
            user = conn.execute('SELECT * FROM crm_users WHERE username=?',
                                (request.form.get('username', ''),)).fetchone()
        if user and check_password_hash(user['password_hash'], request.form.get('password', '')):
            session.clear()
            session['user_id'] = user['id']
            return redirect(url_for('index'))
        error = 'Kullanıcı adı veya şifre hatalı.'
    return render_template_string('''<!doctype html><html lang="tr"><meta charset="utf-8">
        <title>CRM Giriş</title><body><h1>Mim Reklam CRM</h1>
        <form method="post"><label>Kullanıcı adı <input name="username" required autocomplete="username"></label>
        <label>Şifre <input name="password" type="password" required autocomplete="current-password"></label>
        <button>Giriş yap</button><p>{{ error }}</p></form></body></html>''', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(os.getenv('UPLOAD_FOLDER', os.path.join(BASE_DIR, 'uploaded_files')), filename)

@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response

def log_audit(conn, action, detail):
    conn.execute("INSERT INTO audit_logs (action, detail, created_at) VALUES (?,?,?)",
                 (action, detail, datetime.now().strftime("%d.%m.%Y %H:%M")))

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/bootstrap', methods=['GET'])
def bootstrap():
    with get_db() as conn:
        customers = [dict(r) for r in conn.execute("SELECT * FROM customers ORDER BY id DESC").fetchall()]
        interactions = [dict(r) for r in conn.execute("SELECT * FROM interactions ORDER BY id DESC").fetchall()]
        quotes = [dict(r) for r in conn.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()]
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks ORDER BY status DESC, due_date ASC, due_time ASC").fetchall()]
        staff = [dict(r) for r in conn.execute("SELECT id, name, title, email, role, signature FROM staff").fetchall()]
        templates = [dict(r) for r in conn.execute("SELECT * FROM mail_templates").fetchall()]
        mail_logs = [dict(r) for r in conn.execute("SELECT * FROM mail_logs ORDER BY id DESC LIMIT 200").fetchall()]
        audit_logs = [dict(r) for r in conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 100").fetchall()]
        comp_row = conn.execute("SELECT value FROM settings WHERE key='company'").fetchone()
        company = json.loads(comp_row['value']) if comp_row else {}
    return jsonify({
        "customers": customers, "interactions": interactions, "quotes": quotes,
        "tasks": tasks, "staff": staff, "templates": templates,
        "mail_logs": mail_logs, "audit_logs": audit_logs, "company": company
    })

@app.route('/api/customers', methods=['POST'])
def save_customer():
    data = request.json
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    with get_db() as conn:
        if data.get('id'):
            conn.execute("""UPDATE customers SET full_name=?, company=?, title=?, emails=?, phone=?,
                            category=?, city=?, tax_info=?, notes=? WHERE id=?""",
                         (data['full_name'], data.get('company',''), data.get('title',''),
                          data.get('emails',''), data.get('phone',''), data.get('category','Potansiyel'),
                          data.get('city',''), data.get('tax_info',''), data.get('notes',''), data['id']))
            log_audit(conn, "Müşteri Güncellendi", f"{data['full_name']} ({data.get('company','')})")
        else:
            conn.execute("""INSERT INTO customers (full_name, company, title, emails, phone, category, city, tax_info, notes, created_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (data['full_name'], data.get('company',''), data.get('title',''),
                          data.get('emails',''), data.get('phone',''), data.get('category','Potansiyel'),
                          data.get('city',''), data.get('tax_info',''), data.get('notes',''), now))
            log_audit(conn, "Yeni Müşteri", f"{data['full_name']} eklendi")
    return jsonify({"status": "ok"})

@app.route('/api/customers/<int:cust_id>', methods=['DELETE'])
def delete_customer(cust_id):
    with get_db() as conn:
        row = conn.execute("SELECT full_name FROM customers WHERE id=?", (cust_id,)).fetchone()
        conn.execute("DELETE FROM customers WHERE id=?", (cust_id,))
        conn.execute("DELETE FROM interactions WHERE customer_id=?", (cust_id,))
        if row:
            log_audit(conn, "Müşteri Silindi", row['full_name'])
    return jsonify({"status": "ok"})

@app.route('/api/customers/sample_excel', methods=['GET'])
def download_sample_excel_api():
    df = pd.DataFrame([{
        "Ad_Soyad": "Mehmet Kaya", "Firma": "Kaya Yapı A.Ş.", "Unvan": "Pazarlama Müdürü",
        "Eposta": "mehmet@kayayapi.com", "Telefon": "0532 000 00 00", "Kategori": "Potansiyel",
        "Sehir": "Ankara", "Vergi": "Çankaya V.D. 1112223334", "Not": "Açıkhava ve dijital reklam talebi"
    }])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Musteriler')
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="MimReklam_Ornek_Musteri_Sablonu.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route('/api/customers/upload_excel', methods=['POST'])
def upload_excel_api():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "Dosya seçilmedi."}), 400
    f = request.files['file']
    filename = (f.filename or '').lower()
    try:
        df = pd.read_csv(f) if filename.endswith('.csv') else pd.read_excel(f)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Dosya okunamadı: {str(e)}"}), 400

    df = df.fillna('')
    col_map = {str(c).strip().lower(): c for c in df.columns}
    def get_val(row, candidates):
        for cand in candidates:
            if cand in col_map:
                val = str(row[col_map[cand]]).strip()
                if val and val.lower() != 'nan':
                    return val
        return ''

    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    added = 0
    with get_db() as conn:
        for _, row in df.iterrows():
            full_name = get_val(row, ['ad_soyad', 'ad soyad', 'full_name', 'isim', 'müşteri adı', 'musteri adi'])
            if not full_name:
                continue
            conn.execute("""INSERT INTO customers (full_name, company, title, emails, phone, category, city, tax_info, notes, created_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (full_name, get_val(row, ['firma', 'company', 'şirket', 'sirket', 'firma adı']),
                          get_val(row, ['unvan', 'ünvan', 'title', 'pozisyon']),
                          get_val(row, ['eposta', 'e-posta', 'email', 'emails', 'mail']),
                          get_val(row, ['telefon', 'phone', 'tel', 'gsm']),
                          get_val(row, ['kategori', 'category', 'durum']) or 'Potansiyel',
                          get_val(row, ['sehir', 'şehir', 'city', 'il']),
                          get_val(row, ['vergi', 'tax_info', 'vergi no']),
                          get_val(row, ['not', 'notlar', 'notes', 'açıklama']), now))
            added += 1
        log_audit(conn, "Excel/CSV İçe Aktarım", f"{added} müşteri toplu yüklendi")
    return jsonify({"status": "ok", "added": added})

@app.route('/api/customers/bulk_action', methods=['POST'])
def bulk_action():
    data = request.json
    ids = data.get('ids', [])
    action = data.get('action')
    with get_db() as conn:
        if action == 'delete':
            conn.executemany("DELETE FROM customers WHERE id=?", [(i,) for i in ids])
            log_audit(conn, "Toplu Silme", f"{len(ids)} müşteri silindi")
        elif action == 'category':
            cat = data.get('category')
            conn.executemany("UPDATE customers SET category=? WHERE id=?", [(cat, i) for i in ids])
            log_audit(conn, "Toplu Kategori Değişimi", f"{len(ids)} müşteri -> {cat}")
    return jsonify({"status": "ok"})

@app.route('/api/interactions', methods=['POST'])
def add_interaction():
    d = request.json
    staff_name = d.get('staff_name') or 'Belirtilmedi'
    with get_db() as conn:
        conn.execute("INSERT INTO interactions (customer_id, type, summary, staff_name, created_at) VALUES (?,?,?,?,?)",
                     (d['customer_id'], d['type'], d['summary'], staff_name, datetime.now().strftime("%d.%m.%Y %H:%M")))
        log_audit(conn, "Görüşme Eklendi", f"Müşteri ID #{d['customer_id']} - {d['type']} ({staff_name})")
    return jsonify({"status": "ok"})

@app.route('/api/quotes', methods=['POST'])
def save_quote():
    d = request.json
    now = datetime.now().strftime("%d.%m.%Y")
    q_no = d.get('quote_no') or f"MIM-{int(time.time())%100000}"
    ver = d.get('version', 1)
    with get_db() as conn:
        conn.execute("""INSERT INTO quotes (quote_no, customer_id, manual_customer_name, manual_customer_contact, version, items_json, tax_rate, total_amount, footer_note, status, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (q_no, d.get('customer_id') or None, d.get('manual_customer_name',''),
                      d.get('manual_customer_contact',''), ver, json.dumps(d['items']),
                      d.get('tax_rate',20), d['total_amount'], d.get('footer_note',''), 'Gönderildi', now))
        if d.get('customer_id'):
            conn.execute("UPDATE customers SET category='Teklif Verildi' WHERE id=? AND category='Potansiyel'", (d['customer_id'],))
        log_audit(conn, "Teklif Oluşturuldu", f"{q_no} (v{ver}) kaydedildi")
    return jsonify({"status": "ok", "quote_no": q_no})

@app.route('/api/quotes/<int:quote_id>', methods=['PATCH', 'DELETE'])
def manage_quote(quote_id):
    with get_db() as conn:
        if request.method == 'DELETE':
            conn.execute("DELETE FROM quotes WHERE id=?", (quote_id,))
            log_audit(conn, "Teklif Silindi", f"Teklif ID #{quote_id}")
        else:
            st = request.json.get('status')
            conn.execute("UPDATE quotes SET status=? WHERE id=?", (st, quote_id))
            if st == 'Onaylandı':
                q = conn.execute("SELECT customer_id FROM quotes WHERE id=?", (quote_id,)).fetchone()
                if q and q['customer_id']:
                    conn.execute("UPDATE customers SET category='Aktif Müşteri' WHERE id=?", (q['customer_id'],))
            log_audit(conn, "Teklif Durumu", f"Teklif ID #{quote_id} -> {st}")
    return jsonify({"status": "ok"})

@app.route('/api/tasks', methods=['POST'])
def save_task():
    d = request.json
    with get_db() as conn:
        if d.get('toggle_id'):
            conn.execute("UPDATE tasks SET status = CASE WHEN status='Tamamlandı' THEN 'Bekliyor' ELSE 'Tamamlandı' END WHERE id=?", (d['toggle_id'],))
        elif d.get('ack_remind_id'):
            conn.execute("UPDATE tasks SET reminded = 1 WHERE id=?", (d['ack_remind_id'],))
        else:
            conn.execute("""INSERT INTO tasks (title, customer_id, assigned_to, assigned_by, due_date, due_time, remind_before_mins, reminded, priority, status, note)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                         (d['title'], d.get('customer_id') or None, d['assigned_to'], d.get('assigned_by','Yönetici'),
                          d['due_date'], d.get('due_time', '09:00'), int(d.get('remind_before_mins', 30)), 0,
                          d.get('priority','Orta'), 'Bekliyor', d.get('note','')))
            log_audit(conn, "Görev Atandı", f"{d['title']} -> {d['assigned_to']} ({d['due_date']} {d.get('due_time','09:00')})")
    return jsonify({"status": "ok"})

@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    with get_db() as conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return jsonify({"status": "ok"})

@app.route('/api/send_single_mail', methods=['POST'])
def send_single_mail():
    d = request.json
    with get_db() as conn:
        staff = conn.execute("SELECT * FROM staff WHERE id=?", (d['staff_id'],)).fetchone()
        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        status = "İletildi (Simülasyon)"
        err_msg = ""
        if staff and staff['smtp_pass']:
            try:
                msg = MIMEMultipart()
                msg['From'] = staff['email']
                msg['To'] = d['to_email']
                msg['Subject'] = d['subject']
                sig_block = f"<br><br><hr><pre style='font-family:inherit;color:#555'>{staff['signature']}</pre>" if staff['signature'] else ""
                full_html = f"<div style='font-family:Arial; line-height:1.6;'>{d['body'].replace(chr(10),'<br>')}{sig_block}</div>"
                msg.attach(MIMEText(full_html, 'html'))
                server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
                server.starttls()
                server.login(staff['email'], staff['smtp_pass'])
                server.send_message(msg)
                server.quit()
                status = "İletildi (Gerçek SMTP)"
            except Exception as e:
                status = "Hata"
                err_msg = str(e)
        conn.execute("INSERT INTO mail_logs (recipient_name, recipient_email, subject, sender_email, status, error_msg, sent_at) VALUES (?,?,?,?,?,?,?)",
                     (d.get('to_name',''), d['to_email'], d['subject'], staff['email'] if staff else '', status, err_msg, now))
    return jsonify({"status": status, "error": err_msg})

@app.route('/api/settings', methods=['POST'])
def save_settings():
    d = request.json
    with get_db() as conn:
        if d.get('type') == 'company':
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('company', ?)", (json.dumps(d['data']),))
            log_audit(conn, "Firma Ayarları", "Firma kimliği ve logosu güncellendi")
        elif d.get('type') == 'staff':
            s = d['data']
            if s.get('id'):
                if s.get('smtp_pass'):
                    conn.execute("UPDATE staff SET name=?, title=?, email=?, smtp_pass=?, signature=? WHERE id=?",
                                 (s['name'], s['title'], s['email'], s['smtp_pass'], s.get('signature',''), s['id']))
                else:
                    conn.execute("UPDATE staff SET name=?, title=?, email=?, signature=? WHERE id=?",
                                 (s['name'], s['title'], s['email'], s.get('signature',''), s['id']))
                log_audit(conn, "Personel & İmza Güncellendi", f"{s['name']} ({s['title']})")
            else:
                conn.execute("INSERT INTO staff (name, title, email, smtp_pass, role, signature) VALUES (?,?,?,?,?,?)",
                             (s['name'], s['title'], s['email'], s.get('smtp_pass',''), s['role'], s.get('signature','')))
                log_audit(conn, "Personel Eklendi", f"{s['name']} ({s['title']})")
        elif d.get('type') == 'delete_staff':
            conn.execute("DELETE FROM staff WHERE id=?", (d['id'],))
        elif d.get('type') == 'template':
            t = d['data']
            conn.execute("INSERT INTO mail_templates (name, subject, body) VALUES (?,?,?)", (t['name'], t['subject'], t['body']))
            log_audit(conn, "Mail Şablonu", f"{t['name']} eklendi")
        elif d.get('type') == 'delete_template':
            conn.execute("DELETE FROM mail_templates WHERE id=?", (d['id'],))
    return jsonify({"status": "ok"})

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mim Reklam Satış & CRM</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
<style>
  @media print {
    body { background: white !important; }
    .no-print { display: none !important; }
    .print-only { display: block !important; width: 100% !important; box-shadow: none !important; border: none !important; }
    main { overflow: visible !important; }
  }
  .tab-active { background: #1e293b; color: #38bdf8; border-left: 4px solid #38bdf8; }
  .subtab-active { border-bottom: 2px solid #0284c7; color: #0284c7; font-weight: 700; }
</style>
</head>
<body class="bg-slate-100 text-slate-800 font-sans">

<!-- GÖREV HATIRLATMA ALARM PENCERESİ (POPUP) -->
<div id="task-alarm-modal" class="hidden fixed inset-0 bg-slate-900/70 flex items-center justify-center z-50 p-4 no-print">
  <div class="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border-t-4 border-amber-500 space-y-4">
    <div class="flex items-center gap-3">
      <div class="w-11 h-11 rounded-full bg-amber-100 text-amber-600 flex items-center justify-center text-xl animate-bounce">
        <i class="fa-solid fa-bell"></i>
      </div>
      <div>
        <span class="text-[11px] font-bold uppercase tracking-wider text-amber-600">Mim Reklam Görev Hatırlatması</span>
        <h3 id="alarm-task-title" class="font-bold text-base text-slate-900"></h3>
      </div>
    </div>
    <div class="bg-slate-50 p-3.5 rounded-xl border border-slate-200 text-xs space-y-1.5">
      <div><span class="text-slate-400">Sorumlu Personel:</span> <strong id="alarm-task-staff" class="text-slate-800"></strong></div>
      <div><span class="text-slate-400">İlgili Müşteri:</span> <strong id="alarm-task-cust" class="text-sky-700"></strong></div>
      <div><span class="text-slate-400">Görev Zamanı:</span> <strong id="alarm-task-time" class="text-rose-600"></strong></div>
      <div id="alarm-task-note" class="text-slate-600 pt-1 border-t border-slate-200 mt-1"></div>
    </div>
    <div class="flex justify-end gap-2">
      <button type="button" onclick="acknowledgeTaskAlarm()" class="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold">Anlaşıldı (Uyarıyı Kapat)</button>
      <button type="button" onclick="completeTaskFromAlarm()" class="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-bold">Görevi Tamamlandı İşaretle</button>
    </div>
  </div>
</div>

<div class="flex min-h-screen">
  <!-- SIDEBAR -->
  <aside class="w-64 bg-slate-900 text-slate-300 flex flex-col no-print shrink-0">
    <div class="p-5 border-b border-slate-800 flex items-center gap-3">
      <img id="sidebar-logo-img" src="" alt="Logo" class="hidden h-9 w-9 object-contain rounded-lg bg-white p-0.5">
      <div id="sidebar-logo-fallback" class="w-9 h-9 rounded-lg bg-sky-500 flex items-center justify-center text-white font-bold text-lg">M</div>
      <div>
        <h1 class="text-white font-bold tracking-wide">MİM REKLAM</h1>
        <p class="text-xs text-slate-400">Satış & Müşteri CRM</p>
      </div>
    </div>
    <nav class="flex-1 py-4 space-y-1 text-sm">
      <button type="button" onclick="switchTab('customers')" id="nav-customers" class="nav-btn tab-active w-full flex items-center gap-3 px-5 py-3 hover:bg-slate-800 transition">
        <i class="fa-solid fa-users w-5"></i> Müşteriler
      </button>
      <button type="button" onclick="switchTab('quotes')" id="nav-quotes" class="nav-btn w-full flex items-center gap-3 px-5 py-3 hover:bg-slate-800 transition">
        <i class="fa-solid fa-file-invoice-dollar w-5"></i> Teklif Oluştur & İncele
      </button>
      <button type="button" onclick="switchTab('mail')" id="nav-mail" class="nav-btn w-full flex items-center gap-3 px-5 py-3 hover:bg-slate-800 transition">
        <i class="fa-solid fa-paper-plane w-5"></i> Toplu & Tekli Mail
      </button>
      <button type="button" onclick="switchTab('tasks')" id="nav-tasks" class="nav-btn w-full flex items-center gap-3 px-5 py-3 hover:bg-slate-800 transition">
        <i class="fa-solid fa-list-check w-5"></i> Görevler & Hatırlatma
      </button>
      <button type="button" onclick="switchTab('reports')" id="nav-reports" class="nav-btn w-full flex items-center gap-3 px-5 py-3 hover:bg-slate-800 transition">
        <i class="fa-solid fa-chart-pie w-5"></i> Yönetici Raporları
      </button>
      <button type="button" onclick="switchTab('settings')" id="nav-settings" class="nav-btn w-full flex items-center gap-3 px-5 py-3 hover:bg-slate-800 transition">
        <i class="fa-solid fa-sliders w-5"></i> Ayarlar & Loglar
      </button>
    </nav>
    <div class="p-4 border-t border-slate-800 text-xs text-slate-400 space-y-1">
      <div id="sys-status-badge" class="flex items-center gap-2 text-amber-400"><i class="fa-solid fa-circle-notch fa-spin"></i> Yükleniyor...</div>
      <div class="text-[11px] text-slate-500">Aktif Kullanıcı (Alarm Filtresi):</div>
      <select id="active-user-select" onchange="checkTaskReminders()" class="w-full bg-slate-800 text-slate-200 border border-slate-700 rounded px-2 py-1 text-xs">
        <option value="ALL">Tüm Personeller (Yönetici)</option>
      </select>
    </div>
  </aside>

  <!-- MAIN CONTENT -->
  <main class="flex-1 flex flex-col min-w-0">
    <header class="bg-white border-b border-slate-200 px-6 py-3.5 flex items-center justify-between no-print">
      <h2 id="page-title" class="text-lg font-bold text-slate-800">Müşteri Portföyü</h2>
      <div class="flex items-center gap-4">
        <button type="button" onclick="switchTab('tasks')" class="relative p-2 text-slate-600 hover:bg-slate-100 rounded-full" title="Yaklaşan Görevler">
          <i class="fa-solid fa-bell text-lg"></i>
          <span id="task-badge" class="hidden absolute top-0 right-0 bg-rose-600 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full">0</span>
        </button>
        <div class="text-right text-xs">
          <div class="font-semibold text-slate-700" id="active-company-name">Mim Reklam Ajansı</div>
          <div class="text-emerald-600 font-semibold">Canlı Sistem</div>
        </div>
      </div>
    </header>

    <div class="p-6 flex-1 overflow-y-auto">

      <!-- 1. MÜŞTERİLER SEKMESİ -->
      <section id="tab-customers" class="space-y-4">
        <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200 flex flex-wrap gap-3 items-center justify-between no-print">
          <div class="flex flex-wrap gap-2 items-center flex-1">
            <div class="relative w-64">
              <i class="fa-solid fa-search absolute left-3 top-3 text-slate-400 text-sm"></i>
              <input id="cust-search" oninput="renderCustomers()" type="text" placeholder="İsim, firma, mail ara..." class="w-full pl-9 pr-3 py-2 border rounded-lg text-sm focus:outline-sky-500">
            </div>
            <select id="cust-filter" onchange="renderCustomers()" class="border rounded-lg px-3 py-2 text-sm bg-white">
              <option value="ALL">Tüm Kategoriler</option>
              <option value="Potansiyel">Potansiyel</option>
              <option value="Teklif Verildi">Teklif Verildi</option>
              <option value="Aktif Müşteri">Aktif Müşteri</option>
              <option value="VIP">VIP</option>
            </select>
            <select id="cust-sort" onchange="renderCustomers()" class="border rounded-lg px-3 py-2 text-sm bg-white">
              <option value="NEW">En Yeni</option>
              <option value="AZ">Alfabetik (A-Z)</option>
              <option value="ZA">Alfabetik (Z-A)</option>
            </select>
          </div>
          <div class="flex flex-wrap gap-2">
            <button type="button" onclick="downloadSampleExcel()" class="px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-medium inline-flex items-center">
              <i class="fa-solid fa-file-excel text-emerald-600 mr-1"></i> Örnek Şablon
            </button>
            <label class="px-3 py-2 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200 rounded-lg text-xs font-medium cursor-pointer inline-flex items-center">
              <i class="fa-solid fa-upload mr-1"></i> Excel Yükle
              <input id="excel-upload-input" type="file" accept=".xlsx,.xls,.csv" onchange="handleExcelUpload(event)" class="hidden">
            </label>
            <button type="button" onclick="window.print()" class="px-3 py-2 bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 rounded-lg text-xs font-medium">
              <i class="fa-solid fa-print mr-1"></i> Yazdır / PDF
            </button>
            <button type="button" onclick="openCustomerModal()" class="px-4 py-2 bg-sky-600 hover:bg-sky-700 text-white rounded-lg text-xs font-semibold shadow-sm">
              <i class="fa-solid fa-plus mr-1"></i> Yeni Müşteri
            </button>
          </div>
        </div>

        <div id="bulk-bar" class="hidden bg-sky-900 text-white px-4 py-2.5 rounded-lg flex items-center justify-between text-sm no-print">
          <span><strong id="selected-count">0</strong> müşteri seçildi</span>
          <div class="flex items-center gap-2">
            <select id="bulk-cat-select" class="text-slate-800 rounded px-2 py-1 text-xs">
              <option value="Potansiyel">Potansiyel</option>
              <option value="Teklif Verildi">Teklif Verildi</option>
              <option value="Aktif Müşteri">Aktif Müşteri</option>
              <option value="VIP">VIP</option>
            </select>
            <button type="button" onclick="bulkUpdateCategory()" class="bg-sky-600 hover:bg-sky-500 px-3 py-1 rounded text-xs">Kategori Değiştir</button>
            <button type="button" onclick="bulkDelete()" class="bg-rose-600 hover:bg-rose-500 px-3 py-1 rounded text-xs">Seçilenleri Sil</button>
          </div>
        </div>

        <div id="customers-table-wrap" class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-x-auto print-only">
          <table class="w-full text-left border-collapse text-sm">
            <thead class="bg-slate-50 border-b border-slate-200 text-slate-600 text-xs uppercase">
              <tr>
                <th class="p-3.5 no-print"><input type="checkbox" id="select-all-cb" onclick="toggleSelectAll(this)"></th>
                <th class="p-3.5">Ad Soyad / Ünvan</th>
                <th class="p-3.5">Firma</th>
                <th class="p-3.5">E-Posta(lar)</th>
                <th class="p-3.5">Telefon</th>
                <th class="p-3.5">Kategori</th>
                <th class="p-3.5 text-right no-print">İşlemler</th>
              </tr>
            </thead>
            <tbody id="customers-tbody" class="divide-y divide-slate-100"></tbody>
          </table>
        </div>
      </section>

      <!-- 1B. TAM SAYFA MÜŞTERİ KARTI -->
      <section id="tab-customer-detail" class="hidden space-y-5">
        <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200 flex flex-wrap items-center justify-between gap-4 no-print">
          <div class="flex items-center gap-4">
            <button type="button" onclick="switchTab('customers')" class="px-3.5 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold flex items-center gap-1.5">
              <i class="fa-solid fa-arrow-left"></i> Müşterilere Dön
            </button>
            <div>
              <div class="flex items-center gap-2.5">
                <h2 id="full-det-name" class="text-xl font-bold text-slate-900"></h2>
                <span id="full-det-cat" class="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-sky-100 text-sky-800"></span>
              </div>
              <p id="full-det-sub" class="text-xs text-slate-500 mt-0.5"></p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <button type="button" onclick="goCreateQuoteForCustomer()" class="px-3.5 py-2 bg-sky-600 hover:bg-sky-700 text-white rounded-lg text-xs font-semibold">
              <i class="fa-solid fa-file-invoice-dollar mr-1"></i> Yeni Teklif Hazırla
            </button>
            <button type="button" onclick="window.print()" class="px-3.5 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-semibold">
              <i class="fa-solid fa-print mr-1"></i> Kart Yazdır / PDF
            </button>
          </div>
        </div>

        <div id="full-customer-card-print" class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden print-only">
          <div class="flex border-b border-slate-200 bg-slate-50 px-4 no-print">
            <button type="button" onclick="switchCardSubtab('info')" id="csub-btn-info" class="csub-btn subtab-active px-5 py-3.5 text-sm transition flex items-center gap-2">
              <i class="fa-solid fa-address-card"></i> Müşteri Bilgileri
            </button>
            <button type="button" onclick="switchCardSubtab('interactions')" id="csub-btn-interactions" class="csub-btn px-5 py-3.5 text-sm text-slate-600 hover:text-slate-900 transition flex items-center gap-2">
              <i class="fa-solid fa-comments"></i> Görüşme Bilgileri & Notlar
            </button>
            <button type="button" onclick="switchCardSubtab('quotes')" id="csub-btn-quotes" class="csub-btn px-5 py-3.5 text-sm text-slate-600 hover:text-slate-900 transition flex items-center gap-2">
              <i class="fa-solid fa-file-contract"></i> Gönderilen Teklifler
            </button>
          </div>

          <div class="p-6">
            <div id="csub-info" class="space-y-4">
              <div class="flex justify-between items-center border-b pb-2">
                <h3 class="font-bold text-slate-800">Kurumsal & İletişim Detayları</h3>
                <button type="button" onclick="openCustomerModal(activeCustomerId)" class="text-xs bg-amber-50 text-amber-700 border border-amber-200 px-3 py-1.5 rounded-lg font-semibold hover:bg-amber-100 no-print">
                  <i class="fa-solid fa-pen mr-1"></i> Bilgileri Düzenle
                </button>
              </div>
              <div id="full-det-info-grid" class="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm"></div>
            </div>

            <div id="csub-interactions" class="hidden space-y-4">
              <div class="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-3 no-print">
                <h4 class="text-xs font-bold uppercase text-slate-600">Yeni Görüşme Kaydı Ekle</h4>
                <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
                  <div>
                    <label class="text-[11px] font-semibold text-slate-500">Görüşme Türü</label>
                    <select id="int-type" class="w-full mt-1 border rounded-lg px-2.5 py-2 text-xs bg-white">
                      <option value="Telefon Görüşmesi">Telefon Görüşmesi</option>
                      <option value="Online Demo / Toplantı">Online Demo / Toplantı</option>
                      <option value="Yüz Yüze Ziyaret">Yüz Yüze Ziyaret</option>
                      <option value="E-Posta / WhatsApp">E-Posta / WhatsApp</option>
                    </select>
                  </div>
                  <div>
                    <label class="text-[11px] font-semibold text-slate-500">Görüşen Personel *</label>
                    <select id="int-staff" class="w-full mt-1 border rounded-lg px-2.5 py-2 text-xs bg-white"></select>
                  </div>
                  <div class="md:col-span-2">
                    <label class="text-[11px] font-semibold text-slate-500">Görüşme Özeti & Sonraki Adım *</label>
                    <div class="flex gap-2 mt-1">
                      <input id="int-summary" type="text" placeholder="Görüşme detaylarını buraya yazın..." class="flex-1 border rounded-lg px-3 py-2 text-xs bg-white">
                      <button type="button" onclick="addInteraction()" class="bg-sky-600 hover:bg-sky-700 text-white px-4 py-2 rounded-lg text-xs font-semibold shrink-0">Kaydet</button>
                    </div>
                  </div>
                </div>
              </div>
              <div id="full-det-interactions" class="space-y-3"></div>
            </div>

            <div id="csub-quotes" class="hidden space-y-4">
              <div class="flex justify-between items-center border-b pb-2">
                <h3 class="font-bold text-slate-800">Müşteriye Sunulan Teklifler ve Revizyonlar</h3>
                <button type="button" onclick="goCreateQuoteForCustomer()" class="text-xs bg-sky-600 text-white px-3 py-1.5 rounded-lg font-semibold hover:bg-sky-700 no-print">+ Yeni Teklif Oluştur</button>
              </div>
              <div id="full-det-quotes" class="space-y-3"></div>
            </div>
          </div>
        </div>
      </section>

      <!-- 2. TEKLİF OLUŞTURUCU & İNCELEME SEKMESİ -->
      <section id="tab-quotes" class="hidden space-y-6">
        <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div class="lg:col-span-5 bg-white p-5 rounded-xl shadow-sm border border-slate-200 space-y-4 no-print">
            <div class="flex justify-between items-center border-b pb-2">
              <h3 class="font-bold text-slate-800">Teklif Kalemleri & Müşteri</h3>
              <div class="flex items-center gap-2">
                <span id="quote-rev-badge" class="text-xs bg-sky-100 text-sky-700 px-2 py-0.5 rounded font-semibold">Yeni Teklif (v1)</span>
                <button type="button" onclick="resetQuoteForm()" class="text-[11px] text-slate-500 hover:text-slate-800 underline">Yeni Teklif</button>
              </div>
            </div>

            <div class="bg-slate-50 p-3 rounded-lg border border-slate-200 space-y-2.5">
              <div class="flex items-center justify-between">
                <label class="text-xs font-bold text-slate-700">Müşteri Bilgisi</label>
                <div class="flex rounded-lg overflow-hidden border border-slate-300 text-[11px] font-semibold">
                  <button type="button" onclick="setQuoteCustomerMode('registered')" id="qmode-reg-btn" class="px-2.5 py-1 bg-sky-600 text-white">Kayıtlı Müşteri Ara</button>
                  <button type="button" onclick="setQuoteCustomerMode('manual')" id="qmode-man-btn" class="px-2.5 py-1 bg-white text-slate-600">Manuel İsim Yaz</button>
                </div>
              </div>

              <div id="q-reg-box" class="space-y-1.5">
                <div class="relative">
                  <i class="fa-solid fa-search absolute left-2.5 top-2.5 text-slate-400 text-xs"></i>
                  <input id="q-cust-search" oninput="filterQuoteCustomerDropdown()" type="text" placeholder="Müşteri adı veya firma arayın..." class="w-full pl-8 pr-2.5 py-1.5 border rounded-lg text-xs bg-white">
                </div>
                <select id="q-customer" onchange="updateQuotePreview()" class="w-full border rounded-lg p-2 text-xs bg-white"></select>
              </div>

              <div id="q-man-box" class="hidden space-y-2">
                <input id="q-man-name" oninput="updateQuotePreview()" type="text" placeholder="Müşteri Adı Soyadı / Firma Ünvanı *" class="w-full border rounded-lg p-2 text-xs bg-white">
                <input id="q-man-contact" oninput="updateQuotePreview()" type="text" placeholder="E-Posta / Telefon / Adres (Opsiyonel)" class="w-full border rounded-lg p-2 text-xs bg-white">
              </div>
            </div>

            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-700">Hizmet / Ürün Kalemleri</span>
              <button type="button" onclick="addQuoteItemRow()" class="text-xs bg-emerald-600 hover:bg-emerald-700 text-white px-2.5 py-1 rounded font-semibold">+ Kalem Ekle</button>
            </div>

            <div id="q-items-container" class="space-y-2 max-h-64 overflow-y-auto pr-1"></div>

            <div class="grid grid-cols-2 gap-3 pt-2 border-t">
              <div>
                <label class="text-xs font-semibold text-slate-600">KDV Oranı (%)</label>
                <input id="q-tax-rate" oninput="updateQuotePreview()" type="number" value="20" min="0" max="100" class="w-full mt-1 border rounded-lg p-2 text-xs">
              </div>
              <div>
                <label class="text-xs font-semibold text-slate-600">Teklif Geçerlilik / Not</label>
                <input id="q-footer-note" oninput="updateQuotePreview()" type="text" value="Teklifimiz 15 gün süreyle geçerlidir. Fiyatlara KDV dahildir." class="w-full mt-1 border rounded-lg p-2 text-xs">
              </div>
            </div>

            <div class="flex gap-2 pt-2">
              <button type="button" onclick="saveQuoteToDB()" class="flex-1 bg-sky-600 hover:bg-sky-700 text-white py-2.5 rounded-lg text-xs font-bold shadow-sm">
                <i class="fa-solid fa-floppy-disk mr-1"></i> Teklifi Kaydet
              </button>
              <button type="button" onclick="printQuotePreview()" class="px-4 bg-rose-600 hover:bg-rose-700 text-white py-2.5 rounded-lg text-xs font-bold shadow-sm">
                <i class="fa-solid fa-file-pdf mr-1"></i> Yazdır / PDF İndir
              </button>
            </div>
          </div>

          <div class="lg:col-span-7">
            <div id="quote-a4-preview" class="bg-white p-8 rounded-xl shadow-sm border border-slate-200 min-h-[680px] flex flex-col justify-between text-slate-800 print-only">
              <div>
                <div class="flex justify-between items-start border-b-2 border-slate-800 pb-5">
                  <div class="flex items-start gap-3.5">
                    <img id="qp-comp-logo" src="" alt="Firma Logosu" class="hidden h-14 max-w-[140px] object-contain">
                    <div>
                      <h2 id="qp-comp-name" class="text-xl font-extrabold text-slate-900">Mim Reklam Ajansı Ltd. Şti.</h2>
                      <p id="qp-comp-addr" class="text-xs text-slate-500 mt-1"></p>
                      <p id="qp-comp-contact" class="text-xs text-slate-500"></p>
                      <p id="qp-comp-tax" class="text-xs text-slate-500"></p>
                    </div>
                  </div>
                  <div class="text-right">
                    <span class="inline-block bg-sky-600 text-white text-xs font-bold px-3 py-1 rounded uppercase tracking-wider">Fiyat Teklifi</span>
                    <p class="text-xs font-bold text-slate-700 mt-2">No: <span id="qp-no">TASLAK</span> (<span id="qp-ver">v1</span>)</p>
                    <p class="text-xs text-slate-500">Tarih: <span id="qp-date"></span></p>
                  </div>
                </div>

                <div class="mt-5 bg-slate-50 p-4 rounded-lg border border-slate-200">
                  <div class="text-[11px] font-bold uppercase text-slate-400">Sayın / Müşteri Bilgileri</div>
                  <div id="qp-cust-name" class="font-bold text-base text-slate-900 mt-0.5">Müşteri Seçilmedi</div>
                  <div id="qp-cust-detail" class="text-xs text-slate-600 mt-0.5"></div>
                </div>

                <table class="w-full mt-6 text-left border-collapse text-xs">
                  <thead>
                    <tr class="bg-slate-800 text-white">
                      <th class="p-2.5 rounded-tl-md">Hizmet / Ürün Açıklaması</th>
                      <th class="p-2.5 text-center w-20">Miktar</th>
                      <th class="p-2.5 text-right w-28">Birim Fiyat</th>
                      <th class="p-2.5 text-right w-28 rounded-tr-md">Toplam</th>
                    </tr>
                  </thead>
                  <tbody id="qp-items-tbody" class="divide-y divide-slate-200"></tbody>
                </table>

                <div class="mt-5 flex justify-end">
                  <div class="w-64 space-y-1.5 text-xs">
                    <div class="flex justify-between text-slate-600"><span>Ara Toplam:</span><strong id="qp-subtotal">0,00 ₺</strong></div>
                    <div class="flex justify-between text-slate-600"><span>KDV (%<span id="qp-tax-label">20</span>):</span><strong id="qp-tax-amt">0,00 ₺</strong></div>
                    <div class="flex justify-between text-sm font-extrabold text-slate-900 border-t-2 border-slate-800 pt-1.5">
                      <span>GENEL TOPLAM:</span><span id="qp-grand-total" class="text-sky-700">0,00 ₺</span>
                    </div>
                  </div>
                </div>
              </div>

              <div class="border-t border-slate-200 pt-4 mt-8 text-xs text-slate-500 flex justify-between items-end">
                <div id="qp-footer" class="max-w-md"></div>
                <div class="text-right">
                  <div class="font-bold text-slate-700">Yetkili Kaşe / İmza</div>
                  <div class="h-8"></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- KAYDEDİLEN TEKLİFLER LİSTESİ -->
        <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200 space-y-3 no-print">
          <div class="flex justify-between items-center">
            <h3 class="font-bold text-slate-800 text-sm">Kaydedilen Teklifler (İncele, Yazdır veya PDF İndir)</h3>
            <span class="text-xs text-slate-400">Herhangi bir teklifi açıp sağdaki A4 önizlemede inceleyebilir veya PDF alabilirsiniz</span>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-left text-xs border-collapse">
              <thead class="bg-slate-50 border-b text-slate-600 uppercase">
                <tr>
                  <th class="p-3">Teklif No</th>
                  <th class="p-3">Müşteri</th>
                  <th class="p-3">Tarih</th>
                  <th class="p-3">Tutar (KDV Dahil)</th>
                  <th class="p-3">Durum</th>
                  <th class="p-3 text-right">İşlemler</th>
                </tr>
              </thead>
              <tbody id="quotes-history-tbody" class="divide-y divide-slate-100"></tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- 3. TOPLU & TEKLİ MAİL SEKMESİ -->
      <section id="tab-mail" class="hidden grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div class="lg:col-span-7 bg-white p-5 rounded-xl shadow-sm border border-slate-200 space-y-4">
          <h3 class="font-bold text-slate-800 border-b pb-2">E-Posta Gönderim Merkezi</h3>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label class="text-xs font-semibold text-slate-600">Gönderici Personel (SMTP Hesabı)</label>
              <select id="mail-sender" onchange="updateMailSignaturePreview()" class="w-full mt-1 border rounded-lg p-2 text-xs bg-white"></select>
            </div>
            <div>
              <label class="text-xs font-semibold text-slate-600">Hazır Şablon Seç</label>
              <select id="mail-template-select" onchange="applyMailTemplate()" class="w-full mt-1 border rounded-lg p-2 text-xs bg-white">
                <option value="">-- Şablon Seçiniz --</option>
              </select>
            </div>
          </div>

          <div class="bg-slate-50 p-3 rounded-lg border border-slate-200 space-y-2">
            <div class="flex items-center justify-between">
              <label class="text-xs font-bold text-slate-700">Alıcı Hedef Kitlesi</label>
              <select id="mail-target-mode" onchange="toggleMailTargetMode()" class="border rounded px-2 py-1 text-xs bg-white">
                <option value="single">Tekil Alıcı / Müşteri Seç</option>
                <option value="category">Kategoriye Göre Toplu Gönder</option>
                <option value="selected">Müşteri Listesinde Seçilenler</option>
              </select>
            </div>

            <div id="mail-target-single" class="grid grid-cols-1 md:grid-cols-2 gap-2">
              <select id="mail-cust-picker" onchange="fillSingleMailRecipient()" class="border rounded-lg p-2 text-xs bg-white">
                <option value="">Kayıtlı Müşteriden Seç...</option>
              </select>
              <input id="mail-to-email" type="email" placeholder="Alıcı E-Posta Adresi *" class="border rounded-lg p-2 text-xs bg-white">
            </div>

            <div id="mail-target-category" class="hidden flex items-center gap-3">
              <select id="mail-cat-picker" class="border rounded-lg p-2 text-xs bg-white flex-1">
                <option value="ALL">Tüm Müşteriler</option>
                <option value="Potansiyel">Potansiyel</option>
                <option value="Teklif Verildi">Teklif Verildi</option>
                <option value="Aktif Müşteri">Aktif Müşteri</option>
                <option value="VIP">VIP</option>
              </select>
            </div>

            <div id="mail-target-selected" class="hidden text-xs text-slate-600">
              Müşteriler sekmesinde işaretlediğiniz <strong id="mail-sel-count">0</strong> kişiye kişiselleştirilmiş mail gönderilecek.
            </div>
          </div>

          <div>
            <label class="text-xs font-semibold text-slate-600">Konu Başlığı</label>
            <input id="mail-subject" type="text" placeholder="Örn: {firma_adi} - Reklam & Medya Çözüm Önerimiz" class="w-full mt-1 border rounded-lg p-2 text-xs">
          </div>

          <div>
            <div class="flex justify-between items-center">
              <label class="text-xs font-semibold text-slate-600">E-Posta İçeriği</label>
              <span class="text-[11px] text-slate-400">Değişkenler: <code>{musteri_adi}</code>, <code>{firma_adi}</code></span>
            </div>
            <textarea id="mail-body" rows="6" placeholder="Merhaba {musteri_adi}, ..." class="w-full mt-1 border rounded-lg p-2.5 text-xs"></textarea>
          </div>

          <div class="bg-slate-50 p-3 rounded-lg border border-dashed border-slate-300 text-xs">
            <div class="flex justify-between items-center mb-1">
              <span class="font-bold text-slate-500 text-[11px] uppercase"><i class="fa-solid fa-signature mr-1"></i> Otomatik Eklenecek Mail İmzası</span>
              <button type="button" onclick="switchTab('settings')" class="text-sky-600 hover:underline text-[11px] font-semibold">İmzayı Düzenle</button>
            </div>
            <pre id="mail-sig-preview" class="font-sans text-slate-600 whitespace-pre-wrap text-xs">İmza tanımlanmamış.</pre>
          </div>

          <div class="flex items-center justify-between pt-2">
            <div id="mail-progress" class="text-xs font-semibold text-sky-700"></div>
            <button type="button" onclick="sendCampaignMails()" class="bg-sky-600 hover:bg-sky-700 text-white px-5 py-2.5 rounded-lg text-xs font-bold shadow-sm">
              <i class="fa-solid fa-paper-plane mr-1"></i> Gönderimi Başlat
            </button>
          </div>
        </div>

        <div class="lg:col-span-5 bg-white p-5 rounded-xl shadow-sm border border-slate-200 space-y-3">
          <h3 class="font-bold text-slate-800 border-b pb-2">Son Gönderim Logları</h3>
          <div id="mail-logs-list" class="space-y-2 max-h-[540px] overflow-y-auto text-xs"></div>
        </div>
      </section>

      <!-- 4. GÖREVLERİM & HATIRLATMA ALARM SEKMESİ -->
      <section id="tab-tasks" class="hidden grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div class="lg:col-span-4 bg-white p-5 rounded-xl shadow-sm border border-slate-200 space-y-3 h-fit">
          <h3 class="font-bold text-slate-800 border-b pb-2">Yeni Görev & Alarm Oluştur</h3>
          <div>
            <label class="text-xs font-semibold text-slate-600">Görev Başlığı *</label>
            <input id="task-title" type="text" placeholder="Örn: Teklif sonrası takip araması" class="w-full mt-1 border rounded-lg p-2 text-xs">
          </div>
          <div>
            <label class="text-xs font-semibold text-slate-600">İlgili Müşteri (Opsiyonel)</label>
            <select id="task-customer" class="w-full mt-1 border rounded-lg p-2 text-xs bg-white"></select>
          </div>
          <div>
            <label class="text-xs font-semibold text-slate-600">Atanan Personel (Uyarı Alacak Kişi)</label>
            <select id="task-staff" class="w-full mt-1 border rounded-lg p-2 text-xs bg-white"></select>
          </div>
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="text-xs font-semibold text-slate-600">Başlangıç Tarihi *</label>
              <input id="task-due" type="date" class="w-full mt-1 border rounded-lg p-2 text-xs">
            </div>
            <div>
              <label class="text-xs font-semibold text-slate-600">Başlangıç Saati *</label>
              <input id="task-time" type="time" value="10:00" class="w-full mt-1 border rounded-lg p-2 text-xs">
            </div>
          </div>
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="text-xs font-semibold text-slate-600">Sistem Uyarısı (Ne Kadar Önce?)</label>
              <select id="task-remind-mins" class="w-full mt-1 border rounded-lg p-2 text-xs bg-amber-50 border-amber-200 font-semibold text-amber-900">
                <option value="0">Tam Başlangıç Saatinde (0 dk)</option>
                <option value="15">15 Dakika Önce</option>
                <option value="30" selected>30 Dakika Önce (Yarım Saat)</option>
                <option value="60">1 Saat Önce (60 dk)</option>
                <option value="120">2 Saat Önce (120 dk)</option>
                <option value="1440">1 Gün Önce (24 Saat)</option>
              </select>
            </div>
            <div>
              <label class="text-xs font-semibold text-slate-600">Öncelik</label>
              <select id="task-priority" class="w-full mt-1 border rounded-lg p-2 text-xs bg-white">
                <option value="Düşük">Düşük</option>
                <option value="Orta" selected>Orta</option>
                <option value="Yüksek">Yüksek</option>
              </select>
            </div>
          </div>
          <div>
            <label class="text-xs font-semibold text-slate-600">Not</label>
            <textarea id="task-note" rows="2" class="w-full mt-1 border rounded-lg p-2 text-xs"></textarea>
          </div>
          <button type="button" onclick="addTask()" class="w-full bg-sky-600 hover:bg-sky-700 text-white py-2.5 rounded-lg text-xs font-bold">
            <i class="fa-solid fa-bell mr-1"></i> Görevi & Alarmı Kaydet
          </button>
        </div>

        <div class="lg:col-span-8 bg-white p-5 rounded-xl shadow-sm border border-slate-200 space-y-3">
          <div class="flex justify-between items-center border-b pb-2">
            <h3 class="font-bold text-slate-800">Görev & Hatırlatma Listesi</h3>
            <button type="button" onclick="checkTaskReminders(true)" class="text-xs bg-amber-50 text-amber-700 border border-amber-200 px-3 py-1 rounded-lg font-semibold hover:bg-amber-100">
              <i class="fa-solid fa-bell-ring mr-1"></i> Alarmları Şimdi Kontrol Et
            </button>
          </div>
          <div id="tasks-list" class="space-y-2.5"></div>
        </div>
      </section>

      <!-- 5. YÖNETİCİ RAPORLARI SEKMESİ -->
      <section id="tab-reports" class="hidden space-y-5">
        <div class="bg-white p-4 rounded-xl shadow-sm border border-slate-200 flex flex-wrap gap-3 items-end justify-between no-print">
          <div class="flex flex-wrap gap-3 items-end">
            <div>
              <label class="block text-[11px] font-bold text-slate-500 uppercase">Başlangıç Tarihi</label>
              <input id="rep-start-date" type="date" onchange="renderReports()" class="mt-1 border rounded-lg px-3 py-1.5 text-xs bg-white">
            </div>
            <div>
              <label class="block text-[11px] font-bold text-slate-500 uppercase">Bitiş Tarihi</label>
              <input id="rep-end-date" type="date" onchange="renderReports()" class="mt-1 border rounded-lg px-3 py-1.5 text-xs bg-white">
            </div>
            <div>
              <label class="block text-[11px] font-bold text-slate-500 uppercase">Müşteri Kategorisi</label>
              <select id="rep-category" onchange="renderReports()" class="mt-1 border rounded-lg px-3 py-1.5 text-xs bg-white">
                <option value="ALL">Tüm Kategoriler</option>
                <option value="Potansiyel">Potansiyel</option>
                <option value="Teklif Verildi">Teklif Verildi</option>
                <option value="Aktif Müşteri">Aktif Müşteri</option>
                <option value="VIP">VIP</option>
              </select>
            </div>
            <div>
              <label class="block text-[11px] font-bold text-slate-500 uppercase">Personel Filtresi</label>
              <select id="rep-staff" onchange="renderReports()" class="mt-1 border rounded-lg px-3 py-1.5 text-xs bg-white">
                <option value="ALL">Tüm Satış Ekibi</option>
              </select>
            </div>
            <button type="button" onclick="resetReportFilters()" class="px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-xs font-semibold">
              Filtreleri Sıfırla
            </button>
          </div>
          <button type="button" onclick="window.print()" class="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-xs font-bold shadow-sm">
            <i class="fa-solid fa-file-pdf mr-1"></i> Yönetici Raporunu PDF İndir / Yazdır
          </button>
        </div>

        <div id="executive-report-print" class="space-y-5 print-only">
          <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200 flex justify-between items-center">
            <div class="flex items-center gap-3.5">
              <img id="rep-comp-logo" src="" alt="Logo" class="hidden h-12 max-w-[120px] object-contain">
              <div>
                <h2 class="text-lg font-extrabold text-slate-900" id="rep-header-title">MİM REKLAM YÖNETİCİ SATIŞ & CRM PERFORMANS RAPORU</h2>
                <p class="text-xs text-slate-500 mt-0.5" id="rep-filter-summary">Tüm zamanlar ve tüm ekip verileri gösteriliyor</p>
              </div>
            </div>
            <div class="text-right text-xs text-slate-500">
              <div>Rapor Tarihi: <strong id="rep-generated-date"></strong></div>
              <div id="rep-comp-label" class="font-semibold text-slate-700"></div>
            </div>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div class="bg-white p-4 rounded-xl shadow-sm border-l-4 border-sky-500">
              <div class="text-xs font-bold text-slate-400 uppercase">Toplam Müşteri Portföyü</div>
              <div id="kpi-total-cust" class="text-2xl font-extrabold text-slate-900 mt-1">0</div>
              <div id="kpi-vip-cust" class="text-xs text-sky-700 mt-1 font-medium">0 VIP • 0 Aktif Müşteri</div>
            </div>
            <div class="bg-white p-4 rounded-xl shadow-sm border-l-4 border-amber-500">
              <div class="text-xs font-bold text-slate-400 uppercase">Toplam Teklif Hacmi</div>
              <div id="kpi-total-quote-amt" class="text-2xl font-extrabold text-slate-900 mt-1">0,00 ₺</div>
              <div id="kpi-quote-count" class="text-xs text-amber-700 mt-1 font-medium">0 adet teklif oluşturuldu</div>
            </div>
            <div class="bg-white p-4 rounded-xl shadow-sm border-l-4 border-emerald-500">
              <div class="text-xs font-bold text-slate-400 uppercase">Onaylanan Ciro (Kazanılan)</div>
              <div id="kpi-won-amt" class="text-2xl font-extrabold text-emerald-700 mt-1">0,00 ₺</div>
              <div id="kpi-conversion-rate" class="text-xs text-emerald-700 mt-1 font-medium">Dönüşüm Oranı: %0</div>
            </div>
            <div class="bg-white p-4 rounded-xl shadow-sm border-l-4 border-purple-500">
              <div class="text-xs font-bold text-slate-400 uppercase">Saha & Görüşme Aktivitesi</div>
              <div id="kpi-interactions" class="text-2xl font-extrabold text-slate-900 mt-1">0</div>
              <div id="kpi-tasks-done" class="text-xs text-purple-700 mt-1 font-medium">0 / 0 Görev Tamamlandı</div>
            </div>
          </div>

          <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">
            <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200">
              <h4 class="text-xs font-bold uppercase text-slate-600 mb-3">Müşteri Kategori Dağılımı</h4>
              <div class="h-56 flex items-center justify-center">
                <canvas id="chart-cust-category"></canvas>
              </div>
            </div>
            <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200">
              <h4 class="text-xs font-bold uppercase text-slate-600 mb-3">Teklif Durumları & Ciro Dağılımı</h4>
              <div class="h-56 flex items-center justify-center">
                <canvas id="chart-quote-status"></canvas>
              </div>
            </div>
            <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200">
              <h4 class="text-xs font-bold uppercase text-slate-600 mb-3">Personel Görüşme & Görev Performansı</h4>
              <div class="h-56 flex items-center justify-center">
                <canvas id="chart-staff-perf"></canvas>
              </div>
            </div>
          </div>

          <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200 space-y-3">
            <h4 class="text-xs font-bold uppercase text-slate-600">Filtrelenen Teklif & Satış Özeti</h4>
            <div class="overflow-x-auto">
              <table class="w-full text-left text-xs border-collapse">
                <thead class="bg-slate-50 border-b text-slate-600 uppercase">
                  <tr>
                    <th class="p-2.5">Teklif No</th>
                    <th class="p-2.5">Müşteri / Firma</th>
                    <th class="p-2.5">Tarih</th>
                    <th class="p-2.5">Durum</th>
                    <th class="p-2.5 text-right">Tutar (KDV Dahil)</th>
                  </tr>
                </thead>
                <tbody id="rep-quotes-tbody" class="divide-y divide-slate-100"></tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      <!-- 6. AYARLAR & LOGLAR SEKMESİ -->
      <section id="tab-settings" class="hidden space-y-6">
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200 space-y-3 h-fit">
            <h3 class="font-bold text-slate-800 border-b pb-2">Firma Kimliği, Logo & Teklif Başlığı</h3>

            <!-- FİRMA LOGOSU YÜKLEME ALANI -->
            <div class="bg-slate-50 p-3.5 rounded-xl border border-slate-200 flex items-center justify-between gap-4">
              <div class="flex items-center gap-3">
                <div class="w-16 h-16 rounded-lg border border-slate-300 bg-white flex items-center justify-center overflow-hidden p-1">
                  <img id="set-logo-preview" src="" alt="Logo Önizleme" class="hidden max-h-full max-w-full object-contain">
                  <span id="set-logo-empty" class="text-[11px] text-slate-400 text-center">Logo Yok</span>
                </div>
                <div>
                  <div class="text-xs font-bold text-slate-700">Kurumsal Firma Logosu</div>
                  <div class="text-[11px] text-slate-500">Teklif PDF'lerinde ve raporlarda görünür (PNG/JPG)</div>
                </div>
              </div>
              <div class="flex flex-col gap-1.5 shrink-0">
                <label class="px-3 py-1.5 bg-sky-600 hover:bg-sky-700 text-white rounded-lg text-xs font-semibold cursor-pointer text-center">
                  <i class="fa-solid fa-image mr-1"></i> Logo Seç
                  <input id="set-comp-logo-input" type="file" accept="image/*" onchange="handleLogoUpload(event)" class="hidden">
                </label>
                <button type="button" id="set-logo-remove-btn" onclick="removeCompanyLogo()" class="hidden px-3 py-1 bg-rose-50 hover:bg-rose-100 text-rose-600 rounded-lg text-[11px] font-semibold">
                  Logoyu Kaldır
                </button>
              </div>
            </div>

            <input id="set-comp-name" type="text" placeholder="Firma Ünvanı" class="w-full border rounded-lg p-2 text-xs">
            <input id="set-comp-addr" type="text" placeholder="Adres" class="w-full border rounded-lg p-2 text-xs">
            <div class="grid grid-cols-2 gap-2">
              <input id="set-comp-phone" type="text" placeholder="Telefon" class="border rounded-lg p-2 text-xs">
              <input id="set-comp-email" type="text" placeholder="Kurumsal E-Posta" class="border rounded-lg p-2 text-xs">
            </div>
            <input id="set-comp-tax" type="text" placeholder="Vergi Dairesi / No" class="w-full border rounded-lg p-2 text-xs">
            <button type="button" onclick="saveCompanySettings()" class="bg-sky-600 hover:bg-sky-700 text-white px-4 py-2 rounded-lg text-xs font-bold">Firma Bilgilerini & Logoyu Kaydet</button>
          </div>

          <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200 space-y-3">
            <div class="flex justify-between items-center border-b pb-2">
              <h3 id="staff-form-title" class="font-bold text-slate-800">Satış Ekibi, SMTP & Mail İmzası</h3>
              <button type="button" id="staff-cancel-btn" onclick="resetStaffForm()" class="hidden text-xs text-slate-500 hover:text-slate-800 underline">Yeni Ekleme Moduna Dön</button>
            </div>
            <input type="hidden" id="set-st-id">
            <div class="grid grid-cols-2 gap-2">
              <input id="set-st-name" type="text" placeholder="Ad Soyad *" class="border rounded-lg p-2 text-xs">
              <input id="set-st-title" type="text" placeholder="Ünvan (Örn: Müşteri Temsilcisi)" class="border rounded-lg p-2 text-xs">
            </div>
            <div class="grid grid-cols-2 gap-2">
              <input id="set-st-email" type="email" placeholder="E-Posta Adresi *" class="border rounded-lg p-2 text-xs">
              <input id="set-st-pass" type="password" placeholder="Gmail Uygulama Şifresi" class="border rounded-lg p-2 text-xs">
            </div>
            <div>
              <label class="text-[11px] font-semibold text-slate-500">E-Posta İmzası (Tüm giden maillerin altına eklenir)</label>
              <textarea id="set-st-sig" rows="3" placeholder="Saygılarımla,&#10;Ad Soyad | Ünvan&#10;Mim Reklam Ajansı | Tel: ..." class="w-full mt-1 border rounded-lg p-2 text-xs"></textarea>
            </div>
            <button type="button" id="staff-save-btn" onclick="saveStaffMember()" class="bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded-lg text-xs font-bold">+ Personel & İmza Ekle</button>

            <div class="pt-3 border-t">
              <div class="text-[11px] font-bold uppercase text-slate-400 mb-1">Kayıtlı Personeller (İmza düzenlemek için kaleme tıklayın)</div>
              <div id="staff-list" class="divide-y text-xs"></div>
            </div>
          </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200 space-y-3">
            <h3 class="font-bold text-slate-800 border-b pb-2">Hazır E-Posta Şablonları</h3>
            <input id="set-tpl-name" type="text" placeholder="Şablon Adı *" class="w-full border rounded-lg p-2 text-xs">
            <input id="set-tpl-sub" type="text" placeholder="Konu ({firma_adi}, {musteri_adi})" class="w-full border rounded-lg p-2 text-xs">
            <textarea id="set-tpl-body" rows="3" placeholder="İçerik..." class="w-full border rounded-lg p-2 text-xs"></textarea>
            <button type="button" onclick="addMailTemplate()" class="bg-sky-600 hover:bg-sky-700 text-white px-4 py-2 rounded-lg text-xs font-bold">+ Şablon Kaydet</button>
            <div id="templates-list" class="divide-y text-xs pt-2"></div>
          </div>

          <div class="bg-white p-5 rounded-xl shadow-sm border border-slate-200 space-y-3">
            <h3 class="font-bold text-slate-800 border-b pb-2">Sistem Denetim Kayıtları (Audit Logs)</h3>
            <div id="audit-logs-list" class="space-y-1.5 max-h-72 overflow-y-auto text-xs"></div>
          </div>
        </div>
      </section>

    </div>
  </main>
</div>

<!-- MÜŞTERİ EKLE / DÜZENLE MODALI -->
<div id="customer-modal" class="hidden fixed inset-0 bg-slate-900/60 flex items-center justify-center z-50 p-4 no-print">
  <div class="bg-white rounded-2xl max-w-lg w-full p-6 space-y-4 shadow-xl">
    <div class="flex justify-between items-center border-b pb-3">
      <h3 id="cust-modal-title" class="font-bold text-base text-slate-800">Yeni Müşteri Kartı</h3>
      <button type="button" onclick="closeCustomerModal()" class="text-slate-400 hover:text-slate-700"><i class="fa-solid fa-xmark text-lg"></i></button>
    </div>
    <input type="hidden" id="m-cust-id">
    <div class="grid grid-cols-2 gap-3 text-xs">
      <div>
        <label class="font-semibold text-slate-600">Ad Soyad *</label>
        <input id="m-cust-name" type="text" class="w-full mt-1 border rounded-lg p-2">
      </div>
      <div>
        <label class="font-semibold text-slate-600">Firma Adı</label>
        <input id="m-cust-company" type="text" class="w-full mt-1 border rounded-lg p-2">
      </div>
      <div>
        <label class="font-semibold text-slate-600">Ünvan / Pozisyon</label>
        <input id="m-cust-title" type="text" class="w-full mt-1 border rounded-lg p-2">
      </div>
      <div>
        <label class="font-semibold text-slate-600">Kategori</label>
        <select id="m-cust-cat" class="w-full mt-1 border rounded-lg p-2 bg-white">
          <option value="Potansiyel">Potansiyel</option>
          <option value="Teklif Verildi">Teklif Verildi</option>
          <option value="Aktif Müşteri">Aktif Müşteri</option>
          <option value="VIP">VIP</option>
        </select>
      </div>
      <div>
        <label class="font-semibold text-slate-600">E-Posta (Virgülle çoklu)</label>
        <input id="m-cust-emails" type="text" class="w-full mt-1 border rounded-lg p-2">
      </div>
      <div>
        <label class="font-semibold text-slate-600">Telefon</label>
        <input id="m-cust-phone" type="text" class="w-full mt-1 border rounded-lg p-2">
      </div>
      <div>
        <label class="font-semibold text-slate-600">Şehir</label>
        <input id="m-cust-city" type="text" class="w-full mt-1 border rounded-lg p-2">
      </div>
      <div>
        <label class="font-semibold text-slate-600">Vergi Dairesi / No</label>
        <input id="m-cust-tax" type="text" class="w-full mt-1 border rounded-lg p-2">
      </div>
    </div>
    <div class="text-xs">
      <label class="font-semibold text-slate-600">Genel Notlar</label>
      <textarea id="m-cust-notes" rows="2" class="w-full mt-1 border rounded-lg p-2"></textarea>
    </div>
    <div class="flex justify-end gap-2 pt-2">
      <button type="button" onclick="closeCustomerModal()" class="px-4 py-2 bg-slate-100 hover:bg-slate-200 rounded-lg text-xs font-semibold">Vazgeç</button>
      <button type="button" onclick="saveCustomerForm()" class="px-5 py-2 bg-sky-600 hover:bg-sky-700 text-white rounded-lg text-xs font-bold">Kaydet</button>
    </div>
  </div>
</div>

<script>
window.onerror = function(msg, url, line) {
  console.error("Arayüz Hatası (Satır " + line + "): " + msg);
};

let DB = { customers: [], interactions: [], quotes: [], tasks: [], staff: [], templates: [], mail_logs: [], audit_logs: [], company: {} };
let selectedCustomerIds = new Set();
let activeCustomerId = null;
let quoteCustomerMode = 'registered';
let quoteItems = [{ desc: "Sosyal Medya & Dijital Reklam Yönetimi", qty: 1, price: 25000 }];
let currentQuoteNo = null;
let currentQuoteVersion = 1;
let activeAlarmTaskId = null;
let tempCompanyLogoBase64 = "";
let chartCatInstance = null;
let chartQuoteInstance = null;
let chartStaffInstance = null;

function applyCompanyLogoUI() {
  const logo = DB.company.logo || '';
  const sbImg = document.getElementById('sidebar-logo-img');
  const sbFb = document.getElementById('sidebar-logo-fallback');
  if (sbImg && sbFb) {
    if (logo) {
      sbImg.src = logo;
      sbImg.classList.remove('hidden');
      sbFb.classList.add('hidden');
    } else {
      sbImg.classList.add('hidden');
      sbFb.classList.remove('hidden');
    }
  }
  const qpLogo = document.getElementById('qp-comp-logo');
  if (qpLogo) {
    if (logo) { qpLogo.src = logo; qpLogo.classList.remove('hidden'); }
    else { qpLogo.classList.add('hidden'); }
  }
  const repLogo = document.getElementById('rep-comp-logo');
  if (repLogo) {
    if (logo) { repLogo.src = logo; repLogo.classList.remove('hidden'); }
    else { repLogo.classList.add('hidden'); }
  }
}

async function loadBootstrap() {
  const badge = document.getElementById('sys-status-badge');
  try {
    const res = await fetch('/api/bootstrap?t=' + Date.now());
    if (!res.ok) throw new Error('Sunucu yanıt vermedi: ' + res.status);
    DB = await res.json();
    document.getElementById('active-company-name').textContent = DB.company.name || 'Mim Reklam Ajansı';
    applyCompanyLogoUI();
    renderCustomers();
    populateDropdowns();
    updateMailSignaturePreview();
    renderQuoteItemsEditor();
    updateQuotePreview();
    renderQuotesHistory();
    renderTasks();
    renderMailLogs();
    renderSettings();
    renderReports();
    if (activeCustomerId) openCustomerFullCard(activeCustomerId, false);
    if (badge) {
      badge.className = "flex items-center gap-2 text-emerald-400";
      badge.innerHTML = '<i class="fa-solid fa-circle-check"></i> Sistem Hazır';
    }
    checkTaskReminders();
  } catch (err) {
    console.error(err);
    if (badge) {
      badge.className = "flex items-center gap-2 text-rose-400";
      badge.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Bağlantı Hatası';
    }
  }
}

function switchTab(tab) {
  ['customers', 'customer-detail', 'quotes', 'mail', 'tasks', 'reports', 'settings'].forEach(t => {
    const el = document.getElementById('tab-' + t);
    if (el) el.classList.add('hidden');
  });
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('tab-active'));
  const target = document.getElementById('tab-' + tab);
  if (target) target.classList.remove('hidden');
  const navBtn = document.getElementById('nav-' + (tab === 'customer-detail' ? 'customers' : tab));
  if (navBtn) navBtn.classList.add('tab-active');

  const titles = {
    customers: 'Müşteri Portföyü',
    'customer-detail': '360° Müşteri Kartı',
    quotes: 'Teklif Oluşturucu, İnceleme & PDF',
    mail: 'Toplu & Tekli E-Posta Merkezi',
    tasks: 'Görev & Akıllı Hatırlatma Yönetimi',
    reports: 'Yönetici Satış & CRM Raporları',
    settings: 'Sistem Ayarları & Loglar'
  };
  document.getElementById('page-title').textContent = titles[tab] || 'Mim Reklam CRM';
  if (tab === 'reports') renderReports();
}

/* ---------------- MÜŞTERİLER ---------------- */
function getCategoryBadge(cat) {
  const map = {
    'Potansiyel': 'bg-slate-100 text-slate-700',
    'Teklif Verildi': 'bg-amber-100 text-amber-800',
    'Aktif Müşteri': 'bg-emerald-100 text-emerald-800',
    'VIP': 'bg-purple-100 text-purple-800'
  };
  return `<span class="px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${map[cat] || 'bg-slate-100'}">${cat}</span>`;
}

function renderCustomers() {
  const q = document.getElementById('cust-search').value.toLowerCase();
  const cat = document.getElementById('cust-filter').value;
  const sort = document.getElementById('cust-sort').value;

  let list = DB.customers.filter(c => {
    const matchQ = ((c.full_name || '') + ' ' + (c.company || '') + ' ' + (c.emails || '') + ' ' + (c.phone || '')).toLowerCase().includes(q);
    const matchCat = cat === 'ALL' || c.category === cat;
    return matchQ && matchCat;
  });

  if (sort === 'AZ') list.sort((a, b) => a.full_name.localeCompare(b.full_name));
  if (sort === 'ZA') list.sort((a, b) => b.full_name.localeCompare(a.full_name));

  const tbody = document.getElementById('customers-tbody');
  if (!list.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="p-8 text-center text-slate-400">Kayıtlı müşteri bulunamadı.</td></tr>`;
    return;
  }

  tbody.innerHTML = list.map(c => `
    <tr class="hover:bg-slate-50 transition">
      <td class="p-3.5 no-print"><input type="checkbox" ${selectedCustomerIds.has(c.id) ? 'checked' : ''} onchange="toggleRowSelect(${c.id}, this.checked)"></td>
      <td class="p-3.5">
        <button type="button" onclick="openCustomerFullCard(${c.id})" class="font-bold text-sky-700 hover:underline text-left">${c.full_name}</button>
        <div class="text-[11px] text-slate-400">${c.title || ''}</div>
      </td>
      <td class="p-3.5 font-medium text-slate-700">${c.company || '-'}</td>
      <td class="p-3.5 text-xs text-slate-600">${c.emails || '-'}</td>
      <td class="p-3.5 text-xs text-slate-600">${c.phone || '-'}</td>
      <td class="p-3.5">${getCategoryBadge(c.category)}</td>
      <td class="p-3.5 text-right space-x-1 no-print">
        <button type="button" onclick="openCustomerFullCard(${c.id})" class="px-2.5 py-1 bg-sky-50 text-sky-700 hover:bg-sky-100 rounded text-xs font-semibold">Kartı Aç</button>
        <button type="button" onclick="openCustomerModal(${c.id})" class="p-1.5 text-slate-500 hover:text-amber-600" title="Düzenle"><i class="fa-solid fa-pen"></i></button>
        <button type="button" onclick="deleteSingleCustomer(${c.id})" class="p-1.5 text-slate-500 hover:text-rose-600" title="Sil"><i class="fa-solid fa-trash"></i></button>
      </td>
    </tr>
  `).join('');
  updateBulkBar();
}

function toggleRowSelect(id, checked) {
  if (checked) selectedCustomerIds.add(id); else selectedCustomerIds.delete(id);
  updateBulkBar();
}

function toggleSelectAll(cb) {
  if (cb.checked) DB.customers.forEach(c => selectedCustomerIds.add(c.id));
  else selectedCustomerIds.clear();
  renderCustomers();
}

function updateBulkBar() {
  const bar = document.getElementById('bulk-bar');
  document.getElementById('selected-count').textContent = selectedCustomerIds.size;
  document.getElementById('mail-sel-count').textContent = selectedCustomerIds.size;
  bar.classList.toggle('hidden', selectedCustomerIds.size === 0);
}

async function bulkUpdateCategory() {
  const category = document.getElementById('bulk-cat-select').value;
  await fetch('/api/customers/bulk_action', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ action: 'category', ids: Array.from(selectedCustomerIds), category })
  });
  selectedCustomerIds.clear();
  await loadBootstrap();
}

async function bulkDelete() {
  if (!confirm('Seçili müşteriler silinecek. Emin misiniz?')) return;
  await fetch('/api/customers/bulk_action', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ action: 'delete', ids: Array.from(selectedCustomerIds) })
  });
  selectedCustomerIds.clear();
  await loadBootstrap();
}

async function deleteSingleCustomer(id) {
  if (!confirm('Bu müşteri ve görüşme geçmişi silinecek. Onaylıyor musunuz?')) return;
  await fetch('/api/customers/' + id, { method: 'DELETE' });
  await loadBootstrap();
}

function openCustomerModal(id = null) {
  document.getElementById('m-cust-id').value = id || '';
  if (id) {
    const c = DB.customers.find(x => x.id === id);
    if (!c) return;
    document.getElementById('cust-modal-title').textContent = 'Müşteri Bilgilerini Düzenle';
    document.getElementById('m-cust-name').value = c.full_name;
    document.getElementById('m-cust-company').value = c.company || '';
    document.getElementById('m-cust-title').value = c.title || '';
    document.getElementById('m-cust-cat').value = c.category || 'Potansiyel';
    document.getElementById('m-cust-emails').value = c.emails || '';
    document.getElementById('m-cust-phone').value = c.phone || '';
    document.getElementById('m-cust-city').value = c.city || '';
    document.getElementById('m-cust-tax').value = c.tax_info || '';
    document.getElementById('m-cust-notes').value = c.notes || '';
  } else {
    document.getElementById('cust-modal-title').textContent = 'Yeni Müşteri Kartı';
    ['name', 'company', 'title', 'emails', 'phone', 'city', 'tax', 'notes'].forEach(f => document.getElementById('m-cust-' + f).value = '');
    document.getElementById('m-cust-cat').value = 'Potansiyel';
  }
  document.getElementById('customer-modal').classList.remove('hidden');
}

function closeCustomerModal() {
  document.getElementById('customer-modal').classList.add('hidden');
}

async function saveCustomerForm() {
  const full_name = document.getElementById('m-cust-name').value.trim();
  if (!full_name) return alert('Ad Soyad zorunludur.');
  const res = await fetch('/api/customers', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      id: document.getElementById('m-cust-id').value || null,
      full_name,
      company: document.getElementById('m-cust-company').value,
      title: document.getElementById('m-cust-title').value,
      category: document.getElementById('m-cust-cat').value,
      emails: document.getElementById('m-cust-emails').value,
      phone: document.getElementById('m-cust-phone').value,
      city: document.getElementById('m-cust-city').value,
      tax_info: document.getElementById('m-cust-tax').value,
      notes: document.getElementById('m-cust-notes').value
    })
  });
  if (!res.ok) return alert('Kayıt sırasında hata oluştu.');
  closeCustomerModal();
  await loadBootstrap();
}

/* ---------------- TAM SAYFA MÜŞTERİ KARTI ---------------- */
function openCustomerFullCard(id, doSwitch = true) {
  activeCustomerId = id;
  const c = DB.customers.find(x => x.id === id);
  if (!c) return;

  document.getElementById('full-det-name').textContent = c.full_name;
  document.getElementById('full-det-cat').textContent = c.category;
  document.getElementById('full-det-sub').textContent = `${c.company || 'Bireysel'} • ${c.title || 'Ünvan Belirtilmedi'} • Kayıt: ${c.created_at}`;

  const fields = [
    ['Firma', c.company], ['Ünvan', c.title], ['Kategori', c.category],
    ['E-Posta', c.emails], ['Telefon', c.phone], ['Şehir', c.city],
    ['Vergi Bilgisi', c.tax_info], ['Kayıt Tarihi', c.created_at], ['Notlar', c.notes]
  ];
  document.getElementById('full-det-info-grid').innerHTML = fields.map(([k, v]) => `
    <div class="bg-slate-50 p-3.5 rounded-lg border border-slate-200">
      <div class="text-[11px] font-bold uppercase text-slate-400">${k}</div>
      <div class="font-semibold text-slate-800 mt-1">${v || '-'}</div>
    </div>
  `).join('');

  const ints = DB.interactions.filter(i => i.customer_id === id);
  document.getElementById('full-det-interactions').innerHTML = ints.length ? ints.map(i => `
    <div class="p-4 rounded-xl border border-slate-200 bg-white flex justify-between items-start">
      <div>
        <span class="text-xs font-bold text-sky-700 bg-sky-50 px-2 py-0.5 rounded">${i.type}</span>
        <span class="text-xs text-slate-500 ml-2"><i class="fa-solid fa-user-tie mr-1"></i>${i.staff_name}</span>
        <p class="text-sm text-slate-800 mt-2">${i.summary}</p>
      </div>
      <span class="text-[11px] text-slate-400">${i.created_at}</span>
    </div>
  `).join('') : `<p class="text-xs text-slate-400 text-center py-6">Henüz görüşme kaydı girilmemiş.</p>`;

  const qts = DB.quotes.filter(q => q.customer_id === id);
  document.getElementById('full-det-quotes').innerHTML = qts.length ? qts.map(q => `
    <div class="p-4 rounded-xl border border-slate-200 bg-slate-50 flex flex-wrap justify-between items-center gap-3">
      <div>
        <div class="font-bold text-slate-900">${q.quote_no} <span class="text-xs text-sky-700">(v${q.version})</span></div>
        <div class="text-xs text-slate-500">Tarih: ${q.created_at} • Tutar: <strong>${formatTRY(q.total_amount)}</strong></div>
      </div>
      <div class="flex items-center gap-2">
        <span class="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800">${q.status}</span>
        <button type="button" onclick="inspectQuote(${q.id}, false)" class="px-3 py-1.5 bg-sky-600 text-white hover:bg-sky-700 rounded-lg text-xs font-semibold">İncele / PDF</button>
        <button type="button" onclick="loadQuoteAsRevision(${q.id})" class="px-3 py-1.5 bg-white border border-slate-300 hover:bg-slate-100 rounded-lg text-xs font-semibold">Revize Et (v${q.version + 1})</button>
      </div>
    </div>
  `).join('') : `<p class="text-xs text-slate-400 text-center py-6">Bu müşteriye ait teklif bulunmuyor.</p>`;

  if (doSwitch) switchTab('customer-detail');
}

function switchCardSubtab(sub) {
  ['info', 'interactions', 'quotes'].forEach(s => {
    document.getElementById('csub-' + s).classList.add('hidden');
    document.getElementById('csub-btn-' + s).classList.remove('subtab-active');
  });
  document.getElementById('csub-' + sub).classList.remove('hidden');
  document.getElementById('csub-btn-' + sub).classList.add('subtab-active');
}

async function addInteraction() {
  const summary = document.getElementById('int-summary').value.trim();
  if (!summary || !activeCustomerId) return alert('Görüşme özeti giriniz.');
  await fetch('/api/interactions', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      customer_id: activeCustomerId,
      type: document.getElementById('int-type').value,
      staff_name: document.getElementById('int-staff').value,
      summary
    })
  });
  document.getElementById('int-summary').value = '';
  await loadBootstrap();
}

function goCreateQuoteForCustomer() {
  switchTab('quotes');
  resetQuoteForm();
  setQuoteCustomerMode('registered');
  document.getElementById('q-customer').value = activeCustomerId;
  updateQuotePreview();
}

/* ---------------- TEKLİF OLUŞTURUCU, İNCELEME & PDF ---------------- */
function setQuoteCustomerMode(mode) {
  quoteCustomerMode = mode;
  document.getElementById('q-reg-box').classList.toggle('hidden', mode !== 'registered');
  document.getElementById('q-man-box').classList.toggle('hidden', mode !== 'manual');
  document.getElementById('qmode-reg-btn').className = mode === 'registered' ? 'px-2.5 py-1 bg-sky-600 text-white' : 'px-2.5 py-1 bg-white text-slate-600';
  document.getElementById('qmode-man-btn').className = mode === 'manual' ? 'px-2.5 py-1 bg-sky-600 text-white' : 'px-2.5 py-1 bg-white text-slate-600';
  updateQuotePreview();
}

function filterQuoteCustomerDropdown() {
  const q = document.getElementById('q-cust-search').value.toLowerCase();
  const sel = document.getElementById('q-customer');
  const filtered = DB.customers.filter(c => ((c.full_name || '') + ' ' + (c.company || '')).toLowerCase().includes(q));
  sel.innerHTML = `<option value="">-- Müşteri Seçiniz --</option>` + filtered.map(c =>
    `<option value="${c.id}">${c.full_name} ${c.company ? '(' + c.company + ')' : ''}</option>`
  ).join('');
  updateQuotePreview();
}

function renderQuoteItemsEditor() {
  const wrap = document.getElementById('q-items-container');
  wrap.innerHTML = quoteItems.map((it, idx) => `
    <div class="grid grid-cols-12 gap-1.5 items-center bg-slate-50 p-2 rounded-lg border border-slate-200">
      <input type="text" value="${it.desc}" oninput="quoteItems[${idx}].desc=this.value; updateQuotePreview()" placeholder="Hizmet/Ürün" class="col-span-6 border rounded p-1.5 text-xs bg-white">
      <input type="number" value="${it.qty}" min="1" oninput="quoteItems[${idx}].qty=parseFloat(this.value)||0; updateQuotePreview()" class="col-span-2 border rounded p-1.5 text-xs text-center bg-white">
      <input type="number" value="${it.price}" min="0" oninput="quoteItems[${idx}].price=parseFloat(this.value)||0; updateQuotePreview()" class="col-span-3 border rounded p-1.5 text-xs text-right bg-white">
      <button type="button" onclick="removeQuoteItem(${idx})" class="col-span-1 text-rose-500 hover:text-rose-700 text-center"><i class="fa-solid fa-trash"></i></button>
    </div>
  `).join('');
}

function addQuoteItemRow() {
  quoteItems.push({ desc: "Prodüksiyon & Kreatif Tasarım Hizmeti", qty: 1, price: 7500 });
  renderQuoteItemsEditor();
  updateQuotePreview();
}

function removeQuoteItem(idx) {
  if (quoteItems.length <= 1) return;
  quoteItems.splice(idx, 1);
  renderQuoteItemsEditor();
  updateQuotePreview();
}

function formatTRY(val) {
  return new Intl.NumberFormat('tr-TR', { style: 'currency', currency: 'TRY' }).format(val || 0);
}

function updateQuotePreview(customDate = null) {
  applyCompanyLogoUI();
  document.getElementById('qp-comp-name').textContent = DB.company.name || 'Mim Reklam Ajansı Ltd. Şti.';
  document.getElementById('qp-comp-addr').textContent = DB.company.address || '';
  document.getElementById('qp-comp-contact').textContent = `${DB.company.phone || ''} • ${DB.company.email || ''}`;
  document.getElementById('qp-comp-tax').textContent = DB.company.tax || '';
  document.getElementById('qp-no').textContent = currentQuoteNo || 'TASLAK';
  document.getElementById('qp-ver').textContent = 'v' + currentQuoteVersion;
  document.getElementById('qp-date').textContent = customDate || new Date().toLocaleDateString('tr-TR');

  if (quoteCustomerMode === 'registered') {
    const cid = parseInt(document.getElementById('q-customer').value);
    const c = DB.customers.find(x => x.id === cid);
    document.getElementById('qp-cust-name').textContent = c ? `${c.company ? c.company + ' - ' : ''}${c.full_name}` : 'Müşteri Seçilmedi';
    document.getElementById('qp-cust-detail').textContent = c ? `${c.emails || ''} ${c.phone ? '• ' + c.phone : ''} ${c.tax_info ? '• ' + c.tax_info : ''}` : '';
  } else {
    document.getElementById('qp-cust-name').textContent = document.getElementById('q-man-name').value || 'Manuel Müşteri';
    document.getElementById('qp-cust-detail').textContent = document.getElementById('q-man-contact').value || '';
  }

  let sub = 0;
  document.getElementById('qp-items-tbody').innerHTML = quoteItems.map(it => {
    const rowTot = (it.qty || 0) * (it.price || 0);
    sub += rowTot;
    return `
      <tr>
        <td class="p-2.5 font-medium">${it.desc || '-'}</td>
        <td class="p-2.5 text-center">${it.qty}</td>
        <td class="p-2.5 text-right">${formatTRY(it.price)}</td>
        <td class="p-2.5 text-right font-semibold">${formatTRY(rowTot)}</td>
      </tr>
    `;
  }).join('');

  const taxRate = parseFloat(document.getElementById('q-tax-rate').value) || 0;
  const taxAmt = sub * (taxRate / 100);
  const grand = sub + taxAmt;

  document.getElementById('qp-tax-label').textContent = taxRate;
  document.getElementById('qp-subtotal').textContent = formatTRY(sub);
  document.getElementById('qp-tax-amt').textContent = formatTRY(taxAmt);
  document.getElementById('qp-grand-total').textContent = formatTRY(grand);
  document.getElementById('qp-footer').textContent = document.getElementById('q-footer-note').value;
  return grand;
}

function printQuotePreview() {
  const prevTitle = document.title;
  const qno = currentQuoteNo || 'Taslak_Teklif';
  document.title = `MimReklam_Teklif_${qno}_v${currentQuoteVersion}`;
  window.print();
  document.title = prevTitle;
}

function inspectQuote(id, autoPrint = false) {
  const q = DB.quotes.find(x => x.id === id);
  if (!q) return;
  switchTab('quotes');
  currentQuoteNo = q.quote_no;
  currentQuoteVersion = q.version || 1;
  document.getElementById('quote-rev-badge').textContent = `İnceleniyor: ${q.quote_no} (v${currentQuoteVersion})`;

  if (q.customer_id) {
    setQuoteCustomerMode('registered');
    document.getElementById('q-customer').value = q.customer_id;
  } else {
    setQuoteCustomerMode('manual');
    document.getElementById('q-man-name').value = q.manual_customer_name || '';
    document.getElementById('q-man-contact').value = q.manual_customer_contact || '';
  }
  quoteItems = JSON.parse(q.items_json || '[]');
  document.getElementById('q-tax-rate').value = q.tax_rate;
  document.getElementById('q-footer-note').value = q.footer_note || '';
  renderQuoteItemsEditor();
  updateQuotePreview(q.created_at);

  document.getElementById('quote-a4-preview').scrollIntoView({ behavior: 'smooth' });
  if (autoPrint) {
    setTimeout(() => printQuotePreview(), 350);
  }
}

async function saveQuoteToDB() {
  const grand = updateQuotePreview();
  const cid = quoteCustomerMode === 'registered' ? (parseInt(document.getElementById('q-customer').value) || null) : null;
  const manName = quoteCustomerMode === 'manual' ? document.getElementById('q-man-name').value.trim() : '';
  if (!cid && !manName) return alert('Lütfen kayıtlı bir müşteri seçin veya manuel müşteri adı yazın.');

  const res = await fetch('/api/quotes', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      quote_no: currentQuoteNo,
      version: currentQuoteVersion,
      customer_id: cid,
      manual_customer_name: manName,
      manual_customer_contact: document.getElementById('q-man-contact').value,
      items: quoteItems,
      tax_rate: parseFloat(document.getElementById('q-tax-rate').value) || 0,
      total_amount: grand,
      footer_note: document.getElementById('q-footer-note').value
    })
  });
  const out = await res.json();
  currentQuoteNo = out.quote_no;
  alert(`Teklif başarıyla kaydedildi: ${out.quote_no} (v${currentQuoteVersion})`);
  await loadBootstrap();
}

function loadQuoteAsRevision(id) {
  const q = DB.quotes.find(x => x.id === id);
  if (!q) return;
  switchTab('quotes');
  currentQuoteNo = q.quote_no;
  currentQuoteVersion = (q.version || 1) + 1;
  document.getElementById('quote-rev-badge').textContent = `Revizyon: ${q.quote_no} (v${currentQuoteVersion})`;
  if (q.customer_id) {
    setQuoteCustomerMode('registered');
    document.getElementById('q-customer').value = q.customer_id;
  } else {
    setQuoteCustomerMode('manual');
    document.getElementById('q-man-name').value = q.manual_customer_name || '';
    document.getElementById('q-man-contact').value = q.manual_customer_contact || '';
  }
  quoteItems = JSON.parse(q.items_json || '[]');
  document.getElementById('q-tax-rate').value = q.tax_rate;
  document.getElementById('q-footer-note').value = q.footer_note || '';
  renderQuoteItemsEditor();
  updateQuotePreview();
}

function resetQuoteForm() {
  currentQuoteNo = null;
  currentQuoteVersion = 1;
  document.getElementById('quote-rev-badge').textContent = 'Yeni Teklif (v1)';
  quoteItems = [{ desc: "Sosyal Medya & Dijital Reklam Yönetimi", qty: 1, price: 25000 }];
  renderQuoteItemsEditor();
  updateQuotePreview();
}

function renderQuotesHistory() {
  const tbody = document.getElementById('quotes-history-tbody');
  tbody.innerHTML = DB.quotes.map(q => {
    const c = DB.customers.find(x => x.id === q.customer_id);
    const cname = c ? `${c.full_name} (${c.company || '-'})` : (q.manual_customer_name || 'Manuel');
    return `
      <tr class="hover:bg-slate-50">
        <td class="p-3 font-bold text-sky-700">${q.quote_no} <span class="text-slate-400">v${q.version}</span></td>
        <td class="p-3">${cname}</td>
        <td class="p-3 text-slate-500">${q.created_at}</td>
        <td class="p-3 font-bold">${formatTRY(q.total_amount)}</td>
        <td class="p-3">
          <select onchange="updateQuoteStatus(${q.id}, this.value)" class="border rounded px-2 py-1 text-xs bg-white">
            <option ${q.status==='Gönderildi'?'selected':''}>Gönderildi</option>
            <option ${q.status==='Onaylandı'?'selected':''}>Onaylandı</option>
            <option ${q.status==='Reddedildi'?'selected':''}>Reddedildi</option>
          </select>
        </td>
        <td class="p-3 text-right space-x-1.5">
          <button type="button" onclick="inspectQuote(${q.id}, false)" class="px-2.5 py-1 bg-sky-50 text-sky-700 hover:bg-sky-100 rounded font-semibold" title="A4 Önizlemede Aç">
            <i class="fa-solid fa-eye mr-1"></i> İncele
          </button>
          <button type="button" onclick="inspectQuote(${q.id}, true)" class="px-2.5 py-1 bg-rose-50 text-rose-700 hover:bg-rose-100 rounded font-semibold" title="Yazdır veya PDF Olarak Kaydet">
            <i class="fa-solid fa-file-pdf mr-1"></i> PDF / Yazdır
          </button>
          <button type="button" onclick="loadQuoteAsRevision(${q.id})" class="px-2 py-1 bg-slate-100 text-slate-700 hover:bg-slate-200 rounded font-semibold">Revize</button>
          <button type="button" onclick="deleteQuote(${q.id})" class="text-rose-500 hover:text-rose-700 p-1"><i class="fa-solid fa-trash"></i></button>
        </td>
      </tr>
    `;
  }).join('') || `<tr><td colspan="6" class="p-6 text-center text-slate-400">Henüz teklif oluşturulmadı.</td></tr>`;
}

async function updateQuoteStatus(id, status) {
  await fetch('/api/quotes/' + id, {
    method: 'PATCH', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ status })
  });
  await loadBootstrap();
}

async function deleteQuote(id) {
  if (!confirm('Teklif silinsin mi?')) return;
  await fetch('/api/quotes/' + id, { method: 'DELETE' });
  await loadBootstrap();
}

/* ---------------- GÖREVLER & DAKİKA/SAAT ÖNCESİ SİSTEM UYARISI ---------------- */
function playAlarmBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.35);
  } catch (e) {}
}

function checkTaskReminders(manualTrigger = false) {
  if (!document.getElementById('task-alarm-modal').classList.contains('hidden')) return;
  const activeUser = document.getElementById('active-user-select')?.value || 'ALL';
  const now = new Date();

  const dueTask = DB.tasks.find(t => {
    if (t.status === 'Tamamlandı' || t.reminded === 1) return false;
    if (activeUser !== 'ALL' && t.assigned_to !== activeUser) return false;
    if (!t.due_date) return false;

    const timeStr = t.due_time || '09:00';
    const targetDt = new Date(`${t.due_date}T${timeStr}:00`);
    if (isNaN(targetDt.getTime())) return false;

    const remindMins = parseInt(t.remind_before_mins || 0);
    const triggerDt = new Date(targetDt.getTime() - remindMins * 60000);
    return now >= triggerDt;
  });

  if (dueTask) {
    activeAlarmTaskId = dueTask.id;
    const c = DB.customers.find(x => x.id === dueTask.customer_id);
    document.getElementById('alarm-task-title').textContent = dueTask.title;
    document.getElementById('alarm-task-staff').textContent = dueTask.assigned_to;
    document.getElementById('alarm-task-cust').textContent = c ? `${c.full_name} (${c.company || '-'})` : 'Genel Görev';
    document.getElementById('alarm-task-time').textContent = `${dueTask.due_date} • ${dueTask.due_time || '09:00'} (${dueTask.remind_before_mins} dk öncesi uyarısı)`;
    document.getElementById('alarm-task-note').textContent = dueTask.note || 'Ek açıklama girilmemiş.';
    document.getElementById('task-alarm-modal').classList.remove('hidden');
    playAlarmBeep();

    if ("Notification" in window && Notification.permission === "granted") {
      new Notification("Mim Reklam Görev Hatırlatması", {
        body: `${dueTask.assigned_to}: ${dueTask.title} (${dueTask.due_date} ${dueTask.due_time})`
      });
    }
  } else if (manualTrigger) {
    alert('Şu an vakti gelen yeni bir görev uyarısı bulunmuyor.');
  }
}

async function acknowledgeTaskAlarm() {
  if (!activeAlarmTaskId) return;
  await fetch('/api/tasks', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ ack_remind_id: activeAlarmTaskId })
  });
  activeAlarmTaskId = null;
  document.getElementById('task-alarm-modal').classList.add('hidden');
  await loadBootstrap();
}

async function completeTaskFromAlarm() {
  if (!activeAlarmTaskId) return;
  const tid = activeAlarmTaskId;
  await fetch('/api/tasks', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ ack_remind_id: tid })
  });
  await fetch('/api/tasks', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ toggle_id: tid })
  });
  activeAlarmTaskId = null;
  document.getElementById('task-alarm-modal').classList.add('hidden');
  await loadBootstrap();
}

async function addTask() {
  const title = document.getElementById('task-title').value.trim();
  if (!title) return alert('Görev başlığı giriniz.');
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
  await fetch('/api/tasks', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      title,
      customer_id: document.getElementById('task-customer').value || null,
      assigned_to: document.getElementById('task-staff').value,
      due_date: document.getElementById('task-due').value || new Date().toISOString().split('T')[0],
      due_time: document.getElementById('task-time').value || '10:00',
      remind_before_mins: parseInt(document.getElementById('task-remind-mins').value) || 0,
      priority: document.getElementById('task-priority').value,
      note: document.getElementById('task-note').value
    })
  });
  document.getElementById('task-title').value = '';
  document.getElementById('task-note').value = '';
  await loadBootstrap();
}

async function toggleTask(id) {
  await fetch('/api/tasks', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ toggle_id: id })
  });
  await loadBootstrap();
}

async function deleteTask(id) {
  await fetch('/api/tasks/' + id, { method: 'DELETE' });
  await loadBootstrap();
}

function renderTasks() {
  const pendingCount = DB.tasks.filter(t => t.status !== 'Tamamlandı').length;
  const badge = document.getElementById('task-badge');
  badge.textContent = pendingCount;
  badge.classList.toggle('hidden', pendingCount === 0);

  document.getElementById('tasks-list').innerHTML = DB.tasks.map(t => {
    const done = t.status === 'Tamamlandı';
    const c = DB.customers.find(x => x.id === t.customer_id);
    const remLabel = t.remind_before_mins == 0 ? 'Tam saatinde uyarı' : `${t.remind_before_mins} dk önce uyarı`;
    return `
      <div class="p-3.5 rounded-xl border ${done ? 'bg-slate-50 border-slate-200 opacity-60' : 'bg-white border-slate-200'} flex items-center justify-between gap-3">
        <div class="flex items-start gap-3">
          <input type="checkbox" ${done ? 'checked' : ''} onchange="toggleTask(${t.id})" class="mt-1 w-4 h-4 accent-sky-600">
          <div>
            <div class="font-bold text-sm ${done ? 'line-through text-slate-400' : 'text-slate-800'}">${t.title}</div>
            <div class="text-xs text-slate-500 mt-0.5">
              ${c ? `<span class="text-sky-700 font-semibold">${c.full_name}</span> • ` : ''}
              Sorumlu: <strong>${t.assigned_to}</strong> • Zaman: <strong>${t.due_date || '-'} ${t.due_time || '09:00'}</strong>
              <span class="ml-1.5 px-2 py-0.5 bg-amber-50 text-amber-700 border border-amber-200 rounded text-[10px] font-semibold">
                <i class="fa-solid fa-bell mr-0.5"></i> ${remLabel} ${t.reminded ? '(Uyarıldı)' : ''}
              </span>
            </div>
            ${t.note ? `<p class="text-xs text-slate-400 mt-1">${t.note}</p>` : ''}
          </div>
        </div>
        <div class="flex items-center gap-2">
          <span class="px-2 py-0.5 rounded text-[11px] font-semibold ${t.priority==='Yüksek'?'bg-rose-100 text-rose-700':'bg-slate-100 text-slate-600'}">${t.priority}</span>
          <button type="button" onclick="deleteTask(${t.id})" class="text-slate-400 hover:text-rose-600 p-1"><i class="fa-solid fa-trash"></i></button>
        </div>
      </div>
    `;
  }).join('') || `<p class="text-xs text-slate-400 text-center py-6">Kayıtlı görev bulunmuyor.</p>`;
}

/* ---------------- YÖNETİCİ RAPORLARI ---------------- */
function parseTRDateToISO(trStr) {
  if (!trStr) return null;
  const part = trStr.split(' ')[0];
  const segs = part.split('.');
  if (segs.length === 3) return `${segs[2]}-${segs[1].padStart(2,'0')}-${segs[0].padStart(2,'0')}`;
  return part;
}

function resetReportFilters() {
  document.getElementById('rep-start-date').value = '';
  document.getElementById('rep-end-date').value = '';
  document.getElementById('rep-category').value = 'ALL';
  document.getElementById('rep-staff').value = 'ALL';
  renderReports();
}

function renderReports() {
  applyCompanyLogoUI();
  const startDate = document.getElementById('rep-start-date').value;
  const endDate = document.getElementById('rep-end-date').value;
  const catFilter = document.getElementById('rep-category').value;
  const staffFilter = document.getElementById('rep-staff').value;

  document.getElementById('rep-generated-date').textContent = new Date().toLocaleDateString('tr-TR');
  document.getElementById('rep-comp-label').textContent = DB.company.name || 'Mim Reklam Ajansı Ltd. Şti.';

  const fDesc = [];
  if (startDate) fDesc.push(`Başlangıç: ${startDate}`);
  if (endDate) fDesc.push(`Bitiş: ${endDate}`);
  if (catFilter !== 'ALL') fDesc.push(`Kategori: ${catFilter}`);
  if (staffFilter !== 'ALL') fDesc.push(`Personel: ${staffFilter}`);
  document.getElementById('rep-filter-summary').textContent = fDesc.length ? `Aktif Filtreler: ${fDesc.join(' | ')}` : 'Tüm zamanlar ve tüm ekip verileri gösteriliyor';

  const inDateRange = (isoDate) => {
    if (!isoDate) return true;
    if (startDate && isoDate < startDate) return false;
    if (endDate && isoDate > endDate) return false;
    return true;
  };

  const fCustomers = DB.customers.filter(c => {
    if (catFilter !== 'ALL' && c.category !== catFilter) return false;
    return inDateRange(parseTRDateToISO(c.created_at));
  });

  const fQuotes = DB.quotes.filter(q => {
    const c = DB.customers.find(x => x.id === q.customer_id);
    if (catFilter !== 'ALL' && (!c || c.category !== catFilter)) return false;
    return inDateRange(parseTRDateToISO(q.created_at));
  });

  const fInteractions = DB.interactions.filter(i => {
    if (staffFilter !== 'ALL' && i.staff_name !== staffFilter) return false;
    return inDateRange(parseTRDateToISO(i.created_at));
  });

  const fTasks = DB.tasks.filter(t => {
    if (staffFilter !== 'ALL' && t.assigned_to !== staffFilter) return false;
    return inDateRange(t.due_date);
  });

  const vipCount = fCustomers.filter(c => c.category === 'VIP').length;
  const activeCount = fCustomers.filter(c => c.category === 'Aktif Müşteri').length;
  const totalQuoteAmt = fQuotes.reduce((acc, q) => acc + (q.total_amount || 0), 0);
  const wonQuotes = fQuotes.filter(q => q.status === 'Onaylandı');
  const wonAmt = wonQuotes.reduce((acc, q) => acc + (q.total_amount || 0), 0);
  const convRate = fQuotes.length ? Math.round((wonQuotes.length / fQuotes.length) * 100) : 0;
  const tasksDone = fTasks.filter(t => t.status === 'Tamamlandı').length;

  document.getElementById('kpi-total-cust').textContent = fCustomers.length;
  document.getElementById('kpi-vip-cust').textContent = `${vipCount} VIP • ${activeCount} Aktif Müşteri`;
  document.getElementById('kpi-total-quote-amt').textContent = formatTRY(totalQuoteAmt);
  document.getElementById('kpi-quote-count').textContent = `${fQuotes.length} adet teklif oluşturuldu`;
  document.getElementById('kpi-won-amt').textContent = formatTRY(wonAmt);
  document.getElementById('kpi-conversion-rate').textContent = `Onay Oranı: %${convRate} (${wonQuotes.length} Onaylı)`;
  document.getElementById('kpi-interactions').textContent = fInteractions.length;
  document.getElementById('kpi-tasks-done').textContent = `${tasksDone} / ${fTasks.length} Görev Tamamlandı`;

  document.getElementById('rep-quotes-tbody').innerHTML = fQuotes.map(q => {
    const c = DB.customers.find(x => x.id === q.customer_id);
    const cname = c ? `${c.full_name} (${c.company || '-'})` : (q.manual_customer_name || 'Manuel');
    return `
      <tr>
        <td class="p-2.5 font-bold text-sky-700">${q.quote_no} (v${q.version})</td>
        <td class="p-2.5">${cname}</td>
        <td class="p-2.5 text-slate-500">${q.created_at}</td>
        <td class="p-2.5 font-semibold">${q.status}</td>
        <td class="p-2.5 text-right font-bold">${formatTRY(q.total_amount)}</td>
      </tr>
    `;
  }).join('') || `<tr><td colspan="5" class="p-4 text-center text-slate-400">Seçili filtrelere uygun teklif bulunamadı.</td></tr>`;

  if (typeof Chart === 'undefined') return;

  const cats = ['Potansiyel', 'Teklif Verildi', 'Aktif Müşteri', 'VIP'];
  const catCounts = cats.map(ct => fCustomers.filter(c => c.category === ct).length);
  if (chartCatInstance) chartCatInstance.destroy();
  chartCatInstance = new Chart(document.getElementById('chart-cust-category'), {
    type: 'doughnut',
    data: {
      labels: cats,
      datasets: [{ data: catCounts, backgroundColor: ['#94a3b8', '#f59e0b', '#10b981', '#8b5cf6'] }]
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } } } }
  });

  const qStatuses = ['Gönderildi', 'Onaylandı', 'Reddedildi'];
  const qAmounts = qStatuses.map(st => fQuotes.filter(q => q.status === st).reduce((s, q) => s + (q.total_amount || 0), 0));
  if (chartQuoteInstance) chartQuoteInstance.destroy();
  chartQuoteInstance = new Chart(document.getElementById('chart-quote-status'), {
    type: 'bar',
    data: {
      labels: qStatuses,
      datasets: [{ label: 'Tutar (₺)', data: qAmounts, backgroundColor: ['#0284c7', '#10b981', '#f43f5e'], borderRadius: 6 }]
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
  });

  const staffNames = DB.staff.map(s => s.name);
  const staffInts = staffNames.map(sn => fInteractions.filter(i => i.staff_name === sn).length);
  const staffTasks = staffNames.map(sn => fTasks.filter(t => t.assigned_to === sn && t.status === 'Tamamlandı').length);
  if (chartStaffInstance) chartStaffInstance.destroy();
  chartStaffInstance = new Chart(document.getElementById('chart-staff-perf'), {
    type: 'bar',
    data: {
      labels: staffNames,
      datasets: [
        { label: 'Görüşme', data: staffInts, backgroundColor: '#38bdf8', borderRadius: 4 },
        { label: 'Tamamlanan Görev', data: staffTasks, backgroundColor: '#8b5cf6', borderRadius: 4 }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } } }, scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } }
  });
}

/* ---------------- E-POSTA MERKEZİ ---------------- */
function updateMailSignaturePreview() {
  const sid = parseInt(document.getElementById('mail-sender').value);
  const st = DB.staff.find(s => s.id === sid);
  document.getElementById('mail-sig-preview').textContent = (st && st.signature) ? st.signature : 'Bu personel için henüz imza tanımlanmamış.';
}

function toggleMailTargetMode() {
  const mode = document.getElementById('mail-target-mode').value;
  document.getElementById('mail-target-single').classList.toggle('hidden', mode !== 'single');
  document.getElementById('mail-target-category').classList.toggle('hidden', mode !== 'category');
  document.getElementById('mail-target-selected').classList.toggle('hidden', mode !== 'selected');
}

function fillSingleMailRecipient() {
  const cid = parseInt(document.getElementById('mail-cust-picker').value);
  const c = DB.customers.find(x => x.id === cid);
  if (c && c.emails) document.getElementById('mail-to-email').value = c.emails.split(',')[0].trim();
}

function applyMailTemplate() {
  const tid = parseInt(document.getElementById('mail-template-select').value);
  const tpl = DB.templates.find(t => t.id === tid);
  if (!tpl) return;
  document.getElementById('mail-subject').value = tpl.subject;
  document.getElementById('mail-body').value = tpl.body;
}

function personalizeText(text, cust) {
  return text
    .replaceAll('{musteri_adi}', cust?.full_name || 'Değerli İş Ortağımız')
    .replaceAll('{firma_adi}', cust?.company || 'Kurumunuz');
}

async function sendCampaignMails() {
  const staff_id = document.getElementById('mail-sender').value;
  const rawSub = document.getElementById('mail-subject').value.trim();
  const rawBody = document.getElementById('mail-body').value.trim();
  if (!rawSub || !rawBody) return alert('Konu ve içerik boş bırakılamaz.');

  const mode = document.getElementById('mail-target-mode').value;
  let targets = [];

  if (mode === 'single') {
    const email = document.getElementById('mail-to-email').value.trim();
    if (!email) return alert('Alıcı e-posta adresi giriniz.');
    const cid = parseInt(document.getElementById('mail-cust-picker').value);
    const c = DB.customers.find(x => x.id === cid);
    targets.push({ email, cust: c });
  } else if (mode === 'category') {
    const cat = document.getElementById('mail-cat-picker').value;
    DB.customers.filter(c => (cat === 'ALL' || c.category === cat) && c.emails).forEach(c => {
      c.emails.split(',').map(e => e.trim()).filter(Boolean).forEach(email => targets.push({ email, cust: c }));
    });
  } else if (mode === 'selected') {
    DB.customers.filter(c => selectedCustomerIds.has(c.id) && c.emails).forEach(c => {
      c.emails.split(',').map(e => e.trim()).filter(Boolean).forEach(email => targets.push({ email, cust: c }));
    });
  }

  if (!targets.length) return alert('Gönderilecek geçerli e-posta adresi bulunamadı.');
  const prog = document.getElementById('mail-progress');
  for (let i = 0; i < targets.length; i++) {
    prog.textContent = `Gönderiliyor: ${i + 1} / ${targets.length}...`;
    const t = targets[i];
    await fetch('/api/send_single_mail', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        staff_id,
        to_email: t.email,
        to_name: t.cust?.full_name || '',
        subject: personalizeText(rawSub, t.cust),
        body: personalizeText(rawBody, t.cust)
      })
    });
  }
  prog.textContent = `Tamamlandı (${targets.length} e-posta işlendi).`;
  await loadBootstrap();
}

function renderMailLogs() {
  document.getElementById('mail-logs-list').innerHTML = DB.mail_logs.map(m => `
    <div class="p-3 rounded-lg border border-slate-200 bg-slate-50">
      <div class="flex justify-between font-semibold">
        <span class="text-slate-800">${m.recipient_email}</span>
        <span class="${m.status.includes('Hata') ? 'text-rose-600' : 'text-emerald-600'}">${m.status}</span>
      </div>
      <div class="text-slate-600 mt-0.5">${m.subject}</div>
      ${m.error_msg ? `<div class="text-rose-500 text-[11px] mt-1">${m.error_msg}</div>` : ''}
      <div class="text-[10px] text-slate-400 mt-1">${m.sent_at} • Gönderen: ${m.sender_email}</div>
    </div>
  `).join('') || `<p class="text-slate-400 text-center py-6">Henüz mail gönderimi yok.</p>`;
}

/* ---------------- AYARLAR, FİRMA LOGOSU & DROPDOWNLAR ---------------- */
function populateDropdowns() {
  const custOpts = `<option value="">-- Müşteri Seçiniz --</option>` + DB.customers.map(c =>
    `<option value="${c.id}">${c.full_name} ${c.company ? '(' + c.company + ')' : ''}</option>`
  ).join('');
  document.getElementById('q-customer').innerHTML = custOpts;
  document.getElementById('mail-cust-picker').innerHTML = custOpts;
  document.getElementById('task-customer').innerHTML = custOpts;

  const staffNameOpts = DB.staff.map(s => `<option value="${s.name}">${s.name} (${s.title})</option>`).join('');
  document.getElementById('int-staff').innerHTML = staffNameOpts;
  document.getElementById('task-staff').innerHTML = staffNameOpts;

  const prevRepStaff = document.getElementById('rep-staff').value;
  document.getElementById('rep-staff').innerHTML = `<option value="ALL">Tüm Satış Ekibi</option>` +
    DB.staff.map(s => `<option value="${s.name}">${s.name}</option>`).join('');
  if (prevRepStaff) document.getElementById('rep-staff').value = prevRepStaff;

  const prevActiveUser = document.getElementById('active-user-select').value;
  document.getElementById('active-user-select').innerHTML = `<option value="ALL">Tüm Personeller (Yönetici)</option>` +
    DB.staff.map(s => `<option value="${s.name}">${s.name}</option>`).join('');
  if (prevActiveUser) document.getElementById('active-user-select').value = prevActiveUser;

  document.getElementById('mail-sender').innerHTML = DB.staff.map(s =>
    `<option value="${s.id}">${s.name} - ${s.email} ${s.smtp_pass ? '(SMTP Aktif)' : '(Simülasyon)'}</option>`
  ).join('');

  document.getElementById('mail-template-select').innerHTML = `<option value="">-- Şablon Seçiniz --</option>` +
    DB.templates.map(t => `<option value="${t.id}">${t.name}</option>`).join('');
}

function updateSettingsLogoPreview(logoDataUrl) {
  const img = document.getElementById('set-logo-preview');
  const empty = document.getElementById('set-logo-empty');
  const remBtn = document.getElementById('set-logo-remove-btn');
  if (logoDataUrl) {
    img.src = logoDataUrl;
    img.classList.remove('hidden');
    empty.classList.add('hidden');
    remBtn.classList.remove('hidden');
  } else {
    img.src = '';
    img.classList.add('hidden');
    empty.classList.remove('hidden');
    remBtn.classList.add('hidden');
  }
}

function handleLogoUpload(e) {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    alert('Logo dosyası en fazla 2 MB olabilir.');
    e.target.value = '';
    return;
  }
  const reader = new FileReader();
  reader.onload = function(ev) {
    tempCompanyLogoBase64 = ev.target.result;
    updateSettingsLogoPreview(tempCompanyLogoBase64);
  };
  reader.readAsDataURL(file);
}

function removeCompanyLogo() {
  tempCompanyLogoBase64 = "";
  document.getElementById('set-comp-logo-input').value = '';
  updateSettingsLogoPreview("");
}

function renderSettings() {
  document.getElementById('set-comp-name').value = DB.company.name || '';
  document.getElementById('set-comp-addr').value = DB.company.address || '';
  document.getElementById('set-comp-phone').value = DB.company.phone || '';
  document.getElementById('set-comp-email').value = DB.company.email || '';
  document.getElementById('set-comp-tax').value = DB.company.tax || '';
  tempCompanyLogoBase64 = DB.company.logo || '';
  updateSettingsLogoPreview(tempCompanyLogoBase64);

  document.getElementById('staff-list').innerHTML = DB.staff.map(s => `
    <div class="py-2.5 flex justify-between items-start gap-2">
      <div>
        <div><strong>${s.name}</strong> (${s.title}) - <span class="text-slate-500">${s.email}</span></div>
        <div class="text-[11px] text-slate-400 mt-0.5 line-clamp-1">${s.signature ? 'İmza: ' + s.signature.replaceAll('\n', ' | ') : 'İmza eklenmemiş'}</div>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <button type="button" onclick="editStaffMember(${s.id})" class="px-2 py-1 bg-amber-50 text-amber-700 hover:bg-amber-100 rounded text-[11px] font-semibold">
          <i class="fa-solid fa-pen mr-1"></i> Düzenle
        </button>
        ${DB.staff.length > 1 ? `<button type="button" onclick="deleteSettingItem('delete_staff', ${s.id})" class="text-rose-500 hover:text-rose-700 p-1"><i class="fa-solid fa-trash"></i></button>` : ''}
      </div>
    </div>
  `).join('');

  document.getElementById('templates-list').innerHTML = DB.templates.map(t => `
    <div class="py-2 flex justify-between items-center">
      <div><strong>${t.name}</strong>: <span class="text-slate-500">${t.subject}</span></div>
      <button type="button" onclick="deleteSettingItem('delete_template', ${t.id})" class="text-rose-500 hover:text-rose-700"><i class="fa-solid fa-trash"></i></button>
    </div>
  `).join('');

  document.getElementById('audit-logs-list').innerHTML = DB.audit_logs.map(a => `
    <div class="p-2 bg-slate-50 rounded border border-slate-100 flex justify-between">
      <span><strong class="text-slate-700">${a.action}:</strong> ${a.detail}</span>
      <span class="text-slate-400 shrink-0 ml-2">${a.created_at}</span>
    </div>
  `).join('');
}

function editStaffMember(id) {
  const s = DB.staff.find(x => x.id === id);
  if (!s) return;
  document.getElementById('set-st-id').value = s.id;
  document.getElementById('set-st-name').value = s.name;
  document.getElementById('set-st-title').value = s.title || '';
  document.getElementById('set-st-email').value = s.email;
  document.getElementById('set-st-pass').value = '';
  document.getElementById('set-st-pass').placeholder = 'Değiştirmek istemiyorsanız boş bırakın';
  document.getElementById('set-st-sig').value = s.signature || '';

  document.getElementById('staff-form-title').textContent = `Personel & İmza Düzenle: ${s.name}`;
  document.getElementById('staff-save-btn').textContent = 'Değişiklikleri Kaydet';
  document.getElementById('staff-save-btn').className = 'bg-sky-600 hover:bg-sky-700 text-white px-4 py-2 rounded-lg text-xs font-bold';
  document.getElementById('staff-cancel-btn').classList.remove('hidden');
}

function resetStaffForm() {
  document.getElementById('set-st-id').value = '';
  ['name', 'title', 'email', 'pass', 'sig'].forEach(f => document.getElementById('set-st-' + f).value = '');
  document.getElementById('set-st-pass').placeholder = 'Gmail Uygulama Şifresi';
  document.getElementById('staff-form-title').textContent = 'Satış Ekibi, SMTP & Mail İmzası';
  document.getElementById('staff-save-btn').textContent = '+ Personel & İmza Ekle';
  document.getElementById('staff-save-btn').className = 'bg-emerald-600 hover:bg-emerald-700 text-white px-4 py-2 rounded-lg text-xs font-bold';
  document.getElementById('staff-cancel-btn').classList.add('hidden');
}

async function saveCompanySettings() {
  await fetch('/api/settings', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      type: 'company',
      data: {
        name: document.getElementById('set-comp-name').value,
        address: document.getElementById('set-comp-addr').value,
        phone: document.getElementById('set-comp-phone').value,
        email: document.getElementById('set-comp-email').value,
        tax: document.getElementById('set-comp-tax').value,
        logo: tempCompanyLogoBase64
      }
    })
  });
  alert('Firma bilgileri ve logo kaydedildi.');
  await loadBootstrap();
}

async function saveStaffMember() {
  const id = document.getElementById('set-st-id').value || null;
  const name = document.getElementById('set-st-name').value.trim();
  const email = document.getElementById('set-st-email').value.trim();
  if (!name || !email) return alert('Ad Soyad ve E-Posta zorunludur.');
  await fetch('/api/settings', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      type: 'staff',
      data: {
        id,
        name,
        title: document.getElementById('set-st-title').value,
        email,
        smtp_pass: document.getElementById('set-st-pass').value,
        role: 'Satış',
        signature: document.getElementById('set-st-sig').value
      }
    })
  });
  resetStaffForm();
  await loadBootstrap();
}

async function addMailTemplate() {
  const name = document.getElementById('set-tpl-name').value.trim();
  const subject = document.getElementById('set-tpl-sub').value.trim();
  const body = document.getElementById('set-tpl-body').value.trim();
  if (!name || !subject || !body) return alert('Tüm şablon alanlarını doldurunuz.');
  await fetch('/api/settings', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ type: 'template', data: { name, subject, body } })
  });
  ['name', 'sub', 'body'].forEach(f => document.getElementById('set-tpl-' + f).value = '');
  await loadBootstrap();
}

async function deleteSettingItem(type, id) {
  await fetch('/api/settings', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ type, id })
  });
  await loadBootstrap();
}

/* ---------------- BACKEND EXCEL İNDİRME & YÜKLEME ---------------- */
function downloadSampleExcel() {
  window.location.href = '/api/customers/sample_excel?t=' + Date.now();
}

async function handleExcelUpload(e) {
  const file = e.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch('/api/customers/upload_excel', {
      method: 'POST',
      body: formData
    });
    const out = await res.json();
    if (!res.ok || out.status === 'error') {
      alert('Yükleme Hatası: ' + (out.message || 'Bilinmeyen hata'));
    } else {
      alert(`${out.added} müşteri başarıyla içeri aktarıldı.`);
      await loadBootstrap();
    }
  } catch (err) {
    alert('Excel yüklenirken bağlantı hatası: ' + err.message);
  } finally {
    e.target.value = '';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadBootstrap();
  setInterval(checkTaskReminders, 15000);
});
</script>
</body>
</html>
"""

if __name__ == '__main__':
    from migrate_sqlite import main
    main()
    from waitress import serve
    print("Mim Reklam CRM Sunucusu 0.0.0.0:8080 adresinde başlatılıyor...")
    serve(app, host='127.0.0.1', port=8000, threads=8)
