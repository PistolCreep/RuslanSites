from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, abort
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from collections import Counter, defaultdict
import os
import secrets
import re
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload
import json
from functools import wraps
from flask import Response
import csv
import zipfile
from io import StringIO, BytesIO
from xml.sax.saxutils import escape

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///inventory_system.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '').lower() in {'1', 'true', 'yes'}

# Создаем папки
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('static/uploads', exist_ok=True)

db = SQLAlchemy(app)
STATIC_UPLOADS_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

RESERVATION_STATUSES = {
    'pending': 'Ожидает подтверждения',
    'confirmed': 'Подтверждено',
    'issued': 'Выдан',
    'not_issued': 'Не выдан',
    'cancelled': 'Отменено',
}

ALLOWED_STATUS_TRANSITIONS = {
    'pending': {'pending', 'confirmed', 'not_issued', 'cancelled'},
    'confirmed': {'confirmed', 'pending', 'issued', 'not_issued', 'cancelled'},
    'issued': {'issued', 'not_issued', 'cancelled'},
    'not_issued': {'not_issued'},
    'cancelled': {'cancelled'},
}

STATUS_BADGES = {
    'pending': 'badge-info',
    'confirmed': 'badge-primary',
    'issued': 'badge-success',
    'not_issued': 'badge-warning',
    'cancelled': 'badge-secondary',
}

NOT_ISSUED_REASONS = [
    'Клиент не пришел',
    'Передумал покупать',
    'Не подошел размер',
    'Не понравился дизайн',
    'Не устроила цена',
    'Товар был поврежден',
    'Ошибка бронирования',
    'Другая причина',
]

OPEN_RESERVATION_STATUSES = ('pending', 'confirmed')
LIMITED_RESERVATION_STATUSES = ('pending', 'confirmed', 'active', 'issued')
CSRF_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
LOW_STOCK_THRESHOLD = 3


def get_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


def is_valid_csrf_token(submitted_token):
    session_token = session.get('_csrf_token')
    return bool(
        submitted_token
        and session_token
        and secrets.compare_digest(session_token, submitted_token)
    )


@app.before_request
def csrf_protect():
    if request.method not in CSRF_METHODS:
        return None

    submitted_token = (
        request.form.get('csrf_token')
        or request.headers.get('X-CSRFToken')
        or request.headers.get('X-CSRF-Token')
    )
    if not is_valid_csrf_token(submitted_token):
        abort(403)
    return None


@app.context_processor
def inject_csrf_token():
    return {
        'csrf_token': get_csrf_token(),
        'product_total_quantity': product_total_quantity,
        'product_stock_state': product_stock_state,
        'product_stock_label': product_stock_label,
        'product_stock_badge': product_stock_badge,
        'LOW_STOCK_THRESHOLD': LOW_STOCK_THRESHOLD,
    }


def is_password_hash(value):
    value = value or ''
    return value.startswith(('scrypt:', 'pbkdf2:', 'argon2:', 'sha256$', 'sha1$'))


def allowed_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_product_image(file_storage):
    if not file_storage or not file_storage.filename:
        return ''

    if not allowed_image_file(file_storage.filename):
        raise ValueError('Поддерживаются только изображения JPG, JPEG, PNG, GIF или WEBP')

    filename = secure_filename(f"{datetime.now().timestamp()}_{file_storage.filename}")
    if not filename:
        raise ValueError('Некорректное имя файла изображения')

    os.makedirs(STATIC_UPLOADS_FOLDER, exist_ok=True)
    file_storage.save(os.path.join(STATIC_UPLOADS_FOLDER, filename))
    return f"uploads/{filename}"

# Модели базы данных
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='user')  # Изменено на String
    
    def set_password(self, password):
        self.password = generate_password_hash(password)
    
    def check_password(self, password):
        # Совместимость со старыми аккаунтами: если пароль когда-то был сохранен
        # открытым текстом, вход разрешается один раз, а login() ниже обновляет его до хеша.
        if is_password_hash(self.password):
            return check_password_hash(self.password, password)
        return self.password == password

class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    products = db.relationship('Product', backref='category', lazy=True)

class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    image = db.Column(db.String(255))
    price = db.Column(db.Float, nullable=False)  # Изменено на Float для простоты
    max_per_user = db.Column(db.Integer, nullable=False, default=5)
    is_active = db.Column(db.Boolean, default=True)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False, default=1)
    sizes = db.relationship('ProductSize', backref='product', lazy=True, cascade='all, delete-orphan')

class ProductSize(db.Model):
    __tablename__ = 'product_sizes'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    size = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

class Cart(db.Model):
    __tablename__ = 'cart'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    size = db.Column(db.String(20))
    quantity = db.Column(db.Integer, default=1)
    
    __table_args__ = (db.UniqueConstraint('user_id', 'product_id', 'size', name='unique_cart_item'),)

class Reservation(db.Model):
    __tablename__ = 'reservations'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id', ondelete='RESTRICT'), nullable=False)
    size = db.Column(db.String(50), nullable=False)
    reserved_quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='pending')
    manager_comment = db.Column(db.Text)
    not_issued_reason = db.Column(db.String(255))
    processed_at = db.Column(db.DateTime)
    issued_at = db.Column(db.DateTime)
    is_stock_written_off = db.Column(db.Boolean, default=False, nullable=False)
    reservation_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='reservations')
    product = db.relationship('Product', backref='reservations')


class AdminLog(db.Model):
    __tablename__ = 'admin_logs'
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(120), nullable=False)
    object_type = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    admin = db.relationship('User', backref='admin_logs')


class ReservationStatusHistory(db.Model):
    __tablename__ = 'reservation_status_history'
    id = db.Column(db.Integer, primary_key=True)
    reservation_id = db.Column(db.Integer, db.ForeignKey('reservations.id', ondelete='CASCADE'), nullable=False)
    old_status = db.Column(db.String(20))
    new_status = db.Column(db.String(20), nullable=False)
    comment = db.Column(db.Text)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    reservation = db.relationship('Reservation', backref=db.backref('status_history', lazy=True, cascade='all, delete-orphan'))


def reservation_status_label(status):
    return RESERVATION_STATUSES.get(status, RESERVATION_STATUSES['pending'])


def normalize_reservation_status(status):
    legacy_statuses = {
        'active': 'pending',
        'Ожидает подтверждения': 'pending',
        'Подтверждено': 'confirmed',
        'Выдан': 'issued',
        'Не выдан': 'not_issued',
        'Отменено': 'cancelled',
    }
    return legacy_statuses.get(status, status if status in RESERVATION_STATUSES else 'pending')


def normalize_not_issued_reason(reason):
    reason = (reason or '').strip()
    return reason if reason else 'Причина не указана'


def product_total_quantity(product):
    if not product or not product.sizes:
        return 0
    return sum(max(size.quantity or 0, 0) for size in product.sizes)


def product_stock_state(product):
    total = product_total_quantity(product)
    if total <= 0:
        return 'out'
    if total < LOW_STOCK_THRESHOLD:
        return 'low'
    return 'available'


