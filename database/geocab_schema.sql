-- =====================================================================
-- GEOCAB - шема на базата (само структура, табелите се празни)
--
-- Содржина:
--   1. PostGIS екстензија
--   2. Табели (22)
--   3. Индекси
--   4. Помошни функции
--   5. Тригери
--   6. Функции и процедури за текот на возењето
--   7. Views
--
-- Извршете го во НОВА (празна) база, а потоа geocab_lookup_data.sql
-- (статуси, начини на плаќање и пораки - без нив текот не работи).
--
-- Профил на возач креира администратор: fn_admin_create_driver
--
-- Тек на едно возење:
--   sp_driver_start_shift -> fn_request_ride -> sp_accept_ride / sp_reject_ride
--   -> sp_driver_arrived -> sp_start_ride -> sp_complete_ride
--   -> sp_process_payment -> sp_rate_ride
--   (пред крајот, во секој момент: sp_cancel_ride)
--
-- Статуси на возење:
--   requested -> accepted -> driver_arrived -> in_progress -> completed
--   requested -> no_driver   (нема достапен возач)
--   accepted / driver_arrived -> requested   (возачот откажал -> се бара нов возач)
--   requested / accepted / driver_arrived -> cancelled
--   in_progress -> cancelled  (само администратор, итен случај)
--
-- Django треба на неколку секунди да повикува fn_expire_offers(30):
-- понудите на кои возачот не одговорил 30 секунди одат кај следниот возач.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS postgis;


-- =====================================================================
-- 2. ТАБЕЛИ
-- =====================================================================

CREATE TABLE Status (
                        status_id SERIAL PRIMARY KEY,
                        status_tip VARCHAR(255) NOT NULL,
                        status_text VARCHAR(255) NOT NULL,
                        CONSTRAINT uq_status_tip_text UNIQUE (status_tip, status_text)
);

CREATE TABLE Vehicles_model (
                                Vehicles_model_id SERIAL PRIMARY KEY,
                                Model VARCHAR(255) NOT NULL
);

CREATE TABLE Locations (
                           location_id SERIAL PRIMARY KEY,
                           latitude NUMERIC(9,6) NOT NULL,
                           longitude NUMERIC(9,6) NOT NULL,
                           grad VARCHAR(255) NOT NULL,
                           naselba VARCHAR(255) NOT NULL,
                           ulica VARCHAR(255) NOT NULL,
                           broj VARCHAR(255) NOT NULL,
                           created_at TIMESTAMP NOT NULL DEFAULT now(),
                           geom GEOGRAPHY(POINT, 4326)      -- се пополнува автоматски (trg_locations_geom)
);

CREATE TABLE Users (
                       User_id SERIAL PRIMARY KEY,
                       name VARCHAR(255) NOT NULL,
                       username VARCHAR(255) NOT NULL UNIQUE,
                       phone VARCHAR(255) NOT NULL UNIQUE,
                       email VARCHAR(255) NOT NULL UNIQUE,
                       password_hash TEXT NOT NULL,
                       created_at TIMESTAMP NOT NULL DEFAULT now(),
                       status_id INT NOT NULL,
                       CONSTRAINT chk_users_phone CHECK (phone ~ '^[0-9+]+$'),
                       CONSTRAINT chk_users_email CHECK (email ~ '^[^@]+@[^@]+\.[^@]+$'),
                       CONSTRAINT FK_Users_Status
                           FOREIGN KEY (status_id) REFERENCES Status(status_id)
                               ON DELETE RESTRICT
                               ON UPDATE CASCADE
);

CREATE TABLE Admins (
                        admins_id SERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        username VARCHAR(255) NOT NULL UNIQUE,
                        phone VARCHAR(255) NOT NULL UNIQUE,
                        email VARCHAR(255) NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT now(),
                        status_id INT NOT NULL,
                        CONSTRAINT chk_admins_phone CHECK (phone ~ '^[0-9+]+$'),
                        CONSTRAINT chk_admins_email CHECK (email ~ '^[^@]+@[^@]+\.[^@]+$'),
                        CONSTRAINT FK_Admins_Status
                            FOREIGN KEY (status_id) REFERENCES Status(status_id)
                                ON DELETE RESTRICT
                                ON UPDATE CASCADE
);

-- Возачите не се регистрираат сами: профилот (и почетната лозинка) им го креира
-- администратор преку fn_admin_create_driver, а потоа возачот се најавува и сам
-- ги прави сите чекори од возењето.
CREATE TABLE Drivers (
                         driver_id SERIAL PRIMARY KEY,
                         name VARCHAR(255) NOT NULL,
                         username VARCHAR(255) NOT NULL UNIQUE,
                         phone VARCHAR(255) NOT NULL UNIQUE,
                         email VARCHAR(255) NOT NULL UNIQUE,
                         password_hash TEXT NOT NULL,
                         license_number VARCHAR(255) NOT NULL UNIQUE,
                         registration_date DATE NOT NULL,
                         rating NUMERIC(3,2) NOT NULL,
                         status_id INT NOT NULL,
                         created_at TIMESTAMP NOT NULL DEFAULT now(),
                         created_by_admin_id INT NOT NULL,      -- кој администратор го креирал профилот
                         CONSTRAINT chk_drivers_phone CHECK (phone ~ '^[0-9+]+$'),
                         CONSTRAINT chk_drivers_email CHECK (email ~ '^[^@]+@[^@]+\.[^@]+$'),
                         CONSTRAINT chk_drivers_rating CHECK (rating BETWEEN 0 AND 5),
                         CONSTRAINT FK_Drivers_Status
                             FOREIGN KEY (status_id) REFERENCES Status(status_id)
                                 ON DELETE RESTRICT
                                 ON UPDATE CASCADE,
                         CONSTRAINT FK_Drivers_Created_by_Admin
                             FOREIGN KEY (created_by_admin_id) REFERENCES Admins(admins_id)
                                 ON DELETE RESTRICT
);

-- Класа на возило (Standard, Premium...): description е името на класата што го гледа корисникот,
-- а цената е во Pricing_rules. Марката/моделот е на самото возило (Vehicle_ownership.vehicles_model_id).
CREATE TABLE Vehicle_types (
                               vehicle_type_id SERIAL PRIMARY KEY,
                               type_name VARCHAR(255) NOT NULL,
                               description VARCHAR(255) NOT NULL,
                               CONSTRAINT uq_vehicle_types_class UNIQUE (description)
);

CREATE TABLE Vehicle_ownership (
                                   Vehicle_ownership_id SERIAL PRIMARY KEY,
                                   Plate VARCHAR(255) NOT NULL UNIQUE,
                                   Traffic_permit VARCHAR(255) NOT NULL UNIQUE,
                                   Color VARCHAR(255) NOT NULL,
                                   year INT NOT NULL,
                                   vehicle_type_id INT NOT NULL,         -- класа (цена)
                                   vehicles_model_id INT NOT NULL,       -- марка и модел на возилото
                                   CONSTRAINT chk_vehicle_year CHECK (year >= 1900),
                                   CONSTRAINT FK_Vehicle_ownership_Type
                                       FOREIGN KEY (vehicle_type_id)
                                           REFERENCES Vehicle_types(vehicle_type_id)
                                           ON DELETE RESTRICT
                                           ON UPDATE CASCADE,
                                   CONSTRAINT FK_Vehicle_ownership_Model
                                       FOREIGN KEY (vehicles_model_id)
                                           REFERENCES Vehicles_model(Vehicles_model_id)
                                           ON DELETE RESTRICT
                                           ON UPDATE CASCADE
);

CREATE TABLE Pricing_rules (
                               tariff_id SERIAL PRIMARY KEY,
                               base_price NUMERIC(10,2) NOT NULL,
                               price_per_km NUMERIC(10,2) NOT NULL,
                               vehicle_type_id INT NOT NULL,
                               CONSTRAINT chk_price CHECK (base_price >= 0 AND price_per_km >= 0),
                               CONSTRAINT FK_Pricing_rules_Vehicle_types
                                   FOREIGN KEY (vehicle_type_id)
                                       REFERENCES Vehicle_types(vehicle_type_id)
                                       ON DELETE RESTRICT
                                       ON UPDATE CASCADE
);

