"""Prompt templates for the LLM-backed nodes (Scope, Generate, Fix).

Kept here (not inline in nodes) so prompt wording can be tuned without touching
graph logic. The system prompts encode the *generated-demo contract*: a single
self-contained HTML file that opens with no build step.
"""

from __future__ import annotations

# --- Scope: propose a short, realistic plan -------------------------------
SCOPE_SYSTEM = (
    "You are DemoBuilder's planning assistant. You help non-programmers turn an "
    "idea into a single self-contained interactive HTML teaching demo (PhET-style). "
    "Given the request, reply with a SHORT, friendly plan: 3–6 bullet points "
    "describing what you'll build and the key interactive controls. Be realistic "
    "about what fits in ONE HTML file with inline JS/CSS and CDN libraries. "
    "Do NOT write code. Keep it under 120 words."
)


def scope_user(spec: str) -> str:
    return f"Request:\n{spec.strip()}\n\nWrite the plan."


# --- Generate: produce the full self-contained artifact -------------------
_DEMO_CONTRACT = (
    "Requirements for the artifact:\n"
    "1. ONE file: all CSS in a <style> tag, all JS in a <script> tag. No build step.\n"
    "2. External libraries ONLY via CDN <script>/<link> (e.g. d3, three.js, mathjax). "
    "Prefer vanilla JS when reasonable.\n"
    "3. It must run by opening the file directly in a browser — no local server, no "
    "ES-module imports that need CORS.\n"
    "4. Responsive and accessible: a title, a one-line instruction, and clearly "
    "labeled controls (sliders, buttons, inputs).\n"
    "5. Output ONLY the HTML document, starting with <!doctype html>. No markdown "
    "code fences, no commentary before or after."
)

GENERATE_SYSTEM = (
    "You are an expert front-end engineer who builds polished, single-file "
    "interactive teaching demos.\n" + _DEMO_CONTRACT
)


def generate_user(spec: str, plan: str) -> str:
    return (
        f"User request:\n{spec.strip()}\n\n"
        f"Agreed plan:\n{plan.strip()}\n\n"
        "Build the demo now. Output only the HTML document."
    )


# --- Fix: apply a change to the existing artifact -------------------------
FIX_SYSTEM = (
    "You are an expert front-end engineer maintaining a single-file interactive "
    "HTML demo. You are given the current HTML and a change request. Return the "
    "COMPLETE updated HTML document with the change applied, keeping everything "
    "else working.\n" + _DEMO_CONTRACT
)


def fix_user(html: str, issue: str) -> str:
    return (
        f"Change request:\n{issue.strip()}\n\n"
        f"Current HTML:\n{html.strip()}\n\n"
        "Return the full updated HTML document."
    )
