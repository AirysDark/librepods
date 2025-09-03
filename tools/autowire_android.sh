#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

JNI_CPP="$ROOT/android/app/src/main/cpp/jni_bridge.cpp"
CMAKE_ANDROID="$ROOT/android/app/src/main/cpp/CMakeLists.txt"

# Heuristics: look for likely parser files
CANDS=$(git -C "$ROOT" ls-files | grep -E '\.(cpp|cc|c)$' | grep -Ei 'continuity|parser|airpods.*parse|payload' || true)

choose_path() {
  local prompt="$1"
  local default="$2"
  echo "$prompt"
  if [[ -n "$default" ]]; then echo "Detected: $default"; fi
  read -r -p "Path (relative to repo root) [$default]: " ans
  if [[ -z "$ans" ]]; then ans="$default"; fi
  echo "$ans"
}

# pick source file
DEFAULT_SRC=""
if [[ -n "$CANDS" ]]; then
  # prefer src/parser/continuity_parser.cpp if present
  while IFS= read -r f; do
    if [[ "$f" =~ continuity_?parser\.c(pp|c)$ ]]; then DEFAULT_SRC="$f"; break; fi
  done <<< "$CANDS"
  [[ -z "$DEFAULT_SRC" ]] && DEFAULT_SRC="$(echo "$CANDS" | head -n1)"
fi
PARSER_SOURCE=$(choose_path "Enter your parser SOURCE file (the .cpp that decodes the payload to model id)." "$DEFAULT_SRC")

# pick header file
CANDS_H=$(git -C "$ROOT" ls-files | grep -E '\.(h|hpp)$' | grep -Ei 'continuity|parser|airpods.*parse|payload' || true)
DEFAULT_HDR=""
if [[ -n "$CANDS_H" ]]; then
  while IFS= read -r f; do
    if [[ "$f" =~ continuity_?parser\.(h|hpp)$ ]]; then DEFAULT_HDR="$f"; break; fi
  done <<< "$CANDS_H"
  [[ -z "$DEFAULT_HDR" ]] && DEFAULT_HDR="$(echo "$CANDS_H" | head -n1)"
fi
PARSER_HEADER=$(choose_path "Enter your parser HEADER (declares the decode function)." "$DEFAULT_HDR")

# choose call expression
echo "How do we get the model id from your parser?"
echo "  1) uint16_t DecodeModelId(const std::string& payload)"
echo "  2) Parsed Decode(const std::string& payload)   (where Parsed has .model_id)"
read -r -p "Choose [1/2] (default 1): " mode
[[ -z "${mode:-}" ]] && mode=1

if [[ "$mode" == "1" ]]; then
  PARSER_CALL="DecodeModelId(buf)"
else
  PARSER_CALL="Decode(buf).model_id"
fi

# apply replacements
sed -i.bak "s#@@PARSER_SOURCE@@#${PARSER_SOURCE//\//\\/}#g" "$CMAKE_ANDROID"
sed -i.bak "s#@@PARSER_HEADER@@#${PARSER_HEADER//\//\\/}#g" "$JNI_CPP"
sed -i.bak "s#@@PARSER_CALL@@#${PARSER_CALL//\//\\/}#g" "$JNI_CPP"
rm -f "$CMAKE_ANDROID.bak" "$JNI_CPP.bak"

echo "✅ Wired:"
echo "  Parser source: $PARSER_SOURCE"
echo "  Parser header: $PARSER_HEADER"
echo "  Call expr    : $PARSER_CALL"
echo
echo "Next:"
echo "  cd $ROOT/android"
echo "  ./gradlew :app:assembleDebug"
