# Установка распознавания голосовых: whisper.cpp + модель + ffmpeg.
#
# Всё кладётся в tools\whisper внутри папки бота — бот найдёт это сам, ничего
# вписывать не нужно. Скрипт можно запускать повторно: уже скачанное
# пропускается.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = Split-Path -Parent $PSScriptRoot
$home_dir = Join-Path $root "tools\whisper"
$model_name = if ($args[0]) { $args[0] } else { "small" }

function Say($text) { Write-Host "  $text" }
function Ok($text)  { Write-Host "  [OK] $text" -ForegroundColor Green }
function Fail($text) {
    Write-Host ""
    Write-Host "  [X] $text" -ForegroundColor Red
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "  Ставлю распознавание голосовых (whisper.cpp)" -ForegroundColor Cyan
Write-Host "  Папка: $home_dir"
Write-Host ""

New-Item -ItemType Directory -Force -Path $home_dir | Out-Null

# ------------------------------------------------------------- 1. whisper.cpp

$binary = Get-ChildItem -Path $home_dir -Recurse -Include "whisper-cli.exe","main.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($binary) {
    Ok "whisper.cpp уже на месте"
} else {
    Say "Скачиваю whisper.cpp..."
    try {
        $release = Invoke-RestMethod "https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest" -Headers @{ "User-Agent" = "diary-bot" }
    } catch {
        Fail "Не смог узнать, какая версия whisper.cpp свежая. Проверь интернет (и VPN, если GitHub недоступен)."
    }

    $asset = $release.assets | Where-Object { $_.name -match "win.*x64.*\.zip$" -or $_.name -match "bin-x64\.zip$" } | Select-Object -First 1
    if (-not $asset) { Fail "В свежем выпуске whisper.cpp нет сборки для Windows. Напиши мне — подберём другую." }

    $zip = Join-Path $env:TEMP $asset.name
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $home_dir -Force
    Remove-Item $zip -Force

    $binary = Get-ChildItem -Path $home_dir -Recurse -Include "whisper-cli.exe","main.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $binary) { Fail "Скачал архив, но программы внутри не нашёл." }
    Ok "whisper.cpp: $($binary.Name)"
}

# ------------------------------------------------------------------ 2. модель

$model = Get-ChildItem -Path $home_dir -Recurse -Filter "ggml-*.bin" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($model) {
    Ok "Модель уже на месте: $($model.Name)"
} else {
    Say "Скачиваю модель ggml-$model_name.bin (это 100-500 МБ, пара минут)..."
    $target = Join-Path $home_dir "ggml-$model_name.bin"
    try {
        Invoke-WebRequest "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$model_name.bin" -OutFile $target
    } catch {
        Fail "Не смог скачать модель. Проверь интернет."
    }
    if ((Get-Item $target).Length -lt 50MB) {
        Remove-Item $target -Force
        Fail "Модель скачалась битой (слишком маленькая). Попробуй ещё раз."
    }
    Ok "Модель: ggml-$model_name.bin"
}

# ------------------------------------------------------------------ 3. ffmpeg

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    $ffmpeg = Get-ChildItem -Path $home_dir -Recurse -Filter "ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
if ($ffmpeg) {
    Ok "ffmpeg на месте"
} else {
    Say "Скачиваю ffmpeg (нужен, чтобы перегнать голосовое в понятный формат)..."
    try {
        $release = Invoke-RestMethod "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest" -Headers @{ "User-Agent" = "diary-bot" }
        $asset = $release.assets | Where-Object { $_.name -match "win64-gpl.*\.zip$" -and $_.name -notmatch "shared" } | Select-Object -First 1
        if (-not $asset) { throw "нет сборки" }

        $zip = Join-Path $env:TEMP $asset.name
        Invoke-WebRequest $asset.browser_download_url -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $home_dir -Force
        Remove-Item $zip -Force
    } catch {
        Fail "Не смог скачать ffmpeg. Можно поставить его самому (ffmpeg.org) и запустить скрипт заново."
    }

    $ffmpeg = Get-ChildItem -Path $home_dir -Recurse -Filter "ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $ffmpeg) { Fail "Скачал архив ffmpeg, но программы внутри не нашёл." }
    Ok "ffmpeg: скачан"
}

# ------------------------------------------------------------------ 4. проверка

Write-Host ""
Say "Проверяю, что всё запускается..."
$binary = Get-ChildItem -Path $home_dir -Recurse -Include "whisper-cli.exe","main.exe" | Select-Object -First 1
try {
    & $binary.FullName --help *> $null
} catch {
    Fail "whisper.cpp не запускается. Обычно помогает установка Visual C++ Redistributable с сайта Microsoft."
}

Write-Host ""
Write-Host "  Готово. Перезапусти бота — в шапке появится 'Голос ✓'." -ForegroundColor Green
Write-Host "  Проверить: пришли боту голосовое сообщение." -ForegroundColor Green
Write-Host ""
