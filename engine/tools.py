"""
Tool definitions
All tool functions that the B layer can call

Each tool has:
  - Function implementation
  - schema (tells LLM what the tool does and its parameters)
  - Risk level (low/medium/high) - high-risk operations require A layer user confirmation
"""

import os
import sys
import json
import subprocess
import shutil
import glob
import base64
import urllib.request
import urllib.parse
import threading
from pathlib import Path
from engine.db_guard import guarded_connect
from datetime import datetime
from typing import Any, Dict, List, Optional


def _get_app_dir() -> Path:
    """Get project directory (cross-platform)"""
    p = Path(__file__).resolve().parent  # engine/
    app = p.parent  # project root
    if (app / "main.py").exists():
        return app
    return Path.cwd()


def _get_desktop() -> Path:
    """Get user Desktop folder (cross-platform)"""
    import sys
    p = Path.home() / "Desktop"
    if p.exists():
        return p
    # Linux: try xdg-user-dir
    if sys.platform == "linux":
        try:
            result = subprocess.run(
                ["xdg-user-dir", "DESKTOP"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0 and result.stdout.strip():
                alt = Path(result.stdout.strip())
                if alt.exists():
                    return alt
        except Exception:
            pass
    return Path.home()


# ═══════════════════════════════════════════════════
# Tool Registry
# ═══════════════════════════════════════════════════

TOOL_REGISTRY: Dict[str, Dict] = {}


def register_tool(name: str, description: str, parameters: dict, risk: str = "low"):
    """Decorator: register a tool in the registry"""
    def decorator(func):
        # required must be at the top level, not inside properties (DeepSeek/OpenAI spec)
        required_keys = [k for k, v in parameters.items() if v.get("required", False)]
        clean_props = {
            k: {pk: pv for pk, pv in v.items() if pk != "required"}
            for k, v in parameters.items()
        }
        TOOL_REGISTRY[name] = {
            "function": func,
            "schema": {
                "name": name,
                "description": description,
                "input_schema": {
                    "type": "object",
                    "properties": clean_props,
                    "required": required_keys
                }
            },
            "risk": risk
        }
        return func
    return decorator


# ═══════════════════════════════════════════════════
# File System Tools
# ═══════════════════════════════════════════════════

@register_tool(
    name="read_file",
    description="Read local file content. Supports text files (txt/md/py/json/csv, etc.)",
    parameters={
        "path": {"type": "string", "description": "File path (absolute or relative)", "required": True},
        "encoding": {"type": "string", "description": "Encoding format, default utf-8"}
    },
    risk="low"
)
def read_file(path: str, encoding: str = "utf-8") -> Dict:
    try:
        path = os.path.expanduser(path)
        with open(path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        size = len(content)
        # If over 50k chars, return only first 50k
        if size > 50000:
            content = content[:50000] + f"\n\n[File too large, truncated. Total size: {size} chars]"
        return {"ok": True, "content": content, "path": path, "size": size}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="search_in_file",
    description=(
        "Search for a keyword in a specified file, return matching lines with context."
        "Useful for finding specific people, events, passages, etc. in already-read files."
    ),
    parameters={
        "path": {"type": "string", "description": "File path (absolute or relative)", "required": True},
        "keyword": {"type": "string", "description": "Keyword to search for", "required": True},
        "encoding": {"type": "string", "description": "Encoding format, default utf-8"},
        "context_lines": {"type": "integer", "description": "Lines of context before/after each match, default 3"}
    },
    risk="low"
)
def search_in_file(path: str, keyword: str, encoding: str = "utf-8",
                   context_lines: int = 3) -> Dict:
    try:
        path = os.path.expanduser(path)
        with open(path, "r", encoding=encoding, errors="replace") as f:
            lines = f.readlines()

        matches = []
        for i, line in enumerate(lines):
            if keyword in line:
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                context = "".join(lines[start:end]).rstrip()
                matches.append({
                    "line_number": i + 1,
                    "line": line.rstrip(),
                    "context": context
                })

        total_chars = sum(len(m["context"]) for m in matches)
        # Truncate overly long results
        if total_chars > 8000:
            for m in matches:
                m["context"] = m["context"][:500]
            matches = matches[:20]

        return {
            "ok": True,
            "path": path,
            "keyword": keyword,
            "total_lines": len(lines),
            "match_count": len(matches),
            "matches": matches
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="write_file",
    description="Write content to file. If path is empty, saves to desktop by default. Supports relative and absolute paths",
    parameters={
        "path": {"type": "string",
                 "description": "Target file path. Can be a filename (auto-save to desktop), relative path, or absolute path",
                 "required": True},
        "content": {"type": "string", "description": "Content to write", "required": True},
        "append": {"type": "boolean", "description": "Whether to append (instead of overwrite), default false"}
    },
    risk="medium"
)
def write_file(path: str, content: str, append: bool = False) -> Dict:
    try:
        path = path.strip()
        # If it is just a filename (no path separator), auto-save to desktop
        if not any(c in path for c in ["/", "\\", ":"]):
            desktop = _get_desktop()
            path = str(desktop / path)
        path = os.path.expanduser(path)
        abs_path = os.path.abspath(path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        mode = "a" if append else "w"
        with open(abs_path, mode, encoding="utf-8") as f:
            f.write(content)
        return {
            "ok": True,
            "path": abs_path,
            "bytes_written": len(content.encode()),
            "tip": f"File saved to: {abs_path}"
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="list_directory",
    description="List directory contents, including files and subdirectories",
    parameters={
        "path": {"type": "string", "description": "Directory path, default current directory"},
        "pattern": {"type": "string", "description": "Filter pattern, e.g. *.py, *.txt"}
    },
    risk="low"
)
def list_directory(path: str = ".", pattern: str = "*") -> Dict:
    try:
        path = os.path.expanduser(path)
        # "." should resolve to the project directory, not CWD
        if path in (".", "./"):
            path = str(_get_app_dir())
        entries = []
        for item in sorted(Path(path).glob(pattern)):
            stat = item.stat()
            entries.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            })
        return {"ok": True, "path": str(Path(path).absolute()), "entries": entries}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="search_files",
    description="Search for files containing specified content in a directory (searches one level of subdirectories, max 20 results)",
    parameters={
        "directory": {"type": "string", "description": "Search directory (default: project root)", "required": False},
        "keyword": {"type": "string", "description": "Search keyword", "required": True},
        "file_pattern": {"type": "string", "description": "File type filter, e.g. *.py"}
    },
    risk="low"
)
def search_files(keyword: str, directory: str = ".", file_pattern: str = "*") -> Dict:
    try:
        directory = os.path.expanduser(directory)
        if directory in (".", "./"):
            directory = str(_get_app_dir())
        # Safety: prevent searching system directories
        abs_dir = Path(directory).resolve()
        blocked = {"C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
                    "/usr", "/etc", "/bin", "/System", "/Library"}
        if any(abs_dir.is_relative_to(Path(b)) for b in blocked):
            return {"ok": False, "error": f"Cannot search system directory: {directory}"}
        results = []
        max_depth = 3  # limit recursion depth
        for filepath in abs_dir.rglob(file_pattern):
            # depth check
            try:
                rel = filepath.relative_to(abs_dir)
                if len(rel.parts) > max_depth:
                    continue
            except ValueError:
                continue
            if filepath.is_file():
                try:
                    content = filepath.read_text(encoding="utf-8", errors="ignore")
                    if keyword.lower() in content.lower():
                        lines = content.split("\n")
                        matched = [(i+1, l.strip()) for i, l in enumerate(lines)
                                   if keyword.lower() in l.lower()][:3]
                        results.append({
                            "file": str(filepath),
                            "matches": matched
                        })
                        if len(results) >= 20:
                            break
                except Exception:
                    pass
        return {"ok": True, "keyword": keyword, "found": len(results), "results": results[:20]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="delete_file",
    description="Delete a file or empty directory",
    parameters={
        "path": {"type": "string", "description": "Path of file to delete", "required": True}
    },
    risk="high"
)
def delete_file(path: str) -> Dict:
    try:
        path = os.path.expanduser(path)
        if os.path.isfile(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        else:
            return {"ok": False, "error": "Path does not exist"}
        return {"ok": True, "deleted": path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="export_guest_photos",
    description="Export guest session facial photos to desktop. Specify session ID for a single export, or export all without specifying",
    parameters={
        "session_id": {"type": "string",
                       "description": "Guest session ID to export (optional, exports all records with photos if not specified)"}
    },
    risk="low"
)
def export_guest_photos(session_id: str = "") -> Dict:
    try:
        import sqlite3
        # Get database path
        try:
            from desktop.config import DB_FILE
            db_path = DB_FILE
        except Exception:
            db_path = str(Path.home() / "Desktop" / ".agi-desktop" / "memory.db")
            if sys.platform == "win32":
                db_path = str(Path(os.environ.get("APPDATA", str(Path.home())))
                              / "AGI-Desktop" / "memory.db")

        desktop = _get_desktop()
        export_dir = desktop / "AGI Guest Photos"
        export_dir.mkdir(parents=True, exist_ok=True)

        with guarded_connect(db_path) as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT session_id, started_at, photo_b64 FROM guest_sessions "
                    "WHERE session_id=? AND photo_b64 IS NOT NULL AND photo_b64 != ''",
                    (session_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT session_id, started_at, photo_b64 FROM guest_sessions "
                    "WHERE photo_b64 IS NOT NULL AND photo_b64 != '' "
                    "ORDER BY started_at DESC"
                ).fetchall()

        if not rows:
            return {"ok": False, "error": "No guest records with photos found"}

        saved = []
        for row in rows:
            sid, started_at, photo_b64 = row
            try:
                img_data = base64.b64decode(photo_b64)
                time_str = started_at.replace(":", "-").replace(".", "-")[:19] if started_at else "unknown"
                filename = f"guest_{sid}_{time_str}.jpg"
                filepath = export_dir / filename
                filepath.write_bytes(img_data)
                saved.append(filename)
            except Exception as e:
                saved.append(f"{sid}: export failed({e})")

        return {
            "ok": True,
            "export_dir": str(export_dir),
            "total": len(rows),
            "saved": saved,
            "tip": f"Exported {len(rows)} photos to: {export_dir}"
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# Terminal Command Tools
# ═══════════════════════════════════════════════════

@register_tool(
    name="run_command",
    description="Execute shell commands in terminal. Suitable for: installing packages, running scripts, git operations, file processing, etc.",
    parameters={
        "command": {"type": "string", "description": "Command to execute", "required": True},
        "cwd": {"type": "string", "description": "Working directory, default current directory"},
        "timeout": {"type": "integer", "description": "Timeout in seconds, default 30"}
    },
    risk="high"
)
def run_command(command: str, cwd: str = None, timeout: int = 30) -> Dict:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            cwd=cwd,
            timeout=timeout,
        )
        def _safe_decode(data: bytes) -> str:
            try:
                return data.decode("utf-8", errors="replace")
            except Exception:
                return data.decode("utf-8", errors="replace")

        return {
            "ok": True,
            "returncode": result.returncode,
            "stdout": _safe_decode(result.stdout)[-5000:] if result.stdout else "",
            "stderr": _safe_decode(result.stderr)[-2000:] if result.stderr else "",
            "command": command
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Command timed out ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="run_python",
    description="Execute Python code snippet, return output results",
    parameters={
        "code": {"type": "string", "description": "Python code", "required": True},
        "cwd": {"type": "string", "description": "Working directory"}
    },
    risk="high"
)
def run_python(code: str, cwd: str = None) -> Dict:
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py',
                                         delete=False, encoding='utf-8') as f:
            f.write(code)
        tmp_path = f.name
        # Windows compat: prefer python3, fallback python
        python_cmd = "python3" if shutil.which("python3") else "python"
        result = subprocess.run(
            [python_cmd, tmp_path],
            capture_output=True,
            cwd=cwd, timeout=30,
        )
        os.unlink(tmp_path)
        # Manual decode: prefer utf-8, fallback to system default encoding
        def _safe_decode(data: bytes) -> str:
            try:
                return data.decode("utf-8", errors="replace")
            except Exception:
                return data.decode("utf-8", errors="replace")

        success = result.returncode == 0
        return {
            "ok": success,
            "returncode": result.returncode,
            "stdout": _safe_decode(result.stdout)[-5000:] if result.stdout else "",
            "stderr": _safe_decode(result.stderr)[-2000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return {"ok": False, "error": "Script execution timed out (30s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# Network Tools
# ═══════════════════════════════════════════════════

@register_tool(
    name="web_search",
    description="Search the web. Prefers DuckDuckGo, automatically falls back to Bing search on failure",
    parameters={
        "query": {"type": "string", "description": "Search keyword", "required": True},
        "max_results": {"type": "integer", "description": "Maximum results, default 5"}
    },
    risk="low"
)
def web_search(query: str, max_results: int = 5) -> Dict:
    """Search the web, multi-engine fallback"""

    # -- Method 1: DuckDuckGo Instant Answer API --
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())

        results = []
        if data.get("AbstractText"):
            results.append({
                "title":   data.get("Heading", "Summary"),
                "snippet": data["AbstractText"][:500],
                "url":     data.get("AbstractURL", "")
            })
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title":   topic.get("FirstURL", "").split("/")[-1].replace("_", " "),
                    "snippet": topic["Text"][:300],
                    "url":     topic.get("FirstURL", "")
                })
        if results:
            return {"ok": True, "engine": "DuckDuckGo",
                    "query": query, "results": results[:max_results]}
    except Exception:
        pass   # Silent failure, try fallback

    # -- Method 2: Bing search (scrape results page) --
    try:
        import re
        encoded = urllib.parse.quote(query)
        url = f"https://www.bing.com/search?q={encoded}&count={max_results}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9"
            }
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Extract search results
        results = []
        # Match Bing result titles and links
        titles   = re.findall(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html)
        snippets = re.findall(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', html)

        for i, (href, title) in enumerate(titles[:max_results]):
            title_clean   = re.sub(r'<[^>]+>', '', title).strip()
            snippet_clean = re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
            if title_clean and not href.startswith("javascript"):
                results.append({
                    "title":   title_clean,
                    "snippet": snippet_clean[:300],
                    "url":     href
                })

        if results:
            return {"ok": True, "engine": "Bing",
                    "query": query, "results": results}
        else:
            return {"ok": True, "engine": "Bing", "query": query,
                    "results": [], "note": "No results parsed, suggest using fetch_url to directly access"}

    except Exception as e:
        return {"ok": False,
                "error": f"Search failed (both DuckDuckGo and Bing unavailable): {e}",
                "tip": "Suggest using fetch_url tool to directly access the target webpage"}


@register_tool(
    name="fetch_url",
    description="Fetch web page content (plain text) from a specified URL. Results returned directly in content field, no files written, use returned data directly",
    parameters={
        "url": {"type": "string", "description": "Target URL", "required": True},
        "max_chars": {"type": "integer", "description": "Maximum characters, default 8000"}
    },
    risk="low"
)
def fetch_url(url: str, max_chars: int = 8000) -> Dict:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AGI-System/1.0)",
                "Accept": "text/html,application/xhtml+xml"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            html = raw.decode(charset, errors="replace")

        # Simple HTML tag removal
        import re
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        return {
            "ok": True,
            "url": url,
            "content": text[:max_chars],
            "total_length": len(text)
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="read_article",
    description="Extract article body, title, author, publish date and other metadata from news/article URLs. Smarter than fetch_url, automatically removes ads and navigation bars, keeping only the article body",
    parameters={
        "url": {"type": "string", "description": "Article URL", "required": True},
        "max_chars": {"type": "integer", "description": "Maximum body characters, default 5000"}
    },
    risk="low"
)
def read_article(url: str, max_chars: int = 5000) -> Dict:
    try:
        import sys
        import concurrent.futures
        from newspaper import Article

        def _download_with_timeout(article, timeout=10):
            if sys.platform != "win32":
                import signal
                def _handler(signum, frame):
                    raise TimeoutError("Download timed out")
                signal.signal(signal.SIGALRM, _handler)
                signal.alarm(timeout)
                try:
                    article.download()
                    article.parse()
                finally:
                    signal.alarm(0)
            else:
                def _do():
                    article.download()
                    article.parse()
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(_do)
                    future.result(timeout=timeout)

        # Ensure NLTK tokenizer resources are available (auto-download on first use)
        try:
            import nltk
            nltk.data.find('tokenizers/punkt_tab')
        except (ImportError, LookupError):
            try:
                import nltk
                nltk.download('punkt_tab', quiet=True)
                nltk.download('punkt', quiet=True)
            except Exception:
                pass

        article = Article(url, language="zh")
        try:
            _download_with_timeout(article, timeout=10)
        except (TimeoutError, concurrent.futures.TimeoutError):
            return {"ok": False, "error": "Page download timed out (10s)"}

        try:
            article.nlp()
        except Exception:
            pass  # NLP failure does not affect text extraction

        result = {
            "ok": True,
            "url": url,
            "title": article.title or "",
            "authors": article.authors or [],
            "publish_date": str(article.publish_date) if article.publish_date else "",
            "top_image": article.top_image or "",
            "keywords": article.keywords or [],
            "summary": article.summary or "",
            "text": (article.text or "")[:max_chars],
            "text_length": len(article.text or ""),
        }
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# System Control Tools (requires additional dependencies)
# ═══════════════════════════════════════════════════

