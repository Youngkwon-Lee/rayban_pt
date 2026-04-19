"""auto_chart.py — re-exports mask_face, chart_generator for app.py compatibility."""
from .mask_face import mask_faces
from .chart_generator import generate_chart, save_chart

__all__ = ["mask_faces", "generate_chart", "save_chart"]