CREATE TABLE Drivers_Vehicle_ownership (
                                           id SERIAL PRIMARY KEY,
                                           driver_id INT NOT NULL,
                                           Vehicle_ownership_id INT NOT NULL,
                                           CONSTRAINT uq_dvo_driver_vehicle UNIQUE (driver_id, Vehicle_ownership_id),
                                           CONSTRAINT FK_DVO_Drivers
                                               FOREIGN KEY (driver_id)
                                                   REFERENCES Drivers(driver_id)
                                                    ON DELETE CASCADE,
                                           CONSTRAINT FK_DVO_Vehicles
                                               FOREIGN KEY (Vehicle_ownership_id)
                                                   REFERENCES Vehicle_ownership(Vehicle_ownership_id)
                                                   ON DELETE CASCADE
);

CREATE TABLE Active_drivers (
                                driver_id INT PRIMARY KEY,
                                current_location_id INT NOT NULL,
                                is_available BOOLEAN NOT NULL,
                                working_start TIMESTAMP NOT NULL,
                                working_end TIMESTAMP,                 -- NULL додека смената трае
                                vehicle_ownership_id INT NOT NULL,     -- возилото со кое вози во оваа смена
                                position_updated_at TIMESTAMP,         -- кога GPS на возачот последно јавил позиција
                                CONSTRAINT FK_Active_drivers_Drivers
                                    FOREIGN KEY (driver_id)
                                        REFERENCES Drivers(driver_id)
                                        ON DELETE CASCADE,
                                CONSTRAINT FK_Active_drivers_Locations
                                    FOREIGN KEY (current_location_id)
                                        REFERENCES Locations(location_id)
                                        ON DELETE RESTRICT,
                                CONSTRAINT FK_Active_drivers_Vehicle
                                    FOREIGN KEY (vehicle_ownership_id)
                                        REFERENCES Vehicle_ownership(Vehicle_ownership_id)
                                        ON DELETE RESTRICT
);

CREATE TABLE Rides (
                       ride_id SERIAL PRIMARY KEY,
                       user_id INT NOT NULL,
                       driver_id INT,                          -- NULL додека не се најде возач
                       vehicle_ownership_id INT,               -- се пополнува кога возачот ќе прифати
                       pickup_location_id INT NOT NULL,
                       dropoff_location_id INT NOT NULL,
                       status_id INT NOT NULL,
                       requested_vehicle_class VARCHAR(255),   -- 'Economy class' / 'Luxury class' / NULL = било која
                       request_time TIMESTAMP NOT NULL,
                       offered_at TIMESTAMP,                   -- кога е понудено на тековниот возач (за timeout)
                       pickup_time TIMESTAMP,                  -- возачот пристигнал
                       start_time TIMESTAMP,
                       end_time TIMESTAMP,
                       distance_km NUMERIC(6,2) NOT NULL,
                       final_price NUMERIC(10,2),              -- се пресметува на крајот на возењето
                       discount_percentage NUMERIC(5,2) NOT NULL DEFAULT 0,
                       CONSTRAINT chk_distance CHECK (distance_km >= 0),
                       CONSTRAINT chk_discount CHECK (discount_percentage BETWEEN 0 AND 100),
                       CONSTRAINT chk_ride_locations CHECK (pickup_location_id <> dropoff_location_id),
                       CONSTRAINT chk_ride_times CHECK (end_time IS NULL OR start_time IS NULL OR end_time >= start_time),
                       CONSTRAINT FK_Rides_Users
                           FOREIGN KEY (user_id) REFERENCES Users(User_id) ON DELETE RESTRICT,
                       CONSTRAINT FK_Rides_Drivers
                           FOREIGN KEY (driver_id) REFERENCES Drivers(driver_id) ON DELETE RESTRICT,
                       CONSTRAINT FK_Rides_Vehicle
                           FOREIGN KEY (vehicle_ownership_id) REFERENCES Vehicle_ownership(Vehicle_ownership_id) ON DELETE RESTRICT,
                       CONSTRAINT FK_Rides_Pickup_Location
                           FOREIGN KEY (pickup_location_id) REFERENCES Locations(location_id) ON DELETE RESTRICT,
                       CONSTRAINT FK_Rides_Dropoff_Location
                           FOREIGN KEY (dropoff_location_id) REFERENCES Locations(location_id) ON DELETE RESTRICT,
                       CONSTRAINT FK_Rides_Status
                           FOREIGN KEY (status_id) REFERENCES Status(status_id) ON DELETE RESTRICT
);

-- Возачи што одбиле одредено возење (за да не им се понуди повторно)
--   REJECTED         = возачот ја одбил понудата
--   TIMEOUT          = не одговорил навреме (fn_expire_offers)
--   DRIVER_CANCELLED = прифатил, па откажал -> возењето оди кај друг возач
CREATE TABLE Ride_rejections (
                                 id SERIAL PRIMARY KEY,
                                 ride_id INT NOT NULL,
                                 driver_id INT NOT NULL,
                                 rejected_at TIMESTAMP NOT NULL,
                                 reason VARCHAR(255) NOT NULL DEFAULT 'REJECTED',
                                 CONSTRAINT chk_rejection_reason CHECK (reason IN ('REJECTED', 'TIMEOUT', 'DRIVER_CANCELLED')),
                                 CONSTRAINT uq_ride_rejections_ride_driver UNIQUE (ride_id, driver_id),
                                 CONSTRAINT FK_Ride_rejections_Rides
                                     FOREIGN KEY (ride_id) REFERENCES Rides(ride_id) ON DELETE CASCADE,
                                 CONSTRAINT FK_Ride_rejections_Drivers
                                     FOREIGN KEY (driver_id) REFERENCES Drivers(driver_id) ON DELETE CASCADE
);

CREATE TABLE Payment_methods(
                                payment_method_id SERIAL PRIMARY KEY,
                                payment_tip  VARCHAR(255) NOT NULL UNIQUE
);

-- Поставки на платформата (секогаш точно еден ред, setting_id = 1).
-- commission_percentage = дел од секое плаќање што го задржува GEOCAB (возачот го добива остатокот).
CREATE TABLE Platform_settings (
                                   setting_id INT PRIMARY KEY DEFAULT 1,
                                   commission_percentage NUMERIC(5,2) NOT NULL DEFAULT 20,
                                   updated_at TIMESTAMP NOT NULL DEFAULT now(),
                                   CONSTRAINT chk_single_row CHECK (setting_id = 1),
                                   CONSTRAINT chk_commission CHECK (commission_percentage BETWEEN 0 AND 100)
);

-- commission_percentage се запишува при креирање на плаќањето (trg_payment_commission),
-- па подоцнежна промена на провизијата не ги менува старите плаќања.
CREATE TABLE Payments (
                          payment_id SERIAL PRIMARY KEY,
                          amount NUMERIC(10,2) NOT NULL,
                          payment_method_id INT NOT NULL,
                          payment_time TIMESTAMP NOT NULL,
                          ride_id INT NOT NULL,
                          status_id INT NOT NULL,
                          commission_percentage NUMERIC(5,2) NOT NULL DEFAULT 0,
                          platform_amount NUMERIC(10,2)
                              GENERATED ALWAYS AS (ROUND(amount * commission_percentage / 100, 2)) STORED,
                          driver_amount NUMERIC(10,2)
                              GENERATED ALWAYS AS (amount - ROUND(amount * commission_percentage / 100, 2)) STORED,
                          CONSTRAINT chk_amount CHECK (amount >= 0),
                          CONSTRAINT chk_payment_commission CHECK (commission_percentage BETWEEN 0 AND 100),
                          CONSTRAINT FK_Payments_Rides
                              FOREIGN KEY (ride_id) REFERENCES Rides(ride_id) ON DELETE CASCADE,
                          CONSTRAINT FK_Payments_Status
                              FOREIGN KEY (status_id) REFERENCES Status(status_id) ON DELETE RESTRICT,
                          CONSTRAINT FK_Payments_Method
                              FOREIGN KEY (payment_method_id) REFERENCES Payment_methods(payment_method_id) ON DELETE RESTRICT
);

