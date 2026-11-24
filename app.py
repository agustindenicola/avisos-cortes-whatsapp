#!/usr/bin/env python3
import base64
import csv
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from http import cookies
from pathlib import Path
from urllib.parse import parse_qs, urlencode
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape as xml_escape
from zipfile import ZIP_DEFLATED, ZipFile
from io import BytesIO
from wsgiref.simple_server import make_server


ROOT = Path(__file__).resolve().parent


def load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(ROOT / ".env")

DATA_DIR = ROOT / "data"
UPLOAD_DIR = ROOT / "uploads"
DB_PATH = DATA_DIR / "cortes.db"
SECRET = os.environ.get("CORTES_SECRET", "cambiar-este-secreto-en-produccion")
ADMIN_USER = os.environ.get("CORTES_USER", "admin")
ADMIN_PASS = os.environ.get("CORTES_PASS", "cambiar-esta-contrasena")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "ciudad-wsp-demo")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
GROUP_CONNECTOR_TOKEN = os.environ.get("GROUP_CONNECTOR_TOKEN", "")
TESSDATA_PREFIX = os.environ.get(
    "TESSDATA_PREFIX",
    str(Path(os.environ.get("CONDA_PREFIX", "/home/agustin/miniconda3")) / "share" / "tessdata"),
)
os.environ.setdefault("TESSDATA_PREFIX", TESSDATA_PREFIX)
CRITICAL_RUBROS = {"SALUD", "ASISTENCIA", "BOMBEROS"}
PRIORITY_WEIGHTS = {
    "SALUD": 100,
    "BOMBEROS": 95,
    "ASISTENCIA": 85,
    "SERVICIOS VARIOS": 55,
    "COOPERATIVAS": 45,
    "EDUCACION": 70,
    "DISCAPACIDAD": 68,
    "REHABILITACION": 66,
    "COMEDORES": 62,
    "TERCERA EDAD": 60,
}

PRIORITY_LABELS = {
    "critical": "Prioridad 1",
    "high": "Prioridad 2",
    "medium": "Prioridad 3",
    "low": "Prioridad 4",
}

STATUSES = {
    "activo": "Activo",
    "programado": "Programado",
    "restablecido": "Restablecido",
    "pendiente": "En revisión",
    "ignorado": "Ignorado",
}

TYPES = {
    "imprevisto": "Imprevisto",
    "programado": "Programado",
    "restablecido": "Restablecido",
    "sin_clasificar": "Sin clasificar",
}


def db():
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        create table if not exists cortes (
            id integer primary key autoincrement,
            created_at text not null,
            source text not null default 'manual',
            whatsapp_group text,
            sender text,
            message text,
            attachment_name text,
            attachment_path text,
            attachment_sha256 text,
            restore_attachment_name text,
            restore_attachment_path text,
            restore_attachment_sha256 text,
            outage_type text not null default 'sin_clasificar',
            zone_label text,
            start_time text,
            end_time text,
            estimated_end text,
            status text not null default 'pendiente',
            notes text
        )
        """
    )
    return conn


def init_db():
    with db() as conn:
        columns = {row["name"] for row in conn.execute("pragma table_info(cortes)").fetchall()}
        if "attachment_sha256" not in columns:
            conn.execute("alter table cortes add column attachment_sha256 text")
        if "restore_attachment_name" not in columns:
            conn.execute("alter table cortes add column restore_attachment_name text")
        if "restore_attachment_path" not in columns:
            conn.execute("alter table cortes add column restore_attachment_path text")
        if "restore_attachment_sha256" not in columns:
            conn.execute("alter table cortes add column restore_attachment_sha256 text")
        if "end_time" not in columns:
            conn.execute("alter table cortes add column end_time text")
        conn.commit()


def now_label():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_operational_datetime(value, reference=None):
    text = str(value or "").strip()
    if not text:
        return None
    reference = reference or datetime.now()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if match:
        return reference.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
    return None


def schedule_status(start_value, end_value, reference=None):
    reference = reference or datetime.now()
    start = parse_operational_datetime(start_value, reference)
    end = parse_operational_datetime(end_value, start or reference)
    if start and end and end <= start:
        end = end + timedelta(days=1)
    if end and reference > end:
        return "restablecido"
    if start and reference < start:
        return "programado"
    if start:
        return "activo"
    return "programado"


def update_scheduled_cortes():
    now = datetime.now()
    updates = []
    with db() as conn:
        rows = conn.execute(
            """
            select * from cortes
            where outage_type = 'programado'
              and status in ('programado', 'activo')
            """
        ).fetchall()
        for row in rows:
            new_status = schedule_status(row["start_time"], row["estimated_end"], now)
            if new_status == row["status"]:
                continue
            note = (
                f"\nCambio automatico {now_label()}: programado "
                f"{'activado por horario' if new_status == 'activo' else 'cerrado por fin de ventana'}."
            )
            end_time = row["end_time"] or (now.strftime("%Y-%m-%d %H:%M:%S") if new_status == "restablecido" else "")
            updates.append((new_status, end_time, note, row["id"]))
        if updates:
            conn.executemany(
                """
                update cortes
                set status = ?,
                    end_time = ?,
                    notes = trim(coalesce(notes, '') || ?)
                where id = ?
                """,
                updates,
            )
            conn.commit()


def sign(value):
    digest = hmac.new(SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{digest}"


def verify_signed(value):
    if not value or "." not in value:
        return ""
    raw, digest = value.rsplit(".", 1)
    expected = hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return raw if hmac.compare_digest(digest, expected) else ""


def get_cookie(environ, name):
    jar = cookies.SimpleCookie(environ.get("HTTP_COOKIE", ""))
    if name not in jar:
        return ""
    return jar[name].value


def is_logged_in(environ):
    return verify_signed(get_cookie(environ, "session")) == ADMIN_USER


def redirect(start_response, location):
    start_response("302 Found", [("Location", location)])
    return [b""]


def response(start_response, body, status="200 OK", content_type="text/html; charset=utf-8", headers=None):
    all_headers = [("Content-Type", content_type)]
    if headers:
        all_headers.extend(headers)
    start_response(status, all_headers)
    if isinstance(body, str):
        body = body.encode("utf-8")
    return [body]


NO_STORE_HEADERS = [
    ("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"),
    ("Pragma", "no-cache"),
    ("Expires", "0"),
]


def health_api(start_response):
    payload = {
        "ok": True,
        "service": "municipio-ciudad",
        "time": now_label(),
        "database": "ok",
        "uploads": "ok" if UPLOAD_DIR.exists() else "missing",
    }
    try:
        with db() as conn:
            conn.execute("select 1").fetchone()
    except Exception as error:
        payload["ok"] = False
        payload["database"] = "error"
        payload["error"] = str(error)
    status = "200 OK" if payload["ok"] else "500 Internal Server Error"
    return response(
        start_response,
        json.dumps(payload, ensure_ascii=False),
        status=status,
        content_type="application/json; charset=utf-8",
    )


def esc(value):
    return html.escape(str(value or ""))


def normalize_text(value):
    replacements = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return str(value or "").translate(replacements).lower().strip()


def layout(title, content, active="dashboard"):
    def menu_link(key, href, label):
        return f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>'

    operation_active = active == "map"
    records_active = active in {"history", "cuts_dashboard"}
    system_active = active == "system"
    body_class = ' class="dashboard-page"' if active == "cuts_dashboard" else ""
    topbar_html = "" if active == "cuts_dashboard" else f"""
  <header class="topbar">
    <div class="brand">
      <img src="/assets/logo-ciudad.png" alt="">
      <div>
        <strong>Observatorio Municipal Inteligente</strong>
        <span>Panel operativo</span>
      </div>
    </div>
    <nav class="compact-nav" aria-label="Navegación principal">
      {menu_link("dashboard", "/", "Inicio")}
      <details class="nav-menu {"active" if operation_active else ""}">
        <summary>Operación</summary>
        <div>
          {menu_link("map", "/mapa.html?v=20260707-2", "Mapa operativo")}
          <a href="/historial?status=pendiente">Pendientes</a>
        </div>
      </details>
      <details class="nav-menu {"active" if records_active else ""}">
        <summary>Registros</summary>
        <div>
          {menu_link("history", "/historial", "Historial")}
          {menu_link("cuts_dashboard", "/dashboard-cortes", "Dashboard")}
          <a href="/export/cortes.xlsx">Exportar Excel</a>
          <a href="/export/cortes.csv">Exportar CSV</a>
        </div>
      </details>
      <details class="nav-menu {"active" if system_active else ""}">
        <summary>Sistema</summary>
        <div>
          {menu_link("system", "/sistema", "Estado")}
          <a href="/logout">Cerrar sesión</a>
        </div>
      </details>
    </nav>
  </header>"""
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} · Observatorio Municipal Inteligente</title>
  <link rel="stylesheet" href="/static/app.css?v=20260708-1">
</head>
<body{body_class}>
{topbar_html}
  <main class="content">
    {content}
  </main>
  <script>
    document.addEventListener("pointerup", event => {{
      const target = event.target.closest("button, .button, .home-icon-button, .compact-nav > a, .nav-menu summary, .nav-menu a");
      if (target && typeof target.blur === "function") {{
        window.setTimeout(() => target.blur(), 0);
      }}
    }});

    document.addEventListener("click", event => {{
      const clickedMenu = event.target.closest(".nav-menu");
      document.querySelectorAll(".nav-menu[open]").forEach(menu => {{
        if (menu !== clickedMenu || event.target.closest(".nav-menu a")) {{
          menu.removeAttribute("open");
        }}
      }});
    }});

    document.querySelectorAll(".nav-menu").forEach(menu => {{
      menu.addEventListener("toggle", () => {{
        if (!menu.open) return;
        document.querySelectorAll(".nav-menu[open]").forEach(other => {{
          if (other !== menu) other.removeAttribute("open");
        }});
      }});
    }});
  </script>
</body>
</html>"""


def login_page(error=""):
    error_html = f'<div class="alert">{esc(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Acceso · Observatorio Municipal Inteligente</title>
  <link rel="stylesheet" href="/static/app.css?v=20260706-1">
</head>
<body class="login-page">
  <main class="login-shell">
    <section class="login-card">
      <img src="/assets/logo-ciudad.png" alt="">
      <h1>Observatorio Municipal Inteligente</h1>
      {error_html}
      <form method="post" action="/login">
        <label>Usuario</label>
        <input name="username" autocomplete="username" required autofocus>
        <label>Contraseña</label>
        <input name="password" type="password" autocomplete="current-password" required>
        <button type="submit">Ingresar</button>
      </form>
    </section>
  </main>
</body>
</html>"""


def privacy_page():
    return """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Politica de privacidad</title>
  <link rel="stylesheet" href="/static/app.css?v=20260706-1">
</head>
<body class="login-page">
  <main class="login-card">
    <h1>Politica de privacidad</h1>
    <p>Esta aplicacion se utiliza para recibir avisos operativos de WhatsApp relacionados con cortes de servicio y registrarlos para seguimiento interno.</p>
    <p>Los datos recibidos pueden incluir nombre de perfil, numero de telefono, texto del mensaje, fecha, hora e imagenes enviadas por WhatsApp.</p>
    <p>La informacion se usa solo para gestionar avisos operativos, identificar zonas afectadas y mantener un historial de seguimiento.</p>
    <p>No vendemos datos personales ni los compartimos con terceros, salvo cuando sea necesario para operar el servicio o cumplir obligaciones legales.</p>
    <p>Para consultas sobre privacidad o eliminacion de datos, escribir a denicolaagustin46@gmail.com.</p>
  </main>
</body>
</html>"""


def data_deletion_page():
    return """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Eliminacion de datos</title>
  <link rel="stylesheet" href="/static/app.css?v=20260706-1">
</head>
<body class="login-page">
  <main class="login-card">
    <h1>Eliminacion de datos</h1>
    <p>Para solicitar la eliminacion de datos personales asociados a mensajes recibidos por esta aplicacion, enviar un correo a denicolaagustin46@gmail.com.</p>
    <p>Incluir el numero de WhatsApp utilizado, la fecha aproximada del mensaje y una descripcion breve de la solicitud.</p>
    <p>La solicitud sera revisada y se eliminaran los datos cuando corresponda, salvo que deban conservarse por razones operativas o legales.</p>
  </main>
