"""
Builds docs/Deferral_Twin_Complete_Guide.pdf: how the whole project works, from scratch to the end,
written for the final review. Run with any Python that has reportlab:

    python3 docs/make_guide.py
"""
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

HERE = Path(__file__).resolve().parent
OUT = HERE / "Deferral_Twin_Complete_Guide.pdf"
SUP = Path("/System/Library/Fonts/Supplemental")

# ---------- fonts (TTF, so arrows and bullets render) ----------
def font(name, *files):
    for f in files:
        if Path(f).exists():
            pdfmetrics.registerFont(TTFont(name, f))
            return name
    return {"Body": "Helvetica", "Bold": "Helvetica-Bold", "Ital": "Helvetica-Oblique", "Mono": "Courier", "BoldItal": "Helvetica-BoldOblique"}[name]

BODY = font("Body", SUP / "Arial.ttf", SUP / "Arial Unicode.ttf")
BOLD = font("Bold", SUP / "Arial Bold.ttf")
ITAL = font("Ital", SUP / "Arial Italic.ttf")
MONO = font("Mono", SUP / "Courier New.ttf", SUP / "Andale Mono.ttf")
BI = font("BoldItal", SUP / "Arial Bold Italic.ttf") if (SUP / "Arial Bold Italic.ttf").exists() else BOLD
from reportlab.pdfbase.pdfmetrics import registerFontFamily
registerFontFamily(BODY, normal=BODY, bold=BOLD, italic=ITAL, boldItalic=BI)      # so <b> and <i> work in paragraphs

INK, MUTED = colors.HexColor("#111820"), colors.HexColor("#56606b")
ACCENT = colors.HexColor("#2c46c4")
CONF, INF, DEF = colors.HexColor("#0b7d60"), colors.HexColor("#a35f00"), colors.HexColor("#6e37c8")
SOFT_BG, LINE = colors.HexColor("#f2f4f8"), colors.HexColor("#d8dfe7")

S = {
    "title": ParagraphStyle("title", fontName=BOLD, fontSize=26, leading=31, textColor=INK, spaceAfter=10),
    "sub": ParagraphStyle("sub", fontName=BODY, fontSize=13, leading=18, textColor=MUTED),
    "h1": ParagraphStyle("h1", fontName=BOLD, fontSize=18, leading=23, textColor=ACCENT, spaceBefore=4, spaceAfter=8),
    "h2": ParagraphStyle("h2", fontName=BOLD, fontSize=13, leading=17, textColor=INK, spaceBefore=10, spaceAfter=4),
    "p": ParagraphStyle("p", fontName=BODY, fontSize=10.3, leading=15, textColor=INK, spaceAfter=6),
    "small": ParagraphStyle("small", fontName=BODY, fontSize=8.8, leading=12, textColor=MUTED, spaceAfter=4),
    "li": ParagraphStyle("li", fontName=BODY, fontSize=10.3, leading=14.5, textColor=INK, leftIndent=14, bulletIndent=3, spaceAfter=2),
    "code": ParagraphStyle("code", fontName=MONO, fontSize=8.8, leading=12, textColor=INK, backColor=SOFT_BG,
                           borderPadding=6, leftIndent=6, rightIndent=6, spaceBefore=4, spaceAfter=8),
    "box": ParagraphStyle("box", fontName=BODY, fontSize=10.3, leading=15, textColor=INK),
    "cell": ParagraphStyle("cell", fontName=BODY, fontSize=9, leading=12, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName=BOLD, fontSize=9, leading=12, textColor=INK),
    "q": ParagraphStyle("q", fontName=BOLD, fontSize=10.3, leading=14, textColor=ACCENT, spaceBefore=6, spaceAfter=2),
    "center": ParagraphStyle("center", fontName=BODY, fontSize=9, leading=12, textColor=MUTED, alignment=TA_CENTER),
}

story = []
P = lambda t, st="p": story.append(Paragraph(t, S[st]))
H1 = lambda t: (story.append(PageBreak()), story.append(Paragraph(t, S["h1"])))
H2 = lambda t: story.append(Paragraph(t, S["h2"]))
SP = lambda h=6: story.append(Spacer(1, h))


def UL(items):
    for t in items:
        story.append(Paragraph(t, S["li"], bulletText="•"))
    SP(4)