CREATE TABLE Ratings (
                         rating_id SERIAL PRIMARY KEY,
                         score INT NOT NULL,
                         comment VARCHAR(255),
                         created_at TIMESTAMP NOT NULL DEFAULT now(),
                         ride_id INT NOT NULL,
                         User_id INT NOT NULL,
                         driver_id INT NOT NULL,
                         CONSTRAINT chk_score CHECK (score BETWEEN 1 AND 5),
                         CONSTRAINT uq_ratings_ride UNIQUE (ride_id),
                         CONSTRAINT FK_Ratings_Rides
                             FOREIGN KEY (ride_id) REFERENCES Rides(ride_id) ON DELETE CASCADE,
                         CONSTRAINT FK_Ratings_Users
                             FOREIGN KEY (User_id) REFERENCES Users(User_id) ON DELETE CASCADE,
                         CONSTRAINT FK_Ratings_Drivers
                             FOREIGN KEY (driver_id) REFERENCES Drivers(driver_id) ON DELETE CASCADE
);

CREATE TABLE Cancellations (
                               cancellation_id SERIAL PRIMARY KEY,
                               cancelled_by_type VARCHAR(255) NOT NULL,
                               cancelled_by_id INT NOT NULL,
                               reason VARCHAR(255) NOT NULL,
                               cancellation_fee NUMERIC(10,2) NOT NULL,
                               created_at TIMESTAMP NOT NULL DEFAULT now(),
                               ride_id INT NOT NULL,
                               is_no_show BOOLEAN NOT NULL DEFAULT false,   -- корисникот не се појавил
                               CONSTRAINT chk_fee CHECK (cancellation_fee >= 0),
                               CONSTRAINT chk_cancelled_by_type CHECK (cancelled_by_type IN ('USER', 'DRIVER', 'ADMIN')),
                               CONSTRAINT FK_Cancellations_Rides
                                   FOREIGN KEY (ride_id) REFERENCES Rides(ride_id) ON DELETE CASCADE
);

CREATE TABLE Messages(
                         message_id SERIAL PRIMARY KEY,
                         message_text VARCHAR(255) NOT NULL UNIQUE
);

CREATE TABLE Notifications (
                               notification_id SERIAL PRIMARY KEY,
                               message_id INT NOT NULL,
                               sent_time TIMESTAMP NOT NULL,
                               User_id INT NOT NULL,
                               ride_id INT NOT NULL,
                               status_id INT NOT NULL,
                               CONSTRAINT FK_Notifications_Users
                                   FOREIGN KEY (User_id) REFERENCES Users(User_id) ON DELETE CASCADE,
                               CONSTRAINT FK_Notifications_Rides
                                   FOREIGN KEY (ride_id) REFERENCES Rides(ride_id) ON DELETE CASCADE,
                               CONSTRAINT FK_Notifications_Status
                                   FOREIGN KEY (status_id) REFERENCES Status(status_id) ON DELETE RESTRICT,
                               CONSTRAINT FK_Notifications_Messages
                                    FOREIGN KEY (message_id) REFERENCES Messages(message_id) ON DELETE CASCADE
);


-- Уплати на провизија од возачите кон GEOCAB (возачот ја држи готовината од возењата)
CREATE TABLE Commission_payouts (
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


-- Пријави на проблем во тек на возење (од корисник или возач)
CREATE TABLE Ride_reports (
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


-- =====================================================================
-- 3. ИНДЕКСИ
-- =====================================================================

CREATE INDEX idx_locations_geom      ON Locations USING GIST(geom);
CREATE INDEX idx_rides_user          ON Rides(user_id);
CREATE INDEX idx_rides_driver_status ON Rides(driver_id, status_id);
CREATE INDEX idx_payments_ride       ON Payments(ride_id);
CREATE INDEX idx_notifications_user  ON Notifications(User_id);
CREATE INDEX idx_cancellations_ride  ON Cancellations(ride_id);
CREATE INDEX idx_commission_payouts_driver ON Commission_payouts(driver_id);
CREATE INDEX idx_ride_reports_ride ON Ride_reports(ride_id);


-- =====================================================================
-- 4. ПОМОШНИ ФУНКЦИИ
-- =====================================================================

-- ID на статус според тип и текст, пр. fn_get_status_id('ride', 'completed')
CREATE OR REPLACE FUNCTION fn_get_status_id(p_tip VARCHAR, p_text VARCHAR)
    RETURNS INT AS $$
DECLARE
    v_id INT;
BEGIN
    SELECT status_id INTO v_id
    FROM Status
    WHERE status_tip = p_tip AND status_text = p_text;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'Статусот (%, %) не постои во табелата Status', p_tip, p_text;
    END IF;
    RETURN v_id;
END;
$$ LANGUAGE plpgsql STABLE;

-- Текст на статус според ID
CREATE OR REPLACE FUNCTION fn_get_status_text(p_status_id INT)
    RETURNS VARCHAR AS $$
SELECT status_text FROM Status WHERE status_id = p_status_id;
$$ LANGUAGE sql STABLE;

-- ID на порака според текстот
CREATE OR REPLACE FUNCTION fn_get_message_id(p_text VARCHAR)
    RETURNS INT AS $$
DECLARE
    v_id INT;
BEGIN
    SELECT message_id INTO v_id FROM Messages WHERE message_text = p_text;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'Пораката "%" не постои во табелата Messages', p_text;
    END IF;
    RETURN v_id;
END;
$$ LANGUAGE plpgsql STABLE;

-- Испраќа известување до корисник за дадено возење
CREATE OR REPLACE FUNCTION fn_send_notification(p_user_id INT, p_ride_id INT, p_message_text VARCHAR)
    RETURNS VOID AS $$
BEGIN
    INSERT INTO Notifications (message_id, sent_time, User_id, ride_id, status_id)
    VALUES (fn_get_message_id(p_message_text), now(), p_user_id, p_ride_id,
            fn_get_status_id('notification', 'sent'));
END;
$$ LANGUAGE plpgsql;

-- Нова локација (за адреса избрана на мапа); geom се пополнува од тригер
CREATE OR REPLACE FUNCTION fn_create_location(
    p_lat NUMERIC, p_lng NUMERIC,
    p_grad VARCHAR, p_naselba VARCHAR, p_ulica VARCHAR, p_broj VARCHAR
)
    RETURNS INT AS $$
DECLARE
    v_id INT;
BEGIN
    INSERT INTO Locations (latitude, longitude, grad, naselba, ulica, broj, created_at)
    VALUES (p_lat, p_lng, p_grad, p_naselba, p_ulica, p_broj, now())
    RETURNING location_id INTO v_id;
    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

-- Растојание во km помеѓу две локации (воздушна линија)
CREATE OR REPLACE FUNCTION fn_calculate_distance_km(p_from_location_id INT, p_to_location_id INT)
    RETURNS NUMERIC AS $$
DECLARE
    v_km NUMERIC;
BEGIN
    SELECT ROUND((ST_Distance(a.geom, b.geom) / 1000)::NUMERIC, 2) INTO v_km
    FROM Locations a, Locations b
    WHERE a.location_id = p_from_location_id
      AND b.location_id = p_to_location_id;
    IF v_km IS NULL THEN
        RAISE EXCEPTION 'Не може да се пресмета растојание помеѓу локациите % и %',
            p_from_location_id, p_to_location_id;
    END IF;
    RETURN v_km;
END;
$$ LANGUAGE plpgsql STABLE;

-- Формула за цена: база + km * цена_по_km, 20% попуст за возења од 10km и подолги
CREATE OR REPLACE FUNCTION fn_price_formula(p_base_price NUMERIC, p_price_per_km NUMERIC, p_distance_km NUMERIC)
    RETURNS TABLE(final_price NUMERIC, discount_percentage NUMERIC) AS $$
SELECT
    ROUND((p_base_price + p_distance_km * p_price_per_km)
              * CASE WHEN p_distance_km >= 10 THEN 0.8 ELSE 1 END, 2),
    CASE WHEN p_distance_km >= 10 THEN 20.00 ELSE 0.00 END;
$$ LANGUAGE sql IMMUTABLE;

-- Цена за тип на возило и дистанца
CREATE OR REPLACE FUNCTION fn_calculate_price(p_vehicle_type_id INT, p_distance_km NUMERIC)
    RETURNS TABLE(final_price NUMERIC, discount_percentage NUMERIC) AS $$
SELECT f.final_price, f.discount_percentage
FROM (SELECT base_price, price_per_km
      FROM Pricing_rules
      WHERE vehicle_type_id = p_vehicle_type_id
      ORDER BY tariff_id
      LIMIT 1) pr,
     fn_price_formula(pr.base_price, pr.price_per_km, p_distance_km) f;
$$ LANGUAGE sql STABLE;

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

-- Најблизок достапен возач во радиус од 10km.
-- p_ride_id:       ги прескокнува возачите што веќе го одбиле тоа возење
-- p_vehicle_class: само возила од таа класа (NULL = било која)
-- p_limit:         колку кандидати да врати (по растојание)
-- Возачи што веќе имаат понудено/активно возење се прескокнуваат.
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

-- Казна за откажување:
--   возачот пријавил no-show (корисникот не се појавил) -> цела базна цена
--   откажал возач или админ        -> 0
--   статус requested               -> 0
--   статус accepted                -> 50% од базната цена
--   статус driver_arrived          -> цела базна цена
CREATE OR REPLACE FUNCTION fn_cancellation_fee(
    p_ride_id INT,
    p_cancelled_by_type VARCHAR,
    p_no_show BOOLEAN DEFAULT false
)
    RETURNS NUMERIC AS $$
DECLARE
    v_status VARCHAR;
    v_base   NUMERIC;
BEGIN
    IF p_cancelled_by_type <> 'USER' AND NOT p_no_show THEN
        RETURN 0;
    END IF;

    SELECT fn_get_status_text(r.status_id), pr.base_price
    INTO v_status, v_base
    FROM Rides r
             LEFT JOIN Vehicle_ownership vo ON r.vehicle_ownership_id = vo.vehicle_ownership_id
             LEFT JOIN Pricing_rules pr     ON vo.vehicle_type_id     = pr.vehicle_type_id
    WHERE r.ride_id = p_ride_id
    ORDER BY pr.tariff_id
    LIMIT 1;

    IF p_no_show THEN
        RETURN COALESCE(v_base, 0);
    END IF;

    RETURN CASE v_status
               WHEN 'accepted'       THEN ROUND(COALESCE(v_base, 0) * 0.5, 2)
               WHEN 'driver_arrived' THEN COALESCE(v_base, 0)
               ELSE 0
        END;
END;
$$ LANGUAGE plpgsql STABLE;


-- =====================================================================
-- 5. ТРИГЕРИ
-- =====================================================================

-- 5.1 geom секогаш се пресметува од latitude / longitude
CREATE OR REPLACE FUNCTION trg_fn_locations_geom()
    RETURNS TRIGGER AS $$
BEGIN
    NEW.geom := ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326)::GEOGRAPHY;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_locations_geom
    BEFORE INSERT OR UPDATE OF latitude, longitude ON Locations
    FOR EACH ROW EXECUTE FUNCTION trg_fn_locations_geom();


-- 5.2 status_id мора да е од точниот тип (Status е заедничка табела)
--     аргумент на тригерот = status_tip што е дозволен за табелата
CREATE OR REPLACE FUNCTION trg_fn_check_status_tip()
    RETURNS TRIGGER AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM Status
                   WHERE status_id = NEW.status_id AND status_tip = TG_ARGV[0]) THEN
        RAISE EXCEPTION 'Статусот % не е од тип "%" (табела %)',
            NEW.status_id, TG_ARGV[0], TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_check_status_tip BEFORE INSERT OR UPDATE OF status_id ON Users
    FOR EACH ROW EXECUTE FUNCTION trg_fn_check_status_tip('user');