</body>
</html>"""


def read_body(environ):
    length = int(environ.get("CONTENT_LENGTH") or 0)
    return environ["wsgi.input"].read(length)


def parse_urlencoded(environ):
    data = read_body(environ).decode("utf-8", errors="replace")
    return {key: values[0] if values else "" for key, values in parse_qs(data).items()}


def parse_json(environ):
    try:
        return json.loads(read_body(environ).decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return {}


def parse_multipart(environ):
    content_type = environ.get("CONTENT_TYPE", "")
    marker = "boundary="
    if marker not in content_type:
        return {}, {}
    boundary = content_type.split(marker, 1)[1].strip().strip('"')
    body = read_body(environ)
    delimiter = b"--" + boundary.encode()
    fields = {}
    files = {}
    for part in body.split(delimiter):
        part = part.strip(b"\r\n")
        if not part or part == b"--" or b"\r\n\r\n" not in part:
            continue
        raw_headers, value = part.split(b"\r\n\r\n", 1)
        value = value.rstrip(b"\r\n")
        headers = raw_headers.decode("utf-8", errors="replace")
        name_match = None
        filename_match = None
        for header in headers.splitlines():
            if header.lower().startswith("content-disposition:"):
                name_match = header.split('name="', 1)[1].split('"', 1)[0] if 'name="' in header else None
                filename_match = header.split('filename="', 1)[1].split('"', 1)[0] if 'filename="' in header else None
        if not name_match:
            continue
        if filename_match:
            files[name_match] = {"filename": Path(filename_match).name, "content": value}
        else:
            fields[name_match] = value.decode("utf-8", errors="replace")
    return fields, files


def save_upload(file_info):
    if not file_info or not file_info.get("filename") or not file_info.get("content"):
        return "", ""
    safe_name = "".join(ch for ch in file_info["filename"] if ch.isalnum() or ch in "._- ").strip()
    safe_name = safe_name or "archivo"
    stored = f"{int(time.time())}-{secrets.token_hex(5)}-{safe_name}"
    path = UPLOAD_DIR / stored
    path.write_bytes(file_info["content"])
    file_hash = hashlib.sha256(file_info["content"]).hexdigest()
    return safe_name, stored, file_hash


def save_data_url_upload(data_url, filename="aviso_wsp.jpg"):
    if not data_url or "," not in str(data_url):
        return "", "", ""
    header, encoded = str(data_url).split(",", 1)
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return "", "", ""
    if not content:
        return "", "", ""
    extension = ".jpg"
    if "image/png" in header:
        extension = ".png"
    elif "image/webp" in header:
        extension = ".webp"
    safe_name = Path(filename or f"aviso_wsp{extension}").name
    if not Path(safe_name).suffix:
        safe_name = f"{safe_name}{extension}"
    safe_name = "".join(ch for ch in safe_name if ch.isalnum() or ch in "._- ").strip() or f"aviso_wsp{extension}"
    stored = f"{int(time.time())}-{secrets.token_hex(5)}-{safe_name}"
    path = UPLOAD_DIR / stored
    path.write_bytes(content)
    file_hash = hashlib.sha256(content).hexdigest()
    return safe_name, stored, file_hash


def save_binary_upload(content, filename="aviso_wsp.jpg"):
    if not content:
        return "", "", ""
    safe_name = Path(filename or "aviso_wsp.jpg").name
    safe_name = "".join(ch for ch in safe_name if ch.isalnum() or ch in "._- ").strip() or "aviso_wsp.jpg"
    stored = f"{int(time.time())}-{secrets.token_hex(5)}-{safe_name}"
    path = UPLOAD_DIR / stored
    path.write_bytes(content)
    file_hash = hashlib.sha256(content).hexdigest()
    return safe_name, stored, file_hash


def list_cortes(filters):
    update_scheduled_cortes()
    query = "select * from cortes where 1=1"
    params = []
    if filters.get("status"):
        query += " and status = ?"
        params.append(filters["status"])
    if filters.get("outage_type"):
        query += " and outage_type = ?"
        params.append(filters["outage_type"])
    if filters.get("source"):
        query += " and source = ?"
        params.append(filters["source"])
    if filters.get("date"):
        query += " and substr(created_at, 1, 10) = ?"
        params.append(filters["date"])
    if filters.get("q"):
        query += " and (message like ? or zone_label like ? or whatsapp_group like ? or sender like ?)"
        term = f"%{filters['q']}%"
        params.extend([term, term, term, term])
    query += " order by id desc"
    with db() as conn:
        return conn.execute(query, params).fetchall()


def request_filters(environ):
    params = parse_qs(environ.get("QUERY_STRING", ""))
    return {key: values[0] for key, values in params.items() if values and values[0]}


def point_in_ring(lat, lon, ring):
    inside = False
    for index, point in enumerate(ring):
        prev = ring[index - 1]
        yi, xi = point[1], point[0]
        yj, xj = prev[1], prev[0]
        intersects = ((xi > lon) != (xj > lon)) and (lat < (yj - yi) * (lon - xi) / (xj - xi) + yi)
        if intersects:
            inside = not inside
    return inside


def point_in_geometry(lat, lon, geometry):
    if not geometry:
        return False
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    for polygon in polygons:
        if not polygon or not point_in_ring(lat, lon, polygon[0]):
            continue
        if not any(point_in_ring(lat, lon, hole) for hole in polygon[1:]):
            return True
    return False


def geometry_centroid(geometry):
    if not geometry:
        return None
    polygons = geometry.get("coordinates", []) if geometry.get("type") == "MultiPolygon" else [geometry.get("coordinates", [])]
    points = []
    for polygon in polygons:
        if not polygon:
            continue
        ring = polygon[0] if polygon and polygon[0] else []
        for coord in ring:
            if len(coord) >= 2:
                points.append((float(coord[0]), float(coord[1])))
    if not points:
        return None
    lon = sum(point[0] for point in points) / len(points)
    lat = sum(point[1] for point in points) / len(points)
    return lat, lon


def priority_weight(item):
    rubro = (item.get("rubro") or "").upper().strip()
    name = normalize_text(item.get("nombre"))
    weight = PRIORITY_WEIGHTS.get(rubro, 25)
    if "hospital" in name:
        weight = max(weight, 110)
    if any(term in name for term in ("energia", "electrica", "cooperativa electrica", "servicio electrico")):
        weight = max(weight, 88)
    if rubro == "EDUCACION" and any(term in name for term in ("escuela", "jardin", "colegio")):
        weight = max(weight, 72)
    return weight


def priority_tier(score):
    if score >= 100:
        return "critical"
    if score >= 70:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def priority_reason(items, institution_count, urban_density=0):
    if not items and urban_density >= 80:
        return "Alta densidad urbana estimada en la zona"
    if not items and institution_count >= 8:
        return "Alta concentracion de instituciones en la zona"
    if not items:
        return "Sin instituciones sensibles detectadas"
    best = items[0]
    rubro = best.get("rubro", "")
    name = best.get("nombre", "institucion sensible")
    if "hospital" in normalize_text(name):
        return f"Hospital/salud afectado: {name}"
    if rubro == "SALUD":
        return f"Salud afectada: {name}"
    if rubro == "BOMBEROS":
        return f"Bomberos afectado: {name}"
    if any(term in normalize_text(name) for term in ("energia", "electrica", "cooperativa electrica", "servicio electrico")):
        return f"Servicio/energia afectado: {name}"
    if rubro == "EDUCACION":
        return f"Escuela/educacion afectada: {name}"
    return f"{rubro.title()} afectado: {name}"


def row_zone_targets(row, zones_by_id, zones_by_label, groups):
    targets = []
    group_zones = groups.get(group_key(row["whatsapp_group"]))
    if group_zones:
        for item in group_zones:
            zone = zones_by_id.get(item.get("zoneId"))
            if zone:
                targets.append(zone)
    label_key = normalize_text(row["zone_label"])
    if label_key in zones_by_label:
        targets.append(zones_by_label[label_key])
    seen = set()
    unique = []
    for zone in targets:
        zone_id = zone.get("properties", {}).get("id", "")
        if zone_id and zone_id in seen:
            continue
        seen.add(zone_id)
        unique.append(zone)
    return unique


def load_group_geometries():
    try:
        data = json.loads((DATA_DIR / "grupos_whatsapp_manual.geojson").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    groups = {}
    for feature in data.get("features", []):
        key = group_key(feature.get("properties", {}).get("group", ""))
        if not key or not feature.get("geometry"):
            continue
        groups.setdefault(key, []).append(feature)
    return groups


def priority_context(active_rows):
    try:
        zonas = json.loads((DATA_DIR / "zonas_municipales.geojson").read_text(encoding="utf-8"))["features"]
        instituciones = json.loads((DATA_DIR / "instituciones.json").read_text(encoding="utf-8")).get("instituciones", [])
        manzanas = json.loads((DATA_DIR / "manzanas_zte.geojson").read_text(encoding="utf-8")).get("features", [])
        groups = load_group_zones()
        group_geometries = load_group_geometries()
    except (OSError, json.JSONDecodeError):
        return {}

    zones_by_id = {zone.get("properties", {}).get("id", ""): zone for zone in zonas}
    zones_by_label = {normalize_text(zone.get("properties", {}).get("label", "")): zone for zone in zonas}
    manzana_centroids = []
    for manzana in manzanas:
        centroid = geometry_centroid(manzana.get("geometry"))
        if centroid:
            manzana_centroids.append(centroid)

    context = {}
    for row in active_rows:
        group = group_key(row["whatsapp_group"])
        targets = row_zone_targets(row, zones_by_id, zones_by_label, groups)
        targets.extend(group_geometries.get(group, []))
        seen_targets = set()
        unique_targets = []
        for target in targets:
            props = target.get("properties", {})
            target_key = props.get("id") or f'{props.get("group", "")}-{props.get("source_id", "")}-{props.get("term", "")}'
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            unique_targets.append(target)

        sensitive = []
        institution_count = 0
        urban_density = 0
        seen_names = set()
        for item in instituciones:
            if not item.get("lat") or not item.get("lon"):
                continue
            lat = float(item["lat"])
            lon = float(item["lon"])
            if any(point_in_geometry(lat, lon, target.get("geometry")) for target in unique_targets):
                institution_count += 1
                name_key = normalize_text(item.get("nombre"))
                if name_key in seen_names:
                    continue
                seen_names.add(name_key)
                weight = priority_weight(item)
                if weight >= 60:
                    sensitive.append({**item, "weight": weight})
        seen_manzanas = set()
        for lat, lon in manzana_centroids:
            manzana_key = f"{lat:.6f},{lon:.6f}"
            if manzana_key in seen_manzanas:
                continue
            if any(point_in_geometry(lat, lon, target.get("geometry")) for target in unique_targets):
                seen_manzanas.add(manzana_key)
                urban_density += 1
        sensitive.sort(key=lambda item: item["weight"], reverse=True)
        score = sensitive[0]["weight"] if sensitive else 0
        if urban_density >= 120:
            score = max(score, 66)
        elif urban_density >= 80:
            score = max(score, 62)
        elif urban_density >= 45:
            score = max(score, 55)
        if institution_count >= 25:
            score = max(score, 66)
        elif institution_count >= 12:
            score = max(score, 62)
        elif institution_count >= 8:
            score = max(score, 55)
        tier = priority_tier(score)
        context[row["id"]] = {
            "is_priority": score >= 60,
            "score": score,
            "tier": tier,
            "label": PRIORITY_LABELS[tier],
            "reason": priority_reason(sensitive, institution_count, urban_density),
            "critical_count": len(sensitive),
            "institution_count": institution_count,
            "urban_density": urban_density,
            "critical_names": [item.get("nombre", "Institucion sensible") for item in sensitive[:4]],
        }
    return context


def institution_totals():
    try:
        instituciones = json.loads((DATA_DIR / "instituciones.json").read_text(encoding="utf-8")).get("instituciones", [])
    except (OSError, json.JSONDecodeError):
        return {"loaded": 0, "visible": 0}
    loaded = [item for item in instituciones if "ciudad" in normalize_text(item.get("ciudad"))]
    visible = [item for item in loaded if item.get("lat") and item.get("lon")]
    return {"loaded": len(loaded), "visible": len(visible)}


def load_population_by_group():
    try:
        data = json.loads((DATA_DIR / "poblacion_por_zona_2022.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    groups = {}
    for item in data.get("groups", []):
        key = group_key(item.get("id") or item.get("label"))
        if key:
            groups[key] = item
    return groups


def population_for_group(group, populations=None):
    populations = populations if populations is not None else load_population_by_group()
    return populations.get(group_key(group), {})


def population_line(population):
    if not population:
        return ""
    people = int(population.get("population") or 0)
    households = int(population.get("households") or 0)
    if not people and not households:
        return ""
    return f"Población estimada: {people:,} personas · {households:,} hogares".replace(",", ".")


def impact_summary(rows):
    populations = load_population_by_group()
    seen_groups = set()
    affected_people = 0
    affected_households = 0
    zones = set()

    for row in rows:
        group = group_key(row["whatsapp_group"])
        zone = row["zone_label"] or ""
        if zone:
            zones.add(zone)
        elif group:
            zones.add(f"Grupo {group}")

        if not group or group in seen_groups:
            continue
        seen_groups.add(group)
        population = population_for_group(group, populations)
        affected_people += int(population.get("population") or 0)
        affected_households += int(population.get("households") or 0)

    return {
        "people": affected_people,
        "households": affected_households,
        "zones": len(zones),
    }


def format_number(value):
    return f"{int(value or 0):,}".replace(",", ".")


def connector_status_summary():
    state_path = DATA_DIR / "group-connector-state.json"
    log_path = DATA_DIR / "group-connector.log"
    qr_path = DATA_DIR / "whatsapp-qr.png"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}

    if state.get("status") == "ok":
        label = "WhatsApp conectado"
        detail = state.get("lastProcessedAt", "")
        tone = "ok"
    elif state.get("status") == "error":
        label = "WhatsApp con alerta"
        detail = state.get("error", "Revisar log del conector")
        tone = "warn"
    elif log_path.exists():
        label = "WhatsApp iniciado"
        detail = "Esperando actividad de grupos"
        tone = "ok"
    else:
        label = "WhatsApp sin datos"
        detail = "Todavía no hay actividad registrada"
        tone = "muted"

    qr_detail = ""
    if qr_path.exists():
        qr_detail = f"QR disponible: {datetime.fromtimestamp(qr_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M')}"

    return {"label": label, "detail": detail, "tone": tone, "qr": qr_detail}


def file_size_label(path):
    try:
        size = Path(path).stat().st_size
    except OSError:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return "-"


def file_mtime_label(path):
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return "-"


def cloudflare_status_summary():
    runtime_public_url_path = DATA_DIR / "runtime" / "cloudflare.url"
    runtime_pid_path = DATA_DIR / "runtime" / "cloudflare.pid"
    try:
        runtime_public_url = runtime_public_url_path.read_text(encoding="utf-8").strip().rstrip("/")
    except OSError:
        runtime_public_url = ""
    try:
        runtime_pid = int(runtime_pid_path.read_text(encoding="utf-8").strip())
        os.kill(runtime_pid, 0)
        runtime_cloudflare_active = True
    except (OSError, ValueError):
        runtime_cloudflare_active = False
    if runtime_public_url and runtime_cloudflare_active:
        return {
            "tone": "ok",
            "label": "Cloudflare activo",
            "detail": runtime_public_url,
        }

    public_base_url = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if public_base_url:
        return {
            "tone": "ok",
            "label": "Cloudflare configurado",
            "detail": public_base_url,
        }

    config_path = ROOT / "config" / "grupos_wsp_permitidos.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "tone": "warn",
            "label": "URL pública sin configurar",
            "detail": "No pude leer config/grupos_wsp_permitidos.json",
        }
    api_url = str(payload.get("apiUrl") or "")
    if "trycloudflare.com" in api_url or "https://" in api_url:
        public_url = api_url.split("/api/", 1)[0]
        return {
            "tone": "ok",
            "label": "Cloudflare configurado",
            "detail": public_url,
        }
    if api_url.startswith("http://127.0.0.1") or api_url.startswith("http://localhost"):
        return {
            "tone": "ok",
            "label": "Conector local activo",
            "detail": api_url,
        }
    return {
        "tone": "warn",
        "label": "Cloudflare pendiente",
        "detail": api_url or "Sin apiUrl configurada",
    }


def system_metrics():
    update_scheduled_cortes()
    with db() as conn:
        totals = conn.execute(
            """
            select
              count(*) total,
              sum(case when status = 'activo' then 1 else 0 end) active,
              sum(case when status = 'pendiente' then 1 else 0 end) pending,
              sum(case when status = 'restablecido' then 1 else 0 end) restored,
              sum(case when attachment_path is not null and attachment_path != '' then 1 else 0 end) files
            from cortes
            """
        ).fetchone()
        latest = conn.execute("select * from cortes order by id desc limit 1").fetchone()
        latest_file = conn.execute(
            """
            select * from cortes
            where attachment_path is not null and attachment_path != ''
            order by id desc limit 1
            """
        ).fetchone()
    upload_files = [item for item in UPLOAD_DIR.glob("*") if item.is_file()] if UPLOAD_DIR.exists() else []
    return {
        "totals": totals,
        "latest": latest,
        "latest_file": latest_file,
        "upload_count": len(upload_files),
        "db_size": file_size_label(DB_PATH),
        "db_updated": file_mtime_label(DB_PATH),
    }


def latest_corte():
    with db() as conn:
        return conn.execute("select * from cortes order by id desc limit 1").fetchone()


def short_text(value, limit=150):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def public_notice_ocr_text(notes):
    text = str(notes or "")
    if "OCR:" not in text:
        return ""
    text = text.split("OCR:", 1)[1]
    text = re.split(r"\nAlerta:|\nAlerta importante:|\nClasificacion:", text, maxsplit=1)[0]
    return text.strip()


def clean_public_ocr_line(value):
    value = " ".join(str(value or "").split())
    value = re.sub(r"^[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", "", value)
    value = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9.,;:/°º() -]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -_,.;")
    return value


def public_notice_summary_from_ocr(notes, limit=190):
    ocr = public_notice_ocr_text(notes)
    if not ocr:
        return ""

    lines = [clean_public_ocr_line(line) for line in ocr.splitlines()]
    lines = [line for line in lines if line]
    normalized_lines = [normalize_text(line) for line in lines]

    affected = []
    motive = []
    estimate = ""
    mode = ""
    stop_terms = (
        "motivo",
        "tiempo estimado",
        "sepa disculpar",
        "cooperativa",
        "energia",
        "energía",
    )
    for line, normalized in zip(lines, normalized_lines):
        if "zona afectada" in normalized:
            mode = "affected"
            continue
        if "motivo" in normalized or "tiempo estimado" in normalized:
            mode = "motive"
            continue
        if any(term in normalized for term in ("sepa disculpar", "cooperativa", "energia", "energía")):
            mode = ""
            continue
        if "tiempo estimado" in normalized or "reposicion" in normalized or "reposición" in normalized:
            estimate = line
            continue
        if mode == "affected":
            if any(term in normalized for term in stop_terms):
                mode = ""
                continue
            affected.append(line)
        elif mode == "motive":
            if any(term in normalized for term in ("sepa disculpar", "cooperativa", "energia", "energía")):
                mode = ""
                continue
            if "tiempo estimado" in normalized or "reposicion" in normalized or "reposición" in normalized:
                estimate = line
            else:
                motive.append(line)

    parts = []
    affected_text = ", ".join(affected)
    if affected_text:
        parts.append(f"Zonas: {affected_text}")
    motive_text = " ".join(motive)
    if motive_text:
        parts.append(motive_text)
    if estimate:
        parts.append(estimate)
    return short_text(" · ".join(parts), limit)


def public_estimated_restore(row):
    if row["estimated_end"]:
        return row["estimated_end"]
    ocr = public_notice_ocr_text(row["notes"])
    if not ocr:
        return ""
    pending_estimate = False
    for line in ocr.splitlines():
        clean = clean_public_ocr_line(line)
        normalized = normalize_text(clean)
        if "motivo" in normalized:
            continue
        if pending_estimate and clean:
            return clean
        if "reposicion" in normalized or "reposición" in normalized:
            return clean
        if "tiempo estimado" in normalized:
            pending_estimate = True
    return ""


def public_notice_detail(row, limit=130):
    if str(row["source"] or "").startswith("whatsapp"):
        return public_notice_summary_from_ocr(row["notes"], limit)
    text = str(row["notes"] or row["message"] or "").strip()
    hidden_prefixes = (
        "conector grupos whatsapp",
        "grupo origen:",
        "id mensaje:",
        "clasificacion:",
        "ocr:",
        "alerta importante:",
        "alerta:",
        "evidencia adjuntada",
        "clasificado por mensaje posterior",
    )
    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if not clean:
            continue
        normalized = normalize_text(clean)
        if any(normalized.startswith(prefix) for prefix in hidden_prefixes):
            continue
        if (
            "id mensaje" in normalized
            or "messageid" in normalized
            or "zona afectada" in normalized
            or "remitente:" in normalized
            or "grupo origen" in normalized
        ):
            continue
        lines.append(clean)

    clean_text = " ".join(lines).strip()
    normalized_result = normalize_text(clean_text)
    if "zona afectada" in normalized_result or "remitente:" in normalized_result:
        return ""
    if not clean_text or normalize_text(clean_text) in {"aviso recibido por imagen de whatsapp", "aviso recibido por imagen de grupo"}:
        return ""
    return short_text(clean_text, limit)


def public_zone_label(row):
    return row["zone_label"] or (f'Grupo {row["whatsapp_group"]}' if row["whatsapp_group"] else "Zona sin identificar")


def current_operation_summary(active_rows, pending_count, latest):
    active_count = len(active_rows)
    if active_count:
        blocks = [
            f'Grupo {row["whatsapp_group"]}' if row["whatsapp_group"] else row["zone_label"]
            for row in active_rows
            if row["whatsapp_group"] or row["zone_label"]
        ]
        blocks_text = ", ".join(blocks[:3])
        if len(blocks) > 3:
            blocks_text = f"{blocks_text} y {len(blocks) - 3} mas"
        detail = blocks_text or "Hay cortes cargados sin bloque identificado."
        return {
            "tone": "danger",
            "title": f"{active_count} corte{'s' if active_count != 1 else ''} activo{'s' if active_count != 1 else ''}",
            "detail": f"Bloques activos: {detail}",
            "action": "Revisar mapa operativo",
        }
    if pending_count:
        return {
            "tone": "warn",
            "title": f"{pending_count} aviso{'s' if pending_count != 1 else ''} en revision",
            "detail": "No hay cortes activos, pero quedan registros para validar antes de cerrar la operatoria.",
            "action": "Revisar pendientes",
        }
    if latest:
        return {
            "tone": "ok",
            "title": "Sin cortes activos",
            "detail": f"Ultimo movimiento: {STATUSES.get(latest['status'], latest['status'])} en {latest['zone_label'] or latest['whatsapp_group'] or 'zona sin identificar'}.",
            "action": "Ver historial",
        }
    return {
        "tone": "muted",
        "title": "Sin actividad cargada",
        "detail": "Cuando entre un aviso desde WhatsApp o carga manual, va a aparecer aca.",
        "action": "Cargar primer aviso",
    }


def render_latest_notice(row):
    if not row:
        return """
        <article class="operator-card empty-state" id="latestNoticeCard">
          <span>Ultimo aviso</span>
          <strong>Sin registros todavia</strong>
          <p>Los avisos de WhatsApp y las cargas manuales van a aparecer aca.</p>
        </article>"""
    zone = public_zone_label(row)
    source = "WhatsApp" if str(row["source"] or "").startswith("whatsapp") else "Carga manual"
    message = public_notice_detail(row)
    return f"""
    <article class="operator-card" id="latestNoticeCard">
      <span>Ultimo aviso</span>
      <strong>{esc(zone)}</strong>
      <p>{esc(source)} · {esc(TYPES.get(row["outage_type"], row["outage_type"]))} · {esc(row["created_at"])}</p>
      {f"<p>{esc(message)}</p>" if message else ""}
      <a class="inline-link" href="/cortes/{row["id"]}">Abrir detalle</a>
    </article>"""


def dashboard(environ, start_response):
    active_rows = list_cortes({"status": "activo"})
    priority = priority_context(active_rows)
    priority_count = sum(1 for row in active_rows if priority.get(row["id"], {}).get("is_priority"))
    impact = impact_summary(active_rows)
    latest = latest_corte()
    with db() as conn:
        totals = conn.execute(
            """
            select
              count(*) total,
              sum(case when status = 'activo' then 1 else 0 end) active,
              sum(case when status = 'pendiente' then 1 else 0 end) pending,
              sum(case when attachment_path is not null and attachment_path != '' then 1 else 0 end) files
            from cortes
            """
        ).fetchone()
    current = current_operation_summary(active_rows, totals["pending"] or 0, latest)
    initial_active_signature = json.dumps([
        {
            "id": f'db-{row["id"]}',
            "group": row["whatsapp_group"] or "",
        }
        for row in sorted(active_rows, key=lambda item: item["id"])
    ], ensure_ascii=False)
    initial_movement_fingerprint = ""
    if latest:
        initial_movement_fingerprint = f'{latest["id"]}:{latest["status"]}:{latest["end_time"] or ""}:{latest["created_at"]}'

    cards = f"""
    <section class="stats">
      <div><strong id="statPeople">{format_number(impact["people"])}</strong><span>Habitantes afectados</span></div>
      <div><strong id="statHouseholds">{format_number(impact["households"])}</strong><span>Hogares afectados</span></div>
      <div><strong id="statActive">{totals["active"] or 0}</strong><span>Cortes activos</span></div>
      <div><strong id="statZones">{impact["zones"]}</strong><span>Zonas afectadas</span></div>
      <div class="priority-stat"><strong id="statPriority">{priority_count}</strong><span>Prioridad</span></div>
      <div><strong>{totals["pending"] or 0}</strong><span>En revisión</span></div>
    </section>"""

    active_cards = "".join(render_active_card(row, priority.get(row["id"], {})) for row in active_rows) or """
    <div class="active-empty">
      <strong>No hay cortes activos cargados.</strong>
      <span>Cuando se registre un corte con estado activo, va a aparecer en esta pantalla principal.</span>
    </div>"""
    content = f"""
    <header class="municipal-hero">
      <img src="/assets/logo-ciudad.png" alt="">
      <div>
        <span>Panel operativo municipal</span>
        <h1>Seguimiento de cortes</h1>
        <p>Vista de trabajo para revisar avisos activos, zonas prioritarias y registros que necesitan validación.</p>
      </div>
    </header>
    <section class="operator-board">
      <div class="operator-now {esc(current["tone"])}" id="operatorNow">
        <span class="operator-label">Ahora</span>
        <h2 id="operatorTitle">{esc(current["title"])}</h2>
        <p id="operatorDetail">{esc(current["detail"])}</p>
        <div class="operator-actions">
          <a class="button primary" href="/mapa.html?v=20260707-2">Ver mapa</a>
          <a class="button secondary" href="/dashboard-cortes">Dashboard</a>
          <a class="button secondary" href="/historial?status=pendiente">Pendientes</a>
        </div>
      </div>
      <div class="operator-stack">
        {render_latest_notice(latest)}
      </div>
    </section>
    {cards}
    <h2 class="section-title">Cortes activos</h2>
    <section class="active-grid" id="activeGrid">
      {active_cards}
    </section>
    <footer class="powered-by">
      <span>Powered by</span>
      <img src="/assets/logo-ciudad.png" alt="Empresa">
    </footer>
    <script>
      const operatorNow = document.querySelector("#operatorNow");
      const operatorTitle = document.querySelector("#operatorTitle");
      const operatorDetail = document.querySelector("#operatorDetail");
      const latestNoticeCard = document.querySelector("#latestNoticeCard");
      const statActive = document.querySelector("#statActive");
      const statPriority = document.querySelector("#statPriority");
      const statZones = document.querySelector("#statZones");
      const statPeople = document.querySelector("#statPeople");
      const statHouseholds = document.querySelector("#statHouseholds");
      const activeGrid = document.querySelector("#activeGrid");
      let seenMovement = {json.dumps(initial_movement_fingerprint, ensure_ascii=False)};
      let activeSignature = {json.dumps(initial_active_signature, ensure_ascii=False)};

      function escapeHtml(value) {{
        return String(value ?? "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#039;");
      }}

      function rootId(event) {{
        const match = String(event.id || "").match(/^(db-\\d+)-/);
        return match ? match[1] : String(event.id || "");
      }}

      function activeRoots(activeData) {{
        const byRoot = new Map();
        (Array.isArray(activeData.events) ? activeData.events : []).forEach(event => {{
          const id = rootId(event);
          if (!id) return;
          const current = byRoot.get(id) || {{
            id,
            group: event.group || "",
            zones: [],
            priority: event.priority || "low",
            priorityLabel: event.priorityLabel || "",
            populationLabel: event.populationLabel || "",
            population: Number(event.population || 0),
            households: Number(event.households || 0),
            detail: event.detail || "",
            createdAt: event.createdAt || ""
          }};
          if (event.zoneLabel && !current.zones.includes(event.zoneLabel)) current.zones.push(event.zoneLabel);
          if (event.priority === "high") {{
            current.priority = "high";
            current.priorityLabel = event.priorityLabel || "Prioridad 1";
          }}
          byRoot.set(id, current);
        }});
        return [...byRoot.values()].sort((a, b) => a.id.localeCompare(b.id));
      }}

      function formatNumber(value) {{
        return new Intl.NumberFormat("es-AR").format(Number(value || 0));
      }}

      function impactFromActive(active) {{
        return active.reduce((summary, item) => {{
          summary.people += Number(item.population || 0);
          summary.households += Number(item.households || 0);
          return summary;
        }}, {{ people: 0, households: 0 }});
      }}

      function renderLatestNotice(latest) {{
        if (!latestNoticeCard) return;
        if (!latest) {{
          latestNoticeCard.className = "operator-card empty-state";
          latestNoticeCard.innerHTML = `
            <span>Ultimo aviso</span>
            <strong>Sin registros todavia</strong>
            <p>Los avisos de WhatsApp y las cargas manuales van a aparecer aca.</p>`;
          return;
        }}
        latestNoticeCard.className = "operator-card";
        const zone = latest.zone || latest.group ? (latest.zone || `Grupo ${{latest.group}}`) : "Zona sin identificar";
        const source = latest.sourceLabel || "WhatsApp";
        const type = latest.typeLabel || latest.type || "sin clasificar";
        const message = latest.message ? `<p>${{escapeHtml(latest.message)}}</p>` : "";
        latestNoticeCard.innerHTML = `
          <span>Ultimo aviso</span>
          <strong>${{escapeHtml(zone)}}</strong>
          <p>${{escapeHtml(source)}} · ${{escapeHtml(type)}} · ${{escapeHtml(latest.createdAt || "")}}</p>
          ${{message}}
          <a class="inline-link" href="/cortes/${{encodeURIComponent(latest.id)}}">Abrir detalle</a>`;
      }}

      function renderPanelState(activeData, latest) {{
        const active = activeRoots(activeData);
        const zones = new Set(active.flatMap(item => item.zones.length ? item.zones : [item.group ? `Grupo ${{item.group}}` : "Zona sin identificar"]));
        const blocks = active.map(item => item.group ? `Grupo ${{item.group}}` : "Bloque sin identificar");
        const high = active.filter(item => item.priority === "high").length;
        const impact = impactFromActive(active);

        statActive.textContent = active.length;
        statPriority.textContent = high;
        statZones.textContent = zones.size;
        statPeople.textContent = formatNumber(impact.people);
        statHouseholds.textContent = formatNumber(impact.households);

        operatorNow.classList.remove("danger", "warn", "ok", "muted");
        if (active.length) {{
          operatorNow.classList.add("danger");
          operatorTitle.textContent = `${{active.length}} corte${{active.length === 1 ? "" : "s"}} activo${{active.length === 1 ? "" : "s"}}`;
          const blockText = blocks.slice(0, 3).join(", ");
          operatorDetail.textContent = `Bloques activos: ${{blockText || "Hay cortes cargados sin bloque identificado."}}`;
        }} else {{
          operatorNow.classList.add("ok");
          operatorTitle.textContent = "Sin cortes activos";
          operatorDetail.textContent = latest
            ? `Ultimo movimiento: ${{latest.status === "restablecido" ? "Restablecido" : latest.status}} en ${{latest.zone || "zona sin identificar"}}.`
            : "Cuando entre un aviso desde WhatsApp o carga manual, va a aparecer aca.";
        }}

        if (!active.length) {{
          activeGrid.innerHTML = `
            <div class="active-empty">
              <strong>No hay cortes activos cargados.</strong>
              <span>Cuando se registre un corte con estado activo, va a aparecer en esta pantalla principal.</span>
            </div>`;
          return;
        }}

        activeGrid.innerHTML = active.map(item => `
          <article class="active-card">
            <i></i>
            <div>
              <strong>${{escapeHtml(item.group ? `Grupo ${{item.group}}` : "Bloque sin identificar")}}</strong>
              <span>${{escapeHtml([item.zones.length ? `${{item.zones.length}} zonas del bloque` : "", item.priorityLabel || "Prioridad 3"].filter(Boolean).join(" · "))}}</span>
              ${{item.populationLabel ? `<small>${{escapeHtml(item.populationLabel)}}</small>` : ""}}
              ${{item.detail ? `<p>${{escapeHtml(item.detail)}}</p>` : ""}}
            </div>
            <a class="button secondary" href="/historial?status=activo">Ver activos</a>
          </article>
        `).join("");
      }}

      async function pollPanelState() {{
        try {{
          const [movementsResponse, activeResponse] = await Promise.all([
            fetch("/api/cortes-movimientos", {{ cache: "no-store" }}),
            fetch("/api/cortes-activos", {{ cache: "no-store" }})
          ]);
          if (!movementsResponse.ok || !activeResponse.ok) return;
          const movementsData = await movementsResponse.json();
          const activeData = await activeResponse.json();
          const latest = Array.isArray(movementsData.movements) ? movementsData.movements[0] : null;
          const freshActiveSignature = JSON.stringify(activeRoots(activeData).map(item => ({{ id: item.id, group: item.group }})));
          if (!latest?.fingerprint) return;
          renderLatestNotice(latest);
          if (latest.fingerprint !== seenMovement || freshActiveSignature !== activeSignature) {{
            seenMovement = latest.fingerprint;
            activeSignature = freshActiveSignature;
            renderPanelState(activeData, latest);
          }}
        }} catch {{
          return;
        }}
      }}

      window.setInterval(pollPanelState, 5000);
      pollPanelState();
    </script>
    """
    return response(start_response, layout("Panel", content, "dashboard"))


def operational_minutes(row):
    start_value = row["start_time"] or row["created_at"] or ""
    end_value = row["end_time"] or infer_restore_time(row["notes"] or "")
    if not start_value or not end_value:
        return None
    start_dt = parse_operational_datetime(str(start_value).replace("T", " "))
    end_dt = parse_operational_datetime(str(end_value).replace("T", " "), start_dt)
    if not start_dt or not end_dt:
        return None
    if end_dt <= start_dt:
        end_dt = end_dt + timedelta(days=1)
    return max(0, int((end_dt - start_dt).total_seconds() // 60))


def minutes_label(minutes):
    if minutes is None:
        return "Sin dato"
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


def percentage_label(value, total):
    if not total:
        return "0%"
    return f"{round((value / total) * 100)}%"


def dashboard_group_label(value):
    raw = str(value or "").strip()
    if not raw or normalize_text(raw).startswith("sin_grupo"):
        return "Sin grupo"
    key = group_key(raw)
    return key or raw


def source_dashboard_label(value):
    source = str(value or "").strip()
    labels = {
        "whatsapp_group_connector": "WhatsApp conector",
        "whatsapp_real": "WhatsApp",
        "whatsapp_demo": "WhatsApp demo",
        "manual": "Manual",
    }
    return labels.get(source, source or "Sin fuente")


def ranked_table_rows(items, empty_label):
    if not items:
        return f'<tr><td colspan="4" class="empty">{esc(empty_label)}</td></tr>'
    rows = []
    for label, count, percent, extra in items:
        rows.append(f"""
        <tr>
          <td><strong>{esc(label)}</strong></td>
          <td>{format_number(count)}</td>
          <td>{esc(percent)}</td>
          <td>{esc(extra)}</td>
        </tr>""")
    return "".join(rows)


def chart_rows(items, total, empty_label, tone="teal"):
    if not items:
        return f'<div class="chart-empty">{esc(empty_label)}</div>'
    rows = []
    max_count = max(count for _, count, *_ in items) or 1
    for index, item in enumerate(items):
        label, count = item[0], item[1]
        detail = item[2] if len(item) > 2 else percentage_label(count, total)
        href = item[3] if len(item) > 3 else ""
        width = max(4, round((count / max_count) * 100))
        tag = "a" if href else "div"
        href_attr = f' href="{esc(href)}"' if href else ""
        rows.append(f"""
        <{tag} class="chart-row {esc(tone)}"{href_attr}>
          <div class="chart-row-head">
            <strong>{esc(label)}</strong>
            <span>{format_number(count)} · {esc(detail)}</span>
          </div>
          <div class="chart-track"><span style="width:{width}%; --bar-index:{index}"></span></div>
        </{tag}>""")
    return "".join(rows)


def movement_day_info(value):
    parsed = parse_operational_datetime(str(value or "").replace("T", " "))
    if not parsed:
        return "Sin fecha", ""
    return parsed.strftime("%d/%m"), parsed.strftime("%Y-%m-%d")


def movement_row_html(row):
    return f"""
    <tr>
      <td>#{row["id"]}</td>
      <td>{esc(row["created_at"])}</td>
      <td>{esc(row["whatsapp_group"] or row["zone_label"] or "-")}</td>
      <td>{esc(source_dashboard_label(row["source"]))}</td>
      <td><span class="status {esc(row["status"])}">{esc(STATUSES.get(row["status"], row["status"]))}</span></td>
      <td>{esc(TYPES.get(row["outage_type"], row["outage_type"]))}</td>
      <td><a class="inline-link" href="/cortes/{row["id"]}">Detalle</a></td>
    </tr>"""


def cuts_dashboard_page(start_response):
    update_scheduled_cortes()
    with db() as conn:
        rows = conn.execute("select * from cortes order by datetime(created_at) desc, id desc").fetchall()

    total = len(rows)
    statuses = {key: 0 for key in STATUSES}
    types = {key: 0 for key in TYPES}
    sources = {}
    group_counts = {}
    restored_minutes = []
    active_rows = []
    pending_rows = []
    photo_count = 0
    restore_photo_count = 0
    whatsapp_count = 0
    manual_count = 0
    daily_counts = {}
    daily_labels = {}

    for row in rows:
        status = row["status"] or "pendiente"
        outage_type = row["outage_type"] or "sin_clasificar"
        source = row["source"] or "manual"
        group = dashboard_group_label(row["whatsapp_group"])
        statuses[status] = statuses.get(status, 0) + 1
        types[outage_type] = types.get(outage_type, 0) + 1
        sources[source] = sources.get(source, 0) + 1
        group_counts[group] = group_counts.get(group, 0) + 1
        if row["attachment_path"]:
            photo_count += 1
        if row["restore_attachment_path"]:
            restore_photo_count += 1
        if str(source).startswith("whatsapp"):
            whatsapp_count += 1
        if source == "manual":
            manual_count += 1
        if status == "restablecido":
            minutes = operational_minutes(row)
            if minutes is not None:
                restored_minutes.append(minutes)
        if status == "activo":
            active_rows.append(row)
        if status == "pendiente":
            pending_rows.append(row)
        day_label, day_key = movement_day_info(row["created_at"])
        day_key = day_key or day_label
        daily_labels[day_key] = day_label
        daily_counts[day_key] = daily_counts.get(day_key, 0) + 1

    avg_minutes = round(sum(restored_minutes) / len(restored_minutes)) if restored_minutes else None
    sorted_minutes = sorted(restored_minutes)
    median_minutes = sorted_minutes[len(sorted_minutes) // 2] if sorted_minutes else None
    max_minutes = max(restored_minutes) if restored_minutes else None
    impact = impact_summary(active_rows)

    status_rows = ranked_table_rows(
        [
            (STATUSES.get(key, key), count, percentage_label(count, total), key)
            for key, count in sorted(statuses.items(), key=lambda item: item[1], reverse=True)
            if count
        ],
        "Todavia no hay estados registrados.",
    )
    type_rows = ranked_table_rows(
        [
            (TYPES.get(key, key), count, percentage_label(count, total), key)
            for key, count in sorted(types.items(), key=lambda item: item[1], reverse=True)
            if count
        ],
        "Todavia no hay tipos registrados.",
    )
    group_rows = ranked_table_rows(
        [
            (f"Grupo {key}" if key != "Sin grupo" else key, count, percentage_label(count, total), "avisos")
            for key, count in sorted(group_counts.items(), key=lambda item: item[1], reverse=True)[:12]
        ],
        "Todavia no hay grupos registrados.",
    )
    source_rows = ranked_table_rows(
        [
            (source_dashboard_label(source), count, percentage_label(count, total), "origen")
            for source, count in sorted(sources.items(), key=lambda item: item[1], reverse=True)
        ],
        "Todavia no hay fuentes registradas.",
    )
    status_chart = chart_rows(
        [
            (STATUSES.get(key, key), count, percentage_label(count, total), f"/historial?{urlencode({'status': key})}")
            for key, count in sorted(statuses.items(), key=lambda item: item[1], reverse=True)
            if count
        ],
        total,
        "Todavia no hay estados registrados.",
        "status",
    )
    type_chart = chart_rows(
        [
            (TYPES.get(key, key), count, percentage_label(count, total), f"/historial?{urlencode({'outage_type': key})}")
            for key, count in sorted(types.items(), key=lambda item: item[1], reverse=True)
            if count
        ],
        total,
        "Todavia no hay tipos registrados.",
        "type",
    )
    group_chart = chart_rows(
        [
            (
                f"Grupo {key}" if key != "Sin grupo" else key,
                count,
                percentage_label(count, total),
                f"/historial?{urlencode({'q': key if key != 'Sin grupo' else ''})}" if key != "Sin grupo" else "/historial?q=",
            )
            for key, count in sorted(group_counts.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
        total,
        "Todavia no hay grupos registrados.",
        "group",
    )
    source_chart = chart_rows(
        [
            (source_dashboard_label(source), count, percentage_label(count, total), f"/historial?{urlencode({'source': source})}")
            for source, count in sorted(sources.items(), key=lambda item: item[1], reverse=True)
        ],
        total,
        "Todavia no hay fuentes registradas.",
        "source",
    )
    sorted_daily_items = sorted(daily_counts.items(), key=lambda item: item[0], reverse=True)
    recent_daily_items = sorted_daily_items[:7]
    older_daily_items = sorted_daily_items[7:]
    daily_chart = chart_rows(
        [
            (
                daily_labels.get(day, day),
                count,
                "movimientos",
                f"/historial?{urlencode({'date': day})}" if re.match(r"\d{4}-\d{2}-\d{2}", day) else "/historial",
            )
            for day, count in recent_daily_items
        ],
        total,
        "Todavia no hay movimientos diarios.",
        "daily",
    )
    daily_history = ""
    if older_daily_items:
        daily_history = f"""
        <details class="movement-more chart-more">
          <summary>Ver histórico <span>{format_number(len(older_daily_items))} día(s) anteriores</span></summary>
          <div class="chart-list">
            {chart_rows(
                [
                    (
                        daily_labels.get(day, day),
                        count,
                        "movimientos",
                        f"/historial?{urlencode({'date': day})}" if re.match(r"\d{4}-\d{2}-\d{2}", day) else "/historial",
                    )
                    for day, count in older_daily_items
                ],
                total,
                "Todavia no hay movimientos historicos.",
                "daily",
            )}
          </div>
        </details>"""

    recent_cutoff = (datetime.now() - timedelta(days=6)).date()
    recent_rows = []
    older_rows = []
    for row in rows:
        movement_date = parse_operational_datetime(str(row["created_at"] or "").replace("T", " "))
        if movement_date and movement_date.date() >= recent_cutoff:
            recent_rows.append(row)
        else:
            older_rows.append(row)

    latest_rows = "".join(movement_row_html(row) for row in recent_rows) or (
        '<tr><td colspan="7" class="empty">No hay movimientos en los ultimos 7 dias.</td></tr>'
    )
    older_movements = ""
    if older_rows:
        older_movements = f"""
        <details class="movement-more">
          <summary>Ver más movimientos <span>{format_number(len(older_rows))} anteriores</span></summary>
          <table>
            <thead><tr><th>ID</th><th>Fecha</th><th>Grupo / zona</th><th>Fuente</th><th>Estado</th><th>Tipo</th><th></th></tr></thead>
            <tbody>{''.join(movement_row_html(row) for row in older_rows)}</tbody>
          </table>
        </details>"""

    content = f"""
    <header class="page-head">
      <div>
        <span class="operator-label">Dashboard operativo</span>
        <h1>Panel analítico de cortes</h1>
        <p>Resumen operativo para leer volumen, estados, fuentes y tiempos de resolución sin perder detalle.</p>
      </div>
      <div class="head-actions">
        <span class="live-indicator" id="dashboardRefreshLabel">Actualiza cada 10s</span>
        <a class="button primary" href="/export/cortes.xlsx">Exportar Excel</a>
        <a class="button secondary" href="/mapa.html?v=20260707-2">Ver mapa</a>
        <a class="home-icon-button" href="/" title="Volver al inicio" aria-label="Volver al inicio">&#8962;</a>
      </div>
    </header>

    <section class="raw-kpis">
      <article><span>Total registrado</span><strong>{format_number(total)}</strong><small>avisos cargados en la base</small></article>
      <article><span>Activos</span><strong>{format_number(statuses.get("activo", 0))}</strong><small>{format_number(impact["people"])} habitantes afectados ahora</small></article>
      <article><span>Restablecidos</span><strong>{format_number(statuses.get("restablecido", 0))}</strong><small>{percentage_label(statuses.get("restablecido", 0), total)} del total</small></article>
      <article><span>En revisión</span><strong>{format_number(statuses.get("pendiente", 0))}</strong><small>pendientes de validar</small></article>
      <article><span>Fotos de corte</span><strong>{format_number(photo_count)}</strong><small>{percentage_label(photo_count, total)} con evidencia</small></article>
      <article><span>Fotos de cierre</span><strong>{format_number(restore_photo_count)}</strong><small>{percentage_label(restore_photo_count, total)} con respaldo visual</small></article>
      <article><span>Desde WhatsApp</span><strong>{format_number(whatsapp_count)}</strong><small>{percentage_label(whatsapp_count, total)} del total</small></article>
      <article><span>Carga manual</span><strong>{format_number(manual_count)}</strong><small>{percentage_label(manual_count, total)} del total</small></article>
    </section>

    <section class="raw-duration">
      <article>
        <span>Duración promedio</span>
        <strong>{esc(minutes_label(avg_minutes))}</strong>
        <small>solo cortes restablecidos con inicio y fin interpretables</small>
      </article>
      <article>
        <span>Duración mediana</span>
        <strong>{esc(minutes_label(median_minutes))}</strong>
        <small>valor central de los cortes cerrados</small>
      </article>
      <article>
        <span>Duración máxima</span>
        <strong>{esc(minutes_label(max_minutes))}</strong>
        <small>{format_number(len(restored_minutes))} cierres con duración calculada</small>
      </article>
      <article>
        <span>Impacto activo</span>
        <strong>{format_number(impact["households"])}</strong>
        <small>hogares afectados ahora en {format_number(impact["zones"])} zonas</small>
      </article>
    </section>

    <section class="dashboard-charts">
      <article class="chart-card wide">
        <header><strong>Movimiento reciente</strong><span>últimos 7 días · más reciente arriba</span></header>
        <div class="chart-list">{daily_chart}</div>
        {daily_history}
      </article>
      <article class="chart-card">
        <header><strong>Estado de gestión</strong><span>qué requiere acción</span></header>
        <div class="chart-list">{status_chart}</div>
      </article>
      <article class="chart-card">
        <header><strong>Tipos de aviso</strong><span>programado vs imprevisto</span></header>
        <div class="chart-list">{type_chart}</div>
      </article>
      <article class="chart-card">
        <header><strong>Bloques con más avisos</strong><span>top operativo</span></header>
        <div class="chart-list">{group_chart}</div>
      </article>
      <article class="chart-card">
        <header><strong>Origen de carga</strong><span>automatización vs manual</span></header>
        <div class="chart-list">{source_chart}</div>
      </article>
    </section>

    <section class="raw-grid">
      <article class="table-card raw-panel">
        <header><strong>Estados</strong><span>conteo por estado</span></header>
        <table><thead><tr><th>Estado</th><th>Cantidad</th><th>%</th><th>Clave</th></tr></thead><tbody>{status_rows}</tbody></table>
      </article>
      <article class="table-card raw-panel">
        <header><strong>Tipos</strong><span>clasificación del aviso</span></header>
        <table><thead><tr><th>Tipo</th><th>Cantidad</th><th>%</th><th>Clave</th></tr></thead><tbody>{type_rows}</tbody></table>
      </article>
      <article class="table-card raw-panel">
        <header><strong>Grupos con más avisos</strong><span>top 12</span></header>
        <table><thead><tr><th>Grupo</th><th>Cantidad</th><th>%</th><th>Dato</th></tr></thead><tbody>{group_rows}</tbody></table>
      </article>
      <article class="table-card raw-panel">
        <header><strong>Fuentes</strong><span>origen del registro</span></header>
        <table><thead><tr><th>Fuente</th><th>Cantidad</th><th>%</th><th>Dato</th></tr></thead><tbody>{source_rows}</tbody></table>
      </article>
    </section>

    <section class="table-card raw-panel latest-raw">
      <header><strong>Últimos movimientos</strong><span>últimos 7 días · más reciente arriba</span></header>
      <table>
        <thead><tr><th>ID</th><th>Fecha</th><th>Grupo / zona</th><th>Fuente</th><th>Estado</th><th>Tipo</th><th></th></tr></thead>
        <tbody>{latest_rows}</tbody>
      </table>
      {older_movements}
    </section>
    <script>
      (() => {{
        const label = document.querySelector("#dashboardRefreshLabel");
        let seconds = 10;
        window.setInterval(() => {{
          if (document.hidden) return;
          seconds -= 1;
          if (label) label.textContent = `Actualiza en ${{seconds}}s`;
          if (seconds <= 0) {{
            window.location.reload();
          }}
        }}, 1000);
      }})();
    </script>
    """
    return response(start_response, layout("Dashboard cortes", content, "cuts_dashboard"))


def history_page(environ, start_response):
    filters = request_filters(environ)
    rows = list_cortes(filters)
    table_rows = "".join(render_row(row) for row in rows) or '<tr><td colspan="6" class="empty">No hay registros con esos filtros.</td></tr>'
    query_string = urlencode(filters)
    csv_href = f"/export/cortes.csv?{query_string}" if query_string else "/export/cortes.csv"
    xlsx_href = f"/export/cortes.xlsx?{query_string}" if query_string else "/export/cortes.xlsx"
    content = f"""
    <header class="page-head">
      <div>
        <span class="operator-label">Registro</span>
        <h1>Historial</h1>
        <p>Consulta, filtra y exporta los avisos registrados por el panel.</p>
      </div>
      <div class="head-actions">
        <a class="button secondary" href="{esc(csv_href)}">Exportar CSV</a>
        <a class="button primary" href="{esc(xlsx_href)}">Exportar Excel</a>
      </div>
    </header>
    <form class="filters" method="get" action="/historial">
      <input name="q" placeholder="Buscar mensaje, grupo, zona..." value="{esc(filters.get("q", ""))}">
      <select name="status">
        {option("", "Todos los estados", filters.get("status", ""))}
        {''.join(option(k, v, filters.get("status", "")) for k, v in STATUSES.items())}
      </select>
      <select name="outage_type">
        {option("", "Todos los tipos", filters.get("outage_type", ""))}
        {''.join(option(k, v, filters.get("outage_type", "")) for k, v in TYPES.items())}
      </select>
      <button type="submit">Filtrar</button>
    </form>
    <section class="table-card">
      <table>
        <thead>
          <tr>
            <th>ID</th><th>Zona/grupo</th><th>Aviso de corte</th><th>Restablecimiento</th><th>Estado</th><th>Detalle</th>
          </tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
    </section>
    """
    return response(start_response, layout("Historial", content, "history"))


def system_page(start_response):
    connector = connector_status_summary()
    cloudflare = cloudflare_status_summary()
    institutions = institution_totals()
    metrics = system_metrics()
    totals = metrics["totals"]
    latest = metrics["latest"]
    latest_file = metrics["latest_file"]
    latest_zone = public_zone_label(latest) if latest else "Sin avisos recibidos"
    latest_type = TYPES.get(latest["outage_type"], latest["outage_type"]) if latest else "-"
    latest_status = STATUSES.get(latest["status"], latest["status"]) if latest else "-"
    latest_file_label = latest_file["attachment_name"] if latest_file else "Sin fotos recibidas"
    latest_file_time = latest_file["created_at"] if latest_file else "-"
    content = f"""
    <header class="page-head">
      <div>
        <span class="operator-label">Estado</span>
        <h1>Sistema</h1>
        <p>Chequeo rapido de servicios, datos y actividad del panel operativo.</p>
      </div>
    </header>
    <section class="stats">
      <div><strong>{totals["active"] or 0}</strong><span>Cortes activos</span></div>
      <div><strong>{totals["pending"] or 0}</strong><span>En revisión</span></div>
      <div><strong>{totals["restored"] or 0}</strong><span>Restablecidos</span></div>
      <div><strong>{metrics["upload_count"]}</strong><span>Fotos guardadas</span></div>
    </section>
    <section class="system-grid">
      <article class="operator-card system-card">
        <span>Panel</span>
        <div class="system-line"><i class="ops-dot ok"></i><strong>Activo</strong></div>
        <p>Base local disponible · {esc(metrics["db_size"])} · actualizada {esc(metrics["db_updated"])}</p>
      </article>
      <article class="operator-card system-card">
        <span>WhatsApp</span>
        <div class="system-line"><i class="ops-dot {esc(connector["tone"])}"></i><strong>{esc(connector["label"])}</strong></div>
        <p>{esc(connector["detail"] or connector["qr"] or "Sin actividad reciente")}</p>
      </article>
      <article class="operator-card system-card">
        <span>Cloudflare</span>
        <div class="system-line"><i class="ops-dot {esc(cloudflare["tone"])}"></i><strong>{esc(cloudflare["label"])}</strong></div>
        <p>{esc(cloudflare["detail"])}</p>
      </article>
      <article class="operator-card system-card">
        <span>Último aviso</span>
        <div class="system-line"><i class="ops-dot {'warn' if latest and latest['status'] == 'pendiente' else 'ok'}"></i><strong>{esc(latest_zone)}</strong></div>
        <p>{esc(latest_type)} · {esc(latest_status)}{f" · {esc(latest['created_at'])}" if latest else ""}</p>
      </article>
      <article class="operator-card system-card">
        <span>Última foto</span>
        <div class="system-line"><i class="ops-dot {'ok' if latest_file else 'muted'}"></i><strong>{esc(latest_file_label)}</strong></div>
        <p>{esc(latest_file_time)}</p>
      </article>
      <article class="operator-card system-card">
        <span>Mapa</span>
        <div class="system-line"><i class="ops-dot ok"></i><strong>Disponible</strong></div>
        <p>{institutions["visible"]} instituciones/servicios visibles de {institutions["loaded"]} cargados.</p>
      </article>
    </section>
    """
    return response(start_response, layout("Sistema", content, "system"))


def render_active_card(row, priority=None):
    priority = priority or {}
    detail = public_notice_detail(row)
    population = population_for_group(row["whatsapp_group"])
    population_text = population_line(population)
    group = f'Grupo {esc(row["whatsapp_group"])}' if row["whatsapp_group"] else ""
    title = group or esc(row["zone_label"] or "Bloque sin identificar")
    start = f'Inicio {esc(row["start_time"])}' if row["start_time"] else "Inicio sin horario"
    meta = " · ".join(part for part in [TYPES.get(row["outage_type"], row["outage_type"]), start] if part)
    priority_html = ""
    class_name = f'active-card priority {esc(priority.get("tier", "low"))}' if priority.get("is_priority") else "active-card"
    if priority.get("is_priority"):
        names = ", ".join(priority.get("critical_names", []))
        priority_html = f"""
        <span class="priority-badge">{esc(priority.get("label", "Zona de prioridad"))}</span>
        <small>{esc(priority.get("reason", ""))}</small>
        <small>{priority.get("critical_count", 0)} institucion(es) sensible(s), {priority.get("institution_count", 0)} institucion(es) en zona, {priority.get("urban_density", 0)} manzana(s) urbanas{f": {esc(names)}" if names else ""}</small>"""
    return f"""
    <article class="{class_name}">
      <div class="active-strip"></div>
      <div>
        <strong>{title}</strong>
        <span>{meta}</span>
        {f"<small>{esc(population_text)}</small>" if population_text else ""}
        {priority_html}
        {f"<p>{esc(detail)}</p>" if detail else ""}
      </div>
      <a class="button secondary" href="/cortes/{row["id"]}">Detalle</a>
    </article>"""


def option(value, label, selected):
    attr = " selected" if value == selected else ""
    return f'<option value="{esc(value)}"{attr}>{esc(label)}</option>'


def render_row(row):
    zone = row["zone_label"] or row["whatsapp_group"] or "-"
    message = (row["message"] or "").strip()
    if len(message) > 110:
        message = message[:110] + "..."
    start_value = row["start_time"] or row["created_at"]
    end_value = row["end_time"] or infer_restore_time(row["notes"] or "")
    cut_photo = '<span class="evidence-pill">Foto corte</span>' if row["attachment_path"] else '<span class="evidence-missing">Sin foto</span>'
    restore_photo = '<span class="evidence-pill restore">Foto restablecido</span>' if row["restore_attachment_path"] else '<span class="evidence-missing">Sin foto</span>'
    restore_status = f"""
      <div class="timeline-block restore">
        <span>Final</span>
        <strong>{esc(end_value or "-")}</strong>
        <a class="evidence-link" href="/cortes/{row["id"]}">{restore_photo}</a>
      </div>""" if row["status"] == "restablecido" or row["restore_attachment_path"] else """
      <div class="timeline-block pending">
        <span>Final</span>
        <strong>Pendiente</strong>
        <small>Sin restablecimiento cargado</small>
      </div>"""
    return f"""
    <tr>
      <td>#{row["id"]}</td>
      <td>{esc(zone)}</td>
      <td>
        <div class="timeline-block outage">
          <span>Inicio</span>
          <strong>{esc(start_value)}</strong>
          <small>{esc(TYPES.get(row["outage_type"], row["outage_type"]))}</small>
          <a class="evidence-link" href="/cortes/{row["id"]}">{cut_photo}</a>
        </div>
      </td>
      <td>{restore_status}</td>
      <td><span class="status {esc(row["status"])}">{esc(STATUSES.get(row["status"], row["status"]))}</span></td>
      <td>{esc(message or "-")}</td>
    </tr>"""


def infer_restore_time(notes):
    match = re.search(r"Restablecido desde .*?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", notes or "")
    return match.group(1) if match else ""


def get_corte(corte_id):
    with db() as conn:
        return conn.execute("select * from cortes where id = ?", (corte_id,)).fetchone()


def detail_value(label, value):
    return f"""
    <div class="detail-item">
      <span>{esc(label)}</span>
      <strong>{esc(value or "-")}</strong>
    </div>"""


def correction_form(row):
    return f"""
    <section class="correction-card">
      <h2>Corrección rápida</h2>
      <form method="post" action="/cortes/{row["id"]}/editar">
        <div class="form-grid">
          <label>Tipo
            <select name="outage_type">
              {''.join(option(k, v, row["outage_type"]) for k, v in TYPES.items())}
            </select>
          </label>
          <label>Estado
            <select name="status">
              {''.join(option(k, v, row["status"]) for k, v in STATUSES.items())}
            </select>
          </label>
          <label>Zona / barrio
            <input name="zone_label" value="{esc(row["zone_label"])}" placeholder="Ej: Villa Fox">
          </label>
          <label>Grupo / canal
            <input name="whatsapp_group" value="{esc(row["whatsapp_group"])}" placeholder="Ej: Grupo C">
          </label>
          <label>Hora inicio
            <input name="start_time" value="{esc(row["start_time"])}" placeholder="HH:MM o fecha completa">
          </label>
          <label>Hora final
            <input name="end_time" value="{esc(row["end_time"] or infer_restore_time(row["notes"] or ""))}" placeholder="AAAA-MM-DD HH:MM:SS">
          </label>
          <label>Reposición estimada
            <input name="estimated_end" value="{esc(row["estimated_end"])}" placeholder="Ej: 18:30">
          </label>
          <label>Remitente
            <input name="sender" value="{esc(row["sender"])}">
          </label>
        </div>
        <label>Mensaje original
          <textarea name="message">{esc(row["message"])}</textarea>
        </label>
        <label>Observaciones internas
          <textarea name="notes">{esc(row["notes"])}</textarea>
        </label>
        <div class="form-actions">
          <button class="primary" type="submit">Guardar corrección</button>
        </div>
      </form>
      <div class="quick-actions">
        <form method="post" action="/cortes/{row["id"]}/accion">
          <input type="hidden" name="action" value="confirm">
          <button type="submit">Confirmar activo</button>
        </form>
        <form method="post" action="/cortes/{row["id"]}/accion">
          <input type="hidden" name="action" value="restore">
          <button type="submit">Marcar restablecido</button>
        </form>
        <form method="post" action="/cortes/{row["id"]}/accion">
          <input type="hidden" name="action" value="ignore">
          <button type="submit">Ignorar aviso</button>
        </form>
      </div>
    </section>"""


def render_attachment(title, path, name, empty_text):
    if not path:
        return f'<div class="empty evidence-empty"><strong>{esc(title)}</strong><span>{esc(empty_text)}</span></div>'
    suffix = Path(path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return f"""
        <figure class="evidence-preview">
          <strong>{esc(title)}</strong>
          <img src="/uploads/{esc(path)}" alt="{esc(title)}">
          <figcaption>{esc(name)}</figcaption>
        </figure>"""
    return f"""
    <div class="file-preview">
      <strong>{esc(title)}: {esc(name)}</strong>
      <a class="button" href="/uploads/{esc(path)}" target="_blank">Abrir archivo</a>
    </div>"""


def corte_detail(environ, start_response, corte_id):
    row = get_corte(corte_id)
    if not row:
        return response(start_response, "No encontrado", "404 Not Found", "text/plain; charset=utf-8")

    attachment = render_attachment(
        "Evidencia del corte",
        row["attachment_path"],
        row["attachment_name"],
        "Este registro no tiene foto o archivo inicial.",
    )
    restore_attachment = render_attachment(
        "Evidencia de restablecimiento",
        row["restore_attachment_path"],
        row["restore_attachment_name"],
        "Todavia no hay foto de restablecimiento asociada.",
    )

    content = f"""
    <header class="page-head">
      <div>
        <h1>Evidencia del corte #{row["id"]}</h1>
        <p>Vista visual para auditoría: mensaje original, foto/archivo y datos registrados.</p>
      </div>
      <a class="button" href="/">Volver al panel</a>
    </header>

    <section class="evidence-layout">
      <div class="evidence-card">
        {attachment}
        {restore_attachment}
      </div>
      <div class="detail-card">
        <h2>Datos del registro</h2>
        <div class="detail-grid">
          {detail_value("Fecha de registro", row["created_at"])}
          {detail_value("Origen", row["source"])}
          {detail_value("Grupo / canal", row["whatsapp_group"])}
          {detail_value("Remitente", row["sender"])}
          {detail_value("Tipo", TYPES.get(row["outage_type"], row["outage_type"]))}
          {detail_value("Estado", STATUSES.get(row["status"], row["status"]))}
          {detail_value("Zona / barrio", row["zone_label"])}
          {detail_value("Hora inicio", row["start_time"])}
          {detail_value("Hora final", row["end_time"] or infer_restore_time(row["notes"] or ""))}
          {detail_value("Reposición estimada", row["estimated_end"])}
          {detail_value("Archivo original", row["attachment_name"])}
          {detail_value("Archivo restablecimiento", row["restore_attachment_name"])}
        </div>
        <div class="message-box">
          <span>Mensaje original</span>
          <p>{esc(row["message"])}</p>
        </div>
        <div class="message-box">
          <span>Observaciones</span>
          <p>{esc(row["notes"] or "-")}</p>
        </div>
        <div class="hash-box">
          <span>Huella técnica del archivo SHA-256</span>
          <code>{esc(row["attachment_sha256"] or "-")}</code>
        </div>
        {correction_form(row)}
      </div>
    </section>
    """
    return response(start_response, layout(f"Corte #{row['id']}", content, "dashboard"))


def update_corte(environ, start_response, corte_id):
    row = get_corte(corte_id)
    if not row:
        return response(start_response, "No encontrado", "404 Not Found", "text/plain; charset=utf-8")
    fields = parse_urlencoded(environ)
    status = fields.get("status", row["status"])
    if status not in STATUSES:
        status = row["status"]
    outage_type = fields.get("outage_type", row["outage_type"])
    if outage_type not in TYPES:
        outage_type = row["outage_type"]
    with db() as conn:
        conn.execute(
            """
            update cortes
            set whatsapp_group = ?,
                sender = ?,
                message = ?,
                outage_type = ?,
                zone_label = ?,
                start_time = ?,
                end_time = ?,
                estimated_end = ?,
                status = ?,
                notes = ?
            where id = ?
            """,
            (
                fields.get("whatsapp_group", ""),
                fields.get("sender", ""),
                fields.get("message", ""),
                outage_type,
                fields.get("zone_label", ""),
                fields.get("start_time", ""),
                fields.get("end_time", ""),
                fields.get("estimated_end", ""),
                status,
                fields.get("notes", ""),
                corte_id,
            ),
        )
        conn.commit()
    return redirect(start_response, f"/cortes/{corte_id}")


def append_note(existing, note):
    existing = (existing or "").strip()
    return f"{existing}\n{note}".strip() if existing else note


def corte_action(environ, start_response, corte_id):
    row = get_corte(corte_id)
    if not row:
        return response(start_response, "No encontrado", "404 Not Found", "text/plain; charset=utf-8")
    fields = parse_urlencoded(environ)
    action = fields.get("action", "")
    status = row["status"]
    outage_type = row["outage_type"]
    end_time = row["end_time"] or ""
    note = ""
    if action == "confirm":
        status = "activo"
        note = f"Confirmado manualmente el {now_label()}."
    elif action == "restore":
        status = "restablecido"
        outage_type = "restablecido"
        end_time = end_time or now_label()
        note = f"Marcado como restablecido manualmente el {now_label()}."
    elif action == "ignore":
        status = "ignorado"
        note = f"Ignorado manualmente el {now_label()}."
    else:
        return redirect(start_response, f"/cortes/{corte_id}")

    with db() as conn:
        conn.execute(
            "update cortes set status = ?, outage_type = ?, end_time = ?, notes = ? where id = ?",
            (status, outage_type, end_time, append_note(row["notes"], note), corte_id),
        )
        conn.commit()
    return redirect(start_response, f"/cortes/{corte_id}")


def new_corte_page(start_response, error=""):
    error_html = f'<div class="alert">{esc(error)}</div>' if error else ""
    content = f"""
    <header class="page-head">
      <div>
        <h1>Avisos</h1>
        <p>Usá esta pantalla solo para pruebas, correcciones o respaldo operativo. El flujo principal quedará conectado a avisos recibidos.</p>
      </div>
    </header>
    <section class="form-card">
      {error_html}
      <form method="post" action="/cortes/nuevo" enctype="multipart/form-data">
        <div class="form-grid">
          <label>Origen
            <select name="source">
              <option value="manual">Manual</option>
              <option value="whatsapp_demo">WhatsApp</option>
            </select>
          </label>
          <label>Grupo / canal
            <input name="whatsapp_group" placeholder="Ej: Grupo zona centro">
          </label>
          <label>Remitente
            <input name="sender" placeholder="Nombre o teléfono">
          </label>
          <label>Tipo
            <select name="outage_type">
              {''.join(option(k, v, "sin_clasificar") for k, v in TYPES.items())}
            </select>
          </label>
          <label>Estado
            <select name="status">
              {''.join(option(k, v, "pendiente") for k, v in STATUSES.items())}
            </select>
          </label>
          <label>Zona / barrio detectado
            <input name="zone_label" placeholder="Ej: Villa Fox / Mapa 1 zona 101">
          </label>
          <label>Hora inicio
            <input name="start_time" type="time">
          </label>
          <label>Hora final / restablecimiento
            <input name="end_time" type="datetime-local">
          </label>
          <label>Reposición estimada
            <input name="estimated_end" placeholder="Ej: 18:30 o 2 horas">
          </label>
        </div>
        <label>Mensaje original
          <textarea name="message" required placeholder="Pegá el texto recibido por WhatsApp o la descripción del aviso"></textarea>
        </label>
        <label>Foto / evidencia
          <input name="attachment" type="file" accept="image/*,.pdf,.xls,.xlsx,.csv">
        </label>
        <label>Observaciones internas
          <textarea name="notes" placeholder="Aclaraciones, correcciones o estado de revisión"></textarea>
        </label>
        <div class="form-actions">
          <a class="button" href="/">Cancelar</a>
          <button class="primary" type="submit">Guardar registro</button>
        </div>
      </form>
    </section>
    """
    return response(start_response, layout("Avisos", content, "new"))


def create_corte(environ, start_response):
    fields, files = parse_multipart(environ)
    if not fields.get("message", "").strip():
        return new_corte_page(start_response, "El mensaje original es obligatorio.")
    original_name, stored_name, file_hash = save_upload(files.get("attachment"))
    with db() as conn:
        conn.execute(
            """
            insert into cortes (
              created_at, source, whatsapp_group, sender, message, attachment_name, attachment_path, attachment_sha256,
              outage_type, zone_label, start_time, end_time, estimated_end, status, notes
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_label(),
                fields.get("source", "manual"),
                fields.get("whatsapp_group", ""),
                fields.get("sender", ""),
                fields.get("message", ""),
                original_name,
                stored_name,
                file_hash,
                fields.get("outage_type", "sin_clasificar"),
                fields.get("zone_label", ""),
                fields.get("start_time", ""),
                fields.get("end_time", ""),
                fields.get("estimated_end", ""),
                fields.get("status", "pendiente"),
                fields.get("notes", ""),
            ),
        )
        conn.commit()
    return redirect(start_response, "/")


