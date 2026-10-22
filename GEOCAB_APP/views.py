from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Avg
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import db
from .auth import ROLES, current_person, login_person, logout_person, role_required
from .forms import DriverLocationForm, LoginForm, RegisterForm, RideForm
from .models import (ActiveDrivers, CommissionPayouts, Drivers, Notifications, Payments, PlatformSettings, PricingRules,
                     Ratings, Rides, Users, VehicleOwnership)


def _public_context():
    """Податоци за јавните страници: цени по класа, бројки и дел на возачот."""
    prices = []
    for rule in PricingRules.objects.select_related('vehicle_type').order_by('base_price'):
        if rule.vehicle_type.description not in [p['name'] for p in prices]:
            prices.append({'name': rule.vehicle_type.description,
                           'base': float(rule.base_price), 'per_km': float(rule.price_per_km)})
    platform = PlatformSettings.objects.filter(pk=1).first()
    commission = platform.commission_percentage if platform else 0
    return {
        'prices': prices,
        'driver_share': 100 - commission,
        'stats': {
            'drivers': Drivers.objects.filter(status__status_text='active').count(),
            'rides': Rides.objects.filter(status__status_text='completed').count(),
            'users': Users.objects.filter(status__status_text='active').count(),
            'rating': Drivers.objects.filter(status__status_text='active').aggregate(r=Avg('rating'))['r'] or 5,
        },
    }


def home(request):
    return render(request, 'GEOCAB_APP/home.html', _public_context())


def about(request):
    return render(request, 'GEOCAB_APP/about.html', _public_context())


def register(request):
    if current_person(request)[1]:
        return redirect('home')
    form = RegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login_person(request, 'user', user)
        messages.success(request, f'Добредојдовте, {user.name}! Профилот е креиран.')
        return redirect('home')
    return render(request, 'GEOCAB_APP/register.html', {'form': form})


def login_view(request):
    if current_person(request)[1]:
        return redirect('home')
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        role, person = form.cleaned_data['role'], form.cleaned_data['person']
        login_person(request, role, person)
        messages.success(request, f'Најавени сте како {ROLES[role][1].lower()}: {person.name}.')
        if role == 'driver':
            debt = db.driver_debt_status(person.pk)
            if debt['level'] == 'blocked':
                messages.error(request, debt['message'])
            elif debt['level'] in ('warning', 'due'):
                messages.warning(request, debt['message'])
        next_url = request.GET.get('next')
        if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}):
            return redirect(next_url)
        return redirect({'driver': 'driver_dashboard', 'admin': 'manage_dashboard'}.get(role, 'home'))
    return render(request, 'GEOCAB_APP/login.html', {'form': form})


@require_POST
def logout_view(request):
    logout_person(request)
    messages.info(request, 'Одјавени сте.')
    return redirect('home')


# ---- Плаќање (само готовина) ---------------------------------------------------

def _unpaid(user_id):
    """Неплатени плаќања на корисникот: завршени возења и казни за откажување.
    Казните се наплаќаат во готовина заедно со следното завршено возење."""
    return list(Payments.objects.filter(ride__user_id=user_id, status__status_text__in=('pending', 'failed'))
                .select_related('ride__status').order_by('payment_id'))


def _driver_to_collect(driver_id):
    """Последно завршено возење на возачот што уште не е платено, со сите неплатени
    износи на тој корисник (возењето + евентуални стари казни)."""
    ride = (Rides.objects.filter(driver_id=driver_id, status__status_text='completed',
                                 payments__status__status_text__in=('pending', 'failed'))
            .select_related('user').order_by('-ride_id').first())
    if not ride:
        return None
    unpaid = _unpaid(ride.user_id)
    fees = [p for p in unpaid if p.ride_id != ride.pk]
    return {'ride': ride, 'payments': unpaid, 'fees': fees,
            'ride_amount': sum(p.amount for p in unpaid if p.ride_id == ride.pk),
            'fees_amount': sum(p.amount for p in fees),
            'total': sum(p.amount for p in unpaid)}


# ---- Корисник: возење ---------------------------------------------------------

def _current_ride(user_id):
    """Активно возење на корисникот од vw_current_ride (или None)."""
    rows = db.fetch_view('vw_current_ride', 'user_id = %s', [user_id])
    return rows[0] if rows else None