CREATE TRIGGER trg_check_status_tip BEFORE INSERT OR UPDATE OF status_id ON Drivers
    FOR EACH ROW EXECUTE FUNCTION trg_fn_check_status_tip('driver');
CREATE TRIGGER trg_check_status_tip BEFORE INSERT OR UPDATE OF status_id ON Admins
    FOR EACH ROW EXECUTE FUNCTION trg_fn_check_status_tip('admin');
CREATE TRIGGER trg_check_status_tip BEFORE INSERT OR UPDATE OF status_id ON Rides
    FOR EACH ROW EXECUTE FUNCTION trg_fn_check_status_tip('ride');
CREATE TRIGGER trg_check_status_tip BEFORE INSERT OR UPDATE OF status_id ON Payments
    FOR EACH ROW EXECUTE FUNCTION trg_fn_check_status_tip('payment');
CREATE TRIGGER trg_check_status_tip BEFORE INSERT OR UPDATE OF status_id ON Notifications
    FOR EACH ROW EXECUTE FUNCTION trg_fn_check_status_tip('notification');
CREATE TRIGGER trg_check_status_tip BEFORE INSERT OR UPDATE OF status_id ON Ride_reports
    FOR EACH ROW EXECUTE FUNCTION trg_fn_check_status_tip('report');


-- 5.3 Дозволени премини на статус на возење
CREATE OR REPLACE FUNCTION trg_fn_ride_status_transition()
    RETURNS TRIGGER AS $$
DECLARE
    v_old VARCHAR := fn_get_status_text(OLD.status_id);
    v_new VARCHAR := fn_get_status_text(NEW.status_id);
BEGIN
    IF v_old = v_new THEN
        RETURN NEW;
    END IF;
    IF (v_old, v_new) NOT IN (
                              ('requested',      'accepted'),
                              ('requested',      'no_driver'),
                              ('requested',      'cancelled'),
                              ('accepted',       'driver_arrived'),
                              ('accepted',       'cancelled'),
                              ('accepted',       'requested'),       -- возачот откажал -> нов возач
                              ('driver_arrived', 'in_progress'),
                              ('driver_arrived', 'cancelled'),
                              ('driver_arrived', 'requested'),       -- возачот откажал -> нов возач
                              ('in_progress',    'completed'),
                              ('in_progress',    'cancelled')        -- само админ (проверка во trg_apply_cancellation)
        ) THEN
        RAISE EXCEPTION 'Недозволен премин на статус на возење %: % -> %',
            NEW.ride_id, v_old, v_new;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ride_status_transition
    BEFORE UPDATE OF status_id ON Rides
    FOR EACH ROW EXECUTE FUNCTION trg_fn_ride_status_transition();


-- 5.4 Известување до корисникот при секоја промена на статусот на возењето
CREATE OR REPLACE FUNCTION trg_fn_ride_notification()
    RETURNS TRIGGER AS $$
DECLARE
    v_new          VARCHAR := fn_get_status_text(NEW.status_id);
    v_cancelled_by VARCHAR;
    v_no_show      BOOLEAN;
    v_message      VARCHAR;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.status_id = NEW.status_id THEN
        RETURN NEW;
    END IF;

    IF v_new = 'cancelled' THEN
        SELECT cancelled_by_type, is_no_show INTO v_cancelled_by, v_no_show
        FROM Cancellations
        WHERE ride_id = NEW.ride_id
        ORDER BY created_at DESC, cancellation_id DESC
        LIMIT 1;
    END IF;

    v_message := CASE
                     -- возачот откажал по прифаќање, возењето се враќа во requested
                     WHEN v_new = 'requested' AND TG_OP = 'UPDATE'
                         THEN 'Vozacot go otkaza vozenjeto, barame nov vozac'
                     WHEN v_new = 'cancelled' AND v_no_show
                         THEN 'Vozenjeto e otkazano bidejki ne se pojavivte'
                     ELSE CASE v_new
                     WHEN 'requested'      THEN 'Vaseto baranje e uspesno isprateno'
                     WHEN 'accepted'       THEN 'Vozacot go prifati vaseto baranje'
                     WHEN 'driver_arrived' THEN 'Vozacot pristigna na lokacijata za podiganje'
                     WHEN 'in_progress'    THEN 'Vozenjeto zapocna'
                     WHEN 'completed'      THEN 'Vozenjeto e uspesno zavrseno'
                     WHEN 'no_driver'      THEN 'Nemate dostapni vozaci vo momentot'
                     WHEN 'cancelled'      THEN
                         CASE v_cancelled_by
                             WHEN 'DRIVER' THEN 'Vozenjeto e otkazano od strana na vozacot'
                             WHEN 'ADMIN'  THEN 'Vozenjeto e otkazano od administrator'
                             ELSE               'Vozenjeto e otkazano od vasa strana'
                             END
                     END
        END;

    IF v_message IS NOT NULL THEN
        PERFORM fn_send_notification(NEW.user_id, NEW.ride_id, v_message);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ride_notification
    AFTER INSERT OR UPDATE OF status_id ON Rides
    FOR EACH ROW EXECUTE FUNCTION trg_fn_ride_notification();


-- 5.5 Достапност и локација на возачот според статусот на возењето
CREATE OR REPLACE FUNCTION trg_fn_driver_availability()
    RETURNS TRIGGER AS $$
