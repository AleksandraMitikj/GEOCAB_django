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
                                           AND p.payment_time >= current_date), 0) AS earnings_today
FROM Drivers d
         LEFT JOIN Rides r     ON r.driver_id   = d.driver_id
         LEFT JOIN Status rs   ON r.status_id   = rs.status_id
         LEFT JOIN Payments p  ON p.ride_id     = r.ride_id
         LEFT JOIN Status ps   ON p.status_id   = ps.status_id
GROUP BY d.driver_id, d.name;

-- Админ dashboard: на крајот е додаден приходот на платформата (провизии)
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
CREATE OR REPLACE VIEW vw_driver_commission_balance AS
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
