"""
Повикување на SQL функциите, процедурите и views од geocab_schema.sql.

Логиката за возењата е во базата, затоа views треба да ги користат овие
функции наместо директно да ги менуваат Rides/Payments/... преку ORM.

Тек на едно возење:
    driver_start_shift -> request_ride -> accept_ride / reject_ride
    -> driver_arrived -> start_ride -> complete_ride
    -> process_payment -> rate_ride
    (пред крајот, во секој момент: cancel_ride)

Грешките од базата (RAISE EXCEPTION) доаѓаат како django.db.DatabaseError.
"""
from django.db import connection


def error_message(exc):
    """Порака од RAISE EXCEPTION во базата, без техничките детали (CONTEXT, LINE...)."""
    diag = getattr(exc.__cause__, 'diag', None)
    if diag is not None and diag.message_primary:
        return diag.message_primary
    return str(exc).splitlines()[0]


def _rows(cursor):
    columns = [col.name for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def call_function(name, *args):
    """SELECT fn(...) - враќа една вредност."""
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {name}({", ".join(["%s"] * len(args))})', args)
        return cursor.fetchone()[0]


def call_table_function(name, *args):
    """SELECT * FROM fn(...) - за функции што враќаат TABLE, како листа од dict."""
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT * FROM {name}({", ".join(["%s"] * len(args))})', args)
        return _rows(cursor)


def call_procedure(name, *args):
    """CALL sp(...)"""
    with connection.cursor() as cursor:
        cursor.execute(f'CALL {name}({", ".join(["%s"] * len(args))})', args)


def fetch_view(view, where=None, params=()):
    """SELECT * FROM vw_... [WHERE ...] како листа од dict."""
    sql = f'SELECT * FROM {view}'
    if where:
        sql += f' WHERE {where}'
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return _rows(cursor)


# ---- Локации и цени -------------------------------------------------------

def create_location(lat, lng, grad, naselba, ulica, broj):
    return call_function('fn_create_location', lat, lng, grad, naselba, ulica, broj)


def estimate_price(pickup_location_id, dropoff_location_id, vehicle_class=None, distance_km=None):
    """distance_km = растојание по пат (routing.road_distance_km); None = воздушно"""
    return call_table_function('fn_estimate_price', pickup_location_id, dropoff_location_id, vehicle_class, distance_km)


# ---- Администратор --------------------------------------------------------

def admin_create_driver(admin_id, name, username, phone, email, password_hash,
                        license_number, vehicle_ownership_id=None):
    """password_hash = django.contrib.auth.hashers.make_password(лозинка)"""
    return call_function('fn_admin_create_driver', admin_id, name, username, phone, email,
                         password_hash, license_number, vehicle_ownership_id)


def refund_payment(payment_id, admin_id):
    call_procedure('sp_refund_payment', payment_id, admin_id)


MONTHS_MK = ['јануари', 'февруари', 'март', 'април', 'мај', 'јуни', 'јули', 'август',
             'септември', 'октомври', 'ноември', 'декември']


def _add_months(day, months):
    total = day.month - 1 + months
    return day.replace(year=day.year + total // 12, month=total % 12 + 1, day=1)


def driver_debt_status(driver_id):
    """Месечен долг на возачот (fn_driver_debt_status) + рокови и порака за прикажување.
    level: None / 'due' (во рок 1-14) / 'warning' (опомена од 15-ти) / 'blocked' (од 1-ви во М+2)"""
    d = call_table_function('fn_driver_debt_status', driver_id)[0]
    for key in ('due', 'warning', 'overdue'):
        month = d[f'{key}_month']
        d[f'{key}_month_name'] = f'{MONTHS_MK[month.month - 1]} {month.year}' if month else None
    if d['is_blocked']:
        d['level'] = 'blocked'
        d['message'] = (f'Блокирани сте: провизијата за {d["overdue_month_name"]} ({d["overdue_amount"]} ден.) не е платена. '
                        f'Не можете да почнете смена ниту да добивате понуди додека не ја уплатите кај GEOCAB.')
    elif d['warning_amount'] > 0:
        deadline = _add_months(d['warning_month'], 2)
        d['level'] = 'warning'
        d['deadline'] = deadline
        d['message'] = (f'Опомена: провизијата за {d["warning_month_name"]} ({d["warning_amount"]} ден.) не е платена. '
                        f'Последен рок е {deadline:%d.%m.%Y}. Ако не платите, од тој ден ќе бидете блокирани.')
    elif d['due_amount'] > 0:
        deadline = _add_months(d['due_month'], 1).replace(day=15)
        d['level'] = 'due'
        d['deadline'] = deadline
        d['message'] = (f'Платете ја провизијата за {d["due_month_name"]} ({d["due_amount"]} ден.) '
                        f'до {deadline:%d.%m.%Y}.')
    else:
        d['level'] = None
        d['message'] = None
    return d


def record_commission_payout(driver_id, admin_id, amount, note=None):
    """Возачот ѝ уплатил провизија на GEOCAB (не смее да е повеќе од долгот)."""
    call_procedure('sp_record_commission_payout', driver_id, admin_id, amount, note)


def record_driver_payout(driver_id, admin_id, amount, note=None):
    """GEOCAB му исплатил на возачот (заработка од картички; не смее да е повеќе од она што му се должи)."""
    call_procedure('sp_record_driver_payout', driver_id, admin_id, amount, note)


# ---- Возач ----------------------------------------------------------------

def driver_start_shift(driver_id, location_id, vehicle_ownership_id):
    call_procedure('sp_driver_start_shift', driver_id, location_id, vehicle_ownership_id)


def driver_end_shift(driver_id):
    call_procedure('sp_driver_end_shift', driver_id)


def update_driver_location(driver_id, location_id):
    call_procedure('sp_update_driver_location', driver_id, location_id)


def update_driver_position(driver_id, lat, lng):
    """GPS од прелистувачот; нова локација само ако се поместил > 50 m."""
    call_procedure('sp_update_driver_position', driver_id, lat, lng)


def accept_ride(ride_id, driver_id):
    call_procedure('sp_accept_ride', ride_id, driver_id)


def reject_ride(ride_id, driver_id, reason='REJECTED'):
    call_procedure('sp_reject_ride', ride_id, driver_id, reason)


def driver_arrived(ride_id, driver_id):
    call_procedure('sp_driver_arrived', ride_id, driver_id)


def start_ride(ride_id, driver_id):
    call_procedure('sp_start_ride', ride_id, driver_id)


def complete_ride(ride_id, driver_id, distance_km=None):
    call_procedure('sp_complete_ride', ride_id, driver_id, distance_km)


# ---- Корисник -------------------------------------------------------------

CONFIRMATION_VALID_HOURS = 24   # колку часа важи линкот за потврда на е-пошта


def create_pending_registration(name, username, phone, email, password_hash, token_hash):
    """Регистрација што чека потврда на е-пошта (профилот уште не постои во Users). Враќа pending_id."""
    return call_function('fn_create_pending_registration', name, username, phone, email, password_hash, token_hash,
                         CONFIRMATION_VALID_HOURS)


def renew_pending_registration(email, token_hash):
    """Нов линк за потврда (стариот престанува да важи). None ако нема регистрација што чека."""
    return call_function('fn_renew_pending_registration', email, token_hash, CONFIRMATION_VALID_HOURS)


def confirm_registration(token_hash):
    """Линкот од мејлот е отворен: базата го креира профилот во Users. Враќа user_id."""
    return call_function('fn_confirm_registration', token_hash)


def request_ride(user_id, pickup_location_id, dropoff_location_id, vehicle_class=None, distance_km=None,
                 payment_tip='CASH'):
    """Враќа ride_id. Базата веднаш бара најблизок возач. distance_km = по пат (None = воздушно).
    payment_tip: 'CASH' / 'CARD' - како корисникот планира да плати."""
    return call_function('fn_request_ride', user_id, pickup_location_id, dropoff_location_id, vehicle_class,
                         distance_km, payment_tip)


def cancel_ride(ride_id, cancelled_by_type, cancelled_by_id, reason, no_show=False):
    """cancelled_by_type: 'USER', 'DRIVER' или 'ADMIN' (со големи букви, chk_cancelled_by_type)"""
    call_procedure('sp_cancel_ride', ride_id, cancelled_by_type, cancelled_by_id, reason, no_show)


def process_payment(ride_id, payment_tip, success=True, card_brand=None, card_last4=None):
    """payment_tip: 'CASH' или 'CARD'. За CARD: резултатот од платежната порта и тип + последни 4 цифри
    (целиот број никогаш не се праќа во базата)."""
    call_procedure('sp_process_payment', ride_id, payment_tip, success, card_brand, card_last4)


def rate_ride(ride_id, user_id, score, comment=None):
    call_procedure('sp_rate_ride', ride_id, user_id, score, comment)


# ---- Пријави на проблем ------------------------------------------------------

REPORT_CATEGORIES = {
    'SAFETY': '🚨 Безбедност',
    'BEHAVIOR': '😠 Однесување',
    'VEHICLE': '🚗 Возилото',
    'ROUTE': '🗺️ Рута / локација',
    'PRICE': '💵 Цена / плаќање',
    'OTHER': '❓ Друго',
}


def report_problem(ride_id, reported_by_type, reported_by_id, category, message):
    """reported_by_type: 'USER' или 'DRIVER'"""
    call_procedure('sp_report_problem', ride_id, reported_by_type, reported_by_id, category, message)


def resolve_report(report_id, admin_id, note=None):
    call_procedure('sp_resolve_report', report_id, admin_id, note)


# ---- Периодично (на неколку секунди) ---------------------------------------

# Колку време возачот има да одговори на понуда пред да оди кај следниот возач
OFFER_TIMEOUT_SECONDS = 5 * 60


def expire_offers(timeout_seconds=OFFER_TIMEOUT_SECONDS):
    """Понудите без одговор од возачот одат кај следниот возач."""
    return call_function('fn_expire_offers', timeout_seconds)
