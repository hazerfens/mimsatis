# 1. Gerekli Kütüphanelerin Yüklenmesi

import os
import json
import time
import smtplib
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from threading import Thread
from flask import Flask, render_template_string, request, redirect, url_for, send_file, flash, session, send_from_directory, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import pandas as pd

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ['SECRET_KEY']
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.getenv('DATABASE_PATH', '/app/data/mim_reklam_satis_sistemi.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', '/app/uploaded_files')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)

# -----------------------------------------------------------------------------
# VERİTABANI MODELLERİ
# -----------------------------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='Personel')

class CustomerCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

class EmailTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)

class SystemSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.Text)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(200), nullable=False)
    contact_person = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    category = db.Column(db.String(50), default='Genel')
    assigned_sales_rep = db.Column(db.String(100), default='Çiğdem Biçer')
    tax_office = db.Column(db.String(100))
    tax_number = db.Column(db.String(50))
    address = db.Column(db.Text)
    payment_terms = db.Column(db.String(100))
    credit_limit = db.Column(db.Float, default=0.0)
    current_balance = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Proposal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    code = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    created_by = db.Column(db.String(100))
    items_json = db.Column(db.Text)
    subtotal = db.Column(db.Float, default=0.0)
    tax_total = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text)
    parent_id = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(50), default='Hazırlandı')
    created_date = db.Column(db.DateTime, default=datetime.utcnow)

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    assigned_by = db.Column(db.String(100), nullable=False)
    assigned_to = db.Column(db.String(100), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    due_date = db.Column(db.DateTime, nullable=False)
    priority = db.Column(db.String(20), default='Normal')
    status = db.Column(db.String(50), default='Bekliyor')
    reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Interaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    sales_rep = db.Column(db.String(100), nullable=False)
    interaction_date = db.Column(db.DateTime, default=datetime.utcnow)
    location = db.Column(db.String(100))
    customer_feedback = db.Column(db.Text)
    notes = db.Column(db.Text)
    result = db.Column(db.String(100))

class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    filename = db.Column(db.String(200))
    file_type = db.Column(db.String(50))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

class SalesAndPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    job_title = db.Column(db.String(200), nullable=False, default="Genel Sipariş")
    sale_amount = db.Column(db.Float, default=0.0)
    paid_amount = db.Column(db.Float, default=0.0)
    payment_status = db.Column(db.String(50), default='Bekliyor')
    sale_date = db.Column(db.DateTime, default=datetime.utcnow)
    delivery_date = db.Column(db.DateTime, nullable=True)

class ProductionIssue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales_and_payment.id'), nullable=True)
    issue_description = db.Column(db.Text)
    sales_notes = db.Column(db.Text)
    status = db.Column(db.String(50), default='Devam Ediyor')

# -----------------------------------------------------------------------------
# PWA & E-POSTA MOTORU
# -----------------------------------------------------------------------------

@app.route('/manifest.json')
def pwa_manifest():
    manifest_data = {
        "name": "MİM REKLAM Satış Sistemi",
        "short_name": "Mim Satış",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#36082a",
        "theme_color": "#701658",
        "icons": [{"src": "/uploads/logo.png", "sizes": "192x192", "type": "image/png"}]
    }
    return Response(json.dumps(manifest_data), mimetype='application/json')

@app.route('/sw.js')
def service_worker():
    return Response("self.addEventListener('fetch', (e) => {});", mimetype='application/javascript')

def send_gmail_with_attachment(to_email, subject, body_html, attachment_filepath=None, cc_emails=None):
    gmail_user = SystemSetting.query.filter_by(key='GMAIL_USER').first()
    gmail_pass = SystemSetting.query.filter_by(key='GMAIL_APP_PASS').first()
    if not gmail_user or not gmail_pass or not gmail_user.value or not gmail_pass.value:
        return False, "Sistem Ayarlarından Gmail ve 16 haneli Uygulama Şifresi girilmemiş!"
    try:
        msg = MIMEMultipart()
        msg['From'] = gmail_user.value.strip()
        msg['To'] = to_email.strip()
        msg['Subject'] = subject
        recipients = [to_email.strip()]
        if cc_emails:
            clean_ccs = [c.strip() for c in cc_emails.replace(';', ',').split(',') if c.strip() and '@' in c]
            if clean_ccs:
                msg['Cc'] = ", ".join(clean_ccs)
                recipients.extend(clean_ccs)
        msg.attach(MIMEText(body_html, 'html'))
        if attachment_filepath and os.path.exists(attachment_filepath):
            filename = os.path.basename(attachment_filepath)
            with open(attachment_filepath, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename= {filename}")
            msg.attach(part)
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10)
        server.login(gmail_user.value.strip(), gmail_pass.value.strip().replace(" ", ""))
        server.sendmail(gmail_user.value.strip(), recipients, msg.as_string())
        server.quit()
        return True, "Başarılı"
    except Exception as e:
        return False, str(e)

# -----------------------------------------------------------------------------
# ARAYÜZ (HTML)
# -----------------------------------------------------------------------------

BASE_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MİM REKLAM SATIŞ SİSTEMİ</title>
    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#701658">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root { --mim-purple: #701658; --mim-purple-hover: #540f42; }
        body { background:#f9f5f8; font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .sidebar { min-height:100vh; background:#36082a; color:white; }
        .sidebar .nav-link { color:#e8d1e2; margin:4px 0; border-radius:6px; font-weight:500; }
        .sidebar .nav-link:hover, .sidebar .nav-link.active { background:var(--mim-purple); color:white; }
        .btn-mim { background-color: var(--mim-purple); color: white !important; border: none; }
        .btn-mim:hover { background-color: var(--mim-purple-hover); }
        .btn-outline-mim { color: var(--mim-purple); border-color: var(--mim-purple); }
        .btn-outline-mim:hover { background-color: var(--mim-purple); color: white; }
        .card-custom { border:none; border-radius:10px; box-shadow:0 4px 8px rgba(112, 22, 88, 0.08); }
        .text-mim { color: var(--mim-purple) !important; }
        .badge-mim { background-color: var(--mim-purple); color: white; }
        .nav-tabs .nav-link.active { border-bottom: 3px solid var(--mim-purple); color: var(--mim-purple) !important; font-weight: bold; }
        @media (max-width: 768px) { .sidebar { min-height: auto; } }
    </style>
</head>
<body>
<div class="container-fluid"><div class="row">
    {% if session.get('user_id') %}
    <div class="col-md-2 sidebar p-3">
        <div class="text-center mb-4 pb-2 border-bottom border-secondary">
            <h5 class="fw-bold text-white mb-1"><i class="fa-solid fa-shapes text-warning me-2"></i>MİM REKLAM</h5>
            <small class="text-light" style="font-size:0.75rem; letter-spacing:1px;">SATIŞ SİSTEMİ</small>
        </div>
        <div class="mb-3 p-2 rounded text-white small" style="background:#540f42;">
            <i class="fa-solid fa-user-circle me-1"></i> <strong>{{ session.get('full_name') }}</strong><br>
            <span class="badge bg-warning text-dark mt-1">{{ session.get('role') }}</span>
        </div>
        <ul class="nav flex-column">
            <li class="nav-item"><a href="/" class="nav-link text-white"><i class="fa-solid fa-users me-2"></i>Müşteriler</a></li>
            <li class="nav-item"><a href="/tasks" class="nav-link text-white"><i class="fa-solid fa-list-check me-2"></i>Görevler Paneli</a></li>
            <li class="nav-item"><a href="/dashboard" class="nav-link text-white"><i class="fa-solid fa-chart-pie me-2"></i>Yönetici Raporu</a></li>
            <li class="nav-item"><a href="/bulk-email" class="nav-link text-white"><i class="fa-solid fa-paper-plane me-2"></i>Gmail Toplu Mail</a></li>
            {% if session.get('role') == 'Admin' %}
            <li class="nav-item"><a href="/settings" class="nav-link text-white"><i class="fa-solid fa-gears me-2"></i>Ayarlar</a></li>
            {% endif %}
            <li class="nav-item mt-4"><a href="/logout" class="nav-link text-danger"><i class="fa-solid fa-right-from-bracket me-2"></i>Çıkış Yap</a></li>
        </ul>
    </div>
    <div class="col-md-10 p-3 p-md-4">
    {% else %}<div class="col-md-12 p-3 p-md-4">{% endif %}
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}{% for category, message in messages %}
            <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">{{ message }}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>
          {% endfor %}{% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </div>
</div></div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

LOGIN_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="row justify-content-center mt-5"><div class="col-md-4"><div class="card card-custom p-4 text-center">
    <div class="mb-3">
        <h3 class="fw-bold text-mim mb-0"><i class="fa-solid fa-shapes me-2"></i>MİM REKLAM</h3>
        <p class="text-muted small fw-bold">SATIŞ SİSTEMİ</p>
    </div>
    <form method="POST" action="/login" class="text-start">
        <div class="mb-3"><label class="form-label fw-bold">Kullanıcı Adı</label><input type="text" name="username" class="form-control" required placeholder="admin"></div>
        <div class="mb-3"><label class="form-label fw-bold">Şifre</label><input type="password" name="password" class="form-control" required placeholder="123456"></div>
        <button type="submit" class="btn btn-mim w-100 py-2 fw-bold">Sisteme Giriş Yap</button>
    </form>
</div></div></div>
""")

INDEX_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-2">
    <h2><i class="fa-solid fa-address-book text-mim me-2"></i>Müşteri Portföyü</h2>
    <div>
        <a href="/download-template" class="btn btn-outline-secondary btn-sm me-1"><i class="fa-solid fa-download me-1"></i>Excel Şablonu</a>
        <button class="btn btn-outline-success btn-sm me-1" data-bs-toggle="modal" data-bs-target="#importExcelModal"><i class="fa-solid fa-file-excel me-1"></i>Excel Yükle</button>
        <button class="btn btn-mim btn-sm" data-bs-toggle="modal" data-bs-target="#newCustomerModal"><i class="fa-solid fa-plus me-1"></i>Müşteri Ekle</button>
    </div>
</div>

<div class="card card-custom p-3 mb-4 bg-white">
    <form method="GET" action="/" class="row g-2 align-items-end">
        <div class="col-md-5"><input type="text" name="search" class="form-control" placeholder="Müşteri Adı / Yetkili / Vergi No..." value="{{ search_query }}"></div>
        <div class="col-md-3">
            <select name="category" class="form-select">
                <option value="ALL">Tüm Kategoriler</option>
                {% for cat in categories %}<option value="{{ cat.name }}" {% if filter_cat == cat.name %}selected{% endif %}>{{ cat.name }}</option>{% endfor %}
            </select>
        </div>
        <div class="col-md-2"><button type="submit" class="btn btn-mim w-100"><i class="fa-solid fa-magnifying-glass me-1"></i>Ara</button></div>
        <div class="col-md-2"><a href="/" class="btn btn-outline-secondary w-100">Sıfırla</a></div>
    </form>
</div>

<div class="card card-custom p-3 table-responsive">
    <table class="table table-hover align-middle">
        <thead class="table-light"><tr><th>Firma Unvanı</th><th>Yetkili</th><th>İletişim</th><th>Temsilci</th><th>Cari Bakiye</th><th>Kategori</th><th class="text-end">İşlemler</th></tr></thead>
        <tbody>
            {% for c in customers %}
            <tr>
                <td class="fw-bold text-mim">{{ c.company_name }}</td>
                <td>{{ c.contact_person }}</td>
                <td><small>{{ c.email }}<br>{{ c.phone }}</small></td>
                <td><span class="badge bg-secondary">{{ c.assigned_sales_rep }}</span></td>
                <td class="fw-bold">{{ "{:,.2f}".format(c.current_balance) }} ₺</td>
                <td><span class="badge badge-mim">{{ c.category }}</span></td>
                <td class="text-end">
                    <a href="/customer/{{ c.id }}" class="btn btn-sm btn-outline-mim"><i class="fa-solid fa-eye me-1"></i>Kartı Aç</a>
                    <button class="btn btn-sm btn-outline-warning" data-bs-toggle="modal" data-bs-target="#editCustModal{{ c.id }}"><i class="fa-solid fa-pen"></i></button>
                    <a href="/customer/delete/{{ c.id }}" class="btn btn-sm btn-outline-danger" onclick="return confirm('Silmek istiyor musunuz?');"><i class="fa-solid fa-trash"></i></a>
                </td>
            </tr>

            <div class="modal fade" id="editCustModal{{ c.id }}" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content">
                <form method="POST" action="/customer/edit/{{ c.id }}">
                    <div class="modal-header"><h5 class="modal-title text-mim fw-bold">Müşteri Kartını Düzenle</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
                    <div class="modal-body row g-3">
                      <div class="col-md-6"><label class="form-label fw-bold">Firma Unvanı *</label><input type="text" name="company_name" class="form-control" value="{{ c.company_name }}" required></div>
                      <div class="col-md-6"><label class="form-label fw-bold">Yetkili Ad Soyad *</label><input type="text" name="contact_person" class="form-control" value="{{ c.contact_person }}" required></div>
                      <div class="col-md-6"><label class="form-label fw-bold">E-Posta *</label><input type="email" name="email" class="form-control" value="{{ c.email }}" required></div>
                      <div class="col-md-6"><label class="form-label fw-bold">Telefon *</label><input type="text" name="phone" class="form-control" value="{{ c.phone }}" required></div>
                      <div class="col-md-4"><label class="form-label">Vergi Dairesi</label><input type="text" name="tax_office" class="form-control" value="{{ c.tax_office or '' }}"></div>
                      <div class="col-md-4"><label class="form-label">Vergi No</label><input type="text" name="tax_number" class="form-control" value="{{ c.tax_number or '' }}"></div>
                      <div class="col-md-4"><label class="form-label">Ödeme Vadesi</label><input type="text" name="payment_terms" class="form-control" value="{{ c.payment_terms or '' }}"></div>
                      <div class="col-md-4"><label class="form-label">Kredi Limiti</label><input type="number" step="0.01" name="credit_limit" class="form-control" value="{{ c.credit_limit }}"></div>
                      <div class="col-md-4"><label class="form-label">Kategori</label>
                        <select name="category" class="form-select">{% for cat in categories %}<option value="{{ cat.name }}" {% if c.category == cat.name %}selected{% endif %}>{{ cat.name }}</option>{% endfor %}</select>
                      </div>
                      <div class="col-md-4"><label class="form-label">Satıcı</label>
                        <select name="assigned_sales_rep" class="form-select">{% for u in users %}<option value="{{ u.full_name }}" {% if c.assigned_sales_rep == u.full_name %}selected{% endif %}>{{ u.full_name }}</option>{% endfor %}</select>
                      </div>
                      <div class="col-md-12"><label class="form-label">Adres</label><textarea name="address" class="form-control" rows="2">{{ c.address or '' }}</textarea></div>
                    </div>
                    <div class="modal-footer"><button type="submit" class="btn btn-mim">Güncelle</button></div>
                </form>
            </div></div></div>
            {% else %}<tr><td colspan="7" class="text-center py-4 text-muted">Kayıt bulunamadı.</td></tr>{% endfor %}
        </tbody>
    </table>
</div>

<div class="modal fade" id="newCustomerModal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content">
    <form method="POST" action="/customer/add">
        <div class="modal-header"><h5 class="modal-title text-mim fw-bold">Yeni Müşteri Ekle</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body row g-3">
          <div class="col-md-6"><label class="form-label fw-bold">Firma Unvanı *</label><input type="text" name="company_name" class="form-control" required></div>
          <div class="col-md-6"><label class="form-label fw-bold">Yetkili *</label><input type="text" name="contact_person" class="form-control" required></div>
          <div class="col-md-6"><label class="form-label fw-bold">E-Posta *</label><input type="email" name="email" class="form-control" required></div>
          <div class="col-md-6"><label class="form-label fw-bold">Telefon *</label><input type="text" name="phone" class="form-control" required></div>
          <div class="col-md-4"><label class="form-label">Vergi Dairesi</label><input type="text" name="tax_office" class="form-control"></div>
          <div class="col-md-4"><label class="form-label">Vergi No</label><input type="text" name="tax_number" class="form-control"></div>
          <div class="col-md-4"><label class="form-label">Ödeme Vadesi</label><input type="text" name="payment_terms" class="form-control"></div>
          <div class="col-md-4"><label class="form-label">Kredi Limiti</label><input type="number" step="0.01" name="credit_limit" class="form-control" value="0.00"></div>
          <div class="col-md-4"><label class="form-label">Kategori</label>
            <select name="category" class="form-select">{% for cat in categories %}<option value="{{ cat.name }}">{{ cat.name }}</option>{% endfor %}</select>
          </div>
          <div class="col-md-4"><label class="form-label">Satıcı</label>
            <select name="assigned_sales_rep" class="form-select">{% for u in users %}<option value="{{ u.full_name }}">{{ u.full_name }}</option>{% endfor %}</select>
          </div>
          <div class="col-md-12"><label class="form-label">Adres</label><textarea name="address" class="form-control" rows="2"></textarea></div>
        </div>
        <div class="modal-footer"><button type="submit" class="btn btn-mim">Kaydet</button></div>
    </form>
</div></div></div>

<div class="modal fade" id="importExcelModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content">
    <form method="POST" action="/import-excel" enctype="multipart/form-data">
        <div class="modal-header"><h5 class="modal-title text-mim fw-bold">Excel ile Toplu Müşteri Yükle</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body"><input type="file" name="file" class="form-control" accept=".xlsx, .xls" required></div>
        <div class="modal-footer"><button type="submit" class="btn btn-success">İçe Aktar</button></div>
    </form>
</div></div></div>
""")

CUSTOMER_DETAIL_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="d-flex justify-content-between align-items-center mb-3">
    <h2><i class="fa-solid fa-building text-mim me-2"></i>{{ customer.company_name }}</h2>
    <a href="/" class="btn btn-outline-secondary"><i class="fa-solid fa-arrow-left me-1"></i>Listeye Dön</a>
</div>

<ul class="nav nav-tabs flex-wrap" id="customerTab" role="tablist">
  <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#tab-info"><i class="fa-solid fa-id-card me-1 text-mim"></i>Müşteri Künyesi</button></li>
  <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-proposals"><i class="fa-solid fa-file-invoice-dollar me-1 text-mim"></i>Teklifler</button></li>
  <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-interactions"><i class="fa-solid fa-comments me-1 text-mim"></i>Görüşmeler</button></li>
  <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-docs"><i class="fa-solid fa-folder-open me-1 text-mim"></i>Belgeler</button></li>
  <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-sales"><i class="fa-solid fa-cart-shopping me-1 text-mim"></i>Siparişler</button></li>
  <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-production"><i class="fa-solid fa-gears me-1 text-danger"></i>Üretim Notları</button></li>
</ul>

<div class="tab-content bg-white p-4 rounded-bottom shadow-sm">
    <div class="tab-pane fade show active" id="tab-info">
        <div class="row g-4">
            <div class="col-md-6">
                <div class="p-3 bg-light rounded border h-100">
                    <h6 class="fw-bold text-mim mb-3"><i class="fa-solid fa-address-book me-2"></i>İletişim</h6>
                    <table class="table table-sm table-borderless">
                        <tr><th width="35%">Firma:</th><td class="fw-bold">{{ customer.company_name }}</td></tr>
                        <tr><th>Yetkili:</th><td>{{ customer.contact_person }}</td></tr>
                        <tr><th>E-Posta:</th><td><a href="mailto:{{ customer.email }}" class="text-mim">{{ customer.email }}</a></td></tr>
                        <tr><th>Telefon:</th><td>{{ customer.phone }}</td></tr>
                        <tr><th>Kategori:</th><td><span class="badge badge-mim">{{ customer.category }}</span></td></tr>
                        <tr><th>Satıcı:</th><td><span class="badge bg-secondary">{{ customer.assigned_sales_rep }}</span></td></tr>
                    </table>
                </div>
            </div>
            <div class="col-md-6">
                <div class="p-3 bg-light rounded border h-100">
                    <h6 class="fw-bold text-mim mb-3"><i class="fa-solid fa-receipt me-2"></i>Fatura & Muhasebe</h6>
                    <table class="table table-sm table-borderless">
                        <tr><th width="35%">Vergi Dairesi:</th><td>{{ customer.tax_office or '-' }}</td></tr>
                        <tr><th>Vergi No:</th><td>{{ customer.tax_number or '-' }}</td></tr>
                        <tr><th>Vade:</th><td>{{ customer.payment_terms or 'Peşin' }}</td></tr>
                        <tr><th>Kredi Limiti:</th><td>{{ "{:,.2f}".format(customer.credit_limit) }} ₺</td></tr>
                        <tr><th>Cari Bakiye:</th><td class="fw-bold text-danger">{{ "{:,.2f}".format(customer.current_balance) }} ₺</td></tr>
                        <tr><th>Adres:</th><td>{{ customer.address or '-' }}</td></tr>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div class="tab-pane fade" id="tab-proposals">
        <div class="d-flex justify-content-between mb-3">
            <h5>Teklifler</h5>
            <button class="btn btn-sm btn-mim" data-bs-toggle="modal" data-bs-target="#addProposalModal">+ Teklif Hazırla</button>
        </div>
        <table class="table align-middle">
            <thead class="table-light"><tr><th>Kod</th><th>Başlık</th><th>Hazırlayan</th><th>Tutar</th><th>Tarih</th><th>Durum</th><th class="text-end">İşlemler</th></tr></thead>
            <tbody>
                {% for p in proposals %}
                <tr>
                    <td><span class="badge badge-mim">{{ p.code }}</span></td>
                    <td class="fw-bold">{{ p.title }}</td>
                    <td>{{ p.created_by }}</td>
                    <td class="text-success fw-bold">{{ "{:,.2f}".format(p.total_amount) }} ₺</td>
                    <td>{{ p.created_date.strftime('%d.%m.%Y') }}</td>
                    <td><span class="badge bg-info">{{ p.status }}</span></td>
                    <td class="text-end">
                        <a href="/proposal/view/{{ p.id }}" target="_blank" class="btn btn-sm btn-outline-mim"><i class="fa-solid fa-eye"></i> İncele / PDF</a>
                        <button class="btn btn-sm btn-outline-warning" data-bs-toggle="modal" data-bs-target="#reviseModal{{ p.id }}"><i class="fa-solid fa-code-branch"></i> Revize</button>
                        <button class="btn btn-sm btn-outline-danger" data-bs-toggle="modal" data-bs-target="#sendEmailModal{{ p.id }}"><i class="fa-brands fa-google"></i> Mail</button>
                    </td>
                </tr>

                <div class="modal fade" id="reviseModal{{ p.id }}" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content">
                    <form method="POST" action="/proposal/revise/{{ p.id }}">
                        <div class="modal-header"><h5 class="modal-title text-mim fw-bold">Teklif Revizesi: {{ p.code }}</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
                        <div class="modal-body row g-3 text-start">
                            <div class="col-md-12"><input type="text" name="title" class="form-control" value="{{ p.title }} (Revize)" required></div>
                            <div class="col-md-12"><input type="number" step="0.01" name="total_amount" class="form-control" value="{{ p.total_amount }}" required></div>
                            <div class="col-md-12"><textarea name="notes" class="form-control" rows="3">{{ p.notes or '' }}</textarea></div>
                        </div>
                        <div class="modal-footer"><button type="submit" class="btn btn-mim">Revize Kaydet</button></div>
                    </form>
                </div></div></div>

                <div class="modal fade" id="sendEmailModal{{ p.id }}" tabindex="-1"><div class="modal-dialog"><div class="modal-content">
                    <form method="POST" action="/proposal/{{ p.id }}/send-email">
                        <div class="modal-header"><h5 class="modal-title text-mim fw-bold">Mail İlet</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
                        <div class="modal-body row g-3 text-start">
                            <div class="col-md-12"><input type="email" name="to_email" class="form-control" value="{{ customer.email }}" required></div>
                            <div class="col-md-12"><input type="text" name="cc_emails" class="form-control" placeholder="CC Adresleri"></div>
                            <div class="col-md-12"><textarea name="extra_note" class="form-control" rows="3">Teklifimiz ekte sunulmuştur.</textarea></div>
                        </div>
                        <div class="modal-footer"><button type="submit" class="btn btn-danger">Gönder</button></div>
                    </form>
                </div></div></div>
                {% else %}<tr><td colspan="7" class="text-muted">Teklif bulunmuyor.</td></tr>{% endfor %}
            </tbody>
        </table>
    </div>

    <div class="tab-pane fade" id="tab-interactions">
        <div class="d-flex justify-content-between mb-3"><h5>Görüşmeler</h5><button class="btn btn-sm btn-mim" data-bs-toggle="modal" data-bs-target="#addInteractionModal">+ Not</button></div>
        {% for i in interactions %}
        <div class="p-3 border-bottom mb-2 bg-light rounded">
            <strong><i class="fa-solid fa-user me-1 text-mim"></i>{{ i.sales_rep }}</strong> - <small class="text-muted">{{ i.interaction_date.strftime('%d.%m.%Y %H:%M') }}</small>
            <p class="mb-1 mt-1"><strong>Müşteri:</strong> {{ i.customer_feedback }}</p>
            <p class="mb-0"><strong>Not:</strong> {{ i.notes }}</p>
        </div>
        {% else %}<p class="text-muted">Görüşme kaydı yok.</p>{% endfor %}
    </div>

    <div class="tab-pane fade" id="tab-docs">
        <div class="d-flex justify-content-between mb-3"><h5>Belgeler</h5><button class="btn btn-sm btn-mim" data-bs-toggle="modal" data-bs-target="#uploadDocModal">+ Yükle</button></div>
        <ul class="list-group">
            {% for d in documents %}
            <li class="list-group-item d-flex justify-content-between align-items-center">
                <div><i class="fa-solid fa-file-pdf text-danger me-2"></i><strong>[{{ d.file_type }}]</strong> {{ d.filename }}</div>
                <a href="/download-file/{{ d.filename }}" class="btn btn-sm btn-outline-secondary">İndir</a>
            </li>
            {% else %}<p class="text-muted">Yüklenmiş belge yok.</p>{% endfor %}
        </ul>
    </div>

    <div class="tab-pane fade" id="tab-sales">
        <div class="d-flex justify-content-between mb-3"><h5>Siparişler</h5><button class="btn btn-sm btn-mim" data-bs-toggle="modal" data-bs-target="#addSaleModal">+ Sipariş</button></div>
        <table class="table">
            <thead><tr><th>İş Adı</th><th>Tutar</th><th>Tahsilat</th><th>Durum</th><th>Teslim</th></tr></thead>
            <tbody>
                {% for s in sales %}
                <tr><td>{{ s.job_title }}</td><td>{{ "{:,.2f}".format(s.sale_amount) }} ₺</td><td>{{ "{:,.2f}".format(s.paid_amount) }} ₺</td><td>{{ s.payment_status }}</td><td>{{ s.delivery_date.strftime('%d.%m.%Y') if s.delivery_date else '-' }}</td></tr>
                {% else %}<tr><td colspan="5" class="text-muted">Kayıt yok.</td></tr>{% endfor %}
            </tbody>
        </table>
    </div>

    <div class="tab-pane fade" id="tab-production">
        <div class="d-flex justify-content-between mb-3"><h5>Üretim Aksaklıkları</h5><button class="btn btn-sm btn-danger" data-bs-toggle="modal" data-bs-target="#addProductionModal">+ Sorun</button></div>
        {% for pi in production_issues %}
        <div class="alert alert-warning border-start border-danger border-4">
            <h6 class="text-danger fw-bold">{{ pi.job_title }}</h6>
            <p class="mb-1">{{ pi.issue_description }}</p>
            <small class="text-muted">{{ pi.sales_notes or '-' }}</small>
        </div>
        {% else %}<p class="text-muted">Sorun yok.</p>{% endfor %}
    </div>
</div>

<div class="modal fade" id="addProposalModal" tabindex="-1"><div class="modal-dialog modal-xl"><div class="modal-content">
    <form method="POST" action="/customer/{{ customer.id }}/add-proposal">
        <div class="modal-header"><h5 class="modal-title text-mim fw-bold">Teklif Hazırla</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body row g-3">
            <div class="col-md-8"><input type="text" name="title" class="form-control" required placeholder="Teklif Başlığı"></div>
            <div class="col-md-4"><input type="text" name="created_by" class="form-control" value="{{ session.get('full_name') }}" readonly></div>
            <div id="proposalItemsContainer">
                <div class="p-3 mb-2 bg-light rounded border item-row">
                    <div class="row g-2">
                        <div class="col-md-5"><input type="text" name="item_name[]" class="form-control" placeholder="Kalem Adı" required></div>
                        <div class="col-md-2"><input type="number" name="item_qty[]" class="form-control qty" value="1" min="1" required></div>
                        <div class="col-md-2"><input type="number" step="0.01" name="item_price[]" class="form-control price" placeholder="Fiyat" required></div>
                        <div class="col-md-2"><select name="item_tax[]" class="form-select tax"><option value="20">%20</option><option value="10">%10</option><option value="1">%1</option><option value="0">%0</option></select></div>
                        <div class="col-md-1 d-flex align-items-end"><button type="button" class="btn btn-outline-danger w-100 remove-row-btn"><i class="fa-solid fa-trash"></i></button></div>
                        <div class="col-md-12 mt-2"><input type="text" name="item_desc[]" class="form-control form-control-sm" placeholder="Açıklama / Teknik Detay"></div>
                    </div>
                </div>
            </div>
            <div><button type="button" class="btn btn-sm btn-secondary" id="addItemBtn">+ Kalem Ekle</button></div>
            <div class="col-md-12"><textarea name="notes" class="form-control" rows="3" placeholder="Şartlar & Notlar"></textarea></div>
        </div>
        <div class="modal-footer"><button type="submit" class="btn btn-mim">Kaydet</button></div>
    </form>
</div></div></div>

<script>
document.getElementById('addItemBtn').addEventListener('click', function() {
    var container = document.getElementById('proposalItemsContainer');
    var newRow = document.createElement('div');
    newRow.className = 'p-3 mb-2 bg-light rounded border item-row';
    newRow.innerHTML = `
        <div class="row g-2">
            <div class="col-md-5"><input type="text" name="item_name[]" class="form-control" placeholder="Kalem Adı" required></div>
            <div class="col-md-2"><input type="number" name="item_qty[]" class="form-control qty" value="1" min="1" required></div>
            <div class="col-md-2"><input type="number" step="0.01" name="item_price[]" class="form-control price" placeholder="Fiyat" required></div>
            <div class="col-md-2"><select name="item_tax[]" class="form-select tax"><option value="20">%20</option><option value="10">%10</option><option value="1">%1</option><option value="0">%0</option></select></div>
            <div class="col-md-1 d-flex align-items-end"><button type="button" class="btn btn-outline-danger w-100 remove-row-btn"><i class="fa-solid fa-trash"></i></button></div>
            <div class="col-md-12 mt-2"><input type="text" name="item_desc[]" class="form-control form-control-sm" placeholder="Açıklama"></div>
        </div>
    `;
    container.appendChild(newRow);
});
document.addEventListener('click', function(e) {
    if (e.target && e.target.closest('.remove-row-btn')) {
        var rows = document.querySelectorAll('.item-row');
        if (rows.length > 1) { e.target.closest('.item-row').remove(); }
    }
});
</script>

<div class="modal fade" id="addInteractionModal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content"><form method="POST" action="/customer/{{ customer.id }}/add-interaction"><div class="modal-header"><h5 class="modal-title">Görüşme Notu</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body row g-3"><div class="col-md-6"><input type="text" name="sales_rep" class="form-control" value="{{ session.get('full_name') }}"></div><div class="col-md-6"><input type="text" name="location" class="form-control" placeholder="Yer / Tip"></div><div class="col-md-12"><textarea name="customer_feedback" class="form-control" placeholder="Müşteri Ne Dedi?" rows="2"></textarea></div><div class="col-md-12"><textarea name="notes" class="form-control" placeholder="Detay" rows="2"></textarea></div><div class="col-md-12"><input type="text" name="result" class="form-control" placeholder="Sonuç"></div></div><div class="modal-footer"><button type="submit" class="btn btn-mim">Kaydet</button></div></form></div></div></div>
<div class="modal fade" id="uploadDocModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form method="POST" action="/customer/{{ customer.id }}/upload-doc" enctype="multipart/form-data"><div class="modal-header"><h5 class="modal-title">Belge Yükle</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body row g-3"><div class="col-md-12"><select name="file_type" class="form-select"><option value="Sözleşme">Sözleşme</option><option value="Teknik Şartname">Teknik Şartname</option><option value="İhale">İhale</option><option value="Proje">Proje</option></select></div><div class="col-md-12"><input type="file" name="file" class="form-control" required></div></div><div class="modal-footer"><button type="submit" class="btn btn-mim">Yükle</button></div></form></div></div></div>
<div class="modal fade" id="addSaleModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form method="POST" action="/customer/{{ customer.id }}/add-sale"><div class="modal-header"><h5 class="modal-title">Sipariş Kaydı</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body row g-3"><div class="col-md-12"><input type="text" name="job_title" class="form-control" placeholder="İş Adı" required></div><div class="col-md-6"><input type="number" step="0.01" name="sale_amount" class="form-control" placeholder="Tutar" required></div><div class="col-md-6"><input type="number" step="0.01" name="paid_amount" class="form-control" placeholder="Tahsilat" value="0.00"></div><div class="col-md-6"><select name="payment_status" class="form-select"><option value="Ödendi">Ödendi</option><option value="Bekliyor">Bekliyor</option></select></div><div class="col-md-6"><input type="date" name="delivery_date" class="form-control"></div></div><div class="modal-footer"><button type="submit" class="btn btn-mim">Kaydet</button></div></form></div></div></div>
<div class="modal fade" id="addProductionModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form method="POST" action="/customer/{{ customer.id }}/add-production-issue"><div class="modal-header"><h5 class="modal-title text-danger fw-bold">Üretim Aksaklığı</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body row g-3"><div class="col-md-12"><select name="sale_id" class="form-select">{% for s in sales %}<option value="{{ s.id }}">{{ s.job_title }}</option>{% else %}<option value="">Kayıtlı İş Yok</option>{% endfor %}</select></div><div class="col-md-12"><textarea name="issue_description" class="form-control" placeholder="Sorun Açıklaması" required></textarea></div><div class="col-md-12"><textarea name="sales_notes" class="form-control" placeholder="Not"></textarea></div></div><div class="modal-footer"><button type="submit" class="btn btn-danger">Kaydet</button></div></form></div></div></div>
""")

PROPOSAL_VIEW_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>Teklif - {{ proposal.code }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
    <style>
        :root { --mim-purple: #701658; }
        body { background:#fff; padding:20px; font-family:'Segoe UI'; }
        .proposal-header { border-bottom:3px solid var(--mim-purple); padding-bottom:15px; margin-bottom:25px; }
        .btn-mim { background-color: var(--mim-purple); color: white !important; border: none; }
        .text-mim { color: var(--mim-purple) !important; }
        @media print { .no-print { display:none !important; } body { padding:0; } }
    </style>
</head>
<body>
<div class="container">
    <div class="no-print mb-4 d-flex justify-content-between align-items-center">
        <a href="javascript:history.back()" class="btn btn-secondary">Geri Dön</a>
        <div>
            <button onclick="downloadPDF()" class="btn btn-danger me-2">PDF Olarak İndir</button>
            <button onclick="window.print()" class="btn btn-mim">Yazdır</button>
        </div>
    </div>

    <div id="proposalPrintArea" class="p-4 bg-white">
        <div class="row proposal-header align-items-center">
            <div class="col-7">
                {% if company_logo %}
                <img src="/uploads/{{ company_logo }}" style="max-height:85px;" class="mb-2" alt="Firma Logosu">
                {% else %}
                <h2 class="text-mim fw-bold">{{ company_name or 'MİM REKLAM' }}</h2>
                {% endif %}
                <p class="mb-0 small text-muted">{{ company_address or '' }}</p>
                <p class="mb-0 small text-muted">Tel: {{ company_phone or '' }} | E-Posta: {{ company_email or '' }}</p>
            </div>
            <div class="col-5 text-end">
                <h3 class="fw-bold text-uppercase text-secondary">FİYAT TEKLİFİ</h3>
                <h5 class="text-mim fw-bold">Teklif No: {{ proposal.code }}</h5>
                <small class="text-muted">Tarih: {{ proposal.created_date.strftime('%d.%m.%Y') }}</small>
            </div>
        </div>

        <div class="row mb-4">
            <div class="col-6">
                <div class="p-3 bg-light rounded border">
                    <h6 class="fw-bold text-mim">MÜŞTERİ / FİRMA:</h6>
                    <h5 class="fw-bold mb-1">{{ customer.company_name }}</h5>
                    <p class="mb-0 small"><strong>Yetkili:</strong> {{ customer.contact_person }}</p>
                    <p class="mb-0 small"><strong>E-Posta:</strong> {{ customer.email }} | <strong>Tel:</strong> {{ customer.phone }}</p>
                    <p class="mb-0 small"><strong>Adres:</strong> {{ customer.address or '-' }}</p>
                    <p class="mb-0 small"><strong>Vergi:</strong> {{ customer.tax_office or '-' }} / {{ customer.tax_number or '-' }}</p>
                </div>
            </div>
            <div class="col-6">
                <div class="p-3 bg-light rounded border text-end">
                    <h6 class="fw-bold text-mim">HAZIRLAYAN:</h6>
                    <h5 class="fw-bold mb-1">{{ proposal.created_by }}</h5>
                    <p class="mb-0 small text-muted">MİM REKLAM Satış</p>
                </div>
            </div>
        </div>

        <h5 class="mb-3 fw-bold text-secondary">{{ proposal.title }}</h5>

        <table class="table table-bordered align-middle mb-4">
            <thead class="text-white" style="background-color:#701658 !important;">
                <tr>
                    <th width="5%">#</th>
                    <th width="45%">Ürün / Hizmet & Detay</th>
                    <th width="12%" class="text-center">Miktar</th>
                    <th width="13%" class="text-end">Birim Fiyat</th>
                    <th width="10%" class="text-center">KDV</th>
                    <th width="15%" class="text-end">Toplam</th>
                </tr>
            </thead>
            <tbody>
                {% for item in items %}
                <tr>
                    <td>{{ loop.index }}</td>
                    <td>
                        <strong class="text-dark">{{ item.name }}</strong>
                        {% if item.desc %}<div class="small text-muted mt-1 fst-italic">{{ item.desc }}</div>{% endif %}
                    </td>
                    <td class="text-center">{{ item.qty }}</td>
                    <td class="text-end">{{ "{:,.2f}".format(item.price) }} ₺</td>
                    <td class="text-center">%{{ item.tax }}</td>
                    <td class="text-end fw-bold">{{ "{:,.2f}".format(item.total) }} ₺</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>

        <div class="row justify-content-end mb-4">
            <div class="col-md-5 col-12">
                <table class="table table-bordered">
                    <tr><th>Ara Toplam:</th><td class="text-end">{{ "{:,.2f}".format(proposal.subtotal) }} ₺</td></tr>
                    <tr><th>KDV Toplamı:</th><td class="text-end">{{ "{:,.2f}".format(proposal.tax_total) }} ₺</td></tr>
                    <tr class="fw-bold" style="background-color:#fbeff7; color:#701658;">
                        <th>GENEL TOPLAM:</th>
                        <td class="text-end h5 fw-bold">{{ "{:,.2f}".format(proposal.total_amount) }} ₺</td>
                    </tr>
                </table>
            </div>
        </div>

        {% if proposal.notes %}
        <div class="p-3 border rounded bg-light">
            <h6 class="fw-bold text-mim">Teklif Şartları:</h6>
            <p class="mb-0 small" style="white-space:pre-line;">{{ proposal.notes }}</p>
        </div>
        {% endif %}
    </div>
</div>

<script>
function downloadPDF() {
    var element = document.getElementById('proposalPrintArea');
    var opt = {
        margin:       [10, 10, 10, 10],
        filename:     'Teklif_{{ proposal.code }}.pdf',
        image:        { type: 'jpeg', quality: 0.98 },
        html2canvas:  { scale: 2, useCORS: true },
        jsPDF:        { unit: 'mm', format: 'a4', orientation: 'portrait' }
    };
    html2pdf().set(opt).from(element).save();
}
</script>
</body>
</html>
"""

TASKS_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-2">
    <h2><i class="fa-solid fa-list-check text-mim me-2"></i>Görevler Paneli</h2>
    <button class="btn btn-mim" data-bs-toggle="modal" data-bs-target="#addTaskModal">+ Görev Ata</button>
</div>

<ul class="nav nav-pills mb-3">
  <li class="nav-item"><a class="nav-link {% if view_type != 'all' %}active bg-mim{% endif %}" href="/tasks?view=my">Bana Atanan Görevler</a></li>
  <li class="nav-item"><a class="nav-link {% if view_type == 'all' %}active bg-mim{% endif %}" href="/tasks?view=all">Tüm Görevler</a></li>
</ul>

<div class="card card-custom p-3 table-responsive">
    <table class="table table-hover align-middle">
        <thead class="table-light"><tr><th>Görev</th><th>Müşteri</th><th>Atayan</th><th>Atanan</th><th>Son Tarih</th><th>Öncelik</th><th>Durum</th><th class="text-end">İşlemler</th></tr></thead>
        <tbody>
            {% for t in tasks %}
            <tr>
                <td><strong>{{ t.title }}</strong><br><small class="text-muted">{{ t.description or '' }}</small></td>
                <td>{{ t.customer_name }}</td>
                <td><small>{{ t.assigned_by }}</small></td>
                <td><span class="badge bg-secondary">{{ t.assigned_to }}</span></td>
                <td>{{ t.due_date.strftime('%d.%m.%Y') }}</td>
                <td><span class="badge bg-secondary">{{ t.priority }}</span></td>
                <td>
                    {% if t.status == 'Tamamlandı' %}<span class="badge bg-success">Tamamlandı</span>
                    {% elif t.status == 'Tamamlanamadı' %}
                        <span class="badge bg-danger">Tamamlanamadı</span>
                        <button class="btn btn-sm btn-link text-danger p-0 ms-1" data-bs-toggle="modal" data-bs-target="#reasonModal{{ t.id }}"><i class="fa-solid fa-circle-question"></i></button>
                    {% else %}<span class="badge bg-warning text-dark">Bekliyor</span>{% endif %}
                </td>
                <td class="text-end">
                    <button class="btn btn-sm btn-outline-success" data-bs-toggle="modal" data-bs-target="#updateTaskModal{{ t.id }}">Durum</button>
                </td>
            </tr>

            {% if t.status == 'Tamamlanamadı' %}
            <div class="modal fade" id="reasonModal{{ t.id }}" tabindex="-1"><div class="modal-dialog"><div class="modal-content">
                <div class="modal-header bg-danger text-white"><h5 class="modal-title">Gerekçe</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div>
                <div class="modal-body"><p>{{ t.reason or 'Açıklama girilmemiş.' }}</p></div>
            </div></div></div>
            {% endif %}

            <div class="modal fade" id="updateTaskModal{{ t.id }}" tabindex="-1"><div class="modal-dialog"><div class="modal-content">
                <form method="POST" action="/task/update/{{ t.id }}">
                    <div class="modal-header"><h5 class="modal-title">Durum Güncelle</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
                    <div class="modal-body row g-3">
                        <div class="col-md-12"><select name="status" class="form-select"><option value="Bekliyor">Bekliyor</option><option value="Tamamlandı">Tamamlandı</option><option value="Tamamlanamadı">Tamamlanamadı</option></select></div>
                        <div class="col-md-12"><textarea name="reason" class="form-control" placeholder="Tamamlanamadıysa nedeni">{{ t.reason or '' }}</textarea></div>
                    </div>
                    <div class="modal-footer"><button type="submit" class="btn btn-mim">Kaydet</button></div>
                </form>
            </div></div></div>
            {% else %}<tr><td colspan="8" class="text-center py-4 text-muted">Görev bulunamadı.</td></tr>{% endfor %}
        </tbody>
    </table>
</div>

<div class="modal fade" id="addTaskModal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content">
    <form method="POST" action="/task/add">
        <div class="modal-header"><h5 class="modal-title">Görev Ata</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body row g-3">
            <div class="col-md-8"><input type="text" name="title" class="form-control" required placeholder="Görev Başlığı"></div>
            <div class="col-md-4"><select name="assigned_to" class="form-select">{% for u in users %}<option value="{{ u.full_name }}">{{ u.full_name }}</option>{% endfor %}</select></div>
            <div class="col-md-6"><select name="customer_id" class="form-select"><option value="">-- Müşteri Seçilmedi --</option>{% for c in customers %}<option value="{{ c.id }}">{{ c.company_name }}</option>{% endfor %}</select></div>
            <div class="col-md-3"><input type="date" name="due_date" class="form-control" required></div>
            <div class="col-md-3"><select name="priority" class="form-select"><option value="Normal">Normal</option><option value="Yüksek">Yüksek</option><option value="Acil">Acil</option></select></div>
            <div class="col-md-12"><textarea name="description" class="form-control" placeholder="Detay"></textarea></div>
        </div>
        <div class="modal-footer"><button type="submit" class="btn btn-mim">Ata</button></div>
    </form>
</div></div></div>
""")

DASHBOARD_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-2">
    <h2><i class="fa-solid fa-chart-pie text-mim me-2"></i>Yönetici Raporu</h2>
    <a href="/dashboard/export-report?sales_rep={{ filter_rep }}&category={{ filter_cat }}&start_date={{ filter_start }}&end_date={{ filter_end }}" target="_blank" class="btn btn-danger">Raporu Yazdır / PDF</a>
</div>

<div class="card card-custom p-3 mb-4 bg-white">
    <form method="GET" action="/dashboard" class="row g-3 align-items-end">
        <div class="col-md-3"><select name="sales_rep" class="form-select form-select-sm"><option value="ALL">Tüm Satış Ekibi</option>{% for u in sales_reps %}<option value="{{ u.full_name }}" {% if filter_rep == u.full_name %}selected{% endif %}>{{ u.full_name }}</option>{% endfor %}</select></div>
        <div class="col-md-3"><select name="category" class="form-select form-select-sm"><option value="ALL">Tüm Kategoriler</option>{% for c in categories %}<option value="{{ c.name }}" {% if filter_cat == c.name %}selected{% endif %}>{{ c.name }}</option>{% endfor %}</select></div>
        <div class="col-md-2"><input type="date" name="start_date" class="form-control form-control-sm" value="{{ filter_start }}"></div>
        <div class="col-md-2"><input type="date" name="end_date" class="form-control form-control-sm" value="{{ filter_end }}"></div>
        <div class="col-md-2"><button type="submit" class="btn btn-mim btn-sm w-100">Filtrele</button></div>
    </form>
</div>

<div class="row g-3 mb-4">
    <div class="col-md-3"><div class="card card-custom p-3 border-start border-success border-4"><small class="text-muted fw-bold">Toplam Ciro</small><h4 class="mt-1 text-success fw-bold">{{ "{:,.2f}".format(total_sales_amount) }} ₺</h4></div></div>
    <div class="col-md-3"><div class="card card-custom p-3 border-start border-info border-4"><small class="text-muted fw-bold">Tahsilat</small><h4 class="mt-1 text-info fw-bold">{{ "{:,.2f}".format(total_paid_amount) }} ₺</h4></div></div>
    <div class="col-md-3"><div class="card card-custom p-3 border-start border-danger border-4"><small class="text-muted fw-bold">Açık Bakiye</small><h4 class="mt-1 text-danger fw-bold">{{ "{:,.2f}".format(total_balance) }} ₺</h4></div></div>
    <div class="col-md-3"><div class="card card-custom p-3 border-start border-warning border-4"><small class="text-muted fw-bold">Teklifler</small><h4 class="mt-1 text-warning fw-bold">{{ "{:,.2f}".format(total_proposal_amount) }} ₺</h4></div></div>
</div>

<div class="card card-custom p-3 mb-4 table-responsive">
    <h5 class="fw-bold text-mim mb-3">Görev Detayları & Performans</h5>
    <table class="table table-hover align-middle">
        <thead class="table-light"><tr><th>Görev</th><th>Personel</th><th>Müşteri</th><th>Son Tarih</th><th>Durum</th><th>Gerekçe</th></tr></thead>
        <tbody>
            {% for t in tasks_list %}
            <tr><td>{{ t.title }}</td><td>{{ t.assigned_to }}</td><td>{{ t.customer_name }}</td><td>{{ t.due_date.strftime('%d.%m.%Y') }}</td><td>{{ t.status }}</td><td class="text-danger small">{{ t.reason or '-' }}</td></tr>
            {% else %}<tr><td colspan="6" class="text-center py-3 text-muted">Görev bulunamadı.</td></tr>{% endfor %}
        </tbody>
    </table>
</div>
""")

PATRON_REPORT_PDF_HTML = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8"><title>Yönetici Raporu</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>body { background:#fff; padding:30px; font-family:'Segoe UI'; } @media print { .no-print { display:none; } }</style>
</head>
<body>
<div class="container">
    <div class="no-print mb-4 d-flex justify-content-between align-items-center">
        <a href="javascript:history.back()" class="btn btn-secondary">Geri Dön</a>
        <button onclick="window.print()" class="btn btn-danger">Yazdır / PDF</button>
    </div>
    <div class="d-flex justify-content-between align-items-center border-bottom pb-3 mb-4">
        <div>{% if company_logo %}<img src="/uploads/{{ company_logo }}" style="max-height:70px;">{% else %}<h3 style="color:#701658;">MİM REKLAM</h3>{% endif %}</div>
        <div class="text-end"><h3 class="fw-bold" style="color:#701658;">YÖNETİCİ SÜREÇ RAPORU</h3><small class="text-muted">{{ now_str }}</small></div>
    </div>
    <table class="table table-bordered mb-4 text-center">
        <thead class="table-dark" style="background-color:#701658;"><tr><th>Toplam Ciro</th><th>Tahsil Edilen</th><th>Açık Alacak</th><th>Teklifler</th></tr></thead>
        <tbody><tr class="fw-bold h5"><td class="text-success">{{ "{:,.2f}".format(total_sales_amount) }} ₺</td><td class="text-info">{{ "{:,.2f}".format(total_paid_amount) }} ₺</td><td class="text-danger">{{ "{:,.2f}".format(total_balance) }} ₺</td><td class="text-warning">{{ "{:,.2f}".format(total_proposal_amount) }} ₺</td></tr></tbody>
    </table>
    <table class="table table-bordered small mb-4">
        <thead class="table-light"><tr><th>Görev</th><th>Personel</th><th>Müşteri</th><th>Son Tarih</th><th>Durum</th><th>Gerekçe</th></tr></thead>
        <tbody>
            {% for t in tasks_list %}
            <tr><td>{{ t.title }}</td><td>{{ t.assigned_to }}</td><td>{{ t.customer_name }}</td><td>{{ t.due_date.strftime('%d.%m.%Y') }}</td><td>{{ t.status }}</td><td class="text-danger">{{ t.reason or '-' }}</td></tr>
            {% else %}<tr><td colspan="6" class="text-center">Kayıt yok.</td></tr>{% endfor %}
        </tbody>
    </table>
</div>
</body>
</html>
"""

BULK_EMAIL_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<h2 class="mb-4"><i class="fa-brands fa-google text-danger me-2"></i>Gmail Toplu Mail Gönderimi</h2>
<div class="card card-custom p-4">
    <form method="POST" action="/send-bulk-email" enctype="multipart/form-data">
        <div class="row g-3">
            <div class="col-md-6"><select name="category" class="form-select"><option value="ALL">Tüm Müşteriler</option>{% for cat in categories %}<option value="{{ cat.name }}">{{ cat.name }}</option>{% endfor %}</select></div>
            <div class="col-md-6">
                <select class="form-select" id="templateSelector" onchange="applyTemplate()">
                    <option value="">-- Şablon Seçilmedi --</option>
                    {% for t in templates %}<option value="{{ t.id }}" data-subject="{{ t.subject }}" data-body="{{ t.body }}">{{ t.title }}</option>{% endfor %}
                </select>
            </div>
            <div class="col-md-12"><input type="text" name="cc_emails" class="form-control" placeholder="CC (Bilgi E-Postaları)"></div>
            <div class="col-md-12"><input type="text" name="subject" id="emailSubject" class="form-control" placeholder="Konu" required></div>
            <div class="col-md-12"><textarea name="body" id="emailBody" class="form-control" rows="6" placeholder="İçerik" required></textarea></div>
            <div class="col-md-12"><input type="file" name="attachment" class="form-control"></div>
            <div class="col-md-12"><button type="submit" class="btn btn-mim btn-lg">Toplu Gönderimi Başlat</button></div>
        </div>
    </form>
</div>
<script>
function applyTemplate() {
    var sel = document.getElementById('templateSelector');
    var opt = sel.options[sel.selectedIndex];
    if (opt.value) {
        document.getElementById('emailSubject').value = opt.getAttribute('data-subject');
        document.getElementById('emailBody').value = opt.getAttribute('data-body');
    }
}
</script>
""")

