import math
from datetime import time
from typing import Dict, List, Optional, Union
import plotly.graph_objects as go
from models.schema import Course, Schedule, TimeBlock

# Day of week mapping to X coordinate
DAY_MAP = {
    "M": 0,
    "T": 1,
    "W": 2,
    "R": 3,
    "F": 4,
    "S": 5,
    "U": 6,
}

DAY_LABELS = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}

# Curated palette of soft, modern pastel hex codes
PASTEL_COLORS = [
    "#A0C4FF",  # Soft baby blue
    "#BDB2FF",  # Soft periwinkle
    "#FFC6FF",  # Soft pink
    "#FDFFB6",  # Soft pale yellow
    "#CAFFBF",  # Soft mint green
    "#9BF6FF",  # Soft sky cyan
    "#FFD6A5",  # Soft peach
    "#FFADAD",  # Soft coral
    "#D8BBFF",  # Soft lavender
    "#BEE1E6",  # Soft ice blue
    "#FDE2E4",  # Soft blush
    "#DFCCF1",  # Soft lilac
    "#C5DEDD",  # Soft sage
    "#FFE5D9",  # Soft cream
]


def _get_border_color(fill_hex: str, factor: float = 0.65) -> str:
    """
    Computes a slightly darker accent tone of the given pastel hex color for the border.
    """
    clean = fill_hex.lstrip("#")
    if len(clean) == 6:
        try:
            r = int(clean[0:2], 16)
            g = int(clean[2:4], 16)
            b = int(clean[4:6], 16)
            r = max(0, min(255, int(r * factor)))
            g = max(0, min(255, int(g * factor)))
            b = max(0, min(255, int(b * factor)))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            pass
    return "#4B5563"


def time_to_decimal_hour(t: time) -> float:
    """
    Converts a datetime.time object into a decimal hour float (e.g. 09:30 -> 9.5).
    """
    return t.hour + t.minute / 60.0


def format_hour_label(hour_val: int) -> str:
    """
    Formats an integer hour (0-24) into a human-readable 12-hour AM/PM label.
    """
    period = "AM" if hour_val < 12 or hour_val == 24 else "PM"
    h12 = hour_val % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:00 {period}"


def get_course_color_map(
    courses: Union[List[Course], List[Schedule], Schedule, Course]
) -> Dict[str, str]:
    """
    Auto-assigns consistent pastel colors to each unique course ID found in
    the provided courses or schedules.
    """
    if isinstance(courses, (Course, Schedule)):
        items = [courses]
    elif isinstance(courses, list):
        items = courses
    else:
        items = list(courses)

    course_ids = set()
    for item in items:
        if isinstance(item, Course):
            course_ids.add(item.course_id)
        elif isinstance(item, Schedule):
            for sec in item.sections:
                course_ids.add(sec.course_id)
        elif hasattr(item, "course_id"):
            course_ids.add(getattr(item, "course_id"))
        elif hasattr(item, "sections"):
            for sec in getattr(item, "sections"):
                if hasattr(sec, "course_id"):
                    course_ids.add(getattr(sec, "course_id"))

    sorted_ids = sorted(list(course_ids))
    color_map: Dict[str, str] = {}
    for idx, cid in enumerate(sorted_ids):
        color_map[cid] = PASTEL_COLORS[idx % len(PASTEL_COLORS)]
    return color_map