@register_tool(
    name="screenshot",
    description="Take current screen screenshot, return base64 encoded image. Requires pyautogui",
    parameters={
        "region": {"type": "string", "description": "Screenshot region 'x,y,w,h', full screen if not specified"}
    },
    risk="low"
)
def screenshot(region: str = None) -> Dict:
    try:
        import pyautogui
        from PIL import Image
        import io

        if region:
            x, y, w, h = map(int, region.split(","))
            img = pyautogui.screenshot(region=(x, y, w, h))
        else:
            img = pyautogui.screenshot()

        # Compress then convert to base64
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return {"ok": True, "image_base64": b64,
                "size": f"{img.width}x{img.height}"}
    except ImportError:
        return {"ok": False, "error": "Requires installation: pip install pyautogui pillow"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="mouse_click",
    description="Click at specified screen position. Requires pyautogui",
    parameters={
        "x": {"type": "integer", "description": "X coordinate", "required": True},
        "y": {"type": "integer", "description": "Y coordinate", "required": True},
        "button": {"type": "string", "description": "left/right/middle, default left"},
        "clicks": {"type": "integer", "description": "Number of clicks, default 1"}
    },
    risk="high"
)
def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> Dict:
    try:
        import pyautogui
        pyautogui.click(x, y, button=button, clicks=clicks)
        return {"ok": True, "action": f"Clicked ({x},{y}) {button} {clicks} time(s)"}
    except ImportError:
        return {"ok": False, "error": "Requires installation: pip install pyautogui"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="keyboard_type",
    description="Simulate keyboard typing or key presses. Requires pyautogui",
    parameters={
        "text": {"type": "string", "description": "Text to type"},
        "hotkey": {"type": "string", "description": "Hotkey combo, e.g. 'ctrl,c' or 'alt,tab'"}
    },
    risk="high"
)
def keyboard_type(text: str = None, hotkey: str = None) -> Dict:
    try:
        import pyautogui
        import time
        if hotkey:
            keys = [k.strip() for k in hotkey.split(",")]
            pyautogui.hotkey(*keys)
            return {"ok": True, "action": f"Pressed key {hotkey}"}
        elif text:
            pyautogui.typewrite(text, interval=0.03)
            return {"ok": True, "action": f"Typed text ({len(text)} chars)"}
        else:
            return {"ok": False, "error": "Must provide either text or hotkey"}
    except ImportError:
        return {"ok": False, "error": "Requires installation: pip install pyautogui"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="open_application",
    description="Open an application or file",
    parameters={
        "target": {"type": "string", "description": "Application name or file path", "required": True}
    },
    risk="medium"
)
def open_application(target: str) -> Dict:
    try:
        import platform
        system = platform.system()
        if system == "Darwin":      # macOS
            subprocess.Popen(["open", target])
        elif system == "Windows":
            os.startfile(target)
        else:                        # Linux
            subprocess.Popen(["xdg-open", target])
        return {"ok": True, "opened": target}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="browser_action",
    description="Control browser: open URL, get page content, click elements. Requires playwright",
    parameters={
        "action": {"type": "string",
                   "description": "Action type: open_url / get_text / click_text / fill_input / get_screenshot",
                   "required": True},
        "url": {"type": "string", "description": "Target URL (required for open_url)"},
        "selector": {"type": "string", "description": "CSS selector or text content"},
        "value": {"type": "string", "description": "Content to fill (for fill_input)"}
    },
    risk="medium"
)
def browser_action(action: str, url: str = None,
                   selector: str = None, value: str = None) -> Dict:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            if action == "open_url" and url:
                page.goto(url, timeout=15000)
                title = page.title()
                browser.close()
                return {"ok": True, "title": title, "url": url}

            elif action == "get_text" and url:
                page.goto(url, timeout=15000)
                text = page.inner_text("body")[:8000]
                browser.close()
                return {"ok": True, "text": text}

            elif action == "click_text" and selector:
                page.get_by_text(selector).first.click()
                page.wait_for_load_state()
                browser.close()
                return {"ok": True, "clicked": selector}

            elif action == "fill_input" and selector and value:
                page.fill(selector, value)
                browser.close()
                return {"ok": True, "filled": selector}

            elif action == "get_screenshot":
                img_bytes = page.screenshot()
                b64 = base64.b64encode(img_bytes).decode()
                browser.close()
                return {"ok": True, "image_base64": b64}

            browser.close()
            return {"ok": False, "error": f"Unknown action: {action}"}

    except ImportError:
        return {"ok": False,
                "error": "Requires installation: pip install playwright && playwright install chromium"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# System Information Tools
# ═══════════════════════════════════════════════════

@register_tool(
    name="get_system_info",
    description="Get system information: OS, disk, memory, running processes, etc.",
    parameters={
        "info_type": {"type": "string",
                      "description": "os / disk / memory / processes / all, default all"}
    },
    risk="low"
)
def get_system_info(info_type: str = "all") -> Dict:
    import platform
    result = {}
    try:
        if info_type in ("os", "all"):
            result["os"] = {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version()
            }
        if info_type in ("disk", "all"):
            usage = shutil.disk_usage(".")
            result["disk"] = {
                "total_gb": round(usage.total / 1e9, 1),
                "used_gb":  round(usage.used  / 1e9, 1),
                "free_gb":  round(usage.free  / 1e9, 1)
            }
        if info_type in ("processes", "all"):
            ps = subprocess.run(["ps", "aux", "--no-header"],
                                capture_output=True, text=True, timeout=5)
            procs = [l.split()[10] for l in ps.stdout.strip().split("\n")
                     if l.strip()][:20]
            result["processes"] = procs
        return {"ok": True, **result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="read_clipboard",
    description="Read clipboard content",
    parameters={},
    risk="low"
)
def read_clipboard() -> Dict:
    try:
        result = subprocess.run(
            ["pbpaste"] if os.uname().sysname == "Darwin" else ["xclip", "-o"],
            capture_output=True, text=True, timeout=5
        )
        return {"ok": True, "content": result.stdout}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="write_clipboard",
    description="Write content to clipboard",
    parameters={
        "content": {"type": "string", "description": "Content to write to clipboard", "required": True}
    },
    risk="low"
)
def write_clipboard(content: str) -> Dict:
    try:
        import platform
        if platform.system() == "Darwin":
            proc = subprocess.run(["pbcopy"], input=content.encode(), timeout=5)
        else:
            proc = subprocess.run(["xclip", "-selection", "clipboard"],
                                  input=content.encode(), timeout=5)
        return {"ok": True, "written": len(content)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# Financial Data Tools
# ═══════════════════════════════════════════════════

@register_tool(
    name="get_stock_info",
    description="Get real-time stock/fund quotes and basic info, supports A-shares, US stocks, HK stocks, and global markets",
    parameters={
        "symbol": {"type": "string", "description": "Stock symbol, e.g. 600519.SS (Kweichow Moutai), AAPL (Apple), 00700.HK (Tencent)", "required": True},
        "period": {"type": "string", "description": "Query type: info (basic info), quote (real-time quote), history (historical K-line)", "required": False},
        "range": {"type": "string", "description": "Historical K-line range (only effective when period=history): 1d/5d/1mo/3mo/6mo/1y/2y/5y/max, default 1mo"}
    },
    risk="low"
)
def get_stock_info(symbol: str, period: str = "info", range: str = "1mo") -> Dict:
    try:
        import yfinance as yf

        period = period.lower() if period else "info"
        ticker = yf.Ticker(symbol)

        if period == "quote":
            info = ticker.fast_info
            result = {
                "ok": True,
                "symbol": symbol,
                "market_price": getattr(info, "last_price", None),
                "currency": getattr(info, "currency", ""),
                "previous_close": getattr(info, "previous_close", None),
                "open": getattr(info, "open", None),
                "day_high": getattr(info, "day_high", None),
                "day_low": getattr(info, "day_low", None),
                "volume": getattr(info, "last_volume", None),
            }
            # Filter out None values
            result = {k: v for k, v in result.items() if v is not None}

        elif period == "history":
            hist = ticker.history(period=range)
            if hist.empty:
                return {"ok": False, "error": f"Failed to get historical data for {symbol}"}
            records = []
            for idx, row in hist.iterrows():
                records.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "open": round(row.get("Open", 0), 2),
                    "high": round(row.get("High", 0), 2),
                    "low": round(row.get("Low", 0), 2),
                    "close": round(row.get("Close", 0), 2),
                    "volume": int(row.get("Volume", 0)),
                })
            result = {
                "ok": True,
                "symbol": symbol,
                "range": range,
                "count": len(records),
                "records": records,
                "latest": records[-1] if records else None,
            }

        else:
            # Basic info
            info = ticker.info
            result = {
                "ok": True,
                "symbol": symbol,
                "name": info.get("shortName") or info.get("longName", ""),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "market": info.get("market", ""),
                "currency": info.get("currency", ""),
                "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "previous_close": info.get("previousClose"),
                "open": info.get("regularMarketOpen"),
                "day_high": info.get("dayHigh") or info.get("regularMarketDayHigh"),
                "day_low": info.get("dayLow") or info.get("regularMarketDayLow"),
                "volume": info.get("volume") or info.get("regularMarketVolume"),
                "market_cap": info.get("marketCap"),
                "pe_ratio": info.get("trailingPE"),
                "dividend_yield": info.get("dividendYield"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "summary": info.get("longBusinessSummary", "")[:500] if info.get("longBusinessSummary") else "",
            }
            result = {k: v for k, v in result.items() if v is not None}

        return result

    except ImportError:
        return {"ok": False, "error": "Requires installation: pip install yfinance"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="search_stock",
    description="Search stock symbols, fuzzy match stock names or codes by keyword",
    parameters={
        "keyword": {"type": "string", "description": "Search keyword, e.g. Apple, Tencent, TSLA", "required": True},
        "max_results": {"type": "integer", "description": "Max results, default 5"}
    },
    risk="low"
)
def search_stock(keyword: str, max_results: int = 5) -> Dict:
    try:
        import yfinance as yf

        results = yf.Search(keyword, max_results=max_results)
        quotes = []
        for q in getattr(results, "quotes", [])[:max_results]:
            quotes.append({
                "symbol": q.get("symbol", ""),
                "name": q.get("shortname") or q.get("longname", ""),
                "type": q.get("quoteType", ""),
                "exchange": q.get("exchange", ""),
                "market": q.get("market", ""),
            })
        news = []
        for n in getattr(results, "news", [])[:5]:
            news.append({
                "title": n.get("title", ""),
                "publisher": n.get("publisher", ""),
                "link": n.get("link", ""),
            })

        return {
            "ok": True,
            "keyword": keyword,
            "quotes": quotes,
            "news": news,
            "tip": f"Found {len(quotes)} results, use get_stock_info for detailed info"
        }

    except ImportError:
        return {"ok": False, "error": "Requires installation: pip install yfinance"}
    except Exception as e:
        # Older yfinance versions lack Search, provide manual tip
        return {
            "ok": False,
            "error": str(e),
            "tip": "Please ensure yfinance version >= 0.2.31: pip install --upgrade yfinance"
        }


# ═══════════════════════════════════════════════════
# News Tools
# ═══════════════════════════════════════════════════

def _get_newsapi_key(api_key: str = "") -> str:
    """Get NewsAPI key, prefer passed value, fallback to system config and env vars"""
    if api_key:
        return api_key
    try:
        from desktop.config import load_config
        cfg = load_config()
        key = cfg.get("newsapi_key", "")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("NEWSAPI_KEY", "")


@register_tool(
    name="get_news",
    description="Get latest news, supports keyword, source, country, category search. Requires NewsAPI Key (configure newsapi_key in settings)",
    parameters={
        "keyword":     {"type": "string", "description": "Search keyword, e.g. AI, Apple, tech"},
        "category":    {"type": "string", "description": "News category: general/business/entertainment/health/science/sports/technology"},
        "country":     {"type": "string", "description": "Country code, e.g. us (USA), jp (Japan), default us (free tier does not support cn)"},
        "page_size":   {"type": "integer", "description": "Number of results, default 5, max 100"},
        "api_key":     {"type": "string", "description": "NewsAPI Key (optional, uses system config if not specified)"}
    },
    risk="low"
)
def get_news(keyword: str = "", category: str = "", country: str = "us",
             page_size: int = 5, api_key: str = "") -> Dict:
    try:
        from newsapi import NewsApiClient

        key = _get_newsapi_key(api_key)
        if not key:
            return {
                "ok": False,
                "error": "NewsAPI Key not configured",
                "tip": "Please fill in newsapi_key in settings, or set NEWSAPI_KEY environment variable. "
                      "Free registration: https://newsapi.org/register"
            }

        client = NewsApiClient(api_key=key)

        if keyword:
            # get_everything searches by keyword full-text, does not support category param
            articles = client.get_everything(
                q=keyword,
                language="zh" if country in ("cn", "tw", "hk") else "en",
                page_size=min(page_size, 100),
                sort_by="publishedAt"
            )
        else:
            # get_top_headlines supports getting headlines by category + country
            articles = client.get_top_headlines(
                category=category or None,
                country=country,
                page_size=min(page_size, 100)
            )

        if articles.get("status") != "ok":
            return {"ok": False, "error": articles.get("message", "Request failed")}

        total = articles.get("totalResults", 0)
        items = []
        for a in articles.get("articles", [])[:page_size]:
            items.append({
                "title":       a.get("title", ""),
                "source":      a.get("source", {}).get("name", ""),
                "author":      a.get("author", ""),
                "published_at": a.get("publishedAt", ""),
                "description": (a.get("description", "") or "")[:200],
                "url":         a.get("url", ""),
                "url_to_image": a.get("urlToImage", ""),
            })

        return {
            "ok": True,
            "total": total,
            "count": len(items),
            "keyword": keyword,
            "category": category,
            "country": country,
            "articles": items,
        }

    except ImportError:
        return {"ok": False, "error": "Requires installation: pip install newsapi-python"}
    except Exception as e:
        err_msg = str(e)
        # Common error friendly messages
        if "apiKey" in err_msg or "API key" in err_msg:
            return {"ok": False, "error": "NewsAPI Key invalid or not configured",
                    "tip": "Please check newsapi_key in settings. Free registration: https://newsapi.org/register"}
        return {"ok": False, "error": err_msg}


@register_tool(
    name="get_news_sources",
    description="Get NewsAPI supported news source list, filterable by country, language, category",
    parameters={
        "country":   {"type": "string", "description": "Country code, e.g. cn, us"},
        "language":  {"type": "string", "description": "Language code, e.g. zh, en"},
        "category":  {"type": "string", "description": "Category: general/business/entertainment/health/science/sports/technology"}
    },
    risk="low"
)
def get_news_sources(country: str = "", language: str = "", category: str = "") -> Dict:
    try:
        from newsapi import NewsApiClient

        key = _get_newsapi_key()
        if not key:
            return {
                "ok": False,
                "error": "NewsAPI Key not configured",
                "tip": "Please fill in newsapi_key in settings. Free signup: https://newsapi.org/register"
            }

        client = NewsApiClient(api_key=key)

        kwargs = {}
        if country:
            kwargs["country"] = country
        if language:
            kwargs["language"] = language
        if category:
            kwargs["category"] = category

        result = client.get_sources(**kwargs)

        if result.get("status") != "ok":
            return {"ok": False, "error": result.get("message", "Request failed")}

        sources = []
        for s in result.get("sources", [])[:50]:
            sources.append({
                "id":       s.get("id", ""),
                "name":     s.get("name", ""),
                "category": s.get("category", ""),
                "language": s.get("language", ""),
                "country":  s.get("country", ""),
                "url":      s.get("url", ""),
                "description": (s.get("description", "") or "")[:100],
            })

        return {
            "ok": True,
            "total": len(sources),
            "sources": sources,
        }

    except ImportError:
        return {"ok": False, "error": "Requires installation: pip install newsapi-python"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# Image Generation Tool (pollinations.ai, free, no API Key needed)
# ═══════════════════════════════════════════════════

@register_tool(
    name="generate_image",
    description=(
        "Online image generation (pollinations.ai), average quality, slow (10-30s)."
        "Only use as fallback when generate_image_comfy is unavailable. Prefer generate_image_comfy."
    ),
    parameters={
        "prompt": {"type": "string", "description": "English image description, e.g. 'a cat sitting on a rainbow, digital art'", "required": True},
        "width": {"type": "integer", "description": "Image width (pixels), default 1024"},
        "height": {"type": "integer", "description": "Image height (pixels), default 1024"},
        "use_simlife_scene": {"type": "boolean", "description": "Whether to use SimLife current scene as background (set true for photo/selfie), default false"},
    },
    risk="low"
)
def generate_image(prompt: str, width: int = 1024, height: int = 1024, use_simlife_scene: bool = False) -> Dict:
    try:
        from engine.image_gen import generate_image_url, download_image, get_image_dir
        from pathlib import Path
        from datetime import datetime
        import uuid

        # If SimLife scene is requested, try to get current state and merge into prompt
        if use_simlife_scene:
            try:
                from engine.simlife_client import SimLifeClient
                _sl = SimLifeClient()
                sl_ctx = _sl.format_for_prompt()
                if sl_ctx:
                    prompt = f"{prompt}, based on current life scene context"
            except Exception:
                pass

        url = generate_image_url(prompt, width=width, height=height)
        filename = f"tool_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        save_path = str(get_image_dir() / filename)

        image_path = download_image(url, save_path)
        if image_path:
            return {
                "ok": True,
                "image_path": image_path,
                "prompt": prompt,
                "size": f"{width}x{height}",
                "message": f"Image generated and saved to: {image_path}"
            }
        else:
            return {"ok": False, "error": "Image generation or download failed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# Image Generation Tool (ComfyUI local backend, high quality SDXL)
# ═══════════════════════════════════════════════════

_WORKFLOW_JSON = str(Path(__file__).parent.parent / "workflow_api.json")


def _load_comfyui_config() -> dict:
    """Read ComfyUI config from config.json, use defaults if not configured"""
    try:
        from desktop.config import load_config
        cfg = load_config()
    except Exception:
        cfg = {}
    return {
        "url": cfg.get("comfyui_url", "http://127.0.0.1:8188"),
        "output_dir": cfg.get("comfyui_output", ""),
        "style": cfg.get("comfyui_style", ""),
    }


# Style keyword presets (prepended to prompt for highest weight)
_STYLE_PREFIX = {
    "anime": "pixiv",
    "realistic": "photorealistic, 8k uhd, dslr, soft lighting, high quality",
}

# Style conflict word list: when config sets a style, remove opposing style words from prompt
_STYLE_CONFLICTS = {
    "anime": [
        "real", "realistic", "real_photo", "real photo", "photorealistic", "photograph",
        "photo", "dslr", "8k uhd", "camera", "raw photo", "hyperrealistic",
    ],
    "realistic": [
        "anime", "illustration", "pixiv", "manga", "cartoon", "2d", "cel shading",
        "anime style", "ukiyo", "visual novel",
    ],
}


def _clean_style_conflicts(prompt: str, style: str) -> str:
    """Remove words from prompt that conflict with the current style setting"""
    import re as _re
    conflicts = _STYLE_CONFLICTS.get(style, [])
    removed = []
    for word in conflicts:
        pattern = rf'\b{_re.escape(word)}\b[_\s]?,?\s*'
        new_prompt = _re.sub(pattern, '', prompt, flags=_re.IGNORECASE)
        if new_prompt != prompt:
            removed.append(word)
            prompt = new_prompt
    if removed:
        print(f"[ComfyUI] Removed style-conflicting words for {style}: {', '.join(removed)}")
    return prompt


def _get_style_prefix() -> str:
    """Return style prefix based on comfyui_style in config.json"""
    style = _load_comfyui_config()["style"]
    return _STYLE_PREFIX.get(style, "")


def _comfyui_url() -> str:
    return _load_comfyui_config()["url"]


def _comfyui_output_dir() -> str:
    cfg = _load_comfyui_config()
    if cfg["output_dir"]:
        return cfg["output_dir"]
    # Fallback: try common paths
    import sys
    if sys.platform == "win32":
        candidates = [r"D:\ComfyUI_windows_portable\ComfyUI\output",
                       r"C:\ComfyUI_windows_portable\ComfyUI\output"]
    else:
        candidates = [str(Path.home() / "ComfyUI" / "output")]
    for c in candidates:
        if Path(c).is_dir():
            return c
    return candidates[0]


def _parse_comfy_workflow() -> Optional[Dict]:
    """
    Load and parse workflow_api.json, auto-locate key nodes.
    Returns dict: {positive_node_id, negative_node_id, sampler_node_id, seed_node_id, output_node_id, workflow}
    """
    try:
        with open(_WORKFLOW_JSON, "r", encoding="utf-8") as f:
            workflow = json.load(f)
    except Exception as e:
        print(f"[ComfyUI] Unable to load workflow: {e}")
        return None

    # Auto-locate KSampler node
    sampler_id = None
    for nid, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") in ("KSampler", "KSamplerAdvanced"):
            sampler_id = nid
            break

    if not sampler_id:
        print("[ComfyUI] KSampler node not found in workflow")
        return None

    sampler_inputs = workflow[sampler_id].get("inputs", {})
    positive_id = str(sampler_inputs.get("positive", [None])[0])
    negative_id = str(sampler_inputs.get("negative", [None])[0])

    # Find SaveImage node (output)
    output_id = None
    for nid, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == "SaveImage":
            output_id = nid
            break

    return {
        "positive_id": str(positive_id),
        "negative_id": str(negative_id),
        "sampler_id": sampler_id,
        "output_id": output_id,
        "workflow": workflow,
    }


def _check_comfyui_alive() -> bool:
    """Check if ComfyUI is online"""
    try:
        import requests
        resp = requests.get(f"{_comfyui_url()}/system_stats", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _wait_for_comfyui(prompt_id: str, timeout: int = 120) -> Optional[str]:
    """
    Poll ComfyUI until generation is complete.
    Returns output image filename (e.g. ComfyUI_00001_.png), or None on timeout.
    """
    import requests, time

    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{_comfyui_url()}/history/{prompt_id}", timeout=5)
            if resp.status_code == 200:
                history = resp.json()
                if prompt_id in history:
                    outputs = history[prompt_id].get("outputs", {})
                    for node_id, node_out in outputs.items():
                        if "images" in node_out and node_out["images"]:
                            return node_out["images"][0].get("filename")
        except Exception:
            pass
        time.sleep(2)

    return None


@register_tool(
    name="generate_image_comfy",
    description=(
        "Primary image generation tool. Uses local ComfyUI to generate images,"
        "suitable for selfies, scene sharing, and various style images."
        "Use this tool when the user wants to see your appearance, environment, or surroundings."
        "Note: if the user asks to see scenery, landscape, or environment, do not add people to the prompt,"
        "and set the no_human parameter to true."
        "When the user explicitly clicks the tool panel to draw (tool name in dialogue), the system will auto-skip character trait injection."
        "When you are taking a photo or landscape shot yourself (e.g. 'let me see you' 'take a photo'), just generate the prompt and the system will auto-inject your character traits."
    ),
    parameters={
        "prompt": {
            "type": "string",
            "description": "English image description in comma-separated tag/keyword format. Count: 1girl/1boy/2girls etc. Composition: solo/full body/upper body etc. Clothing/appearance: white_shirt/black_dress/long_hair etc. Example: '1girl, solo, full body, long_hair, white_shirt, standing, indoors, cafe, warm_lighting'",
            "required": True,
        },
        "negative_prompt": {
            "type": "string",
            "description": "Negative prompt (content to exclude), uses workflow default if not specified",
            "required": False,
        },
        "no_human": {
            "type": "boolean",
            "description": "Whether to generate pure landscape/still life (no people). Set true when user asks to see scenery, landscape, environment, food, objects - do not add 1girl/1boy/solo etc. person tags to prompt",
            "required": False,
        },
        "width": {
            "type": "integer",
            "description": "Image width (pixels), e.g. 512/768/1024. Auto-inferred from prompt if not specified (portrait 768/landscape 1024/default 832)",
            "required": False,
        },
        "height": {
            "type": "integer",
            "description": "Image height (pixels), e.g. 512/768/1024. Auto-inferred from prompt if not specified (portrait 1024/landscape 768/default 832)",
            "required": False,
        },
    },
    risk="medium",
)
def generate_image_comfy(prompt: str, negative_prompt: str = "", no_human: bool = False,
                         width: int = 0, height: int = 0) -> Dict:
    try:
        import requests
        import random
        import shutil
        from datetime import datetime
        from engine.image_gen import get_image_dir

        # 0.0 Determine whether to skip character trait injection
        # Strategy: if user raw message contains "generate_image_comfy" (clicked tool panel) -> skip injection
        #       Other cases (system autonomous call, natural dialogue trigger) -> inject character traits
        _raw_input = getattr(generate_image_comfy, '_user_raw_input', '') or ''
        no_inject = 'generate_image_comfy' in _raw_input
        # Compat: LLM may also add [NO_INJECT], only effective when user message also matches
        if not no_inject and prompt.strip().startswith("[NO_INJECT]"):
            prompt = prompt.strip().replace("[NO_INJECT]", "").strip().lstrip(",").strip()
            # LLM unilaterally added [NO_INJECT], user message has no tool name, ignore
        if no_inject:
            prompt = prompt.strip().replace("[NO_INJECT]", "").strip().lstrip(",").strip()
            print(f"[ComfyUI] User explicitly specified drawing, skipping character trait injection")
        else:
            print(f"[ComfyUI] System autonomous call, will inject character traits")

        # 0.0.1 Style conflict cleanup (anime <-> realistic, needed for both modes)
        _current_style = _load_comfyui_config()["style"]
        if _current_style in _STYLE_CONFLICTS:
            prompt = _clean_style_conflicts(prompt, _current_style)

        if no_inject:
            # User-specified drawing: only inject style prefix, skip all character trait injection
            style_prefix = _get_style_prefix()
            if style_prefix:
                prompt = f"{style_prefix}, {prompt}"
                print(f"[ComfyUI] User-specified mode, only injecting style prefix: {style_prefix}")
            else:
                print(f"[ComfyUI] User-specified mode, no style prefix, using original prompt")
            prompt_lower = prompt.lower()
        else:
            # 0. Inject style prefix (anime/realistic, highest weight placed first)
            style_prefix = _get_style_prefix()
            if style_prefix:
                prompt = f"{style_prefix}, {prompt}"
                print(f"[ComfyUI] Injected style prefix: {style_prefix}")

            # 0.1 Determine if person subject is present (no avatar/clothing injection for pure landscape/still life)
            prompt_lower = prompt.lower()
            # no_human parameter takes priority (tool description guides LLM to set true for landscapes)
            if no_human:
                has_person = False
                # Remove common person tags from prompt
                import re as _re
                _human_tags_remove = ("1girl", "1boy", "2girls", "2boys", "3girls", "3boys",
                                      "solo", "girl", "boy", "woman", "man",
                                      "selfie", "portrait", "looking at viewer")
                for tag in _human_tags_remove:
                    prompt = _re.sub(rf'\b{_re.escape(tag)}\b\s*,?\s*', '', prompt, flags=_re.IGNORECASE)
                prompt_lower = prompt.lower()
                print(f"[ComfyUI] no_human=true, cleared person tags")
            else:
                _person_indicators = (
                    # Person terms
                    "1girl", "1boy", "2girls", "2boys", "3girls", "3boys",
                    "solo", "duo", "trio",
                    "girl", "boy", "woman", "man", "person", "people",
                    "child", "kid", "teen", "elder", "lady", "gentleman",
                    # Body parts
                    "hair", "eye", "eyes", "face", "skin", "hand", "hands",
                    "smile", "expression", "lips", "mouth", "body",
                    # Actions/poses
                    "sitting", "standing", "walking", "lying", "looking",
                    "selfie", "portrait", "upper body", "full body",
                    # Explicitly no people
                    "no humans",
                )
                has_person = any(tag in prompt_lower for tag in _person_indicators)
                if "no humans" in prompt_lower:
                    has_person = False

            # 0.5 Inject character appearance traits (nationality/hair/eye/size, only when person present)
            # Read structured fields from CharacterCard, not free text from personality.json
            if has_person:
                try:
                    from engine.simlife_client import SimLifeClient
                    _sl = SimLifeClient()
                    character = _sl._read_character()
                    if character:
                        basic = character.get("basic", {})
                        parts = []
                        nationality = basic.get("nationality", "").strip()
                        hair = basic.get("hair_color", "").strip()
                        eyes = basic.get("eye_color", "").strip()
                        body = basic.get("body_type", "").strip()
                        if nationality:
                            parts.append(f"{nationality} girl")
                        if hair:
                            parts.append(f"{hair} hair")
                        if eyes:
                            parts.append(f"{eyes} eyes")
                        if body:
                            parts.append(body)
                        if parts:
                            avatar_desc = ", ".join(parts)
                            if avatar_desc.lower() not in prompt_lower:
                                prompt = f"{avatar_desc}, {prompt}"
                                print(f"[ComfyUI] Injected character appearance: {avatar_desc}")
                            else:
                                print(f"[ComfyUI] Character appearance already in prompt, skipping injection")
                        else:
                            print(f"[ComfyUI] No appearance fields in character card (nationality/hair_color/eye_color/body_type)")
                    else:
                        # Fallback: read avatar_prompt from personality.json
                        from desktop.config import PERSONALITY_FILE
                        import json
                        if Path(PERSONALITY_FILE).exists():
                            personality = json.loads(Path(PERSONALITY_FILE).read_text(encoding="utf-8"))
                            avatar = personality.get("avatar_prompt", "").strip()
                            if avatar and avatar.lower() not in prompt_lower:
                                prompt = f"{avatar}, {prompt}"
                                print(f"[ComfyUI] Fallback: injected avatar_prompt: {avatar[:60]}...")
                except Exception as e:
                    print(f"[ComfyUI] Character appearance injection failed: {e}")
            else:
                print(f"[ComfyUI] Detected as landscape/still life, skipping appearance injection")

            # 0.6 Clothing decided by Layer A LLM in prompt, backend no longer injects SimLife clothing

            # 0.7 Random pose/angle (avoid static images, different pools for person vs landscape)
            _pose_angles_person = [
                "hand on hip", "leaning forward", "arms crossed", "head tilt", "looking away",
                "stretching", "adjusting hair", "hand resting on chin", "turning back",
                "dynamic pose", "looking up", "looking down", "side glance",
                "from side", "from above", "dutch angle", "over shoulder shot",
                "cowboy shot", "headshot", "close-up on face",
            ]
            _pose_angles_scene = [
                "wide angle", "bird eye view", "low angle shot", "aerial view",
                "golden hour lighting", "rule of thirds", "dramatic sky",
                "leading lines", "depth of field", "long shot", "panoramic",
                "vibrant colors", "soft focus", "sunrise", "sunset glow",
            ]
            _pool = _pose_angles_person if has_person else _pose_angles_scene
            _angle_blacklist = ("from above", "from below", "wide angle", "close-up", "low angle")
            _has_angle = any(a in prompt_lower for a in _angle_blacklist)
            if not _has_angle:
                import random
                _chosen = random.sample(_pool, min(2, len(_pool)))
                _angle_str = ", ".join(_chosen)
                prompt = f"{prompt}, {_angle_str}"
                print(f"[ComfyUI] Appended pose/angle: {_angle_str}")

            # 0.6 Inject travel destination info (in travel blogger mode, add current city scene description)
            try:
                from engine.simlife_client import SimLifeClient
                _sl = SimLifeClient()
                character = _sl._read_character()
                if character:
                    ws = character.get("basic", {}).get("work_style", "")
                    if ws == "travel":
                        from datetime import date
                        plan = character.get("travel_plan", {})
                        if plan and plan.get("enabled"):
                            today = date.today()
                            for dest in plan.get("destinations", []):
                                start = dest.get("start_date", "")
                                end = dest.get("end_date", "")
                                if start and end:
                                    try:
                                        if date.fromisoformat(start) <= today <= date.fromisoformat(end):
                                            city_en = dest.get("city_en", dest.get("city", ""))
                                            country = dest.get("country", "")
                                            location_hint = f"in {city_en}"
                                            if country:
                                                location_hint = f"in {city_en}, {country}"
                                            if location_hint.lower() not in prompt.lower():
                                                prompt = f"{prompt}, {location_hint}"
                                                print(f"[ComfyUI] Injected travel destination: {location_hint}")
                                            break
                                    except (ValueError, TypeError):
                                        continue
            except Exception as e:
                print(f"[ComfyUI] Travel destination injection failed: {e}")

        # 1. Parse workflow
        parsed = _parse_comfy_workflow()
        if not parsed:
            return {"ok": False, "error": "Unable to load workflow_api.json, please confirm the file exists in the project root directory"}

        workflow = parsed["workflow"]

        # 1.1 Resolution: user-specified first, otherwise auto-infer (portrait / landscape / default square)
        if width > 0 and height > 0:
            _w, _h = width, height
            print(f"[ComfyUI] User-specified resolution: {_w}x{_h}")
        else:
            _portrait_tags = (
                "selfie", "portrait", "full body", "upper body", "headshot",
                "cowboy shot", "bust shot", "standing",
            )
            _landscape_tags = (
                "landscape", "scenery", "cityscape", "panorama", "panoramic",
                "aerial view", "bird eye", "skyline", "horizon",
                "mountain", "ocean", "sea", "river", "field", "forest",
                "view from window", "sunset", "sunrise",
            )
            is_portrait = any(t in prompt_lower for t in _portrait_tags)
            is_landscape = any(t in prompt_lower for t in _landscape_tags)
            if is_portrait and not is_landscape:
                _w, _h = 768, 1024
            elif is_landscape and not is_portrait:
                _w, _h = 1024, 768
            else:
                _w, _h = 832, 832
        # Find EmptyLatentImage node and modify resolution
        for nid, node in workflow.items():
            if isinstance(node, dict) and node.get("class_type") == "EmptyLatentImage":
                node["inputs"]["width"] = _w
                node["inputs"]["height"] = _h
                print(f"[ComfyUI] Resolution: {_w}x{_h}")
                break

        # 2. Replace positive prompt
        workflow[parsed["positive_id"]]["inputs"]["text"] = prompt

        # 3. Replace negative prompt (if provided)
        if negative_prompt.strip():
            workflow[parsed["negative_id"]]["inputs"]["text"] = negative_prompt

        # 4. Random seed
        workflow[parsed["sampler_id"]]["inputs"]["seed"] = random.randint(1, 10**12)

        # 5. Send job to ComfyUI
        resp = requests.post(
            f"{_comfyui_url()}/prompt",
            json={"prompt": workflow},
            timeout=10,
        )
        result = resp.json()

        if "error" in result:
            return {"ok": False, "error": f"ComfyUI returned error: {result['error'].get('message', result['error'])}"}

        prompt_id = result.get("prompt_id")
        if not prompt_id:
            return {"ok": False, "error": "ComfyUI did not return prompt_id"}

        # 6. Poll and wait for generation to complete (max 120 seconds)
        output_filename = _wait_for_comfyui(prompt_id, timeout=120)
        if not output_filename:
            return {"ok": False, "error": "Image generation timed out (120s), ComfyUI may be stuck"}

        # 7. Copy from ComfyUI output dir to AGI images dir
        comfy_output = Path(_comfyui_output_dir()) / output_filename
        if not comfy_output.exists():
            return {"ok": False, "error": f"Generation complete but image not found: {comfy_output}"}

        dest_dir = get_image_dir()
        dest_path = dest_dir / f"comfy_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{output_filename}"
        shutil.copy2(str(comfy_output), str(dest_path))

        size_kb = dest_path.stat().st_size // 1024
        print(f"[ComfyUI] Image saved: {dest_path} ({size_kb}KB)")
        return {
            "ok": True,
            "image_path": str(dest_path),
            "prompt": prompt,
            "size": f"{size_kb}KB",
            "message": f"ComfyUI image generated and saved to: {dest_path} ({size_kb}KB)",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# Everything full-disk search tool (Windows)
# ═══════════════════════════════════════════════════

_ES_PATHS = [
    r"C:\Program Files\Everything\es.exe",
    r"C:\Program Files (x86)\Everything\es.exe",
    str(Path(__file__).parent / "es.exe"),
]
_es_exe_cache: Optional[str] = None


def _find_es_exe() -> Optional[str]:
    """Find es.exe path (cache result, cache empty string on failure)"""
    global _es_exe_cache
    if _es_exe_cache is not None:
        return _es_exe_cache

    # 1. Find in PATH
    es_in_path = shutil.which("es")
    if es_in_path:
        _es_exe_cache = es_in_path
        return _es_exe_cache

    # 2. Check fixed paths
    for p in _ES_PATHS:
        if os.path.isfile(p):
            _es_exe_cache = p
            return _es_exe_cache

    # 3. Cache failure result
    _es_exe_cache = ""
    return ""


def _reset_es_cache():
    """Reset es.exe search cache (call after installing es.exe)"""
    global _es_exe_cache
    _es_exe_cache = None


@register_tool(
    name="everything_search",
    description=(
        "Ultra-fast full-disk file search using Everything (100x faster than system search)."
        "Requires Everything installed and es.exe placed in PATH or Everything installation directory."
        "Supports wildcards, e.g. *.py, report*.docx"
    ),
    parameters={
        "query":       {"type": "string", "description": "Search keyword or wildcard, e.g. *.py, report*.docx", "required": True},
        "max_results": {"type": "integer", "description": "Max results to return, default 20"},
        "search_path": {"type": "string", "description": "Limit search directory (e.g. D:\\Projects), empty means full disk"},
    },
    risk="low"
)
def everything_search(query: str, max_results: int = 20, search_path: str = "") -> Dict:
    try:
        es = _find_es_exe()
        # If previously cached as failure, re-search (es.exe may have been installed later)
        if not es:
            _reset_es_cache()
            es = _find_es_exe()
        if not es:
            return {
                "ok": False,
                "error": (
                    "es.exe not found. Install Everything (https://www.voidtools.com) "
                    "and download es.exe (https://www.voidtools.com/es.zip) "
                    "and place it in the Everything installation directory or PATH."
                    "\n\nes.exe not found. Install Everything and put es.exe "
                    "in the Everything directory or PATH."
                ),
            }

        cmd = [es, "-n", str(max_results), "-full-path-and-name"]
        if search_path:
            cmd.append("-path")
            cmd.append(search_path)
        cmd.append(query)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            return {"ok": False, "error": stderr or "es.exe execution failed"}

        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        return {
            "ok": True,
            "results": lines,
            "count": len(lines),
            "query": query,
            "search_path": search_path or "(full disk)",
        }

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Search timed out (5s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# Trending tools
# ═══════════════════════════════════════════════════

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _fetch_baidu_trending() -> list:
    """Baidu trending"""
    import httpx
    url = "https://top.baidu.com/api/board?tab=realtime"
    with httpx.Client(headers=_HEADERS, timeout=10, verify=False) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    cards = data.get("data", {}).get("cards", [])
    if not cards:
        return []
    content = cards[0].get("content", [])
    result = []
    for i, item in enumerate(content):
        word = item.get("word", "")
        hot = item.get("hotScore", "")
        if word:
            result.append({"rank": i + 1, "title": word, "hot": str(hot)})
    return result[:30]


def _fetch_sspai_feed() -> list:
    """SSpai RSS"""
    import feedparser
    feed = feedparser.parse("https://sspai.com/feed")
    result = []
    for entry in feed.entries[:10]:
        result.append({
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
        })
    return result


def _fetch_github_trending() -> list:
    """GitHub Trending (Python)"""
    import httpx
    from bs4 import BeautifulSoup

    url = "https://github.com/trending/python?since=daily"
    headers = {**_HEADERS, "Accept": "text/html"}
    with httpx.Client(headers=headers, timeout=10, verify=False) as client:
        resp = client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = soup.select("article.Box-row")
    result = []
    for art in articles[:10]:
        # Repo name in h2 > a
        h2 = art.select_one("h2 a")
        if not h2:
            continue
        repo = "/".join(h2.get_text(strip=True).split())
        # Description in p
        p = art.select_one("p")
        desc = p.get_text(strip=True) if p else ""
        result.append({"repo": repo, "desc": desc})
    return result


@register_tool(
    name="get_trending",
    description="Get Baidu trending, SSpai latest articles, GitHub trending Python projects today",
    parameters={},
    risk="low"
)
def get_trending() -> Dict:
    """Scrape trending data from three platforms and return structured results"""
    baidu, sspai, github = [], [], []
    errors = []

    # Baidu trending
    try:
        baidu = _fetch_baidu_trending()
    except Exception as e:
        errors.append(f"Baidu trending failed: {e}")

    # SSpai
    try:
        sspai = _fetch_sspai_feed()
    except Exception as e:
        errors.append(f"SSpai failed: {e}")

    # GitHub Trending
    try:
        github = _fetch_github_trending()
    except Exception as e:
        errors.append(f"GitHub Trending failed: {e}")

    summary_parts = []
    if baidu:
        summary_parts.append(f"Baidu trending {len(baidu)} items")
    if sspai:
        summary_parts.append(f"SSpai {len(sspai)} items")
    if github:
        summary_parts.append(f"GitHub {len(github)} items")

    result = {
        "ok": True,
        "baidu": baidu,
        "sspai": sspai,
        "github": github,
    }
    if errors:
        result["partial_errors"] = errors
        result["summary"] = ", ".join(summary_parts) + f" (partial failure: {len(errors)}/3)"
    else:
        result["summary"] = ", ".join(summary_parts) + ", all fetched successfully"

    return result


# ═══════════════════════════════════════════════════
# Memory query tool (B layer on-demand call, fallback for memory recall)
# ═══════════════════════════════════════════════════

def _get_memory_store():
    """Get global MemoryStore instance"""
    try:
        from engine.tools import _memory_store_ref
        if _memory_store_ref:
            return _memory_store_ref
    except Exception:
        pass
    return None


@register_tool(
    name="search_memories_by_date",
    description=(
        "Search historical memories by date range. Use only when you cannot recall "
        "a specific time period the user is asking about from your existing memory context. "
        "Do not repeat queries if the relevant information is already in your context. "
        "Date format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS"
    ),
    parameters={
        "start_date": {"type": "string",
                       "description": "Start date, format YYYY-MM-DD", "required": True},
        "end_date":   {"type": "string",
                       "description": "End date, format YYYY-MM-DD", "required": True},
        "level":      {"type": "string",
                       "description": "Memory level: summary/outline/detail, default summary"},
        "top_k":      {"type": "integer",
                       "description": "Max results, default 30"},
    },
    risk="low"
)
def search_memories_by_date(
    start_date: str, end_date: str, level: str = "summary", top_k: int = 30
) -> Dict:
    try:
        from engine.models import MemoryLevel
        from engine.memory import MemoryStore

        store = _get_memory_store()
        if not store:
            # Try direct creation via db_path
            try:
                from desktop.config import DB_FILE
                db_path = DB_FILE
            except Exception:
                import os
                db_path = os.path.join(
                    os.environ.get("APPDATA", str(Path.home())),
                    "AGI-Desktop", "memory.db"
                ) if os.name == "nt" else str(
                    Path.home() / "Desktop" / ".agi-desktop" / "memory.db"
                )
            store = MemoryStore(db_path)

        level_map = {
            "summary": MemoryLevel.SUMMARY,
            "outline": MemoryLevel.OUTLINE,
            "detail":  MemoryLevel.DETAIL,
        }
        mem_level = level_map.get(level, MemoryLevel.SUMMARY)

        # Complete time: pure date auto-filled with 00:00:00 / 23:59:59
        if len(start_date) == 10:
            start_date += "T00:00:00"
        if len(end_date) == 10:
            end_date += "T23:59:59"

        nodes = store.get_by_date_range(
            start_date=start_date,
            end_date=end_date,
            level=mem_level,
            top_k=top_k,
        )

        if not nodes:
            return {"ok": True, "count": 0, "memories": [],
                    "hint": f"No memory records found between {start_date[:10]} ~ {end_date[:10]}"}

        items = []
        for n in nodes:
            items.append({
                "date": n.created_at[:16] if n.created_at else "",
                "content": n.content[:300],
                "importance": n.importance,
                "emotion": n.emotion.primary.value if n.emotion else "",
            })

        return {
            "ok": True,
            "count": len(items),
            "date_range": f"{start_date[:10]} ~ {end_date[:10]}",
            "memories": items,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Global reference: externally inject MemoryStore instance
_memory_store_ref = None


def set_memory_store(store):
    """Called at agent startup, inject MemoryStore instance"""
    global _memory_store_ref
    _memory_store_ref = store


# ═══════════════════════════════════════════════════
# SimLife Schedule management tools
# ═══════════════════════════════════════════════════

@register_tool(
    "add_schedule",
    "Add future plans mentioned by user or system to SimLife schedule. Call when future events appear in conversation.",
    {
        "content": {"type": "string", "description": "Plan content description", "required": True},
        "date": {"type": "string", "description": "Plan date, format YYYY-MM-DD. Supports relative dates like 'tomorrow', 'day after', 'next Monday'", "required": True},
        "category": {"type": "string", "description": "Category: entertainment/work/personal/health/social/other", "required": False},
        "source": {"type": "string", "description": "Source: user / system", "required": False},
    },
    risk="low",
)
def add_schedule(content: str, date: str, category: str = "personal", source: str = "user") -> Dict:
    """Add plan to SimLife schedule (record only, no scheduled execution)"""
    try:
        from datetime import timedelta

        date_lower = date.strip().lower()
        if date_lower in ("tomorrow", "tmr"):
            target = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        elif date_lower in ("day after tomorrow",):
            target = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        elif date_lower in ("day after day after tomorrow",):
            target = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        elif date_lower.startswith("next week"):
            weekdays = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
                        "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
                        "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
            today = datetime.now()
            target_dow = weekdays.get(date_lower.replace("next week", "").strip(), 0)
            current_dow = today.weekday()
            days_ahead = (target_dow - current_dow + 7) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        elif "-" in str(date) and len(str(date)) >= 8:
            target = str(date)[:10]
        else:
            return {"ok": False, "error": f"Cannot parse date: {date}"}

        schedule_path = Path(__file__).resolve().parent.parent / "simlife" / "data" / "scheduled_events.json"
        schedule_path.parent.mkdir(parents=True, exist_ok=True)

        events = []
        if schedule_path.exists():
            with open(schedule_path, "r", encoding="utf-8") as f:
                try:
                    events = json.load(f)
                except json.JSONDecodeError:
                    events = []

        event = {
            "id": f"sch_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(events)}",
            "content": content,
            "scheduled_date": target,
            "scheduled_time_range": "09:00-21:00",
            "category": category,
            "source": source,
            "created_at": datetime.now().isoformat(),
        }
        events.append(event)

        with open(schedule_path, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)

        return {"ok": True, "message": f"Schedule added: {content} ({target})", "event": event}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# Scheduled tasks (TaskScheduler background thread)
# ═══════════════════════════════════════════════════

@register_tool(
    "create_timed_task",
    description=(
        "Create a scheduled task to speak or execute a tool at a specified time."
        "Supports multiple time formats:"
        "1) ISO format '2026-05-21T15:00:00' "
        "2) Simple time '15:00' (today or tomorrow) "
        "3) Relative time '+30m' '+2h' '+1d' (30min/2h/1day from now)"
        "repeat options: daily/weekly/interval:N (every N minutes)/empty means one-time"
    ),
    parameters={
        "content": {"type": "string", "description": "Task description, e.g. 'remind user to drink water'", "required": True},
        "trigger_time": {"type": "string", "description": "Trigger time, e.g. '+30m' or '15:00' or '2026-05-19T15:00:00'", "required": True},
        "action": {"type": "string", "description": "Action: speak / tool", "required": True},
        "message": {"type": "string", "description": "Message to speak when action=speak", "required": False},
        "tool_name": {"type": "string", "description": "Tool name to call when action=tool", "required": False},
        "tool_params": {"type": "object", "description": "Parameters to pass to tool when action=tool", "required": False},
        "repeat": {"type": "string", "description": "Repeat mode: daily/weekly/interval:N, empty for one-time", "required": False},
    },
    risk="medium"
)
def create_timed_task(
    content: str,
    trigger_time: str,
    action: str = "speak",
    message: str = "",
    tool_name: str = "",
    tool_params: dict = None,
    repeat: str = "",
) -> Dict:
    try:
        from engine.task_scheduler import get_scheduler
        scheduler = get_scheduler()

        action_params = {}
        if action == "speak":
            action_params = {"message": message or content}
        elif action == "tool":
            action_params = {"tool_name": tool_name, "tool_params": tool_params or {}}

        result = scheduler.create_task(
            content=content,
            trigger_time=trigger_time,
            action=action,
            action_params=action_params,
            repeat=repeat or None,
            source="system",
        )
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    "cancel_timed_task",
    "Cancel a pending scheduled task. Pass the task ID to cancel.",
    {
        "task_id": {"type": "string", "description": "Task ID to cancel", "required": True},
    },
    risk="low"
)
def cancel_timed_task(task_id: str) -> Dict:
    try:
        from engine.task_scheduler import get_scheduler
        scheduler = get_scheduler()
        return scheduler.cancel_task(task_id)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    "list_timed_tasks",
    "List all currently pending scheduled tasks.",
    {
        "status": {"type": "string", "description": "Filter status: pending / done / all", "required": False},
    },
    risk="low"
)
def list_timed_tasks(status: str = "pending") -> Dict:
    try:
        from engine.task_scheduler import get_scheduler
        scheduler = get_scheduler()
        if status == "all":
            tasks = scheduler._tasks
        else:
            tasks = scheduler.list_tasks(status)
        if not tasks:
            return {"ok": True, "tasks": [], "message": "No scheduled tasks"}
        summaries = []
        for t in tasks:
            s = f"[{t['id']}] {t['content']} \u2192 {t['trigger_time'][:16]} ({t['action']})"
            if t.get("repeat"):
                s += f" repeat:{t['repeat']}"
            summaries.append(s)
        return {"ok": True, "tasks": tasks, "summary": "\n".join(summaries)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════
# Tool Execution Entry Point
# ═══════════════════════════════════════════════════

def execute_tool(name: str, params: dict, user_input: str = "") -> Dict:
    """Execute specified tool and return results. user_input passes user raw message (for tool context awareness)"""
    if name not in TOOL_REGISTRY:
        return {"ok": False, "error": f"Tool '{name}' does not exist"}
    try:
        func = TOOL_REGISTRY[name]["function"]
        # Inject user raw message into function attribute for tools like generate_image_comfy
        if user_input and hasattr(func, '_user_raw_input') is not False:
            func._user_raw_input = user_input
        result = func(**params)
        return result
    except TypeError as e:
        return {"ok": False, "error": f"Parameter error: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Execution exception: {e}"}


def get_tool_risk(name: str) -> str:
    return TOOL_REGISTRY.get(name, {}).get("risk", "low")


def get_all_schemas() -> List[Dict]:
    """Get all tool schemas for passing to LLM"""
    return [info["schema"] for info in TOOL_REGISTRY.values()]


def get_schemas_by_risk(max_risk: str = "high") -> List[Dict]:
    """Filter tools by risk level"""
    risk_order = {"low": 0, "medium": 1, "high": 2}
    max_level = risk_order.get(max_risk, 2)
    return [
        info["schema"] for info in TOOL_REGISTRY.values()
        if risk_order.get(info["risk"], 0) <= max_level
    ]

# ═══════════════════════════════════════════════════
# Tool self-test (check if dependencies are installed)
# ═══════════════════════════════════════════════════

# Tool dependency list
TOOL_DEPS = {
    "screenshot":      ["pyautogui", "PIL"],
    "mouse_click":     ["pyautogui"],
    "keyboard_type":   ["pyautogui"],
    "browser_action":  ["playwright"],
    "read_clipboard":  [],   # Linux needs xclip, built-in on Windows/Mac
    "write_clipboard": [],
    "get_stock_info":  ["yfinance"],
    "search_stock":    ["yfinance"],
    "get_news":        ["newsapi"],
    "get_news_sources":["newsapi"],
    "read_article":    ["newspaper"],
    "get_trending":    ["httpx", "feedparser", "bs4"],
    "everything_search": [],   # Depends on es.exe external program, not a Python package
}

def check_tool_deps(tool_name: str) -> Dict:
    """
    Check if dependencies for a tool are installed
    Returns {ok, missing, installable}
    """
    deps = TOOL_DEPS.get(tool_name, [])
    missing = []
    for dep in deps:
        try:
            __import__(dep)
        except ImportError:
            missing.append(dep)

    install_cmds = {
        "pyautogui":  "pip install pyautogui",
        "PIL":        "pip install Pillow",
        "playwright": "pip install playwright && playwright install chromium",
        "newspaper":  "pip install newspaper3k",
    }
    cmds = [install_cmds.get(m, f"pip install {m}") for m in missing]

    return {
        "ok":      len(missing) == 0,
        "tool":    tool_name,
        "missing": missing,
        "install": cmds,
        "tip":     ("All dependencies installed" if not missing
                    else f"Missing dependencies: {', '.join(missing)}\nInstall commands:\n" + "\n".join(cmds))
    }


def check_all_deps() -> Dict[str, Dict]:
    """Check all tools with dependencies"""
    results = {}
    for tool_name in TOOL_DEPS:
        results[tool_name] = check_tool_deps(tool_name)
    return results


def self_test(tool_name: str = None) -> List[Dict]:
    """
    Tool self-test: test each tool with safe parameters, return test results
    When tool_name=None, tests all safe tools
    """
    safe_tests = {
        "list_directory":  {"path": ".", "pattern": "*.py"},
        "get_system_info": {"info_type": "os"},
        "read_file":       {"path": __file__},          # Read self
        "write_file":      {"path": "agi_self_test.txt",
                            "content": "AGI tool self-test success"},
        "web_search":      {"query": "python", "max_results": 1},
        "fetch_url":       {"url": "http://httpbin.org/get", "max_chars": 200},
        "run_command":     {"command": "echo AGI_TOOL_TEST_OK", "timeout": 5},
        "run_python":      {"code": "print('AGI Python tool test OK')"},
        "search_files":    {"keyword": "def", "directory": ".",
                            "file_pattern": "*.py"},
        "search_stock":    {"keyword": "AAPL", "max_results": 1},
    }

    # Skip auto-test for high-risk tools and tools needing dependencies
    skip = {
        "delete_file", "mouse_click", "keyboard_type",
        "screenshot", "browser_action",
        "read_clipboard", "write_clipboard",
        "open_application", "get_stock_info",
        "get_news", "get_news_sources",   # Requires API Key
        "read_article",                    # Requires network request for articles
        "everything_search",               # Requires external es.exe
    }

    targets = [tool_name] if tool_name else list(safe_tests.keys())
    results = []

    for name in targets:
        if name in skip:
            results.append({"tool": name, "status": "skipped",
                             "reason": "High-risk/needs dependencies, skipping auto-test"})
            continue
        if name not in safe_tests:
            results.append({"tool": name, "status": "no_test_case"})
            continue

        params = safe_tests[name]
        try:
            result = execute_tool(name, params)
            ok = result.get("ok", True)
            results.append({
                "tool":   name,
                "status": "pass" if ok else "fail",
                "result": result
            })
        except Exception as e:
            results.append({
                "tool":   name,
                "status": "error",
                "error":  str(e)
            })

    return results


# ══════════════════════════════════════════
# Office File Tools (read/write docx/xlsx/pptx/pdf)
# ══════════════════════════════════════════

@register_tool(
    name="read_office",
    description="Read Office file or PDF content. Supports .docx .xlsx .pptx .pdf .csv .txt",
    parameters={
        "path": {"type": "string",
                 "description": "File path (absolute or relative)",
                 "required": True}
    },
    risk="low"
)
def read_office(path: str) -> Dict:
    from engine.office_tools import read_office_file
    result = read_office_file(path)
    if result.get("ok"):
        text = result.get("text", "")
        if len(text) > 8000:
            text = text[:8000] + f"\n\n[Content truncated, {len(text)} total chars]"
        return {"ok": True, "type": result.get("type"), "text": text,
                "summary": f"Successfully read {result.get('type','').upper()} file, {len(text)} chars"}
    return result


@register_tool(
    name="create_word",
    description="Create Word document (.docx). Content supports Markdown: # heading, **bold**, - list, | table",
    parameters={
        "path":    {"type": "string", "description": "Save path or filename (e.g. report.docx)", "required": True},
        "content": {"type": "string", "description": "Document content, supports Markdown", "required": True},
        "title":   {"type": "string", "description": "Document title (optional)"}
    },
    risk="medium"
)
def create_word(path: str, content: str, title: str = "") -> Dict:
    from engine.office_tools import create_docx
    return create_docx(path, content, title)


@register_tool(
    name="create_excel",
    description="Create Excel spreadsheet (.xlsx). Pass a 2D array, first row auto-set as header row",
    parameters={
        "path":       {"type": "string", "description": "Save path or filename", "required": True},
        "data":       {"type": "string", "description": "2D array in JSON format, e.g. [[\"Name\",\"Score\"],[\"Alice\",90]]", "required": True},
        "sheet_name": {"type": "string", "description": "Sheet name, default Sheet1"}
    },
    risk="medium"
)
def create_excel(path: str, data: str, sheet_name: str = "Sheet1") -> Dict:
    from engine.office_tools import create_xlsx
    try:
        parsed = json.loads(data)
    except Exception:
        return {"ok": False, "error": "data must be a valid JSON 2D array"}
    return create_xlsx(path, parsed, sheet_name)


@register_tool(
    name="create_ppt",
    description="Create PowerPoint presentation (.pptx)",
    parameters={
        "path":        {"type": "string", "description": "Save path or filename", "required": True},
        "slides_json": {"type": "string",
                       "description": 'JSON array, each item with title/content/bullets, e.g. [{"title":"Intro","bullets":["Point 1","Point 2"]}]',
                       "required": True}
    },
    risk="medium"
)
def create_ppt(path: str, slides_json: str) -> Dict:
    from engine.office_tools import create_pptx
    try:
        slides = json.loads(slides_json)
    except Exception:
        return {"ok": False, "error": "slides_json must be a valid JSON array"}
    return create_pptx(path, slides)


@register_tool(
    name="create_pdf",
    description="Create PDF document. Content supports Markdown heading format",
    parameters={
        "path":    {"type": "string", "description": "Save path or filename (e.g. document.pdf)", "required": True},
        "content": {"type": "string", "description": "Document content, supports # ## headings", "required": True},
        "title":   {"type": "string", "description": "PDF title (optional)"}
    },
    risk="medium"
)
def create_pdf_file(path: str, content: str, title: str = "") -> Dict:
    from engine.office_tools import create_pdf
    return create_pdf(path, content, title)


@register_tool(
    name="analyze_image",
    description="Analyze image content. Uses independent multimodal model (non-text LLM), supports OCR, chart interpretation, scene description, etc. Supports OpenAI GPT-4o / Claude / Gemini / Qwen-VL / GLM-4V / Ollama(llava)",
    parameters={
        "image_path": {"type": "string", "description": "Image file path (jpg/png/gif/webp, etc.)", "required": True},
        "question":   {"type": "string", "description": "Question about the image, auto-describes if not specified"}
    },
    risk="low"
)
def analyze_image_tool(image_path: str, question: str = "") -> Dict:
    from engine.vision_client import create_vision_client
    client = create_vision_client()
    if not client:
        return {"ok": False,
                "error": "Multimodal model not configured",
                "tip": "Please configure multimodal model (Vision) in settings, or click \"Multimodal Config\" on the settings page"}
    result = client.analyze(image_path, question or "Please describe this image in detail, including main objects, scene, text, and other key information.")
    return result


@register_tool(
    name="analyze_video",
    description="Analyze video content. Uses multimodal model to understand video, describing visuals, actions, scenes, etc. Requires Gemini or other video-capable models",
    parameters={
        "video_path": {"type": "string", "description": "Video file path (mp4/webm/mov, etc., recommended under 30s)", "required": True},
        "question":   {"type": "string", "description": "Question about the video, auto-describes if not specified"}
    },
    risk="low"
)
def analyze_video_tool(video_path: str, question: str = "") -> Dict:
    from engine.vision_client import create_vision_client
    client = create_vision_client()
    if not client:
        return {"ok": False,
                "error": "Multimodal model not configured",
                "tip": "Video analysis requires Gemini or other video-capable models, please configure multimodal model in settings"}
    result = client.analyze(video_path, question or "Please describe this video in detail, including scenes, character actions, key events, etc.")
    return result


@register_tool(
    name="analyze_audio",
    description="Analyze audio content. Uses multimodal model to understand audio, can perform speech recognition, music analysis, emotion detection, etc. Requires Gemini or other audio-capable models",
    parameters={
        "audio_path": {"type": "string", "description": "Audio file path (mp3/wav/ogg/m4a, etc.)", "required": True},
        "question":   {"type": "string", "description": "Question about the audio, auto-transcribes and describes if not specified"}
    },
    risk="low"
)
def analyze_audio_tool(audio_path: str, question: str = "") -> Dict:
    from engine.vision_client import create_vision_client
    client = create_vision_client()
    if not client:
        return {"ok": False,
                "error": "Multimodal model not configured",
                "tip": "Audio analysis requires Gemini or other audio-capable models, please configure multimodal model in settings"}
    result = client.analyze(audio_path, question or "Please transcribe and describe this audio content.")
    return result


# ═══════════════════════════════════════════════════
# Speech-to-Text Tool (STT)
# ═══════════════════════════════════════════════════

@register_tool(
    name="stt_tool",
    description=(
        "Speech-to-text tool. Convert audio files to text."
        "Supports iFlytek online, DeepSeek Whisper, and local Whisper backends."
        "Use when user sends voice messages or needs audio transcription."
    ),
    parameters={
        "audio_path": {"type": "string",
                       "description": "Audio file path (wav/mp3/m4a/ogg, etc.), supports absolute and relative paths",
                       "required": True},
        "language":   {"type": "string",
                       "description": "Language code, default zh (Chinese). Supports en, ja, ko, etc."},
    },
    risk="low"
)
def stt_tool(audio_path: str, language: str = "zh") -> Dict:
    """
    STT: audio file -> text
    Runs as a tool plugin, does not modify agent.py main logic
    """
    try:
        from engine.stt_engine import STTEngine
        from desktop.config import load_config

        cfg = load_config()
        engine = STTEngine(cfg)
        engine.language = language

        if not engine.is_available():
            return {
                "ok": False,
                "error": "Speech recognition unavailable",
                "tip": STTEngine.install_guide()
            }

        result = engine.recognize_file(audio_path)
        return result

    except Exception as e:
        return {"ok": False, "error": f"Speech recognition error: {e}"}


@register_tool(
    name="stt_record",
    description=(
        "Record speech and transcribe to text."
        "Opens microphone to record audio for specified duration, then transcribes to text."
        "Requires sounddevice or pyaudio installed."
    ),
    parameters={
        "duration": {"type": "integer",
                     "description": "Recording duration (seconds), default 5s, max 30s"},
    },
    risk="low"
)
def stt_record(duration: int = 5) -> Dict:
    """
    Recording + STT
    """
    try:
        duration = max(1, min(30, duration))

        from engine.stt_engine import STTEngine, record_audio
        from desktop.config import load_config

        cfg = load_config()
        engine = STTEngine(cfg)

        if not engine.is_available():
            return {
                "ok": False,
                "error": "Speech recognition unavailable",
                "tip": STTEngine.install_guide()
            }

        # Record audio
        audio_path = record_audio(duration=duration)
        if not audio_path:
            return {"ok": False, "error": "Recording failed, check microphone or install sounddevice: pip install sounddevice"}

        # Recognize
        result = engine.recognize_file(audio_path)

        # Clean up temp file
        try:
            os.unlink(audio_path)
        except Exception:
            pass

        return result

    except Exception as e:
        return {"ok": False, "error": f"Recording/recognition error: {e}"}


# ═══════════════════════════════════════════════════
# Text-to-Speech Tool (TTS)
# ═══════════════════════════════════════════════════

@register_tool(
    name="tts_tool",
    description=(
        "Text-to-speech tool. Convert text to speech and play."
        "Uses Microsoft Edge TTS (high-quality online synthesis) or pyttsx3 (offline fallback)."
        "Use when you need to read replies aloud."
    ),
    parameters={
        "text":    {"type": "string", "description": "Text to read aloud", "required": True},
        "voice":   {"type": "string", "description": "Voice ID, e.g. zh-CN-XiaoxiaoNeural (default female), zh-CN-YunjianNeural (male)"},
        "save_to": {"type": "string", "description": "Save to file path (optional, plays directly if not specified)"},
    },
    risk="low"
)
def tts_tool(text: str, voice: str = "", save_to: str = "") -> Dict:
    """
    TTS: text -> speech play/save
    Reuses existing TTSEngine (edge-tts / pyttsx3)
    """
    try:
        from engine.tts_engine import get_tts
        import tempfile

        tts = get_tts()

        if not tts.is_available():
            return {
                "ok": False,
                "error": "TTS unavailable",
                "tip": tts.install_guide()
            }

        if voice:
            tts.set_voice(voice)

        if save_to:
            # Save to file
            import asyncio
            try:
                import edge_tts

                async def _save():
                    communicate = edge_tts.Communicate(text=text, voice=tts.voice)
                    await communicate.save(save_to)

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(_save())
                finally:
                    loop.close()

                size = os.path.getsize(save_to) if os.path.exists(save_to) else 0
                return {"ok": True, "saved_to": save_to, "size_bytes": size}
            except Exception as e:
                return {"ok": False, "error": f"Save failed: {e}"}
        else:
            # Async playback (non-blocking tool call)
            result_holder = {"done": False, "ok": False, "error": ""}
            done_event = threading.Event()

            def _on_done():
                result_holder["done"] = True
                result_holder["ok"] = True
                done_event.set()

            def _on_error(err):
                result_holder["error"] = err
                done_event.set()

            tts.speak(text, on_done=_on_done, on_error=_on_error)

            # Wait for playback to start (max 2 seconds)
            done_event.wait(timeout=2)

            return {
                "ok": True,
                "message": f"TTS playback started ({tts.get_backend_name()})",
                "text_preview": text[:100],
                "backend": tts.get_backend_name()
            }

    except Exception as e:
        return {"ok": False, "error": f"TTS error: {e}"}


# ═══════════════════════════════════════════════════
# Sensor Data Tool (Sensor Agent)
# ═══════════════════════════════════════════════════

@register_tool(
    name="sensor_status",
    description=(
        "Query robot/robot dog sensor status."
        "Returns natural language description of battery, temperature, attitude, speed, obstacle distance, etc."
        "Use when you need to know the robot's current physical state."
        "Uses simulated data when no hardware is present."
    ),
    parameters={
        "detailed": {"type": "boolean",
                     "description": "Whether to return detailed data (JSON), default false returns text summary"},
    },
    risk="low"
)
def sensor_status(detailed: bool = False) -> Dict:
    """
    Query sensor status
    Runs as tool plugin, does not modify agent.py main logic
    """
    try:
        from engine.sensor_agent import get_sensor_agent
        from desktop.config import load_config

        cfg = load_config()
        agent = get_sensor_agent(cfg)

        if not agent.is_available():
            return {"ok": False, "error": "Sensor module not enabled"}

        if detailed:
            data = agent.get_all_sensors()
            # Truncate overly large data
            data_str = json.dumps(data, ensure_ascii=False, default=str)
            if len(data_str) > 5000:
                data_str = data_str[:5000] + "...(truncated)"
            return {
                "ok": True,
                "data": json.loads(data_str),
                "formatted": agent.get_status_text()
            }
        else:
            return {
                "ok": True,
                "status_text": agent.get_status_text(),
                "source": "simulated" if agent.mock_mode else "hardware"
            }

    except Exception as e:
        return {"ok": False, "error": f"Sensor query error: {e}"}


@register_tool(
    name="sensor_command",
    description=(
        "Send control commands to robot/robot dog."
        "Supports walk, sit, stand, stop, turn and other basic actions."
    ),
    parameters={
        "command": {"type": "string",
                    "description": "Control command: walk/sit/stand/stop/turn_left/turn_right/speed_up/speed_down",
                    "required": True},
        "params":  {"type": "string",
                    "description": "Additional parameters (JSON format), e.g. {\"speed\": 0.5, \"duration\": 3}"},
    },
    risk="medium"
)
def sensor_command(command: str, params: str = "") -> Dict:
    """
    Send control command to robot
    """
    try:
        from engine.sensor_agent import get_sensor_agent
        from desktop.config import load_config

        cfg = load_config()
        agent = get_sensor_agent(cfg)

        if not agent.is_available():
            return {"ok": False, "error": "Sensor module not enabled"}

        param_dict = {}
        if params:
            try:
                param_dict = json.loads(params)
            except Exception:
                return {"ok": False, "error": "params must be valid JSON format"}

        result = agent.send_command(command, param_dict)
        return result

    except Exception as e:
        return {"ok": False, "error": f"Command send error: {e}"}


# Update tool dependency list
TOOL_DEPS.update({
    "stt_tool":     ["websocket-client"],     # iFlytek
    "stt_record":   ["sounddevice"],           # Recording
    "tts_tool":     ["edge_tts", "pyttsx3"],   # Already in requirements.txt
    "sensor_status": ["paho.mqtt"],            # MQTT
    "sensor_command": ["paho.mqtt"],           # MQTT
})
