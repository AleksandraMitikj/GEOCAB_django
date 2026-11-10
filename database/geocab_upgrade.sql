-- =====================================================================
-- GEOCAB - надградба на постоечка база
--
-- Само за база креирана ПРЕД овие промени. Нова инсталација не треба ова -
-- geocab_schema.sql и geocab_lookup_data.sql веќе ги содржат.
-- Може да се изврши повеќе пати без проблем.
--
--   1. Провизија на платформата (возачот 80%, GEOCAB 20%)
--   2. Уплати на провизија од возачите
--   3. Пријави на проблем во тек на возење
--   4. Месечен рок за провизија, опомена и блокада
--   5. GPS позиција на возачот и цена по вистински пат
--   6. Марката/моделот е на возилото, класата (Vehicle_types) е само класа со цена
--   7. Плаќање со картичка (избор при барање, демо порта) и пребивање возач - GEOCAB
--   8. Потврда на е-пошта при регистрација (профилот се креира по кликот на линкот)
-- =====================================================================

-- Поставки на платформата (секогаш точно еден ред, setting_id = 1)
CREATE TABLE IF NOT EXISTS Platform_settings (
                                   setting_id INT PRIMARY KEY DEFAULT 1,
                                   commission_percentage NUMERIC(5,2) NOT NULL DEFAULT 20,
                                   updated_at TIMESTAMP NOT NULL DEFAULT now(),
                                   CONSTRAINT chk_single_row CHECK (setting_id = 1),
                                   CONSTRAINT chk_commission CHECK (commission_percentage BETWEEN 0 AND 100)
);
INSERT INTO Platform_settings (setting_id, commission_percentage) VALUES (1, 20)
ON CONFLICT (setting_id) DO NOTHING;

-- Поделба на секое плаќање. Процентот се запишува при креирање на плаќањето,
-- па подоцнежна промена на провизијата не ги менува старите плаќања.
ALTER TABLE Payments
    ADD COLUMN IF NOT EXISTS commission_percentage NUMERIC(5,2) NOT NULL DEFAULT 0
        CONSTRAINT chk_payment_commission CHECK (commission_percentage BETWEEN 0 AND 100);
ALTER TABLE Payments
    ADD COLUMN IF NOT EXISTS platform_amount NUMERIC(10,2)
        GENERATED ALWAYS AS (ROUND(amount * commission_percentage / 100, 2)) STORED;
ALTER TABLE Payments
    ADD COLUMN IF NOT EXISTS driver_amount NUMERIC(10,2)
        GENERATED ALWAYS AS (amount - ROUND(amount * commission_percentage / 100, 2)) STORED;

-- Постоечките плаќања (креирани пред провизијата) добиваат тековна провизија
UPDATE Payments
SET commission_percentage = (SELECT commission_percentage FROM Platform_settings WHERE setting_id = 1)
WHERE commission_percentage = 0;

-- 5.12 Секое ново плаќање ја добива тековната провизија на платформата
CREATE OR REPLACE FUNCTION trg_fn_payment_commission()
    RETURNS TRIGGER AS $$
BEGIN
    NEW.commission_percentage := COALESCE(
            (SELECT commission_percentage FROM Platform_settings WHERE setting_id = 1), 0);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_payment_commission ON Payments;
CREATE TRIGGER trg_payment_commission
    BEFORE INSERT ON Payments
    FOR EACH ROW EXECUTE FUNCTION trg_fn_payment_commission();

-- Заработка по ден: на крајот се додадени делот на возачот и на платформата
CREATE OR REPLACE VIEW vw_driver_earnings AS
SELECT
    d.driver_id,
    d.name                                              AS driver_name,
    r.end_time::DATE                                    AS ride_date,
    date_trunc('month', r.end_time)::DATE               AS ride_month,
    COUNT(*)                                            AS completed_rides,
    SUM(r.distance_km)                                  AS total_km,
    SUM(r.final_price)                                  AS total_fare,
    COALESCE(SUM(p.amount) FILTER (WHERE ps.status_text = 'completed'), 0) AS paid_amount,
    COALESCE(SUM(r.final_price) FILTER (WHERE ps.status_text IS DISTINCT FROM 'completed'), 0) AS unpaid_amount,
    COALESCE(SUM(p.driver_amount) FILTER (WHERE ps.status_text = 'completed'), 0) AS driver_earnings,
    COALESCE(SUM(p.platform_amount) FILTER (WHERE ps.status_text = 'completed'), 0) AS platform_commission
FROM Rides r
         JOIN Status s       ON r.status_id = s.status_id AND s.status_text = 'completed'
         JOIN Drivers d      ON r.driver_id = d.driver_id
         LEFT JOIN Payments p ON p.ride_id  = r.ride_id
         LEFT JOIN Status ps  ON p.status_id = ps.status_id
GROUP BY d.driver_id, d.name, r.end_time::DATE, date_trunc('month', r.end_time)::DATE;

-- Вкупна заработка на возачот: возења + казни за откажување на неговите возења.
-- Возачот ја држи целата готовина, а на GEOCAB ѝ должи platform_commission.
-- DROP + CREATE: секција 7 додава колони, па при повторно извршување
-- CREATE OR REPLACE со постарата (покуса) дефиниција би паднал
DROP VIEW IF EXISTS vw_driver_earnings_summary CASCADE;
CREATE VIEW vw_driver_earnings_summary AS
SELECT
    d.driver_id,
    d.name                                                                        AS driver_name,
    COUNT(DISTINCT r.ride_id) FILTER (WHERE rs.status_text = 'completed')         AS completed_rides,
    COALESCE(SUM(r.distance_km) FILTER (WHERE rs.status_text = 'completed'
                                         AND p.payment_id IS NOT NULL), 0)        AS total_km,
    COALESCE(SUM(p.amount) FILTER (WHERE ps.status_text = 'completed'), 0)        AS total_collected,
    COALESCE(SUM(p.driver_amount) FILTER (WHERE ps.status_text = 'completed'), 0) AS driver_earnings,
    COALESCE(SUM(p.platform_amount) FILTER (WHERE ps.status_text = 'completed'), 0) AS platform_commission,
    COALESCE(SUM(p.driver_amount) FILTER (WHERE ps.status_text = 'completed'
                                           AND rs.status_text = 'cancelled'), 0)  AS cancellation_earnings,
    COALESCE(SUM(p.amount) FILTER (WHERE ps.status_text IN ('pending', 'failed')), 0) AS unpaid_amount,
    COALESCE(SUM(p.driver_amount) FILTER (WHERE ps.status_text = 'completed'
                                           AND p.payment_time >= date_trunc('month', now())), 0) AS earnings_this_month,
    COALESCE(SUM(p.driver_amount) FILTER (WHERE ps.status_text = 'completed'
                                           AND p.payment_time >= current_date), 0) AS earnings_today
FROM Drivers d
         LEFT JOIN Rides r     ON r.driver_id   = d.driver_id
         LEFT JOIN Status rs   ON r.status_id   = rs.status_id
         LEFT JOIN Payments p  ON p.ride_id     = r.ride_id
         LEFT JOIN Status ps   ON p.status_id   = ps.status_id
GROUP BY d.driver_id, d.name;

-- Админ dashboard: на крајот е додаден приходот на платформата (провизии)
-- DROP + CREATE: секција 7 додава колони, па при повторно извршување
-- CREATE OR REPLACE со постарата (покуса) дефиниција би паднал
DROP VIEW IF EXISTS vw_admin_dashboard CASCADE;
CREATE VIEW vw_admin_dashboard AS
SELECT
    r.request_time::DATE                                            AS day,
    COUNT(*)                                                        AS total_rides,
    COUNT(*) FILTER (WHERE s.status_text = 'completed')             AS completed_rides,
    COUNT(*) FILTER (WHERE s.status_text = 'cancelled')             AS cancelled_rides,
    COUNT(*) FILTER (WHERE s.status_text = 'no_driver')             AS no_driver_rides,
    COUNT(*) FILTER (WHERE c.is_no_show)                            AS no_show_rides,
    ROUND(100.0 * COUNT(*) FILTER (WHERE s.status_text = 'cancelled')
              / COUNT(*), 2)                                        AS cancel_rate_percent,
    COUNT(DISTINCT r.user_id)                                       AS active_users,
    COUNT(DISTINCT r.driver_id)                                     AS active_drivers,
    COALESCE(SUM(p.amount) FILTER (WHERE ps.status_text = 'completed'), 0) AS revenue,
    COALESCE(SUM(c.cancellation_fee), 0)                            AS cancellation_fees,
    ROUND(AVG(r.distance_km) FILTER (WHERE s.status_text = 'completed'), 2) AS avg_distance_km,
    ROUND(AVG(rt.score), 2)                                         AS avg_rating,
    COALESCE(SUM(p.platform_amount) FILTER (WHERE ps.status_text = 'completed'), 0) AS platform_revenue
FROM Rides r
         JOIN Status s          ON r.status_id = s.status_id
         LEFT JOIN Payments p   ON p.ride_id   = r.ride_id
         LEFT JOIN Status ps    ON p.status_id = ps.status_id
         LEFT JOIN Ratings rt   ON rt.ride_id  = r.ride_id
         LEFT JOIN Cancellations c ON c.ride_id = r.ride_id
GROUP BY r.request_time::DATE;


-- =====================================================================
-- Уплати на провизија од возачите кон GEOCAB
-- (возачот ја држи готовината, админот евидентира кога ќе ја уплати провизијата)
-- =====================================================================