def product_stock_label(product):
    labels = {
        'available': 'В наличии',
        'low': 'Заканчивается',
        'out': 'Нет в наличии',
    }
    return labels.get(product_stock_state(product), 'Нет в наличии')


def product_stock_badge(product):
    badges = {
        'available': 'stock-badge stock-badge-available',
        'low': 'stock-badge stock-badge-low',
        'out': 'stock-badge stock-badge-out',
    }
    return badges.get(product_stock_state(product), 'stock-badge stock-badge-out')


def validate_phone(phone):
    phone = (phone or '').strip()
    digits = re.sub(r'\D', '', phone)
    return bool(phone and 10 <= len(digits) <= 15 and re.fullmatch(r'[\d\s()+-]+', phone))


def log_admin_action(action, object_type, description='', admin_id=None):
    db.session.add(AdminLog(
        admin_id=admin_id if admin_id is not None else session.get('user_id'),
        action=action,
        object_type=object_type,
        description=description
    ))


def add_reservation_history(reservation, old_status, new_status, comment=''):
    db.session.add(ReservationStatusHistory(
        reservation_id=reservation.id,
        old_status=old_status,
        new_status=new_status,
        comment=comment
    ))


def get_low_stock_items(threshold=LOW_STOCK_THRESHOLD, limit=None):
    query = ProductSize.query.join(Product).join(Category).filter(
        Product.is_active.is_(True),
        ProductSize.quantity < threshold
    ).options(
        joinedload(ProductSize.product).joinedload(Product.category)
    ).order_by(ProductSize.quantity.asc(), Product.name.asc(), ProductSize.size.asc())

    if limit:
        query = query.limit(limit)

    return query.all()


def auto_cancel_old_pending_reservations():
    deadline = datetime.utcnow() - timedelta(days=3)
    old_reservations = Reservation.query.filter(
        Reservation.status == 'pending',
        Reservation.reservation_date < deadline
    ).all()

    if not old_reservations:
        return 0

    reason = 'Автоматическая отмена из-за истечения срока ожидания'
    now = datetime.utcnow()

    for reservation in old_reservations:
        old_status = reservation.status
        reservation.status = 'cancelled'
        reservation.processed_at = now
        current_comment = (reservation.manager_comment or '').strip()
        reservation.manager_comment = f'{current_comment}\n{reason}'.strip() if current_comment else reason
        add_reservation_history(reservation, old_status, 'cancelled', reason)
        log_admin_action(
            'Автоматическая отмена бронирования',
            f'Бронирование #{reservation.id}',
            reason,
            admin_id=None
        )

    db.session.commit()
    return len(old_reservations)


def validate_product_form(form):
    errors = []
    name = (form.get('name') or '').strip()
    description = (form.get('description') or '').strip()

    if not name:
        errors.append('Название товара не может быть пустым.')

    try:
        price = float((form.get('price') or '').replace(',', '.'))
    except (TypeError, ValueError):
        price = 0
    if price <= 0:
        errors.append('Цена должна быть больше 0.')

    category_id = form.get('category_id', type=int)
    if not category_id or not Category.query.get(category_id):
        errors.append('Выберите корректную категорию.')

    max_per_user = form.get('max_per_user', type=int) or 5
    if max_per_user < 1:
        errors.append('Лимит на пользователя должен быть больше 0.')
        max_per_user = 1

    raw_sizes = form.getlist('sizes[]')
    raw_quantities = form.getlist('quantities[]')
    sizes = []
    seen_sizes = set()

    for index, (size, qty) in enumerate(zip(raw_sizes, raw_quantities), start=1):
        size = (size or '').strip()
        qty_text = (qty or '').strip()

        if not size and not qty_text:
            continue
        if not size:
            errors.append(f'Размер в строке {index} не должен быть пустым.')
            continue

        try:
            quantity = int(qty_text)
        except (TypeError, ValueError):
            quantity = -1

        if quantity < 0:
            errors.append(f'Количество для размера "{size}" не может быть отрицательным.')
            continue

        size_key = size.lower()
        if size_key in seen_sizes:
            errors.append(f'Размер "{size}" указан несколько раз.')
            continue

        seen_sizes.add(size_key)
        sizes.append({'size': size, 'quantity': quantity})

    if not sizes:
        errors.append('Добавьте хотя бы один размер с корректным остатком.')

    return {
        'name': name,
        'description': description,
        'price': price,
        'category_id': category_id,
        'max_per_user': max_per_user,
        'sizes': sizes,
    }, errors