def export_csv(environ, start_response):
    rows = list_cortes(request_filters(environ))
    output = []
    fields = export_headers()

    class Writer:
        def write(self, value):
            output.append(value)

    writer = csv.DictWriter(Writer(), fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: operational_export_row(row, environ).get(field, "") for field in fields})
    headers = [("Content-Disposition", 'attachment; filename="registro_cortes.csv"')]
    return response(start_response, "".join(output), content_type="text/csv; charset=utf-8", headers=headers)


def duration_label(start_value, end_value):
    if not start_value or not end_value:
        return ""
    formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%H:%M"]
    start_dt = None
    end_dt = None
    for fmt in formats:
        try:
            start_dt = datetime.strptime(start_value, fmt)
            break
        except ValueError:
            pass
    for fmt in formats:
        try:
            end_dt = datetime.strptime(end_value, fmt)
            break
        except ValueError:
            pass
    if not start_dt or not end_dt:
        return ""
    delta = end_dt - start_dt
    if delta.total_seconds() < 0:
        return ""
    minutes = int(delta.total_seconds() // 60)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


def public_url(environ, path):
    scheme = environ.get("HTTP_X_FORWARDED_PROTO") or environ.get("wsgi.url_scheme") or "http"
    host = environ.get("HTTP_X_FORWARDED_HOST") or environ.get("HTTP_HOST") or "localhost:8091"
    return f"{scheme}://{host}{path}"


def first_ocr_text(notes):
    if "OCR:" not in (notes or ""):
        return ""
    text = (notes or "").split("OCR:", 1)[1]
    text = re.split(r"\n(?:Alerta|Corregido|Pendiente|Restablecido):", text, maxsplit=1)[0]
    return " ".join(text.split())


def clean_operational_notes(notes):
    lines = []
    for line in (notes or "").splitlines():
        if line.startswith("OCR:"):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def evidence_url(environ, stored_name):
    return public_url(environ, f"/uploads/{stored_name}") if stored_name else ""


def export_headers():
    return [
        "id",
        "fecha_registro",
        "hora_inicio",
        "hora_fin",
        "duracion",
        "grupo_operativo",
        "zona_grupo",
        "tipo_aviso",
        "estado",
        "reposicion_estimada",
        "origen",
        "remitente",
        "evidencia_corte",
        "link_evidencia_corte",
        "evidencia_restablecimiento",
        "link_evidencia_restablecimiento",
        "link_detalle",
        "mensaje",
        "ocr",
        "observaciones",
        "hash_evidencia_corte",
        "hash_evidencia_restablecimiento",
    ]


EXCEL_COLUMNS = [
    ("id", "ID", 8),
    ("fecha_registro", "Fecha registro", 19),
    ("hora_inicio", "Inicio", 19),
    ("hora_fin", "Fin / restablecimiento", 22),
    ("duracion", "Duracion", 14),
    ("grupo_operativo", "Bloque operativo", 18),
    ("zona_grupo", "Zona / grupo", 26),
    ("tipo_aviso", "Tipo de aviso", 18),
    ("estado", "Estado", 16),
    ("reposicion_estimada", "Reposicion estimada", 22),
    ("origen", "Origen", 22),
    ("remitente", "Remitente", 22),
    ("evidencia_corte", "Evidencia corte", 26),
    ("link_evidencia_corte", "Link evidencia corte", 38),
    ("evidencia_restablecimiento", "Evidencia restablecimiento", 28),
    ("link_evidencia_restablecimiento", "Link evidencia restablecimiento", 38),
    ("link_detalle", "Link detalle", 34),
    ("mensaje", "Mensaje original", 56),
    ("ocr", "OCR", 56),
    ("observaciones", "Observaciones", 48),
    ("hash_evidencia_corte", "Hash evidencia corte", 34),
    ("hash_evidencia_restablecimiento", "Hash evidencia restablecimiento", 34),
]


def operational_export_row(row, environ=None):
    environ = environ or {}
    start_value = row["start_time"] or row["created_at"] or ""
    end_value = row["end_time"] or infer_restore_time(row["notes"] or "")
    detail_url = public_url(environ, f"/cortes/{row['id']}") if row["id"] else ""
    cut_url = evidence_url(environ, row["attachment_path"] or "")
    restore_url = evidence_url(environ, row["restore_attachment_path"] or "")
    notes = row["notes"] or ""
    return {
        "id": row["id"],
        "fecha_registro": row["created_at"] or "",
        "hora_inicio": start_value,
        "hora_fin": end_value,
        "duracion": duration_label(start_value, end_value),
        "grupo_operativo": row["whatsapp_group"] or "",
        "zona_grupo": row["zone_label"] or row["whatsapp_group"] or "",
        "tipo_aviso": TYPES.get(row["outage_type"], row["outage_type"]),
        "estado": STATUSES.get(row["status"], row["status"]),
        "reposicion_estimada": row["estimated_end"] or "",
        "origen": row["source"] or "",
        "remitente": row["sender"] or "",
        "evidencia_corte": row["attachment_name"] or "",
        "link_evidencia_corte": cut_url,
        "evidencia_restablecimiento": row["restore_attachment_name"] or "",
        "link_evidencia_restablecimiento": restore_url,
        "link_detalle": detail_url,
        "mensaje": row["message"] or "",
        "ocr": first_ocr_text(notes),
        "observaciones": clean_operational_notes(notes),
        "hash_evidencia_corte": row["attachment_sha256"] or "",
        "hash_evidencia_restablecimiento": row["restore_attachment_sha256"] or "",
    }

def column_name(index):
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def sheet_name(value):
    return re.sub(r"[][\\/*?:]", " ", value)[:31] or "Hoja"


def cell_xml(value, ref, style=""):
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{xml_escape(str(value or ""))}</t></is></c>'


def excel_columns(columns):
    normalized = []
    for item in columns:
        if isinstance(item, tuple):
            key = item[0]
            label = item[1] if len(item) > 1 else item[0]
            width = item[2] if len(item) > 2 else min(max(len(str(label)) + 4, 14), 32)
            normalized.append((key, label, width))
        else:
            normalized.append((item, item, min(max(len(str(item)) + 4, 14), 32)))
    return normalized


def excel_cell_style(key, value):
    normalized = normalize_text(value)
    if key in {"mensaje", "ocr", "observaciones"}:
        return "2"
    if key == "estado":
        if "activo" in normalized:
            return "4"
        if "restablecido" in normalized:
            return "5"
        if "pendiente" in normalized or "revision" in normalized:
            return "6"
        if "ignorado" in normalized:
            return "7"
    if key.startswith("link_"):
        return "8"
    return ""


def worksheet_xml(columns, rows):
    normalized_columns = excel_columns(columns)
    sheet_rows = []
    header_cells = [
        cell_xml(label, f"{column_name(index)}1", "1")
        for index, (_, label, _) in enumerate(normalized_columns, start=1)
    ]
    sheet_rows.append(f'<row r="1">{"".join(header_cells)}</row>')
    for row_index, row in enumerate(rows, start=2):
        cells = [
            cell_xml(row.get(key, ""), f"{column_name(col_index)}{row_index}", excel_cell_style(key, row.get(key, "")))
            for col_index, (key, _, _) in enumerate(normalized_columns, start=1)
        ]
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    cols = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, (_, _, width) in enumerate(normalized_columns, start=1)
    )
    last_cell = f"{column_name(len(normalized_columns))}{max(len(rows) + 1, 1)}"
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>{cols}</cols>
  <sheetData>{"".join(sheet_rows)}</sheetData>
  <autoFilter ref="A1:{last_cell}"/>
