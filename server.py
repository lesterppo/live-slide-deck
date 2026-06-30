#!/usr/bin/env python3
"""
audience-deck server.py — Real-time audience-directed presentation slide deck.
DeepSeek API backend generates slide content from aggregated audience input.
Single-file HTML serves display (projector), mobile (audience phones), and admin (presenter).
"""
import os, sys, json, time, threading, argparse, socket, urllib.parse, uuid, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime

# --- Config ---
DEEPSEEK_API_KEY = None
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
STATE_FILE = Path.home() / ".audience-deck" / "state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
LOCK = threading.Lock()
ADMIN_TOKEN = None  # Set from --admin-token arg or env var

# --- State ---
def default_state():
    return {
        "topic": "",
        "slide_history": [],       # [{layout, title, subtitle, content}]
        "current_slide": None,     # active slide shown on display
        "prompt_live": None,       # {type, question, options, ...}  currently pushed to audience
        "submissions": [],         # [{session, type, response, timestamp}]
        "sessions": {},            # {session_id: {created_at, submissions: [...]}}
        "generating": False,       # true while DeepSeek call is in flight
        "generation_result": None, # the latest generated slide (shown when done)
    }

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return default_state()

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# --- DeepSeek API ---
def call_deepseek(system_prompt: str, user_prompt: str) -> dict:
    """Call DeepSeek API with JSON mode. Returns parsed JSON dict."""
    import urllib.request, urllib.error

    body = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 1024,
        "temperature": 0.8,
    }).encode()

    req = urllib.request.Request(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()[:500]
        return {"layout": "hero", "title": "API Error", "subtitle": "",
                "content": {"hero_text": f"DeepSeek API error: {e.code}", "insight": err_body,
                            "bullets": [], "chartData": [], "words": [], "next_prompt": ""}}
    except Exception as e:
        return {"layout": "hero", "title": "Error", "subtitle": "",
                "content": {"hero_text": str(e)[:200], "insight": "",
                            "bullets": [], "chartData": [], "words": [], "next_prompt": ""}}

def build_generation_prompt(state: dict) -> tuple:
    """Build system + user prompts for DeepSeek from accumulated state."""
    topic = state.get("topic", "General Discussion")
    prompt_live = state.get("prompt_live") or {}
    submissions = state.get("submissions", [])
    history = state.get("slide_history", [])

    # Summarize history
    history_str = ""
    if history:
        titles = [s.get("title", "Untitled") for s in history[-5:]]
        history_str = "Previous slides: " + " → ".join(titles)

    # Aggregate submissions
    agg = ""
    if submissions:
        prompt_type = prompt_live.get("type", "question")
        if prompt_type == "poll":
            options = prompt_live.get("options", [])
            counts = {}
            for s in submissions:
                ans = s.get("response", "")
                counts[ans] = counts.get(ans, 0) + 1
            total = sum(counts.values())
            agg = "Poll results ({} votes): ".format(total)
            agg += ", ".join("{}: {} votes ({}%)".format(
                opt, counts.get(opt, 0),
                round(counts.get(opt, 0) / total * 100) if total else 0
            ) for opt in options)
        elif prompt_type == "wordcloud":
            words = [s.get("response", "").strip() for s in submissions if s.get("response", "").strip()]
            agg = "Word cloud inputs ({} responses): {}".format(len(words), ", ".join(words[:30]))
        elif prompt_type == "rating":
            ratings = []
            for s in submissions:
                try:
                    ratings.append(float(s.get("response", 0)))
                except:
                    pass
            if ratings:
                avg = sum(ratings) / len(ratings)
                agg = "Rating results: average {:.1f}/10 from {} responses (min={}, max={})".format(
                    avg, len(ratings), min(ratings), max(ratings))
        elif prompt_type == "question":
            answers = [s.get("response", "") for s in submissions if s.get("response", "").strip()]
            agg = "Audience responses ({} total): ".format(len(answers))
            agg += " | ".join(answers[:10])

    system_prompt = (
        "You are the AI director of a live interactive presentation on: {topic}. "
        "Your job: take aggregated audience input and generate the next slide's content. "
        "Respond ONLY with a JSON object matching this schema:\n"
        '{{"layout": "hero"|"split-text"|"chart-bar"|"word-cloud", '
        '"title": "Slide title", "subtitle": "Optional subtitle", '
        '"content": {{'
        '"hero_text": "For hero layout — one impactful sentence", '
        '"bullets": ["point 1", "point 2"], '
        '"chartData": [{{"label": "A", "value": 40}}], '
        '"words": [{{"text": "word", "weight": 10}}], '
        '"insight": "One insight from the audience data", '
        '"next_prompt": "Suggested next audience prompt"'
        '}}}}.\n'
        "Choose layout based on input type: poll→chart-bar, wordcloud→word-cloud, "
        "question→split-text, empty→hero. Be engaging, insightful, visually varied."
    ).format(topic=topic)

    user_prompt = (
        "Topic: {topic}\n"
        "{history}\n"
        "Current audience prompt: {prompt_q}\n"
        "{aggregated}\n\n"
        "Generate the next presentation slide."
    ).format(
        topic=topic,
        history=history_str,
        prompt_q=prompt_live.get("question", "No prompt active"),
        aggregated=agg or "No audience input yet. Generate an introductory slide."
    )

    return system_prompt, user_prompt

# --- QR Code Generation ---
QR_IMAGE_DATA = None

def generate_qr(url: str):
    import qrcode, io
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# --- HTML Templates ---
def get_index_html(public_url: str) -> str:
    """Self-contained HTML serving all three views via ?view=mobile|display|admin"""
    # QR_JS, DISPLAY_JS, ADMIN_JS defined inline below
    return _HTML_TEMPLATE.replace("__PUBLIC_URL__", public_url)

# The complete HTML is large — we'll write it separately and server reads it
HTML_FILE = Path(__file__).parent / "index.html"

def get_html_content(public_url: str) -> str:
    if HTML_FILE.exists():
        content = HTML_FILE.read_text()
        return content.replace("__PUBLIC_URL__", public_url)
    return _FALLBACK_HTML.replace("__PUBLIC_URL__", public_url)

# --- HTTP Handler ---
class DeckHandler(BaseHTTPRequestHandler):
    public_url = ""

    def log_message(self, format, *args):
        pass

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _png(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _check_admin(self):
        """Return True if admin access is authorized."""
        if not ADMIN_TOKEN:
            return True  # No token required
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        token = qs.get("token", [None])[0]
        return token == ADMIN_TOKEN

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            self._html(get_html_content(self.public_url))
        elif path == "/qr.png":
            global QR_IMAGE_DATA
            self._png(QR_IMAGE_DATA)
        elif path == "/api/state":
            with LOCK:
                state = load_state()
            self._json({
                "current_slide": state.get("current_slide"),
                "prompt_live": state.get("prompt_live"),
                "generating": state.get("generating", False),
                "generation_result": state.get("generation_result"),
                "submission_count": len(state.get("submissions", [])),
                "topic": state.get("topic", ""),
            })
        elif path == "/api/results":
            with LOCK:
                state = load_state()
            prompt_live = state.get("prompt_live") or {}
            submissions = state.get("submissions", [])
            prompt_type = prompt_live.get("type", "question")
            result = {"type": prompt_type, "count": len(submissions), "data": None}
            if prompt_type == "poll":
                options = prompt_live.get("options", [])
                counts = {o: 0 for o in options}
                for s in submissions:
                    r = s.get("response", "")
                    if r in counts:
                        counts[r] += 1
                total = sum(counts.values())
                result["data"] = [{"label": o, "value": counts[o],
                                   "pct": round(counts[o]/total*100) if total else 0}
                                  for o in options]
            elif prompt_type == "wordcloud":
                words = {}
                for s in submissions:
                    for w in s.get("response", "").lower().split():
                        w = re.sub(r'[^a-z0-9]', '', w)
                        if len(w) > 2:
                            words[w] = words.get(w, 0) + 1
                result["data"] = [{"text": w, "weight": c} for w, c in
                                  sorted(words.items(), key=lambda x: -x[1])[:30]]
            elif prompt_type == "rating":
                ratings = []
                for s in submissions:
                    try:
                        ratings.append(float(s.get("response", 0)))
                    except:
                        pass
                if ratings:
                    result["data"] = {
                        "avg": round(sum(ratings)/len(ratings), 1),
                        "count": len(ratings),
                        "min": min(ratings),
                        "max": max(ratings),
                    }
            elif prompt_type == "question":
                result["data"] = [s.get("response", "") for s in submissions[-20:]]
            self._json(result)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))

        # Payload size limit: 64KB
        if length > 65536:
            self._json({"ok": False, "error": "Payload too large"}, 413)
            return

        body = self.rfile.read(length)

        if path == "/api/submit":
            try:
                data = json.loads(body)
            except:
                self._json({"ok": False, "error": "invalid JSON"}, 400)
                return

            session_id = data.get("session", str(uuid.uuid4())[:8])
            response = str(data.get("response", ""))[:500]  # Limit response length

            with LOCK:
                state = load_state()
                state.setdefault("sessions", {})
                state["sessions"].setdefault(session_id, {"created_at": datetime.now().isoformat(), "submissions": []})
                state["sessions"][session_id]["submissions"].append({
                    "response": response,
                    "timestamp": datetime.now().isoformat(),
                })
                state.setdefault("submissions", [])
                state["submissions"].append({
                    "session": session_id,
                    "type": (state.get("prompt_live") or {}).get("type", "unknown"),
                    "response": response,
                    "timestamp": datetime.now().isoformat(),
                })
                save_state(state)
                count = len(state["submissions"])
            self._json({"ok": True, "session": session_id,
                        "count": count})

        elif path == "/api/generate":
            if not self._check_admin():
                self._json({"ok": False, "error": "Admin token required"}, 403)
                return

            with LOCK:
                state = load_state()
                if state.get("generating"):
                    self._json({"ok": False, "error": "Already generating"}, 409)
                    return
                state["generating"] = True
                state["generation_result"] = None
                save_state(state)

            # Acknowledge immediately
            self._json({"ok": True, "message": "Generation started"})

            # Background: call DeepSeek
            def generate():
                sys_prompt, user_prompt = build_generation_prompt(state)
                result = call_deepseek(sys_prompt, user_prompt)
                with LOCK:
                    s = load_state()
                    slide = {
                        "layout": result.get("layout", "hero"),
                        "title": result.get("title", "Untitled"),
                        "subtitle": result.get("subtitle", ""),
                        "content": result.get("content", {}),
                    }
                    s.setdefault("slide_history", []).append(slide)
                    s["current_slide"] = slide
                    s["generating"] = False
                    s["generation_result"] = slide
                    s["submissions"] = []
                    s["prompt_live"] = None
                    save_state(s)
            threading.Thread(target=generate, daemon=True).start()

        elif path == "/api/push":
            if not self._check_admin():
                self._json({"ok": False, "error": "Admin token required"}, 403)
                return
            try:
                data = json.loads(body)
            except:
                self._json({"ok": False, "error": "invalid JSON"}, 400)
                return

            with LOCK:
                state = load_state()
                state["prompt_live"] = {
                    "type": data.get("type", "poll"),
                    "question": str(data.get("question", ""))[:200],
                    "options": [str(o)[:100] for o in data.get("options", [])][:6],
                    "timestamp": datetime.now().isoformat(),
                }
                state["submissions"] = []
                save_state(state)
            self._json({"ok": True})

        elif path == "/api/topic":
            if not self._check_admin():
                self._json({"ok": False, "error": "Admin token required"}, 403)
                return
            try:
                data = json.loads(body)
            except:
                self._json({"ok": False, "error": "invalid JSON"}, 400)
                return
            with LOCK:
                state = load_state()
                state["topic"] = str(data.get("topic", ""))[:200]
                save_state(state)
            self._json({"ok": True})

        elif path == "/api/reset":
            if not self._check_admin():
                self._json({"ok": False, "error": "Admin token required"}, 403)
                return
            with LOCK:
                save_state(default_state())
            self._json({"ok": True})

        else:
            self._json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