def get_admin_dashboard_data():
    low_stock_items = get_low_stock_items(limit=8)
    status_counts = {
        status: Reservation.query.filter_by(status=status).count()
        for status in RESERVATION_STATUSES
    }
    stats = {
        'total_products': Product.query.count(),
        'active_products': Product.query.filter_by(is_active=True).count(),
        'inactive_products': Product.query.filter_by(is_active=False).count(),
        'total_reservations': Reservation.query.count(),
        'pending_reservations': status_counts.get('pending', 0),
        'confirmed_reservations': status_counts.get('confirmed', 0),
        'issued_reservations': status_counts.get('issued', 0),
        'low_stock_products': db.session.query(ProductSize.id).join(Product).filter(
            Product.is_active.is_(True),
            ProductSize.quantity < LOW_STOCK_THRESHOLD
        ).count(),
    }
    return stats, low_stock_items


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin':
            flash('Доступ запрещен. Требуются права администратора.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def get_product_limit(product):
    return product.max_per_user if product and product.max_per_user else 5


def get_reserved_quantity_for_user(user_id, product_id):
    return db.session.query(
        db.func.coalesce(db.func.sum(Reservation.reserved_quantity), 0)
    ).filter(
        Reservation.user_id == user_id,
        Reservation.product_id == product_id,
        Reservation.status.in_(LIMITED_RESERVATION_STATUSES)
    ).scalar() or 0


def get_cart_quantity_for_user(user_id, product_id, exclude_cart_id=None):
    query = Cart.query.filter_by(user_id=user_id, product_id=product_id)
    if exclude_cart_id:
        query = query.filter(Cart.id != exclude_cart_id)
    return sum(item.quantity or 0 for item in query.all())


def check_product_user_limit(user_id, product, requested_quantity, include_cart=True, exclude_cart_id=None):
    limit = get_product_limit(product)
    already_reserved = get_reserved_quantity_for_user(user_id, product.id)
    cart_quantity = get_cart_quantity_for_user(user_id, product.id, exclude_cart_id) if include_cart else 0
    total_after_request = already_reserved + cart_quantity + requested_quantity

    if total_after_request > limit:
        return False, (
            f'Вы можете забронировать не более {limit} шт. данного товара. '
            f'Сейчас уже учтено: {already_reserved + cart_quantity} шт.'
        )

    return True, ''


def generate_reservation_insights(metrics, product_stats, reason_labels, reason_values):
    total = metrics['total']
    issued = metrics['issued']
    not_issued = metrics['not_issued']
    success_percent = metrics['success_percent']
    problems = []
    recommendations = []

    if not total:
        return {
            'summary': 'Данных для анализа пока недостаточно: бронирования еще не созданы.',
            'problems': ['Нет истории бронирований для оценки выдачи товаров.'],
            'recommendations': ['После появления первых бронирований анализ автоматически покажет слабые места ассортимента.']
        }

    if success_percent < 50:
        summary = (
            f'Процент успешной выдачи составляет {success_percent}%. '
            'Эффективность бронирований низкая, требуется проверка причин отказов и процесса выдачи.'
        )
        problems.append('Доля выданных товаров ниже 50%, заявки часто не доходят до фактической выдачи.')
    elif success_percent > 75:
        summary = (
            f'Процент успешной выдачи составляет {success_percent}%. '
            'Система бронирования работает стабильно, большинство заявок завершается выдачей товара.'
        )
    else:
        summary = (
            f'Процент успешной выдачи составляет {success_percent}%. '
            'Показатель находится на среднем уровне, есть потенциал для снижения количества невыдач.'
        )

    if reason_labels and reason_values:
        top_reason_index = max(range(len(reason_values)), key=reason_values.__getitem__)
        top_reason = reason_labels[top_reason_index]
        top_reason_count = reason_values[top_reason_index]
        summary += f' Основная причина невыдачи — «{top_reason}» ({top_reason_count}).'
        problems.append(f'Чаще всего товары не выдаются по причине: «{top_reason}».')

        reason_recommendations = {
            'Не подошел размер': 'Добавить таблицу размеров, описание посадки и рекомендации по выбору размера.',
            'Не понравился дизайн': 'Улучшить фотографии товара, добавить крупные планы и подробнее описать внешний вид.',
            'Не устроила цена': 'Проверить конкурентность цены, добавить акции или выделить преимущества товара.',
            'Клиент не пришел': 'Добавить подтверждение бронирования перед выдачей и напоминание клиенту.',
            'Ошибка бронирования': 'Проверить процесс оформления брони и понятность выбора размера/количества.',
        }
        recommendations.append(reason_recommendations.get(
            top_reason,
            'Проанализировать комментарии менеджера и уточнить карточки товаров, по которым чаще возникают отказы.'
        ))
    elif not_issued:
        problems.append('Есть невыданные бронирования, но причины отказа заполнены не у всех заявок.')
        recommendations.append('Обязать менеджера указывать причину невыдачи для каждой закрытой заявки.')

    weak_products = sorted(
        [
            item for item in product_stats
            if item['total'] >= 2 and item['success_percent'] < 50
        ],
        key=lambda item: item['success_percent']
    )[:3]
    high_not_issued_products = sorted(
        [
            item for item in product_stats
            if item['not_issued'] > 0
        ],
        key=lambda item: item['not_issued'],
        reverse=True
    )[:3]

    if weak_products:
        product_names = ', '.join(item['name'] for item in weak_products)
        problems.append(f'Низкий процент выдачи у товаров: {product_names}.')
        recommendations.append('Проверить карточки этих товаров: размеры, описание, фото, цену и наличие на складе.')
    if high_not_issued_products:
        product_names = ', '.join(item['name'] for item in high_not_issued_products)
        problems.append(f'Больше всего невыдач зафиксировано у товаров: {product_names}.')
        recommendations.append('Начать разбор отказов с товаров, по которым накопилось больше всего невыдач.')

    if not recommendations:
        recommendations.append('Продолжать отслеживать причины невыдач и регулярно обновлять карточки товаров по результатам анализа.')

    summary += f' Всего бронирований: {total}, выдано: {issued}, не выдано: {not_issued}.'

    return {
        'summary': summary,
        'problems': problems or ['Критических проблем по текущим данным не выявлено.'],
        'recommendations': recommendations,
    }


def excel_column_name(index):
    name = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def xlsx_response(data, filename, sheet_name='Лист1', headers=None):
    headers = headers or (list(data[0].keys()) if data else [])
    rows = [headers]
    rows.extend([[row.get(header, '') for header in headers] for row in data])

    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            cell_ref = f'{excel_column_name(col_index)}{row_index}'
            value = '' if value is None else str(value)
            cells.append(
                f'<c r="{cell_ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    worksheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    {''.join(sheet_rows)}
  </sheetData>
</worksheet>'''

    workbook_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>'''

    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>''')
        archive.writestr('_rels/.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')
        archive.writestr('xl/workbook.xml', workbook_xml)
        archive.writestr('xl/_rels/workbook.xml.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>''')
        archive.writestr('xl/worksheets/sheet1.xml', worksheet_xml)

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment;filename={filename}'}
    )

def export_reservations(reservations, export_format):
    data = []
    for r in reservations:
        data.append({
            'ID': r.id,
            'Пользователь': r.user.username if r.user else 'Удалён',
            'Телефон': r.user.phone if r.user else '',
            'Товар': r.product.name if r.product else 'Удалён',
            'Категория': r.product.category.name if r.product and r.product.category else '',
            'Размер': r.size,
            'Количество': r.reserved_quantity,
            'Статус': RESERVATION_STATUSES.get(r.status, r.status),
            'Дата брони': r.reservation_date.strftime('%Y-%m-%d %H:%M'),
            'Причина невыдачи': r.not_issued_reason or '',
            'Комментарий менеджера': r.manager_comment or ''
        })

    if export_format == 'csv':
        output = StringIO()
        headers = list(data[0].keys()) if data else [
            'ID', 'Пользователь', 'Телефон', 'Товар', 'Категория', 'Размер',
            'Количество', 'Статус', 'Дата брони', 'Причина невыдачи', 'Комментарий менеджера'
        ]
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        writer.writerows(data)
        return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment;filename=analytics.csv'})

    elif export_format == 'excel':
        headers = list(data[0].keys()) if data else [
            'ID', 'Пользователь', 'Телефон', 'Товар', 'Категория', 'Размер',
            'Количество', 'Статус', 'Дата брони', 'Причина невыдачи', 'Комментарий менеджера'
        ]
        return xlsx_response(data, 'analytics.xlsx', sheet_name='Аналитика', headers=headers)


def export_inventory_excel():
    products = Product.query.options(
        joinedload(Product.category),
        joinedload(Product.sizes)
    ).order_by(Product.name.asc()).all()

    data = []
    for product in products:
        sizes = product.sizes or [None]
        for size in sizes:
            data.append({
                'ID товара': product.id,
                'Название': product.name,
                'Категория': product.category.name if product.category else 'Без категории',
                'Размер': size.size if size else '—',
                'Остаток': size.quantity if size else 0,
                'Цена': product.price,
                'Статус активности': 'Активен' if product.is_active else 'Неактивен/удален',
            })

    headers = ['ID товара', 'Название', 'Категория', 'Размер', 'Остаток', 'Цена', 'Статус активности']
    return xlsx_response(data, 'inventory.xlsx', sheet_name='Остатки', headers=headers)

