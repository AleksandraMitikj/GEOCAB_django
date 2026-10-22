// Заеднички помошни функции за мапите во GEOCAB (Leaflet + OpenStreetMap)
const GeocabMap = (function () {
    const SKOPJE = [41.9981, 21.4254];
    // OpenStreetMap ги блокира (403) барањата без Referer, а Django стандардно праќа
    // Referer само кон истиот сајт (SECURE_REFERRER_POLICY = 'same-origin')
    const REFERRER = 'strict-origin-when-cross-origin';

    function create(elementId) {
        const map = L.map(elementId).setView(SKOPJE, 13);
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19, attribution: '&copy; OpenStreetMap', referrerPolicy: REFERRER
        }).addTo(map);
        return map;
    }

    function icon(color) {
        return L.divIcon({
            className: '', iconSize: [22, 22], iconAnchor: [11, 11],
            html: `<div style="width:22px;height:22px;border-radius:50%;background:${color};border:3px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.5)"></div>`
        });
    }

    // Адреса од координати (Nominatim) во полињата id_<prefix>_grad/naselba/ulica/broj
    async function fillAddress(prefix, latlng) {
        const field = name => document.getElementById(`id_${prefix}_${name}`);
        try {
            const url = `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${latlng.lat}&lon=${latlng.lng}&accept-language=mk`;
            const a = (await (await fetch(url, {referrerPolicy: REFERRER})).json()).address || {};
            field('grad').value = a.city || a.town || a.village || a.municipality || '';
            field('naselba').value = a.suburb || a.neighbourhood || a.quarter || a.city_district || a.city || a.town || '';
            field('ulica').value = a.road || a.pedestrian || a.footway || '';
            field('broj').value = a.house_number || '';
        } catch (e) { /* без интернет - адресата се пишува рачно */ }
    }

    // Влечлив маркер што ги пополнува id_<prefix>_lat/lng и адресата
    function pointPicker(map, prefix, color, onChange) {
        const lat = document.getElementById(`id_${prefix}_lat`), lng = document.getElementById(`id_${prefix}_lng`);
        let marker = null;
        function set(latlng, lookup) {
            lat.value = latlng.lat.toFixed(6);
            lng.value = latlng.lng.toFixed(6);
            if (!marker) {
                marker = L.marker(latlng, {draggable: true, icon: icon(color)}).addTo(map);
                marker.on('dragend', e => set(e.target.getLatLng(), true));
            } else {
                marker.setLatLng(latlng);
            }
            if (lookup) fillAddress(prefix, latlng);
            if (onChange) onChange();
        }
        // точка од претходно испратена форма
        if (lat.value && lng.value) set(L.latLng(parseFloat(lat.value), parseFloat(lng.value)), false);
        return {set, get marker() { return marker; }};
    }

    // Икона за такси (возачот на мапата)
    function carIcon() {
        return L.divIcon({
            className: '', iconSize: [34, 34], iconAnchor: [17, 17],
            html: '<div style="width:34px;height:34px;border-radius:50%;background:#ffc107;border:3px solid #1b1f3b;display:grid;place-items:center;font-size:18px;box-shadow:0 2px 6px rgba(0,0,0,.4)">🚕</div>'
        });
    }

    // Пат по улиците (OSRM) низ две или повеќе точки (A → B → C ...):
    // {distance (m), duration (s), coords [[lat,lng]...], steps, legs: [{distance, duration, coords}]}
    const OSRM = 'https://router.project-osrm.org';
    async function route(...points) {
        points = points.flat().filter(Boolean);
        const path = points.map(p => `${p.lng},${p.lat}`).join(';');
        const url = `${OSRM}/route/v1/driving/${path}?overview=full&geometries=geojson&steps=true`;
        const data = await (await fetch(url, {referrerPolicy: REFERRER})).json();
        if (data.code !== 'Ok' || !data.routes.length) throw new Error('Нема пат');
        const r = data.routes[0];
        return {
            distance: r.distance, duration: r.duration,
            coords: r.geometry.coordinates.map(c => [c[1], c[0]]),
            legs: r.legs.map(leg => ({
                distance: leg.distance, duration: leg.duration,
                coords: leg.steps.flatMap(st => st.geometry.coordinates.map(c => [c[1], c[0]]))
            })),
            steps: r.legs.flatMap(leg => leg.steps).map(st => ({
                text: stepText(st), distance: st.distance
            })).filter(st => st.text)
        };
    }

    // Упатство за еден чекор на македонски (од OSRM maneuver)
    const MODIFIERS = {left: 'лево', right: 'десно', 'slight left': 'благо лево', 'slight right': 'благо десно',
                       'sharp left': 'остро лево', 'sharp right': 'остро десно', straight: 'право', uturn: 'полукружно'};
    function stepText(st) {
        const m = st.maneuver, road = st.name ? ` на ${st.name}` : '', dir = MODIFIERS[m.modifier] || '';
        switch (m.type) {
            case 'depart': return `Тргнете${road}`;
            case 'arrive': return 'Пристигнавте на целта';
            case 'turn': case 'end of road': return `Свртете ${dir}${road}`;
            case 'roundabout': case 'rotary': return `На кружниот тек излезете на ${m.exit || ''}. излез${road}`;
            case 'merge': case 'on ramp': case 'off ramp': case 'fork': return `Продолжете ${dir}${road}`;
            case 'new name': case 'continue': return dir && dir !== 'право' ? `Продолжете ${dir}${road}` : `Продолжете право${road}`;
            default: return '';
        }
    }

    function formatDistance(m) { return m >= 1000 ? `${(m / 1000).toFixed(1).replace('.', ',')} km` : `${Math.round(m / 10) * 10} m`; }
    function formatDuration(s) { const min = Math.max(1, Math.round(s / 60)); return min >= 60 ? `${Math.floor(min / 60)} ч ${min % 60} мин` : `${min} мин`; }

    // Линкови за навигација во апликациите на телефонот (бесплатно, без API клуч)
    const ll = p => `${p.lat.toFixed(6)},${p.lng.toFixed(6)}`;
    // Google Maps: тргнување (возачот) → попатни точки (корисникот) → дестинација
    function googleMapsLink(origin, destination, waypoints = []) {
        const params = new URLSearchParams({api: '1', destination: ll(destination), travelmode: 'driving'});
        if (origin) params.set('origin', ll(origin));
        if (waypoints.length) params.set('waypoints', waypoints.map(ll).join('|'));
        return `https://www.google.com/maps/dir/?${params}`;
    }
    // Waze поддржува само една дестинација.
    // Телефон: ја отвора Waze апликацијата, која тргнува од GPS.
    // Компјутер: веб-верзијата нема GPS, затоа се праќа и почетната точка (возачот).
    const isPhone = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
    function wazeLink(to, from = null) {
        const dest = `ll.${encodeURIComponent(ll(to))}`;
        if (isPhone || !from) return `https://waze.com/ul?ll=${encodeURIComponent(ll(to))}&navigate=yes`;
        return `https://www.waze.com/live-map/directions?navigate=yes&to=${dest}&from=ll.${encodeURIComponent(ll(from))}`;
    }

    // Центрира на точките. Ако мапата е креирана додека е скриена (големина 0), повторно
    // се центрира штом ќе стане видлива - инаку Leaflet ја покажува целата Земја.
    function fit(map, points, pad = 0.25) {
        const bounds = L.latLngBounds(points.filter(Boolean)).pad(pad);
        const el = map.getContainer();
        const apply = () => { map.invalidateSize(); map.fitBounds(bounds, {maxZoom: 16}); };
        apply();
        if (window.ResizeObserver) {
            const observer = new ResizeObserver(() => {
                if (el.clientWidth && el.clientHeight && map.getZoom() < 5) apply();
            });
            observer.observe(el);
        }
        document.addEventListener('visibilitychange', () => { if (!document.hidden && map.getZoom() < 5) apply(); });
    }

    return {create, icon, carIcon, fit, fillAddress, pointPicker, route, formatDistance, formatDuration, googleMapsLink, wazeLink};
})();