# --- Fallback HTML (if index.html not found) ---
_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Audience Deck</title></head>
<body><h1>Audience Deck</h1><p>index.html not found. Create it in the same directory as server.py.</p>
<p>Public URL: __PUBLIC_URL__</p></body></html>"""

# --- Main ---
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def main():
    global QR_IMAGE_DATA, DEEPSEEK_API_KEY

    parser = argparse.ArgumentParser(description="Audience-directed presentation server")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--public-url", type=str, default=None)
    parser.add_argument("--api-key", type=str, default=None)
    parser.add_argument("--model", type=str, default="deepseek-chat")
    parser.add_argument("--admin-token", type=str, default=None,
                        help="Secret token required for admin endpoints (?token=...)")
    args = parser.parse_args()

    # API key resolution: --api-key flag or DEEPSEEK_API_KEY env var
    DEEPSEEK_API_KEY = args.api_key or os.environ.get("DEEPSEEK_API_KEY")
    # Optionally read from .env file in current directory
    if not DEEPSEEK_API_KEY:
        local_env = Path(".env")
        if local_env.exists():
            for line in local_env.read_text().splitlines():
                if line.startswith("DEEPSEEK_API_KEY="):
                    DEEPSEEK_API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not DEEPSEEK_API_KEY:
        print("ERROR: DEEPSEEK_API_KEY not found. Set DEEPSEEK_API_KEY env var, use --api-key, or create .env file")
        sys.exit(1)

    global DEEPSEEK_MODEL, ADMIN_TOKEN
    DEEPSEEK_MODEL = args.model
    ADMIN_TOKEN = args.admin_token or os.environ.get("AUDIENCE_DECK_ADMIN_TOKEN")

    host = args.host or get_local_ip()
    public_url = args.public_url or f"http://{host}:{args.port}"

    print(f"Generating QR code for {public_url} ...")
    QR_IMAGE_DATA = generate_qr(public_url)

    DeckHandler.public_url = public_url

    server = HTTPServer((host, args.port), DeckHandler)
    print(f"\n{'='*55}")
    print(f"  Audience Deck Server")
    print(f"  Local:    http://{host}:{args.port}")
    if args.public_url:
        print(f"  Public:   {public_url}")
    print(f"  Display:  {public_url}?view=display")
    print(f"  Mobile:   {public_url}  (default)")
    print(f"  Model:    {DEEPSEEK_MODEL}")
    if ADMIN_TOKEN:
        print(f"  Admin:    {public_url}?view=admin&token={ADMIN_TOKEN}")
    else:
        print(f"  Admin:    {public_url}?view=admin  (NO TOKEN SET — unprotected)")
    print(f"{'='*55}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()

if __name__ == "__main__":
    main()
