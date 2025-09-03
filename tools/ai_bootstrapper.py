#!/usr/bin/env python3
import os, pathlib, textwrap

ROOT = pathlib.Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
WF.mkdir(parents=True, exist_ok=True)

# ---------- Detection helpers ----------
def exists_any(patterns):
    for pat in patterns:
        if list(ROOT.glob(pat)):
            return True
    return False

def detect_types():
    types = []
    if (ROOT / "gradlew").exists() or exists_any(["android/**/gradlew", "**/build.gradle*", "**/settings.gradle*"]):
        types.append("android")
    if (ROOT / "CMakeLists.txt").exists() or exists_any(["**/CMakeLists.txt"]):
        types.append("cmake")
    if (ROOT / "package.json").exists():
        types.append("node")
    if (ROOT / "setup.py").exists() or (ROOT / "pyproject.toml").exists():
        types.append("python")
    if (ROOT / "Cargo.toml").exists():
        types.append("rust")
    if exists_any(["*.sln", "**/*.csproj", "**/*.fsproj"]):
        types.append("dotnet")
    if (ROOT / "pom.xml").exists():
        types.append("maven")
    if (ROOT / "pubspec.yaml").exists():
        types.append("flutter")
    if (ROOT / "go.mod").exists():
        types.append("go")
    if not types:
        types.append("unknown")
    return types

# ---------- Build commands ----------
BUILD_CMDS = {
    "android": "./gradlew assembleDebug --stacktrace",
    "cmake":   "cmake -S . -B build && cmake --build build -j",
    "node":    "npm ci && npm run build --if-present",
    "python":  "pip install -e . && pytest || python -m pytest",
    "rust":    "cargo build --locked --all-targets --verbose",
    "dotnet":  "dotnet restore && dotnet build -c Release",
    "maven":   "mvn -B package --file pom.xml",
    "flutter": "flutter build apk --debug",
    "go":      "go build ./...",
    "unknown": "echo 'No build system detected' && exit 1",
}

# ---------- Type-specific setup ----------
def setup_steps(ptype: str) -> str:
    if ptype == "android":
        return textwrap.dedent("""
          - uses: actions/setup-java@v4
            with: { distribution: temurin, java-version: "17" }
          - uses: android-actions/setup-android@v3
          - run: yes | sdkmanager --licenses
          - run: sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0"
        """)
    if ptype == "node":
        return textwrap.dedent("""
          - uses: actions/setup-node@v4
            with: { node-version: "20" }
        """)
    if ptype == "rust":
        return textwrap.dedent("""
          - uses: dtolnay/rust-toolchain@stable
          - run: rustc --version && cargo --version
        """)
    if ptype == "dotnet":
        return textwrap.dedent("""
          - uses: actions/setup-dotnet@v4
            with: { dotnet-version: "8.0.x" }
          - run: dotnet --info
        """)
    if ptype == "maven":
        return textwrap.dedent("""
          - uses: actions/setup-java@v4
            with: { distribution: temurin, java-version: "17" }
          - run: mvn --version
        """)
    if ptype == "flutter":
        return textwrap.dedent("""
          - uses: subosito/flutter-action@v2
            with: { flutter-version: "3.22.0" }
          - run: flutter --version
        """)
    if ptype == "go":
        return textwrap.dedent("""
          - uses: actions/setup-go@v5
            with: { go-version: "1.22" }
          - run: go version
        """)
    # cmake/python/unknown don't need extra setup beyond setup-python
    return ""

# ---------- Common AI steps (llama.cpp + TinyLlama + fixer) ----------
def common_ai():
    return textwrap.dedent("""
      - name: Build llama.cpp (CMake, no CURL)
        run: |
          git clone --depth=1 https://github.com/ggml-org/llama.cpp
          cd llama.cpp
          cmake -S . -B build -D CMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF
          cmake --build build -j
          echo "LLAMA_CPP_BIN=$PWD/build/bin/llama-cli" >> $GITHUB_ENV

      - name: Fetch GGUF model (TinyLlama)
        run: |
          mkdir -p models
          curl -L -o models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf \
            https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf

      - name: Attempt AI auto-fix (OpenAI → llama fallback)
        if: always() && steps.build.outputs.EXIT_CODE != '0'
        env:
          PROVIDER: openai
          FALLBACK_PROVIDER: llama
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          OPENAI_MODEL: ${{ vars.OPENAI_MODEL || 'gpt-4o-mini' }}
          MODEL_PATH: models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf
          AI_BUILDER_ATTEMPTS: "2"
          BUILD_CMD: ${{ steps.build.outputs.BUILD_CMD }}
        run: python3 tools/ai_autobuilder.py || true
    """)

# ---------- Workflow writer ----------
def write_workflow(ptype: str, cmd: str):
    setup = setup_steps(ptype)
    yaml = f"""
    name: AI Autobuilder — {ptype.capitalize()} (generated)

    on: [push, pull_request, workflow_dispatch]

    jobs:
      build:
        runs-on: ubuntu-latest
        permissions:
          contents: write
          pull-requests: write
        steps:
          - uses: actions/checkout@v4

          - uses: actions/setup-python@v5
            with: {{ python-version: "3.11" }}
          - run: pip install requests
{setup if setup.strip() else ""}
          - name: Build (capture)
            id: build
            shell: bash
            run: |
              set -euxo pipefail
              CMD="{cmd}"
              echo "BUILD_CMD=$CMD" >> "$GITHUB_OUTPUT"
              set +e; bash -lc "$CMD" | tee build.log; EXIT=$?; set -e
              echo "EXIT_CODE=$EXIT" >> "$GITHUB_OUTPUT"
              [ -f build.log ] || echo "(no build output captured)" > build.log
              exit 0
            continue-on-error: true
{common_ai()}
    """
    out = WF / f"ai-autobuilder-{ptype}.yml"
    out.write_text(textwrap.dedent(yaml))
    print(f"✅ Generated: {out.name}")

# ---------- Main ----------
def main():
    types = detect_types()
    for t in types:
        write_workflow(t, BUILD_CMDS[t])
    print(f"Done. Generated {len(types)} workflow(s) in {WF}")

if __name__ == "__main__":
    main()