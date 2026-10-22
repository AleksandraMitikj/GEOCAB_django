"""
Админ панел во апликацијата (/manage/) - за администраторите од табелата Admins.
Django /admin/ останува само за развој.
"""
from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import db
from .auth import role_required, set_password
from .forms import PROFILE_LABELS as PROFILE_LABEL
from .forms import (AdminSetPasswordForm, CommissionForm, profile_form, NewDriverForm, NewVehicleClassForm, PayoutForm, PriceForm,
                    VehicleChoiceForm, VehicleForm)
from .models import (ActiveDrivers, Cancellations, CommissionPayouts, Drivers, DriversVehicleOwnership, Notifications,
                     Payments, PlatformSettings, PricingRules, Ratings, RideRejections, Rides, Status, Users,
                     VehicleOwnership, VehicleTypes, VehiclesModel)


def _by_driver(view, where=None, params=()):
    return {row['driver_id']: row for row in db.fetch_view(view, where, params)}


def _sum_dashboard(rows):
    keys = ('total_rides', 'completed_rides', 'cancelled_rides', 'no_driver_rides', 'revenue', 'platform_revenue')
    return {k: sum(r[k] or 0 for r in rows) for k in keys}


@role_required('admin')
def dashboard(request):
    today = date.today()
    month_start = today.replace(day=1)
    days = db.fetch_view('vw_admin_dashboard', 'day >= %s', [month_start])
    money = (Payments.objects.filter(status__status_text='completed', payment_time__date__gte=month_start)
             .aggregate(collected=Sum('amount'), platform=Sum('platform_amount'), drivers=Sum('driver_amount')))
    balances = db.fetch_view('vw_driver_commission_balance', 'balance > 0 ORDER BY balance DESC')
    for b in balances:
        b['debt'] = db.driver_debt_status(b['driver_id'])['level']
    shifts = ActiveDrivers.objects.filter(working_end__isnull=True)
    return render(request, 'GEOCAB_APP/manage/dashboard.html', {
        'today': _sum_dashboard([d for d in days if d['day'] == today]),
        'month': _sum_dashboard(days),
        'money': {k: v or 0 for k, v in money.items()},
        'on_shift': shifts.count(),
        'available': shifts.filter(is_available=True).count(),
        'active_rides': len(db.fetch_view('vw_current_ride')),
        'outstanding': sum(b['balance'] for b in balances),
        'debtors': balances[:5],
        'blocked_for_debt': sum(1 for b in balances if b['debt'] == 'blocked'),
        'warned_for_debt': sum(1 for b in balances if b['debt'] == 'warning'),
        'recent_rides': db.fetch_view('vw_ride_details', 'TRUE ORDER BY ride_id DESC LIMIT 10'),
        'open_reports': db.fetch_view('vw_ride_reports', "report_status = 'open' ORDER BY report_id DESC"),
    })


@role_required('admin')
def drivers(request):
    q = request.GET.get('q', '').strip()
    qs = Drivers.objects.select_related('status').order_by('name')
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(username__icontains=q) | Q(phone__icontains=q)
                       | Q(driversvehicleownership__vehicle_ownership__plate__icontains=q)).distinct()
    summary = _by_driver('vw_driver_earnings_summary')
    balance = _by_driver('vw_driver_commission_balance')
    on_shift = set(ActiveDrivers.objects.filter(working_end__isnull=True).values_list('driver_id', flat=True))
    plates = {}
    for link in DriversVehicleOwnership.objects.select_related('vehicle_ownership'):
        plates.setdefault(link.driver_id, []).append(link.vehicle_ownership.plate)
    rows = [{'driver': d, 'summary': summary.get(d.pk, {}), 'balance': balance.get(d.pk, {}).get('balance', 0),
             'on_shift': d.pk in on_shift, 'plates': plates.get(d.pk, []),
             'debt': db.driver_debt_status(d.pk)['level']} for d in qs]
    if request.GET.get('debt'):
        rows = sorted((r for r in rows if r['balance'] > 0), key=lambda r: -r['balance'])
    return render(request, 'GEOCAB_APP/manage/drivers.html', {'rows': rows, 'q': q, 'debt': request.GET.get('debt')})