def migrate_sqlite_reservations():
    """Adds Reservation columns that db.create_all() cannot add to an existing SQLite DB."""
    with db.engine.begin() as connection:
        product_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(products)")).fetchall()
        }
        if 'max_per_user' not in product_columns:
            connection.execute(text("ALTER TABLE products ADD COLUMN max_per_user INTEGER DEFAULT 5"))
            product_columns.add('max_per_user')
        if 'max_per_user' in product_columns:
            connection.execute(text("UPDATE products SET max_per_user = 5 WHERE max_per_user IS NULL"))

        columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(reservations)")).fetchall()
        }
        first_reservation_migration = 'is_stock_written_off' not in columns

        migrations = {
            'status': "ALTER TABLE reservations ADD COLUMN status VARCHAR(20) DEFAULT 'pending'",
            'manager_comment': "ALTER TABLE reservations ADD COLUMN manager_comment TEXT",
            'not_issued_reason': "ALTER TABLE reservations ADD COLUMN not_issued_reason VARCHAR(255)",
            'processed_at': "ALTER TABLE reservations ADD COLUMN processed_at DATETIME",
            'issued_at': "ALTER TABLE reservations ADD COLUMN issued_at DATETIME",
            'is_stock_written_off': "ALTER TABLE reservations ADD COLUMN is_stock_written_off BOOLEAN DEFAULT 0",
        }

        for column_name, statement in migrations.items():
            if column_name not in columns:
                connection.execute(text(statement))

        if first_reservation_migration:
            # Legacy active reservations were created with stock already deducted.
            # Return that stock once, then move them into the new pending workflow.
            connection.execute(text("""
                UPDATE product_sizes
                SET quantity = quantity + COALESCE((
                    SELECT SUM(reserved_quantity)
                    FROM reservations
                    WHERE reservations.product_id = product_sizes.product_id
                      AND reservations.size = product_sizes.size
                      AND reservations.status = 'active'
                ), 0)
                WHERE EXISTS (
                    SELECT 1
                    FROM reservations
                    WHERE reservations.product_id = product_sizes.product_id
                      AND reservations.size = product_sizes.size
                      AND reservations.status = 'active'
                )
            """))
            connection.execute(text("""
                UPDATE reservations
                SET status = 'pending',
                    is_stock_written_off = 0
                WHERE status = 'active'
            """))
            connection.execute(text("""
                UPDATE reservations
                SET status = 'issued',
                    is_stock_written_off = 1,
                    processed_at = COALESCE(processed_at, reservation_date),
                    issued_at = COALESCE(issued_at, reservation_date)
                WHERE status = 'confirmed'
            """))
        connection.execute(text("""
            UPDATE reservations
            SET status = 'pending'
            WHERE status IS NULL OR status = ''
        """))
        connection.execute(text("UPDATE reservations SET status = 'pending' WHERE status IN ('active', 'Ожидает подтверждения')"))
        connection.execute(text("UPDATE reservations SET status = 'confirmed' WHERE status = 'Подтверждено'"))
        connection.execute(text("UPDATE reservations SET status = 'issued' WHERE status = 'Выдан'"))
        connection.execute(text("UPDATE reservations SET status = 'not_issued' WHERE status = 'Не выдан'"))
        connection.execute(text("UPDATE reservations SET status = 'cancelled' WHERE status = 'Отменено'"))

# Главная страница
@app.route('/')
def index():
    search_query = (request.args.get('q') or '').strip()
    selected_category = request.args.get('category', type=int)
    sort_order = request.args.get('sort', '')
    in_stock_only = request.args.get('in_stock') == '1'

    product_query = Product.query.options(
        joinedload(Product.category),
        joinedload(Product.sizes)
    ).filter_by(is_active=True)

    if search_query:
        product_query = product_query.filter(Product.name.ilike(f'%{search_query}%'))
    if selected_category:
        product_query = product_query.filter(Product.category_id == selected_category)
    if sort_order == 'price_asc':
        product_query = product_query.order_by(Product.price.asc(), Product.name.asc())
    elif sort_order == 'price_desc':
        product_query = product_query.order_by(Product.price.desc(), Product.name.asc())
    else:
        product_query = product_query.order_by(Product.category_id.asc(), Product.name.asc())

    products = product_query.all()
    if in_stock_only:
        products = [product for product in products if product_total_quantity(product) > 0]

    # Группируем по категориям
    products_by_cat = {}
    for product in products:
        cat_id = product.category_id
        if cat_id not in products_by_cat:
            # Название категории берём из загруженного объекта category
            products_by_cat[cat_id] = {
                'name': product.category.name,
                'products': []
            }
        products_by_cat[cat_id]['products'].append(product)

    return render_template('index.html', 
                         products_by_cat=products_by_cat,
                         categories=Category.query.order_by(Category.name.asc()).all(),
                         filters={
                             'q': search_query,
                             'category': selected_category or '',
                             'sort': sort_order,
                             'in_stock': in_stock_only,
                         },
                         session=session)

# Добавление в корзину (AJAX)
@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    if 'user_id' not in session:
        return "Ошибка: нужно войти в систему", 401
    
    product_id = request.form.get('product_id', type=int)
    size = request.form.get('size')
    
    if not product_id or not size:
        return "Ошибка: товар или размер не указан", 400

    product = Product.query.get(product_id)
    if not product or not product.is_active:
        return "Ошибка: товар не найден или недоступен", 404

    size_data = ProductSize.query.filter_by(product_id=product_id, size=size).first()
    if not size_data or size_data.quantity <= 0:
        return "Ошибка: выбранного размера нет в наличии", 400
    
    # Проверяем, есть ли уже в корзине
    cart_item = Cart.query.filter_by(
        user_id=session['user_id'],
        product_id=product_id,
        size=size
    ).first()
    
    if cart_item:
        return "Товар с этим размером уже в корзине", 400

    limit_ok, limit_message = check_product_user_limit(
        session['user_id'],
        product,
        requested_quantity=1,
        include_cart=True
    )
    if not limit_ok:
        return limit_message, 400
    
    # Добавляем в корзину
    cart_item = Cart(
        user_id=session['user_id'],
        product_id=product_id,
        size=size,
        quantity=1
    )
    db.session.add(cart_item)
    db.session.commit()
    
    return "Товар добавлен в корзину", 200

# Корзина
@app.route('/cart')
def cart():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    cart_items = Cart.query.filter_by(user_id=session['user_id']).all()
    items_with_details = []
    total_price = 0   # ← добавляем переменную
    
    for item in cart_items:
        product = Product.query.get(item.product_id)
        if product:
            item_total = product.price * item.quantity
            total_price += item_total
            items_with_details.append({
                'id': item.id,
                'product_id': item.product_id,
                'name': product.name,
                'price': product.price,
                'image': product.image,
                'size': item.size,
                'quantity': item.quantity,
                'total': item_total      
            })
    
    return render_template('cart.html', cart_items=items_with_details, total_price=total_price)

