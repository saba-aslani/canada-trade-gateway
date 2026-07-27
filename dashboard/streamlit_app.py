"""
Canada Trade Gateway Live — public operations dashboard.

Reads the dbt marts in Neon Postgres and renders the current state of Canada's
Pacific ocean gateway (Vancouver, Prince Rupert) and the commercial land border.

Design note: the visual language borrows from hydrographic charts — chart-paper
ground, sounding-blue linework, italic serif for water annotations — because
that is the working vernacular of the ports this dashboard watches.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pydeck as pdk
import streamlit as st
from sqlalchemy import create_engine, text

MARTS = "analytics_marts"
STAGING = "analytics_staging"

PAPER = "#E9EEE9"
INK = "#12313F"
SOUNDING = "#4A7C8C"
FAIRWAY = "#C6D9DE"
AMBER = "#C4761E"
CORAL = "#A8352C"

st.set_page_config(
    page_title="Canada Trade Gateway Live",
    page_icon="⚓",
    layout="wide",
)


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------

@st.cache_resource
def get_engine():
    """One pooled engine per server process."""
    return create_engine(st.secrets["DATABASE_URL"], pool_pre_ping=True)


@st.cache_data(ttl=300, show_spinner=False)
def run_query(sql: str) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn)


def load_latest_congestion() -> pd.DataFrame:
    return run_query(f"""
        with latest as (
            select region, max(snapshot_ts) as snapshot_ts
            from {MARTS}.fct_port_congestion
            group by region
        )
        select c.*
        from {MARTS}.fct_port_congestion c
        join latest l
          on c.region = l.region
         and c.snapshot_ts = l.snapshot_ts
    """)


def load_congestion_trend(days: int) -> pd.DataFrame:
    return run_query(f"""
        select snapshot_ts, region, vessels_total, vessels_stationary,
               stationary_share
        from {MARTS}.fct_port_congestion
        where snapshot_ts > now() - interval '{days} days'
        order by snapshot_ts
    """)


def load_vessel_positions() -> pd.DataFrame:
    return run_query(f"""
        with latest as (
            select region, max(snapshot_ts) as snapshot_ts
            from {STAGING}.stg_vessel_positions
            group by region
        )
        select p.mmsi, p.ship_name, p.region, p.latitude, p.longitude,
               p.speed_over_ground_kn, p.is_stationary, p.nav_status_desc,
               coalesce(v.vessel_category, 'unknown') as vessel_category,
               v.length_m, v.destination
        from {STAGING}.stg_vessel_positions p
        join latest l
          on p.region = l.region
         and p.snapshot_ts = l.snapshot_ts
        left join {MARTS}.dim_vessels v
          on p.mmsi = v.mmsi
    """)


def load_latest_border_waits() -> pd.DataFrame:
    return run_query(f"""
        select crossing_name, canada_province, region_group, traffic_type,
               delay_minutes, delay_status, congestion_band, fetched_at
        from {MARTS}.fct_border_waits
        where fetched_at = (select max(fetched_at) from {MARTS}.fct_border_waits)
          and direction = 'canada'
          and delay_status = 'reported'
    """)


def load_border_trend(days: int) -> pd.DataFrame:
    return run_query(f"""
        select date_trunc('hour', fetched_at) as hour_utc,
               crossing_name,
               avg(delay_minutes) as avg_delay_minutes
        from {MARTS}.fct_border_waits
        where traffic_type = 'commercial'
          and direction = 'canada'
          and delay_status = 'reported'
          and fetched_at > now() - interval '{days} days'
        group by 1, 2
        order by 1
    """)


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------

def inject_styles() -> None:
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Archivo+Narrow:wght@500;700&family=IBM+Plex+Mono:wght@400;600&family=Spectral:ital,wght@1,400&display=swap');

        .stApp {{
            background-color: {PAPER};
            background-image:
                linear-gradient(rgba(74,124,140,.07) 1px, transparent 1px),
                linear-gradient(90deg, rgba(74,124,140,.07) 1px, transparent 1px);
            background-size: 32px 32px;
        }}
        html, body, [class*="css"] {{ color: {INK}; }}

        .masthead {{
            border-top: 3px solid {INK};
            border-bottom: 1px solid {SOUNDING};
            padding: .9rem 0 .7rem;
            margin-bottom: 1.4rem;
        }}
        .masthead h1 {{
            font-family: 'Archivo Narrow', sans-serif;
            font-weight: 700;
            font-size: 2.3rem;
            letter-spacing: .02em;
            text-transform: uppercase;
            margin: 0;
            color: {INK};
        }}
        .masthead p {{
            font-family: 'Spectral', serif;
            font-style: italic;
            font-size: 1rem;
            color: {SOUNDING};
            margin: .25rem 0 0;
        }}
        .stamp {{
            font-family: 'IBM Plex Mono', monospace;
            font-size: .72rem;
            letter-spacing: .08em;
            text-transform: uppercase;
            color: {SOUNDING};
        }}

        .eyebrow {{
            font-family: 'Archivo Narrow', sans-serif;
            font-weight: 700;
            font-size: .8rem;
            letter-spacing: .16em;
            text-transform: uppercase;
            color: {SOUNDING};
            border-bottom: 1px solid rgba(74,124,140,.35);
            padding-bottom: .35rem;
            margin: 1.6rem 0 .9rem;
        }}

        /* Soundings: the signature treatment. Values are set like depth
           readings on a chart — figure first, unit trailing and small. */
        .sounding {{
            border-left: 2px solid {SOUNDING};
            padding: .1rem 0 .1rem .7rem;
            margin-bottom: .2rem;
        }}
        .sounding .label {{
            font-family: 'Archivo Narrow', sans-serif;
            font-size: .78rem;
            letter-spacing: .12em;
            text-transform: uppercase;
            color: {SOUNDING};
            display: block;
        }}
        .sounding .value {{
            font-family: 'IBM Plex Mono', monospace;
            font-weight: 600;
            font-size: 2.1rem;
            line-height: 1.15;
            color: {INK};
        }}
        .sounding .unit {{
            font-family: 'IBM Plex Mono', monospace;
            font-size: .8rem;
            color: {SOUNDING};
            margin-left: .25rem;
        }}
        .sounding .value.warn {{ color: {AMBER}; }}
        .sounding .value.alert {{ color: {CORAL}; }}

        .note {{
            font-family: 'Spectral', serif;
            font-style: italic;
            color: {SOUNDING};
            font-size: .93rem;
        }}
        [data-testid="stDataFrame"] {{ border: 1px solid rgba(74,124,140,.35); }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def sounding(label: str, value: str, unit: str = "", tone: str = "") -> str:
    unit_html = f'<span class="unit">{unit}</span>' if unit else ""
    return (
        f'<div class="sounding"><span class="label">{label}</span>'
        f'<span class="value {tone}">{value}</span>{unit_html}</div>'
    )


def eyebrow(text_: str) -> None:
    st.markdown(f'<div class="eyebrow">{text_}</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------

def render_masthead() -> None:
    now = datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    st.markdown(
        f"""
        <div class="masthead">
          <h1>Canada Trade Gateway Live</h1>
          <p>Vessel dwell at the Pacific ports and commercial delay at the land border</p>
          <div class="stamp">Rendered {now} · sources: AISstream, CBSA open data</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_ports(days: int) -> None:
    eyebrow("Pacific gateway — vessels on station")

    congestion = load_latest_congestion()
    if congestion.empty:
        st.markdown(
            '<p class="note">No vessel snapshots yet. The collector writes a '
            'new reading every 15 minutes.</p>',
            unsafe_allow_html=True,
        )
        return

    cols = st.columns(len(congestion) * 2)
    for i, row in enumerate(congestion.itertuples()):
        share = float(row.stationary_share or 0)
        tone = "alert" if share >= 0.7 else "warn" if share >= 0.5 else ""
        place = row.region.replace("_", " ").title()
        cols[i * 2].markdown(
            sounding(f"{place} · vessels", f"{int(row.vessels_total)}"),
            unsafe_allow_html=True,
        )
        cols[i * 2 + 1].markdown(
            sounding(f"{place} · holding", f"{share * 100:.0f}", "%", tone),
            unsafe_allow_html=True,
        )

    reading_time = pd.to_datetime(congestion["snapshot_ts"].max())
    st.markdown(
        f'<p class="note">Holding is the share of tracked vessels not making '
        f'way — the practical early signal of berth and anchorage pressure. '
        f'Latest reading {reading_time:%d %b %H:%M} UTC.</p>',
        unsafe_allow_html=True,
    )

    positions = load_vessel_positions()
    if not positions.empty:
        positions = positions.copy()
        positions["radius"] = positions["length_m"].fillna(120).clip(60, 400) * 1.6
        positions["colour"] = positions.apply(
            lambda r: [168, 53, 44, 200] if r["is_stationary"]
            else [74, 124, 140, 170],
            axis=1,
        )
        positions["status"] = positions["is_stationary"].map(
            {True: "holding", False: "under way"}
        )
        positions["ship_name"] = positions["ship_name"].fillna("unidentified")
        positions["speed"] = positions["speed_over_ground_kn"].fillna(0).round(1)

        layer = pdk.Layer(
            "ScatterplotLayer",
            data=positions,
            get_position="[longitude, latitude]",
            get_fill_color="colour",
            get_radius="radius",
            pickable=True,
            stroked=True,
            get_line_color=[18, 49, 63, 120],
            line_width_min_pixels=1,
        )
        view = pdk.ViewState(
            latitude=float(positions["latitude"].mean()),
            longitude=float(positions["longitude"].mean()),
            zoom=6.2,
        )
        st.pydeck_chart(
            pdk.Deck(
                layers=[layer],
                initial_view_state=view,
                map_style="light",
                tooltip={
                    "html": "<b>{ship_name}</b><br/>{vessel_category} · {status}"
                            "<br/>{speed} kn",
                    "style": {"backgroundColor": INK, "color": "white"},
                },
            ),
            height=430,
        )
        st.markdown(
            '<p class="note">Red marks a vessel holding position; blue is '
            'under way. Marker size follows hull length where AIS static data '
            'has been received.</p>',
            unsafe_allow_html=True,
        )

    trend = load_congestion_trend(days)
    if trend["snapshot_ts"].nunique() >= 4:
        wide = trend.pivot_table(
            index="snapshot_ts", columns="region",
            values="stationary_share", aggfunc="mean",
        ).sort_index()
        wide.columns = [c.replace("_", " ").title() for c in wide.columns]
        eyebrow("Share of vessels holding — trend")
        st.line_chart(
            wide, height=240,
            color=[CORAL, SOUNDING][: len(wide.columns)],
        )
    else:
        st.markdown(
            '<p class="note">The holding trend appears once a few hours of '
            'readings have accumulated.</p>',
            unsafe_allow_html=True,
        )


