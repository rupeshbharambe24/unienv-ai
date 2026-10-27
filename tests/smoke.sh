#!/bin/sh
# End-to-end smoke test: real document/data tasks through UniEnv, the way an
# agent runs them. Run it from a fresh non-interactive shell to also test the
# PATH wiring:
#
#   env -i HOME="$HOME" USER="$USER" PATH=/usr/bin:/bin /bin/zsh -c tests/smoke.sh
#
# Exit code is the number of failed tasks. Missing optional tools are SKIPs.
set -u
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ue_smoke.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK" || exit 99
FAIL=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL + 1)); }
skip() { printf '  \033[33mSKIP\033[0m %s (%s)\n' "$1" "$2"; }
has() { command -v "$1" >/dev/null 2>&1; }

echo "UniEnv smoke test in $WORK"
has ue-py || { fail "ue-py on PATH (shell wiring)"; exit 1; }
pass "ue-py on PATH: $(command -v ue-py)"

# Word: create with python-docx, read back with markitdown
if ue-py - <<'EOF'
import docx
d = docx.Document(); d.add_heading("UniEnv report", 1); d.add_paragraph("Hello from python-docx.")
t = d.add_table(rows=2, cols=2); t.cell(0, 0).text = "k"; t.cell(0, 1).text = "v"
d.save("report.docx")
EOF
then pass "docx: create with python-docx"; else fail "docx: create with python-docx"; fi

if ue-py -m markitdown report.docx 2>/dev/null | grep -q "Hello from python-docx"; then
  pass "docx: read as Markdown with markitdown"; else fail "docx: read as Markdown with markitdown"; fi

if has pandoc; then
  if pandoc report.docx -t markdown 2>/dev/null | grep -q "UniEnv report"; then
    pass "docx: pandoc docx → markdown"; else fail "docx: pandoc docx → markdown"; fi
else skip "docx: pandoc" "pandoc not installed"; fi

# Excel: formulas with openpyxl, analysis with pandas
if ue-py - <<'EOF'
import openpyxl, pandas as pd
wb = openpyxl.Workbook(); ws = wb.active
ws.append(["item", "qty", "price"]); ws.append(["a", 2, 3.5]); ws.append(["b", 4, 1.25])
ws["D1"] = "total"; ws["D2"] = "=B2*C2"; ws["D3"] = "=B3*C3"
wb.save("sales.xlsx")
df = pd.read_excel("sales.xlsx")
assert list(df.columns[:3]) == ["item", "qty", "price"], df.columns
EOF
then pass "xlsx: openpyxl write + pandas read"; else fail "xlsx: openpyxl write + pandas read"; fi

# PowerPoint
if ue-py -c "import pptx; p = pptx.Presentation(); s = p.slides.add_slide(p.slide_layouts[1]); s.shapes.title.text = 'UniEnv'; p.save('deck.pptx')"; then
  pass "pptx: create with python-pptx"; else fail "pptx: create with python-pptx"; fi

# Node document libraries
if has ue-node; then
  if ue-node -e "const {Document,Packer,Paragraph}=require('docx');const fs=require('fs');Packer.toBuffer(new Document({sections:[{children:[new Paragraph('hi from docx-js')]}]})).then(b=>fs.writeFileSync('js.docx',b))" && [ -s js.docx ]; then
    pass "docx: create with docx-js via ue-node"; else fail "docx: create with docx-js via ue-node"; fi
  if ue-node -e "const P=require('pptxgenjs');const p=new P();p.addSlide().addText('hi',{x:1,y:1});p.writeFile({fileName:'js.pptx'})" >/dev/null && [ -s js.pptx ]; then
    pass "pptx: create with pptxgenjs via ue-node"; else fail "pptx: create with pptxgenjs via ue-node"; fi
else skip "node batteries" "ue-node missing"; fi

# LibreOffice conversion (docx → pdf), twice in parallel to prove profile isolation
if soffice-safe --version >/dev/null 2>&1; then
  mkdir -p out1 out2
  soffice-safe --convert-to pdf --outdir out1 report.docx >/dev/null 2>&1 &
  soffice-safe --convert-to pdf --outdir out2 report.docx >/dev/null 2>&1 &
  wait
  if [ -s out1/report.pdf ] && [ -s out2/report.pdf ]; then
    pass "LibreOffice: 2 parallel docx → pdf conversions"; else fail "LibreOffice: 2 parallel docx → pdf conversions"; fi
  cp out1/report.pdf report.pdf 2>/dev/null
else skip "LibreOffice conversion" "LibreOffice not installed"; fi

# PDF: generate with reportlab (if no LibreOffice output), extract text with poppler and pypdf
[ -s report.pdf ] || ue-py -c "from reportlab.pdfgen import canvas; c = canvas.Canvas('report.pdf'); c.drawString(72, 720, 'Hello from python-docx.'); c.save()"
if ue-py -c "import pypdf; t = pypdf.PdfReader('report.pdf').pages[0].extract_text(); assert 'Hello' in t, t"; then
  pass "pdf: pypdf text extraction"; else fail "pdf: pypdf text extraction"; fi
if has pdftotext; then
  if pdftotext -layout report.pdf - | grep -q "Hello"; then pass "pdf: pdftotext"; else fail "pdf: pdftotext"; fi
else skip "pdf: pdftotext" "poppler not installed"; fi
if has qpdf; then
  if qpdf --check report.pdf >/dev/null 2>&1; then pass "pdf: qpdf --check"; else fail "pdf: qpdf --check"; fi
else skip "pdf: qpdf" "qpdf not installed"; fi

# OCR: render text to an image, read it back
if has tesseract; then
  if ue-py - <<'EOF'
from PIL import Image, ImageDraw, ImageFont
import pytesseract
img = Image.new("RGB", (900, 160), "white")
try: font = ImageFont.load_default(size=64)
except TypeError: font = ImageFont.load_default()
ImageDraw.Draw(img).text((20, 40), "UNIENV WORKS", fill="black", font=font)
txt = pytesseract.image_to_string(img)
assert "UNIENV" in txt.upper(), repr(txt)
EOF
  then pass "ocr: tesseract via pytesseract"; else fail "ocr: tesseract via pytesseract"; fi
else skip "ocr" "tesseract not installed"; fi

# Data: duckdb over a CSV, headless matplotlib chart
if ue-py - <<'EOF'
import duckdb, pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
pd.DataFrame({"x": [1, 2, 3], "y": [2, 4, 8]}).to_csv("d.csv", index=False)
assert duckdb.sql("select sum(y) from 'd.csv'").fetchall()[0][0] == 14
plt.plot([1, 2, 3], [2, 4, 8]); plt.savefig("chart.png")
EOF
then pass "data: duckdb + pandas + matplotlib"; else fail "data: duckdb + pandas + matplotlib"; fi

echo "smoke: $FAIL failed"
exit "$FAIL"