def _estimate(form):
    """Проценка на цена за сите класи. Локациите се креираат само за пресметката
    и веднаш се враќаат назад (rollback), за да не остануваат во базата."""
    with transaction.atomic():
        pickup = db.create_location(*form.location_args('pickup'))
        dropoff = db.create_location(*form.location_args('dropoff'))
        rows = db.estimate_price(pickup, dropoff, distance_km=form.road_distance_km())
        transaction.set_rollback(True)
    return rows


@role_required('user')
def ride_new(request):
    if _current_ride(request.person.pk):
        messages.info(request, 'Веќе имате активно возење.')
        return redirect('ride_current')

    form = RideForm(request.POST or None)
    estimates = None
    if request.method == 'POST' and form.is_valid():
        try:
            if request.POST.get('action') == 'request':
                with transaction.atomic():
                    pickup = db.create_location(*form.location_args('pickup'))
                    dropoff = db.create_location(*form.location_args('dropoff'))
                    ride_id = db.request_ride(request.person.pk, pickup, dropoff,
                                              form.cleaned_data['vehicle_class'] or None, form.road_distance_km())
                ride = Rides.objects.select_related('status').get(pk=ride_id)
                if ride.status.status_text != 'no_driver':
                    messages.success(request, 'Барањето е испратено.')
                    return redirect('ride_current')
                messages.warning(request, 'Моментално нема слободен возач во близина. Обидете се повторно за малку.')
            estimates = _estimate(form)
            if not estimates:
                messages.warning(request, 'Сè уште нема внесени цени за возилата.')
        except DatabaseError as exc:
            messages.error(request, db.error_message(exc))
    return render(request, 'GEOCAB_APP/ride_new.html', {
        'form': form, 'estimates': estimates, 'selected_class': form.data.get('vehicle_class', ''),
        'by_road': bool(estimates) and form.road_distance_km() is not None,
    })


STATUS_LABELS = {
    'requested': 'Бараме возач...',
    'accepted': 'Возачот го прифати возењето и доаѓа кон вас.',
    'driver_arrived': 'Возачот пристигна на локацијата.',
    'in_progress': 'Возењето е во тек.',
    'completed': 'Возењето е завршено.',
    'cancelled': 'Возењето е откажано.',
    'no_driver': 'Немаше слободен возач.',
}


@role_required('user')
def ride_current(request):
    db.expire_offers()  # понуда без одговор од возачот оди кај следниот возач
    ride = _current_ride(request.person.pk)
    context = {'ride': ride}
    if ride:
        context['status_label'] = STATUS_LABELS.get(ride['ride_status'], ride['ride_status'])
        if ride['ride_status'] == 'requested' and ride['driver_id']:
            context['status_label'] = f'Чекаме возачот {ride["driver_name"]} да ја прифати понудата...'
        context['cancellation_fee'] = db.call_function('fn_cancellation_fee', ride['ride_id'], 'USER', False)
        if ride['ride_status'] in ACTIVE_RIDE_STATUSES:
            context.update(_report_context(ride['ride_id'], 'USER', request.person.pk))
    else:
        last = (Rides.objects.filter(user_id=request.person.pk)
                .select_related('status', 'driver').order_by('-ride_id').first())
        context['last_ride'] = last
        if last:
            context['last_label'] = STATUS_LABELS.get(last.status.status_text)
        if last and last.status.status_text == 'completed':
            context['last_payment'] = last.payments_set.select_related('status').order_by('-payment_id').first()
            context['can_rate'] = not Ratings.objects.filter(ride=last).exists()
    context['unpaid_fees'] = [p for p in _unpaid(request.person.pk) if p.ride.status.status_text == 'cancelled']
    context['unpaid_fees_total'] = sum(p.amount for p in context['unpaid_fees'])
    return render(request, 'GEOCAB_APP/ride_current.html', context)


@role_required('user')
def ride_current_status(request):
    """JSON за автоматско освежување на страницата."""
    db.expire_offers()
    ride = _current_ride(request.person.pk)
    if not ride:
        payment = (Payments.objects.filter(ride__user_id=request.person.pk)
                   .select_related('status').order_by('-payment_id').first())
        return JsonResponse({'status': None, 'payment': payment.status.status_text if payment else None})
    return JsonResponse({'status': ride['ride_status'], 'driver_id': ride['driver_id']})


