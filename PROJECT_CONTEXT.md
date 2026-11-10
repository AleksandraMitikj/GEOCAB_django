# PROJECT_CONTEXT - GEOCAB_django

Резиме на состојбата на проектот, за продолжување на работата во нов chat без објаснување од почеток.
Последно ажурирано: 10.11.2026. Git: гранка `main` = `origin/main`; последниот commit ги содржи плаќањето со картичка (7.13) и потврдата на е-пошта (7.14).
Сè во овој документ е проверено во кодот, тестовите, базата или Git во моментот на пишување.

---

## 1. Опис и цел

**GEOCAB** е веб апликација за такси **платформа** во Скопје (школски проект). GEOCAB не е класична такси
компанија: приватни лица со автомобил се пријавуваат кај GEOCAB, администраторот ги регистрира и тие возат
според правилата и цените на платформата. Од секое возење возачот добива 80%, платформата задржува 20%
(процентот се менува во поставки).

Три улоги:
- **Корисник (патник)** - бара возење на мапа, следи го возачот во живо, плаќа **во готовина или со картичка** (избира при барањето), оценува.
- **Возач** - смена со GPS, прима понуди, го вози корисникот, наплаќа готовина (должи провизија) или чека плаќање со картичка (GEOCAB му го должи неговиот дел) - пребивање.
- **Администратор** (вработен во GEOCAB) - панел `/manage/`: возачи, возила, провизија, возења, поставки, пријави.

Покрај тоа, **Django `/admin/`** (superuser од `createsuperuser`, табела `auth_user`) е за развивачот/сопственикот.
Нов GEOCAB администратор (ред во табелата `Admins`) засега се додава **само** преку Django `/admin/` → Adminss → Add.

---

## 2. Технологии и архитектура

| | |
|---|---|
| Python / Django | Python 3.14 (во `.venv`), Django 6.1.2, psycopg 3.3.6 (`requirements.txt`) |
| База | PostgreSQL 18.6 (Postgres.app на macOS) + PostGIS 3.6.3, база **`geocab_app`**, корисник `aleksandramitic` без лозинка |
| Frontend | Django шаблони, Bootstrap 5.3.3 (CDN), Leaflet 1.9.4 + OpenStreetMap (unpkg CDN) |
| Рути | OSRM демо сервер `https://router.project-osrm.org` (растојание/пат/време), Nominatim (адреса од координати) |
| Django проект / апликација | `GEOCAB` / `GEOCAB_APP` (една апликација) |

**Клучна архитектонска одлука: логиката е во базата.** Табелите, тригерите, функциите (`fn_*`), процедурите
(`sp_*`) и views (`vw_*`) се дефинирани во `database/geocab_schema.sql`. Django:
- има модели врз постоечките табели со **`managed = False`** (`GEOCAB_APP/models.py`, генерирани со `inspectdb`
  и рачно дополнети) - Django миграциите **не** креираат и не менуваат GEOCAB табели;
- ги повикува SQL функциите/процедурите преку `GEOCAB_APP/db.py` (`call_function`, `call_procedure`,
  `call_table_function`, `fetch_view`) и ги чита views;
- грешките од `RAISE EXCEPTION` ги прикажува со `db.error_message(exc)` (само `message_primary`).

Миграциите `0001`-`0004` во `GEOCAB_APP/migrations/` се само за состојбата на Django (unmanaged модели, 0 DDL).
`python manage.py migrate` креира само Django табели (auth, sessions, admin, contenttypes) во истата база.

**Табели (25)** во `geocab_app`: `status`, `vehicles_model`, `locations`, `users`, `admins`, `drivers`,
`vehicle_types`, `vehicle_ownership`, `drivers_vehicle_ownership`, `active_drivers`, `pricing_rules`, `rides`,
`ride_rejections`, `messages`, `notifications`, `payment_methods`, `platform_settings`, `payments`, `ratings`,
`cancellations`, `commission_payouts`, `ride_reports`, `driver_payouts`, `card_transactions`, `pending_registrations`.

Главни врски: `rides` → `users`, `drivers` (NULL додека нема возач), `vehicle_ownership`, `locations`
(pickup/dropoff), `status`. `payments`/`cancellations`/`ratings`/`notifications`/`ride_rejections`/`ride_reports`
→ `rides`. `active_drivers` (1 ред по возач, смена) → `drivers`, `locations` (моментална), `vehicle_ownership`.
`drivers_vehicle_ownership` = M:N возач-возило. **`vehicle_ownership` → `vehicle_types` (класа: Standard/Premium,
уникатно `description`, цена во `pricing_rules`) и → `vehicles_model` (марка и модел на возилото).** Класата нема марка. `commission_payouts` → `drivers`, `admins`.
`status` е заедничка табела по `status_tip` (user, driver, admin, ride, payment, notification, report) -
тригерот `trg_check_status_tip` го чува точниот тип по табела.