DECLARE
    v_old VARCHAR := fn_get_status_text(OLD.status_id);
    v_new VARCHAR := fn_get_status_text(NEW.status_id);
BEGIN
    IF v_old = v_new OR NEW.driver_id IS NULL THEN
        RETURN NEW;
    END IF;

    IF v_new IN ('cancelled', 'requested')
        AND v_old IN ('accepted', 'driver_arrived', 'in_progress') THEN
        -- откажано, или возачот се откажал и возењето оди кај друг -> возачот е пак слободен
        UPDATE Active_drivers
        SET is_available = (working_end IS NULL)
        WHERE driver_id = NEW.driver_id;

    ELSIF v_new = 'accepted' THEN
        UPDATE Active_drivers
        SET is_available = false
        WHERE driver_id = NEW.driver_id;

    ELSIF v_new = 'completed' THEN
        -- возачот е пак слободен и се наоѓа на дестинацијата
        UPDATE Active_drivers
        SET is_available        = (working_end IS NULL),
            current_location_id = NEW.dropoff_location_id
        WHERE driver_id = NEW.driver_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_driver_availability
    AFTER UPDATE OF status_id ON Rides
    FOR EACH ROW EXECUTE FUNCTION trg_fn_driver_availability();


-- 5.6 На завршено возење се креира плаќање (pending, CASH по default;
--     начинот се менува во sp_process_payment)
CREATE OR REPLACE FUNCTION trg_fn_create_payment()
    RETURNS TRIGGER AS $$
BEGIN
    IF fn_get_status_text(NEW.status_id) = 'completed'
        AND fn_get_status_text(OLD.status_id) <> 'completed' THEN
        INSERT INTO Payments (amount, payment_method_id, payment_time, ride_id, status_id)
        VALUES (NEW.final_price,
                (SELECT payment_method_id FROM Payment_methods WHERE payment_tip = 'CASH'),
                now(),
                NEW.ride_id,
                fn_get_status_id('payment', 'pending'));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_create_payment
    AFTER UPDATE OF status_id ON Rides
    FOR EACH ROW EXECUTE FUNCTION trg_fn_create_payment();


-- 5.7 Известување за успешно / неуспешно плаќање
CREATE OR REPLACE FUNCTION trg_fn_payment_notification()
    RETURNS TRIGGER AS $$
DECLARE
    v_new     VARCHAR := fn_get_status_text(NEW.status_id);
    v_user_id INT;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.status_id = NEW.status_id THEN
        RETURN NEW;
    END IF;
    IF v_new IN ('completed', 'failed', 'refunded') THEN
        SELECT user_id INTO v_user_id FROM Rides WHERE ride_id = NEW.ride_id;
        PERFORM fn_send_notification(
                v_user_id, NEW.ride_id,
                CASE v_new WHEN 'completed' THEN 'Plakjanjeto e uspesno'
                           WHEN 'failed'    THEN 'Plakjanjeto ne uspea'
                           ELSE                  'Parite vi se vrateni' END);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_payment_notification
    AFTER INSERT OR UPDATE OF status_id ON Payments
    FOR EACH ROW EXECUTE FUNCTION trg_fn_payment_notification();


-- 5.8 Запис во Cancellations го откажува возењето
--     (премините ги проверува trg_ride_status_transition - завршено возење не може да се откаже).
--     Возење во тек може да прекине само администратор.
--     Ако има казна, се креира плаќање (pending) што корисникот треба да го плати.
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
                (SELECT payment_method_id FROM Payment_methods WHERE payment_tip = 'CASH'),
                now(),
                NEW.ride_id,
                fn_get_status_id('payment', 'pending'));
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_apply_cancellation
    AFTER INSERT ON Cancellations
    FOR EACH ROW EXECUTE FUNCTION trg_fn_apply_cancellation();


-- 5.9 Известување кога возач ќе го одбие возењето (само за експлицитно одбивање;
--     за TIMEOUT корисникот не се вознемирува, а за DRIVER_CANCELLED веќе добил порака)
CREATE OR REPLACE FUNCTION trg_fn_rejection_notification()
    RETURNS TRIGGER AS $$
BEGIN
    IF NEW.reason = 'REJECTED' THEN
        PERFORM fn_send_notification(
                (SELECT user_id FROM Rides WHERE ride_id = NEW.ride_id),
                NEW.ride_id,
                'Vozacot go odbi vaseto baranje');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_rejection_notification
    AFTER INSERT ON Ride_rejections
    FOR EACH ROW EXECUTE FUNCTION trg_fn_rejection_notification();


-- 5.10 Оценка е дозволена само за завршено возење, од неговиот корисник, за неговиот возач
CREATE OR REPLACE FUNCTION trg_fn_validate_rating()
    RETURNS TRIGGER AS $$
DECLARE
    v_ride Rides%ROWTYPE;
BEGIN
    SELECT * INTO v_ride FROM Rides WHERE ride_id = NEW.ride_id;
    IF fn_get_status_text(v_ride.status_id) <> 'completed' THEN
        RAISE EXCEPTION 'Возењето % не е завршено и не може да се оцени', NEW.ride_id;
    END IF;
    IF NEW.User_id <> v_ride.user_id THEN
        RAISE EXCEPTION 'Корисникот % не го направил возењето %', NEW.User_id, NEW.ride_id;
    END IF;
    IF NEW.driver_id <> v_ride.driver_id THEN
        RAISE EXCEPTION 'Возачот % не го возел возењето %', NEW.driver_id, NEW.ride_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_rating
    BEFORE INSERT OR UPDATE ON Ratings
    FOR EACH ROW EXECUTE FUNCTION trg_fn_validate_rating();


-- 5.11 Просечна оценка на возачот = AVG од сите негови оценки
CREATE OR REPLACE FUNCTION trg_fn_update_driver_rating()
    RETURNS TRIGGER AS $$
DECLARE
    v_driver_id INT := COALESCE(NEW.driver_id, OLD.driver_id);
BEGIN
    UPDATE Drivers
    SET rating = COALESCE((SELECT ROUND(AVG(score), 2) FROM Ratings WHERE driver_id = v_driver_id), 0)
    WHERE driver_id = v_driver_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_driver_rating
    AFTER INSERT OR UPDATE OF score OR DELETE ON Ratings
    FOR EACH ROW EXECUTE FUNCTION trg_fn_update_driver_rating();


-- 5.12 Секое ново плаќање ја добива тековната провизија на платформата
CREATE OR REPLACE FUNCTION trg_fn_payment_commission()
    RETURNS TRIGGER AS $$
BEGIN
    NEW.commission_percentage := COALESCE(
            (SELECT commission_percentage FROM Platform_settings WHERE setting_id = 1), 0);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_payment_commission
    BEFORE INSERT ON Payments
    FOR EACH ROW EXECUTE FUNCTION trg_fn_payment_commission();


-- =====================================================================
-- 6. ФУНКЦИИ И ПРОЦЕДУРИ ЗА ТЕКОТ НА ВОЗЕЊЕТО
--    Од Django:  cursor.execute("CALL sp_accept_ride(%s, %s)", [ride_id, driver_id])
--                cursor.execute("SELECT fn_request_ride(%s, %s, %s)", [...])
-- =====================================================================

-- 6.0 Администратор креира профил на возач. Враќа driver_id.
--     p_password_hash = хеш од Django (make_password), никогаш обична лозинка.
--     p_vehicle_ownership_id (опционално) = возило што веднаш се доделува на возачот.
CREATE OR REPLACE FUNCTION fn_admin_create_driver(
    p_admin_id INT,
    p_name VARCHAR,
    p_username VARCHAR,
    p_phone VARCHAR,
    p_email VARCHAR,
    p_password_hash TEXT,
    p_license_number VARCHAR,
    p_vehicle_ownership_id INT DEFAULT NULL
)
    RETURNS INT AS $$
DECLARE
    v_driver_id INT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM Admins
                   WHERE admins_id = p_admin_id
                     AND status_id = fn_get_status_id('admin', 'active')) THEN
        RAISE EXCEPTION 'Администраторот % не постои или не е активен', p_admin_id;
    END IF;

    INSERT INTO Drivers (name, username, phone, email, password_hash, license_number,
                         registration_date, rating, status_id, created_at, created_by_admin_id)
    VALUES (p_name, p_username, p_phone, p_email, p_password_hash, p_license_number,
            current_date, 5.00, fn_get_status_id('driver', 'active'), now(), p_admin_id)
    RETURNING driver_id INTO v_driver_id;

    IF p_vehicle_ownership_id IS NOT NULL THEN
        INSERT INTO Drivers_Vehicle_ownership (driver_id, Vehicle_ownership_id)
        VALUES (v_driver_id, p_vehicle_ownership_id);
    END IF;

    RETURN v_driver_id;