CREATE TABLE IF NOT EXISTS Commission_payouts (
                                    payout_id SERIAL PRIMARY KEY,
                                    driver_id INT NOT NULL,
                                    admin_id INT NOT NULL,                 -- кој администратор ја примил уплатата
                                    amount NUMERIC(10,2) NOT NULL,
                                    paid_at TIMESTAMP NOT NULL DEFAULT now(),
                                    note VARCHAR(255),
                                    CONSTRAINT chk_payout_amount CHECK (amount > 0),
                                    CONSTRAINT FK_Commission_payouts_Drivers
                                        FOREIGN KEY (driver_id) REFERENCES Drivers(driver_id) ON DELETE RESTRICT,
                                    CONSTRAINT FK_Commission_payouts_Admins
                                        FOREIGN KEY (admin_id) REFERENCES Admins(admins_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_commission_payouts_driver ON Commission_payouts(driver_id);

-- Колку провизија должи секој возач: провизија од платените плаќања - уплатено.
-- balance < 0 значи дека возачот уплатил повеќе (на пр. по враќање пари на корисник).
-- DROP + CREATE: секција 7 додава колони, па при повторно извршување
-- CREATE OR REPLACE со постарата (покуса) дефиниција би паднал
DROP VIEW IF EXISTS vw_driver_commission_balance CASCADE;
CREATE VIEW vw_driver_commission_balance AS
SELECT
    d.driver_id,
    d.name                                                  AS driver_name,
    COALESCE(c.total_commission, 0)                         AS total_commission,
    COALESCE(p.total_paid, 0)                               AS total_paid,
    COALESCE(c.total_commission, 0) - COALESCE(p.total_paid, 0) AS balance,
    p.last_payout_at
FROM Drivers d
         LEFT JOIN (SELECT r.driver_id, SUM(pay.platform_amount) AS total_commission
                    FROM Payments pay
                             JOIN Rides r  ON r.ride_id    = pay.ride_id
                             JOIN Status s ON s.status_id  = pay.status_id
                    WHERE s.status_text = 'completed'
                    GROUP BY r.driver_id) c ON c.driver_id = d.driver_id
         LEFT JOIN (SELECT driver_id, SUM(amount) AS total_paid, MAX(paid_at) AS last_payout_at
                    FROM Commission_payouts
                    GROUP BY driver_id) p ON p.driver_id = d.driver_id;

-- 6.14 Администраторот евидентира уплата на провизија од возач
CREATE OR REPLACE PROCEDURE sp_record_commission_payout(
    p_driver_id INT,
    p_admin_id INT,
    p_amount NUMERIC,
    p_note VARCHAR DEFAULT NULL
) AS $$
DECLARE
    v_balance NUMERIC;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM Admins
                   WHERE admins_id = p_admin_id
                     AND status_id = fn_get_status_id('admin', 'active')) THEN
        RAISE EXCEPTION 'Администраторот % не постои или не е активен', p_admin_id;
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'Износот мора да е поголем од 0';
    END IF;

    -- заклучи го возачот за две истовремени уплати да не го надминат долгот
    PERFORM 1 FROM Drivers WHERE driver_id = p_driver_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Возачот % не постои', p_driver_id;
    END IF;
    SELECT balance INTO v_balance FROM vw_driver_commission_balance WHERE driver_id = p_driver_id;
    IF p_amount > v_balance THEN
        RAISE EXCEPTION 'Износот % е поголем од долгот на возачот (% ден.)', p_amount, v_balance;
    END IF;

    INSERT INTO Commission_payouts (driver_id, admin_id, amount, paid_at, note)
    VALUES (p_driver_id, p_admin_id, p_amount, now(), p_note);
END;
$$ LANGUAGE plpgsql;



-- =====================================================================
-- 3. Пријави на проблем во тек на возење (од корисник или возач)
-- =====================================================================

INSERT INTO Status (status_tip, status_text) VALUES ('report', 'open'), ('report', 'resolved')
ON CONFLICT (status_tip, status_text) DO NOTHING;

CREATE TABLE IF NOT EXISTS Ride_reports (
                              report_id SERIAL PRIMARY KEY,
                              ride_id INT NOT NULL,
                              reported_by_type VARCHAR(255) NOT NULL,    -- USER / DRIVER
                              reported_by_id INT NOT NULL,
                              category VARCHAR(255) NOT NULL,
                              message VARCHAR(1000) NOT NULL,
                              created_at TIMESTAMP NOT NULL DEFAULT now(),
                              status_id INT NOT NULL,
                              resolved_by_admin_id INT,
                              resolved_at TIMESTAMP,
                              admin_note VARCHAR(1000),
                              CONSTRAINT chk_report_by CHECK (reported_by_type IN ('USER', 'DRIVER')),
                              CONSTRAINT chk_report_category CHECK (category IN ('SAFETY', 'BEHAVIOR', 'VEHICLE', 'ROUTE', 'PRICE', 'OTHER')),
                              CONSTRAINT FK_Ride_reports_Rides
                                  FOREIGN KEY (ride_id) REFERENCES Rides(ride_id) ON DELETE CASCADE,
                              CONSTRAINT FK_Ride_reports_Status
                                  FOREIGN KEY (status_id) REFERENCES Status(status_id) ON DELETE RESTRICT,
                              CONSTRAINT FK_Ride_reports_Admins
                                  FOREIGN KEY (resolved_by_admin_id) REFERENCES Admins(admins_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_ride_reports_ride ON Ride_reports(ride_id);

DROP TRIGGER IF EXISTS trg_check_status_tip ON Ride_reports;
CREATE TRIGGER trg_check_status_tip BEFORE INSERT OR UPDATE OF status_id ON Ride_reports
    FOR EACH ROW EXECUTE FUNCTION trg_fn_check_status_tip('report');

-- 6.15 Корисникот или возачот пријавува проблем за возење што е во тек
CREATE OR REPLACE PROCEDURE sp_report_problem(
    p_ride_id INT,
    p_reported_by_type VARCHAR,
    p_reported_by_id INT,
    p_category VARCHAR,
    p_message VARCHAR
) AS $$
DECLARE
    v_ride Rides%ROWTYPE;
BEGIN
    SELECT * INTO v_ride FROM Rides WHERE ride_id = p_ride_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Возењето % не постои', p_ride_id;
    END IF;
    IF p_reported_by_type = 'USER' AND v_ride.user_id <> p_reported_by_id
        OR p_reported_by_type = 'DRIVER' AND v_ride.driver_id IS DISTINCT FROM p_reported_by_id THEN
        RAISE EXCEPTION 'Може да пријавите проблем само за ваше возење';
    END IF;
    IF fn_get_status_text(v_ride.status_id) NOT IN ('accepted', 'driver_arrived', 'in_progress') THEN
        RAISE EXCEPTION 'Проблем може да се пријави само додека возењето е во тек';
    END IF;
    IF trim(COALESCE(p_message, '')) = '' THEN
        RAISE EXCEPTION 'Опишете го проблемот';
    END IF;

    INSERT INTO Ride_reports (ride_id, reported_by_type, reported_by_id, category, message, created_at, status_id)
    VALUES (p_ride_id, p_reported_by_type, p_reported_by_id, p_category, trim(p_message), now(),
            fn_get_status_id('report', 'open'));
END;
$$ LANGUAGE plpgsql;

-- 6.16 Администраторот ја означува пријавата како решена
CREATE OR REPLACE PROCEDURE sp_resolve_report(p_report_id INT, p_admin_id INT, p_note VARCHAR DEFAULT NULL) AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM Admins
                   WHERE admins_id = p_admin_id
                     AND status_id = fn_get_status_id('admin', 'active')) THEN
        RAISE EXCEPTION 'Администраторот % не постои или не е активен', p_admin_id;
    END IF;
    UPDATE Ride_reports
    SET status_id            = fn_get_status_id('report', 'resolved'),
        resolved_by_admin_id = p_admin_id,
        resolved_at          = now(),
        admin_note           = p_note
    WHERE report_id = p_report_id
      AND status_id = fn_get_status_id('report', 'open');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Пријавата % не постои или е веќе решена', p_report_id;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Пријави со имиња на корисникот, возачот и пријавувачот
CREATE OR REPLACE VIEW vw_ride_reports AS
SELECT
    rr.report_id,
    rr.ride_id,
    rr.reported_by_type,
    rr.reported_by_id,
    CASE rr.reported_by_type WHEN 'USER' THEN u.name ELSE d.name END  AS reporter_name,
    CASE rr.reported_by_type WHEN 'USER' THEN u.phone ELSE d.phone END AS reporter_phone,
    rr.category,
    rr.message,
    rr.created_at,
    s.status_text                                                      AS report_status,
    rr.resolved_at,
    a.name                                                             AS resolved_by,
    rr.admin_note,
    r.user_id,
    u.name                                                             AS user_name,
    r.driver_id,
    d.name                                                             AS driver_name,
    vo.plate                                                           AS vehicle_plate,
    rs.status_text                                                     AS ride_status
FROM Ride_reports rr
         JOIN Status s              ON rr.status_id = s.status_id
         JOIN Rides r               ON rr.ride_id   = r.ride_id
         JOIN Status rs             ON r.status_id  = rs.status_id
         JOIN Users u               ON r.user_id    = u.user_id
         LEFT JOIN Drivers d        ON r.driver_id  = d.driver_id
         LEFT JOIN Vehicle_ownership vo ON r.vehicle_ownership_id = vo.vehicle_ownership_id
         LEFT JOIN Admins a         ON rr.resolved_by_admin_id = a.admins_id;



-- =====================================================================
-- 4. Месечен рок за провизија: опомена од 15-ти, блокада од 1-ви следниот месец
-- =====================================================================

-- Долг на возачот кон GEOCAB по месеци (уплатите прво го покриваат најстариот месец).
-- Провизијата за месец М се плаќа од 1 до 15 во месец М+1:
--   due     - од 1 до 14 во М+1: во рок за плаќање
--   warning - од 15 во М+1: опомена, последен рок е 1 во М+2
--   overdue - од 1 во М+2: возачот е блокиран (не може да почне смена ни да добива понуди) додека не плати
-- p_today е за тестирање; во апликацијата секогаш е денешниот датум.
CREATE OR REPLACE FUNCTION fn_driver_debt_status(p_driver_id INT, p_today DATE DEFAULT current_date)
    RETURNS TABLE(
                     total_commission NUMERIC,
                     total_paid       NUMERIC,
                     due_amount       NUMERIC,
                     due_month        DATE,
                     warning_amount   NUMERIC,
                     warning_month    DATE,
                     overdue_amount   NUMERIC,
                     overdue_month    DATE,
                     is_blocked       BOOLEAN
                 ) AS $$
WITH months AS (
    SELECT date_trunc('month', p.payment_time)::DATE AS m, SUM(p.platform_amount) AS c
    FROM Payments p
             JOIN Rides r  ON r.ride_id   = p.ride_id
             JOIN Status s ON s.status_id = p.status_id
    WHERE r.driver_id = p_driver_id
      AND s.status_text = 'completed'
    GROUP BY 1
), paid AS (
    SELECT COALESCE(SUM(amount), 0) AS total FROM Commission_payouts WHERE driver_id = p_driver_id
), staged AS (
    SELECT m,
           LEAST(c, GREATEST(0, SUM(c) OVER (ORDER BY m) - (SELECT total FROM paid))) AS left_to_pay,
           CASE WHEN p_today >= (m + INTERVAL '2 months')::DATE                    THEN 'overdue'
                WHEN p_today >= (m + INTERVAL '1 month' + INTERVAL '14 days')::DATE THEN 'warning'
                WHEN p_today >= (m + INTERVAL '1 month')::DATE                     THEN 'due'
                ELSE 'current' END AS stage
    FROM months
)
SELECT
    COALESCE((SELECT SUM(c) FROM months), 0),
    (SELECT total FROM paid),
    COALESCE((SELECT SUM(left_to_pay) FROM staged WHERE stage = 'due'), 0),
    (SELECT MIN(m) FROM staged WHERE stage = 'due' AND left_to_pay > 0),
    COALESCE((SELECT SUM(left_to_pay) FROM staged WHERE stage = 'warning'), 0),
    (SELECT MIN(m) FROM staged WHERE stage = 'warning' AND left_to_pay > 0),
    COALESCE((SELECT SUM(left_to_pay) FROM staged WHERE stage = 'overdue'), 0),
    (SELECT MIN(m) FROM staged WHERE stage = 'overdue' AND left_to_pay > 0),
    EXISTS (SELECT 1 FROM staged WHERE stage = 'overdue' AND left_to_pay > 0);
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION fn_find_nearest_driver(
    p_pickup_location_id INT,
    p_ride_id INT DEFAULT NULL,
    p_vehicle_class VARCHAR DEFAULT NULL,
    p_limit INT DEFAULT 1
)
    RETURNS TABLE(
                     driver_id        INT,
                     driver_name      VARCHAR,
                     driver_phone     VARCHAR,
                     driver_rating    NUMERIC,
                     vehicle_plate    VARCHAR,
                     vehicle_class    VARCHAR,
                     distance_meters  NUMERIC
                 ) AS $$
#variable_conflict use_column
DECLARE
    v_pickup_geom GEOGRAPHY;
BEGIN
    -- Земи го geom од pickup локацијата
    SELECT geom INTO v_pickup_geom
    FROM Locations
    WHERE location_id = p_pickup_location_id;
    IF v_pickup_geom IS NULL THEN
        RAISE EXCEPTION 'Локацијата со ID % не постои', p_pickup_location_id;
    END IF;
    RETURN QUERY
        SELECT
            d.driver_id,
            d.name,
            d.phone,
            d.rating,
            vo.plate,
            vt.description,
            ROUND(ST_Distance(l.geom, v_pickup_geom)::NUMERIC, 2) AS distance_meters
        FROM Active_drivers ad
                 JOIN Drivers d            ON ad.driver_id              = d.driver_id
                 JOIN Locations l          ON ad.current_location_id    = l.location_id
                 JOIN Vehicle_ownership vo ON ad.vehicle_ownership_id   = vo.vehicle_ownership_id
                 JOIN Vehicle_types vt     ON vo.vehicle_type_id        = vt.vehicle_type_id
        WHERE ad.is_available = true
          AND ad.working_end IS NULL
          AND d.status_id = fn_get_status_id('driver', 'active')
          AND (p_vehicle_class IS NULL OR vt.description = p_vehicle_class)
          -- не му нудиме повторно на возач што веќе одбил
          AND NOT EXISTS (SELECT 1 FROM Ride_rejections rr
                          WHERE rr.ride_id = p_ride_id AND rr.driver_id = ad.driver_id)
          -- возачот нема друго понудено или активно возење
          AND NOT EXISTS (SELECT 1 FROM Rides r
                                            JOIN Status s ON r.status_id = s.status_id
                          WHERE r.driver_id = ad.driver_id
                            AND r.ride_id IS DISTINCT FROM p_ride_id
                            AND s.status_tip = 'ride'
                            AND s.status_text IN ('requested', 'accepted', 'driver_arrived', 'in_progress'))
          -- возач блокиран поради неплатена провизија не добива понуди
          AND NOT (SELECT ds.is_blocked FROM fn_driver_debt_status(d.driver_id) ds)
          AND ST_DWithin(l.geom, v_pickup_geom, 10000)  -- Само во радиус од 10km
        ORDER BY l.geom <-> v_pickup_geom               -- KNN сортирање, најблиску прво
        LIMIT p_limit;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE PROCEDURE sp_driver_start_shift(
    p_driver_id INT,
    p_location_id INT,
    p_vehicle_ownership_id INT
) AS $$
DECLARE
    v_debt RECORD;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM Drivers
                   WHERE driver_id = p_driver_id
                     AND status_id = fn_get_status_id('driver', 'active')) THEN
        RAISE EXCEPTION 'Возачот % не постои или не е активен', p_driver_id;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM Drivers_Vehicle_ownership
                   WHERE driver_id = p_driver_id
                     AND Vehicle_ownership_id = p_vehicle_ownership_id) THEN
        RAISE EXCEPTION 'Возилото % не е на возачот %', p_vehicle_ownership_id, p_driver_id;
    END IF;
    IF EXISTS (SELECT 1 FROM Active_drivers
               WHERE vehicle_ownership_id = p_vehicle_ownership_id
                 AND working_end IS NULL
                 AND driver_id <> p_driver_id) THEN
        RAISE EXCEPTION 'Возилото % веќе го вози друг возач во моментов', p_vehicle_ownership_id;
    END IF;
    SELECT * INTO v_debt FROM fn_driver_debt_status(p_driver_id);
    IF v_debt.is_blocked THEN
        RAISE EXCEPTION 'Блокирани сте поради неплатена провизија од % ден. (за %). Уплатете ја кај GEOCAB за да можете повторно да возите.',
            v_debt.overdue_amount, to_char(v_debt.overdue_month, 'MM.YYYY');
    END IF;

    INSERT INTO Active_drivers (driver_id, current_location_id, is_available,
                                working_start, working_end, vehicle_ownership_id)
    VALUES (p_driver_id, p_location_id, true, now(), NULL, p_vehicle_ownership_id)
    ON CONFLICT (driver_id) DO UPDATE
        SET current_location_id  = EXCLUDED.current_location_id,
            is_available         = true,
            working_start        = EXCLUDED.working_start,
            working_end          = NULL,
            vehicle_ownership_id = EXCLUDED.vehicle_ownership_id;