**Најава (своја, не Django auth):** `GEOCAB_APP/auth.py`. Во session: `request.session['geocab'] =
{'role': 'user'|'driver'|'admin', 'id', 'name'}`. Декоратор `@role_required(...)` секое барање го чита профилот
од базата (блокиран профил веднаш ја губи сесијата) и поставува `request.role`, `request.person`.
Лозинки: `make_password`/`check_password` во колоната `password_hash`.

---

## 3. Структура на проектот

```
GEOCAB/settings.py        поставки (база, временска зона, тунели, OSRM, контакт, тест стартер)
GEOCAB/urls.py            '' -> GEOCAB_APP.urls, 'admin/' -> Django admin
GEOCAB_APP/
  models.py               unmanaged модели (+ GeneratedField за platform_amount/driver_amount)
  mail_backend.py         ReadableConsoleEmailBackend: во развој мејлот се печати читливо во терминалот
  card_gateway.py         ДЕМО платежна порта: Luhn, тип (VISA/MASTERCARD/...), charge() -> CardResult; тест картички
  db.py                   SQL обвивки, OFFER_TIMEOUT_SECONDS = 5*60, REPORT_CATEGORIES, driver_debt_status(), MONTHS_MK
  auth.py                 најава/сесија, role_required, лозинки (set_password, reset токен со django.core.signing, 1 час)
  forms.py                Register/Login/Ride/DriverLocation/NewDriver/Vehicle/VehicleChoice/Payout/Commission/
                          Price/NewVehicleClass/ChangePassword/ForgotPassword/NewPassword/AdminSetPassword/CardPayment, profile_form()
  views.py                јавни страници, регистрација/најава, корисник (возење, историја, известувања, оценка,
                          пријава), возач (смена, акции, GPS, профил), плаќање (_unpaid, _driver_to_collect)
  account_views.py        профил („Мои податоци“), промена/заборавена/нова лозинка
  manage_views.py         админ панел /manage/ (dashboard, возачи, возила, возења, пријави, корисници, поставки)
  routing.py              road_distance_km() преку OSRM (lru_cache, SSL контекст со /etc/ssl/cert.pem, timeout 4 s)
  error_views.py          csrf_failure (CSRF_FAILURE_VIEW), MethodNotAllowedMiddleware (наша 405 наместо празна)
  context_processors.py   notifications (🔔 бројка + init за pop-up), contact (GEOCAB_CONTACT), open_reports
  admin.py                Django /admin/: read-only табели, лозинка->hash, возач преку fn_admin_create_driver
  templatetags/geocab.py  ride_status, message_label (латиница->кирилица), static_v (?v=mtime против кеш)
  management/commands/seed.py   тест податоци (повторливо); тест профили и TEST_PASSWORD на врвот на фајлот
  test_runner.py          GeocabTestRunner: во тест базата ги извршува database/geocab_schema.sql + lookup
  tests/                  base.py + 7 тест модули, error_urls.py (намерни грешки за тест) (види точка 6)
  static/GEOCAB_APP/      geocab.css, geocab_map.js (Leaflet помошник: мапи, рути, Google/Waze линкови), img/*.svg
  templates/GEOCAB_APP/   base.html (мени по улога, pop-up известувања), home, about, login, register,
                          ride_new, ride_current, ride_history, pay_card, notifications, driver_dashboard, driver_profile,
                          account/*, manage/*, partials/* (field, route_map, report_problem, debt_banner,
                          driver_location, manage/partials/vehicle_choice)
database/
  geocab_schema.sql       цела шема (25 табели, индекси, функции, тригери, процедури, views)
  geocab_lookup_data.sql  задолжителни редови: статуси (вкл. report open/resolved), CASH/CARD, 15 пораки, Platform_settings (20%)
  geocab_upgrade.sql      надградба на постара база, секции 1-8 (може да се изврши повеќе пати)
deploy/errors/50x.html    страница за nginx (502/503/504) кога контејнерот не работи - сè во еден фајл, без Django
README.md                 опис, инсталација, тестови, тест на телефон (на македонски)
requirements.txt, .gitignore (.venv, __pycache__, .idea, .DS_Store, db.sqlite3, .env)
```

SQL фајловите постојат и на `~/Desktop/geocab_fajlovi/`. **Од 10.11.2026 `geocab_schema.sql` и `geocab_upgrade.sql` на Desktop се ЗАСТАРЕНИ** (без картичка) - копирањето го прави корисничката (`cp database/geocab_schema.sql database/geocab_upgrade.sql ~/Desktop/geocab_fajlovi/`).
DataGrip проектот `~/DataGripProjects/default` е поврзан со `geocab_app@localhost` и ги отвора фајловите од Desktop.
**Главен извор е `database/` во проектот** (тестовите и Git го користат).