END;
$$ LANGUAGE plpgsql;

-- 6.1 Возачот почнува смена со едно од своите возила (не ако е блокиран поради неплатена провизија)
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

-- 6.2 Возачот завршува смена (не може додека има активно возење).
--     Понудените (уште неприфатени) возења се префрлаат на друг возач.
CREATE OR REPLACE PROCEDURE sp_driver_end_shift(p_driver_id INT) AS $$
DECLARE
    v_ride_id INT;
BEGIN
    IF EXISTS (SELECT 1 FROM Rides r
                                 JOIN Status s ON r.status_id = s.status_id
               WHERE r.driver_id = p_driver_id
                 AND s.status_text IN ('accepted', 'driver_arrived', 'in_progress')) THEN
        RAISE EXCEPTION 'Возачот % има активно возење и не може да ја заврши смената', p_driver_id;
    END IF;

    UPDATE Active_drivers
    SET is_available = false,
        working_end  = now()
    WHERE driver_id = p_driver_id
      AND working_end IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Возачот % нема отворена смена', p_driver_id;
    END IF;

    FOR v_ride_id IN
        SELECT ride_id FROM Rides
        WHERE driver_id = p_driver_id
          AND status_id = fn_get_status_id('ride', 'requested')
        LOOP
            UPDATE Rides SET driver_id = NULL WHERE ride_id = v_ride_id;
            PERFORM fn_assign_driver(v_ride_id);
        END LOOP;
END;
$$ LANGUAGE plpgsql;

-- 6.3 Нова моментална локација на возачот
CREATE OR REPLACE PROCEDURE sp_update_driver_location(p_driver_id INT, p_location_id INT) AS $$
BEGIN
    UPDATE Active_drivers
    SET current_location_id = p_location_id
    WHERE driver_id = p_driver_id
      AND working_end IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Возачот % нема отворена смена', p_driver_id;
    END IF;
END;
$$ LANGUAGE plpgsql;

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

-- 6.4 Доделува најблизок возач на возење (повикува се од fn_request_ride, sp_reject_ride,
--     sp_cancel_ride и sp_driver_end_shift). Ако нема возач, возењето станува no_driver.
--     Враќа driver_id или NULL.
--     Заштита од истовремени нарачки: редот на возачот во Active_drivers се заклучува
--     (FOR UPDATE SKIP LOCKED) и повторно се проверува дека е слободен, па двајца
--     корисници во иста секунда не можат да го добијат истиот возач.
CREATE OR REPLACE FUNCTION fn_assign_driver(p_ride_id INT)
    RETURNS INT AS $$
DECLARE
    v_ride      Rides%ROWTYPE;
    v_candidate INT;
    v_driver_id INT;
BEGIN
    SELECT * INTO v_ride FROM Rides WHERE ride_id = p_ride_id;

    FOR v_candidate IN
        SELECT f.driver_id
        FROM fn_find_nearest_driver(v_ride.pickup_location_id, v_ride.ride_id,
                                    v_ride.requested_vehicle_class, 10) f
        LOOP
            -- заклучи го возачот; ако друга трансакција веќе го зема, прескокни го
            PERFORM 1 FROM Active_drivers
            WHERE driver_id = v_candidate
              AND is_available
              AND working_end IS NULL
                FOR UPDATE SKIP LOCKED;
            CONTINUE WHEN NOT FOUND;

            -- повторна проверка по заклучувањето (друга нарачка можеби штотуку завршила)
            CONTINUE WHEN EXISTS (SELECT 1 FROM Rides r
                                                    JOIN Status s ON r.status_id = s.status_id
                                  WHERE r.driver_id = v_candidate
                                    AND r.ride_id <> p_ride_id
                                    AND s.status_tip = 'ride'
                                    AND s.status_text IN ('requested', 'accepted', 'driver_arrived', 'in_progress'));

            v_driver_id := v_candidate;
            EXIT;
        END LOOP;

    IF v_driver_id IS NULL THEN
        UPDATE Rides
        SET driver_id  = NULL,
            offered_at = NULL,
            status_id  = fn_get_status_id('ride', 'no_driver')
        WHERE ride_id = p_ride_id;
    ELSE
        UPDATE Rides
        SET driver_id  = v_driver_id,
            offered_at = now()
        WHERE ride_id = p_ride_id;
    END IF;
    RETURN v_driver_id;
END;
$$ LANGUAGE plpgsql;

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

-- Помошна: го заклучува возењето и проверува дека е на тој возач и во очекуваниот статус
CREATE OR REPLACE FUNCTION fn_lock_driver_ride(p_ride_id INT, p_driver_id INT, p_expected_status VARCHAR)
    RETURNS Rides AS $$
DECLARE
    v_ride Rides%ROWTYPE;
BEGIN
    SELECT * INTO v_ride FROM Rides WHERE ride_id = p_ride_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Возењето % не постои', p_ride_id;
    END IF;
    IF v_ride.driver_id IS DISTINCT FROM p_driver_id THEN
        RAISE EXCEPTION 'Возењето % не е доделено на возачот %', p_ride_id, p_driver_id;
    END IF;
    IF fn_get_status_text(v_ride.status_id) <> p_expected_status THEN
        RAISE EXCEPTION 'Возењето % е во статус %, а се очекува %',
            p_ride_id, fn_get_status_text(v_ride.status_id), p_expected_status;
    END IF;
    RETURN v_ride;
END;
$$ LANGUAGE plpgsql;

-- 6.6 Возачот го прифаќа возењето
CREATE OR REPLACE PROCEDURE sp_accept_ride(p_ride_id INT, p_driver_id INT) AS $$
BEGIN
    PERFORM fn_lock_driver_ride(p_ride_id, p_driver_id, 'requested');

    UPDATE Rides
    SET status_id            = fn_get_status_id('ride', 'accepted'),
        vehicle_ownership_id = (SELECT vehicle_ownership_id FROM Active_drivers
                                WHERE driver_id = p_driver_id)
    WHERE ride_id = p_ride_id;
END;
$$ LANGUAGE plpgsql;

-- 6.7 Возачот го одбива возењето -> се бара следниот најблизок возач
--     p_reason: 'REJECTED' (возачот одбил) или 'TIMEOUT' (од fn_expire_offers)
CREATE OR REPLACE PROCEDURE sp_reject_ride(
    p_ride_id INT,
    p_driver_id INT,
    p_reason VARCHAR DEFAULT 'REJECTED'
) AS $$
BEGIN
    PERFORM fn_lock_driver_ride(p_ride_id, p_driver_id, 'requested');

    INSERT INTO Ride_rejections (ride_id, driver_id, rejected_at, reason)
    VALUES (p_ride_id, p_driver_id, now(), p_reason);

    UPDATE Rides SET driver_id = NULL, offered_at = NULL WHERE ride_id = p_ride_id;
    PERFORM fn_assign_driver(p_ride_id);
END;
$$ LANGUAGE plpgsql;

-- 6.7a Понуди на кои возачот не одговорил p_timeout_seconds секунди се третираат како
--      одбивање (TIMEOUT) и одат кај следниот возач. Django ја повикува на неколку секунди:
--      cursor.execute("SELECT fn_expire_offers(30)"). Враќа колку понуди истекле.
CREATE OR REPLACE FUNCTION fn_expire_offers(p_timeout_seconds INT DEFAULT 30)
    RETURNS INT AS $$
DECLARE
    v_ride  RECORD;
    v_count INT := 0;
BEGIN
    FOR v_ride IN
        SELECT r.ride_id, r.driver_id
        FROM Rides r
        WHERE r.status_id  = fn_get_status_id('ride', 'requested')
          AND r.driver_id  IS NOT NULL
          AND r.offered_at <= now() - make_interval(secs => p_timeout_seconds)
            FOR UPDATE SKIP LOCKED
        LOOP
            CALL sp_reject_ride(v_ride.ride_id, v_ride.driver_id, 'TIMEOUT');
            v_count := v_count + 1;
        END LOOP;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- 6.8 Возачот пристигна на локацијата за подигање