@role_required('user')
def ride_track(request):
    """JSON за мапата во живо: каде е возачот за активното возење на корисникот."""
    ride = _current_ride(request.person.pk)
    if not ride:
        return JsonResponse({'ride': None})
    data = {'ride': ride['ride_id'], 'status': ride['ride_status'], 'driver': None}
    if ride['driver_id'] and ride['ride_status'] != 'requested':
        shift = (ActiveDrivers.objects.select_related('current_location')
                 .filter(driver_id=ride['driver_id'], working_end__isnull=True).first())
        if shift:
            loc = shift.current_location
            data['driver'] = {
                'lat': float(loc.latitude), 'lng': float(loc.longitude),
                'gps': shift.position_updated_at is not None
                       and (datetime.now() - shift.position_updated_at).total_seconds() < 120,
            }
    return JsonResponse(data)


@role_required('user')
@require_POST
def ride_rate(request, ride_id):
    ride = Rides.objects.filter(pk=ride_id, user_id=request.person.pk).first()
    if not ride:
        messages.error(request, 'Возењето не постои.')
    elif Ratings.objects.filter(ride=ride).exists():
        messages.info(request, 'Ова возење веќе е оценето.')
    elif request.POST.get('score') not in ('1', '2', '3', '4', '5'):
        messages.error(request, 'Изберете оценка од 1 до 5 ѕвезди.')
    else:
        try:
            with transaction.atomic():
                db.rate_ride(ride.pk, request.person.pk, int(request.POST['score']),
                             request.POST.get('comment', '').strip()[:255] or None)
            messages.success(request, 'Ви благодариме за оценката!')
        except DatabaseError as exc:
            messages.error(request, db.error_message(exc))
    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}):
        return redirect(next_url)
    return redirect('ride_current')


@role_required('user')
@require_POST
def ride_cancel(request):
    ride = _current_ride(request.person.pk)
    if not ride:
        messages.info(request, 'Немате активно возење.')
        return redirect('ride_current')
    try:
        with transaction.atomic():
            db.cancel_ride(ride['ride_id'], 'USER', request.person.pk,
                           request.POST.get('reason') or 'Откажано од корисникот')
        messages.success(request, 'Возењето е откажано.')
    except DatabaseError as exc:
        messages.error(request, db.error_message(exc))
    return redirect('ride_current')


# ---- Возач ------------------------------------------------------------------------

DRIVER_STEPS = {
    # статус: (следна акција, текст на копчето)
    'accepted': ('arrived', 'Пристигнав на локацијата'),
    'driver_arrived': ('start', 'Почни возење'),
    'in_progress': ('complete', 'Заврши возење'),
}
DRIVER_STATUS_LABELS = {
    'accepted': 'Одите кон корисникот.',
    'driver_arrived': 'Чекате на корисникот.',
    'in_progress': 'Возењето е во тек.',
}


def _driver_state(driver_id):
    """Смена, понуда и активно возење на возачот."""
    shift = (ActiveDrivers.objects.select_related('current_location', 'vehicle_ownership__vehicle_type')
             .filter(driver_id=driver_id, working_end__isnull=True).first())
    offers = db.fetch_view('vw_driver_pending_requests', 'driver_id = %s', [driver_id])
    rides = db.fetch_view('vw_ride_details',
                          "driver_id = %s AND ride_status IN ('accepted', 'driver_arrived', 'in_progress')",
                          [driver_id])
    return shift, (offers[0] if offers else None), (rides[0] if rides else None)


def _driver_vehicles(driver_id):
    return VehicleOwnership.objects.filter(driversvehicleownership__driver_id=driver_id).select_related('vehicle_type')