---

## 4. Имплементирани функционалности

### Јавно
- `/` почетна (херо со анимирано такси, бројки од базата, калкулатор на цена со вистинските цени и попуст >10 km,
  „Готовина или картичка“, секција за возачи со % од поставките, ЧПП), `/about/` (контакт од `GEOCAB_CONTACT`).
- `/register/` (само корисници) **со потврда на е-пошта**: барањето чека во `Pending_registrations`, мејл со линк
  (`/register/confirm/<токен>/`, важи 24 h, страница со копче „Потврди“ - POST), профилот во `Users` се креира дури
  по потврдата (`fn_confirm_registration`); `/register/sent/`, `/register/resend/` (нов линк, стариот не важи);
  најава пред потврда -> „Профилот уште не е активиран“. `/login/` (избор улога), одјава само со POST.
- `/password/forgot/` (линк по мејл; во развој мејлот се печати во терминалот на runserver - `console.EmailBackend`
  преку `MAILERS` во settings), `/password/reset/<token>/` (важи 1 час, еднаш).

### Корисник
- `/ride/new/`: мапа (Leaflet), две точки, адреса од Nominatim, **пат по улиците (OSRM) со km и време**,
  „Пресметај цена“ (локациите се креираат во трансакција со rollback - не остануваат), цена **по пат**
  (`fn_estimate_price(..., p_distance_km)`), ознака „по пат“/„(воздушно)“, „Побарај возење“ (`fn_request_ride`).
- `/ride/current/`: статус, **мапа во живо** (такси, пат до корисникот, зелен пат на возењето, „Возачот е на X km ·
  ~Y мин“), автоматско освежување на 5 s, откажување со приказ на казна, пријава на проблем (во тек на возење),
  по завршување: износ за готовина, статус на плаќање, оценка 1-5 + коментар, неплатени казни.
- `/rides/` историја (збир, статуси, казни, оценување од листата, 10 по страница), `/notifications/` + 🔔 бројка,
  **pop-up известувања** (горе десно, 5 s, проверка на 4 s преку `/notifications/poll/`; само нови).
- `/account/` (име, телефон, е-пошта), `/account/password/`.

### Возач
- `/driver/`: почни смена (возило + локација на мапа / „📍 Користи моја локација“), **GPS во живо** (watchPosition,
  праќа на `/driver/position/` на ~8 s или >30 m; во базата нова локација само ако >50 m), понуда со одбројување
  **5:00**, мапа со пат (возач → корисник сино, корисник → дестинација зелено испрекинато), упатства на македонски,
  **Google Maps со 3 точки** (возач → корисник → дестинација), **Waze** две копчиња (Waze поддржува 1 дестинација;
  на телефон отвора апликација, на компјутер веб со почетна точка), чекори прифати/пристигнав/почни/заврши,
  откажи се, „Корисникот не дојде“ (no-show), „Примив готовина“ (вкл. стари казни на корисникот), пријава на
  проблем, лента за долг/блокада, крај на смена, нова локација. Освежување на 4 s (паузира ако е отворен modal/панел).
- `/driver/profile/`: заработка (80%), месец/денес, поделба, по ден (14), последни плаќања, долг кон GEOCAB и уплати, возила.

### Администратор `/manage/`
- dashboard (на смена, возења во тек, приход месец, долгови, блокирани/опомена, отворени пријави, последни возења).
- возачи: листа (пребарување вкл. таблица, „само должници“, ознаки блокиран/опомена), нов возач (ново/постоечко/
  без возило), детали: уреди податоци, нова лозинка, блокирај/активирај (крај на смена; не за време на возење),
  евидентирај уплата (≤ долг), додели/уреди (`/manage/vehicles/<id>/`)/отстрани возило.
- возења: филтри (статус, датум, пребарување), детали (тек, одбивања, откажување, оценка, плаќања, известувања,
  пријави), **итно откажување** (и во тек, без казна), **враќање пари**.
- пријави (`/manage/reports/`, безбедност прва, „Решено“ + забелешка), корисници (блокирај, нова лозинка),
  поставки (провизија 0-100%, цени по класа, нова класа - description мора да е уникатен).

---

## 5. Бизнис правила и ограничувања

