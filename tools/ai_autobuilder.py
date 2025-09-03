#!/usr/bin/env python3
import os, sys, subprocess, json, tempfile, re, pathlib, requests

PROVIDER = os.getenv("PROVIDER", "openai")  # default to OpenAI
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# Fallback settings
FALLBACK_PROVIDER = os.getenv("FALLBACK_PROVIDER", "llama")
LLAMA_CPP_BIN = os.getenv("LLAMA_CPP_BIN", "llama-cli")
LLAMA_MODEL_PATH = os.getenv("MODEL_PATH", "models/Meta-Llama-3-8B-Instruct.Q4_K_M.gguf")

MAX_ATTEMPTS = int(os.getenv("AI_BUILDER_ATTEMPTS", "3"))
BUILD_CMD = os.getenv("BUILD_CMD", "./gradlew assembleDebug --stacktrace")
PROJECT_ROOT = pathlib.Path(os.getenv("PROJECT_ROOT", ".")).resolve()

PROMPT = """You are an automated build fixer. You are working in a Git repository.
Goal: Fix build/test failures by editing files minimally.

Repository file list (truncated):
{repo_tree}

Recent changes (last few commits diff):
{recent_diff}

Build command:
{build_cmd}

Build log tail (last 400 lines):
{build_tail}

Constraints:
- Return ONLY a valid unified diff starting with ---/+++ and @@ hunks.
- Keep edits minimal and safe.
- If build config changes are needed (e.g., Gradle/CMake), include them in the diff.
- Prefer updating deprecated APIs or SDK versions if that's the cause.
- Do NOT modify unrelated files.

Now output the unified diff to fix the error.
"""

def run(cmd, cwd=PROJECT_ROOT, capture=False, check=False):
    if capture:
        return subprocess.run(cmd, cwd=cwd, shell=True, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)
    else:
        return subprocess.run(cmd, cwd=cwd, shell=True, check=check)

def git(*args, capture=False):
    return run("git " + " ".join(args), capture=capture)

def get_repo_tree():
    out = run("git ls-files || true", capture=True)
    files = out.stdout.strip().splitlines()
    return "\n".join(files[:220])

def get_recent_diff():
    out = run("git log --oneline -n 1 || true", capture=True)
    if out.stdout.strip() == "":
        return "(no recent commits)"
    diff = run("git diff --unified=2 -M -C HEAD~5..HEAD || true", capture=True)
    return diff.stdout[-8000:]

def tail_build_log(lines=400):
    p = pathlib.Path("build.log")
    if not p.exists():
        return "(no build log)"
    data = p.read_text(errors="ignore").splitlines()
    return "\n".join(data[-lines:])

def run_build():
    with open("build.log", "wb") as f:
        p = subprocess.Popen(BUILD_CMD, cwd=PROJECT_ROOT, shell=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for line in p.stdout:
            sys.stdout.buffer.write(line)
            f.write(line)
    return p.wait()

def _call_openai(prompt):
    key = os.environ["OPENAI_API_KEY"]
    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if os.getenv("OPENAI_ORG"):
        headers["OpenAI-Organization"] = os.environ["OPENAI_ORG"]

    r = requests.post(url, headers=headers, json=payload, timeout=180)
    if r.status_code >= 400:
        try:
            err = r.json()
        except Exception:
            err = {"raw": r.text}
        print("OpenAI API error:", json.dumps(err, indent=2))
        raise RuntimeError(f"openai_error:{json.dumps(err)}")
    data = r.json()
    return data["choices"][0]["message"]["content"]

def _call_llama(prompt):
    cmd = f'{LLAMA_CPP_BIN} -m "{LLAMA_MODEL_PATH}" -p {json.dumps(prompt)} -n 2048 --temp 0.2'
    out = subprocess.run(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if out.returncode != 0:
        print("llama.cpp error output:\n", out.stdout)
        raise RuntimeError("llama_failed")
    return out.stdout

def call_llm(prompt):
    if PROVIDER == "openai":
        try:
            return _call_openai(prompt)
        except RuntimeError as e:
            if "openai_error" in str(e) and FALLBACK_PROVIDER == "llama":
                print("⚠️ OpenAI failed (quota or request). Falling back to llama.cpp…")
                return _call_llama(prompt)
            raise
    elif PROVIDER == "llama":
        return _call_llama(prompt)
    else:
        raise RuntimeError(f"Unknown PROVIDER={PROVIDER}")

def extract_unified_diff(text):
    m = re.search(r'(?ms)^--- [^\n]+\n\+\+\+ [^\n]+\n', text)
    if not m:
        return None
    start = m.start()
    return text[start:].strip()

def apply_patch(diff_text):
    tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".patch")
    tmp.write(diff_text)
    tmp.close()
    try:
        git("add", "-A")
        run("git diff --staged > .pre_ai_fix.patch || true")
        run(f"git apply --reject --whitespace=fix {tmp.name}", capture=True)
        git("add", "-A")
        run('git commit -m "ai-autobuilder: apply automatic fix" || true')
        return True
    except Exception as e:
        print("Patch apply failed:", e)
        return False
    finally:
        os.unlink(tmp.name)

def main():
    print("== AI Autobuilder (with OpenAI→llama fallback) ==")
    print("Project:", PROJECT_ROOT)
    print(f"Provider: {PROVIDER}, Model: {OPENAI_MODEL}, Fallback: {FALLBACK_PROVIDER}")
    if not (PROJECT_ROOT / ".git").exists():
        run("git init")
        run('git config user.name "ai-autobuilder"')
        run('git config user.email "ai-autobuilder@local"')
        git("add", "-A")
        run('git commit -m "ai-autobuilder: initial snapshot" || true')

    code = run_build()
    if code == 0:
        print("✅ Build already succeeds. Nothing to do.")
        return 0

    attempts = 0
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        print(f"\n== Attempt {attempts}/{MAX_ATTEMPTS} ==")
        prompt = PROMPT.format(
            repo_tree=get_repo_tree(),
            recent_diff=get_recent_diff(),
            build_cmd=BUILD_CMD,
            build_tail=tail_build_log()
        )
        llm_out = call_llm(prompt)
        diff = extract_unified_diff(llm_out)
        if not diff:
            print("LLM did not return a unified diff. Aborting this attempt.")
            break

        print("\n--- Proposed diff (truncated) ---\n")
        print(diff[:1500])
        print("\n--- end preview ---\n")

        if not apply_patch(diff):
            print("Could not apply patch. Stopping.")
            break

        code = run_build()
        if code == 0:
            print("✅ Build fixed!")
            return 0

    print("❌ Still failing after attempts.")
    print("Check build.log and .pre_ai_fix.patch to revert:  git apply -R .pre_ai_fix.patch")
    return 1

if __name__ == "__main__":
    sys.exit(main())