@role_required('driver')
def driver_dashboard(request):
    db.expire_offers()
    driver_id = request.person.pk
    shift, offer, ride = _driver_state(driver_id)
    context = {'shift': shift, 'offer': offer, 'ride': ride, 'collect': _driver_to_collect(driver_id),
               'debt': db.driver_debt_status(driver_id)}
    if not shift:
        context['vehicles'] = _driver_vehicles(driver_id)
        context['form'] = DriverLocationForm(vehicles=context['vehicles'])
    else:
        context['form'] = DriverLocationForm()
    if offer:
        dropoff = Rides.objects.select_related('dropoff_location').get(pk=offer['ride_id']).dropoff_location
        context['offer_dropoff'] = dropoff
        waited = (datetime.now() - offer['offered_at']).total_seconds()
        context['seconds_left'] = max(0, int(db.OFFER_TIMEOUT_SECONDS - waited))
    if ride:
        context.update(_report_context(ride['ride_id'], 'DRIVER', driver_id))
        context['next_action'] = DRIVER_STEPS.get(ride['ride_status'])
        context['status_label'] = DRIVER_STATUS_LABELS.get(ride['ride_status'])
    return render(request, 'GEOCAB_APP/driver_dashboard.html', context)


@role_required('driver')
def driver_profile(request):
    driver_id = request.person.pk
    summary = db.fetch_view('vw_driver_earnings_summary', 'driver_id = %s', [driver_id])[0]
    daily = db.fetch_view('vw_driver_earnings', 'driver_id = %s ORDER BY ride_date DESC LIMIT 14', [driver_id])
    payments = (Payments.objects.filter(ride__driver_id=driver_id, status__status_text='completed')
                .select_related('ride__status', 'ride__user').order_by('-payment_time')[:15])
    settings_row = PlatformSettings.objects.filter(pk=1).first()
    commission = settings_row.commission_percentage if settings_row else 0
    return render(request, 'GEOCAB_APP/driver_profile.html', {
        'summary': summary, 'daily': daily, 'payments': payments,
        'vehicles': _driver_vehicles(driver_id),
        'commission': commission, 'driver_share': 100 - commission,
        'balance': db.fetch_view('vw_driver_commission_balance', 'driver_id = %s', [driver_id])[0],
        'debt': db.driver_debt_status(driver_id),
        'payouts': CommissionPayouts.objects.filter(driver_id=driver_id).order_by('-payout_id')[:10],
    })


@role_required('driver')
@require_POST
def driver_position(request):
    """GPS позиција од прелистувачот на возачот (JSON)."""
    try:
        lat, lng = Decimal(request.POST['lat']), Decimal(request.POST['lng'])
    except (KeyError, InvalidOperation):
        return JsonResponse({'ok': False, 'error': 'Невалидна позиција'}, status=400)
    try:
        with transaction.atomic():
            db.update_driver_position(request.person.pk, lat.quantize(Decimal('0.000001')), lng.quantize(Decimal('0.000001')))
    except DatabaseError as exc:
        return JsonResponse({'ok': False, 'error': db.error_message(exc)}, status=409)
    return JsonResponse({'ok': True})


@role_required('driver')
def driver_status(request):
    """JSON за автоматско освежување на возачката страница."""
    db.expire_offers()
    shift, offer, ride = _driver_state(request.person.pk)
    collect = _driver_to_collect(request.person.pk)
    return JsonResponse({
        'collect': collect['ride'].pk if collect else None,
        'shift': bool(shift),
        'offer': offer['ride_id'] if offer else None,
        'ride': [ride['ride_id'], ride['ride_status']] if ride else None,
    })