| Правило | Каде |
|---|---|
| Класа на возило = `Vehicle_types.description` (не `type_name`), уникатна (`uq_vehicle_types_class`); цената зависи само од класата, марката (`Vehicle_ownership.vehicles_model_id`) е само податок за возилото | `fn_estimate_price`, `fn_request_ride`, `fn_find_nearest_driver` |
| Цена = (почетна + km × по km) × 0.8 ако km ≥ 10 | `fn_price_formula` |
| km = по пат (OSRM) ако апликацијата го прати, инаку воздушно (`fn_calculate_distance_km`) | `p_distance_km` во `fn_estimate_price`, `fn_request_ride` |
| Најблизок возач: на смена, слободен, активен, ≤10 km, ја има класата, не одбил, нема друго активно возење, **не е блокиран за долг** | `fn_find_nearest_driver` |
| Понуда без одговор **5 минути** → TIMEOUT → следен возач; се проверува при секое отворање на страница на корисник/возач (нема cron) | `fn_expire_offers`, `db.OFFER_TIMEOUT_SECONDS` |
| Корисник има најмногу 1 активно возење | `fn_request_ride` |
| Премини на статус: requested→accepted/no_driver/cancelled; accepted→driver_arrived/cancelled/requested; driver_arrived→in_progress/cancelled/requested; in_progress→completed/cancelled (само ADMIN) | `trg_ride_status_transition`, `trg_apply_cancellation` |
| Чекорите ги проверува `fn_lock_driver_ride` (туѓо возење / погрешен статус → грешка) | процедури `sp_*` |
| Казна (корисник): requested 0, accepted 50% од почетната, driver_arrived 100%; no-show (возач, по пристигнување) 100%; возач/админ 0 | `fn_cancellation_fee`, `sp_cancel_ride` |
| Возач што се откажува: возењето оди кај друг возач (DRIVER_CANCELLED) | `sp_cancel_ride` |
| `cancelled_by_type` е 'USER'/'DRIVER'/'ADMIN' (големи букви) | CHECK `chk_cancelled_by_type` |
| Плаќање **готовина или картичка** (од 10.11.2026). Корисникот бира при барањето (`Rides.payment_method_id`, стандардно CASH); плаќање се креира `pending` со тој начин при completed / казна > 0. Готовина: возачот потврдува. Картичка: `/pay/card/` ги плаќа сите неплатени износи (возење + казни) преку `card_gateway.charge`; одбиена -> `failed`, може повторно или готовина. Во `Card_transactions` само тип + последни 4 цифри, никогаш цел број/CVV | `trg_create_payment`, `trg_apply_cancellation`, `sp_process_payment`, `views.pay_card` |
| **Пребивање** возач - GEOCAB: салдо = провизија од готовина - дел на возачот од картички - уплати на возачот (`Commission_payouts`) + исплати од GEOCAB (`Driver_payouts`). > 0 возачот должи (рокови/блокада како досега, заработката од картички се смета како уплата, FIFO); < 0 GEOCAB должи -> админ „Исплати на возачот“ (≤ долгот) | `vw_driver_commission_balance`, `fn_driver_debt_status`, `sp_record_driver_payout` |
| Казна се наплаќа веднаш со картичка или со **следното** завршено возење на корисникот (кој било возач); заработката од казната е на возачот на откажаното возење | `views._driver_to_collect` |
| Провизија: % од `Platform_settings` се запишува на секое ново плаќање; `platform_amount`/`driver_amount` се генерирани колони; промена важи само за нови плаќања; `refunded` не се брои | `trg_payment_commission`, `Payments` |
| Долг за месец М (по `payment_time`): 1-14 во М+1 „во рок“, од 15 во М+1 **опомена** (рок 1 во М+2), од 1 во М+2 **блокада** (не почнува смена, не добива понуди) додека не плати; уплатите покриваат најстар месец прв (FIFO) | `fn_driver_debt_status`, `sp_driver_start_shift`, `fn_find_nearest_driver` |
| Уплата на провизија: активен админ, износ > 0 и ≤ долг | `sp_record_commission_payout` |
| Пријава на проблем: само корисникот/возачот на возењето, само во accepted/driver_arrived/in_progress | `sp_report_problem`; решава `sp_resolve_report` |
| GPS: нова локација само при поместување > 50 m; `position_updated_at` се ажурира секогаш | `sp_update_driver_position` |
| Возач се креира само преку `fn_admin_create_driver` (активен админ; статус active, оценка 5.00) | апликација и Django admin |
| Оценка: само завршено возење, само корисникот; просек на возачот го пресметува тригер | `trg_validate_rating`, `trg_update_driver_rating` |
| Текстовите во `Messages` (латиница) **не смеат да се менуваат** - функциите ги бараат по текст; на екран се преведуваат | `views.MESSAGE_LABELS` |
| Телефон `^[0-9+]+$`, е-пошта `^[^@]+@[^@]+\.[^@]+$`, година ≥ 1900, цени ≥ 0 | CHECK во базата + исти валидатори во `forms.py` |
| `USE_TZ = False`, `TIME_ZONE = 'Europe/Skopje'` (сите колони се TIMESTAMP без зона) | settings |

---

## 6. Тестови