END;
$$ LANGUAGE plpgsql;



-- =====================================================================
-- 5. GPS позиција на возачот и цена по вистински пат (растојание по улиците)
-- =====================================================================

ALTER TABLE Active_drivers ADD COLUMN IF NOT EXISTS position_updated_at TIMESTAMP;

-- 6.3a GPS позиција на возачот (на неколку секунди од прелистувачот додека е на смена).
--      Нова локација се запишува само ако возачот се поместил повеќе од 50 m,
--      за табелата Locations да не расте со секое јавување.
CREATE OR REPLACE PROCEDURE sp_update_driver_position(p_driver_id INT, p_lat NUMERIC, p_lng NUMERIC) AS $$
DECLARE
    v_location_id INT;
    v_current     Locations%ROWTYPE;
BEGIN
    IF p_lat NOT BETWEEN -90 AND 90 OR p_lng NOT BETWEEN -180 AND 180 THEN
        RAISE EXCEPTION 'Невалидна GPS позиција';
    END IF;
    SELECT l.* INTO v_current
    FROM Active_drivers ad
             JOIN Locations l ON l.location_id = ad.current_location_id
    WHERE ad.driver_id = p_driver_id
      AND ad.working_end IS NULL
        FOR UPDATE OF ad;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Возачот % нема отворена смена', p_driver_id;
    END IF;

    IF ST_Distance(v_current.geom, ST_SetSRID(ST_MakePoint(p_lng, p_lat), 4326)::GEOGRAPHY) > 50 THEN
        v_location_id := fn_create_location(p_lat, p_lng, v_current.grad, v_current.naselba, 'GPS позиција', '');
        UPDATE Active_drivers
        SET current_location_id = v_location_id, position_updated_at = now()
        WHERE driver_id = p_driver_id;
    ELSE
        UPDATE Active_drivers SET position_updated_at = now() WHERE driver_id = p_driver_id;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- нов параметар p_distance_km -> старите верзии на функциите се бришат
DROP FUNCTION IF EXISTS fn_estimate_price(INT, INT, VARCHAR);
DROP FUNCTION IF EXISTS fn_request_ride(INT, INT, INT, VARCHAR);