def create_schedule_calendar(
    schedule: Schedule,
    course_colors: Optional[Dict[str, str]] = None,
) -> go.Figure:
    """
    Builds a weekly schedule calendar using coordinate-based Plotly shapes and scatter text traces.
    
    - X-axis: Days of the week (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, optional Sat/Sun).
    - Y-axis: Decimal hour, reversed so 8:00 AM (or earlier if needed) is at the top.
    - Each TimeBlock is rendered as a rounded rectangle shape with a pastel fill.
    - Text trace displays Course ID, Section ID, and Instructor in the center with detailed hover tooltips.
    - Automatically expands Y-axis if classes fall outside 8:00 AM - 8:00 PM.
    """
    if course_colors is None:
        course_colors = get_course_color_map([schedule])

    fig = go.Figure()

    min_hour = 8.0
    max_hour = 20.0
    max_day = 4  # Default: Monday to Friday

    all_timeblocks: List[tuple] = []
    arranged_sections: List[str] = []

    for sec in schedule.sections:
        if not sec.meeting_times:
            arranged_sections.append(f"{sec.course_id} (Sec {sec.section_id})")
            continue

        for tb in sec.meeting_times:
            day_idx = DAY_MAP.get(tb.day)
            if day_idx is None:
                continue

            if day_idx > max_day:
                max_day = day_idx

            start_dec = time_to_decimal_hour(tb.start_time)
            end_dec = time_to_decimal_hour(tb.end_time)

            if start_dec < min_hour:
                min_hour = float(math.floor(start_dec))
            if end_dec > max_hour:
                max_hour = float(math.ceil(end_dec))

            all_timeblocks.append((sec, tb, day_idx, start_dec, end_dec))

    min_hour = max(0.0, min_hour)
    max_hour = min(24.0, max_hour)

    scatter_x: List[float] = []
    scatter_y: List[float] = []
    scatter_text: List[str] = []
    scatter_hover: List[str] = []

    col_width = 0.84
    for sec, tb, day_idx, y0, y1 in all_timeblocks:
        if y1 <= y0:
            continue

        x0 = day_idx - col_width / 2.0
        x1 = day_idx + col_width / 2.0

        fill_color = course_colors.get(sec.course_id, "#BEE1E6")
        border_color = _get_border_color(fill_color)

        rx = 0.05
        ry = min(0.10, abs(y1 - y0) * 0.15)
        path = (
            f"M {x0 + rx} {y0} "
            f"L {x1 - rx} {y0} "
            f"Q {x1} {y0} {x1} {y0 + ry} "
            f"L {x1} {y1 - ry} "
            f"Q {x1} {y1} {x1 - rx} {y1} "
            f"L {x0 + rx} {y1} "
            f"Q {x0} {y1} {x0} {y1 - ry} "
            f"L {x0} {y0 + ry} "
            f"Q {x0} {y0} {x0 + rx} {y0} Z"
        )

        fig.add_shape(
            type="path",
            path=path,
            fillcolor=fill_color,
            line=dict(color=border_color, width=1.5),
            opacity=0.92,
            layer="below",
        )

        center_x = float(day_idx)
        center_y = (y0 + y1) / 2.0

        start_str = tb.start_time.strftime("%I:%M %p").lstrip("0")
        end_str = tb.end_time.strftime("%I:%M %p").lstrip("0")
        duration_mins = int(round((y1 - y0) * 60))

        header = f"<b>{sec.course_id} - {sec.section_id}</b>"
        time_label = f"<span style='font-size:10px;'>{start_str} - {end_str}</span>"

        if duration_mins >= 80:
            instructor_display = sec.instructor if sec.instructor != "Arranged" else ""
            inst_line = f"<br><span style='font-size:9px; color:#374151;'>{instructor_display}</span>" if instructor_display else ""
            label = f"{header}<br>{time_label}{inst_line}"
        else:
            label = f"{header}<br>{time_label}"

        hover = (
            f"<b>{sec.course_id}</b><br>"
            f"<b>Section:</b> {sec.section_id}<br>"
            f"<b>Instructor:</b> {sec.instructor}<br>"
            f"<b>Day:</b> {DAY_LABELS[day_idx]}<br>"
            f"<b>Time:</b> {start_str} – {end_str} ({duration_mins} min)"
        )

        scatter_x.append(center_x)
        scatter_y.append(center_y)
        scatter_text.append(label)
        scatter_hover.append(hover)

    # Layer scatter text trace
    if scatter_x:
        fig.add_trace(
            go.Scatter(
                x=scatter_x,
                y=scatter_y,
                text=scatter_text,
                hovertext=scatter_hover,
                hoverinfo="text",
                mode="text",
                textposition="middle center",
                textfont=dict(family="Arial, sans-serif", size=11, color="#1E293B"),
                showlegend=False,
            )
        )
    else:
        # Dummy anchor point to maintain coordinate system on empty schedules
        fig.add_trace(
            go.Scatter(
                x=[0],
                y=[min_hour],
                mode="markers",
                marker=dict(opacity=0, size=0.1),
                hoverinfo="none",
                showlegend=False,
            )
        )

    # X-axis configuration
    x_tickvals = list(range(max_day + 1))
    x_ticktext = [DAY_LABELS[i] for i in x_tickvals]

    # Y-axis configuration
    y_tickvals = list(range(int(min_hour), int(max_hour) + 1))
    y_ticktext = [format_hour_label(h) for h in y_tickvals]

    fig.update_layout(
        margin=dict(l=65, r=25, t=50, b=30),
        plot_bgcolor="#FAFAFA",
        paper_bgcolor="#FFFFFF",
        height=650,
        hoverlabel=dict(
            bgcolor="#1F2937",
            font_size=12,
            font_family="Arial, sans-serif",
            font_color="#FFFFFF",
        ),
        xaxis=dict(
            tickmode="array",
            tickvals=x_tickvals,
            ticktext=x_ticktext,
            range=[-0.5, max_day + 0.5],
            showgrid=True,
            gridcolor="#E5E7EB",
            zeroline=False,
            fixedrange=True,
            side="top",
            tickfont=dict(size=12, family="Arial, sans-serif", color="#1F2937"),
        ),
        yaxis=dict(
            tickmode="array",
            tickvals=y_tickvals,
            ticktext=y_ticktext,
            range=[max_hour, min_hour],
            autorange="reversed",
            showgrid=True,
            gridcolor="#E5E7EB",
            zeroline=False,
            fixedrange=True,
            tickfont=dict(size=11, family="Arial, sans-serif", color="#4B5563"),
        ),
    )

    # Add footnote for online / arranged courses if any
    if arranged_sections:
        fig.add_annotation(
            text=f"<b>Online / Arranged (no scheduled meeting times):</b> {', '.join(arranged_sections)}",
            xref="paper",
            yref="paper",
            x=0.5,
            y=-0.05,
            showarrow=False,
            font=dict(size=11, color="#64748B"),
        )

    return fig

