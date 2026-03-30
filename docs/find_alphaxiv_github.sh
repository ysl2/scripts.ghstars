#!/usr/bin/env bash
set -euo pipefail

# 用法:
#   ALPHAXIV_TOKEN=xxxx ./find_alphaxiv_github.sh "论文标题"
#
# 例子:
#   ALPHAXIV_TOKEN=xxxx ./find_alphaxiv_github.sh \
#     "MoRe: Motion-aware Feed-forward 4D Reconstruction Transformer"

if ! command -v curl >/dev/null 2>&1; then
  echo "Error: curl 未安装" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "Error: jq 未安装" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 未安装" >&2
  exit 1
fi

if [ $# -lt 1 ]; then
  echo "Usage: ALPHAXIV_TOKEN=... $0 \"paper title\"" >&2
  exit 1
fi

TITLE="$1"
ARXIV_API_URL="https://export.arxiv.org/api/query"
ALPHAXIV_API_BASE="https://api.alphaxiv.org"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

ARXIV_XML="$tmpdir/arxiv.xml"
ALPHAXIV_JSON="$tmpdir/alphaxiv.json"

echo "[1/3] 通过标题查询 arXiv ID..."
curl -fsSLG "$ARXIV_API_URL" \
  --data-urlencode "search_query=ti:\"$TITLE\"" \
  --data-urlencode "start=0" \
  --data-urlencode "max_results=10" \
  > "$ARXIV_XML"

ARXIV_ID="$(
python3 - "$TITLE" "$ARXIV_XML" <<'PY'
import re
import sys
import xml.etree.ElementTree as ET

title_query = sys.argv[1].strip().lower()
xml_path = sys.argv[2]

ns = {"a": "http://www.w3.org/2005/Atom"}
root = ET.parse(xml_path).getroot()

best_id = None
best_score = -1

for entry in root.findall("a:entry", ns):
    title_el = entry.find("a:title", ns)
    id_el = entry.find("a:id", ns)
    if title_el is None or id_el is None:
        continue

    title = " ".join(title_el.text.split()).strip()
    entry_id = id_el.text.strip()

    m = re.search(r'/abs/([0-9]{4}\.[0-9]{4,5})(v\d+)?$', entry_id)
    if not m:
        continue

    arxiv_id = m.group(1)

    # 简单打分：完全匹配优先，其次标题包含
    score = 0
    tl = title.lower()
    if tl == title_query:
        score = 100
    elif title_query in tl:
        score = 80
    elif tl in title_query:
        score = 60

    if score > best_score:
        best_score = score
        best_id = arxiv_id

if not best_id:
    sys.exit(2)

print(best_id)
PY
)" || {
  echo "Error: 没从 arXiv 标题检索中找到匹配论文" >&2
  exit 2
}

echo "    arXiv ID = $ARXIV_ID"

echo "[2/3] 查询 AlphaXiv 论文 JSON..."
if [ -n "${ALPHAXIV_TOKEN:-}" ]; then
  curl -fsSL \
    -H "Authorization: Bearer ${ALPHAXIV_TOKEN}" \
    "${ALPHAXIV_API_BASE}/papers/v3/${ARXIV_ID}" \
    > "$ALPHAXIV_JSON"
else
  curl -fsSL \
    "${ALPHAXIV_API_BASE}/papers/v3/${ARXIV_ID}" \
    > "$ALPHAXIV_JSON"
fi

echo "[3/3] 从 AlphaXiv 返回里抽取 GitHub 仓库链接..."

# 尽量从几个可能的地方找：
# 1) implementation / marimo_implementation / resources
# 2) 整份 JSON 里递归搜索 github.com 字符串
GITHUB_URLS="$(
jq -r '
  [
    (.paper.implementation // empty),
    (.paper.marimo_implementation // empty),
    (.paper.paper_group.resources // empty),
    (.paper.resources // empty),
    .
  ]
  | map(
      ..
      | strings
      | select(test("^https?://(www\\.)?github\\.com/"))
    )
  | add
  | unique
  | .[]
' "$ALPHAXIV_JSON" 2>/dev/null || true
)"

if [ -n "$GITHUB_URLS" ]; then
  echo
  echo "找到 GitHub 链接："
  echo "$GITHUB_URLS"
  exit 0
fi

echo
echo "AlphaXiv 返回里没有直接找到 GitHub 链接。"
echo "建议你检查原始 JSON： $ALPHAXIV_JSON"
echo
echo "可先看这些字段："
jq '
{
  title: .paper.paper_version.title,
  implementation: .paper.implementation,
  marimo_implementation: .paper.marimo_implementation,
  resources: (.paper.paper_group.resources // .paper.resources // null),
  source: .paper.paper_group.source
}
' "$ALPHAXIV_JSON" || true