-- Проценета цена ПРЕД нарачка, по класа на возило (NULL = сите класи).
-- p_distance_km = растојание по улиците (OSRM); ако е NULL се користи воздушното растојание.
CREATE OR REPLACE FUNCTION fn_estimate_price(
    p_pickup_location_id INT,
    p_dropoff_location_id INT,
    p_vehicle_class VARCHAR DEFAULT NULL,
    p_distance_km NUMERIC DEFAULT NULL       -- растојание по пат (од апликацијата); NULL = воздушно
)
    RETURNS TABLE(
                     vehicle_class       VARCHAR,
                     distance_km         NUMERIC,
                     base_price          NUMERIC,
                     price_per_km        NUMERIC,
                     discount_percentage NUMERIC,
                     estimated_price     NUMERIC
                 ) AS $$
DECLARE
    v_km NUMERIC := COALESCE(p_distance_km, fn_calculate_distance_km(p_pickup_location_id, p_dropoff_location_id));
BEGIN
    RETURN QUERY
        SELECT t.description, v_km, t.base_price, t.price_per_km,
               f.discount_percentage, f.final_price
        FROM (SELECT DISTINCT vt.description, pr.base_price, pr.price_per_km
              FROM Vehicle_types vt
                       JOIN Pricing_rules pr ON pr.vehicle_type_id = vt.vehicle_type_id
              WHERE p_vehicle_class IS NULL OR vt.description = p_vehicle_class) t,
             fn_price_formula(t.base_price, t.price_per_km, v_km) f
        ORDER BY f.final_price;
END;
$$ LANGUAGE plpgsql STABLE;

-- 6.5 Корисникот нарачува возење. Враќа ride_id.
CREATE OR REPLACE FUNCTION fn_request_ride(
    p_user_id INT,
    p_pickup_location_id INT,
    p_dropoff_location_id INT,
    p_vehicle_class VARCHAR DEFAULT NULL,
    p_distance_km NUMERIC DEFAULT NULL       -- растојание по пат (од апликацијата); NULL = воздушно
)
    RETURNS INT AS $$
DECLARE
    v_ride_id INT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM Users
                   WHERE User_id = p_user_id
                     AND status_id = fn_get_status_id('user', 'active')) THEN
        RAISE EXCEPTION 'Корисникот % не постои или не е активен', p_user_id;
    END IF;
    IF EXISTS (SELECT 1 FROM Rides r
                                 JOIN Status s ON r.status_id = s.status_id
               WHERE r.user_id = p_user_id
                 AND s.status_text IN ('requested', 'accepted', 'driver_arrived', 'in_progress')) THEN
        RAISE EXCEPTION 'Корисникот % веќе има активно возење', p_user_id;
    END IF;
    IF p_vehicle_class IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM Vehicle_types WHERE description = p_vehicle_class) THEN
        RAISE EXCEPTION 'Непозната класа на возило: %', p_vehicle_class;
    END IF;
    IF p_distance_km IS NOT NULL AND p_distance_km < 0 THEN
        RAISE EXCEPTION 'Растојанието не може да е негативно';
    END IF;

    INSERT INTO Rides (user_id, pickup_location_id, dropoff_location_id, status_id,
                       requested_vehicle_class, request_time, distance_km, discount_percentage)
    VALUES (p_user_id, p_pickup_location_id, p_dropoff_location_id,
            fn_get_status_id('ride', 'requested'), p_vehicle_class, now(),
            COALESCE(p_distance_km, fn_calculate_distance_km(p_pickup_location_id, p_dropoff_location_id)), 0)
    RETURNING ride_id INTO v_ride_id;

    PERFORM fn_assign_driver(v_ride_id);
    RETURN v_ride_id;
END;
$$ LANGUAGE plpgsql;



-- =====================================================================
-- 6. Марката/моделот се преместува од класата (Vehicle_types) на возилото (Vehicle_ownership).
--    Класата (Standard, Premium) ја одредува цената; марката е податок за конкретното возило.
-- =====================================================================

ALTER TABLE Vehicle_ownership ADD COLUMN IF NOT EXISTS vehicles_model_id INT;

-- секое постоечко возило ја добива марката што досега ја имаше неговата класа
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'vehicle_types' AND column_name = 'vehicles_model_id') THEN
        UPDATE Vehicle_ownership vo
        SET vehicles_model_id = vt.vehicles_model_id
        FROM Vehicle_types vt
        WHERE vo.vehicle_type_id = vt.vehicle_type_id
          AND vo.vehicles_model_id IS NULL;
    END IF;
END $$;

ALTER TABLE Vehicle_ownership ALTER COLUMN vehicles_model_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_vehicle_ownership_model') THEN
        ALTER TABLE Vehicle_ownership
            ADD CONSTRAINT FK_Vehicle_ownership_Model
                FOREIGN KEY (vehicles_model_id) REFERENCES Vehicles_model(Vehicles_model_id)
                    ON DELETE RESTRICT ON UPDATE CASCADE;
    END IF;
END $$;

-- views ја земаат марката од возилото
CREATE OR REPLACE VIEW vw_driver_profile AS
SELECT
    d.driver_id,
    d.name                                  AS driver_name,
    d.phone                                 AS driver_phone,
    d.rating                                AS driver_rating,
    d.registration_date,
    -- Dali e momentalno aktiven/dostapen
    ad.is_available,
    ad.working_start,
    ad.working_end,
    -- Momentalna lokacija
    l.grad                                  AS current_grad,
    l.naselba                               AS current_naselba,
    l.ulica                                 AS current_ulica,
    l.latitude                              AS current_lat,
    l.longitude                             AS current_lng,
    -- Vozilo
    vo.Vehicle_ownership_id,
    vo.Plate                                AS vehicle_plate,
    vo.Color                                AS vehicle_color,
    vo.year                                 AS vehicle_year,
    -- Tip i brend
    vm.Model                                AS vehicle_brand,
    vt.type_name                            AS vehicle_model,
    vt.description                          AS vehicle_class,   -- Economy / Luxury
    -- Cena za ovoj vozac
    pr.base_price,
    pr.price_per_km,
    -- Status na vozacot
    s.status_text                           AS driver_status
FROM Drivers d
         JOIN Status s                       ON d.status_id              = s.status_id
         LEFT JOIN Active_drivers ad         ON d.driver_id              = ad.driver_id
         LEFT JOIN Locations l               ON ad.current_location_id   = l.location_id
         LEFT JOIN Vehicle_ownership vo      ON ad.vehicle_ownership_id  = vo.Vehicle_ownership_id
         LEFT JOIN Vehicle_types vt          ON vo.vehicle_type_id       = vt.vehicle_type_id
         LEFT JOIN Vehicles_model vm         ON vo.vehicles_model_id     = vm.Vehicles_model_id
         LEFT JOIN Pricing_rules pr          ON vt.vehicle_type_id       = pr.vehicle_type_id
WHERE s.status_text = 'active' and ad.is_available is true;

CREATE OR REPLACE VIEW vw_active_driver_status
            (driver_id, driver_name, driver_phone, driver_rating, is_available, working_start,
             current_location_id, grad, naselba, current_street, vehicle_plate, vehicle_color,
             vehicle_model, vehicle_class, vehicle_brand, base_price, price_per_km, driver_status)
AS
SELECT d.driver_id,
       d.name         AS driver_name,
       d.phone        AS driver_phone,
       d.rating       AS driver_rating,
       ad.is_available,
       ad.working_start,
       l.location_id  AS current_location_id,
       l.grad,
       l.naselba,
       l.ulica        AS current_street,
       vo.plate       AS vehicle_plate,
       vo.color       AS vehicle_color,
       vt.type_name   AS vehicle_model,
       vt.description AS vehicle_class,
       vm.model       AS vehicle_brand,
       pr.base_price,
       pr.price_per_km,
       s.status_text  AS driver_status
FROM active_drivers ad
         JOIN drivers d ON ad.driver_id = d.driver_id
         JOIN locations l ON ad.current_location_id = l.location_id
         JOIN status s ON d.status_id = s.status_id
         LEFT JOIN vehicle_ownership vo ON ad.vehicle_ownership_id = vo.vehicle_ownership_id
         LEFT JOIN vehicle_types vt ON vo.vehicle_type_id = vt.vehicle_type_id
         LEFT JOIN vehicles_model vm ON vo.vehicles_model_id = vm.vehicles_model_id
         LEFT JOIN pricing_rules pr ON vt.vehicle_type_id = pr.vehicle_type_id
WHERE s.status_text::text = 'active'::text
  AND ad.working_end IS NULL;

-- DROP + CREATE: секција 7 додава колони, па при повторно извршување
-- CREATE OR REPLACE со постарата (покуса) дефиниција би паднал
DROP VIEW IF EXISTS vw_ride_details CASCADE;
CREATE VIEW vw_ride_details AS
SELECT
    r.ride_id,
    -- Korisnik
    u.user_id,
    u.name                                  AS user_name,
    u.phone                                 AS user_phone,
    -- Vozac
    d.driver_id,
    d.name                                  AS driver_name,
    d.phone                                 AS driver_phone,
    d.rating                                AS driver_rating,
    -- Avto
    vo.plate       AS vehicle_plate,
    vo.color       AS vehicle_color,
    vt.type_name   AS vehicle_model,
    vt.description AS vehicle_class,
    vm.model       AS vehicle_brand,
    -- Pickup lokacija
    lp.grad                                 AS pickup_grad,
    lp.naselba                              AS pickup_naselba,
    lp.ulica                                AS pickup_ulica,
    lp.broj                                 AS pickup_broj,
    lp.latitude                             AS pickup_lat,
    lp.longitude                            AS pickup_lng,
    -- Dropoff lokacija
    ld.grad                                 AS dropoff_grad,
    ld.naselba                              AS dropoff_naselba,
    ld.ulica                                AS dropoff_ulica,
    ld.broj                                 AS dropoff_broj,
    ld.latitude                             AS dropoff_lat,
    ld.longitude                            AS dropoff_lng,
    -- Vremijna sekvenca
    r.request_time,
    r.pickup_time,
    r.start_time,
    r.end_time,
    -- Cena i distanca
    r.distance_km,
    r.discount_percentage,
    r.final_price,
    -- Status na voznata
    s.status_text                           AS ride_status