@role_required('admin')
def driver_new(request):
    form = NewDriverForm(request.POST or None)
    vehicle_choice = VehicleChoiceForm(request.POST or None)
    vehicle_form = VehicleForm(request.POST or None, prefix='v')
    if request.method == 'POST' and form.is_valid() and vehicle_choice.is_valid():
        mode = vehicle_choice.cleaned_data['vehicle_mode']
        if mode != 'new' or vehicle_form.is_valid():
            try:
                with transaction.atomic():
                    vehicle = None
                    if mode == 'new':
                        vehicle = vehicle_form.save()
                    elif mode == 'existing':
                        vehicle = vehicle_choice.cleaned_data['existing_vehicle']
                    c = form.cleaned_data
                    driver_id = db.admin_create_driver(
                        request.person.pk, c['name'], c['username'], c['phone'], c['email'],
                        make_password(c['password']), c['license_number'], vehicle.pk if vehicle else None)
                messages.success(request, f'Возачот {c["name"]} е регистриран. Може да се најави со корисничкото име „{c["username"]}“.')
                return redirect('manage_driver', driver_id=driver_id)
            except DatabaseError as exc:
                messages.error(request, db.error_message(exc))
    if request.method == 'POST' and request.POST.get('vehicle_mode') != 'new':
        vehicle_form = VehicleForm(prefix='v')  # формата за ново возило не се користи - без нејзини грешки
    return render(request, 'GEOCAB_APP/manage/driver_new.html', {
        'form': form, 'vehicle_choice': vehicle_choice, 'vehicle_form': vehicle_form,
    })


DRIVER_ADMIN_FIELDS = ('name', 'username', 'phone', 'email', 'license_number')


@role_required('admin')
def driver_detail(request, driver_id):
    driver = get_object_or_404(Drivers.objects.select_related('status', 'created_by_admin'), pk=driver_id)
    shift = (ActiveDrivers.objects.select_related('current_location', 'vehicle_ownership')
             .filter(driver=driver, working_end__isnull=True).first())
    balance = db.fetch_view('vw_driver_commission_balance', 'driver_id = %s', [driver.pk])[0]
    return render(request, 'GEOCAB_APP/manage/driver_detail.html', {
        'driver': driver,
        'edit_form': profile_form(Drivers, DRIVER_ADMIN_FIELDS)(instance=driver),
        'is_active': driver.status.status_text == 'active',
        'shift': shift,
        'summary': db.fetch_view('vw_driver_earnings_summary', 'driver_id = %s', [driver.pk])[0],
        'balance': balance,
        'debt': db.driver_debt_status(driver.pk),
        'vehicles': VehicleOwnership.objects.filter(driversvehicleownership__driver=driver).select_related('vehicle_type'),
        'payouts': CommissionPayouts.objects.filter(driver=driver).select_related('admin').order_by('-payout_id'),
        'payout_form': PayoutForm(initial={'amount': balance['balance'] if balance['balance'] > 0 else None}),
        'vehicle_choice': VehicleChoiceForm(allow_none=False),
        'vehicle_form': VehicleForm(prefix='v'),
    })