</worksheet>'''


def workbook_xml(sheet_names):
    sheets = "".join(
        f'<sheet name="{xml_escape(sheet_name(name))}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{sheets}</sheets>
</workbook>'''


def workbook_rels(sheet_count):
    rels = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    rels += f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>'''


def root_rels():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''


def content_types(sheet_count):
    overrides = [
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    for index in range(1, sheet_count + 1):
        overrides.append(f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  {"".join(overrides)}
</Types>'''


def styles_xml():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="4">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/></font>
    <font><u/><sz val="11"/><color rgb="FF2257C7"/><name val="Calibri"/></font>
  </fonts>
  <fills count="8">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF04786C"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE6F7F4"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFFF1F1"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE6F7F4"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFFF7DF"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF1F5F9"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left style="thin"><color rgb="FFD7DEE6"/></left><right style="thin"><color rgb="FFD7DEE6"/></right><top style="thin"><color rgb="FFD7DEE6"/></top><bottom style="thin"><color rgb="FFD7DEE6"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="9">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment horizontal="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"><alignment wrapText="1" vertical="top"/></xf>
    <xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="2" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="2" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="2" fillId="6" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="2" fillId="7" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="3" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def build_xlsx(sheets):
    buffer = BytesIO()
    names = list(sheets)
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types(len(names)))
        zf.writestr("_rels/.rels", root_rels())
        zf.writestr("xl/workbook.xml", workbook_xml(names))
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels(len(names)))
        zf.writestr("xl/styles.xml", styles_xml())
        for index, name in enumerate(names, start=1):
            headers, rows = sheets[name]
            zf.writestr(f"xl/worksheets/sheet{index}.xml", worksheet_xml(headers, rows))
    return buffer.getvalue()


