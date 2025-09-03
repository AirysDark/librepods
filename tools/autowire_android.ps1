$ErrorActionPreference = "Stop"

$ROOT = (Resolve-Path "$PSScriptRoot\..").Path
$JNI_CPP = Join-Path $ROOT "android/app/src/main/cpp/jni_bridge.cpp"
$CMAKE_ANDROID = Join-Path $ROOT "android/app/src/main/cpp/CMakeLists.txt"

function Choose-Path($Prompt, $Default) {
  Write-Host $Prompt
  if ($Default) { Write-Host "Detected: $Default" }
  $ans = Read-Host "Path (relative to repo root) [$Default]"
  if (-not $ans) { $ans = $Default }
  return $ans
}

# candidates
$cands = git -C $ROOT ls-files | Select-String -Pattern '\.(cpp|cc|c)$' | ForEach-Object { $_.Line }
$cands = $cands | Where-Object { $_ -match 'continuity|parser|airpods.*parse|payload' }

$defaultSrc = ""
if ($cands) {
  $defaultSrc = $cands | Where-Object { $_ -match 'continuity_?parser\.c(pp|c)$' } | Select-Object -First 1
  if (-not $defaultSrc) { $defaultSrc = $cands | Select-Object -First 1 }
}
$PARSER_SOURCE = Choose-Path "Enter your parser SOURCE file (.cpp):" $defaultSrc

$candsH = git -C $ROOT ls-files | Select-String -Pattern '\.(h|hpp)$' | ForEach-Object { $_.Line }
$candsH = $candsH | Where-Object { $_ -match 'continuity|parser|airpods.*parse|payload' }
$defaultHdr = ""
if ($candsH) {
  $defaultHdr = $candsH | Where-Object { $_ -match 'continuity_?parser\.(h|hpp)$' } | Select-Object -First 1
  if (-not $defaultHdr) { $defaultHdr = $candsH | Select-Object -First 1 }
}
$PARSER_HEADER = Choose-Path "Enter your parser HEADER (.h/.hpp):" $defaultHdr

Write-Host "How do we get the model id from your parser?"
Write-Host "  1) uint16_t DecodeModelId(const std::string& payload)"
Write-Host "  2) Parsed Decode(const std::string& payload)   (Parsed has .model_id)"
$mode = Read-Host "Choose [1/2] (default 1)"
if (-not $mode) { $mode = "1" }
if ($mode -eq "1") { $PARSER_CALL = "DecodeModelId(buf)" } else { $PARSER_CALL = "Decode(buf).model_id" }

(Get-Content $CMAKE_ANDROID -Raw).Replace('@@PARSER_SOURCE@@', $PARSER_SOURCE) | Set-Content $CMAKE_ANDROID -NoNewline
(Get-Content $JNI_CPP -Raw).Replace('@@PARSER_HEADER@@', $PARSER_HEADER).Replace('@@PARSER_CALL@@', $PARSER_CALL) | Set-Content $JNI_CPP -NoNewline

Write-Host "✅ Wired:"
Write-Host "  Parser source: $PARSER_SOURCE"
Write-Host "  Parser header: $PARSER_HEADER"
Write-Host "  Call expr    : $PARSER_CALL"
Write-Host ""
Write-Host "Next:"
Write-Host "  cd $ROOT/android"
Write-Host "  .\gradlew :app:assembleDebug"