SETTINGS_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<h2 class="mb-4"><i class="fa-solid fa-gears text-mim me-2"></i>Ayarlar</h2>
<div class="row g-4">
    <div class="col-md-6">
        <div class="card card-custom p-4">
            <h5 class="fw-bold text-mim mb-3">Firma & Teklif Bilgileri</h5>
            {% if company_logo %}<div class="mb-3 text-center p-2 bg-light rounded"><img src="/uploads/{{ company_logo }}" style="max-height:80px;"></div>{% endif %}
            <form method="POST" action="/settings/save-company" enctype="multipart/form-data">
                <div class="mb-2"><input type="file" name="logo" class="form-control form-control-sm" accept="image/*"></div>
                <div class="mb-2"><input type="text" name="company_name" class="form-control form-control-sm" value="{{ company_name }}" required placeholder="Firma Adı"></div>
                <div class="mb-2"><input type="text" name="company_address" class="form-control form-control-sm" value="{{ company_address }}" placeholder="Adres"></div>
                <div class="row g-2 mb-3">
                    <div class="col-6"><input type="text" name="company_phone" class="form-control form-control-sm" value="{{ company_phone }}" placeholder="Telefon"></div>
                    <div class="col-6"><input type="email" name="company_email" class="form-control form-control-sm" value="{{ company_email }}" placeholder="E-Posta"></div>
                </div>
                <button type="submit" class="btn btn-mim btn-sm">Kaydet</button>
            </form>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card card-custom p-4">
            <h5 class="fw-bold text-danger mb-3">Gmail SMTP</h5>
            <form method="POST" action="/settings/save-gmail">
                <div class="mb-3"><input type="email" name="gmail_user" class="form-control" value="{{ gmail_user }}" required placeholder="Gmail Adresi"></div>
                <div class="mb-3"><input type="password" name="gmail_pass" class="form-control" value="{{ gmail_pass }}" required placeholder="16 Haneli Şifre"></div>
                <button type="submit" class="btn btn-danger btn-sm">Kaydet</button>
            </form>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card card-custom p-4">
            <h5 class="fw-bold text-mim mb-3">Kategoriler</h5>
            <form method="POST" action="/settings/add-category" class="row g-2 mb-3">
                <div class="col-8"><input type="text" name="category_name" class="form-control form-control-sm" placeholder="Kategori" required></div>
                <div class="col-4"><button type="submit" class="btn btn-mim btn-sm w-100">Ekle</button></div>
            </form>
            <ul class="list-group">
                {% for cat in categories %}<li class="list-group-item d-flex justify-content-between align-items-center py-2">{{ cat.name }}<a href="/settings/delete-category/{{ cat.id }}" class="btn btn-sm btn-outline-danger">Sil</a></li>{% endfor %}
            </ul>
        </div>
    </div>
    <div class="col-md-6">
        <div class="card card-custom p-4">
            <div class="d-flex justify-content-between align-items-center mb-3"><h5 class="fw-bold text-mim">Hazır Şablonlar</h5><button class="btn btn-sm btn-mim" data-bs-toggle="modal" data-bs-target="#addTplModal">+ Ekle</button></div>
            <ul class="list-group">
                {% for t in templates %}<li class="list-group-item d-flex justify-content-between align-items-center py-2"><div><strong>{{ t.title }}</strong></div><a href="/settings/delete-template/{{ t.id }}" class="btn btn-sm btn-outline-danger">Sil</a></li>{% endfor %}
            </ul>
        </div>
    </div>
    <div class="col-md-12">
        <div class="card card-custom p-4">
            <div class="d-flex justify-content-between align-items-center mb-3"><h5 class="fw-bold">Personel Hesapları</h5><button class="btn btn-sm btn-mim" data-bs-toggle="modal" data-bs-target="#addUserModal">+ Personel Ekle</button></div>
            <table class="table">
                <thead><tr><th>Kullanıcı</th><th>Ad Soyad</th><th>E-Posta</th><th>Yetki</th><th>İşlem</th></tr></thead>
                <tbody>
                    {% for u in users %}
                    <tr><td>{{ u.username }}</td><td>{{ u.full_name }}</td><td>{{ u.email or '-' }}</td><td>{{ u.role }}</td><td>{% if u.username != 'admin' %}<a href="/settings/delete-user/{{ u.id }}" class="btn btn-sm btn-outline-danger">Sil</a>{% endif %}</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>