FROM Rides r
         LEFT JOIN Users u        ON r.user_id                = u.user_id
         LEFT JOIN Drivers d      ON r.driver_id              = d.driver_id
         JOIN Locations lp   ON r.pickup_location_id     = lp.location_id
         JOIN Locations ld   ON r.dropoff_location_id    = ld.location_id
         JOIN Status s       ON r.status_id              = s.status_id
         LEFT JOIN vehicle_ownership vo ON r.vehicle_ownership_id = vo.vehicle_ownership_id
         LEFT JOIN vehicle_types vt ON vo.vehicle_type_id = vt.vehicle_type_id
         LEFT JOIN vehicles_model vm ON vo.vehicles_model_id = vm.vehicles_model_id;

CREATE OR REPLACE VIEW vw_price_list AS
SELECT
    vt.description                          AS vehicle_class,
    pr.base_price,
    pr.price_per_km,
    COUNT(DISTINCT vo.vehicles_model_id)    AS models_count,     -- марки/модели на возилата во класата
    string_agg(DISTINCT vm.Model, ', ')     AS brands
FROM Vehicle_types vt
         JOIN Pricing_rules pr       ON pr.vehicle_type_id  = vt.vehicle_type_id
         LEFT JOIN Vehicle_ownership vo ON vo.vehicle_type_id = vt.vehicle_type_id
         LEFT JOIN Vehicles_model vm ON vo.vehicles_model_id = vm.Vehicles_model_id
GROUP BY vt.description, pr.base_price, pr.price_per_km;


-- класата повеќе нема марка
ALTER TABLE Vehicle_types DROP COLUMN IF EXISTS vehicles_model_id;

-- класата е уникатна (функциите ја бараат по description)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_vehicle_types_class') THEN
        IF EXISTS (SELECT description FROM Vehicle_types GROUP BY description HAVING COUNT(*) > 1) THEN
            RAISE EXCEPTION 'Има повеќе класи со исто име во Vehicle_types - спојте ги пред надградбата';
        END IF;
        ALTER TABLE Vehicle_types ADD CONSTRAINT uq_vehicle_types_class UNIQUE (description);
    END IF;
END $$;

-- =====================================================================
-- 7. Плаќање со картичка: корисникот бира CASH / CARD при барањето, плаќањето со картичка
--    оди преку платежна порта (во апликацијата), а парите меѓу возачот и GEOCAB се пребиваат:
--    провизија од готовина - заработка од картички - уплати + исплати.
-- =====================================================================

-- избраниот начин на плаќање на возењето; постоечките возења се во готовина
ALTER TABLE Rides ADD COLUMN IF NOT EXISTS payment_method_id INT;
UPDATE Rides
SET payment_method_id = (SELECT payment_method_id FROM Payment_methods WHERE payment_tip = 'CASH')
WHERE payment_method_id IS NULL;
ALTER TABLE Rides ALTER COLUMN payment_method_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_rides_payment_method') THEN
        ALTER TABLE Rides
            ADD CONSTRAINT FK_Rides_Payment_method
                FOREIGN KEY (payment_method_id) REFERENCES Payment_methods(payment_method_id) ON DELETE RESTRICT;
    END IF;
END $$;

-- Исплати од GEOCAB кон возачот: заработката од плаќања со картичка ја прима GEOCAB,
-- па по пребивањето со провизијата од готовина GEOCAB му го исплаќа остатокот на возачот.
CREATE TABLE IF NOT EXISTS Driver_payouts (
                                payout_id SERIAL PRIMARY KEY,
                                driver_id INT NOT NULL,
                                admin_id INT NOT NULL,                 -- кој администратор ја направил исплатата
                                amount NUMERIC(10,2) NOT NULL,
                                paid_at TIMESTAMP NOT NULL DEFAULT now(),
                                note VARCHAR(255),
                                CONSTRAINT chk_driver_payout_amount CHECK (amount > 0),
                                CONSTRAINT FK_Driver_payouts_Drivers
                                    FOREIGN KEY (driver_id) REFERENCES Drivers(driver_id) ON DELETE RESTRICT,
                                CONSTRAINT FK_Driver_payouts_Admins
                                    FOREIGN KEY (admin_id) REFERENCES Admins(admins_id) ON DELETE RESTRICT
);


-- Секој обид за плаќање со картичка (одобрен или одбиен).
-- Целиот број и CVV НИКОГАШ не се чуваат - само тип и последните 4 цифри.
CREATE TABLE IF NOT EXISTS Card_transactions (
                                   card_transaction_id SERIAL PRIMARY KEY,
                                   payment_id INT NOT NULL,
                                   card_brand VARCHAR(20) NOT NULL,       -- VISA / MASTERCARD / MAESTRO / AMEX / OTHER
                                   card_last4 CHAR(4) NOT NULL,
                                   approved BOOLEAN NOT NULL,
                                   created_at TIMESTAMP NOT NULL DEFAULT now(),
                                   CONSTRAINT chk_card_last4 CHECK (card_last4 ~ '^[0-9]{4}$'),
                                   CONSTRAINT FK_Card_transactions_Payments
                                       FOREIGN KEY (payment_id) REFERENCES Payments(payment_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_driver_payouts_driver ON Driver_payouts(driver_id);
CREATE INDEX IF NOT EXISTS idx_card_transactions_payment ON Card_transactions(payment_id);

CREATE OR REPLACE FUNCTION fn_driver_debt_status(p_driver_id INT, p_today DATE DEFAULT current_date)
    RETURNS TABLE(
                     total_commission NUMERIC,   -- провизија од плаќања во готовина
                     total_paid       NUMERIC,   -- уплати на возачот + заработка од картички - исплати од GEOCAB
                     due_amount       NUMERIC,
                     due_month        DATE,
                     warning_amount   NUMERIC,
                     warning_month    DATE,
                     overdue_amount   NUMERIC,
                     overdue_month    DATE,
                     is_blocked       BOOLEAN
                 ) AS $$
-- Пребивање: заработката на возачот од плаќања со картичка (ја држи GEOCAB) се смета како уплата
-- на провизијата од готовина. Уплатите го покриваат најстариот месец прв (FIFO).
WITH completed AS (
    SELECT p.payment_time, p.platform_amount, p.driver_amount, pm.payment_tip
    FROM Payments p
             JOIN Rides r            ON r.ride_id            = p.ride_id
             JOIN Status s           ON s.status_id          = p.status_id
             JOIN Payment_methods pm ON pm.payment_method_id = p.payment_method_id
    WHERE r.driver_id = p_driver_id
      AND s.status_text = 'completed'
), months AS (
    SELECT date_trunc('month', payment_time)::DATE AS m, SUM(platform_amount) AS c
    FROM completed
    WHERE payment_tip = 'CASH'
    GROUP BY 1
), paid AS (
    SELECT (SELECT COALESCE(SUM(amount), 0) FROM Commission_payouts WHERE driver_id = p_driver_id)
         + (SELECT COALESCE(SUM(driver_amount), 0) FROM completed WHERE payment_tip = 'CARD')
         - (SELECT COALESCE(SUM(amount), 0) FROM Driver_payouts WHERE driver_id = p_driver_id) AS total
), staged AS (
    SELECT m,
           LEAST(c, GREATEST(0, SUM(c) OVER (ORDER BY m) - (SELECT total FROM paid))) AS left_to_pay,
           CASE WHEN p_today >= (m + INTERVAL '2 months')::DATE                    THEN 'overdue'
                WHEN p_today >= (m + INTERVAL '1 month' + INTERVAL '14 days')::DATE THEN 'warning'
                WHEN p_today >= (m + INTERVAL '1 month')::DATE                     THEN 'due'
                ELSE 'current' END AS stage
    FROM months
)
SELECT
    COALESCE((SELECT SUM(c) FROM months), 0),
    (SELECT total FROM paid),
    COALESCE((SELECT SUM(left_to_pay) FROM staged WHERE stage = 'due'), 0),
    (SELECT MIN(m) FROM staged WHERE stage = 'due' AND left_to_pay > 0),
    COALESCE((SELECT SUM(left_to_pay) FROM staged WHERE stage = 'warning'), 0),
    (SELECT MIN(m) FROM staged WHERE stage = 'warning' AND left_to_pay > 0),
    COALESCE((SELECT SUM(left_to_pay) FROM staged WHERE stage = 'overdue'), 0),
    (SELECT MIN(m) FROM staged WHERE stage = 'overdue' AND left_to_pay > 0),
    EXISTS (SELECT 1 FROM staged WHERE stage = 'overdue' AND left_to_pay > 0);
$$ LANGUAGE sql STABLE;

-- 5.6 На завршено возење се креира плаќање (pending) со начинот што корисникот го избрал
--     при барањето; корисникот може да го смени при плаќањето (sp_process_payment)
CREATE OR REPLACE FUNCTION trg_fn_create_payment()
    RETURNS TRIGGER AS $$
BEGIN
    IF fn_get_status_text(NEW.status_id) = 'completed'
        AND fn_get_status_text(OLD.status_id) <> 'completed' THEN
        INSERT INTO Payments (amount, payment_method_id, payment_time, ride_id, status_id)
        VALUES (NEW.final_price,
                NEW.payment_method_id,
                now(),
                NEW.ride_id,
                fn_get_status_id('payment', 'pending'));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 5.8 казната за откажување го добива начинот на плаќање од возењето
CREATE OR REPLACE FUNCTION trg_fn_apply_cancellation()
    RETURNS TRIGGER AS $$
BEGIN
    IF NEW.cancelled_by_type <> 'ADMIN'
        AND (SELECT fn_get_status_text(status_id) FROM Rides WHERE ride_id = NEW.ride_id) = 'in_progress' THEN
        RAISE EXCEPTION 'Возењето % е во тек и може да го прекине само администратор', NEW.ride_id;
    END IF;

    UPDATE Rides
    SET status_id = fn_get_status_id('ride', 'cancelled')
    WHERE ride_id = NEW.ride_id;

    IF NEW.cancellation_fee > 0 THEN
        INSERT INTO Payments (amount, payment_method_id, payment_time, ride_id, status_id)
        VALUES (NEW.cancellation_fee,
                (SELECT payment_method_id FROM Rides WHERE ride_id = NEW.ride_id),
                now(),
                NEW.ride_id,
                fn_get_status_id('payment', 'pending'));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- нов параметар p_payment_tip -> старата сигнатура се брише
DROP FUNCTION IF EXISTS fn_request_ride(INT, INT, INT, VARCHAR, NUMERIC);
-- 6.5 Корисникот нарачува возење. Враќа ride_id.
CREATE OR REPLACE FUNCTION fn_request_ride(
    p_user_id INT,
    p_pickup_location_id INT,
    p_dropoff_location_id INT,
    p_vehicle_class VARCHAR DEFAULT NULL,
    p_distance_km NUMERIC DEFAULT NULL,      -- растојание по пат (од апликацијата); NULL = воздушно
    p_payment_tip VARCHAR DEFAULT 'CASH'     -- CASH / CARD: како корисникот планира да плати
)
    RETURNS INT AS $$
DECLARE
    v_ride_id   INT;
    v_method_id INT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM Users
                   WHERE User_id = p_user_id
                     AND status_id = fn_get_status_id('user', 'active')) THEN
        RAISE EXCEPTION 'Корисникот % не постои или не е активен', p_user_id;
    END IF;
    IF EXISTS (SELECT 1 FROM Rides r
                                 JOIN Status s ON r.status_id = s.status_id
               WHERE r.user_id = p_user_id
                 AND s.status_text IN ('requested', 'accepted', 'driver_arrived', 'in_progress')) THEN
        RAISE EXCEPTION 'Корисникот % веќе има активно возење', p_user_id;
    END IF;
    IF p_vehicle_class IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM Vehicle_types WHERE description = p_vehicle_class) THEN
        RAISE EXCEPTION 'Непозната класа на возило: %', p_vehicle_class;
    END IF;
    IF p_distance_km IS NOT NULL AND p_distance_km < 0 THEN
        RAISE EXCEPTION 'Растојанието не може да е негативно';
    END IF;
    SELECT payment_method_id INTO v_method_id FROM Payment_methods WHERE payment_tip = p_payment_tip;
    IF v_method_id IS NULL THEN
        RAISE EXCEPTION 'Непознат начин на плаќање: %', p_payment_tip;
    END IF;

    INSERT INTO Rides (user_id, pickup_location_id, dropoff_location_id, status_id,
                       requested_vehicle_class, request_time, distance_km, discount_percentage, payment_method_id)
    VALUES (p_user_id, p_pickup_location_id, p_dropoff_location_id,
            fn_get_status_id('ride', 'requested'), p_vehicle_class, now(),
            COALESCE(p_distance_km, fn_calculate_distance_km(p_pickup_location_id, p_dropoff_location_id)), 0,
            v_method_id)
    RETURNING ride_id INTO v_ride_id;

    PERFORM fn_assign_driver(v_ride_id);
    RETURN v_ride_id;