def count_rows(rows, key):
    counts = {}
    for row in rows:
        value = row.get(key) or "Sin dato"
        counts[value] = counts.get(value, 0) + 1
    return [
        {"categoria": label, "cantidad": count, "porcentaje": percentage_label(count, len(rows))}
        for label, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    ]


def export_xlsx(environ, start_response):
    rows = list_cortes(request_filters(environ))
    export_rows = [operational_export_row(row, environ) for row in rows]
    headers = EXCEL_COLUMNS
    total = len(export_rows)
    active_count = sum(1 for row in rows if row["status"] == "activo")
    restored_count = sum(1 for row in rows if row["status"] == "restablecido")
    pending_count = sum(1 for row in rows if row["status"] == "pendiente")
    ignored_count = sum(1 for row in rows if row["status"] == "ignorado")
    photo_count = sum(1 for row in rows if row["attachment_path"])
    restore_photo_count = sum(1 for row in rows if row["restore_attachment_path"])
    sheets = {
        "Resumen": ([("indicador", "Indicador", 34), ("valor", "Valor", 22), ("lectura", "Lectura", 52)], [
            {"indicador": "Total registros", "valor": total, "lectura": "Cantidad total exportada segun los filtros aplicados."},
            {"indicador": "Activos", "valor": active_count, "lectura": "Cortes abiertos al momento de exportar."},
            {"indicador": "Restablecidos", "valor": restored_count, "lectura": f"{percentage_label(restored_count, total)} del total exportado."},
            {"indicador": "En revision", "valor": pending_count, "lectura": "Avisos pendientes de confirmar, corregir o descartar."},
            {"indicador": "Ignorados", "valor": ignored_count, "lectura": "Registros descartados o no operativos."},
            {"indicador": "Con foto de corte", "valor": photo_count, "lectura": f"{percentage_label(photo_count, total)} con evidencia inicial."},
            {"indicador": "Con foto de restablecimiento", "valor": restore_photo_count, "lectura": f"{percentage_label(restore_photo_count, total)} con evidencia de cierre."},
            {"indicador": "Generado", "valor": now_label()},
        ]),
        "Por bloque": ([("categoria", "Bloque / zona", 28), ("cantidad", "Cantidad", 12), ("porcentaje", "Porcentaje", 14)], count_rows(export_rows, "grupo_operativo")),
        "Por estado": ([("categoria", "Estado", 22), ("cantidad", "Cantidad", 12), ("porcentaje", "Porcentaje", 14)], count_rows(export_rows, "estado")),
        "Por tipo": ([("categoria", "Tipo", 24), ("cantidad", "Cantidad", 12), ("porcentaje", "Porcentaje", 14)], count_rows(export_rows, "tipo_aviso")),
        "Por fuente": ([("categoria", "Fuente", 28), ("cantidad", "Cantidad", 12), ("porcentaje", "Porcentaje", 14)], count_rows(export_rows, "origen")),
        "Activos": (headers, [item for item in export_rows if item["estado"] == STATUSES["activo"]]),
        "Restablecidos": (headers, [item for item in export_rows if item["estado"] == STATUSES["restablecido"]]),
        "Revision": (headers, [item for item in export_rows if item["estado"] == STATUSES["pendiente"]]),
        "Ignorados": (headers, [item for item in export_rows if item["estado"] == STATUSES["ignorado"]]),
        "Todos": (headers, export_rows),
    }
    headers_response = [("Content-Disposition", f'attachment; filename="registro_cortes_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx"')]
    return response(
        start_response,
        build_xlsx(sheets),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers_response,
    )


