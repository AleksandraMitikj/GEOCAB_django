-- =====================================================================
-- GEOCAB - задолжителни шифрарници
-- Извршете го ВЕДНАШ ПО geocab_schema.sql.
-- Функциите, процедурите и тригерите ги бараат овие редови по текст,
-- па текстовите не смеат да се менуваат.
-- =====================================================================

INSERT INTO Status (status_tip, status_text) VALUES
    ('user',         'active'),
    ('user',         'inactive'),
    ('user',         'blocked'),
    ('user',         'deleted'),
    ('driver',       'active'),
    ('driver',       'inactive'),
    ('admin',        'active'),
    ('admin',        'inactive'),
    ('admin',        'blocked'),
    ('ride',         'requested'),
    ('ride',         'accepted'),
    ('ride',         'driver_arrived'),
    ('ride',         'in_progress'),
    ('ride',         'completed'),
    ('ride',         'cancelled'),
    ('ride',         'no_driver'),
    ('payment',      'pending'),
    ('payment',      'completed'),
    ('payment',      'failed'),
    ('payment',      'refunded'),
    ('notification', 'sent'),
    ('notification', 'failed'),
    ('report',       'open'),
    ('report',       'resolved');

-- Провизија на платформата: GEOCAB 20%, возачот 80% (се менува во /admin/)
INSERT INTO Platform_settings (setting_id, commission_percentage) VALUES (1, 20);

INSERT INTO Payment_methods (payment_tip) VALUES
    ('CASH'),
    ('CARD');

INSERT INTO Messages (message_text) VALUES
    ('Vaseto baranje e uspesno isprateno'),
    ('Vozacot go prifati vaseto baranje'),
    ('Vozacot go odbi vaseto baranje'),
    ('Vozacot pristigna na lokacijata za podiganje'),
    ('Vozenjeto e otkazano od strana na vozacot'),
    ('Vozenjeto e otkazano od vasa strana'),
    ('Plakjanjeto e uspesno'),
    ('Plakjanjeto ne uspea'),
    ('Nemate dostapni vozaci vo momentot'),
    ('Vozenjeto zapocna'),
    ('Vozenjeto e uspesno zavrseno'),
    ('Vozenjeto e otkazano od administrator'),
    ('Vozacot go otkaza vozenjeto, barame nov vozac'),
    ('Vozenjeto e otkazano bidejki ne se pojavivte'),
    ('Parite vi se vrateni');