END;
$$ LANGUAGE plpgsql;

-- нови параметри за картичката -> старата сигнатура се брише
DROP PROCEDURE IF EXISTS sp_process_payment(INT, VARCHAR, BOOLEAN);
-- 6.11 Плаќање на завршено возење или казна. p_payment_tip = 'CASH' / 'CARD'.
--      CARD: апликацијата прво ја прашува платежната порта, па го праќа резултатот
--      (p_success) и податоците за картичката (тип, последни 4 цифри) - се запишува во
--      Card_transactions. Одбиено плаќање станува failed и може повторно да се обиде.
CREATE OR REPLACE PROCEDURE sp_process_payment(
    p_ride_id INT,
    p_payment_tip VARCHAR,
    p_success BOOLEAN DEFAULT true,
    p_card_brand VARCHAR DEFAULT NULL,
    p_card_last4 VARCHAR DEFAULT NULL
) AS $$
DECLARE
    v_payment_id INT;
    v_method_id  INT;
BEGIN
    SELECT payment_method_id INTO v_method_id
    FROM Payment_methods WHERE payment_tip = p_payment_tip;
    IF v_method_id IS NULL THEN
        RAISE EXCEPTION 'Непознат начин на плаќање: %', p_payment_tip;
    END IF;
    IF p_payment_tip = 'CARD' AND (p_card_brand IS NULL OR p_card_last4 IS NULL) THEN
        RAISE EXCEPTION 'За плаќање со картичка потребни се тип и последни 4 цифри од картичката';
    END IF;

    SELECT p.payment_id INTO v_payment_id
    FROM Payments p
             JOIN Status s ON p.status_id = s.status_id
    WHERE p.ride_id = p_ride_id
      AND s.status_text IN ('pending', 'failed')
    ORDER BY p.payment_id DESC
    LIMIT 1
        FOR UPDATE OF p;
    IF v_payment_id IS NULL THEN
        RAISE EXCEPTION 'Нема плаќање што чека за возењето %', p_ride_id;
    END IF;

    IF p_payment_tip = 'CARD' THEN
        INSERT INTO Card_transactions (payment_id, card_brand, card_last4, approved, created_at)
        VALUES (v_payment_id, p_card_brand, p_card_last4, p_success, now());
    END IF;

    UPDATE Payments
    SET payment_method_id = v_method_id,
        payment_time      = now(),
        status_id         = fn_get_status_id('payment',
                                             CASE WHEN p_success THEN 'completed' ELSE 'failed' END)
    WHERE payment_id = v_payment_id;
END;
$$ LANGUAGE plpgsql;

-- 6.14a Администраторот евидентира исплата од GEOCAB на возачот (кога по пребивањето
--       GEOCAB му должи - заработка од плаќања со картичка). Не смее да е повеќе од долгот.
CREATE OR REPLACE PROCEDURE sp_record_driver_payout(
    p_driver_id INT,
    p_admin_id INT,
    p_amount NUMERIC,
    p_note VARCHAR DEFAULT NULL
) AS $$
DECLARE
    v_balance NUMERIC;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM Admins
                   WHERE admins_id = p_admin_id
                     AND status_id = fn_get_status_id('admin', 'active')) THEN
        RAISE EXCEPTION 'Администраторот % не постои или не е активен', p_admin_id;
    END IF;
    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'Износот мора да е поголем од 0';
    END IF;

    PERFORM 1 FROM Drivers WHERE driver_id = p_driver_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Возачот % не постои', p_driver_id;
    END IF;
    SELECT balance INTO v_balance FROM vw_driver_commission_balance WHERE driver_id = p_driver_id;
    IF p_amount > -v_balance THEN
        RAISE EXCEPTION 'Износот % е поголем од она што GEOCAB му должи на возачот (% ден.)',
            p_amount, GREATEST(-v_balance, 0);
    END IF;

    INSERT INTO Driver_payouts (driver_id, admin_id, amount, paid_at, note)
    VALUES (p_driver_id, p_admin_id, p_amount, now(), p_note);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE VIEW vw_driver_commission_balance AS
SELECT
    d.driver_id,
    d.name                                                  AS driver_name,
    COALESCE(c.cash_commission, 0)                          AS total_commission,
    COALESCE(p.total_paid, 0)                               AS total_paid,
    COALESCE(c.cash_commission, 0) - COALESCE(c.card_earnings, 0)
        - COALESCE(p.total_paid, 0) + COALESCE(dp.total_paid_out, 0) AS balance,
    p.last_payout_at,
    COALESCE(c.card_earnings, 0)                            AS card_earnings,
    COALESCE(dp.total_paid_out, 0)                          AS driver_payouts,
    dp.last_driver_payout_at
FROM Drivers d
         LEFT JOIN (SELECT r.driver_id,
                           SUM(pay.platform_amount) FILTER (WHERE pm.payment_tip = 'CASH') AS cash_commission,
                           SUM(pay.driver_amount)   FILTER (WHERE pm.payment_tip = 'CARD') AS card_earnings
                    FROM Payments pay
                             JOIN Rides r            ON r.ride_id            = pay.ride_id
                             JOIN Status s           ON s.status_id          = pay.status_id
                             JOIN Payment_methods pm ON pm.payment_method_id = pay.payment_method_id
                    WHERE s.status_text = 'completed'
                    GROUP BY r.driver_id) c ON c.driver_id = d.driver_id
         LEFT JOIN (SELECT driver_id, SUM(amount) AS total_paid, MAX(paid_at) AS last_payout_at
                    FROM Commission_payouts
                    GROUP BY driver_id) p ON p.driver_id = d.driver_id
         LEFT JOIN (SELECT driver_id, SUM(amount) AS total_paid_out, MAX(paid_at) AS last_driver_payout_at
                    FROM Driver_payouts
                    GROUP BY driver_id) dp ON dp.driver_id = d.driver_id;