Пуштање: `python manage.py test GEOCAB_APP` (~67 s). Стартерот `GEOCAB_APP/test_runner.py` креира `test_geocab_app`,
ги извршува `database/geocab_schema.sql` и `geocab_lookup_data.sql`, на крај ја брише. `geocab_app` не се допира.
OSRM е заменет (`mock.patch('GEOCAB_APP.routing._road_km', return_value=Decimal('3.79'))` во `tests/base.py`).

**Последно пуштање (10.11.2026): 199 тестови - 199 OK, 0 FAIL, 0 ERROR** (~90 s). Нема тестови што паѓаат. (Порано 147 тестови беа пуштени и двапати со `--shuffle` - OK.)

| Модул | Класи (број тестови) |
|---|---|
| `test_accounts.py` (41) | PublicPagesTest 5, RegisterTest 16 (потврда на е-пошта), LoginTest 7, RoleProtectionTest 3, PasswordTest 6, ProfileTest 4 |
| `test_user_rides.py` (33) | EstimateTest 6, RequestRideTest 9, CancelTest 6, RatingAndHistoryTest 7, NotificationsTest 5 |
| `test_driver.py` (30) | ShiftTest 7, OfferTest 7, RideStepsTest 8, GpsTest 6, DriverProfileTest 2 |
| `test_money.py` (20) | CashPaymentTest 3, CommissionTest 4, PayoutAndDebtTest 9, ReportTest 4 |
| `test_card.py` (22) | CardGatewayTest 3, CardPaymentTest 12 (избор, плаќање, одбиена → готовина, бројот не се чува, казна со картичка, двојно плаќање), NettingTest 7 (пребивање, блокада, исплата, враќање пари, dashboard) |
| `test_errors.py` (11) | ErrorPagesTest 11 (404 по улога, 500 без база, 400 и непознат домен, 403, CSRF, 405) |
| `test_manage.py` (42) | ManageDriversTest 10, VehicleBrandTest 8, ManageRidesTest 6, ManageSettingsAndUsersTest 3, DjangoAdminTest 4, DatabaseRulesTest 6, RoutingTest 2, SeedAndTemplatesTest 2, AllLinksTest 1 (обиколка на сите линкови/форми за сите улоги) |

Тестовите **не** покриваат: JavaScript во прелистувач (мапи, GPS, pop-up, Leaflet/OSRM/Nominatim одговори) -
тие се проверени рачно во прелистувач (види точка 8).

---

## 7. Последни измени (хронолошки, најважно)

1. Поврзување со `geocab_app`, модели од `inspectdb`, `db.py`, админ ограничувања.
2. Најава/регистрација (своја), корисничко возење со мапа, возачка страница, плаќање само готовина + оценка.
3. **База**: провизија (`Platform_settings`, колони во `Payments`, `trg_payment_commission`, views
   `vw_driver_earnings_summary`), `Commission_payouts` + `vw_driver_commission_balance` + `sp_record_commission_payout`,
   `Ride_reports` + статуси report + `sp_report_problem`/`sp_resolve_report` + `vw_ride_reports`,
   `fn_driver_debt_status` + блокада во `sp_driver_start_shift` и `fn_find_nearest_driver`,
   `Active_drivers.position_updated_at` + `sp_update_driver_position`, параметар `p_distance_km`
   (старите сигнатури на `fn_estimate_price`/`fn_request_ride` се бришат во надградбата).
   Сè е и во `geocab_schema.sql` и во `geocab_upgrade.sql` (секции 1-5); `geocab_app` е надградена.
4. Админ панел (дел А и Б), историја, известувања, pop-up, лозинки, профил, пријави, месечен долг.
5. GPS, мапи со пат, Google Maps 3 точки, Waze, цена по пат.
6. Поправки откриени при тестирање/користење:
   - OpenStreetMap 403 („Access blocked“) → `referrerPolicy: 'strict-origin-when-cross-origin'` за tiles и fetch
     (Django праќа `same-origin` Referrer-Policy).
   - Python на macOS без сертификати → `routing._ssl_context()` (certifi или `/etc/ssl/cert.pem`).
   - Мапа креирана додека е скриена (зум на цел свет) → `GeocabMap.fit()` со ResizeObserver/visibilitychange.
   - Chrome кешираше стар JS → template tag `static_v` (`?v=<mtime>`) во сите шаблони.
   - Корисничката страница истекуваше понуди по 30 s (стандарден параметар) → едно место `db.OFFER_TIMEOUT_SECONDS`.
   - Скриена/расипана трансакција по одбиена DB акција → секоја акција во `transaction.atomic()`.
   - „Нов возач“ со „Постоечко возило“ без слободни возила не покажуваше грешка → поправено (`manage_views.driver_new`,
     `partials/vehicle_choice.html`) - пронајдено од тестовите.