def render_border(days: int) -> None:
    eyebrow("Land border — commercial delay, Canada bound")

    waits = load_latest_border_waits()
    if waits.empty:
        st.markdown(
            '<p class="note">No border readings yet.</p>',
            unsafe_allow_html=True,
        )
        return

    commercial = waits[waits["traffic_type"] == "commercial"]
    if commercial.empty:
        st.markdown(
            '<p class="note">CBSA is not reporting commercial lanes right now.</p>',
            unsafe_allow_html=True,
        )
        return

    worst = commercial.sort_values("delay_minutes", ascending=False).iloc[0]
    delayed = int((commercial["delay_minutes"] > 0).sum())
    avg_delay = commercial["delay_minutes"].mean()

    c1, c2, c3 = st.columns(3)
    c1.markdown(
        sounding("Crossings reporting", f"{len(commercial)}"),
        unsafe_allow_html=True,
    )
    c2.markdown(
        sounding("With delay", f"{delayed}",
                 tone="warn" if delayed else ""),
        unsafe_allow_html=True,
    )
    tone = "alert" if worst.delay_minutes >= 30 else "warn" if worst.delay_minutes > 0 else ""
    c3.markdown(
        sounding("Longest wait", f"{int(worst.delay_minutes)}", "min", tone),
        unsafe_allow_html=True,
    )

    avg_text = (
        "under a minute" if avg_delay < 1
        else f"{avg_delay:.0f} minute" + ("" if round(avg_delay) == 1 else "s")
    )
    lead = (
        f"Every reporting commercial lane is clear. Average wait is {avg_text}."
        if worst.delay_minutes == 0 else
        f"Slowest lane right now is {worst.crossing_name} "
        f"({worst.canada_province}) at {int(worst.delay_minutes)} minutes. "
        f"Average across all reporting crossings is {avg_text}."
    )
    st.markdown(f'<p class="note">{lead}</p>', unsafe_allow_html=True)

    commercial = commercial.assign(
        delay_minutes=pd.to_numeric(commercial["delay_minutes"], errors="coerce")
    )
    ranked = (
        commercial.sort_values(
            ["delay_minutes", "crossing_name"], ascending=[False, True]
        )
        .loc[:, ["crossing_name", "canada_province", "delay_minutes", "congestion_band"]]
        .rename(columns={
            "crossing_name": "Crossing",
            "canada_province": "Province",
            "delay_minutes": "Delay (min)",
            "congestion_band": "Band",
        })
    )
    st.dataframe(ranked, hide_index=True, use_container_width=True)

    trend = load_border_trend(days)
    if len(trend) > 2:
        busiest = (
            trend.groupby("crossing_name")["avg_delay_minutes"]
            .mean().sort_values(ascending=False).head(4).index
        )
        wide = (
            trend[trend["crossing_name"].isin(busiest)]
            .pivot_table(index="hour_utc", columns="crossing_name",
                         values="avg_delay_minutes", aggfunc="mean")
        )
        eyebrow("Delay by hour — busiest commercial crossings")
        st.line_chart(wide, height=260)


def render_footer() -> None:
    st.markdown(
        '<p class="stamp">Vessel positions from AISstream · border waits from '
        'CBSA under the Open Government Licence — Canada. Readings are '
        'collected every 15 minutes and modelled with dbt; treat them as '
        'indicative, not operational guidance.</p>',
        unsafe_allow_html=True,
    )


def main() -> None:
    inject_styles()
    render_masthead()

    with st.sidebar:
        st.markdown('<div class="eyebrow">View</div>', unsafe_allow_html=True)
        days = st.slider("History window (days)", 1, 14, 7)
        if st.button("Refresh readings"):
            st.cache_data.clear()

    try:
        render_ports(days)
        render_border(days)
    except Exception as exc:  # surfaced rather than swallowed
        st.error(
            "Could not reach the warehouse. Check the DATABASE_URL secret and "
            f"that the dbt marts have been built.\n\n{exc}"
        )
        return

    render_footer()


if __name__ == "__main__":
    main()
