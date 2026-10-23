"""Bundle the front end into one self-contained page (CSS and JS inlined) for the
published preview, which runs in simulation mode because no server answers.
  python app/build_preview.py OUT_DIR      -> OUT_DIR/index.html + OUT_DIR/questions.json"""
import re
import shutil
import sys
from pathlib import Path

src = Path(__file__).resolve().parent / "static"
out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview")
out.mkdir(parents=True, exist_ok=True)
s = (src / "index.html").read_text(encoding="utf-8")
s = re.sub(r'<!doctype html>\s*<html[^>]*>\s*<head>\s*<meta charset="utf-8">\s*<meta name="viewport"[^>]*>\s*', "", s)
s = s.replace("</head>\n<body>\n", "").replace("</body>\n</html>\n", "")
s = s.replace('<link rel="stylesheet" href="styles.css">', "<style>\n" + (src / "styles.css").read_text(encoding="utf-8") + "\n</style>")
s = s.replace('<script src="app.js"></script>', "<script>\n" + (src / "app.js").read_text(encoding="utf-8") + "\n</script>")
(out / "index.html").write_text(s, encoding="utf-8")
shutil.copy2(src / "questions.json", out / "questions.json")
print("wrote", out / "index.html")