# Действие с корзиной (удаление)
@app.route('/cart_action', methods=['POST'])
def cart_action():
    if 'user_id' not in session:
        return "Авторизуйтесь", 401
    
    action = request.form.get('action')
    
    if action == 'remove':
        product_id = request.form.get('id', type=int)
        size = request.form.get('size')
        
        cart_item = Cart.query.filter_by(
            user_id=session['user_id'],
            product_id=product_id,
            size=size
        ).first()
        
        if cart_item:
            db.session.delete(cart_item)
            db.session.commit()
            return "OK", 200
    
    return "Ошибка", 400

# Бронирование всей корзины
@app.route('/reserve_cart', methods=['POST'])
def reserve_cart():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    cart_items = Cart.query.filter_by(user_id=session['user_id']).all()
    if not cart_items:
        flash('Корзина пуста', 'warning')
        return redirect(url_for('cart'))
    
    created_count = 0
    skipped_items = []
    cart_totals_by_product = defaultdict(int)
    products_by_id = {}

    for item in cart_items:
        cart_totals_by_product[item.product_id] += item.quantity or 0
        if item.product_id not in products_by_id:
            products_by_id[item.product_id] = Product.query.get(item.product_id)

    blocked_products = set()
    for product_id, requested_total in cart_totals_by_product.items():
        product = products_by_id.get(product_id)
        if not product or not product.is_active:
            blocked_products.add(product_id)
            skipped_items.append(f'товар #{product_id}')
            continue

        limit_ok, limit_message = check_product_user_limit(
            session['user_id'],
            product,
            requested_quantity=requested_total,
            include_cart=False
        )
        if not limit_ok:
            blocked_products.add(product_id)
            skipped_items.append(f'{product.name}: {limit_message}')

    for item in cart_items:
        product = products_by_id.get(item.product_id)
        size_data = ProductSize.query.filter_by(
            product_id=item.product_id,
            size=item.size
        ).first()
        
        if item.product_id in blocked_products:
            continue

        if product and product.is_active and size_data and size_data.quantity >= item.quantity and item.quantity > 0:
            reservation = Reservation(
                user_id=session['user_id'],
                product_id=item.product_id,
                size=item.size,
                reserved_quantity=item.quantity,
                status='pending'
            )
            db.session.add(reservation)
            db.session.flush()
            add_reservation_history(reservation, None, 'pending', 'Бронирование создано пользователем')
            db.session.delete(item)
            created_count += 1
        else:
            item_name = product.name if product else f'товар #{item.product_id}'
            skipped_items.append(f'{item_name} ({item.size})')
    
    db.session.commit()
    
    if created_count:
        flash('Заявки на бронирование отправлены менеджеру. Остатки будут списаны только после выдачи.', 'success')
    if skipped_items:
        flash('Не удалось забронировать: ' + ', '.join(skipped_items), 'warning')
    if not created_count:
        flash('Ни одна позиция не была забронирована', 'danger')

    return redirect(url_for('cart'))

# Мои бронирования
@app.route('/my_reservations', methods=['GET', 'POST'])
def my_reservations():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Отмена брони
    if request.method == 'POST':
        cancel_id = request.form.get('cancel_id', type=int)
        reservation = Reservation.query.get(cancel_id)
        
        if reservation and reservation.user_id == session['user_id'] and normalize_reservation_status(reservation.status) in OPEN_RESERVATION_STATUSES:
            old_status = reservation.status
            reservation.status = 'cancelled'
            reservation.processed_at = datetime.utcnow()
            add_reservation_history(reservation, old_status, 'cancelled', 'Бронирование отменено пользователем')
            db.session.commit()
            flash('Бронь отменена', 'success')
            return redirect(url_for('my_reservations'))
        elif reservation and reservation.user_id == session['user_id']:
            flash('Эту бронь уже нельзя отменить', 'warning')
            return redirect(url_for('my_reservations'))
    
    # Получаем все брони пользователя
    reservations = Reservation.query.filter_by(user_id=session['user_id']).order_by(Reservation.reservation_date.desc()).all()
    
    return render_template(
        'my_reservations.html',
        reservations=reservations,
        status_labels=RESERVATION_STATUSES,
        status_badges=STATUS_BADGES,
        open_statuses=OPEN_RESERVATION_STATUSES
    )

# Вход
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''

        if not username or not password:
            flash('Введите логин и пароль', 'danger')
            return render_template('login.html')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if not is_password_hash(user.password):
                user.set_password(password)
                db.session.commit()
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role
            session['name'] = user.name
            return redirect(url_for('index'))
        else:
            flash('Неверный логин или пароль', 'danger')
    
    return render_template('login.html')

# Регистрация
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''   # <-- добавить
        name = (request.form.get('name') or '').strip()
        phone = (request.form.get('phone') or '').strip()
        
        if not username or not password or not name or not phone:
            flash('Заполните все обязательные поля', 'danger')
        elif password != confirm_password:   # <-- добавить проверку
            flash('Пароли не совпадают', 'danger')
        elif len(username) < 3 or len(password) < 4:
            flash('Имя пользователя должно быть не короче 3 символов, пароль — не короче 4 символов', 'danger')
        elif not validate_phone(phone):
            flash('Укажите корректный телефон: 10-15 цифр, допускаются +, пробелы, скобки и дефисы', 'danger')
        elif User.query.filter_by(username=username).first():
            flash('Ошибка: логин уже занят', 'danger')
        else:
            user = User(
                username=username,
                name=name,
                phone=phone,
                role='user'
            )
            user.set_password(password)
            
            db.session.add(user)
            db.session.commit()
            
            flash('Регистрация успешна!', 'success')
            return redirect(url_for('login'))
    
    return render_template('register.html')


@app.route('/help')
def help():
    return render_template('help.html')

# Выход
@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('login'))

# Бронирование товара
@app.route('/reserve', methods=['GET', 'POST'])
def reserve():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    product_id = request.args.get('id', type=int)
    if not product_id:
        flash('Некорректный товар', 'danger')
        return redirect(url_for('index'))
    
    product = Product.query.get(product_id)
    if not product or not product.is_active:
        flash('Товар не найден или недоступен', 'danger')
        return redirect(url_for('index'))

    if product_total_quantity(product) <= 0:
        flash('Товара нет в наличии, бронирование недоступно', 'warning')
        return redirect(url_for('product_detail', product_id=product_id))
    
    if request.method == 'POST':
        size = (request.form.get('size') or '').strip()
        quantity = request.form.get('quantity', type=int)
        
        if not size:
            flash('Выберите размер', 'danger')
            return redirect(url_for('reserve', id=product_id))
        
        if not quantity or quantity <= 0:
            flash('Введите корректное количество', 'danger')
            return redirect(url_for('reserve', id=product_id))
        
        # Проверяем наличие размера
        size_data = ProductSize.query.filter_by(
            product_id=product_id,
            size=size
        ).first()
        
        if not size_data or size_data.quantity < quantity:
            flash('Недостаточно товара на складе', 'danger')
            return redirect(url_for('reserve', id=product_id))

        limit_ok, limit_message = check_product_user_limit(
            session['user_id'],
            product,
            requested_quantity=quantity,
            include_cart=True
        )
        if not limit_ok:
            flash(limit_message, 'danger')
            return redirect(url_for('reserve', id=product_id))
        
        # Создаем бронь
        reservation = Reservation(
            user_id=session['user_id'],
            product_id=product_id,
            size=size,
            reserved_quantity=quantity,
            status='pending'
        )
        db.session.add(reservation)
        db.session.flush()
        add_reservation_history(reservation, None, 'pending', 'Бронирование создано пользователем')
        db.session.commit()
        
        flash('Заявка на бронирование отправлена менеджеру. Остаток будет списан только после выдачи.', 'success')
        return redirect(url_for('my_reservations'))
    
    # Для GET запроса - показываем форму
    sizes = ProductSize.query.filter_by(product_id=product_id).all()
    return render_template('reserve.html', product=product, sizes=sizes)