CREATE OR REPLACE PROCEDURE sp_driver_arrived(p_ride_id INT, p_driver_id INT) AS $$
BEGIN
    PERFORM fn_lock_driver_ride(p_ride_id, p_driver_id, 'accepted');

    UPDATE Rides
    SET status_id   = fn_get_status_id('ride', 'driver_arrived'),
        pickup_time = now()
    WHERE ride_id = p_ride_id;
END;
$$ LANGUAGE plpgsql;

-- 6.9 Возењето започнува
CREATE OR REPLACE PROCEDURE sp_start_ride(p_ride_id INT, p_driver_id INT) AS $$
BEGIN
    PERFORM fn_lock_driver_ride(p_ride_id, p_driver_id, 'driver_arrived');

    UPDATE Rides
    SET status_id  = fn_get_status_id('ride', 'in_progress'),
        start_time = now()
    WHERE ride_id = p_ride_id;
END;
$$ LANGUAGE plpgsql;

-- 6.10 Возењето завршува: финална цена, completed (тригерите креираат плаќање
--      и го ослободуваат возачот). p_distance_km = вистинската дистанца од апликацијата;
--      ако е NULL се користи дистанцата пресметана при нарачката.
CREATE OR REPLACE PROCEDURE sp_complete_ride(
    p_ride_id INT,
    p_driver_id INT,
    p_distance_km NUMERIC DEFAULT NULL
) AS $$
DECLARE
    v_ride  Rides%ROWTYPE;
    v_km    NUMERIC;
    v_price RECORD;
BEGIN
    v_ride := fn_lock_driver_ride(p_ride_id, p_driver_id, 'in_progress');
    v_km   := COALESCE(p_distance_km, v_ride.distance_km);

    SELECT p.* INTO v_price
    FROM Vehicle_ownership vo,
         fn_calculate_price(vo.vehicle_type_id, v_km) p
    WHERE vo.vehicle_ownership_id = v_ride.vehicle_ownership_id;
    IF v_price IS NULL THEN
        RAISE EXCEPTION 'Нема тарифа за возилото на возењето %', p_ride_id;
    END IF;

    UPDATE Rides
    SET status_id           = fn_get_status_id('ride', 'completed'),
        end_time            = now(),
        distance_km         = v_km,
        final_price         = v_price.final_price,
        discount_percentage = v_price.discount_percentage
    WHERE ride_id = p_ride_id;
END;
$$ LANGUAGE plpgsql;

-- 6.11 Плаќање на завршено возење. p_payment_tip = 'CASH' / 'CARD',
--      p_success = false симулира неуспешно плаќање со картичка (може повторно да се обиде)
CREATE OR REPLACE PROCEDURE sp_process_payment(
    p_ride_id INT,
    p_payment_tip VARCHAR,
    p_success BOOLEAN DEFAULT true
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

    UPDATE Payments
    SET payment_method_id = v_method_id,
        payment_time      = now(),
        status_id         = fn_get_status_id('payment',
                                             CASE WHEN p_success THEN 'completed' ELSE 'failed' END)
    WHERE payment_id = v_payment_id;
END;
$$ LANGUAGE plpgsql;

-- 6.12 Откажување (USER / DRIVER / ADMIN). Казната се пресметува автоматски,
--      а тригерот trg_apply_cancellation го менува статусот на возењето
--      и креира плаќање за казната ако е поголема од 0.
--
--      USER:   requested / accepted / driver_arrived -> cancelled (0 / 50% / 100% од базата)
--      DRIVER: возењето НЕ се откажува, туку оди кај друг возач:
--                requested                -> исто како sp_reject_ride
--                accepted / driver_arrived -> назад во requested, бара нов возач
--              p_no_show = true (во driver_arrived): корисникот не се појавил ->
--                cancelled, корисникот плаќа цела базна цена
--      ADMIN:  во кој било недовршен статус, вклучително in_progress (итен случај), без казна
CREATE OR REPLACE PROCEDURE sp_cancel_ride(
    p_ride_id INT,
    p_cancelled_by_type VARCHAR,
    p_cancelled_by_id INT,
    p_reason VARCHAR,
    p_no_show BOOLEAN DEFAULT false
) AS $$
DECLARE
    v_ride   Rides%ROWTYPE;
    v_status VARCHAR;
BEGIN
    SELECT * INTO v_ride FROM Rides WHERE ride_id = p_ride_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Возењето % не постои', p_ride_id;
    END IF;
    v_status := fn_get_status_text(v_ride.status_id);

    IF p_cancelled_by_type = 'USER' AND v_ride.user_id <> p_cancelled_by_id THEN
        RAISE EXCEPTION 'Корисникот % не може да го откаже туѓото возење %', p_cancelled_by_id, p_ride_id;
    ELSIF p_cancelled_by_type = 'DRIVER' AND v_ride.driver_id IS DISTINCT FROM p_cancelled_by_id THEN
        RAISE EXCEPTION 'Возачот % не е доделен на возењето %', p_cancelled_by_id, p_ride_id;
    ELSIF p_cancelled_by_type = 'ADMIN'
        AND NOT EXISTS (SELECT 1 FROM Admins WHERE admins_id = p_cancelled_by_id) THEN
        RAISE EXCEPTION 'Администраторот % не постои', p_cancelled_by_id;
    END IF;

    IF p_no_show AND (p_cancelled_by_type <> 'DRIVER' OR v_status <> 'driver_arrived') THEN
        RAISE EXCEPTION 'No-show може да пријави само возачот, откако пристигнал на локацијата';
    END IF;

    -- Возачот се откажува: возењето оди кај друг возач наместо да се откаже
    IF p_cancelled_by_type = 'DRIVER' AND NOT p_no_show THEN
        IF v_status = 'requested' THEN
            CALL sp_reject_ride(p_ride_id, p_cancelled_by_id, 'REJECTED');
            RETURN;
        ELSIF v_status IN ('accepted', 'driver_arrived') THEN
            -- 1) назад во requested (тригерите го ослободуваат возачот и го известуваат корисникот)
            UPDATE Rides
            SET status_id = fn_get_status_id('ride', 'requested')
            WHERE ride_id = p_ride_id;
            -- 2) тргни го возачот и возилото од возењето
            UPDATE Rides
            SET driver_id = NULL, vehicle_ownership_id = NULL,
                offered_at = NULL, pickup_time = NULL
            WHERE ride_id = p_ride_id;
            -- 3) запамети го за да не му се понуди повторно, па барај нов возач
            INSERT INTO Ride_rejections (ride_id, driver_id, rejected_at, reason)
            VALUES (p_ride_id, p_cancelled_by_id, now(), 'DRIVER_CANCELLED');
            PERFORM fn_assign_driver(p_ride_id);
            RETURN;
        END IF;
    END IF;

    INSERT INTO Cancellations (cancelled_by_type, cancelled_by_id, reason,
                               cancellation_fee, created_at, ride_id, is_no_show)
    VALUES (p_cancelled_by_type, p_cancelled_by_id, p_reason,
            fn_cancellation_fee(p_ride_id, p_cancelled_by_type, p_no_show), now(), p_ride_id,
            p_no_show);
END;
$$ LANGUAGE plpgsql;

-- 6.12a Враќање пари за платено плаќање (само администратор)
CREATE OR REPLACE PROCEDURE sp_refund_payment(p_payment_id INT, p_admin_id INT) AS $$
DECLARE
    v_status VARCHAR;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM Admins
                   WHERE admins_id = p_admin_id
                     AND status_id = fn_get_status_id('admin', 'active')) THEN
        RAISE EXCEPTION 'Администраторот % не постои или не е активен', p_admin_id;
    END IF;

    SELECT fn_get_status_text(status_id) INTO v_status
    FROM Payments WHERE payment_id = p_payment_id FOR UPDATE;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'Плаќањето % не постои', p_payment_id;
    END IF;
    IF v_status <> 'completed' THEN
        RAISE EXCEPTION 'Може да се вратат само платени плаќања (плаќањето % е %)', p_payment_id, v_status;
    END IF;

    UPDATE Payments
    SET status_id = fn_get_status_id('payment', 'refunded')
    WHERE payment_id = p_payment_id;
END;
$$ LANGUAGE plpgsql;