@role_required('admin')
@require_POST
def driver_action(request, driver_id):
    driver = get_object_or_404(Drivers.objects.select_related('status'), pk=driver_id)
    action = request.POST.get('action')
    try:
        with transaction.atomic():
            if action == 'payout':
                form = PayoutForm(request.POST)
                if not form.is_valid():
                    messages.error(request, 'Внесете валиден износ.')
                else:
                    db.record_commission_payout(driver.pk, request.person.pk, form.cleaned_data['amount'],
                                                form.cleaned_data['note'] or None)
                    messages.success(request, f'Евидентирана уплата од {form.cleaned_data["amount"]} ден.')
            elif action == 'block':
                if ActiveDrivers.objects.filter(driver=driver, working_end__isnull=True).exists():
                    db.driver_end_shift(driver.pk)  # не успева ако возачот има активно возење
                driver.status = Status.objects.get(status_tip='driver', status_text='inactive')
                driver.save(update_fields=['status'])
                messages.info(request, f'{driver.name} е блокиран и не може да се најави.')
            elif action == 'unblock':
                driver.status = Status.objects.get(status_tip='driver', status_text='active')
                driver.save(update_fields=['status'])
                messages.success(request, f'{driver.name} е повторно активен.')
            elif action == 'edit':
                form = profile_form(Drivers, DRIVER_ADMIN_FIELDS)(request.POST, instance=driver)
                if form.is_valid():
                    form.save(commit=False).save(update_fields=DRIVER_ADMIN_FIELDS)
                    messages.success(request, 'Податоците на возачот се зачувани.')
                else:
                    errors = '; '.join(f'{PROFILE_LABEL.get(k, k)}: {" ".join(v)}' for k, v in form.errors.items())
                    messages.error(request, f'Не е зачувано. {errors}')
            elif action == 'remove_vehicle':
                vehicle = get_object_or_404(VehicleOwnership, pk=request.POST.get('vehicle_id'))
                if ActiveDrivers.objects.filter(driver=driver, vehicle_ownership=vehicle, working_end__isnull=True).exists():
                    messages.error(request, f'{driver.name} моментално вози со {vehicle.plate}. Прво нека ја заврши смената.')
                else:
                    DriversVehicleOwnership.objects.filter(driver=driver, vehicle_ownership=vehicle).delete()
                    messages.info(request, f'Возилото {vehicle.plate} е отстрането од возачот (останува во базата).')
            elif action == 'set_password':
                form = AdminSetPasswordForm(request.POST)
                if form.is_valid():
                    set_password(driver, form.cleaned_data['new_password'])
                    messages.success(request, f'Поставена е нова лозинка за {driver.name}. Кажете му ја лично.')
                else:
                    messages.error(request, 'Лозинката не е прифатена: ' + ' '.join(form.errors['new_password']))
            elif action == 'add_vehicle':
                choice = VehicleChoiceForm(request.POST, allow_none=False)
                vehicle_form = VehicleForm(request.POST, prefix='v')
                if not choice.is_valid():
                    messages.error(request, 'Изберете возило.')
                elif choice.cleaned_data['vehicle_mode'] == 'new' and not vehicle_form.is_valid():
                    errors = '; '.join(e for errs in vehicle_form.errors.values() for e in errs)
                    messages.error(request, f'Возилото не е зачувано: {errors}')
                else:
                    vehicle = (vehicle_form.save() if choice.cleaned_data['vehicle_mode'] == 'new'
                               else choice.cleaned_data['existing_vehicle'])
                    DriversVehicleOwnership.objects.create(driver=driver, vehicle_ownership=vehicle)
                    messages.success(request, f'Возилото {vehicle.plate} е доделено.')
            else:
                messages.error(request, 'Непозната акција.')
    except DatabaseError as exc:
        messages.error(request, db.error_message(exc))
    return redirect('manage_driver', driver_id=driver.pk)



# ---- Возења ------------------------------------------------------------------------

ACTIVE_STATUSES = ('requested', 'accepted', 'driver_arrived', 'in_progress')
RIDE_FILTERS = {
    'active': ACTIVE_STATUSES, 'completed': ('completed',), 'cancelled': ('cancelled',), 'no_driver': ('no_driver',),
}


@role_required('admin')
def rides(request):
    where, params = ['TRUE'], []
    status = request.GET.get('status', '')
    if status in RIDE_FILTERS:
        where.append('ride_status = ANY(%s)')
        params.append(list(RIDE_FILTERS[status]))
    for key, op in (('from', '>='), ('to', '<=')):
        try:
            day = date.fromisoformat(request.GET.get(key, ''))
        except ValueError:
            continue
        where.append(f'request_time::date {op} %s')
        params.append(day)
    q = request.GET.get('q', '').strip()
    if q:
        where.append('(user_name ILIKE %s OR driver_name ILIKE %s OR vehicle_plate ILIKE %s OR ride_id::text = %s)')
        params += [f'%{q}%'] * 3 + [q]
    rows = db.fetch_view('vw_ride_details', ' AND '.join(where) + ' ORDER BY ride_id DESC', params)
    payments = {p.ride_id: p for p in Payments.objects.select_related('status')
                .filter(ride_id__in=[r['ride_id'] for r in rows]).order_by('payment_id')}
    for r in rows:
        r['payment'] = payments.get(r['ride_id'])
    querystring = request.GET.copy()
    querystring.pop('page', None)
    return render(request, 'GEOCAB_APP/manage/rides.html', {
        'page': Paginator(rows, 20).get_page(request.GET.get('page')),
        'filters': request.GET, 'status': status, 'querystring': querystring.urlencode(),
    })