# Админ: панель управления
@app.route('/admin')
@admin_required
def admin_dashboard():
    auto_cancelled = auto_cancel_old_pending_reservations()
    if auto_cancelled:
        flash(f'Автоматически отменено просроченных бронирований: {auto_cancelled}', 'info')
    stats, low_stock_items = get_admin_dashboard_data()
    return render_template(
        'admin/dashboard.html',
        stats=stats,
        low_stock_items=low_stock_items
    )


# Админ: добавление товара
@app.route('/admin/add_product', methods=['GET', 'POST'])
@admin_required
def add_product():
    
    categories = Category.query.all()
    
    if request.method == 'POST':
        product_data, errors = validate_product_form(request.form)
        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('admin/add_product.html', categories=categories)
        
        image_path = ""
        if 'image' in request.files and request.files['image'].filename:
            try:
                image_path = save_product_image(request.files['image'])
            except ValueError as error:
                flash(str(error), 'danger')
                return redirect(url_for('add_product'))
        
        # Создаем товар
        product = Product(
            name=product_data['name'],
            price=product_data['price'],
            description=product_data['description'],
            category_id=product_data['category_id'],
            max_per_user=product_data['max_per_user'],
            image=image_path,
            is_active=True
        )
        db.session.add(product)
        db.session.flush()
        
        # Добавляем размеры
        for item in product_data['sizes']:
            product_size = ProductSize(
                product_id=product.id,
                size=item['size'],
                quantity=item['quantity']
            )
            db.session.add(product_size)
        log_admin_action(
            'Добавление товара',
            f'Товар #{product.id}',
            f'Добавлен товар "{product.name}"'
        )
        
        db.session.commit()
        flash('Товар добавлен', 'success')
        return redirect(url_for('add_product'))
    
    return render_template('admin/add_product.html', categories=categories)

# Админ: удаление/редактирование товаров
@app.route('/admin/delete_product', methods=['GET', 'POST'])
@admin_required
def delete_product():
    
    # Мягкое удаление
    if request.method == 'POST':
        delete_id = request.form.get('delete_id', type=int)
        # Проверяем активные брони
        active_reservations = Reservation.query.filter(
            Reservation.product_id == delete_id,
            Reservation.status.in_(OPEN_RESERVATION_STATUSES)
        ).count()
        
        if active_reservations > 0:
            flash('Невозможно удалить товар — есть активные бронирования!', 'danger')
        else:
            product = Product.query.get(delete_id)
            if product:
                product.is_active = False
                log_admin_action(
                    'Мягкое удаление товара',
                    f'Товар #{product.id}',
                    f'Товар "{product.name}" скрыт из каталога'
                )
                db.session.commit()
                flash('Товар удалён', 'success')
        return redirect(url_for('delete_product'))
    
    # Пагинация
    page = request.args.get('page', 1, type=int)
    per_page = 20  # количество товаров на странице
    pagination = Product.query.options(joinedload(Product.category)).paginate(
    page=page, per_page=per_page, error_out=False
)
    products = pagination.items
    return render_template('admin/delete_product.html', products=products, pagination=pagination)

@app.route('/admin/restore_product', methods=['POST'])
@admin_required
def restore_product():
    
    product_id = request.form.get('restore_id', type=int)
    if product_id:
        product = Product.query.get(product_id)
        if product and not product.is_active:
            product.is_active = True
            log_admin_action(
                'Восстановление товара',
                f'Товар #{product.id}',
                f'Товар "{product.name}" восстановлен в каталоге'
            )
            db.session.commit()
            flash(f'Товар "{product.name}" восстановлен', 'success')
        else:
            flash('Товар не найден или уже активен', 'warning')
    return redirect(url_for('delete_product'))

# Админ: редактирование товара
@app.route('/admin/edit_product', methods=['GET', 'POST'])
@admin_required
def edit_product():
    # Обработка POST запроса (сохранение)
    if request.method == 'POST':
        product_id = request.form.get('id', type=int)
        product = Product.query.get(product_id)
        
        if product:
            product_data, errors = validate_product_form(request.form)
            if errors:
                for error in errors:
                    flash(error, 'danger')
                categories = Category.query.all()
                sizes = ProductSize.query.filter_by(product_id=product_id).all()
                return render_template(
                    'admin/edit_product.html',
                    product=product,
                    categories=categories,
                    sizes=sizes
                )

            product.name = product_data['name']
            product.category_id = product_data['category_id']
            product.description = product_data['description']
            product.price = product_data['price']
            product.max_per_user = product_data['max_per_user']

            if 'image' in request.files and request.files['image'].filename:
                try:
                    product.image = save_product_image(request.files['image'])
                except ValueError as error:
                    flash(str(error), 'danger')
                    return redirect(url_for('edit_product', edit=product_id))
            
            # Удаляем старые размеры
            ProductSize.query.filter_by(product_id=product_id).delete()
            
            # Добавляем новые размеры
            for item in product_data['sizes']:
                product_size = ProductSize(
                    product_id=product_id,
                    size=item['size'],
                    quantity=item['quantity']
                )
                db.session.add(product_size)
            log_admin_action(
                'Редактирование товара',
                f'Товар #{product.id}',
                f'Обновлены данные товара "{product.name}"'
            )
            
            db.session.commit()
            flash('Товар успешно обновлен!', 'success')
            return redirect(url_for('delete_product'))
        flash('Товар не найден', 'danger')
    
    # GET запрос - показываем форму
    edit_id = request.args.get('edit', type=int)
    if edit_id:
        product = Product.query.get(edit_id)
        if product:
            categories = Category.query.all()
            sizes = ProductSize.query.filter_by(product_id=edit_id).all()
            return render_template('admin/edit_product.html', 
                                 product=product, 
                                 categories=categories, 
                                 sizes=sizes)
    
    return redirect(url_for('delete_product'))