-- 6.13 Корисникот го оценува возачот (проверките ги прави trg_validate_rating,
--      а просекот на возачот го ажурира trg_update_driver_rating)
CREATE OR REPLACE PROCEDURE sp_rate_ride(
    p_ride_id INT,
    p_user_id INT,
    p_score INT,
    p_comment VARCHAR DEFAULT NULL
) AS $$
BEGIN
    INSERT INTO Ratings (score, comment, created_at, ride_id, User_id, driver_id)
    SELECT p_score, p_comment, now(), r.ride_id, p_user_id, r.driver_id
    FROM Rides r
    WHERE r.ride_id = p_ride_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Возењето % не постои', p_ride_id;
    END IF;
END;
$$ LANGUAGE plpgsql;


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


-- =====================================================================
-- 7. VIEWS
-- =====================================================================

CREATE OR REPLACE VIEW vw_user_notifications AS
SELECT
    n.notification_id,
    -- Korisnik
    u.user_id,
    u.name                                  AS user_name,
    -- Poraka
    m.message_id,
    m.message_text,
    n.sent_time,
    -- Za koja voznja se odnesuva
    n.ride_id,
    r.request_time                          AS ride_request_time,
    -- Kratka adresa za kontekst
    lp.naselba                              AS pickup_naselba,
    lp.ulica || ' ' || lp.broj             AS pickup_address,
    ld.ulica || ' ' || ld.broj             AS dropoff_address,
    -- Status na voznata (za kontekst vo notifikacijata)
    sr.status_text                          AS ride_status,
    -- Status na notifikacijata (sent / fail)
    sn.status_text                          AS notification_status
FROM Notifications n
         JOIN Users u            ON n.user_id                    = u.user_id
         JOIN Messages m         ON n.message_id                 = m.message_id
         JOIN Rides r            ON n.ride_id                    = r.ride_id
         JOIN Status sn          ON n.status_id                  = sn.status_id
         JOIN Status sr          ON r.status_id                  = sr.status_id
         JOIN Locations lp       ON r.pickup_location_id         = lp.location_id
         JOIN Locations ld       ON r.dropoff_location_id        = ld.location_id;

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
    sr.status_text                          AS ride_status
FROM Payments p
         JOIN Rides r            ON p.ride_id                    = r.ride_id
         JOIN Users u            ON r.user_id                    = u.user_id
         JOIN Drivers d          ON r.driver_id                  = d.driver_id
         JOIN Payment_methods pm ON p.payment_method_id          = pm.payment_method_id
         JOIN Status sp          ON p.status_id                  = sp.status_id
         JOIN Status sr          ON r.status_id                  = sr.status_id
         JOIN Locations lp       ON r.pickup_location_id         = lp.location_id
         JOIN Locations ld       ON r.dropoff_location_id        = ld.location_id;

-- Поправено: возилото се зема од тековната смена (Active_drivers),
-- а не од сите возила на возачот -> нема дупли редови
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

-- Поправено: возачот може да е NULL (откажано пред да се најде возач)
CREATE OR REPLACE VIEW vw_cancellation_details AS
SELECT
    c.cancellation_id,
    c.ride_id,
    -- Koj otkazal
    c.cancelled_by_type,                    -- USER / DRIVER / ADMIN
    c.cancelled_by_id,
    c.reason,
    c.is_no_show,
    c.cancellation_fee,
    c.created_at                            AS cancelled_at,
    -- Korisnik na voznata
    u.user_id,
    u.name                                  AS user_name,
    u.phone                                 AS user_phone,
    -- Vozac na voznata
    d.driver_id,
    d.name                                  AS driver_name,
    d.phone                                 AS driver_phone,
    -- Vozilo (za da se vidi dali e Economy ili Luxury)
    vt.description                          AS vehicle_class,
    pr.base_price                           AS tariff_base_price,
    -- Pickup lokacija
    lp.naselba                              AS pickup_naselba,
    lp.ulica || ' ' || lp.broj             AS pickup_address,
    -- Vreme na baranjeto
    r.request_time,
    r.distance_km,
    -- Status na voznata
    s.status_text                           AS ride_status
FROM Cancellations c
         JOIN Rides r            ON c.ride_id                    = r.ride_id
         JOIN Users u            ON r.user_id                    = u.user_id
         LEFT JOIN Drivers d     ON r.driver_id                  = d.driver_id
         JOIN Status s           ON r.status_id                  = s.status_id
         JOIN Locations lp       ON r.pickup_location_id         = lp.location_id
         LEFT JOIN Vehicle_ownership vo      ON r.Vehicle_ownership_id = vo.Vehicle_ownership_id
         LEFT JOIN Vehicle_types vt          ON vo.vehicle_type_id       = vt.vehicle_type_id
         LEFT JOIN Pricing_rules pr          ON vt.vehicle_type_id       = pr.vehicle_type_id;

-- Поправено: без ORDER BY (подредувањето го прави апликацијата)
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

-- Поправено: без ORDER BY
CREATE OR REPLACE VIEW vw_drivers_near_center AS
SELECT
    d.driver_id,
    d.name                  AS driver_name,
    d.rating,
    l.naselba,
    l.ulica,
    vt.description          AS vehicle_class,
    vo.plate                AS vehicle_plate,
    ad.is_available,
    l.geom,
    ROUND(ST_Distance(
                  l.geom,
                  ST_MakePoint(21.7144, 42.1322)::GEOGRAPHY
          )::NUMERIC, 0)          AS meters_from_center
FROM Active_drivers ad
         JOIN Drivers d            ON ad.driver_id            = d.driver_id
         JOIN Locations l          ON ad.current_location_id  = l.location_id
         JOIN Vehicle_ownership vo ON ad.vehicle_ownership_id = vo.vehicle_ownership_id
         JOIN Vehicle_types vt     ON vo.vehicle_type_id      = vt.vehicle_type_id
WHERE ST_DWithin(
              l.geom,
              ST_MakePoint(21.7144, 42.1322)::GEOGRAPHY,
              2000
      );

-- ---------------------------------------------------------------------
-- Нови views
-- ---------------------------------------------------------------------

-- Тековно (недовршено) возење на корисник или возач
-- Django: WHERE user_id = %s   или   WHERE driver_id = %s
CREATE OR REPLACE VIEW vw_current_ride AS
SELECT *
FROM vw_ride_details
WHERE ride_status IN ('requested', 'accepted', 'driver_arrived', 'in_progress');

-- Барања што чекаат одговор од возачот (прифати / одбиј)
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
    price.final_price                       AS estimated_price
FROM Rides r
         JOIN Status s            ON r.status_id            = s.status_id
         JOIN Users u             ON r.user_id              = u.user_id
         JOIN Locations lp        ON r.pickup_location_id   = lp.location_id
         JOIN Locations ld        ON r.dropoff_location_id  = ld.location_id
         JOIN Active_drivers ad   ON r.driver_id            = ad.driver_id
         JOIN Locations lc        ON ad.current_location_id = lc.location_id
         JOIN Vehicle_ownership vo ON ad.vehicle_ownership_id = vo.vehicle_ownership_id
         LEFT JOIN LATERAL fn_calculate_price(vo.vehicle_type_id, r.distance_km) price ON true
         LEFT JOIN LATERAL (SELECT ROUND(AVG(score), 2) AS avg_given_score
                            FROM Ratings WHERE User_id = r.user_id) u_rating ON true
WHERE s.status_text = 'requested';

-- Историја на возења на корисник (со плаќање, оценка и откажување)
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
    c.cancellation_fee
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
                            ORDER BY cc.cancellation_id DESC LIMIT 1) c ON true;

-- Заработка на возач по ден (за месец: GROUP BY ride_month во апликацијата)
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

-- Оценки и коментари за возач
CREATE OR REPLACE VIEW vw_driver_ratings AS
SELECT
    rt.rating_id,
    rt.driver_id,
    d.name                                  AS driver_name,
    d.rating                                AS driver_avg_rating,
    rt.score,
    rt.comment,
    rt.created_at,
    rt.ride_id,
    r.end_time                              AS ride_date,
    u.user_id,
    u.name                                  AS user_name
FROM Ratings rt
         JOIN Drivers d ON rt.driver_id = d.driver_id
         JOIN Users u   ON rt.User_id   = u.user_id
         JOIN Rides r   ON rt.ride_id   = r.ride_id;

-- Ценовник по класа на возило
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

-- Дневна статистика за администраторот
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