@role_required('admin')
def ride_detail(request, ride_id):
    rows = db.fetch_view('vw_ride_details', 'ride_id = %s', [ride_id])
    if not rows:
        messages.error(request, f'Возењето #{ride_id} не постои.')
        return redirect('manage_rides')
    ride = rows[0]
    timeline = [(label, ride[key]) for label, key in (
        ('Побарано', 'request_time'), ('Возачот пристигна', 'pickup_time'),
        ('Возењето почна', 'start_time'), ('Возењето заврши', 'end_time')) if ride[key]]
    return render(request, 'GEOCAB_APP/manage/ride_detail.html', {
        'ride': ride,
        'is_active': ride['ride_status'] in ACTIVE_STATUSES,
        'timeline': timeline,
        'payments': Payments.objects.filter(ride_id=ride_id).select_related('status', 'payment_method').order_by('payment_id'),
        'cancellation': Cancellations.objects.filter(ride_id=ride_id).order_by('-cancellation_id').first(),
        'rejections': RideRejections.objects.filter(ride_id=ride_id).select_related('driver').order_by('rejected_at'),
        'rating': Ratings.objects.filter(ride_id=ride_id).first(),
        'notifications': Notifications.objects.filter(ride_id=ride_id).select_related('message').order_by('notification_id'),
        'reports': [dict(r, category_label=db.REPORT_CATEGORIES.get(r['category'])) for r in
                    db.fetch_view('vw_ride_reports', 'ride_id = %s ORDER BY report_id', [ride_id])],
    })


@role_required('admin')
@require_POST
def ride_action(request, ride_id):
    action = request.POST.get('action')
    try:
        with transaction.atomic():
            if action == 'cancel':
                reason = request.POST.get('reason', '').strip()
                if not reason:
                    messages.error(request, 'Внесете причина за откажувањето.')
                else:
                    db.cancel_ride(ride_id, 'ADMIN', request.person.pk, reason[:255])
                    messages.success(request, f'Возењето #{ride_id} е откажано. Корисникот е известен.')
            elif action == 'refund':
                payment = get_object_or_404(Payments, pk=request.POST.get('payment_id'), ride_id=ride_id)
                db.refund_payment(payment.pk, request.person.pk)
                messages.success(request, f'Вратени се {payment.amount} ден. на корисникот.')
            else:
                messages.error(request, 'Непозната акција.')
    except DatabaseError as exc:
        messages.error(request, db.error_message(exc))
    return redirect('manage_ride', ride_id=ride_id)


# ---- Поставки ---------------------------------------------------------------------

def _vehicle_classes():
    classes = []
    for vt in VehicleTypes.objects.select_related('vehicles_model').order_by('vehicle_type_id'):
        rule = PricingRules.objects.filter(vehicle_type=vt).order_by('tariff_id').first()
        classes.append({'type': vt, 'rule': rule,
                        'vehicles': VehicleOwnership.objects.filter(vehicle_type=vt).count()})
    return classes


@role_required('admin')
def settings_view(request):
    platform = PlatformSettings.objects.get(pk=1)
    commission_form = CommissionForm(initial={'commission_percentage': platform.commission_percentage})
    new_class_form = NewVehicleClassForm()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'commission':
            commission_form = CommissionForm(request.POST)
            if commission_form.is_valid():
                platform.commission_percentage = commission_form.cleaned_data['commission_percentage']
                platform.updated_at = datetime.now()
                platform.save()
                messages.success(request, f'Провизијата е {platform.commission_percentage}%. Важи за новите плаќања.')
                return redirect('manage_settings')
        elif action == 'price':
            vehicle_type = get_object_or_404(VehicleTypes, pk=request.POST.get('vehicle_type_id'))
            form = PriceForm(request.POST)
            if form.is_valid():
                PricingRules.objects.update_or_create(vehicle_type=vehicle_type, defaults=form.cleaned_data)
                messages.success(request, f'Цената за {vehicle_type.description} е зачувана.')
            else:
                messages.error(request, 'Цените мора да се броеви поголеми или еднакви на 0.')
            return redirect('manage_settings')
        elif action == 'new_class':
            new_class_form = NewVehicleClassForm(request.POST)
            if new_class_form.is_valid():
                c = new_class_form.cleaned_data
                with transaction.atomic():
                    model, _ = VehiclesModel.objects.get_or_create(model=c['model'].strip())
                    vehicle_type = VehicleTypes.objects.create(type_name=c['type_name'], description=c['description'],
                                                               vehicles_model=model)
                    PricingRules.objects.create(vehicle_type=vehicle_type, base_price=c['base_price'],
                                                price_per_km=c['price_per_km'])
                messages.success(request, f'Додадена е класата {c["description"]}.')
                return redirect('manage_settings')
    return render(request, 'GEOCAB_APP/manage/settings.html', {
        'platform': platform, 'commission_form': commission_form,
        'classes': _vehicle_classes(), 'new_class_form': new_class_form,
    })