7. Тестови (147), `database/` во проектот, README, Git + GitHub.
8. **Марката е на возилото, не на класата** (барање на корисничката, начин Б): нова колона
   `Vehicle_ownership.vehicles_model_id` (NOT NULL, FK), `Vehicle_types.vehicles_model_id` избришана,
   UNIQUE на `Vehicle_types.description`, views `vw_driver_profile`, `vw_active_driver_status`, `vw_ride_details`,
   `vw_price_list` ја земаат марката од возилото. `geocab_upgrade.sql` секција 6 (ги пренесува постоечките марки);
   `geocab_app` надградена. Django: `models.py`, `VehicleForm` (класа + поле „Марка и модел“ со `<datalist>`,
   нова марка се креира автоматски, без дупликати по големи/мали букви), `NewVehicleClassForm` без модел,
   `admin.py`, `manage_views.py`, `views._driver_vehicles`, шаблони (возач, профил, детали, поставки, field.html),
   `seed.py` (CLASSES + CARS со марка), тестови (+8 во `VehicleBrandTest`).
9. **Звук за известувања - испробан и ВРАТЕН** (23.10.2026): беше додаден `static/GEOCAB_APP/geocab_sound.js`
   (Web Audio „динг“ за корисник, повторлив звук за понуда кај возач, копче 🔊/🔇, лента „Допрете за звук“).
   Проблеми: на iPhone немаше звук (Safari отклучува звук само во `touchend`/`click`, а кодот користеше
   `pointerdown` + async; прекинувачот за тивко ги гаси веб звуците), на лаптоп само еден звук (по
   `location.reload()` новата страница е „недопрена“ и прелистувачот го блокира звукот). Корисничката одлучи
   **да нема звук** - сите промени се вратени со `git restore` и фајлот е избришан; ништо од тоа не беше во commit.
10. **`CLAUDE.md` е само локален**: во `.gitignore`; историјата е преправена (последните commit-и споени во `e9ff2da`,
    `git push --force-with-lease`) така што `CLAUDE.md` не постои во ниеден commit ни на GitHub.
11. **Пристап од телефон**: проверено дека работи преку HTTPS тунел (корисничката го проба на iPhone).
    Пристап преку домашно Wi-Fi (`http://192.168.1.184:8000`, без GPS) бара `ALLOWED_HOSTS` за `192.168.x.x` и
    `runserver 0.0.0.0:8000` - предложено, **не е направено** (корисничката го користи тунелот).
12. **Страници за грешки** (25.10.2026, во дизајнот на почетната): шаблони `GEOCAB_APP/templates/400.html`, `403.html`,
    `403_csrf.html`, `404.html`, `405.html`, `500.html` + `GEOCAB_APP/errors/hero.html` (заеднички изглед, слика
    `errors/scene_*.html`, копчиња по улога). 403/404/405/CSRF го наследуваат `base.html`; 400/500 се прикажуваат
    без request/база, затоа наследуваат `errors/bare.html`. Се гледаат само со `DEBUG = False`. Проверени во
    прелистувач (компјутер и телефон). Многуредов коментар во шаблон = `{% comment %}`, не `{# #}`.
13. **Плаќање со картичка + пребивање** (10.11.2026, барање на професорката): база секција 7 (`Rides.payment_method_id`,
    `Card_transactions`, `Driver_payouts`, `fn_request_ride(..., p_payment_tip)`, `sp_process_payment(..., p_card_brand,
    p_card_last4)`, `sp_record_driver_payout`, нови колони на крајот на `vw_ride_details`, `vw_driver_pending_requests`,
    `vw_user_ride_history`, `vw_payment_history`, `vw_driver_earnings_summary`, `vw_driver_commission_balance`,
    `vw_admin_dashboard`). Во `geocab_upgrade.sql` секциите 1, 2 и 6 сега прават `DROP VIEW IF EXISTS ... CASCADE` +
    `CREATE VIEW` за овие view-а (повторно извршување не смее да падне поради помалку колони; секција 7 ги враќа).
    Надградбата е пробана 2x на копија од бекапот и споредена со свежа инсталација (разлики само Django табели и
    стар запис на CHECK). Django: `card_gateway.py`, `CardPaymentForm`, `/pay/card/` (`sensitive_post_parameters`,
    бројот не се враќа во формата), избор на начин во `/ride/new/`, приказ кај возач/корисник/админ, „Исплати на
    возачот“, текстови на почетна/„За нас“. Демо тест картички: 4242 4242 4242 4242 (одобрено), 4000 0000 0000 0002 (одбиено).