# Админ: все бронирования
@app.route('/admin/reservations', methods=['GET', 'POST'])
@admin_required
def admin_reservations():
    auto_cancelled = auto_cancel_old_pending_reservations()
    if auto_cancelled:
        flash(f'Автоматически отменено просроченных бронирований: {auto_cancelled}', 'info')
    
    if request.method == 'POST':
        reservation_ids = request.form.getlist('reservation_ids')
        now = datetime.utcnow()
        updated_count = 0

        for reservation_id in reservation_ids:
            try:
                reservation_id = int(reservation_id)
            except (TypeError, ValueError):
                continue

            reservation = Reservation.query.get(reservation_id)
            if not reservation:
                continue

            new_status = normalize_reservation_status(request.form.get(f'status_{reservation_id}', reservation.status))
            if new_status not in RESERVATION_STATUSES:
                flash(f'Некорректный статус для бронирования #{reservation_id}', 'danger')
                return redirect(url_for('admin_reservations'))
            manager_comment = request.form.get(f'manager_comment_{reservation_id}', '').strip()
            not_issued_reason = request.form.get(f'not_issued_reason_{reservation_id}', '').strip()

            # Если статус не изменился и комментарии не менялись - пропускаем
            if (reservation.status == new_status and 
                (reservation.manager_comment or '') == manager_comment and
                (reservation.not_issued_reason or '') == not_issued_reason):
                continue

            old_status = reservation.status

            # Если статус меняется на "Не выдан" - проверяем причину
            if new_status == 'not_issued' and not not_issued_reason:
                flash(f'Для бронирования #{reservation_id} со статусом "Не выдан" обязательно укажите причину', 'danger')
                return redirect(url_for('admin_reservations'))

            # Проверка: можно ли выдать? Выдать можно из статусов "pending" или "confirmed"
            if new_status == 'issued' and old_status not in ['pending', 'confirmed']:
                flash(f'Бронирование #{reservation_id} можно выдать только из статуса "Ожидает подтверждения" или "Подтверждено"', 'danger')
                return redirect(url_for('admin_reservations'))

            # Получаем размер для списания/возврата
            size_data = ProductSize.query.filter_by(
                product_id=reservation.product_id,
                size=reservation.size
            ).first()

            # Если статус меняется на "Выдан" и ещё не списан - списываем остаток
            if new_status == 'issued' and not reservation.is_stock_written_off:
                if size_data and size_data.quantity >= reservation.reserved_quantity:
                    size_data.quantity -= reservation.reserved_quantity
                    reservation.is_stock_written_off = True
                    reservation.issued_at = now
                else:
                    flash(f'Недостаточно товара на складе для выдачи бронирования #{reservation_id}', 'danger')
                    return redirect(url_for('admin_reservations'))

            # Если статус меняется с "Выдан" на что-то другое и был списан - возвращаем остаток
            if old_status == 'issued' and new_status != 'issued' and reservation.is_stock_written_off:
                if size_data:
                    size_data.quantity += reservation.reserved_quantity
                    reservation.is_stock_written_off = False
                    reservation.issued_at = None

            # Обновляем статус и комментарии
            reservation.status = new_status
            reservation.manager_comment = manager_comment or None
            reservation.not_issued_reason = not_issued_reason if new_status == 'not_issued' else None
            reservation.processed_at = now
            if old_status != new_status:
                history_comment = manager_comment or not_issued_reason or 'Статус изменен администратором'
                add_reservation_history(reservation, old_status, new_status, history_comment)
                action = 'Подтверждение бронирования' if new_status == 'confirmed' else 'Изменение статуса бронирования'
                log_admin_action(
                    action,
                    f'Бронирование #{reservation.id}',
                    f'{reservation_status_label(old_status)} → {reservation_status_label(new_status)}'
                )
            updated_count += 1

        db.session.commit()

        if updated_count:
            flash(f'Изменения сохранены. Обновлено бронирований: {updated_count}', 'success')
        else:
            flash('Изменений для сохранения не найдено', 'info')

        return redirect(url_for('admin_reservations'))

    # GET запрос - показываем таблицу
    status_filter = request.args.get('status', '')
    product_filter = request.args.get('product_id', type=int)
    reason_filter = request.args.get('reason', '')

    query = Reservation.query.options(
        joinedload(Reservation.user),
        joinedload(Reservation.product).joinedload(Product.category)
    )
    if status_filter:
        query = query.filter(Reservation.status == status_filter)
    if product_filter:
        query = query.filter(Reservation.product_id == product_filter)
    if reason_filter:
        query = query.filter(Reservation.not_issued_reason == reason_filter)

    # Пагинация
    page = request.args.get('page', 1, type=int)
    per_page = 30  # количество броней на странице
    pagination = query.order_by(Reservation.reservation_date.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    reservations = pagination.items
    products = Product.query.order_by(Product.name.asc()).all()
    
    return render_template(
        'admin/reservations.html',
        reservations=reservations,
        pagination=pagination,  # передаём объект пагинации в шаблон
        products=products,
        status_labels=RESERVATION_STATUSES,
        status_badges=STATUS_BADGES,
        not_issued_reasons=NOT_ISSUED_REASONS,
        filters={
            'status': status_filter,
            'product_id': product_filter or '',
            'reason': reason_filter,
        }
    )

# Админ: подтверждение бронирования
@app.route('/admin/confirm_reservation', methods=['POST'])
@admin_required
def admin_confirm_reservation():

    reservation_id = request.form.get('id', type=int)
    if not reservation_id:
        flash('Некорректный запрос', 'danger')
        return redirect(url_for('admin_reservations'))
    
    reservation = Reservation.query.get(reservation_id)
    
    if not reservation:
        flash('Бронирование не найдено', 'danger')
        return redirect(url_for('admin_reservations'))
    
    if reservation.status != 'pending':
        flash(f'Бронирование #{reservation_id} уже обработано', 'warning')
        return redirect(url_for('admin_reservations'))
    
    # Проверяем остаток
    size_data = ProductSize.query.filter_by(
        product_id=reservation.product_id,
        size=reservation.size
    ).first()
    
    if not size_data or size_data.quantity < reservation.reserved_quantity:
        flash(f'Недостаточно товара на складе для подтверждения бронирования #{reservation_id}', 'danger')
        return redirect(url_for('admin_reservations'))
    
    # Подтверждаем бронь
    old_status = reservation.status
    reservation.status = 'confirmed'
    reservation.processed_at = datetime.utcnow()
    add_reservation_history(reservation, old_status, 'confirmed', 'Бронирование подтверждено администратором')
    log_admin_action(
        'Подтверждение бронирования',
        f'Бронирование #{reservation.id}',
        f'Бронирование #{reservation.id} подтверждено'
    )
    db.session.commit()
    
    flash(f'Бронирование #{reservation_id} подтверждено! Теперь его можно выдать.', 'success')
    return redirect(url_for('admin_reservations'))
    
# Админ: аналитика продаж и бронирований
@app.route('/admin/analytics')
@admin_required
def admin_analytics():
    auto_cancelled = auto_cancel_old_pending_reservations()
    if auto_cancelled:
        flash(f'Автоматически отменено просроченных бронирований: {auto_cancelled}', 'info')

    selected_product_id = request.args.get('product_id', type=int)
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    export = request.args.get('export', '')

    # Базовый запрос с подгрузкой связанных данных
    query = Reservation.query.options(
        joinedload(Reservation.product).joinedload(Product.category),
        joinedload(Reservation.user)
    )

    if selected_product_id:
        query = query.filter(Reservation.product_id == selected_product_id)

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            query = query.filter(Reservation.reservation_date >= start_date)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            end_date = end_date.replace(hour=23, minute=59, second=59)
            query = query.filter(Reservation.reservation_date <= end_date)
        except ValueError:
            pass

    reservations = query.order_by(Reservation.reservation_date.desc()).all()
    products = Product.query.order_by(Product.name.asc()).all()

    if export in ('csv', 'excel'):
        return export_reservations(reservations, export)

    total_count = len(reservations)
    status_counter = Counter(normalize_reservation_status(r.status) for r in reservations)
    issued_count = status_counter.get('issued', 0)
    not_issued_count = status_counter.get('not_issued', 0)
    pending_count = status_counter.get('pending', 0)
    success_percent = round((issued_count / total_count) * 100, 1) if total_count else 0

    product_stats_map = {}
    reason_map = defaultdict(Counter)

    for reservation in reservations:
        product = reservation.product
        pid = reservation.product_id
        if pid not in product_stats_map:
            product_stats_map[pid] = {
                'product_id': pid,
                'name': product.name if product else f'Товар #{pid}',
                'category': product.category.name if product and product.category else 'Без категории',
                'total': 0, 'issued': 0, 'not_issued': 0,
                'success_percent': 0, 'top_reason': '—',
            }
        stats = product_stats_map[pid]
        stats['total'] += 1
        norm_status = normalize_reservation_status(reservation.status)
        if norm_status == 'issued':
            stats['issued'] += 1
        elif norm_status == 'not_issued':
            stats['not_issued'] += 1
            reason = normalize_not_issued_reason(reservation.not_issued_reason)
            reason_map[pid][reason] += 1

    product_stats = []
    for pid, stats in product_stats_map.items():
        stats['success_percent'] = round((stats['issued'] / stats['total']) * 100, 1) if stats['total'] else 0
        if reason_map[pid]:
            stats['top_reason'] = reason_map[pid].most_common(1)[0][0]
        product_stats.append(stats)
    product_stats.sort(key=lambda x: x['total'], reverse=True)

    reason_counter = Counter()
    for reservation in reservations:
        if normalize_reservation_status(reservation.status) == 'not_issued':
            reason = normalize_not_issued_reason(reservation.not_issued_reason)
            reason_counter[reason] += 1

    reason_items = reason_counter.most_common()
    reason_labels = [label for label, _ in reason_items]
    reason_values = [value for _, value in reason_items]

    top_chart_products = product_stats[:8]
    status_values = [status_counter.get(k, 0) for k in RESERVATION_STATUSES]
    product_issued_values = [item['issued'] for item in top_chart_products]
    product_not_issued_values = [item['not_issued'] for item in top_chart_products]
    chart_data = {
        'statuses': {
            'keys': list(RESERVATION_STATUSES.keys()),
            'labels': [RESERVATION_STATUSES[k] for k in RESERVATION_STATUSES],
            'values': status_values,
            'success_percent': success_percent,
        },
        'products': {
            'labels': [item['name'] for item in top_chart_products],
            'issued': product_issued_values,
            'not_issued': product_not_issued_values,
        },
        'reasons': {
            'labels': reason_labels,
            'values': reason_values,
        },
    }
    metrics = {
        'total': total_count,
        'issued': issued_count,
        'not_issued': not_issued_count,
        'pending': pending_count,
        'success_percent': success_percent,
    }
    ai_analysis = generate_reservation_insights(metrics, product_stats, reason_labels, reason_values)

    chart_data_json = json.dumps(chart_data, ensure_ascii=False).replace('</', '<\\/')

    return render_template(
        'admin/analytics.html',
        metrics=metrics,
        products=products,
        product_stats=product_stats,
        selected_product_id=selected_product_id or '',
        start_date=start_date_str,
        end_date=end_date_str,
        chart_data_json=chart_data_json,
        has_status_data=any(value > 0 for value in status_values),
        has_product_data=any(value > 0 for value in product_issued_values + product_not_issued_values),
        has_reason_data=any(value > 0 for value in reason_values),
        ai_analysis=ai_analysis,
        status_labels=RESERVATION_STATUSES,
    )


@app.route('/admin/export_inventory')
@admin_required
def admin_export_inventory():
    return export_inventory_excel()


@app.route('/admin/logs')
@admin_required
def admin_logs():
    page = request.args.get('page', 1, type=int)
    pagination = AdminLog.query.options(joinedload(AdminLog.admin)).order_by(
        AdminLog.created_at.desc()
    ).paginate(page=page, per_page=40, error_out=False)
    return render_template('admin/logs.html', logs=pagination.items, pagination=pagination)


@app.route('/admin/reservation/<int:reservation_id>/ticket')
@admin_required
def reservation_ticket(reservation_id):
    reservation = Reservation.query.options(
        joinedload(Reservation.user),
        joinedload(Reservation.product).joinedload(Product.category)
    ).get_or_404(reservation_id)
    return render_template(
        'admin/reservation_ticket.html',
        reservation=reservation,
        status_labels=RESERVATION_STATUSES,
        generated_at=datetime.utcnow()
    )
    
# Страница товара (детальная)
@app.route('/product/<int:product_id>')
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)
    # Проверяем активность товара
    if not product.is_active:
        flash('Товар не найден или недоступен', 'danger')
        return redirect(url_for('index'))
    
    # Получаем размеры товара
    sizes = ProductSize.query.filter_by(product_id=product_id).all()
    
    return render_template('product_detail.html', product=product, sizes=sizes)
    
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        migrate_sqlite_reservations()
        
        # Создаем тестовые категории
        if not Category.query.first():
            categories = ['Одежда', 'Обувь', 'Аксессуары']
            for cat in categories:
                db.session.add(Category(name=cat))
            db.session.commit()
            print("Категории созданы")
        
        # Создаем админа
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                name='Administrator',
                phone='+79991234567',
                role='admin'
            )
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("Админ создан (admin/admin123)")
        
        # Создаем тестовый товар для проверки
        if not Product.query.first():
            category = Category.query.first()
            if category:
                product = Product(
                    name='Тестовый товар',
                    description='Это тестовый товар для проверки',
                    price=1000,
                    category_id=category.id,
                    max_per_user=5,
                    image='',
                    is_active=True
                )
                db.session.add(product)
                db.session.flush()
                
                # Добавляем размеры
                sizes = [('S', 5), ('M', 10), ('L', 7)]
                for size, qty in sizes:
                    product_size = ProductSize(
                        product_id=product.id,
                        size=size,
                        quantity=qty
                    )
                    db.session.add(product_size)
                
                db.session.commit()
                print("Тестовый товар создан")
    
    print("Сервер запущен на http://127.0.0.1:5000")
    app.run(debug=True)
