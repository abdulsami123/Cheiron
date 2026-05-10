"""Gradio frontend for the cheiron2 ClinicalTrials.gov visualizer.

Run:
    uv run python -m frontend.app

Talks to the FastAPI backend over HTTP — no shared imports with `app/`.
Override the backend URL with BACKEND_URL (default http://127.0.0.1:8000).

UX: one textbox. The user types a natural-language question; the backend
translates it to an Essie expression (LLM #1) and chooses a visualization
(LLM #2). NCT ids in the input route to the single-study branch.
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

import gradio as gr
import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT_S = float(os.getenv("FRONTEND_REQUEST_TIMEOUT_S", "120"))

NCT_RE = re.compile(r"\bNCT\d{8}\b")


def _empty_figure(message: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        annotations=[
            {
                "text": message,
                "x": 0.5,
                "y": 0.5,
                "xref": "paper",
                "yref": "paper",
                "showarrow": False,
                "font": {"size": 14},
            }
        ],
        xaxis={"visible": False},
        yaxis={"visible": False},
        margin={"l": 20, "r": 20, "t": 30, "b": 20},
    )
    return fig


def _render_visualization(viz: dict[str, Any]) -> go.Figure:
    vtype = viz.get("type")
    title = viz.get("title") or ""
    encoding = viz.get("encoding") or {}
    data = viz.get("data") or []

    if not data:
        return _empty_figure("No rows in visualization.data")

    df = pd.DataFrame(data)
    x = encoding.get("x")
    y = encoding.get("y")
    color = encoding.get("color")
    size = encoding.get("size")
    tooltip = encoding.get("tooltip") or []
    hover_cols = [c for c in tooltip if c in df.columns]

    try:
        if vtype == "bar_chart" and x and y and x in df.columns and y in df.columns:
            fig = px.bar(df, x=x, y=y, color=color if color in df.columns else None,
                         hover_data=hover_cols, title=title)
        elif vtype == "scatter_plot" and x and y and x in df.columns and y in df.columns:
            fig = px.scatter(df, x=x, y=y,
                             color=color if color in df.columns else None,
                             size=size if size in df.columns else None,
                             hover_data=hover_cols, title=title)
        elif vtype == "time_series" and x and y and x in df.columns and y in df.columns:
            df_sorted = df.sort_values(by=x) if x in df.columns else df
            fig = px.line(df_sorted, x=x, y=y,
                          color=color if color in df.columns else None,
                          markers=True, hover_data=hover_cols, title=title)
        elif vtype == "histogram" and (x in df.columns if x else False):
            fig = px.histogram(df, x=x, y=y if (y and y in df.columns) else None,
                               color=color if color in df.columns else None,
                               title=title)
        elif vtype == "network_graph":
            src, dst = _pick_edge_columns(df, encoding=encoding)
            if src and dst:
                fig = _render_network(df, src=src, dst=dst, title=title)
            else:
                fig = _empty_figure(
                    f"network_graph: couldn't infer edge columns from "
                    f"{list(df.columns)}; raw data shown below."
                )
        else:
            fig = _empty_figure(
                f"Could not render type={vtype!r} with encoding={encoding}; raw data shown below."
            )
    except Exception as e:
        fig = _empty_figure(f"Plot error: {e}")
    return fig


_SOURCE_ALIASES = ("source", "from", "src", "node1", "a", "u")
_TARGET_ALIASES = ("target", "to", "dst", "node2", "b", "v")


def _pick_edge_columns(
    df: pd.DataFrame, *, encoding: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Find the source/target columns for a network_graph.

    Order: encoding.x/y if both present in df → common alias names → first two
    string-typed columns as a last resort.
    """
    cols = set(df.columns)
    x, y = encoding.get("x"), encoding.get("y")
    if x and y and x in cols and y in cols and x != y:
        return x, y

    src = next((c for c in _SOURCE_ALIASES if c in cols), None)
    dst = next((c for c in _TARGET_ALIASES if c in cols and c != src), None)
    if src and dst:
        return src, dst

    string_cols = [c for c in df.columns if df[c].dtype == object]
    if len(string_cols) >= 2:
        return string_cols[0], string_cols[1]

    return None, None