14. **Потврда на е-пошта при регистрација** (10.11.2026, корисничката - доказ за професорката): база секција 8
    (`Pending_registrations` со hash од токенот (SHA-256) и од лозинката, `fn_create_pending_registration`,
    `fn_renew_pending_registration`, `fn_confirm_registration`); `geocab_app` надградена (бекап
    `~/geocab_backups/geocab_app_pred_potvrda_email_20261110_2307.dump`). Регистрацијата е преместена од `views.py`
    во `account_views.py`. Мејл: текст + HTML (`templates/GEOCAB_APP/emails/confirm_email.*`). Засега само во
    терминалот (одлука на корисничката); Gmail се вклучува преку env без промена на кодот. Постоечките корисници
    не се засегнати. Познато: промена на е-пошта во „Мои податоци“ уште не бара потврда.

---

## 8. Тековна состојба

**Завршено и проверено:** сите функционалности од точка 4; 199/199 тестови. GitHub:
`https://github.com/AleksandraMitikj/GEOCAB_django` (приватно), `main` = `origin/main` (види `git log`).
Push работи од овој Mac без прашање за лозинка (macOS ја памти најавата со fine-grained токенот на корисничката,
важи до 20.01.2027; токенот не е во проектот).

**Отворено, предложено, НЕ е одобрено/направено:**
- **„Возачи-духови“**: возач што ја затворил страницата останува на смена и добива понуди. Пример: `vozac_marko`
  е на смена од 22.10.2026 19:33 (повторно); на 22.10 доби понуда пред `vozac_elena` иако неговата страница
  не беше отворена ~5 часа. Предлог: без сигнал >2 мин → без понуди (`fn_find_nearest_driver`); >30 мин →
  автоматски крај на смена; „Моја смена“ праќа „жив сум“ на 30 s и без GPS. Чека одобрување.
- **Одзумирана мапа** на `/ride/current/` во статус `requested` (пред возачот да прифати): кај корисничката се
  гледаа Албанија/Косово наместо Скопје. Во тој статус се повикува само почетното `GeocabMap.fit(map, [pickup, dropoff], 0.3)`
  (`update()` не се извршува) - причината не е истражена.
- Пристап преку Wi-Fi без тунел (точка 7.11).

**Состојба на вистинската база `geocab_app` (10.11.2026):** 24 GEOCAB табели (надградена со секција 7, бекап пред тоа: `~/geocab_backups/geocab_app_pred_kartichka_20261110_2044.dump`); провизија во поставките **30%**; 7 корисници, 22 возења, 14 плаќања. На 23.10: 6 корисници (`Mit`, `korisnik_ana`,
`korisnik_petar`, `Iva`, `sasomitic`, `Sara`), 2 возачи (`vozac_marko`, `vozac_elena`), 1 админ (`admin_test`),
21 возење, 13 плаќања, 2 класи (Standard, Premium), 2 возила (SK-1234-AB Standard Škoda Octavia,
SK-5678-CD Premium Mercedes E-Class), провизија 20%. **`vozac_marko` е на смена** - тест барања со класа
„Било кое“/Standard во Скопје одат кај него ако е најблизок.

**Одлуки на корисничката (да не се менуваат без прашање):**
- плаќање **готовина или картичка** (10.11.2026, по забелешка на професорката; порано само готовина). Картичката оди преку **демо порта** (`card_gateway.py`) додека нема договор со банка; **пребивање** возач - GEOCAB;
- **нема звук за известувања** (испробан и вратен, точка 7.9);
- пораките од Django (на пр. „This field is required.“) **остануваат на англиски**;
- нов GEOCAB администратор **останува преку Django `/admin/`** (можно подоцна);
- марката/моделот е на возилото, класата е само Standard/Premium (начин Б, точка 7.8);
- Waze со една дестинација (ограничување на Waze) - две копчиња; Google Maps со 3 точки;
- `CLAUDE.md` **не смее да оди на GitHub** (само локален, во `.gitignore`);
- контакт податоците во `GEOCAB_CONTACT` (`settings.py`) се **пример** (телефон, info@geocab.mk, адреса) - не се вистински.

**Познати ограничувања (не се грешки):**
- GPS работи само додека страницата на возачот е отворена; на телефон бара HTTPS
  (тунел: `ssh -R 80:localhost:8000 nokey@localhost.run` - јавно достапно, со `DEBUG = True`).
- Корисник и возач не можат да бидат најавени во ист прозорец (иста сесија) → Incognito; најава во друг таб
  ротира CSRF токенот (стара отворена форма дава 403 - освежи).
- Chrome ги кешира JS/CSS - решено со `{% static_v %}`; по промена на шаблон што е отворен, освежи со ⌘⇧R.
- OSRM/Nominatim се бесплатни демо сервиси; без OSRM цената е воздушна.
- Истекувањето понуди нема позадинска задача - се случува при отворање/полирање на страница.
- „Моја смена“ и „Моето возење“ се освежуваат со `location.reload()` (затоа звукот не можеше да работи стабилно).
- Не е подготвено за јавен сервер: `DEBUG = True`, `SECRET_KEY` во `settings.py`, нема `STATIC_ROOT`.
- `LANGUAGE_CODE = 'en-us'`.