@role_required('driver')
@require_POST
def driver_action(request):
    driver_id = request.person.pk
    action = request.POST.get('action')
    ride_id = request.POST.get('ride_id')
    try:
        # секоја акција во своја трансакција: ако базата ја одбие, се враќа само таа
        with transaction.atomic():
            if action in ('start_shift', 'update_location'):
                vehicles = _driver_vehicles(driver_id) if action == 'start_shift' else None
                form = DriverLocationForm(request.POST, vehicles=vehicles)
                if not form.is_valid():
                    messages.error(request, 'Изберете ја вашата локација на мапата и пополнете ја адресата.')
                    return redirect('driver_dashboard')
                location_id = db.create_location(*form.location_args())
                if action == 'start_shift':
                    db.driver_start_shift(driver_id, location_id, form.cleaned_data['vehicle_ownership'].pk)
                else:
                    db.update_driver_location(driver_id, location_id)
                messages.success(request, 'Смената започна. Чекате понуди.' if action == 'start_shift' else 'Локацијата е ажурирана.')
            elif action == 'end_shift':
                db.driver_end_shift(driver_id)
                messages.info(request, 'Смената е завршена.')
            elif action == 'accept':
                db.accept_ride(ride_id, driver_id)
                messages.success(request, 'Возењето е прифатено. Тргнете кон корисникот.')
            elif action == 'reject':
                db.reject_ride(ride_id, driver_id)
                messages.info(request, 'Понудата е одбиена.')
            elif action == 'arrived':
                db.driver_arrived(ride_id, driver_id)
            elif action == 'start':
                db.start_ride(ride_id, driver_id)
            elif action == 'complete':
                db.complete_ride(ride_id, driver_id)
                ride = Rides.objects.get(pk=ride_id)
                messages.success(request, f'Возењето е завршено. Цена: {ride.final_price} ден.')
            elif action == 'cash_received':
                collect = _driver_to_collect(driver_id)
                if not collect or str(collect['ride'].pk) != ride_id:
                    messages.error(request, 'Нема плаќање што чека за ова возење.')
                    return redirect('driver_dashboard')
                for payment_ride_id in dict.fromkeys(p.ride_id for p in collect['payments']):
                    db.process_payment(payment_ride_id, 'CASH')
                messages.success(request, f'Наплатено {collect["total"]} ден. во готовина.')
            elif action == 'cancel':
                db.cancel_ride(ride_id, 'DRIVER', driver_id, 'Откажано од возачот')
                messages.info(request, 'Се откажавте. Возењето оди кај друг возач.')
            elif action == 'no_show':
                db.cancel_ride(ride_id, 'DRIVER', driver_id, 'Корисникот не се појави', True)
                messages.info(request, 'Пријавено: корисникот не се појави.')
            else:
                messages.error(request, 'Непозната акција.')
    except DatabaseError as exc:
        messages.error(request, db.error_message(exc))
    return redirect('driver_dashboard')


# ---- Корисник: историја и известувања ---------------------------------------------

HISTORY_STATUS = {
    'completed': ('Завршено', 'success'),
    'cancelled': ('Откажано', 'danger'),
    'no_driver': ('Немаше возач', 'warning'),
}
CANCELLED_BY = {'USER': 'од вас', 'DRIVER': 'од возачот', 'ADMIN': 'од администратор'}


@role_required('user')
def ride_history(request):
    rows = db.fetch_view('vw_user_ride_history', 'user_id = %s ORDER BY request_time DESC, ride_id DESC', [request.person.pk])
    spent = sum((r['final_price'] if r['ride_status'] == 'completed' else r['cancellation_fee']) or 0
                for r in rows if r['payment_status'] == 'completed')
    completed = [r for r in rows if r['ride_status'] == 'completed']
    for r in rows:
        r['label'], r['color'] = HISTORY_STATUS.get(r['ride_status'], ('Во тек', 'primary'))
        r['cancelled_by_label'] = CANCELLED_BY.get(r['cancelled_by_type'])
        r['can_rate'] = r['ride_status'] == 'completed' and r['rating_score'] is None and r['driver_id']
    page = Paginator(rows, 10).get_page(request.GET.get('page'))
    return render(request, 'GEOCAB_APP/ride_history.html', {
        'page': page,
        'total_rides': len(completed),
        'total_km': sum(r['distance_km'] or 0 for r in completed),
        'total_spent': spent,
    })


# Пораките во базата се на латиница (функциите ги бараат по текст), па тука само се прикажуваат на кирилица
MESSAGE_LABELS = {
    'Vaseto baranje e uspesno isprateno': 'Вашето барање е успешно испратено',
    'Vozacot go prifati vaseto baranje': 'Возачот го прифати вашето барање',
    'Vozacot go odbi vaseto baranje': 'Возачот го одби вашето барање',
    'Vozacot pristigna na lokacijata za podiganje': 'Возачот пристигна на локацијата за подигање',
    'Vozenjeto e otkazano od strana na vozacot': 'Возењето е откажано од страна на возачот',
    'Vozenjeto e otkazano od vasa strana': 'Возењето е откажано од ваша страна',
    'Plakjanjeto e uspesno': 'Плаќањето е успешно',
    'Plakjanjeto ne uspea': 'Плаќањето не успеа',
    'Nemate dostapni vozaci vo momentot': 'Немате достапни возачи во моментот',
    'Vozenjeto zapocna': 'Возењето започна',
    'Vozenjeto e uspesno zavrseno': 'Возењето е успешно завршено',
    'Vozenjeto e otkazano od administrator': 'Возењето е откажано од администратор',
    'Vozacot go otkaza vozenjeto, barame nov vozac': 'Возачот го откажа возењето, бараме нов возач',
    'Vozenjeto e otkazano bidejki ne se pojavivte': 'Возењето е откажано бидејќи не се појавивте',
    'Parite vi se vrateni': 'Парите ви се вратени',
}
NOTIFICATIONS_SEEN_KEY = 'geocab_notifications_seen'   # до кое известување е отворена страницата 🔔
NOTIFICATIONS_TOAST_KEY = 'geocab_notifications_toast'  # до кое известување е прикажано како pop-up


