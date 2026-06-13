"""Development helper commands for tvcli."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Annotated

import typer

from ..layers import freefloat, freefloat_archive
from ..output import build_envelope, emit

app = typer.Typer(add_completion=False, help="Developer helper utilities")

# Static list of famous BIST symbols
BIST_SYMBOLS = [
    ("THYAO", "Hava Yolları", "Ulaştırma", "Aviation"),
    ("ASELS", "Aselsan", "Savunma", "Defense"),
    ("EREGL", "Erdemir", "Metal", "Steel"),
    ("TUPRS", "Tüpraş", "Rafineri", "Refinery"),
    ("KCHOL", "Koç Holding", "Holding", "Conglomerates"),
    ("SAHOL", "Sabancı Holding", "Holding", "Conglomerates"),
    ("GARAN", "Garanti Bankası", "Bankacılık", "Banking"),
    ("AKBNK", "Akbank", "Bankacılık", "Banking"),
    ("YKBNK", "Yapı Yredi", "Bankacılık", "Banking"),
    ("ISCTR", "İş Bankası", "Bankacılık", "Banking"),
    ("BIMAS", "BİM Mağazalar", "Perakende", "Retail"),
    ("SISE", "Şişecam", "Cam", "Glass"),
    ("TOASO", "Tofaş Oto", "Otomotiv", "Automotive"),
    ("FROTO", "Ford Otosan", "Otomotiv", "Automotive"),
    ("HEKTS", "Hektaş", "Tarım", "Agriculture"),
    ("SASA", "Sasa Polyester", "Kimya", "Chemicals"),
    ("PETKM", "Petkim", "Kimya", "Chemicals"),
    ("TTKOM", "Türk Telekom", "Telekom", "Telecom"),
    ("TCELL", "Turkcell", "Telekom", "Telecom"),
    ("PGSUS", "Pegasus", "Hava Yolları", "Aviation"),
]


@app.command("seed-db")
def seed_db(
    ctx: typer.Context,
    symbols_count: Annotated[
        int, typer.Option("--symbols", help="Number of symbols to generate")
    ] = 30,
    days_count: Annotated[
        int, typer.Option("--days", help="Number of days of history to generate")
    ] = 20,
    json_mode: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Populate the local SQLite archive database with high-quality mock data."""
    store = freefloat_archive.ArchiveStore()

    # 1. Clear database tables first to prevent constraints/overlap conflicts
    with store._connect() as conn:
        conn.execute("DELETE FROM freefloat_reports")
        conn.execute("DELETE FROM freefloat_snapshots")
        conn.execute("DELETE FROM freefloat_changes")
        conn.execute("DELETE FROM freefloat_events")
        conn.execute("DELETE FROM freefloat_symbol_summary")
        conn.execute("DELETE FROM freefloat_symbol_metadata")
        conn.execute("DELETE FROM sync_state")
        conn.execute("DELETE FROM kap_disclosures")

    # 2. Select / Generate symbols
    selected_symbols: list[tuple[str, str, str, str]] = []
    for i in range(symbols_count):
        if i < len(BIST_SYMBOLS):
            selected_symbols.append(BIST_SYMBOLS[i])
        else:
            # Generate dummy symbol
            code = f"MOCK{i + 1:03d}"
            name = f"Mock Company {i + 1}"
            selected_symbols.append((code, name, "Genel", "General"))

    # 3. Add metadata
    now_iso = datetime.now().isoformat()
    with store._connect() as conn:
        for code, _, sector, industry in selected_symbols:
            conn.execute(
                "INSERT INTO freefloat_symbol_metadata"
                "(code, sector, industry, updated_at) "
                "VALUES(?, ?, ?, ?)",
                (code, sector, industry, now_iso),
            )

    # 4. Generate dates (business days only)
    dates: list[date] = []
    curr = datetime.now().date()
    while len(dates) < days_count:
        if curr.weekday() < 5:
            dates.append(curr)
        curr -= timedelta(days=1)
    dates.reverse()

    ratios: dict[str, float] = {}
    capitals: dict[str, float] = {}
    for code, _, _, _ in selected_symbols:
        ratios[code] = random.uniform(10.0, 75.0)
        capitals[code] = random.uniform(1e8, 1e10)

    # 5. Populate day by day
    reports_synced = 0
    kap_to_insert: list[tuple[str, str, str, str, str, str]] = []
    for day in dates:
        day_records: list[freefloat.FloatRecord] = []
        date_label = day.strftime("%d.%m.%Y")

        jump_symbol = (
            random.choice(selected_symbols)[0] if random.random() < 0.3 else None
        )

        for code, name, _, _ in selected_symbols:
            if code == jump_symbol:
                delta = random.choice([-8.0, 7.5, -12.0, 6.0])
                ratios[code] = max(1.0, min(99.0, ratios[code] + delta))

                # Create a mock KAP disclosure
                title = random.choice(
                    [
                        "Fiili Dolaşım Pay Oranı Değişikliği",
                        "Pay Satış Bilgi Formu",
                        "Sermaye Artırımı Tescili",
                    ]
                )
                summary = (
                    "Şirketimizin fiili dolaşım pay oranı, ortakların pay "
                    "işlemleri veya sermaye değişiklikleri kapsamında "
                    f"güncellenmiştir. Güncel oran %{ratios[code]:.2f} "
                    "olarak gerçekleşmiştir."
                )
                rand_id = random.randint(100000, 999999)
                url = f"https://www.kap.org.tr/tr/Bildirim/{rand_id}"
                kap_to_insert.append(
                    (code, day.isoformat(), title, summary, url, now_iso)
                )
            else:
                ratios[code] = max(
                    1.0, min(99.0, ratios[code] + random.uniform(-0.3, 0.3))
                )

            if random.random() < 0.02:
                capitals[code] *= random.choice([1.1, 1.2, 1.5])

            float_shares = capitals[code] * ratios[code] / 100.0

            day_records.append(
                freefloat.FloatRecord(
                    code=code,
                    isin=f"TR{code}91W{random.randint(1, 9)}",
                    name=name,
                    float_shares=float_shares,
                    capital=capitals[code],
                    ratio=ratios[code],
                    date=date_label,
                )
            )

        store.sync_records(tuple(day_records))
        reports_synced += 1

    # Insert mock KAP disclosures in a single connection block
    with store._connect() as conn:
        for item in kap_to_insert:
            conn.execute(
                """
                INSERT INTO kap_disclosures
                (code, disclosure_date, title, summary, url, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                item,
            )

    payload = {
        "success": True,
        "reports_synced": reports_synced,
        "symbols_count": len(selected_symbols),
        "days_count": days_count,
        "kap_disclosures_seeded": len(kap_to_insert),
    }
    emit(build_envelope(command="dev.seed-db", data=payload), json_mode=json_mode)