CREATE OR REPLACE VIEW vw_payment_history AS
SELECT
    p.payment_id,
    -- Korisnik
    u.user_id,
    u.name                                  AS user_name,
    u.phone                                 AS user_phone,
    -- Ride info
    r.ride_id,
    r.request_time                          AS ride_requested_at,
    r.start_time                            AS ride_started_at,
    r.end_time                              AS ride_ended_at,
    r.distance_km,
    -- Adresa kratko
    lp.naselba  || ' - ' ||
    lp.ulica    || ' '   || lp.broj         AS pickup_address,
    ld.naselba  || ' - ' ||
    ld.ulica    || ' '   || ld.broj         AS dropoff_address,
    -- Vozac
    d.driver_id,
    d.name                                  AS driver_name,
    -- Plakjanje
    p.amount,
    p.payment_time,
    pm.payment_tip                          AS payment_method,  -- CASH / CARD
    -- Originalna cena pred popust
    ROUND(
            p.amount / NULLIF(1.0 - COALESCE(r.discount_percentage, 0) / 100.0, 0)
                ::numeric, 2)                           AS amount_before_discount,
    r.discount_percentage,
    -- Statusi
    sp.status_text                          AS payment_status,
    sr.status_text                          AS ride_status,
    card.card_brand,
    card.card_last4
FROM Payments p
         JOIN Rides r            ON p.ride_id                    = r.ride_id
         JOIN Users u            ON r.user_id                    = u.user_id
         JOIN Drivers d          ON r.driver_id                  = d.driver_id
         JOIN Payment_methods pm ON p.payment_method_id          = pm.payment_method_id
         JOIN Status sp          ON p.status_id                  = sp.status_id
         JOIN Status sr          ON r.status_id                  = sr.status_id
         JOIN Locations lp       ON r.pickup_location_id         = lp.location_id
         JOIN Locations ld       ON r.dropoff_location_id        = ld.location_id
         LEFT JOIN LATERAL (SELECT ct.card_brand, ct.card_last4 FROM Card_transactions ct
                            WHERE ct.payment_id = p.payment_id AND ct.approved
                            ORDER BY ct.card_transaction_id DESC LIMIT 1) card ON true;

CREATE OR REPLACE VIEW vw_ride_details AS
SELECT
    r.ride_id,
    -- Korisnik
    u.user_id,
    u.name                                  AS user_name,
    u.phone                                 AS user_phone,
    -- Vozac
    d.driver_id,
    d.name                                  AS driver_name,
    d.phone                                 AS driver_phone,
    d.rating                                AS driver_rating,
    -- Avto
    vo.plate       AS vehicle_plate,
    vo.color       AS vehicle_color,
    vt.type_name   AS vehicle_model,
    vt.description AS vehicle_class,
    vm.model       AS vehicle_brand,
    -- Pickup lokacija
    lp.grad                                 AS pickup_grad,
    lp.naselba                              AS pickup_naselba,
    lp.ulica                                AS pickup_ulica,
    lp.broj                                 AS pickup_broj,
    lp.latitude                             AS pickup_lat,
    lp.longitude                            AS pickup_lng,
    -- Dropoff lokacija
    ld.grad                                 AS dropoff_grad,
    ld.naselba                              AS dropoff_naselba,
    ld.ulica                                AS dropoff_ulica,
    ld.broj                                 AS dropoff_broj,
    ld.latitude                             AS dropoff_lat,
    ld.longitude                            AS dropoff_lng,
    -- Vremijna sekvenca
    r.request_time,
    r.pickup_time,
    r.start_time,
    r.end_time,
    -- Cena i distanca
    r.distance_km,
    r.discount_percentage,
    r.final_price,
    -- Status na voznata
    s.status_text                           AS ride_status,
    -- Izbran nacin na plakjanje (CASH / CARD)
    pmr.payment_tip                         AS chosen_payment_method
FROM Rides r
         LEFT JOIN Users u        ON r.user_id                = u.user_id
         LEFT JOIN Drivers d      ON r.driver_id              = d.driver_id
         JOIN Locations lp   ON r.pickup_location_id     = lp.location_id
         JOIN Locations ld   ON r.dropoff_location_id    = ld.location_id
         JOIN Status s       ON r.status_id              = s.status_id
         LEFT JOIN vehicle_ownership vo ON r.vehicle_ownership_id = vo.vehicle_ownership_id
         LEFT JOIN vehicle_types vt ON vo.vehicle_type_id = vt.vehicle_type_id
         LEFT JOIN vehicles_model vm ON vo.vehicles_model_id = vm.vehicles_model_id
         JOIN Payment_methods pmr ON r.payment_method_id = pmr.payment_method_id;

CREATE OR REPLACE VIEW vw_current_ride AS
SELECT *
FROM vw_ride_details
WHERE ride_status IN ('requested', 'accepted', 'driver_arrived', 'in_progress');

CREATE OR REPLACE VIEW vw_driver_pending_requests AS
SELECT
    r.ride_id,
    r.driver_id,
    r.request_time,
    r.offered_at,                                       -- од тука тече рокот за одговор
    u.name                                  AS user_name,
    u.phone                                 AS user_phone,
    u_rating.avg_given_score                AS user_avg_given_score,
    lp.naselba || ' - ' || lp.ulica || ' ' || lp.broj AS pickup_address,
    ld.naselba || ' - ' || ld.ulica || ' ' || ld.broj AS dropoff_address,
    lp.latitude                             AS pickup_lat,
    lp.longitude                            AS pickup_lng,
    r.distance_km,
    ROUND(ST_Distance(lc.geom, lp.geom)::NUMERIC, 0) AS meters_to_pickup,
    price.final_price                       AS estimated_price,
    pmr.payment_tip                         AS chosen_payment_method
FROM Rides r
         JOIN Status s            ON r.status_id            = s.status_id
         JOIN Users u             ON r.user_id              = u.user_id
         JOIN Locations lp        ON r.pickup_location_id   = lp.location_id
         JOIN Locations ld        ON r.dropoff_location_id  = ld.location_id
         JOIN Active_drivers ad   ON r.driver_id            = ad.driver_id
         JOIN Locations lc        ON ad.current_location_id = lc.location_id
         JOIN Vehicle_ownership vo ON ad.vehicle_ownership_id = vo.vehicle_ownership_id
         JOIN Payment_methods pmr ON r.payment_method_id    = pmr.payment_method_id
         LEFT JOIN LATERAL fn_calculate_price(vo.vehicle_type_id, r.distance_km) price ON true
         LEFT JOIN LATERAL (SELECT ROUND(AVG(score), 2) AS avg_given_score
                            FROM Ratings WHERE User_id = r.user_id) u_rating ON true
WHERE s.status_text = 'requested';

CREATE OR REPLACE VIEW vw_user_ride_history AS
SELECT
    r.ride_id,
    r.user_id,
    r.request_time,
    r.start_time,
    r.end_time,
    lp.naselba || ' - ' || lp.ulica || ' ' || lp.broj AS pickup_address,
    ld.naselba || ' - ' || ld.ulica || ' ' || ld.broj AS dropoff_address,
    d.driver_id,
    d.name                                  AS driver_name,
    vo.plate                                AS vehicle_plate,
    vt.description                          AS vehicle_class,
    r.distance_km,
    r.discount_percentage,
    r.final_price,
    s.status_text                           AS ride_status,
    pm.payment_tip                          AS payment_method,
    ps.status_text                          AS payment_status,
    rt.score                                AS rating_score,
    rt.comment                              AS rating_comment,
    c.cancelled_by_type,
    c.reason                                AS cancellation_reason,
    c.is_no_show,
    c.cancellation_fee,
    pmr.payment_tip                         AS chosen_payment_method,
    card.card_brand,
    card.card_last4,
    pay.payment_id
FROM Rides r
         JOIN Status s            ON r.status_id            = s.status_id
         JOIN Locations lp        ON r.pickup_location_id   = lp.location_id
         JOIN Locations ld        ON r.dropoff_location_id  = ld.location_id
         LEFT JOIN Drivers d      ON r.driver_id            = d.driver_id
         LEFT JOIN Vehicle_ownership vo ON r.vehicle_ownership_id = vo.vehicle_ownership_id
         LEFT JOIN Vehicle_types vt     ON vo.vehicle_type_id     = vt.vehicle_type_id
         LEFT JOIN LATERAL (SELECT * FROM Payments p
                            WHERE p.ride_id = r.ride_id
                            ORDER BY p.payment_id DESC LIMIT 1) pay ON true
         LEFT JOIN Payment_methods pm ON pay.payment_method_id = pm.payment_method_id
         LEFT JOIN Status ps          ON pay.status_id         = ps.status_id
         LEFT JOIN Ratings rt         ON rt.ride_id            = r.ride_id
         LEFT JOIN LATERAL (SELECT * FROM Cancellations cc
                            WHERE cc.ride_id = r.ride_id
                            ORDER BY cc.cancellation_id DESC LIMIT 1) c ON true
         JOIN Payment_methods pmr     ON r.payment_method_id   = pmr.payment_method_id
         LEFT JOIN LATERAL (SELECT ct.card_brand, ct.card_last4 FROM Card_transactions ct
                            WHERE ct.payment_id = pay.payment_id AND ct.approved
                            ORDER BY ct.card_transaction_id DESC LIMIT 1) card ON true;