@role_required('user')
def notifications(request):
    rows = db.fetch_view('vw_user_notifications', 'user_id = %s ORDER BY notification_id DESC LIMIT 50',
                         [request.person.pk])
    seen = request.session.get(NOTIFICATIONS_SEEN_KEY, 0)
    for r in rows:
        r['text'] = MESSAGE_LABELS.get(r['message_text'], r['message_text'])
        r['is_new'] = r['notification_id'] > seen
    if rows:
        request.session[NOTIFICATIONS_SEEN_KEY] = rows[0]['notification_id']
    return render(request, 'GEOCAB_APP/notifications.html', {'notifications': rows})


@role_required('user')
def notifications_poll(request):
    """JSON за pop-up известувањата: само новите од последната проверка."""
    user_id = request.person.pk
    last = Notifications.objects.filter(user_id=user_id).order_by('-notification_id').values_list('notification_id', flat=True).first() or 0
    shown = request.session.get(NOTIFICATIONS_TOAST_KEY)
    new = []
    if shown is not None:
        for n in (Notifications.objects.filter(user_id=user_id, notification_id__gt=shown)
                  .select_related('message').order_by('notification_id')[:5]):
            new.append({'id': n.notification_id, 'ride_id': n.ride_id, 'time': n.sent_time.strftime('%H:%M'),
                        'text': MESSAGE_LABELS.get(n.message.message_text, n.message.message_text)})
    request.session[NOTIFICATIONS_TOAST_KEY] = last
    seen = request.session.get(NOTIFICATIONS_SEEN_KEY, 0)
    return JsonResponse({'notifications': new,
                         'unread': Notifications.objects.filter(user_id=user_id, notification_id__gt=seen).count()})



# ---- Пријава на проблем (корисник и возач, во тек на возење) -------------------------

ACTIVE_RIDE_STATUSES = ('accepted', 'driver_arrived', 'in_progress')


def _my_active_ride(role, person_id):
    column = 'user_id' if role == 'user' else 'driver_id'
    return Rides.objects.filter(**{column: person_id}, status__status_text__in=ACTIVE_RIDE_STATUSES).order_by('-ride_id').first()


def _report_context(ride_id, reported_by_type, person_id):
    """За копчето „Пријави проблем“: категории и моите пријави за ова возење."""
    return {
        'report_ride_id': ride_id,
        'report_categories': db.REPORT_CATEGORIES,
        'my_reports': db.fetch_view('vw_ride_reports',
                                    'ride_id = %s AND reported_by_type = %s AND reported_by_id = %s ORDER BY report_id',
                                    [ride_id, reported_by_type, person_id]),
    }


@role_required('user', 'driver')
@require_POST
def report_problem(request):
    ride = _my_active_ride(request.role, request.person.pk)
    back = 'ride_current' if request.role == 'user' else 'driver_dashboard'
    category = request.POST.get('category')
    if not ride:
        messages.error(request, 'Проблем може да се пријави само додека возењето е во тек.')
    elif category not in db.REPORT_CATEGORIES:
        messages.error(request, 'Изберете тип на проблем.')
    else:
        try:
            with transaction.atomic():
                db.report_problem(ride.pk, request.role.upper(), request.person.pk, category,
                                  request.POST.get('message', '')[:1000])
            messages.success(request, 'Пријавата е испратена до GEOCAB. Ќе ве контактираме што е можно побрзо.')
        except DatabaseError as exc:
            messages.error(request, db.error_message(exc))
    return redirect(back)