def group_key(value):
    replacements = str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU")
    text = str(value or "").translate(replacements).upper().replace("GRUPO", "").strip()
    if text == "ISLA":
        return "ISLA"
    range_match = re.search(r"\b([A-ZÑ])\s*(?:-|Y)\s*([A-ZÑ])\b", text)
    if range_match:
        return f"{range_match.group(1)}-{range_match.group(2)}"
    match = re.search(r"\b([A-ZÑ])\b", text)
    return match.group(1) if match else text


def infer_group(text):
    replacements = str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU")
    normalized = str(text or "").translate(replacements).upper()
    if re.search(r"GRUPO\s+ISLA\b", normalized):
        return "ISLA"
    range_match = re.search(r"GRUPO\s+([A-ZÑ])\s*-\s*([A-ZÑ])\b", normalized)
    if range_match:
        return f"{range_match.group(1)}-{range_match.group(2)}"
    range_match = re.search(r"GRUPO\s+([A-ZÑ])\s+Y\s+GRUPO\s+([A-ZÑ])\b", normalized)
    if range_match:
        return f"{range_match.group(1)}-{range_match.group(2)}"
    range_match = re.search(r"\b([A-ZÑ])\s*-\s*([A-ZÑ])\b", normalized)
    if range_match:
        return f"{range_match.group(1)}-{range_match.group(2)}"
    match = re.search(r"GRUPO\s+([A-ZÑ])(?:\s*[-/]\s*[A-ZÑ])?(?:\d+|[LI])?\b", normalized)
    if match:
        return match.group(1)
    return infer_group_from_zone_text(text)


def infer_outage_type(text):
    normalized = normalize_text(text)
    if any(word in normalized for word in ("restablecido", "normalizado", "volvio", "regreso", "levantado")):
        return "restablecido"
    if any(word in normalized for word in ("programado", "mantenimiento")):
        return "programado"
    if any(word in normalized for word in ("imprevisto", "inesperado", "urgente", "sin aviso", "corte", "falla de red", "media tension")):
        return "imprevisto"
    return "sin_clasificar"


def infer_group_from_street_perimeter(text):
    normalized = normalize_text(text)
    if not normalized:
        return ""
    searchable = f" {re.sub(r'[^a-z0-9n]+', ' ', normalized)} "
    rules = {
        "A": (
            ("urquiza", 4),
            ("independencia", 3),
            ("costanera", 2),
            ("anta", 4),
            ("principal", 3),
            ("principal", 3),
        ),
        "B": (
            ("independencia", 3),
            ("larrea", 4),
            ("costanera", 2),
            ("lavalle", 4),
            ("principal", 3),
            ("principal", 3),
        ),
        "F": (
            ("alferez pacheco", 5),
            ("pacheco", 3),
            ("lavalle", 3),
            ("principal", 3),
            ("principal", 3),
            ("castelli", 4),
            ("mitre", 4),
            ("costanera sur", 4),
            ("costanera", 2),
            ("villa fox", 4),
        ),
        "H": (
            ("espana", 4),
            ("lintridis", 5),
            ("ruta 9", 3),
        ),
        "I": (
            ("ruta 193", 5),
            ("km 3", 3),
            ("km 14", 3),
        ),
        "K": (
            ("santa fe", 4),
            ("pavon", 4),
            ("vias", 2),
            ("camino parque industrial", 5),
            ("calle 42", 4),
            ("camino viejo a lima", 5),
            ("callejon santa ana", 5),
        ),
        "L": (
            ("ruta 9", 3),
            ("km 92", 4),
            ("km 108", 4),
            ("camino parque industrial", 5),
            ("vias del ferrocarril", 4),
            ("entrada a lima", 4),
            ("calle 20", 3),
        ),
        "M": (
            ("vias del ferrocarril", 4),
            ("barrio cervecero", 5),
            ("atucha", 4),
            ("complejo nuclear", 5),
            ("puerto panal", 4),
        ),
        "Ñ": (
            ("lima", 2),
            ("calle 20", 5),
            ("vias del ferrocarril", 5),
        ),
    }

    ranked = []
    for group, terms in rules.items():
        score = 0
        hits = set()
        for term, weight in terms:
            compact = f" {normalize_text(term)} "
            if compact in searchable:
                score += weight
                hits.add(term)
        if score:
            ranked.append((score, len(hits), group))

    if not ranked:
        return ""
    ranked.sort(reverse=True)
    best_score, best_hits, best_group = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0
    if best_score >= 7 and best_hits >= 2 and best_score - second_score >= 3:
        return best_group
    return ""


def infer_program_schedule(text):
    normalized = normalize_text(text)
    if "programado" not in normalized and "mantenimiento" not in normalized:
        return {}

    reference = datetime.now()
    day = reference.day
    month = reference.month
    year = reference.year
    date_match = re.search(r"\b(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*(\d{2,4}))?\b", normalized)
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        if date_match.group(3):
            raw_year = int(date_match.group(3))
            year = raw_year + 2000 if raw_year < 100 else raw_year

    time_match = re.search(
        r"\b(?:de\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*(?:hs?|h)?\s*(?:a|hasta|-)\s*(\d{1,2})(?:[:.](\d{2}))?\s*(?:hs?|h)?\b",
        normalized,
    )
    if not time_match:
        return {}

    start_hour = min(int(time_match.group(1)), 23)
    start_minute = min(int(time_match.group(2) or 0), 59)
    end_hour = min(int(time_match.group(3)), 23)
    end_minute = min(int(time_match.group(4) or 0), 59)
    try:
        start = datetime(year, month, day, start_hour, start_minute)
    except ValueError:
        return {}
    if not date_match and start < reference.replace(second=0, microsecond=0):
        # Si no vino fecha y la hora ya paso, asumir que el aviso habla del proximo dia.
        start = start + timedelta(days=1)
    if date_match and start.date() < reference.date():
        try:
            start = datetime(year + 1, month, day, start_hour, start_minute)
        except ValueError:
            return {}
    end = start.replace(hour=end_hour, minute=end_minute)
    if end <= start:
        end = end + timedelta(days=1)
    return {
        "start": start.strftime("%Y-%m-%d %H:%M"),
        "estimatedRestore": end.strftime("%Y-%m-%d %H:%M"),
    }


def ocr_image_text(image_path):
    try:
        from PIL import Image
        import pytesseract
    except Exception:
        return ""
    try:
        image = Image.open(image_path)
        return pytesseract.image_to_string(image, lang="eng+spa", config="--psm 6")
    except Exception:
        return ""