**Испробани начини за проверка:** привремена копија од базата (`pg_dump` → `geocab_visual_tmp`) и сервер
`GEOCAB_DB=geocab_visual_tmp python manage.py runserver 127.0.0.1:8765` за рачна проверка во прелистувач без
да се полни `geocab_app` (по проверката: `dropdb geocab_visual_tmp`); два различни хоста (`127.0.0.1` и
`localhost`) за две сесии (возач и корисник); бекапи пред секоја промена на базата (`pg_dump -Fc`).

Нема познати грешки во кодот што се откриени, а не се поправени - освен одзумираната мапа погоре (неистражена).

---

## 9. Следни чекори

Корисничката не одлучила за следната задача. Отворени можности, по редослед на важност:
0. Поставување на домашниот Debian сервер (Docker: PostGIS + gunicorn, постоечки nginx, Let's Encrypt на free DNS домен, копија од `geocab_app`; `deploy/errors/50x.html` е готов) - планот е договорен, фајловите уште не се направени.
1. „Возачи-духови“ (предлогот во точка 8) - чека одобрување.
2. Истражување и поправка на одзумираната мапа на `/ride/current/` во статус `requested`.
3. Опционално: пристап преку Wi-Fi (`ALLOWED_HOSTS` за `192.168.x.x`).
4. Опционално: управување со GEOCAB администратори во `/manage/` (отложено со одлука).
5. Опционално: подготовка за јавен сервер (`DEBUG=False`, клуч од env, `STATIC_ROOT`/collectstatic).
6. Опционално: вистински контакт податоци во `GEOCAB_CONTACT`.
6a. При продажба: вистинска платежна порта (на пр. CaSys/cPay преку банка) - се менува само `card_gateway.charge()`.
7. По секоја промена: тестови → commit → push (само со дозвола на корисничката; `CLAUDE.md` никогаш во commit).

---

## 10. Инструкции за следниот chat

**Прочитај прво:** `PROJECT_CONTEXT.md` (ова; `CLAUDE.md` постои само локално и се вчитува автоматски), `README.md`, `GEOCAB_APP/db.py`, `GEOCAB_APP/models.py`,
`GEOCAB_APP/auth.py`, па фајлот што се менува (`views.py` / `manage_views.py` / `account_views.py` / шаблонот).
За база: `database/geocab_schema.sql` (барај по име на функција/процедура).

**Пред да менуваш код провери:**
- `git status` (чисто ли е), `python manage.py check`, `python manage.py test GEOCAB_APP` (треба 166 OK).
- Дали Postgres.app работи (`pg_isready -h localhost`).

**Правила за работа во овој проект:**
- Разговорот е на **македонски**; текстот во апликацијата и коментарите во кодот се на македонски.
- Корисничката сака: прво објасни што ќе се прави, чекај потврда, па направи; по промена - тестирај и кажи јасно.
- Бизнис логиката оди во **базата** (SQL функција/процедура/тригер), Django ја повикува преку `db.py`.
- **Промена во базата** = (1) бекап `pg_dump -Fc`, (2) додај во `database/geocab_schema.sql` (свежа инсталација),
  (3) додај нова секција во `database/geocab_upgrade.sql` (idempotent: `IF NOT EXISTS`, `CREATE OR REPLACE`,
  `DROP FUNCTION IF EXISTS` при промена на сигнатура), (4) изврши ја надградбата на `geocab_app`, (5) провери
  свежа инсталација vs надградена (pg_dump -s), (6) ако е нова колона/табела - ажурирај `models.py`
  (`managed = False`) и `makemigrations` (без DDL). Копијата на Desktop (`~/Desktop/geocab_fajlovi/`) е за
  DataGrip - ажурирај ја ако корисничката ја користи.
- Секоја DB акција во view → `with transaction.atomic():` + `except DatabaseError: messages.error(request, db.error_message(exc))`.
- Нови страници: `@role_required(...)`; POST акции со `@require_POST`; шаблоните наследуваат `GEOCAB_APP/base.html`;
  JS/CSS со `{% static_v '...' %}`; полиња со `partials/field.html`.
- Нова функционалност → нов тест во `GEOCAB_APP/tests/` (користи `GeocabTestCase` од `tests/base.py`).
- Не менувај текстови во табелата `Messages`; не ги враќај одлуките од точка 8 без прашање.
- Мејлови во развој: се печатат читливо во терминалот на runserver (`mail_backend.py`). Вистински мејл: env
  `GEOCAB_SMTP_USER` + `GEOCAB_SMTP_PASSWORD` (Gmail App password, само во `.env`, никогаш во кодот/chat).
- Тест профили: `GEOCAB_APP/management/commands/seed.py` (лозинката не ја пишувај во chat/документи).