<div class="modal fade" id="addTplModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form method="POST" action="/settings/add-template"><div class="modal-header"><h5 class="modal-title">Şablon</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body row g-3"><div class="col-md-12"><input type="text" name="title" class="form-control" placeholder="Başlık" required></div><div class="col-md-12"><input type="text" name="subject" class="form-control" placeholder="Konu" required></div><div class="col-md-12"><textarea name="body" class="form-control" rows="4" placeholder="İçerik" required></textarea></div></div><div class="modal-footer"><button type="submit" class="btn btn-mim">Kaydet</button></div></form></div></div></div>
<div class="modal fade" id="addUserModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form method="POST" action="/settings/add-user"><div class="modal-header"><h5 class="modal-title">Personel</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body row g-3"><div class="col-md-6"><input type="text" name="username" class="form-control" placeholder="Kullanıcı Adı" required></div><div class="col-md-6"><input type="text" name="full_name" class="form-control" placeholder="Ad Soyad" required></div><div class="col-md-6"><input type="email" name="email" class="form-control" placeholder="E-Posta" required></div><div class="col-md-6"><input type="password" name="password" class="form-control" placeholder="Şifre" required></div><div class="col-md-12"><select name="role" class="form-select"><option value="Personel">Personel</option><option value="Admin">Admin</option></select></div></div><div class="modal-footer"><button type="submit" class="btn btn-mim">Kaydet</button></div></form></div></div></div>
""")

# -----------------------------------------------------------------------------
# FLASK CONTROLLER ROTALARI
# -----------------------------------------------------------------------------

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    if not session.get('user_id'):
        return redirect(url_for('login'))
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.before_request
def check_auth():
    allowed = ['login', 'static', 'pwa_manifest', 'service_worker']
    if not session.get('user_id') and request.endpoint not in allowed:
        return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username'].strip()).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            session['user_id'] = user.id
            session['username'] = user.username
            session['full_name'] = user.full_name
            session['role'] = user.role
            flash(f'Hoş geldiniz, {user.full_name}', 'success')
            return redirect(url_for('index'))
        flash('Hatalı kullanıcı adı veya şifre!', 'danger')
    return render_template_string(LOGIN_HTML)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    s_query = request.args.get('search', '').strip()
    f_cat = request.args.get('category', 'ALL')
    q = Customer.query
    if s_query:
        q = q.filter((Customer.company_name.contains(s_query)) | (Customer.contact_person.contains(s_query)) | (Customer.tax_number.contains(s_query)))
    if f_cat != 'ALL':
        q = q.filter(Customer.category == f_cat)
    customers = q.order_by(Customer.id.desc()).all()
    return render_template_string(INDEX_HTML, customers=customers, categories=CustomerCategory.query.all(), users=User.query.all(), search_query=s_query, filter_cat=f_cat)

@app.route('/customer/add', methods=['POST'])
def add_customer():
    c = Customer(
        company_name=request.form['company_name'],
        contact_person=request.form['contact_person'],
        email=request.form['email'],
        phone=request.form['phone'],
        tax_office=request.form.get('tax_office'),
        tax_number=request.form.get('tax_number'),
        category=request.form.get('category', 'Genel'),
        assigned_sales_rep=request.form.get('assigned_sales_rep', 'Çiğdem Biçer'),
        payment_terms=request.form.get('payment_terms'),
        credit_limit=float(request.form.get('credit_limit', 0) or 0),
        address=request.form.get('address')
    )
    db.session.add(c)
    db.session.commit()
    flash('Müşteri kaydedildi.', 'success')
    return redirect(url_for('index'))

@app.route('/customer/edit/<int:id>', methods=['POST'])
def edit_customer(id):
    c = Customer.query.get_or_404(id)
    c.company_name = request.form['company_name']
    c.contact_person = request.form['contact_person']
    c.email = request.form['email']
    c.phone = request.form['phone']
    c.tax_office = request.form.get('tax_office')
    c.tax_number = request.form.get('tax_number')
    c.category = request.form.get('category')
    c.assigned_sales_rep = request.form.get('assigned_sales_rep')
    c.payment_terms = request.form.get('payment_terms')
    c.credit_limit = float(request.form.get('credit_limit', 0) or 0)
    c.address = request.form.get('address')
    db.session.commit()
    flash('Müşteri güncellendi.', 'success')
    return redirect(request.referrer or url_for('index'))

@app.route('/customer/delete/<int:id>')
def delete_customer(id):
    c = Customer.query.get_or_404(id)
    Proposal.query.filter_by(customer_id=id).delete()
    Task.query.filter_by(customer_id=id).delete()
    Interaction.query.filter_by(customer_id=id).delete()
    Document.query.filter_by(customer_id=id).delete()
    SalesAndPayment.query.filter_by(customer_id=id).delete()
    ProductionIssue.query.filter_by(customer_id=id).delete()
    db.session.delete(c)
    db.session.commit()
    flash('Müşteri silindi.', 'info')
    return redirect(url_for('index'))

@app.route('/customer/<int:id>')
def customer_detail(id):
    customer = Customer.query.get_or_404(id)
    proposals = Proposal.query.filter_by(customer_id=id).order_by(Proposal.id.desc()).all()
    interactions = Interaction.query.filter_by(customer_id=id).order_by(Interaction.interaction_date.desc()).all()
    documents = Document.query.filter_by(customer_id=id).all()
    sales = SalesAndPayment.query.filter_by(customer_id=id).all()
    raw_issues = ProductionIssue.query.filter_by(customer_id=id).all()
    production_issues = [{'job_title': (SalesAndPayment.query.get(pi.sale_id).job_title if pi.sale_id else 'Genel Sipariş'), 'issue_description': pi.issue_description, 'sales_notes': pi.sales_notes, 'status': pi.status} for pi in raw_issues]
    return render_template_string(CUSTOMER_DETAIL_HTML, customer=customer, proposals=proposals, interactions=interactions, documents=documents, sales=sales, production_issues=production_issues)

@app.route('/customer/<int:id>/add-proposal', methods=['POST'])
def add_proposal(id):
    item_names = request.form.getlist('item_name[]')
    item_qtys = request.form.getlist('item_qty[]')
    item_prices = request.form.getlist('item_price[]')
    item_taxes = request.form.getlist('item_tax[]')
    item_descs = request.form.getlist('item_desc[]')
    
    items, subtotal, tax_total = [], 0.0, 0.0
    for i in range(len(item_names)):
        qty = float(item_qtys[i] or 1)
        price = float(item_prices[i] or 0)
        tax = float(item_taxes[i] or 0)
        desc = item_descs[i] if i < len(item_descs) else ''
        line_sub = qty * price
        line_tax = line_sub * (tax / 100.0)
        subtotal += line_sub
        tax_total += line_tax
        items.append({'name': item_names[i], 'qty': qty, 'price': price, 'tax': tax, 'desc': desc, 'total': line_sub + line_tax})

    p_count = Proposal.query.count() + 1
    code = f"MİM-{1000 + p_count}"

    p = Proposal(
        customer_id=id,
        code=code,
        title=request.form['title'],
        created_by=request.form['created_by'],
        items_json=json.dumps(items),
        subtotal=subtotal,
        tax_total=tax_total,
        total_amount=subtotal + tax_total,
        notes=request.form.get('notes'),
        status='Hazırlandı'
    )
    db.session.add(p)
    db.session.commit()
    flash(f'{code} teklifi kaydedildi.', 'success')
    return redirect(url_for('customer_detail', id=id))

@app.route('/proposal/revise/<int:p_id>', methods=['POST'])
def revise_proposal(p_id):
    orig = Proposal.query.get_or_404(p_id)
    rev_count = Proposal.query.filter((Proposal.parent_id == orig.id) | (Proposal.id == orig.id)).count()
    base_code = orig.code.split('-R')[0]
    new_code = f"{base_code}-R{rev_count}"

    new_p = Proposal(
        customer_id=orig.customer_id,
        code=new_code,
        title=request.form['title'],
        created_by=session.get('full_name'),
        items_json=orig.items_json,
        subtotal=orig.subtotal,
        tax_total=orig.tax_total,
        total_amount=float(request.form['total_amount']),
        notes=request.form.get('notes'),
        parent_id=orig.id,
        status='Revize Edildi'
    )
    db.session.add(new_p)
    db.session.commit()
    flash(f'{new_code} revize teklifi kaydedildi.', 'success')
    return redirect(url_for('customer_detail', id=orig.customer_id))

@app.route('/proposal/view/<int:p_id>')
def view_proposal(p_id):
    proposal = Proposal.query.get_or_404(p_id)
    customer = Customer.query.get(proposal.customer_id)
    items = json.loads(proposal.items_json) if proposal.items_json else []
    logo = SystemSetting.query.filter_by(key='COMPANY_LOGO').first()
    c_name = SystemSetting.query.filter_by(key='COMPANY_NAME').first()
    c_addr = SystemSetting.query.filter_by(key='COMPANY_ADDR').first()
    c_phone = SystemSetting.query.filter_by(key='COMPANY_PHONE').first()
    c_email = SystemSetting.query.filter_by(key='COMPANY_EMAIL').first()

    return render_template_string(
        PROPOSAL_VIEW_HTML,
        proposal=proposal,
        customer=customer,
        items=items,
        company_logo=logo.value if logo else None,
        company_name=c_name.value if c_name else 'MİM REKLAM',
        company_address=c_addr.value if c_addr else '',
        company_phone=c_phone.value if c_phone else '',
        company_email=c_email.value if c_email else ''
    )

@app.route('/proposal/<int:p_id>/send-email', methods=['POST'])
def send_proposal_email(p_id):
    proposal = Proposal.query.get_or_404(p_id)
    customer = Customer.query.get(proposal.customer_id)
    to_email = request.form.get('to_email')
    cc_emails = request.form.get('cc_emails')
    extra_note = request.form.get('extra_note', '')

    body = f"""
    <h3>Sayın {customer.contact_person or customer.company_name},</h3>
    <p>Firmanıza özel hazırlanan <strong>{proposal.code} - {proposal.title}</strong> teklifimiz sunulmuştur.</p>
    <p><strong>Toplam Tutar:</strong> {proposal.total_amount:,.2f} TL</p>
    <p style='background:#fbeff7; padding:10px; border-left:4px solid #701658;'>{extra_note}</p>
    <br><small>MİM REKLAM Kurumsal Satış</small>
    """
    success, msg = send_gmail_with_attachment(to_email, f"Teklif: {proposal.code} - {proposal.title}", body, cc_emails=cc_emails)
    flash(f'Teklif {to_email} adresine iletildi.' if success else f'Mail hatası: {msg}', 'success' if success else 'danger')
    return redirect(url_for('customer_detail', id=customer.id))

@app.route('/tasks')
def tasks():
    view_type = request.args.get('view', 'my')
    curr_user = session.get('full_name', '').strip()
    raw_tasks = Task.query.order_by(Task.due_date.asc()).all() if view_type == 'all' else Task.query.filter(Task.assigned_to == curr_user).order_by(Task.due_date.asc()).all()
    tasks_list = []
    for t in raw_tasks:
        c = Customer.query.get(t.customer_id) if t.customer_id else None
        tasks_list.append({
            'id': t.id,
            'title': t.title,
            'description': t.description,
            'assigned_by': t.assigned_by,
            'assigned_to': t.assigned_to,
            'customer_name': c.company_name if c else 'Genel Görev',
            'due_date': t.due_date,
            'priority': t.priority,
            'status': t.status,
            'reason': t.reason
        })
    return render_template_string(TASKS_HTML, tasks=tasks_list, view_type=view_type, users=User.query.all(), customers=Customer.query.all())

@app.route('/task/add', methods=['POST'])
def add_task():
    try:
        cust_id = request.form.get('customer_id')
        due_str = request.form.get('due_date')
        customer_id_val = int(cust_id) if (cust_id and cust_id.strip() != '') else None
        due_date_val = datetime.strptime(due_str, '%Y-%m-%d') if due_str else datetime.now()
        t = Task(
            title=request.form['title'].strip(),
            description=request.form.get('description'),
            assigned_by=session.get('full_name'),
            assigned_to=request.form['assigned_to'].strip(),
            customer_id=customer_id_val,
            due_date=due_date_val,
            priority=request.form.get('priority', 'Normal')
        )
        db.session.add(t)
        db.session.commit()
        flash('Görev başarıyla atandı.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Hata: {str(e)}', 'danger')
    return redirect(url_for('tasks'))

@app.route('/task/update/<int:id>', methods=['POST'])
def update_task(id):
    t = Task.query.get_or_404(id)
    t.status = request.form['status']
    t.reason = request.form.get('reason') if t.status == 'Tamamlanamadı' else None
    db.session.commit()
    flash('Görev güncellendi.', 'success')
    return redirect(url_for('tasks'))

@app.route('/customer/<int:id>/add-interaction', methods=['POST'])
def add_interaction(id):
    inter = Interaction(customer_id=id, sales_rep=request.form['sales_rep'], location=request.form.get('location'), customer_feedback=request.form.get('customer_feedback'), notes=request.form.get('notes'), result=request.form.get('result'))
    db.session.add(inter)
    db.session.commit()
    flash('Görüşme notu eklendi.', 'success')
    return redirect(url_for('customer_detail', id=id))

@app.route('/customer/<int:id>/upload-doc', methods=['POST'])
def upload_doc(id):
    file = request.files.get('file')
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        doc = Document(customer_id=id, filename=filename, file_type=request.form.get('file_type'))
        db.session.add(doc)
        db.session.commit()
        flash('Belge yüklendi.', 'success')
    return redirect(url_for('customer_detail', id=id))

@app.route('/download-file/<filename>')
def download_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)

@app.route('/customer/<int:id>/add-sale', methods=['POST'])
def add_sale(id):
    sale_amt = float(request.form['sale_amount'])
    paid_amt = float(request.form.get('paid_amount', 0))
    del_date = request.form.get('delivery_date')
    s = SalesAndPayment(customer_id=id, job_title=request.form['job_title'], sale_amount=sale_amt, paid_amount=paid_amt, payment_status=request.form['payment_status'], delivery_date=datetime.strptime(del_date, '%Y-%m-%d') if del_date else None)
    c = Customer.query.get(id)
    c.current_balance += (sale_amt - paid_amt)
    db.session.add(s)
    db.session.commit()
    flash('Satış kaydedildi.', 'success')
    return redirect(url_for('customer_detail', id=id))

@app.route('/customer/<int:id>/add-production-issue', methods=['POST'])
def add_production_issue(id):
    sale_id = request.form.get('sale_id')
    pi = ProductionIssue(customer_id=id, sale_id=int(sale_id) if (sale_id and sale_id.strip() != '') else None, issue_description=request.form['issue_description'], sales_notes=request.form.get('sales_notes'))
    db.session.add(pi)
    db.session.commit()
    flash('Üretim notu kaydedildi.', 'warning')
    return redirect(url_for('customer_detail', id=id))

def get_report_metrics(f_rep, f_cat, f_start, f_end):
    c_query = Customer.query
    if f_rep != 'ALL': c_query = c_query.filter(Customer.assigned_sales_rep == f_rep)
    if f_cat != 'ALL': c_query = c_query.filter(Customer.category == f_cat)
    customers = c_query.all()
    cust_ids = [c.id for c in customers]

    s_query = SalesAndPayment.query.filter(SalesAndPayment.customer_id.in_(cust_ids))
    if f_start: s_query = s_query.filter(SalesAndPayment.sale_date >= datetime.strptime(f_start, '%Y-%m-%d'))
    if f_end: s_query = s_query.filter(SalesAndPayment.sale_date <= datetime.strptime(f_end, '%Y-%m-%d'))
    sales = s_query.all()

    total_sales_amount = sum(s.sale_amount for s in sales)
    total_paid_amount = sum(s.paid_amount for s in sales)
    total_balance = sum(c.current_balance for c in customers)
    total_proposal_amount = sum(p.total_amount for p in Proposal.query.filter(Proposal.customer_id.in_(cust_ids)).all())

    t_query = Task.query
    if f_rep != 'ALL': t_query = t_query.filter(Task.assigned_to == f_rep)
    tasks = t_query.order_by(Task.due_date.asc()).all()

    tasks_list = []
    for t in tasks:
        cust = Customer.query.get(t.customer_id) if t.customer_id else None
        tasks_list.append({
            'title': t.title,
            'assigned_to': t.assigned_to,
            'customer_name': cust.company_name if cust else '-',
            'due_date': t.due_date,
            'status': t.status,
            'reason': t.reason
        })

    return {
        'total_sales_amount': total_sales_amount,
        'total_paid_amount': total_paid_amount,
        'total_balance': total_balance,
        'total_proposal_amount': total_proposal_amount,
        'tasks_list': tasks_list
    }

@app.route('/dashboard')
def dashboard():
    f_rep = request.args.get('sales_rep', 'ALL')
    f_cat = request.args.get('category', 'ALL')
    f_start = request.args.get('start_date', '')
    f_end = request.args.get('end_date', '')
    data = get_report_metrics(f_rep, f_cat, f_start, f_end)
    return render_template_string(DASHBOARD_HTML, filter_rep=f_rep, filter_cat=f_cat, filter_start=f_start, filter_end=f_end, sales_reps=User.query.all(), categories=CustomerCategory.query.all(), **data)

@app.route('/dashboard/export-report')
def export_report():
    f_rep = request.args.get('sales_rep', 'ALL')
    f_cat = request.args.get('category', 'ALL')
    f_start = request.args.get('start_date', '')
    f_end = request.args.get('end_date', '')
    data = get_report_metrics(f_rep, f_cat, f_start, f_end)
    logo = SystemSetting.query.filter_by(key='COMPANY_LOGO').first()
    c_name = SystemSetting.query.filter_by(key='COMPANY_NAME').first()
    return render_template_string(PATRON_REPORT_PDF_HTML, filter_rep=f_rep, filter_cat=f_cat, filter_start=f_start, filter_end=f_end, now_str=datetime.now().strftime('%d.%m.%Y %H:%M'), company_logo=logo.value if logo else None, company_name=c_name.value if c_name else '', **data)

@app.route('/bulk-email')
def bulk_email():
    return render_template_string(BULK_EMAIL_HTML, categories=CustomerCategory.query.all(), templates=EmailTemplate.query.all())

@app.route('/send-bulk-email', methods=['POST'])
def send_bulk_email():
    cat = request.form.get('category')
    subject = request.form.get('subject')
    body = request.form.get('body')
    cc_emails = request.form.get('cc_emails')
    attachment_path = None
    file = request.files.get('attachment')
    if file and file.filename:
        filename = secure_filename(file.filename)
        attachment_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(attachment_path)

    targets = Customer.query.all() if cat == 'ALL' else Customer.query.filter_by(category=cat).all()
    sent_count = 0
    for c in targets:
        if c.email and isinstance(c.email, str) and '@' in c.email:
            success, _ = send_gmail_with_attachment(c.email.strip(), subject, f"<p>Sayın {c.company_name},</p>{body}", attachment_path, cc_emails=cc_emails)
            if success: sent_count += 1
            time.sleep(1)

    flash(f'Toplu e-posta tamamlandı! {sent_count} müşteriye iletildi.', 'success')
    return redirect(url_for('bulk_email'))

@app.route('/settings')
def settings():
    if session.get('role') != 'Admin': return redirect(url_for('index'))
    g_user = SystemSetting.query.filter_by(key='GMAIL_USER').first()
    g_pass = SystemSetting.query.filter_by(key='GMAIL_APP_PASS').first()
    logo = SystemSetting.query.filter_by(key='COMPANY_LOGO').first()
    c_name = SystemSetting.query.filter_by(key='COMPANY_NAME').first()
    c_addr = SystemSetting.query.filter_by(key='COMPANY_ADDR').first()
    c_phone = SystemSetting.query.filter_by(key='COMPANY_PHONE').first()
    c_email = SystemSetting.query.filter_by(key='COMPANY_EMAIL').first()

    return render_template_string(
        SETTINGS_HTML,
        gmail_user=g_user.value if g_user else '',
        gmail_pass=g_pass.value if g_pass else '',
        company_logo=logo.value if logo else None,
        company_name=c_name.value if c_name else 'MİM REKLAM',
        company_address=c_addr.value if c_addr else '',
        company_phone=c_phone.value if c_phone else '',
        company_email=c_email.value if c_email else '',
        categories=CustomerCategory.query.all(),
        templates=EmailTemplate.query.all(),
        users=User.query.all()
    )

@app.route('/settings/save-company', methods=['POST'])
def save_company():
    c_name = SystemSetting.query.filter_by(key='COMPANY_NAME').first() or SystemSetting(key='COMPANY_NAME')
    c_addr = SystemSetting.query.filter_by(key='COMPANY_ADDR').first() or SystemSetting(key='COMPANY_ADDR')
    c_phone = SystemSetting.query.filter_by(key='COMPANY_PHONE').first() or SystemSetting(key='COMPANY_PHONE')
    c_email = SystemSetting.query.filter_by(key='COMPANY_EMAIL').first() or SystemSetting(key='COMPANY_EMAIL')
    c_name.value = request.form['company_name']
    c_addr.value = request.form['company_address']
    c_phone.value = request.form['company_phone']
    c_email.value = request.form['company_email']
    db.session.add_all([c_name, c_addr, c_phone, c_email])

    file = request.files.get('logo')
    if file and file.filename:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        logo = SystemSetting.query.filter_by(key='COMPANY_LOGO').first() or SystemSetting(key='COMPANY_LOGO')
        logo.value = filename
        db.session.add(logo)

    db.session.commit()
    flash('Firma ve logo güncellendi.', 'success')
    return redirect(url_for('settings'))

@app.route('/settings/save-gmail', methods=['POST'])
def save_gmail():
    u = SystemSetting.query.filter_by(key='GMAIL_USER').first() or SystemSetting(key='GMAIL_USER')
    p = SystemSetting.query.filter_by(key='GMAIL_APP_PASS').first() or SystemSetting(key='GMAIL_APP_PASS')
    u.value, p.value = request.form['gmail_user'], request.form['gmail_pass']
    db.session.add_all([u, p])
    db.session.commit()
    flash('Gmail kaydedildi.', 'success')
    return redirect(url_for('settings'))

@app.route('/settings/add-category', methods=['POST'])
def add_category():
    cat = CustomerCategory(name=request.form['category_name'].strip())
    db.session.add(cat)
    db.session.commit()
    flash('Kategori eklendi.', 'success')
    return redirect(url_for('settings'))

@app.route('/settings/delete-category/<int:id>')
def delete_category(id):
    db.session.delete(CustomerCategory.query.get_or_404(id))
    db.session.commit()
    return redirect(url_for('settings'))

@app.route('/settings/add-template', methods=['POST'])
def add_template():
    t = EmailTemplate(title=request.form['title'], subject=request.form['subject'], body=request.form['body'])
    db.session.add(t)
    db.session.commit()
    flash('Şablon eklendi.', 'success')
    return redirect(url_for('settings'))

@app.route('/settings/delete-template/<int:id>')
def delete_template(id):
    db.session.delete(EmailTemplate.query.get_or_404(id))
    db.session.commit()
    return redirect(url_for('settings'))

@app.route('/settings/add-user', methods=['POST'])
def add_user():
    u = User(username=request.form['username'].strip(), full_name=request.form['full_name'].strip(), email=request.form['email'].strip(), password_hash=generate_password_hash(request.form['password']), role=request.form['role'])
    db.session.add(u)
    db.session.commit()
    flash('Personel eklendi.', 'success')
    return redirect(url_for('settings'))

@app.route('/settings/delete-user/<int:id>')
def delete_user(id):
    db.session.delete(User.query.get_or_404(id))
    db.session.commit()
    return redirect(url_for('settings'))

@app.route('/download-template')
def download_template():
    cats = [c.name for c in CustomerCategory.query.all()]
    cat_str = ", ".join(cats) if cats else "Genel, VIP, Aktif"
    df = pd.DataFrame(columns=['Firma Adı (Zorunlu)', 'Yetkili Ad Soyad (Zorunlu)', 'E-Posta (Zorunlu)', 'Telefon (Zorunlu)', 'Kategori (' + cat_str + ')', 'Sorumlu Satıcı', 'Vergi Dairesi', 'Vergi No', 'Ödeme Vadesi', 'Kredi Limiti', 'Adres'])
    filepath = os.path.join(UPLOAD_FOLDER, 'Musteri_Yukleme_Sablonu.xlsx')
    df.to_excel(filepath, index=False)
    return send_file(filepath, as_attachment=True)

@app.route('/import-excel', methods=['POST'])
def import_excel():
    file = request.files.get('file')
    if file:
        df = pd.read_excel(file)
        success_count = 0
        for _, row in df.iterrows():
            c_name = row.get('Firma Adı (Zorunlu)') or row.get('Firma Adı')
            c_person = row.get('Yetkili Ad Soyad (Zorunlu)') or row.get('Yetkili')
            c_email = row.get('E-Posta (Zorunlu)') or row.get('E-Posta')
            c_phone = row.get('Telefon (Zorunlu)') or row.get('Telefon')

            if pd.notnull(c_name) and pd.notnull(c_person) and pd.notnull(c_email) and pd.notnull(c_phone):
                cat_val = 'Genel'
                for col in row.index:
                    if 'Kategori' in str(col) and pd.notnull(row[col]):
                        cat_val = str(row[col]).strip()
                        break
                c = Customer(
                    company_name=str(c_name).strip(),
                    contact_person=str(c_person).strip(),
                    email=str(c_email).strip(),
                    phone=str(c_phone).strip(),
                    category=cat_val,
                    assigned_sales_rep=str(row.get('Sorumlu Satıcı', 'Çiğdem Biçer')),
                    tax_office=str(row.get('Vergi Dairesi', '')) if pd.notnull(row.get('Vergi Dairesi')) else '',
                    tax_number=str(row.get('Vergi No', '')) if pd.notnull(row.get('Vergi No')) else '',
                    payment_terms=str(row.get('Ödeme Vadesi', '')) if pd.notnull(row.get('Ödeme Vadesi')) else '',
                    credit_limit=float(row.get('Kredi Limiti', 0)) if pd.notnull(row.get('Kredi Limiti')) else 0.0,
                    address=str(row.get('Adres', '')) if pd.notnull(row.get('Adres')) else ''
                )
                db.session.add(c)
                success_count += 1
        db.session.commit()
        flash(f'{success_count} müşteri yüklendi.', 'success')
    return redirect(url_for('index'))

# -----------------------------------------------------------------------------
# Production initialization. Existing database is preserved.
# -----------------------------------------------------------------------------
def init_system():
    with app.app_context():
        db.create_all()
        if User.query.count() == 0:
            password = os.getenv('ADMIN_INITIAL_PASSWORD')
            if not password or len(password) < 12:
                raise RuntimeError('New database requires ADMIN_INITIAL_PASSWORD (12+ characters)')
            db.session.add(User(
                username='admin',
                full_name='Sistem Yöneticisi',
                email='admin@mimreklam.com',
                password_hash=generate_password_hash(password),
                role='Admin'
            ))
            db.session.commit()

if __name__ == '__main__':
    init_system()
    app.run(host='0.0.0.0', port=8000)