def ocr_group_letter_from_plate(image_path):
    try:
        from PIL import Image, ImageOps
        import pytesseract
    except Exception:
        return ""
    try:
        image = Image.open(image_path).convert("RGB")
    except Exception:
        return ""

    width, height = image.size
    letter = image.crop((
        int(width * 0.22),
        int(height * 0.22),
        int(width * 0.34),
        int(height * 0.34),
    ))
    letter = letter.resize((letter.width * 6, letter.height * 6))

    # The group letter is black over a white rounded badge. Isolate dark pixels;
    # isolating the white badge makes Tesseract confuse the badge shape as text.
    mask = Image.new("L", letter.size, 255)
    source = letter.load()
    target = mask.load()
    for y in range(letter.height):
        for x in range(letter.width):
            red, green, blue = source[x, y]
            if red < 95 and green < 95 and blue < 95:
                target[x, y] = 0

    pixels = mask.load()
    black_pixels = [
        (x, y)
        for y in range(mask.height)
        for x in range(mask.width)
        if pixels[x, y] < 128
    ]
    if not black_pixels:
        return ""

    min_x = min(x for x, _ in black_pixels)
    max_x = max(x for x, _ in black_pixels) + 1
    min_y = min(y for _, y in black_pixels)
    max_y = max(y for _, y in black_pixels) + 1
    crop = mask.crop((min_x, min_y, max_x, max_y))
    crop = ImageOps.expand(crop, border=40, fill=255)

    try:
        raw = pytesseract.image_to_string(
            crop,
            lang="eng",
            config="--psm 13 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        )
    except Exception:
        return ""
    match = re.search(r"[A-ZÑ]", raw.upper())
    return match.group(0) if match else ""


def infer_notice_from_image(stored_name):
    if not stored_name:
        return {}
    image_path = UPLOAD_DIR / stored_name
    text = ocr_image_text(image_path)
    group = infer_group(text)
    if not group:
        group = infer_group_from_zone_text(text)
    if not group and "grupo" in normalize_text(text):
        group = ocr_group_letter_from_plate(image_path)
    outage_type = infer_outage_type(text)
    if outage_type == "sin_clasificar" and any(word in normalize_text(text) for word in ("corte", "afectada", "reposicion")):
        outage_type = "imprevisto"
    result = {
        "ocrText": text.strip(),
        "group": group,
        "type": outage_type,
    }
    result.update(infer_program_schedule(text))
    if group:
        result["message"] = f"Grupo {group} {TYPES.get(outage_type, outage_type)} desde imagen"
    return result


def infer_group_from_zone_text(text):
    normalized = normalize_text(text)
    if not normalized:
        return ""
    if re.search(r"\bisla\b", normalized):
        return "ISLA"
    street_group = infer_group_from_street_perimeter(text)
    if street_group:
        return street_group
    street_words = set(re.findall(r"[a-z0-9n]+", normalized))
    if {"urquiza", "independencia", "anta"}.issubset(street_words) and (
        "principal" in street_words or "costanera" in street_words
    ):
        return "A"
    if {"independencia", "larrea", "lavalle"}.issubset(street_words) and (
        "principal" in street_words or "costanera" in street_words
    ):
        return "B"
    try:
        zones_data = json.loads((DATA_DIR / "grupos_wsp_zonas.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    try:
        streets_data = json.loads((DATA_DIR / "grupos_wsp_calles.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        streets_data = {"groups": []}

    text_for_match = f" {re.sub(r'[^a-z0-9n]+', ' ', normalized)} "
    street_terms_by_group = {}
    for group in streets_data.get("groups", []):
        key = group_key(group.get("group", ""))
        terms = set()
        for item in group.get("terms", []):
            for match in item.get("matches", []):
                phrase = normalize_text(match.get("phrase", ""))
                if phrase:
                    terms.add(phrase)
                for street in match.get("streets", []):
                    name = normalize_text(street.get("name", ""))
                    if name:
                        terms.add(name)
        if terms:
            street_terms_by_group[key] = terms

    def add_term(target, value, weight):
        term = normalize_text(value)
        term = re.sub(r"^\d+\s*[-.]\s*", "", term)
        term = re.sub(r"[^a-z0-9n]+", " ", term).strip()
        if len(term) < 4:
            return
        ignored = {
            "grupo", "calles", "calle", "avenida", "av", "perimetro",
            "comprendido", "entre", "norte", "sur", "este", "oeste",
            "parcial", "zona", "afectada",
            "mitre", "costanera", "principal", "principal2", "lavalle",
            "independencia", "larrea", "urquiza", "anta", "castelli",
            "pacheco", "alferez pacheco",
        }
        if term in ignored:
            return
        target[term] = max(target.get(term, 0), weight)

    def split_terms(value):
        for term in re.split(r"[;,/()]+|\s+-\s+", value or ""):
            yield term

    def has_term(term):
        compact = f" {term} "
        if compact in text_for_match:
            return True
        if len(term) >= 8:
            first_words = " ".join(term.split()[:2])
            return len(first_words) >= 8 and f" {first_words} " in text_for_match
        return False

    ranked = []
    for group in zones_data.get("groups", []):
        key = group_key(group.get("group", ""))
        terms = {}
        add_term(terms, group.get("label", ""), 1)
        score = 0
        hits = set()
        for barrio in group.get("barrios", []):
            add_term(terms, barrio.get("name", ""), 5)
            add_term(terms, barrio.get("label", ""), 5)
            add_term(terms, barrio.get("matched_from", ""), 4)
        for term in split_terms(group.get("perimeter", "")):
            add_term(terms, term, 2)
        for item in group.get("review_terms", []):
            add_term(terms, item.get("term", ""), 2)
            for match in item.get("matches", []):
                add_term(terms, match.get("name", ""), 4)
                add_term(terms, match.get("label", ""), 4)
                add_term(terms, match.get("matched_from", ""), 3)
        for term in street_terms_by_group.get(key, set()):
            add_term(terms, term, 2)

        for term, weight in terms.items():
            if has_term(term):
                score += weight
                hits.add(term)
        if score:
            ranked.append((score, len(hits), key))

    if not ranked:
        return ""
    ranked.sort(reverse=True)
    best_score, best_hits, best_group = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0
    if len(ranked) > 1:
        _, _, second_group = ranked[1]
        if (
            len(best_group) == 1
            and len(second_group) == 1
            and best_score >= 8
            and second_score >= 8
            and abs(ord(best_group) - ord(second_group)) == 1
            and best_score - second_score <= 4
        ):
            left, right = sorted([best_group, second_group])
            return f"{left}-{right}"
    if best_score >= 5 and (best_hits >= 2 or best_score - second_score >= 3):
        return best_group
    return ""


def graph_get_json(url):
    request = Request(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"})
    with urlopen(request, timeout=20) as response_file:
        return json.loads(response_file.read().decode("utf-8"))


def graph_get_bytes(url):
    request = Request(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"})
    with urlopen(request, timeout=30) as response_file:
        return response_file.read()


def download_whatsapp_media(media_id, filename="aviso_wsp.jpg"):
    if not WHATSAPP_TOKEN or not media_id:
        return "", "", ""
    try:
        metadata = graph_get_json(f"https://graph.facebook.com/v20.0/{media_id}")
        media_url = metadata.get("url", "")
        mime_type = metadata.get("mime_type", "")
        content = graph_get_bytes(media_url) if media_url else b""
    except Exception:
        return "", "", ""
    if not Path(filename).suffix:
        if "png" in mime_type:
            filename = f"{filename}.png"
        elif "webp" in mime_type:
            filename = f"{filename}.webp"
        else:
            filename = f"{filename}.jpg"
    return save_binary_upload(content, filename)


def load_group_zones():
    try:
        data = json.loads((DATA_DIR / "grupos_wsp_zonas.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    groups = {}
    for group in data.get("groups", []):
        key = group_key(group.get("group", ""))
        zones = []
        for barrio in group.get("barrios", []):
            if barrio.get("id"):
                zones.append({
                    "zoneId": barrio.get("id", ""),
                    "zoneLabel": barrio.get("label", "") or barrio.get("name", ""),
                })
        if zones:
            groups[key] = zones
    shared_ab_zones = [
        {"zoneId": "zona-p1-1-12-de-octubre", "zoneLabel": "1-12 DE OCTUBRE"},
        {"zoneId": "zona-p1-20-centro", "zoneLabel": "20-CENTRO"},
        {"zoneId": "zona-p2-3-centro", "zoneLabel": "3-CENTRO"},
    ]
    for key in ("A", "B", "A/B"):
        groups.setdefault(key, shared_ab_zones)
    return groups


def known_group_keys():
    try:
        data = json.loads((DATA_DIR / "grupos_wsp_zonas.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    groups = {group_key(group.get("group", "")) for group in data.get("groups", [])}
    return {group for group in groups if group}


def group_validation(group):
    key = group_key(group)
    known = known_group_keys()
    if not key:
        return {"key": "", "valid": False, "combined": False, "reason": "sin grupo"}
    if key in known:
        return {"key": key, "valid": True, "combined": False, "reason": "grupo explicito del mapa"}
    if "-" in key:
        parts = [part for part in key.split("-") if part]
        if parts and all(part in known for part in parts):
            return {"key": key, "valid": True, "combined": True, "reason": "combinacion de grupos del mapa"}
    return {"key": key, "valid": False, "combined": False, "reason": "grupo fuera del mapa"}


def explicit_group_from_text(text):
    return infer_group(text)


def classify_group_from_notice(notice, message="", fallback_group=""):
    ocr_text = notice.get("ocrText", "")
    explicit = explicit_group_from_text(ocr_text) or explicit_group_from_text(message)
    if explicit:
        validation = group_validation(explicit)
        validation["source"] = "grupo_explicitamente_leido"
        return validation

    fallback = fallback_group or ""
    if fallback:
        validation = group_validation(fallback)
        if validation.get("valid"):
            validation["source"] = "grupo_del_conector"
            return validation

    inferred = notice.get("group") or ""
    if inferred:
        validation = group_validation(inferred)
        validation["source"] = "grupo_sugerido_por_descripcion"
        return validation

    if fallback:
        validation = group_validation(fallback)
        validation["source"] = "grupo_del_conector"
        return validation

    return {"key": "", "valid": False, "combined": False, "reason": "sin grupo", "source": "sin_grupo"}


def event_for_row(row, zone=None, priority=None):
    zone = zone or {}
    priority = priority or {}
    population = population_for_group(row["whatsapp_group"])
    map_priority = "low"
    if priority.get("tier") in {"critical", "high"}:
        map_priority = "high"
    elif priority.get("tier") == "medium":
        map_priority = "medium"
    return {
        "id": f"db-{row['id']}-{zone.get('zoneId') or 'general'}",
        "source": row["source"],
        "zoneId": zone.get("zoneId", ""),
        "zoneLabel": zone.get("zoneLabel") or row["zone_label"] or "Zona sin identificar",
        "type": row["outage_type"],
        "start": row["start_time"] or "",
        "end": "",
        "restored": False,
        "priority": map_priority,
        "priorityLabel": priority.get("label") or "Prioridad 4",
        "priorityReason": priority.get("reason") or "",
        "criticalCount": priority.get("critical_count", 0),
        "institutionCount": priority.get("institution_count", 0),
        "urbanDensity": priority.get("urban_density", 0),
        "group": row["whatsapp_group"] or "",
        "population": int(population.get("population") or 0),
        "households": int(population.get("households") or 0),
        "populationLabel": population_line(population),
        "affectedArea": row["zone_label"] or "",
        "detail": public_notice_detail(row),
        "estimatedRestore": public_estimated_restore(row),
        "message": row["message"] or "",
        "imageName": row["attachment_name"] or "",
        "createdAt": row["created_at"],
        "historyAt": row["start_time"] or row["created_at"],
    }


def active_cortes_api(start_response):
    update_scheduled_cortes()
    with db() as conn:
        rows = conn.execute(
            """
            select * from cortes
            where status = 'activo'
            order by created_at desc, id desc
            """
        ).fetchall()

    groups = load_group_zones()
    priority = priority_context(rows)
    events = []
    for row in rows:
        zones = groups.get(group_key(row["whatsapp_group"]))
        if zones:
            events.extend(event_for_row(row, zone, priority.get(row["id"], {})) for zone in zones)
        else:
            events.append(event_for_row(row, priority=priority.get(row["id"], {})))

    return response(
        start_response,
        json.dumps({"events": events}, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
        headers=NO_STORE_HEADERS,
    )


def historical_cortes_api(start_response):
    with db() as conn:
        rows = conn.execute(
            """
            select * from cortes
            where status != 'ignorado'
            order by datetime(coalesce(nullif(start_time, ''), created_at)) asc, id asc
            """
        ).fetchall()
        stored_count = conn.execute("select count(*) from cortes").fetchone()[0]
        ignored_count = conn.execute("select count(*) from cortes where status = 'ignorado'").fetchone()[0]

    groups = load_group_zones()
    priority = priority_context(rows)
    events = []
    for row in rows:
        zones = groups.get(group_key(row["whatsapp_group"]))
        row_events = zones or [None]
        for zone in row_events:
            item = event_for_row(row, zone, priority.get(row["id"], {}))
            item["restored"] = row["status"] == "restablecido"
            item["end"] = (row["end_time"] or infer_restore_time(row["notes"] or "") or "restablecido") if row["status"] == "restablecido" else ""
            item["status"] = row["status"]
            events.append(item)

    return response(
        start_response,
        json.dumps({
            "events": events,
            "meta": {
                "storedRecords": stored_count,
                "ignoredRecords": ignored_count,
                "historyRecords": len(rows),
                "historyEvents": len(events),
            },
        }, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
        headers=NO_STORE_HEADERS,
    )


def cortes_movements_api(start_response):
    with db() as conn:
        rows = conn.execute(
            """
            select
              id, created_at, source, status, outage_type, whatsapp_group, zone_label,
              message, notes, attachment_name, restore_attachment_name, end_time,
              coalesce(end_time, created_at) movement_time
            from cortes
            where source in ('whatsapp_group_connector', 'whatsapp_real', 'whatsapp_demo', 'manual')
            order by datetime(coalesce(end_time, created_at)) desc, id desc
            limit 20
            """
        ).fetchall()

    movements = []
    for row in rows:
        zone = row["zone_label"] or row["whatsapp_group"] or "Zona sin identificar"
        movements.append({
            "id": row["id"],
            "source": row["source"] or "",
            "sourceLabel": "WhatsApp" if str(row["source"] or "").startswith("whatsapp") else "Carga manual",
            "status": row["status"],
            "statusLabel": STATUSES.get(row["status"], row["status"]),
            "type": row["outage_type"],
            "typeLabel": TYPES.get(row["outage_type"], row["outage_type"]),
            "group": row["whatsapp_group"] or "",
            "zone": zone,
            "message": row["message"] or row["notes"] or "",
            "createdAt": row["created_at"],
            "endedAt": row["end_time"] or "",
            "movementAt": row["movement_time"] or row["created_at"],
            "hasOutageImage": bool(row["attachment_name"]),
            "hasRestoreImage": bool(row["restore_attachment_name"]),
            "fingerprint": f'{row["id"]}:{row["status"]}:{row["end_time"] or ""}:{row["created_at"]}',
        })

    return response(
        start_response,
        json.dumps({"movements": movements}, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
        headers=NO_STORE_HEADERS,
    )


def close_active_group(group, note, restore_attachment=None):
    restore_attachment = restore_attachment or {}
    params = [note, now_label()]
    set_attachment = ""
    if restore_attachment.get("path"):
        set_attachment = """,
                    restore_attachment_name = ?,
                    restore_attachment_path = ?,
                    restore_attachment_sha256 = ?"""
        params.extend([
            restore_attachment.get("name", ""),
            restore_attachment.get("path", ""),
            restore_attachment.get("sha256", ""),
        ])
    params.append(group_key(group))
    with db() as conn:
        cursor = conn.execute(
            f"""
            update cortes
            set status = 'restablecido',
                notes = trim(coalesce(notes, '') || ?),
                end_time = ?
                {set_attachment}
            where status = 'activo'
              and upper(replace(coalesce(whatsapp_group, ''), 'Grupo ', '')) = ?
            """,
            params,
        )
        conn.commit()
        return cursor.rowcount


def whatsapp_ingest_api(environ, start_response):
    payload = parse_json(environ)
    message = str(payload.get("message") or "").strip()
    group = str(payload.get("group") or "").strip()
    action = str(payload.get("action") or "outage").strip()
    outage_type = str(payload.get("type") or "sin_clasificar").strip()
    if not message or not group:
        return response(
            start_response,
            json.dumps({"ok": False, "error": "message y group son obligatorios"}, ensure_ascii=False),
            "400 Bad Request",
            "application/json; charset=utf-8",
        )

    original_name, stored_name, file_hash = save_data_url_upload(
        payload.get("imageData") or "",
        payload.get("fileName") or "aviso_wsp.jpg",
    )
    if not original_name:
        original_name = payload.get("fileName") or ""
        stored_name = ""
        file_hash = ""

    if action == "restore" or outage_type == "restablecido":
        closed = close_active_group(
            group,
            f"\nRestablecido desde WSP demo {now_label()}: {payload.get('observation') or message}",
            {"name": original_name, "path": stored_name, "sha256": file_hash},
        )
        return response(
            start_response,
            json.dumps({"ok": True, "action": "restore", "closed": closed}, ensure_ascii=False),
            content_type="application/json; charset=utf-8",
        )

    with db() as conn:
        cursor = conn.execute(
            """
            insert into cortes (
              created_at, source, whatsapp_group, sender, message, attachment_name, attachment_path, attachment_sha256,
              outage_type, zone_label, start_time, estimated_end, status, notes
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_label(),
                payload.get("source") or "whatsapp_demo",
                group_key(group),
                payload.get("sender") or "Canal WSP demo",
                message,
                original_name,
                stored_name,
                file_hash,
                outage_type if outage_type in TYPES else "sin_clasificar",
                payload.get("affectedArea") or group,
                payload.get("start") or "",
                payload.get("estimatedRestore") or payload.get("estimated_end") or "",
                "activo",
                payload.get("detail") or payload.get("observation") or "",
            ),
        )
        conn.commit()
        corte_id = cursor.lastrowid
    return response(
        start_response,
        json.dumps({"ok": True, "action": "outage", "id": corte_id}, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
    )


def is_local_request(environ):
    remote = environ.get("REMOTE_ADDR", "")
    return remote in {"127.0.0.1", "::1", "localhost"}


def group_connector_api(environ, start_response):
    if not is_local_request(environ):
        token = environ.get("HTTP_X_CONNECTOR_TOKEN", "")
        if not GROUP_CONNECTOR_TOKEN or not hmac.compare_digest(token, GROUP_CONNECTOR_TOKEN):
            return response(
                start_response,
                json.dumps({"ok": False, "error": "No autorizado"}, ensure_ascii=False),
                "403 Forbidden",
                "application/json; charset=utf-8",
            )

    payload = parse_json(environ)
    group = str(payload.get("group") or "").strip()
    message = str(payload.get("message") or "").strip() or "Aviso recibido por imagen de grupo"
    if not group:
        return response(
            start_response,
            json.dumps({"ok": False, "error": "group es obligatorio"}, ensure_ascii=False),
            "400 Bad Request",
            "application/json; charset=utf-8",
        )

    original_name, stored_name, file_hash = save_data_url_upload(
        payload.get("imageData") or "",
        payload.get("fileName") or "grupo-wsp.jpg",
    )
    notice = infer_notice_from_image(stored_name) if stored_name else {}
    group_decision = classify_group_from_notice(notice, message, payload.get("operationalGroup") or "")
    inferred_group = group_decision.get("key", "")
    outage_type = payload.get("type") or notice.get("type") or infer_outage_type(message)
    final_message = notice.get("message") or message
    notes = "\n".join(
        part for part in [
            f"Conector grupos WhatsApp. Grupo origen: {group}",
            f"Remitente: {payload.get('sender') or '-'}",
            f"ID mensaje: {payload.get('messageId') or '-'}",
            f"Clasificacion: {group_decision.get('source')} / {group_decision.get('reason')}",
            f"OCR: {notice.get('ocrText', '')[:900]}" if notice.get("ocrText") else "",
        ]
        if part
    )
    result = insert_whatsapp_event({
        "source": "whatsapp_group_connector",
        "sender": payload.get("sender") or "",
        "group": inferred_group,
        "message": final_message,
        "fileName": original_name,
        "attachmentPath": stored_name,
        "attachmentSha256": file_hash,
        "type": outage_type,
        "affectedArea": f"Grupo {group_key(inferred_group)}" if inferred_group else "",
        "start": notice.get("start") or payload.get("start") or "",
        "estimatedRestore": notice.get("estimatedRestore") or payload.get("estimatedRestore") or "",
        "detail": notes,
        "groupDecision": group_decision,
    })
    return response(
        start_response,
        json.dumps({"ok": True, "processed": result}, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
    )


def insert_whatsapp_event(payload):
    message = str(payload.get("message") or "").strip()
    current_source = payload.get("source") or "whatsapp_real"
    source_label = "conector grupos WhatsApp" if current_source == "whatsapp_group_connector" else "WhatsApp real"
    group_decision = payload.get("groupDecision") or {}
    if not group_decision:
        explicit = explicit_group_from_text(message)
        candidate = payload.get("group") or explicit
        group_decision = group_validation(candidate)
        group_decision["source"] = "grupo_explicitamente_leido" if explicit else "grupo_sugerido_por_descripcion"
    inferred_group = group_decision.get("key") or group_key(payload.get("group") or infer_group(message))
    group = inferred_group
    outage_type = payload.get("type") or infer_outage_type(message)
    if not group:
        group = "SIN_GRUPO"
    original_name = payload.get("fileName") or ""
    stored_name = payload.get("attachmentPath") or ""
    file_hash = payload.get("attachmentSha256") or ""
    if outage_type == "restablecido":
        if not group_decision.get("valid"):
            with db() as conn:
                cursor = conn.execute(
                    """
                    insert into cortes (
                      created_at, source, whatsapp_group, sender, message, attachment_name, attachment_path, attachment_sha256,
                      outage_type, zone_label, start_time, estimated_end, status, notes
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now_label(),
                        current_source,
                        group,
                        payload.get("sender") or "",
                        message or "Aviso recibido por imagen de WhatsApp",
                        original_name,
                        stored_name,
                        file_hash,
                        "restablecido",
                        payload.get("affectedArea") or f"Grupo {group}",
                        payload.get("start") or "",
                        payload.get("estimatedRestore") or "",
                        "pendiente",
                        (payload.get("detail") or "") + f"\nPendiente: restablecimiento sin grupo valido. {group_decision.get('reason') or ''}",
                    ),
                )
                conn.commit()
                return {"action": "pending_restore", "id": cursor.lastrowid}
        closed = close_active_group(
            group,
            f"\nRestablecido desde {source_label} {now_label()}: {message}",
            {"name": original_name, "path": stored_name, "sha256": file_hash},
        )
        if not closed:
            with db() as conn:
                cursor = conn.execute(
                    """
                    insert into cortes (
                      created_at, source, whatsapp_group, sender, message, attachment_name, attachment_path, attachment_sha256,
                      outage_type, zone_label, start_time, estimated_end, status, notes
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now_label(),
                        current_source,
                        group,
                        payload.get("sender") or "",
                        message or "Restablecimiento recibido por imagen de WhatsApp",
                        "",
                        "",
                        "",
                        "restablecido",
                        payload.get("affectedArea") or f"Grupo {group}",
                        payload.get("start") or "",
                        payload.get("estimatedRestore") or "",
                        "pendiente",
                        (
                            (payload.get("detail") or "")
                            + "\nPendiente: restablecimiento recibido pero no habia corte activo para ese grupo."
                        ).strip(),
                    ),
                )
                if stored_name:
                    conn.execute(
                        """
                        update cortes
                        set restore_attachment_name = ?,
                            restore_attachment_path = ?,
                            restore_attachment_sha256 = ?
                        where id = ?
                        """,
                        (original_name, stored_name, file_hash, cursor.lastrowid),
                    )
                conn.commit()
                return {"action": "pending_restore_without_active", "id": cursor.lastrowid}
        return {"action": "restore", "closed": closed}

    is_meta_sample = normalize_text(message) == "this is a text message"

    # WhatsApp users often send the photo and the group text as two messages.
    # Keep the map clean by joining those nearby messages from the same sender.
    sender = payload.get("sender") or ""
    if inferred_group and not stored_name:
        with db() as conn:
            pending_image = conn.execute(
                """
                select id from cortes
                where source = ?
                  and sender = ?
                  and status = 'pendiente'
                  and coalesce(attachment_path, '') != ''
                  and upper(replace(coalesce(whatsapp_group, ''), 'Grupo ', '')) = 'SIN_GRUPO'
                  and datetime(created_at) >= datetime('now', 'localtime', '-15 minutes')
                order by created_at desc, id desc
                limit 1
                """,
                (current_source, sender),
            ).fetchone()
            if pending_image:
                conn.execute(
                    """
                    update cortes
                    set whatsapp_group = ?,
                        message = ?,
                        outage_type = ?,
                        zone_label = ?,
                        status = 'activo',
                        notes = trim(coalesce(notes, '') || ?)
                    where id = ?
                    """,
                    (
                        inferred_group,
                        message,
                        outage_type if outage_type in TYPES else "sin_clasificar",
                        f"Grupo {inferred_group}",
                        f"\nClasificado por mensaje posterior {now_label()}: {message}",
                        pending_image["id"],
                    ),
                )
                conn.commit()
                return {"action": "classify_pending_image", "id": pending_image["id"]}

    if stored_name and not inferred_group:
        with db() as conn:
            active_event = conn.execute(
                """
                select id from cortes
                where source = ?
                  and sender = ?
                  and status = 'activo'
                  and coalesce(attachment_path, '') = ''
                  and upper(replace(coalesce(whatsapp_group, ''), 'Grupo ', '')) != 'SIN_GRUPO'
                  and datetime(created_at) >= datetime('now', 'localtime', '-15 minutes')
                order by created_at desc, id desc
                limit 1
                """,
                (current_source, sender),
            ).fetchone()
            if active_event:
                conn.execute(
                    """
                    update cortes
                    set attachment_name = ?,
                        attachment_path = ?,
                        attachment_sha256 = ?,
                        notes = trim(coalesce(notes, '') || ?)
                    where id = ?
                    """,
                    (
                        original_name,
                        stored_name,
                        file_hash,
                        f"\nEvidencia adjuntada desde WhatsApp {now_label()}",
                        active_event["id"],
                    ),
                )
                conn.commit()
                return {"action": "attach_image", "id": active_event["id"]}

    can_activate = bool(inferred_group) and group_decision.get("valid") and not is_meta_sample
    if outage_type == "programado" and can_activate:
        status = schedule_status(payload.get("start") or "", payload.get("estimatedRestore") or "")
    else:
        status = "activo" if can_activate else "pendiente"
    notes = payload.get("detail") or ""
    if status == "activo" and group_decision.get("source") == "grupo_sugerido_por_descripcion":
        notes = (
            notes
            + "\nAlerta: corte activado automaticamente con grupo sugerido por descripcion. Revisar cuando sea posible."
        ).strip()
    if outage_type == "programado" and status == "programado":
        notes = (notes + "\nProgramado: se activara automaticamente al llegar la hora de inicio y se cerrara al finalizar la ventana estimada.").strip()
    if outage_type == "programado" and status == "restablecido":
        notes = (notes + "\nProgramado: ingresado con ventana horaria ya finalizada.").strip()
    if status == "pendiente":
        reason = group_decision.get("reason") or "mensaje sin grupo operativo identificado"
        source = group_decision.get("source") or "sin_grupo"
        notes = (notes + f"\nAlerta importante: aviso recibido automaticamente pero requiere revision. Motivo: {reason}. Fuente: {source}.").strip()
    with db() as conn:
        cursor = conn.execute(
            """
            insert into cortes (
              created_at, source, whatsapp_group, sender, message, attachment_name, attachment_path, attachment_sha256,
              outage_type, zone_label, start_time, estimated_end, status, notes
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_label(),
                current_source,
                group,
                payload.get("sender") or "",
                message or "Aviso recibido por imagen de WhatsApp",
                original_name,
                stored_name,
                file_hash,
                outage_type if outage_type in TYPES else "sin_clasificar",
                payload.get("affectedArea") or f"Grupo {group}",
                payload.get("start") or "",
                payload.get("estimatedRestore") or "",
                status,
                notes,
            ),
        )
        conn.commit()
        return {"action": "outage", "id": cursor.lastrowid}


def whatsapp_webhook_verify(environ, start_response):
    params = parse_qs(environ.get("QUERY_STRING", ""))
    mode = (params.get("hub.mode") or [""])[0]
    token = (params.get("hub.verify_token") or [""])[0]
    challenge = (params.get("hub.challenge") or [""])[0]
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return response(start_response, challenge, content_type="text/plain; charset=utf-8")
    return response(start_response, "Forbidden", "403 Forbidden", "text/plain; charset=utf-8")


def whatsapp_webhook_receive(environ, start_response):
    payload = parse_json(environ)
    processed = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = value.get("contacts", [])
            contact_name = contacts[0].get("profile", {}).get("name", "") if contacts else ""
            for message_item in value.get("messages", []):
                msg_type = message_item.get("type", "")
                sender = message_item.get("from", "")
                text = ""
                file_name = ""
                stored_name = ""
                file_hash = ""
                detail = f"WhatsApp real {msg_type}"
                if msg_type == "text":
                    text = message_item.get("text", {}).get("body", "")
                elif msg_type == "image":
                    image = message_item.get("image", {})
                    caption = image.get("caption", "")
                    text = caption or "Aviso recibido por imagen de WhatsApp"
                    media_id = image.get("id", "")
                    file_name = f"wsp-{message_item.get('id', int(time.time()))}.jpg"
                    original, stored, digest = download_whatsapp_media(media_id, file_name)
                    file_name, stored_name, file_hash = original, stored, digest
                    if not stored_name:
                        detail = f"{detail}. Imagen recibida, pendiente de descarga/configuración de token."
                    elif not caption:
                        notice = infer_notice_from_image(stored_name)
                        if notice.get("message"):
                            text = notice["message"]
                        if notice.get("ocrText"):
                            detail = f"{detail}. OCR: {notice['ocrText'][:900]}"
                else:
                    text = f"Aviso WhatsApp tipo {msg_type}. Revisar manualmente."
                payload = {
                    "source": "whatsapp_real",
                    "sender": contact_name or sender,
                    "message": text,
                    "fileName": file_name,
                    "attachmentPath": stored_name,
                    "attachmentSha256": file_hash,
                    "detail": detail,
                }
                if msg_type == "image" and stored_name and not image.get("caption", ""):
                    notice = infer_notice_from_image(stored_name)
                    if notice.get("group"):
                        payload["group"] = notice["group"]
                    if notice.get("type"):
                        payload["type"] = notice["type"]
                result = insert_whatsapp_event(payload)
                processed.append(result)
    return response(
        start_response,
        json.dumps({"ok": True, "processed": processed}, ensure_ascii=False),
        content_type="application/json; charset=utf-8",
    )


def serve_file(start_response, base_dir, rel_path, content_type=None):
    safe = Path(rel_path).name if base_dir == UPLOAD_DIR else rel_path.strip("/")
    path = (base_dir / safe).resolve()
    if not str(path).startswith(str(base_dir.resolve())) or not path.exists() or not path.is_file():
        return response(start_response, "No encontrado", "404 Not Found", "text/plain; charset=utf-8")
    if content_type is None:
        suffix = path.suffix.lower()
        content_type = {
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".html": "text/html; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".geojson": "application/geo+json; charset=utf-8",
            ".pdf": "application/pdf",
            ".csv": "text/csv; charset=utf-8",
        }.get(suffix, "application/octet-stream")
    return response(start_response, path.read_bytes(), content_type=content_type)


def app(environ, start_response):
    init_db()
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")

    if path.startswith("/static/"):
        return serve_file(start_response, ROOT / "static", path.removeprefix("/static/"))
    if path.startswith("/assets/"):
        return serve_file(start_response, ROOT / "assets", path.removeprefix("/assets/"))
    if path == "/health":
        return health_api(start_response)
    if path == "/privacy":
        return response(start_response, privacy_page())
    if path == "/data-deletion":
        return response(start_response, data_deletion_page())
    if path == "/api/whatsapp-demo" and method == "POST":
        return whatsapp_ingest_api(environ, start_response)
    if path == "/api/group-connector" and method == "POST":
        return group_connector_api(environ, start_response)
    if path == "/webhook/whatsapp" and method == "GET":
        return whatsapp_webhook_verify(environ, start_response)
    if path == "/webhook/whatsapp" and method == "POST":
        return whatsapp_webhook_receive(environ, start_response)
    if path.startswith("/uploads/"):
        if not is_logged_in(environ):
            return redirect(start_response, "/login")
        return serve_file(start_response, UPLOAD_DIR, path.removeprefix("/uploads/"))

    if path == "/login" and method == "GET":
        return response(start_response, login_page())
    if path == "/login" and method == "POST":
        fields = parse_urlencoded(environ)
        if fields.get("username") == ADMIN_USER and fields.get("password") == ADMIN_PASS:
            headers = [("Set-Cookie", f"session={sign(ADMIN_USER)}; Path=/; HttpOnly; SameSite=Lax")]
            return response(start_response, "", "302 Found", headers=[("Location", "/"), *headers])
        return response(start_response, login_page("Usuario o contraseña incorrectos."), "401 Unauthorized")
    if path == "/logout":
        return response(start_response, "", "302 Found", headers=[("Location", "/login"), ("Set-Cookie", "session=; Path=/; Max-Age=0")])

    if not is_logged_in(environ):
        return redirect(start_response, "/login")

    if path == "/mapa.html":
        return response(
            start_response,
            (ROOT / "mapa.html").read_bytes(),
            content_type="text/html; charset=utf-8",
            headers=NO_STORE_HEADERS,
        )
    if path in ("/index.html", "/revisar_zonas.html", "/revisar_wsp.html", "/revisar_calles.html"):
        return serve_file(start_response, ROOT, path.removeprefix("/"))
    if path.startswith("/data/plano_pdf/"):
        return serve_file(start_response, ROOT, path.removeprefix("/"))
    if path in ("/data/instituciones.json", "/data/zonas_municipales.geojson", "/data/barrios_2026.geojson", "/data/renabap_zte.geojson", "/data/manzanas_zte.geojson", "/data/calles_ciudad.geojson", "/data/grupos_wsp_perimetros.geojson", "/data/grupos_wsp_perimetros_ruta.geojson", "/data/grupos_wsp_calles_detectadas.geojson", "/data/grupos_wsp_calles.json", "/data/grupos_wsp_zonas.json", "/data/grupos_whatsapp_cooperativa.json", "/data/grupos_whatsapp_manual.geojson", "/data/poblacion_por_zona_2022.json"):
        return serve_file(start_response, ROOT, path.removeprefix("/"))
    if path == "/api/cortes-activos":
        return active_cortes_api(start_response)
    if path == "/api/cortes-historico":
        return historical_cortes_api(start_response)
    if path == "/api/cortes-movimientos":
        return cortes_movements_api(start_response)
    if path == "/" and method == "GET":
        return dashboard(environ, start_response)
    if path == "/dashboard-cortes" and method == "GET":
        return cuts_dashboard_page(start_response)
    if path == "/historial" and method == "GET":
        return history_page(environ, start_response)
    if path == "/sistema" and method == "GET":
        return system_page(start_response)
    if path == "/cortes/nuevo" and method == "GET":
        return new_corte_page(start_response)
    if path == "/cortes/nuevo" and method == "POST":
        return create_corte(environ, start_response)
    if path.startswith("/cortes/") and path.endswith("/editar") and method == "POST":
        corte_id = path.removeprefix("/cortes/").removesuffix("/editar").strip("/")
        if corte_id.isdigit():
            return update_corte(environ, start_response, int(corte_id))
    if path.startswith("/cortes/") and path.endswith("/accion") and method == "POST":
        corte_id = path.removeprefix("/cortes/").removesuffix("/accion").strip("/")
        if corte_id.isdigit():
            return corte_action(environ, start_response, int(corte_id))
    if path.startswith("/cortes/") and method == "GET":
        corte_id = path.removeprefix("/cortes/").strip("/")
        if corte_id.isdigit():
            return corte_detail(environ, start_response, int(corte_id))
    if path == "/export/cortes.csv" and method == "GET":
        return export_csv(environ, start_response)
    if path == "/export/cortes.xlsx" and method == "GET":
        return export_xlsx(environ, start_response)

    return response(start_response, "No encontrado", "404 Not Found", "text/plain; charset=utf-8")


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "8091"))
    print(f"Servidor en http://localhost:{port}")
    print(f"Usuario inicial: {ADMIN_USER} / contraseña configurada en .env")
    with make_server("", port, app) as server:
        server.serve_forever()
