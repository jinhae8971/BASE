"""Static HTML dashboard generator.

Reads run artifacts from `data/runs/<date>/*.json` and renders a single
self-contained HTML file. Deliberately avoids Streamlit/Dash/Flask so
there's zero server-side dependency -- the operator just opens the file
in a browser. Perfect for daily email attachments and airgapped review.
"""

from src.dashboard.generator import generate_dashboard, render_html

__all__ = ["generate_dashboard", "render_html"]