# ---- Корисници -------------------------------------------------------------------------

@role_required('admin')
def users(request):
    q = request.GET.get('q', '').strip()
    qs = Users.objects.select_related('status').annotate(
        completed=Count('rides', filter=Q(rides__status__status_text='completed'), distinct=True),
        cancelled=Count('rides', filter=Q(rides__status__status_text='cancelled'), distinct=True),
    ).order_by('name')
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(username__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q))
    spent = dict(Payments.objects.filter(status__status_text='completed')
                 .values_list('ride__user_id').annotate(total=Sum('amount')))
    rows = [{'user': u, 'spent': spent.get(u.pk, 0)} for u in qs]
    return render(request, 'GEOCAB_APP/manage/users.html', {'page': Paginator(rows, 25).get_page(request.GET.get('page')), 'q': q})


@role_required('admin')
@require_POST
def user_action(request, user_id):
    user = get_object_or_404(Users, pk=user_id)
    action = request.POST.get('action')
    if action == 'set_password':
        form = AdminSetPasswordForm(request.POST)
        if form.is_valid():
            set_password(user, form.cleaned_data['new_password'])
            messages.success(request, f'Поставена е нова лозинка за {user.name}.')
        else:
            messages.error(request, 'Лозинката не е прифатена: ' + ' '.join(form.errors['new_password']))
    elif action in ('block', 'unblock'):
        user.status = Status.objects.get(status_tip='user', status_text='blocked' if action == 'block' else 'active')
        user.save(update_fields=['status'])
        messages.info(request, f'{user.name} е {"блокиран" if action == "block" else "повторно активен"}.')
    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}):
        return redirect(next_url)
    return redirect('manage_users')



@role_required('admin')
def vehicle_edit(request, vehicle_id):
    vehicle = get_object_or_404(VehicleOwnership.objects.select_related('vehicle_type'), pk=vehicle_id)
    drivers_of_vehicle = Drivers.objects.filter(driversvehicleownership__vehicle_ownership=vehicle)
    back = request.GET.get('driver') or (drivers_of_vehicle[0].pk if drivers_of_vehicle else None)
    form = VehicleForm(request.POST or None, instance=vehicle)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Возилото {vehicle.plate} е зачувано.')
        return redirect('manage_driver', driver_id=back) if back else redirect('manage_drivers')
    return render(request, 'GEOCAB_APP/manage/vehicle_edit.html', {
        'form': form, 'vehicle': vehicle, 'drivers': drivers_of_vehicle, 'back': back,
    })



# ---- Пријави на проблем ------------------------------------------------------------

@role_required('admin')
def reports(request):
    status = request.GET.get('status', 'open')
    where = "report_status = 'open'" if status == 'open' else 'TRUE'
    rows = db.fetch_view('vw_ride_reports', where + " ORDER BY (category = 'SAFETY' AND report_status = 'open') DESC, report_id DESC")
    for r in rows:
        r['category_label'] = db.REPORT_CATEGORIES.get(r['category'], r['category'])
    return render(request, 'GEOCAB_APP/manage/reports.html', {'reports': rows, 'status': status})


@role_required('admin')
@require_POST
def report_resolve(request, report_id):
    try:
        with transaction.atomic():
            db.resolve_report(report_id, request.person.pk, request.POST.get('note', '').strip()[:1000] or None)
        messages.success(request, f'Пријавата #{report_id} е означена како решена.')
    except DatabaseError as exc:
        messages.error(request, db.error_message(exc))
    return redirect(request.POST.get('next') if url_has_allowed_host_and_scheme(request.POST.get('next'), {request.get_host()}) else 'manage_reports')