def _render_network(df: pd.DataFrame, *, src: str, dst: str, title: str) -> go.Figure:
    nodes: dict[str, tuple[float, float]] = {}
    unique_nodes = list({*df[src].astype(str), *df[dst].astype(str)})
    n = max(len(unique_nodes), 1)
    for i, name in enumerate(unique_nodes):
        angle = 2 * math.pi * i / n
        nodes[name] = (math.cos(angle), math.sin(angle))

    edge_x: list[float | None] = []
    edge_y: list[float | None] = []
    for _, row in df.iterrows():
        a = str(row[src])
        b = str(row[dst])
        if a in nodes and b in nodes:
            edge_x.extend([nodes[a][0], nodes[b][0], None])
            edge_y.extend([nodes[a][1], nodes[b][1], None])

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=edge_x, y=edge_y, mode="lines",
                   line={"width": 1, "color": "#888"}, hoverinfo="none", showlegend=False)
    )
    fig.add_trace(
        go.Scatter(
            x=[p[0] for p in nodes.values()],
            y=[p[1] for p in nodes.values()],
            mode="markers+text",
            text=list(nodes.keys()),
            textposition="top center",
            marker={"size": 14},
            hoverinfo="text",
            showlegend=False,
        )
    )
    fig.update_layout(
        title=title,
        xaxis={"visible": False},
        yaxis={"visible": False},
        margin={"l": 20, "r": 20, "t": 40, "b": 20},
    )
    return fig


def _build_params(query: str) -> dict[str, str]:
    """Single-input → backend params. NCT-shaped tokens route to nct_id; the
    rest goes to `query` (the backend then runs Essie translation server-side)."""
    text = query.strip()
    nct_match = NCT_RE.search(text)
    params: dict[str, str] = {"query": text}
    if nct_match:
        params["nct_id"] = nct_match.group(0)
    return params


def submit(query: str) -> tuple[go.Figure, pd.DataFrame, dict[str, Any], dict[str, Any], str, str]:
    if not query or not query.strip():
        return (
            _empty_figure("Type a question first."),
            pd.DataFrame(),
            {},
            {"error": "query is required"},
            "",
            "",
        )

    params = _build_params(query)
    request_url = f"{BACKEND_URL}/trials/visualize?{httpx.QueryParams(params)}"

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_S) as c:
            r = c.get(f"{BACKEND_URL}/trials/visualize", params=params)
    except httpx.HTTPError as e:
        return (
            _empty_figure(f"Request failed: {e}"),
            pd.DataFrame(),
            {},
            {"error": str(e)},
            request_url,
            f"Network error: {e}",
        )

    status_line = f"HTTP {r.status_code}"

    try:
        body = r.json()
    except json.JSONDecodeError:
        return (
            _empty_figure(f"Non-JSON response (status {r.status_code})"),
            pd.DataFrame(),
            {},
            {"raw": r.text[:1000]},
            request_url,
            status_line,
        )

    if r.status_code != 200:
        return (
            _empty_figure(f"Backend returned {r.status_code}"),
            pd.DataFrame(),
            {},
            body,
            request_url,
            status_line,
        )

    viz = body.get("visualization") or {}
    metadata = body.get("metadata") or {}

    fig = _render_visualization(viz)
    data_df = pd.DataFrame(viz.get("data") or [])

    return fig, data_df, metadata, body, request_url, status_line


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Cheiron2 — Trials Visualizer") as demo:
        gr.Markdown(
            f"# Cheiron2 — ClinicalTrials.gov Visualizer\n"
            f"Backend: `{BACKEND_URL}`. Type a question and hit **Visualize**."
        )

        with gr.Row():
            with gr.Column(scale=1):
                query = gr.Textbox(
                    label="Your question",
                    placeholder=(
                        "e.g. enrollment over time in pediatric leukemia "
                        "chemotherapy trials"
                    ),
                    lines=3,
                )
                visualize_btn = gr.Button("Visualize", variant="primary")
                status = gr.Textbox(label="Status", interactive=False)
                request_url = gr.Textbox(label="Request URL", interactive=False)

            with gr.Column(scale=2):
                plot = gr.Plot(label="Visualization")
                with gr.Tab("Data"):
                    data_table = gr.Dataframe(label="visualization.data", wrap=True)
                with gr.Tab("Metadata"):
                    metadata_json = gr.JSON(label="metadata")
                with gr.Tab("Raw response"):
                    raw_json = gr.JSON(label="full backend response")

        outputs = [plot, data_table, metadata_json, raw_json, request_url, status]
        visualize_btn.click(submit, inputs=query, outputs=outputs)
        query.submit(submit, inputs=query, outputs=outputs)

    return demo


def main() -> None:
    demo = build_ui()
    demo.launch(
        server_name=os.getenv("FRONTEND_HOST", "127.0.0.1"),
        server_port=int(os.getenv("FRONTEND_PORT", "7860")),
        show_error=True,
    )


if __name__ == "__main__":
    main()
