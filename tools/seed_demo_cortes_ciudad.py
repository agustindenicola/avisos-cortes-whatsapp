#!/usr/bin/env python3
import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "cortes.db"
SOURCE = "demo_ciudad"

DEMO_CORTES = [
    {
        "zone": "San Cayetano",
        "kind": "imprevisto",
        "message": "SIMULACION: corte imprevisto en barrio San Cayetano, zona CAPS 4.",
        "notes": "Demo comercial Ciudad. No corresponde a un evento real.",
        "minutes_ago": 18,
        "estimated": "90 min",
    },
    {
        "zone": "Barrio Dálmine",
        "kind": "imprevisto",
        "message": "SIMULACION: usuarios reportan baja tension y corte parcial en Barrio Dálmine.",
        "notes": "Demo comercial Ciudad. No corresponde a un evento real.",
        "minutes_ago": 42,
        "estimated": "2 hs",
    },
    {
        "zone": "Siderca",
        "kind": "programado",
        "message": "SIMULACION: corte programado en zona Siderca por mantenimiento de red.",
        "notes": "Demo comercial Ciudad. No corresponde a un evento real.",
        "minutes_ago": 75,
        "estimated": "12:30",
    },
    {
        "zone": "Lomas del Río Luján",
        "kind": "imprevisto",
        "message": "SIMULACION: corte de luz informado en Lomas del Rio Lujan.",
        "notes": "Demo comercial Ciudad. No corresponde a un evento real.",
        "minutes_ago": 105,
        "estimated": "en evaluacion",
    },
]


def ensure_schema(conn):
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


def clear_demo(conn):
    conn.execute("delete from cortes where source = ?", (SOURCE,))


def seed_demo(conn):
    now = datetime.now()
    clear_demo(conn)
    for item in DEMO_CORTES:
        created_at = now - timedelta(minutes=item["minutes_ago"])
        conn.execute(
            """
            insert into cortes (
              created_at, source, whatsapp_group, sender, message,
              attachment_name, attachment_path, attachment_sha256,
              restore_attachment_name, restore_attachment_path, restore_attachment_sha256,
              outage_type, zone_label, start_time, end_time, estimated_end, status, notes
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at.strftime("%Y-%m-%d %H:%M:%S"),
                SOURCE,
                "Demo Ciudad",
                "simulacion",
                item["message"],
                "",
                "",
                "",
                "",
                "",
                "",
                item["kind"],
                item["zone"],
                created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "",
                item["estimated"],
                "activo",
                item["notes"],
            ),
        )


def main():
    parser = argparse.ArgumentParser(description="Carga o borra cortes demo de Ciudad.")
    parser.add_argument("--clear", action="store_true", help="Borra solo los cortes demo.")
    args = parser.parse_args()

    DB.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB) as conn:
        ensure_schema(conn)
        if args.clear:
            clear_demo(conn)
            conn.commit()
            print("Cortes demo borrados.")
            return
        seed_demo(conn)
        conn.commit()
        count = conn.execute("select count(*) from cortes where source = ?", (SOURCE,)).fetchone()[0]
        print(f"Cortes demo cargados: {count}")


if __name__ == "__main__":
    main()