CREATE OR REPLACE VIEW vw_driver_earnings_summary AS
SELECT
    d.driver_id,
    d.name                                                                        AS driver_name,
    COUNT(DISTINCT r.ride_id) FILTER (WHERE rs.status_text = 'completed')         AS completed_rides,
    COALESCE(SUM(r.distance_km) FILTER (WHERE rs.status_text = 'completed'
                                         AND p.payment_id IS NOT NULL), 0)        AS total_km,
    COALESCE(SUM(p.amount) FILTER (WHERE ps.status_text = 'completed'), 0)        AS total_collected,
    COALESCE(SUM(p.driver_amount) FILTER (WHERE ps.status_text = 'completed'), 0) AS driver_earnings,
    COALESCE(SUM(p.platform_amount) FILTER (WHERE ps.status_text = 'completed'), 0) AS platform_commission,
    COALESCE(SUM(p.driver_amount) FILTER (WHERE ps.status_text = 'completed'
                                           AND rs.status_text = 'cancelled'), 0)  AS cancellation_earnings,
    COALESCE(SUM(p.amount) FILTER (WHERE ps.status_text IN ('pending', 'failed')), 0) AS unpaid_amount,
    COALESCE(SUM(p.driver_amount) FILTER (WHERE ps.status_text = 'completed'
                                           AND p.payment_time >= date_trunc('month', now())), 0) AS earnings_this_month,
    COALESCE(SUM(p.driver_amount) FILTER (WHERE ps.status_text = 'completed'
                                           AND p.payment_time >= current_date), 0) AS earnings_today,
    COALESCE(SUM(p.amount) FILTER (WHERE ps.status_text = 'completed' AND pm.payment_tip = 'CASH'), 0) AS cash_collected,
    COALESCE(SUM(p.amount) FILTER (WHERE ps.status_text = 'completed' AND pm.payment_tip = 'CARD'), 0) AS card_collected,
    COALESCE(SUM(p.driver_amount) FILTER (WHERE ps.status_text = 'completed' AND pm.payment_tip = 'CARD'), 0) AS card_earnings
FROM Drivers d
         LEFT JOIN Rides r     ON r.driver_id   = d.driver_id
         LEFT JOIN Status rs   ON r.status_id   = rs.status_id
         LEFT JOIN Payments p  ON p.ride_id     = r.ride_id
         LEFT JOIN Status ps   ON p.status_id   = ps.status_id
         LEFT JOIN Payment_methods pm ON p.payment_method_id = pm.payment_method_id
GROUP BY d.driver_id, d.name;

CREATE OR REPLACE VIEW vw_admin_dashboard AS
SELECT
    r.request_time::DATE                                            AS day,
    COUNT(*)                                                        AS total_rides,
    COUNT(*) FILTER (WHERE s.status_text = 'completed')             AS completed_rides,
    COUNT(*) FILTER (WHERE s.status_text = 'cancelled')             AS cancelled_rides,
    COUNT(*) FILTER (WHERE s.status_text = 'no_driver')             AS no_driver_rides,
    COUNT(*) FILTER (WHERE c.is_no_show)                            AS no_show_rides,
    ROUND(100.0 * COUNT(*) FILTER (WHERE s.status_text = 'cancelled')
              / COUNT(*), 2)                                        AS cancel_rate_percent,
    COUNT(DISTINCT r.user_id)                                       AS active_users,
    COUNT(DISTINCT r.driver_id)                                     AS active_drivers,
    COALESCE(SUM(p.amount) FILTER (WHERE ps.status_text = 'completed'), 0) AS revenue,
    COALESCE(SUM(c.cancellation_fee), 0)                            AS cancellation_fees,
    ROUND(AVG(r.distance_km) FILTER (WHERE s.status_text = 'completed'), 2) AS avg_distance_km,
    ROUND(AVG(rt.score), 2)                                         AS avg_rating,
    COALESCE(SUM(p.platform_amount) FILTER (WHERE ps.status_text = 'completed'), 0) AS platform_revenue,
    COALESCE(SUM(p.amount) FILTER (WHERE ps.status_text = 'completed' AND pm.payment_tip = 'CARD'), 0) AS card_revenue
FROM Rides r
         JOIN Status s          ON r.status_id = s.status_id
         LEFT JOIN Payments p   ON p.ride_id   = r.ride_id
         LEFT JOIN Status ps    ON p.status_id = ps.status_id
         LEFT JOIN Payment_methods pm ON p.payment_method_id = pm.payment_method_id
         LEFT JOIN Ratings rt   ON rt.ride_id  = r.ride_id
         LEFT JOIN Cancellations c ON c.ride_id = r.ride_id
GROUP BY r.request_time::DATE;


-- =====================================================================
-- 8. Потврда на е-пошта при регистрација: барањето чека во Pending_registrations,
--    профилот во Users се креира дури по кликот на линкот од мејлот.
-- =====================================================================

-- Регистрации што чекаат потврда на е-пошта. Профилот во Users се креира дури кога корисникот
-- ќе го отвори линкот од мејлот (fn_confirm_registration). Се чува само hash од токенот (SHA-256),
-- па ни со пристап до базата не може да се активира туѓ профил; лозинката е исто само hash.
CREATE TABLE IF NOT EXISTS Pending_registrations (
                                       pending_id SERIAL PRIMARY KEY,
                                       name VARCHAR(255) NOT NULL,
                                       username VARCHAR(255) NOT NULL,
                                       phone VARCHAR(255) NOT NULL,
                                       email VARCHAR(255) NOT NULL UNIQUE,
                                       password_hash TEXT NOT NULL,
                                       token_hash CHAR(64) NOT NULL UNIQUE,
                                       created_at TIMESTAMP NOT NULL DEFAULT now(),
                                       expires_at TIMESTAMP NOT NULL,
                                       CONSTRAINT chk_pending_phone CHECK (phone ~ '^[0-9+]+$'),
                                       CONSTRAINT chk_pending_email CHECK (email ~ '^[^@]+@[^@]+\.[^@]+$'),
                                       CONSTRAINT chk_pending_token CHECK (token_hash ~ '^[0-9a-f]{64}$')
);

-- 6.17 Регистрација со потврда на е-пошта.
--      fn_create_pending_registration: барање што чека потврда (рок p_valid_hours); старото барање
--      со истата е-пошта се заменува, истечените се бришат. Враќа pending_id.
CREATE OR REPLACE FUNCTION fn_create_pending_registration(
    p_name VARCHAR,
    p_username VARCHAR,
    p_phone VARCHAR,
    p_email VARCHAR,
    p_password_hash TEXT,
    p_token_hash VARCHAR,
    p_valid_hours INT DEFAULT 24
)
    RETURNS INT AS $$
DECLARE
    v_pending_id INT;
BEGIN
    DELETE FROM Pending_registrations WHERE expires_at < now();

    IF EXISTS (SELECT 1 FROM Users WHERE username = p_username) THEN
        RAISE EXCEPTION 'Корисничкото име % е зафатено', p_username;
    END IF;
    IF EXISTS (SELECT 1 FROM Users WHERE phone = p_phone) THEN
        RAISE EXCEPTION 'Веќе постои профил со телефонот %', p_phone;
    END IF;
    IF EXISTS (SELECT 1 FROM Users WHERE lower(email) = lower(p_email)) THEN
        RAISE EXCEPTION 'Веќе постои профил со е-поштата %', p_email;
    END IF;

    -- истиот човек се регистрира повторно (на пр. не го добил мејлот) -> новото барање го заменува старото
    DELETE FROM Pending_registrations WHERE lower(email) = lower(p_email);

    IF EXISTS (SELECT 1 FROM Pending_registrations WHERE username = p_username) THEN
        RAISE EXCEPTION 'Корисничкото име % е резервирано за друга регистрација што чека потврда', p_username;
    END IF;
    IF EXISTS (SELECT 1 FROM Pending_registrations WHERE phone = p_phone) THEN
        RAISE EXCEPTION 'Телефонот % е во друга регистрација што чека потврда', p_phone;
    END IF;

    INSERT INTO Pending_registrations (name, username, phone, email, password_hash, token_hash, created_at, expires_at)
    VALUES (p_name, p_username, p_phone, p_email, p_password_hash, p_token_hash, now(),
            now() + make_interval(hours => p_valid_hours))
    RETURNING pending_id INTO v_pending_id;
    RETURN v_pending_id;
END;
$$ LANGUAGE plpgsql;

-- Нов линк за истата регистрација (стариот линк престанува да важи). Враќа pending_id или NULL
-- ако нема барање што чека за таа е-пошта (апликацијата не открива дали постои).
CREATE OR REPLACE FUNCTION fn_renew_pending_registration(
    p_email VARCHAR,
    p_token_hash VARCHAR,
    p_valid_hours INT DEFAULT 24
)
    RETURNS INT AS $$
DECLARE
    v_pending_id INT;
BEGIN
    UPDATE Pending_registrations
    SET token_hash = p_token_hash,
        expires_at = now() + make_interval(hours => p_valid_hours)
    WHERE lower(email) = lower(p_email)
      AND expires_at >= now()
    RETURNING pending_id INTO v_pending_id;
    RETURN v_pending_id;
END;
$$ LANGUAGE plpgsql;

-- Корисникот го отворил линкот од мејлот: дури тогаш се креира профилот во Users (активен).
-- Барањето се брише. Враќа user_id.
CREATE OR REPLACE FUNCTION fn_confirm_registration(p_token_hash VARCHAR)
    RETURNS INT AS $$
DECLARE
    v_pending Pending_registrations%ROWTYPE;
    v_user_id INT;
BEGIN
    SELECT * INTO v_pending FROM Pending_registrations WHERE token_hash = p_token_hash FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Линкот не е важечки или профилот е веќе активиран. Обидете се да се најавите.';
    END IF;
    IF v_pending.expires_at < now() THEN
        RAISE EXCEPTION 'Линкот истече. Регистрирајте се повторно.';
    END IF;
    -- во меѓувреме некој друг можел да ги земе податоците
    IF EXISTS (SELECT 1 FROM Users WHERE username = v_pending.username
                                      OR phone = v_pending.phone
                                      OR lower(email) = lower(v_pending.email)) THEN
        RAISE EXCEPTION 'Корисничкото име, телефонот или е-поштата веќе ги користи друг профил. Регистрирајте се повторно.';
    END IF;

    INSERT INTO Users (name, username, phone, email, password_hash, created_at, status_id)
    VALUES (v_pending.name, v_pending.username, v_pending.phone, v_pending.email, v_pending.password_hash,
            now(), fn_get_status_id('user', 'active'))
    RETURNING User_id INTO v_user_id;

    DELETE FROM Pending_registrations WHERE pending_id = v_pending.pending_id;
    RETURN v_user_id;
END;
$$ LANGUAGE plpgsql;