def CODE(t):
    story.append(Paragraph(t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>").replace("  ", "&nbsp;&nbsp;"), S["code"]))


def BOX(t, color=ACCENT):
    tb = Table([[Paragraph(t, S["box"])]], colWidths=[170 * mm])
    tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT_BG), ("LINEBEFORE", (0, 0), (0, -1), 3, color),
                            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story.append(tb)
    SP(8)


def TABLE(rows, widths, head=True):
    data = [[Paragraph(str(c), S["cellb"] if (head and i == 0) else S["cell"]) for c in r] for i, r in enumerate(rows)]
    tb = Table(data, colWidths=[w * mm for w in widths], repeatRows=1 if head else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
          ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if head:
        st.append(("BACKGROUND", (0, 0), (-1, 0), SOFT_BG))
    tb.setStyle(TableStyle(st))
    story.append(tb)
    SP(8)


# ---------- diagrams ----------
def arrow(d, x1, y1, x2, y2, c=MUTED):
    d.add(Line(x1, y1, x2, y2, strokeColor=c, strokeWidth=1.2))
    import math
    a = math.atan2(y2 - y1, x2 - x1)
    p = [x2, y2, x2 - 7 * math.cos(a - 0.4), y2 - 7 * math.sin(a - 0.4), x2 - 7 * math.cos(a + 0.4), y2 - 7 * math.sin(a + 0.4)]
    d.add(Polygon(p, fillColor=c, strokeColor=c))


def box(d, x, y, w, h, title, lines=(), fill=colors.white, stroke=ACCENT):
    d.add(Rect(x, y, w, h, rx=6, ry=6, fillColor=fill, strokeColor=stroke, strokeWidth=1.1))
    d.add(String(x + w / 2, y + h - 14, title, fontName=BOLD, fontSize=9.5, fillColor=INK, textAnchor="middle"))
    for i, l in enumerate(lines):
        d.add(String(x + w / 2, y + h - 27 - i * 11, l, fontName=BODY, fontSize=7.6, fillColor=MUTED, textAnchor="middle"))


def architecture():
    d = Drawing(480, 300)
    box(d, 10, 225, 140, 64, "The app window", ["pywebview (macOS WebKit)", "HTML / CSS / JavaScript", "app/static/app.js"], SOFT_BG)
    box(d, 175, 225, 140, 64, "Local server", ["FastAPI on 127.0.0.1", "app/server.py", "only this Mac can reach it"], SOFT_BG)
    box(d, 340, 225, 130, 64, "Google Gemini", ["optional Online mode", "key in macOS Keychain", "app/online.py"], colors.HexColor("#fff7e8"), INF)
    arrow(d, 150, 257, 175, 257); arrow(d, 175, 249, 150, 249)
    arrow(d, 315, 257, 340, 257, INF); arrow(d, 340, 249, 315, 249, INF)
    box(d, 10, 120, 145, 82, "Twin core (memory)", ["your answers as facts", "all-mpnet-base-v2 embeddings", "FAISS index (cosine)", "src/rag_pipeline.py"])
    box(d, 168, 120, 145, 82, "Deferral gate", ["raw cosine confidence", "< 0.15  -> I don't know", "0.15-0.35 -> labelled guess", ">= 0.35 -> answers", "src/deferral.py"])
    box(d, 326, 120, 145, 82, "Language model", ["Phi-3-mini, 4-bit", "Apple MLX (GPU)", "frozen: never trained", "src/llm_backend.py"])
    arrow(d, 245, 225, 245, 202)
    arrow(d, 155, 160, 168, 160); arrow(d, 313, 160, 326, 160)
    box(d, 10, 12, 145, 82, "Voice", ["XTTS-v2 voice cloning", "from your 45 s clip", "English and Hindi", "src/multimodal.py"], colors.HexColor("#eefaf5"), CONF)
    box(d, 168, 12, 145, 82, "Avatar video", ["Wav2Lip lip-sync", "+ mouth-only blend", "from one photo", "src/multimodal.py"], colors.HexColor("#eefaf5"), CONF)
    box(d, 326, 12, 145, 82, "Talk mode", ["Whisper-small", "speech -> text", "English / Hindi / Telugu", "app/asr.py"], colors.HexColor("#eefaf5"), CONF)
    arrow(d, 205, 225, 82, 94, CONF); arrow(d, 245, 120, 240, 94, CONF); arrow(d, 285, 225, 398, 94, CONF)
    return d


def pipeline():
    steps = [("1  Question arrives", "typed or spoken (Whisper)"), ("2  Language + follow-up", "Telugu/Hindi -> English; 'why?' -> full question"),
             ("3  Special cases", "clock, 'who are you', greetings -> local template"), ("4  Expert check (online)", "is it about your field? -> Gemini expert answer"),
             ("5  Retrieve", "embed question, FAISS top-8 of your facts"), ("6  Confidence", "best RAW cosine similarity (0 to 1)"),
             ("7  Gate", "< 0.15 refuse | 0.15-0.35 guess | >= 0.35 answer"), ("8  Generate", "prompt = persona + your answers -> Phi-3 or Gemini"),
             ("9  Clean + reply", "remove AI phrases; translate back; show evidence"), ("10 Voice / video", "optional: XTTS voice, Wav2Lip avatar")]
    d = Drawing(480, 36 * len(steps) + 4)
    for i, (a, b) in enumerate(steps):
        y = d.height - (i + 1) * 36
        col = DEF if i == 6 else ACCENT
        d.add(Rect(10, y + 4, 460, 28, rx=5, ry=5, fillColor=SOFT_BG if i != 6 else colors.HexColor("#f4effc"), strokeColor=col, strokeWidth=1))
        d.add(String(20, y + 14, a, fontName=BOLD, fontSize=9.5, fillColor=INK))
        d.add(String(190, y + 14, b, fontName=BODY, fontSize=8.8, fillColor=MUTED))
        if i < len(steps) - 1:
            arrow(d, 240, y + 4, 240, y - 0, MUTED)
    return d


def gate_bar():
    d = Drawing(480, 70)
    W = 440
    x = lambda v: 20 + W * v / 0.6
    d.add(Rect(x(0), 30, x(0.15) - x(0), 16, fillColor=colors.HexColor("#e9dffb"), strokeColor=None))
    d.add(Rect(x(0.15), 30, x(0.35) - x(0.15), 16, fillColor=colors.HexColor("#fbefd9"), strokeColor=None))
    d.add(Rect(x(0.35), 30, x(0.6) - x(0.35), 16, fillColor=colors.HexColor("#dcf2ea"), strokeColor=None))
    for v, t in ((0, "0"), (0.15, "0.15"), (0.35, "0.35"), (0.6, "0.6+")):
        d.add(String(x(v), 18, t, fontName=BODY, fontSize=8, fillColor=MUTED, textAnchor="middle"))
    d.add(String((x(0) + x(0.15)) / 2, 35, "I don't know", fontName=BOLD, fontSize=8.5, fillColor=DEF, textAnchor="middle"))
    d.add(String((x(0.15) + x(0.35)) / 2, 35, "labelled guess", fontName=BOLD, fontSize=8.5, fillColor=INF, textAnchor="middle"))
    d.add(String((x(0.35) + x(0.6)) / 2, 35, "answers from what you said", fontName=BOLD, fontSize=8.5, fillColor=CONF, textAnchor="middle"))
    d.add(String(240, 55, "confidence = best raw cosine similarity between the question and your facts", fontName=BODY, fontSize=8.5, fillColor=INK, textAnchor="middle"))
    return d


def footer(c, doc):
    c.saveState()
    c.setFont(BODY, 8)
    c.setFillColor(MUTED)
    c.drawString(20 * mm, 12 * mm, "Deferral-Aware Digital Twin · complete guide")
    c.drawRightString(190 * mm, 12 * mm, f"page {doc.page}")
    c.restoreState()


# =====================================================================
# CONTENT
# =====================================================================
P("Deferral-Aware Digital Twin", "title")
P("The complete guide: what it is, how every part works from the first click to the final answer, "
  "how it was evaluated, and how to explain it in your review.", "sub")
SP(16)
BOX("<b>How to use this guide.</b> Part A explains the idea. Part B follows the app from installation to the first answer. "
    "Part C opens up the backend: memory, the deferral gate, the language model, learning, Online and Expert mode, languages, "
    "voice, lip-sync and the character. Part D covers the results. Part E is a file map, the tech stack, limitations, "
    "and the questions examiners are likely to ask, with answers.")
TABLE([["Part", "Sections"],
       ["A · The idea", "1 The problem · 2 The solution in one page · 3 Big picture"],
       ["B · The journey", "4 Install and setup · 5 Consent and interview · 6 Building the twin"],
       ["C · The backend", "7 Answering a question · 8 The deferral gate · 9 Prompt and persona · 10 Learning without training · "
        "11 Online mode · 12 Expert mode · 13 Languages and chat memory · 14 Talk, voice and avatar · 15 Landing character · "
        "16 The extra features"],
       ["D · Evaluation", "17 How it is measured · 18 Phase 1 results · 19 Your Phase 2 results"],
       ["E · Reference", "20 Tech stack · 21 File map · 22 Privacy and rules · 23 Limitations and future work · 24 Viva questions · 25 Glossary"]],
      [32, 138])

# ---------------- PART A ----------------
H1("1 · The problem")
P("A <b>digital twin of a person</b> is software that answers questions the way that person would: their opinions, habits, "
  "choices and way of talking. The obvious way to build one today is to give a chatbot (ChatGPT, Gemini…) some facts about "
  "the person and ask it to role-play.")
P("That has a serious flaw: <b>language models never say \"I don't know\"</b>. Ask a role-playing chatbot \"What is your passport "
  "number?\" or \"What did you eat on 3 March?\" and it confidently invents an answer. For a twin that is meant to represent a "
  "real person, a confident invention is worse than no answer: it puts words in the person's mouth.")
UL(["Chatbots <b>hallucinate</b>: they fill gaps with plausible fiction.",
    "They give <b>no evidence</b>: you can't see which of the person's real statements an answer came from.",
    "Making them more personal usually means <b>fine-tuning</b> (training) on the person's data, which is slow, needs GPUs, and "
    "bakes private data into model weights.",
    "Most run in the <b>cloud</b>, so private answers leave the device."])
H2("Research question")
BOX("Can we build a digital twin that answers like the person when it has evidence, <b>says so when it is only guessing</b>, "
    "and <b>refuses (\"I don't know\") when nothing the person said is related</b>, while learning from corrections instantly, "
    "without any model training, on an ordinary laptop?", DEF)

H1("2 · The solution in one page")
P("The twin is built on <b>retrieval</b>, not training. Every interview answer becomes a <b>fact</b> in a searchable memory. "
  "When a question comes in, the twin finds the facts closest in meaning and measures <b>how close</b> the best one is. That "
  "number, the <b>confidence</b>, decides what the twin is allowed to do:")
story.append(gate_bar())
SP(4)
TABLE([["Confidence", "Tier", "What the twin does", "Shown as"],
       ["below 0.15", "hard_defer", "Refuses. No language model is even called, so it cannot invent anything.", "\"I don't know\" (purple)"],
       ["0.15 to 0.35", "soft_answer", "Reasons from the person's profile and the weak matches, and labels it as a guess.", "\"You never answered this · reasoning from your profile\" (amber)"],
       ["0.35 and above", "confident_answer", "Answers from what the person actually said, and shows that answer as evidence.", "\"You said this\" (green)"]],
      [26, 28, 70, 46])
H2("What makes it novel")
UL(["<b>Deferral as the core idea</b>: three honest tiers decided by a measurable number, not by the model's own opinion.",
    "<b>Honest confidence</b>: the gate always uses the <i>raw</i> similarity. Facts the person stated get a small ranking boost, "
    "but the boost never reaches the gate, so adding facts can't fake confidence (there is a test for this).",
    "<b>Learning without training</b>: a correction is added to memory and works on the very next question. Nothing is fine-tuned.",
    "<b>Evidence for every answer</b>: \"Why this answer\" shows which real answers were used, with a bar for each.",
    "<b>Runs on a laptop</b>: Phi-3 on Apple's GPU, voice cloning and lip-sync locally. Online mode (Gemini) is optional.",
    "<b>Multimodal</b>: the twin speaks in the person's cloned voice (English and Hindi) through a talking avatar from one photo, "
    "understands speech (Whisper) in English, Hindi and Telugu, and has an animated landing character.",
    "<b>Measured</b>: tested on 100 people from a public benchmark and on the real subject's own held-out answers."])

H1("3 · The big picture")
P("The app is a normal Mac application. Inside, it is a small website served by a local Python server, shown in a native window. "
  "Nothing listens on the network: the server only accepts connections from the same Mac (127.0.0.1).")
story.append(architecture())
story.append(Paragraph("Figure 1. The parts of the system. Green boxes are optional media features; amber is the optional cloud part.", S["center"]))
SP(8)
H2("The three layers, in plain words")
UL(["<b>Front end</b> (what you see): HTML, CSS and JavaScript in <font face='%s'>app/static/</font>. It draws the pages, the animated "
    "character, the chat, the charts, and sends requests to the server." % MONO,
    "<b>Server</b> (the coordinator): <font face='%s'>app/server.py</font>, built with FastAPI. It receives each request (\"ask this\", "
    "\"save my photo\", \"make a video\"), calls the right component and returns JSON." % MONO,
    "<b>Twin core</b> (the brain): memory and retrieval (<font face='%s'>rag_pipeline.py</font>), the deferral gate and prompt "
    "building (<font face='%s'>deferral.py</font>), the person's profile (<font face='%s'>persona.py</font>), and the language model "
    "(<font face='%s'>llm_backend.py</font>)." % (MONO, MONO, MONO, MONO)])
P("Heavy models run in separate Python environments (main, Wav2Lip, GFPGAN) so their libraries don't conflict. The server starts "
  "them as separate processes when needed.")

# ---------------- PART B ----------------
H1("4 · Installation and first-run setup")
P("<b>DeferralTwin.app</b> is small (about 200 MB): it contains a Python runtime, the launcher and the project code, but not the "
  "AI models. On first launch the <b>bootstrapper</b> (<font face='%s'>bootstrap/engine.py</font>) sets everything up in "
  "<font face='%s'>~/Library/Application Support/DeferralTwin</font>:" % (MONO, MONO))
TABLE([["Step", "What happens"],
       ["Environments", "uv creates isolated Python environments from locked dependency lists (bootstrap/locks/*.lock), so every Mac gets identical versions."],
       ["Models", "Downloads Phi-3-mini 4-bit (MLX), the all-mpnet-base-v2 embedder, XTTS-v2 (voice), Wav2Lip + face detector, GFPGAN, the English→Hindi translator. Resumable downloads."],
       ["ffmpeg", "Links the static ffmpeg build that ships inside a Python wheel (no Homebrew needed)."],
       ["Self-test", "A benchmark measures this Mac: model load time, answer time, voice and lip-sync speed. The app uses these numbers for time estimates."],
       ["Launch", "The launcher (bootstrap/launcher.py) starts the local server and opens a pywebview window (macOS WebKit). Camera and microphone permission texts come from the app's Info.plist."]],
      [30, 140])
P("Talk mode (Whisper, about 970 MB) and the Hindi→English translator are downloaded once on first use.", "small")
H2("Where your data lives")
CODE("~/Library/Application Support/DeferralTwin/\n"
     "  twin/phase2_answers.json      your interview answers\n"
     "  twin/ref.wav, photo_fixed.jpg your voice sample and photo\n"
     "  twin/benchmark/phase2/phase2_me/\n"
     "     train.jsonl / test.jsonl  facts the twin knows / answers held back for testing\n"
     "     faiss_demo.bin            the memory index\n"
     "     feedback_demo.jsonl       your corrections   notes.jsonl  things you told it\n"
     "     profile.json              your computed profile and writing style\n"
     "  twin/online_profile.json     the Gemini-written profile (Online mode only)\n"
     "  twin/work_profile.json       your job and skills (Expert mode)\n"
     "  models/  envs/  cache/       downloaded models, Python environments, cached audio/video")

H1("5 · Consent and the interview")
H2("Consent")
P("Before anything is recorded, the person agrees to how their data is used. Voice cloning and the avatar are enabled <b>only</b> for "
  "the person who consented (a hard rule of the project). Online mode has its own consent line, because it sends text to Google.")
H2("The interview: 109 questions in 8 sections")
TABLE([["Section", "Example", "Answer type", "Why it's there"],
       ["About you (12)", "Which city do you call home? What are you studying?", "free text", "basic facts, background"],
       ["Everyday preferences (13)", "Tea or coffee? Morning person or night owl?", "short choice", "tastes and habits"],
       ["What would you do? (16)", "A friend cancels plans last minute. What do you do?", "2–3 sentences", "behaviour in situations"],
       ["What you believe (14)", "Learning by building beats learning by reading.", "1–5 agreement (Likert)", "values and opinions"],
       ["How you tend to be (20)", "I start conversations with new people easily.", "1–5 agreement", "personality (Big Five)"],
       ["Either or (12)", "Stable work or risky work with a big upside?", "one of two", "trade-offs"],
       ["In your own words (10)", "Describe a perfect day.", "longer text", "voice and writing style"],
       ["Quick re-check (12)", "Reworded repeats of earlier questions", "same as original", "measures your own consistency (the human ceiling)"]],
      [34, 56, 30, 50])
P("Answers are autosaved as you type. Then the person records about 45 seconds of a read-aloud paragraph (voice) and one photo; both "
  "are checked immediately (too short, clipped or too quiet audio is rejected; the photo must show one whole face with the mouth visible).")

H1("6 · Building the twin (no training)")
P("Pressing <b>Build my twin</b> runs <font face='%s'>load_phase2.py</font> and the build steps. Nothing is trained. It is "
  "data preparation plus an index:" % MONO)
TABLE([["Step", "What it does", "Result"],
       ["1 Split", "A seeded, stratified 25% of answers is <b>held out</b>: the twin never sees them. (Stratified = every section is represented.)",
        "train.jsonl (what it knows), test.jsonl (18 secret answers for testing)"],
       ["2 Facts", "Each remaining answer becomes a fact written as \"Q: question  A: answer\".", "79 facts for Hardhik"],
       ["3 Embed", "Each fact is turned into a 768-number vector by <b>all-mpnet-base-v2</b>, a sentence-embedding model. "
        "Texts with similar meaning get vectors pointing in similar directions.", "79 vectors"],
       ["4 Index", "The vectors go into a <b>FAISS</b> IndexFlatIP index. Because the vectors are normalised, inner product = cosine similarity.",
        "faiss_demo.bin"],
       ["5 Profile", "persona.py computes a profile: how you rate statements, your either-or habits, Big Five, and a <b>style card</b> "
        "(typical answer length, lowercase or not, example answers).", "profile.json"],
       ["6 Baseline + consent", "A simple baseline for comparison and the consent record are saved.", "baseline.jsonl, consent.json"]],
      [24, 96, 50])
BOX("<b>Why retrieval instead of training?</b> Training (fine-tuning) changes the model's weights: it is slow, needs a strong GPU, "
    "can't be undone easily, and the model may still invent things. Retrieval keeps the model frozen and gives it the person's "
    "own words at question time. It is instant to update, transparent (we can show the evidence), and it gives us a number "
    "(similarity) to decide when to refuse.")

# ---------------- PART C ----------------
H1("7 · What happens when you ask a question")
P("Every question goes through the same pipeline (<font face='%s'>_ask</font> in app/server.py, then deferral.py):" % MONO)
story.append(pipeline())
story.append(Paragraph("Figure 2. The answering pipeline, step by step.", S["center"]))
SP(6)
H2("Retrieval and cosine similarity, explained")
P("The question is embedded into a vector with the same model as the facts. FAISS compares it with all fact vectors and returns "
  "the top 8. The comparison is <b>cosine similarity</b>: the cosine of the angle between two vectors. 1 means the same meaning, "
  "0 means unrelated. Example scores for Hardhik's twin:")
TABLE([["Question", "Closest fact", "Score", "Tier"],
       ["Tea or coffee?", "Q: Tea or coffee? A: Both", "0.8", "confident"],
       ["What do you do in your free time?", "Q: What do you do to relax? …", "0.55", "confident"],
       ["Do you know linear algebra?", "(no close fact)", "0.19", "labelled guess"],
       ["What is your passport number?", "(nothing related)", "below 0.15", "I don't know"]],
      [52, 66, 22, 30])

H1("8 · The deferral gate, in detail")
story.append(gate_bar())
P("The gate is three lines of logic, but it is the heart of the project:")
CODE("results, confidence = memory.retrieve(question)   # confidence = best RAW cosine\n"
     "if confidence < 0.15:   tier = 'hard_defer'        # refuse, no model call\n"
     "elif confidence < 0.35: tier = 'soft_answer'       # guess from profile, labelled\n"
     "else:                   tier = 'confident_answer'  # answer from the person's words")
H2("Why \"raw\" similarity matters")
P("Facts the person stated directly (corrections and notes) get a <b>+0.05 ranking boost</b>, so that \"I moved to Chennai\" "
  "outranks the old interview answer \"I live in Tirupati\". But the boost is used <b>only for ordering</b>. The confidence that the "
  "gate sees is the raw cosine. Otherwise, simply adding more notes would push weak matches over the thresholds and fake confidence. "
  "A dedicated test (tests/test_gate_and_media.py) keeps this honest.")
H2("Where the thresholds come from")
P("0.15 and 0.35 were chosen on the public Twin-2K-500 benchmark (Phase 1) by looking at how answer quality changes with similarity "
  "(the risk–coverage curve, section 18). They are kept fixed; all later features (Online mode, Expert mode, languages) leave them unchanged.")
H2("What each tier does after the gate")
UL(["<b>hard_defer</b>: returns a fixed \"I don't know\" sentence. The language model is not called at all, so it is impossible to hallucinate.",
    "<b>soft_answer</b>: the prompt includes the person's profile and the weak matches, and asks for an in-character guess. The answer "
    "card says \"You never answered this · reasoning from your profile\".",
    "<b>confident_answer</b>: the prompt includes the closest real answers, and the model answers from them. The card shows the "
    "matching question and answer as evidence.",
    "<b>Format follows the question</b>: a statement to rate gets \"Agree (4 of 5)\"; an either-or question gets one choice; everything "
    "else gets free text, at the person's usual length."])

H1("9 · The prompt and the persona")
P("For tiers that call the model, deferral.py builds a <b>system prompt</b>. Its pieces:")
UL(["<b>Identity rules</b>: speak as the person in first person; never say \"I am an AI\"; if asked what you are, \"I'm X's digital twin\".",
    "<b>Style card</b>: \"usually about N words, mostly lowercase, direct and casual\" plus a few of the person's real answers as examples, "
    "computed from the interview so the twin <i>sounds</i> like them.",
    "<b>Evidence</b>: the retrieved facts (the person's own words).",
    "<b>Profile summary</b>: for guesses, how the person tends to behave."])
P("The model is <b>Phi-3-mini</b> (3.8 billion parameters), quantised to 4 bits so it fits in about 2.4 GB, running on the Mac's GPU "
  "through <b>Apple MLX</b>. A typical answer takes about 1 second. Its output is cleaned: phrases like \"As an AI…\", \"Great question!\", "
  "and sympathy/advice openers are removed, and a sentence cut off by the length limit is trimmed.")
P("Special questions skip memory entirely: the clock (\"what time is it?\"), questions about the twin (\"who are you?\", \"what can you do?\") "
  "and greetings are answered from templates, labelled \"About your twin · not from your memories\".")

H1("10 · Learning without training")
P("The twin improves in four ways, and none of them changes the model:")
TABLE([["Way", "How the person does it", "What happens inside"],
       ["Correction", "\"That's not me\" under an answer → type the real answer", "A new fact \"Q: … A: …\" (source user_correction) is added to the FAISS index and logged to feedback_demo.jsonl. It outranks the old fact (+0.05 boost)."],
       ["\"That's me ✓\"", "Confirm a good guess", "The guessed answer becomes a confirmed fact."],
       ["Notes", "Teach page: one thought per line", "Each line becomes its own fact (source user_note)."],
       ["Gaps", "Teach page lists questions it refused or guessed", "Answering one adds it as a correction."],
       ["Guess me", "A game: the twin locks a guess, you answer", "Your answer is stored as a correction; the match score is logged."]],
      [26, 58, 86])
P("On restart, all corrections and notes are replayed into memory, so nothing is forgotten. In Phase 1, five corrections lifted "
  "similarity on related questions from 0.35 to 0.563 (section 18). This is the \"learns instantly, no training\" claim, measured.")

H1("11 · Online mode (Google Gemini, optional)")
P("Off by default. When turned on, the twin can reason better about new situations using Gemini, while the deferral gate still "
  "decides what it may answer.")
H2("How it works")
UL(["<b>Key</b>: pasted once, checked with Google, stored in the <b>macOS Keychain</b> (never in a file, log or response). Errors are scrubbed so the key can't leak.",
    "<b>Model choice</b>: the app asks the API which models exist and builds a chain: newest Pro → newest Flash models → Flash-Lite. "
    "On the free tier Pro has no quota, so Flash usually answers. A model out of quota is skipped for 30 min, a busy one for 1 min.",
    "<b>Profile written once</b>: Gemini reads the interview, corrections and notes <b>once</b> and writes a first-person profile "
    "(values, personality, habits, how you decide, how you talk). It is rewritten only when your memory changes (fingerprint check), "
    "so each question doesn't resend the whole interview.",
    "<b>Each question</b> sends: persona rules + date/time + the profile + the top retrieved real answers + the earlier chat turns.",
    "<b>Same gate</b>: below 0.15 there is no Gemini call at all; the tier label still comes from the raw confidence.",
    "<b>Fallback</b>: no internet, no quota, any error → the local answer, marked \"offline · this mac\" with the reason.",
    "<b>Voice and video always stay on the Mac.</b>"])

H1("12 · Expert mode")
P("A twin of a civil engineer should be able to answer \"what causes cracks in walls?\" even though that was never in the interview. "
  "Expert mode does this <b>honestly</b>:")
UL(["<b>Job and skills</b>: picked up from the interview answers by Gemini (Hardhik: \"AI & data science student\"), or typed on the Teach page with an optional CV.",
    "<b>Field subjects</b>: Gemini lists about 70 subjects someone in that job knows (half everyday problems, half core concepts).",
    "<b>Recognising a field question</b>: the question is embedded and compared with those subjects. Cosine ≥ 0.40 → field question; "
    "0.22–0.40 → Gemini is asked a one-word yes/no; personal questions (\"do you like…\", \"your favourite…\") never count.",
    "<b>Only when your own memory has nothing confident</b> (raw confidence < 0.35). If you said something related, your answer wins.",
    "<b>Answer</b>: Gemini's domain knowledge, in your tone, labelled <b>\"Expert knowledge · &lt;field&gt;\"</b>, with a note that it isn't a personal memory.",
    "<b>Outside the field, and offline, the normal gate applies</b>: an AI student's twin still says \"I don't know\" to a concrete-grade question."])

H1("13 · Telugu, Hindi and chat memory")
H2("Asking in Telugu or Hindi (app/lang.py)")
P("Memory and retrieval are in English, so the app works in three steps: (1) detect the script (Telugu or Devanagari Unicode "
  "ranges), (2) turn the question into one standalone English question (Gemini online; offline, a local Hindi→English Marian model), "
  "(3) run the normal pipeline and gate, then translate the answer back into the question's language <b>and script</b>. Example: "
  "a Telugu question \"which is your town?\" → \"What is your village?\" → \"My hometown is Tirupati\" in Telugu script. "
  "Hindi answers can be spoken in the cloned voice; Telugu is text-only because the voice model has no Telugu.")
H2("Remembering the chat")
P("The last 6 turns are sent with each question. A short follow-up (\"why?\", \"and on weekends?\") is rewritten into a full "
  "question before retrieval, so the gate still scores a real question: \"why?\" after \"Which city do you call home?\" becomes "
  "\"Why do you call Tirupati home?\". Online answers also see the earlier turns. \"New chat\" clears the conversation, not the memory.")

H1("14 · Talk mode, voice and the avatar")
H2("Talk mode: speech → text (app/asr.py)")
P("Press the mic, speak, press again. The recording is converted to 16 kHz audio and transcribed by <b>Whisper-small</b> "
  "(OpenAI, multilingual) on the Mac's CPU in about 1–2 s. Whisper detects the language. Its small model often writes Telugu "
  "speech in Devanagari letters; because the two Unicode blocks share one layout, the app shifts each letter to Telugu script. The "
  "text then goes through the normal pipeline, and the answer is spoken in your voice.")
H2("The cloned voice: text → speech (XTTS-v2)")
P("XTTS-v2 (Coqui) clones a voice from a short sample: it encodes the 28-second <font face='%s'>ref.wav</font> into a speaker "
  "representation and generates new speech in that voice. Text is spoken sentence by sentence (splitting on \".\", \"?\", \"!\" and "
  "the Hindi danda \"।\"), with tuned sampling (top-p 0.7, top-k 30), trimmed silences and even loudness. Hindi uses Gemini's "
  "translation when online, else the local Marian model." % MONO)
H2("The talking avatar (Wav2Lip + mouth-only blend)")
P("<b>Wav2Lip</b> takes the photo and the voice audio and generates, for every video frame, a mouth shape matching the sound. "
  "It regenerates the whole lower face at only 96×96 pixels, which makes the face blurry and leaves a box edge. The app then "
  "<b>keeps the original photo everywhere</b> and blends in only the mouth and jaw through a soft oval, sharpened and colour-matched. "
  "Measured: eye sharpness 13 → 25 (the photo is 26), mouth 8 → 16. Optional GFPGAN \"Sharpen the face\" restores every frame "
  "(much slower). Clips are cached, so a repeated answer plays instantly.")
H2("The greeting")
P("When the app opens, the avatar says hello in the person's voice (\"Hi, I'm X's digital twin…\"). It is made once through the "
  "same voice and lip-sync queue, then served from the cache.")

H1("15 · The landing character")
P("The home page shows an animated cartoon character whose head and eyes follow the mouse. It is not a 3D model; it is a "
  "<b>grid of pre-rendered images</b>:")
UL(["The character image was cleaned with <b>Real-ESRGAN</b>, then <b>LivePortrait</b> (a face re-animation model) rendered it at "
    "yaw −20°…+20° in 1° steps and pitch −10°…+10° in 2.5° steps (369 poses), plus half-closed and closed eyes for blinking (90).",
    "Each frame was colour-matched and detail-restored with optical flow, the background removed, and saved as WebP.",
    "<b>Eye layer</b>: the irises were painted out of every frame and are drawn live by the page, clipped to each eye opening and "
    "moved toward the cursor, so the eyes really follow you.",
    "<b>Smooth motion</b>: a critically damped spring moves the head (settles in ~150 ms); every animation frame blends the 4 nearest "
    "poses; images are decoded by background Web Workers so the page never stutters; small always-ready copies stand in during fast moves.",
    "Measured in the app's own WebKit engine: 60 frames per second, no wrong-pose frames while moving."])

H1("16 · The extra features")
TABLE([["Feature", "What the person sees", "How it works"],
       ["Why this answer", "Under every answer: the real answers it used, each with a similarity bar and the 0.15 / 0.35 marks", "Shows the retrieved evidence and raw scores; marks which ones were above the floor (\"used\")."],
       ["What would I do?", "Describe a choice + options; the twin picks one with reasons and certainty", "Retrieval on the situation gives the confidence (below 0.15 → \"I don't know\"); Gemini or Phi-3 picks an option citing your answers; never \"high\" certainty below 0.35; each option's closeness to your answers is shown."],
       ["Guess me", "A new question; the twin's guess is locked before you answer; then a match score", "The guess is kept on the server (can't be peeked); similarity of meaning is the score; your answer becomes memory, so the score can climb."],
       ["Personality snapshot", "A Big Five bar chart on the Teach page", "The 20 \"How you tend to be\" items: 4 per trait, some reverse-scored (e.g. \"Long social events leave me drained\" counts against Extraversion), averaged and scaled 0–100."],
       ["Change photo / voice", "Settings: replace either at any time", "Same checks as the interview; every cached voice/video clip is cleared so they're made again."]],
      [30, 60, 80])

# ---------------- PART D ----------------
H1("17 · How the twin is measured")
P("The key idea: <b>hide some real answers, ask the twin those questions, and compare</b>. The twin never saw the hidden answers, "
  "so this tests whether it can predict the person, not whether it remembers.")
UL(["<b>Score</b>: semantic similarity (cosine of embeddings) between the twin's answer and the real answer, from 0 to 1. "
    "This captures meaning, so \"I'd fix it myself\" and \"I'd repair it on my own\" score high.",
    "<b>Human ceiling</b>: people don't answer identically twice. The same person answering a reworded question again gives the "
    "best realistic score. A twin can't be expected to match you better than you match yourself.",
    "<b>Per-tier scores</b>: how good answers are in each tier, and how often each tier is used (coverage).",
    "<b>Selective accuracy</b>: if the twin only answered when confident, how good would it be, and how much would it cover?"])

H1("18 · Phase 1: the public benchmark (100 people)")
P("Phase 1 used <b>Twin-2K-500</b>, a public research dataset of survey answers from real people. 100 participants, "
  "<b>9,564 held-out questions</b>. (These results are frozen and reproducible: the evaluation script and data are never modified.)")
TABLE([["Measure", "Value", "Meaning"],
       ["Twin similarity", "0.332 (95% CI 0.325–0.339)", "average closeness of the twin's answers to the true ones"],
       ["Human re-test ceiling", "0.892", "the same people answering again (multiple-choice survey items are very repeatable)"],
       ["Confident answers only", "0.373 at 80% coverage", "+0.042 better when it only answers when confident: the gate selects better answers"],
       ["Tier shares", "confident 79.6% · guess 19.4% · refuse 1.0%", "guesses score 0.16 vs 0.373 for confident: the tiers sort quality correctly"],
       ["Correction loop (9 people)", "0.35 → 0.563 after 5 corrections", "learning without training works; a control reached 0.57; 20 corrections on personal facts +0.023"]],
      [40, 52, 78])
for f, cap in (("figures/risk_coverage_curve.png", "Risk–coverage: answering only above a confidence level raises quality; lower coverage, higher accuracy."),
               ("figures/feedback_curve_control.png", "Feedback curve: similarity on related questions rises with each correction, without training.")):
    p = HERE / f
    if p.exists():
        story.append(KeepTogether([Image(str(p), width=150 * mm, height=85 * mm, kind="proportional"), Paragraph(cap, S["center"]), Spacer(1, 8)]))

H1("19 · Phase 2: your own twin (the 18 questions)")
P("Phase 2 tests the twin of the real subject (Hardhik). From the interview, a stratified 25% of answers was hidden: "
  "<b>18 questions</b> the twin never saw. Each was asked to the twin, and its answer compared with the real one.")
TABLE([["Measure", "Value", "In plain words"],
       ["Twin average", "0.57", "on questions it never saw, the twin's answers are fairly close in meaning to Hardhik's"],
       ["Your own ceiling", "0.79", "Hardhik vs Hardhik: the \"Quick re-check\" reworded repeats match the originals 0.79"],
       ["Of ceiling", "72%", "0.57 / 0.79: the twin is about 72% as consistent with Hardhik as Hardhik is with himself"],
       ["Confident answers", "12 of 18 (67%)", "average 0.48"],
       ["Guessed answers", "6 of 18 (33%)", "average 0.77 (see below)"],
       ["Refused", "0", "every hidden question was related to something he said"]],
      [36, 30, 104])
BOX("<b>One-line answer for the panel:</b> \"On 18 questions it had never seen, my twin answered about 72% as consistently as I answer "
    "myself, and on the public benchmark of 100 people the confidence gate reliably separated good answers from guesses.\"", CONF)
H2("Why do guesses score higher than confident answers here?")
P("With only 18 questions, a few answers swing the averages (6 guesses vs 12 confident). Several hidden questions were "
  "either-or or rating questions where a profile-based guess happens to be right, while some confident answers were long free-text "
  "answers that score lower on similarity even when correct. On the 9,564-question Phase 1 benchmark the expected order holds "
  "clearly (confident 0.373 vs guess 0.16). <b>Be honest about the small sample</b>: it is one person's interview.")
P("The Results tab has a \"Score the twin now\" button that re-runs this evaluation with all current corrections included.", "small")

# ---------------- PART E ----------------
H1("20 · Technology stack")
TABLE([["Area", "Technology", "Why"],
       ["App shell", "pywebview + macOS WebKit; custom .app bundle with its own Python, ad-hoc signed, plus a .dmg", "native window, no browser needed"],
       ["Server", "Python 3.11, FastAPI, Uvicorn", "simple JSON API, streaming answers"],
       ["Front end", "Vanilla JavaScript, CSS, Canvas, Web Workers, SVG", "no framework; fast and small"],
       ["Language model", "Phi-3-mini-4k-instruct, 4-bit, Apple MLX (mlx-lm)", "runs on a 16 GB Mac in ~2.4 GB, ~1 s/answer"],
       ["Embeddings", "sentence-transformers all-mpnet-base-v2 (768-d)", "strong semantic similarity"],
       ["Vector search", "FAISS IndexFlatIP (exact cosine)", "fast, exact for a few hundred facts"],
       ["Online model", "Google Gemini via google-genai SDK; key in Keychain via keyring", "optional better reasoning"],
       ["Voice", "Coqui XTTS-v2", "zero-shot voice cloning, multilingual incl. Hindi"],
       ["Translation", "Helsinki-NLP opus-mt (Marian) en↔hi; Gemini online", "offline Hindi"],
       ["Speech recognition", "OpenAI Whisper-small (transformers)", "multilingual, local"],
       ["Lip-sync", "Wav2Lip (GAN) + OpenCV mouth-only blend; optional GFPGAN", "one photo → talking video"],
       ["Character", "LivePortrait, Real-ESRGAN, OpenCV optical flow", "pre-rendered head-turn grid"],
       ["Packaging", "uv with locked requirements; bootstrapper downloads models", "same versions on every Mac"],
       ["Tests", "6 test files with stand-in models (no network)", "gate honesty, key safety, every feature"]],
      [28, 82, 60])

H1("21 · File map: which file does what")
TABLE([["File", "Role"],
       ["app/server.py", "The API: ask, build, correct, notes, media, online, expert, decide, Big Five, Guess me, talk"],
       ["app/static/app.js, styles.css", "Every page, the chat, the character player, charts"],
       ["app/online.py", "Gemini: Keychain key, model chain, profile, prompts, fallback"],
       ["app/work.py", "Expert mode: job profile, field subjects, field-question matching"],
       ["app/lang.py", "Language detection, translation, follow-up rewriting"],
       ["app/asr.py", "Whisper speech recognition (Talk mode)"],
       ["app/media.py", "Queue for voice and video jobs, clip cache"],
       ["src/rag_pipeline.py", "Facts, embeddings, FAISS index, retrieve() with raw confidence"],
       ["src/deferral.py", "The three tiers, prompts, answer formats, cleaning, corrections, notes"],
       ["src/persona.py", "Profile and style card from the interview"],
       ["src/llm_backend.py", "Loads Phi-3 on MLX (or a stand-in for tests)"],
       ["src/multimodal.py", "XTTS voice, Hindi translation, Wav2Lip + mouth blend, GFPGAN"],
       ["src/load_phase2.py, eval_phase2.py", "Build the twin from the interview; Phase 2 evaluation"],
       ["src/eval_benchmark.py", "Phase 1 evaluation (frozen)"],
       ["bootstrap/engine.py, launcher.py", "First-run setup and launching the window"],
       ["tools/liveportrait/*", "Rendering and processing the landing character"],
       ["tests/*.py", "Automated checks for every feature"]],
      [52, 118])

H1("22 · Privacy, ethics and the project's hard rules")
UL(["<b>Local by default</b>: answers, memory, voice and photo stay in the app folder on the Mac. The server only accepts local connections.",
    "<b>Online mode is opt-in</b>, with its own consent line; voice and video never leave the Mac; the key is in the Keychain only.",
    "<b>Consent boundary</b>: voice cloning and the avatar only for the person who consented; benchmark participants (anonymous) never get a voice or face.",
    "<b>No training</b>: the language model is frozen; personal data is never baked into model weights.",
    "<b>Honest deferral</b>: the gate uses raw similarity; the boost never reaches it; there is a test.",
    "<b>Frozen evaluation</b>: the Phase 1 script and data are never modified, so the published numbers stay reproducible.",
    "<b>Delete anytime</b>: Settings can delete the twin, or remove everything including models."])

H1("23 · Limitations and future work")
H2("Limitations (say these before they are asked)")
UL(["<b>Small personal test set</b>: 18 hidden questions for one person; averages move with a few answers.",
    "<b>Similarity is a proxy</b>: cosine similarity measures closeness of meaning, not correctness; two different but valid answers can score low.",
    "<b>Fixed thresholds</b>: 0.15 and 0.35 were tuned on a survey dataset; the best values may differ per person.",
    "<b>Speech</b>: Whisper-small is weak in Telugu; the cloned Hindi voice has an English accent (the voice sample is English); no Telugu voice.",
    "<b>Avatar</b>: Wav2Lip moves only the mouth and is slow on a Mac (minutes per answer); the head is still.",
    "<b>Expert mode</b> depends on Gemini and on correctly recognising field questions."])
H2("Future work")
UL(["Per-person calibration of the thresholds from their own corrections.",
    "An Indic voice model (e.g. AI4Bharat) for natural Hindi and a Telugu voice; a larger Whisper for Telugu.",
    "A newer talking-head model with head motion and expressions.",
    "Asking the person the questions it is least sure about (active learning).",
    "A Windows version (CUDA instead of MLX) and secure sharing of a twin with family."])

H1("24 · Questions examiners are likely to ask")
QA = [
    ("How is this different from ChatGPT with a prompt?", "Three things: it refuses when nothing related is known (no model call below 0.15, so no invention), "
     "every answer shows the person's real answers it came from, and it learns from corrections instantly without training. It also runs offline on a laptop."),
    ("Did you train or fine-tune a model?", "No. The model is frozen. The twin learns by adding facts to a retrieval index; that's why a correction works on the next question."),
    ("What is the confidence, exactly?", "The highest cosine similarity between the question's embedding and any of the person's fact embeddings (all-mpnet-base-v2, FAISS), using the raw value, not the boosted ranking score."),
    ("Why 0.15 and 0.35?", "Chosen on the Phase 1 benchmark from the risk–coverage curve: below 0.15 almost nothing relevant exists; above 0.35 answers are reliably grounded. They are fixed for all features."),
    ("Can adding notes fake confidence?", "No. Notes and corrections get a +0.05 boost only for ranking. The gate uses raw similarity, and a test enforces this."),
    ("What does 72% mean?", "On 18 hidden questions the twin scored 0.57 similarity; the person matches their own earlier answers at 0.79. 0.57/0.79 = 72% of the realistic maximum."),
    ("Why is the Phase 1 twin score (0.332) lower than Phase 2 (0.57)?", "Different data: Phase 1 is a large survey dataset with many multiple-choice items and a very high human re-test (0.892); "
     "Phase 2 is one person's open interview. Compare each to its own ceiling, not to each other."),
    ("Where does the persona/style come from?", "From the interview: persona.py computes typical answer length, lowercase use, rating habits, Big Five and example answers, and puts them in the prompt."),
    ("Is the voice cloning trained on my voice?", "No. XTTS-v2 is zero-shot: it conditions on a 28-second sample at generation time."),
    ("How does the lip-sync work?", "Wav2Lip predicts mouth shapes from the audio for each frame of the photo; the app keeps the original photo and blends only the mouth region, sharpened and colour-matched."),
    ("What happens if Gemini is down?", "The model chain tries other Gemini models; if all fail, the local Phi-3 answers and the card says \"offline · this mac\" with the reason."),
    ("Is Online mode safe for privacy?", "It is off by default and needs consent. Only text is sent (profile, the question, closest answers); voice and video stay local; the key is in the Keychain."),
    ("How do you stop Expert mode from making things up about the person?", "It only triggers for field questions when personal memory has nothing confident, is labelled \"Expert knowledge\", and the prompt forbids personal claims; personal questions still go through the gate."),
    ("Does the chat use all interview questions?", "Its memory is your interview answers (except the 18 held back for testing), plus every correction and note. Online, Gemini also reads a profile written from them, and Expert mode adds general field knowledge, clearly labelled."),
    ("What would you improve with more time?", "Per-person threshold calibration, a better Indic voice, a head-moving avatar, and active learning (asking the questions it's least sure about)."),
]
for q, a in QA:
    story.append(KeepTogether([Paragraph("Q. " + q, S["q"]), Paragraph(a, S["p"])]))

H1("25 · Glossary")
TABLE([["Term", "Meaning"],
       ["Digital twin", "Software that answers as a specific person would"],
       ["Deferral", "Choosing not to answer (\"I don't know\") when evidence is too weak"],
       ["Embedding", "A list of numbers (here 768) representing the meaning of a text"],
       ["Cosine similarity", "How closely two embeddings point the same way: 1 = same meaning, 0 = unrelated"],
       ["RAG", "Retrieval-Augmented Generation: find relevant facts, then let the model answer from them"],
       ["FAISS", "A library for fast vector search (Facebook AI Similarity Search)"],
       ["LLM", "Large language model (here Phi-3-mini locally, Gemini online)"],
       ["Quantisation (4-bit)", "Storing model weights with fewer bits so the model fits in less memory"],
       ["MLX", "Apple's machine-learning framework for Apple-silicon GPUs"],
       ["Fine-tuning", "Training a model further on new data (not used here)"],
       ["Held-out set", "Answers hidden from the twin, used only for testing"],
       ["Human ceiling", "How well the person matches themselves when asked again"],
       ["Likert scale", "1–5 agreement scale (Strongly disagree … Strongly agree)"],
       ["Big Five", "Openness, Conscientiousness, Extraversion, Agreeableness, Neuroticism"],
       ["XTTS-v2", "A text-to-speech model that clones a voice from a short sample"],
       ["Wav2Lip", "A model that makes a face's mouth move in sync with audio"],
       ["Whisper", "OpenAI's speech-recognition model"],
       ["Coverage", "The share of questions the twin answers (instead of refusing)"]],
      [38, 132])

doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=20 * mm,
                        title="Deferral-Aware Digital Twin: complete guide", author="Deferral Twin project")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print("wrote", OUT)
