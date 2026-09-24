<#
.SYNOPSIS
  Jarvis Control Core — установка на Windows 10/11 (x64).

.DESCRIPTION
  Идемпотентный установщик (повторный запуск пропускает готовые шаги):
    1. Проверяет Python 3.11–3.13 и создаёт venv.
    2. Ставит зависимости: requests, webrtcvad-wheels, piper-tts[http],
       sounddevice; опционально openwakeword (если есть модель wake-word).
    3. Скачивает готовый whisper-server.exe (релиз whisper.cpp v1.9.2),
       если его ещё нет — собирать MSVC не нужно.
    4. Скачивает модели Whisper/Piper, если их нет в models\ (в git они
       уже входят, так что на свежем клонае шаг пропускается).
    5. Регистрирует Scheduled Tasks на вход в систему:
         jarvis-whisper   — STT  (whisper-server, 127.0.0.1:8081)
         jarvis-piper     — TTS  (piper http_server, 127.0.0.1:5000)
         jarvis           — демон (jarvis.py)
         jarvis-autonomy  — фоновые проверки (autonomy.py)
         jarvis-hotkey    — глобальный хоткей Win+J (hotkey-win.py)
         jarvis-wakeword  — только если установлена модель «Джарвис»
    6. Сразу запускает все сервисы.

.PARAMETER Uninstall
  Удаляет Scheduled Tasks (venv и модели не трогает).

.PARAMETER SkipTasks
  Ставит зависимости/бинари, но не регистрирует задачи.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-win.ps1
#>
param(
  [switch]$Uninstall,
  [switch]$SkipTasks
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root 'venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
$Pyw = Join-Path $Venv 'Scripts\pythonw.exe'
$WhisperDir = Join-Path $Root 'whisper-win'
$WhisperSrv = Join-Path $WhisperDir 'Release\whisper-server.exe'
$WhisperZip = Join-Path $Root 'whisper-win.zip'
$WhisperVer = 'v1.9.2'
$Model = Join-Path $Root 'models\ggml-base-q5_1.bin'
$PiperVoice = Join-Path $Root 'models\ru_RU-dmitri-medium.onnx'
$WakewordModels = Get-ChildItem (Join-Path $Root 'models\wakeword') -Filter '*.onnx' -ErrorAction SilentlyContinue
$Tasks = @(
  @{ Name = 'jarvis-whisper';  Exe = $WhisperSrv; Args = "--host 127.0.0.1 --port 8081 -m `"$Model`" -l ru"; Work = $WhisperDir },
  @{ Name = 'jarvis-piper';    Exe = $Py;         Args = "-m piper.http_server --host 127.0.0.1 --port 5000 -m ru_RU-dmitri-medium --data-dir `"$((Join-Path $Root 'models'))`""; Work = $Root },
  @{ Name = 'jarvis';          Exe = $Pyw;        Args = "`"$((Join-Path $Root 'jarvis.py'))`""; Work = $Root },
  @{ Name = 'jarvis-autonomy'; Exe = $Pyw;        Args = "`"$((Join-Path $Root 'autonomy.py'))`""; Work = $Root },
  @{ Name = 'jarvis-hotkey';   Exe = $Pyw;        Args = "`"$((Join-Path $Root 'hotkey-win.py'))`""; Work = $Root }
)
if ($WakewordModels) {
  $Tasks += @{ Name = 'jarvis-wakeword'; Exe = $Pyw; Args = "`"$((Join-Path $Root 'wakeword.py'))`""; Work = $Root }
}

function Write-Step($msg) { Write-Host "-> $msg" -ForegroundColor Cyan }

if ($Uninstall) {
  Write-Step 'Удаляю Scheduled Tasks...'
  foreach ($t in $Tasks) {
    Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "   удалена задача $($t.Name)"
  }
  Write-Host 'Готово. venv и models не тронуты.' -ForegroundColor Green
  exit 0
}

Write-Host '== Jarvis Control Core: установка на Windows ==' -ForegroundColor Green
Write-Host "Каталог: $Root"

# --- 1. Python ---------------------------------------------------------------
Write-Step 'Проверяю Python...'
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) {
  Write-Host 'Python не найден. Поставь Python 3.11–3.13 с python.org (галочка "Add to PATH").' -ForegroundColor Red
  Write-Host 'Важно: 3.14 пока не подходит — под неё нет webrtcvad-wheels.' -ForegroundColor Yellow
  exit 1
}
$pyVer = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
$pyMajor, $pyMinor = $pyVer.Split('.')
if ([int]$pyMajor -ne 3 -or [int]$pyMinor -lt 11 -or [int]$pyMinor -gt 13) {
  Write-Host "Найден Python $pyVer — нужен 3.11, 3.12 или 3.13 (webrtcvad-wheels)." -ForegroundColor Red
  exit 1
}
Write-Host "   Python $pyVer — ок"

# --- 2. venv + зависимости ---------------------------------------------------
if (-not (Test-Path $Py)) {
  Write-Step 'Создаю venv...'
  & python -m venv $Venv
  if ($LASTEXITCODE -ne 0) { Write-Host 'venv не создался.' -ForegroundColor Red; exit 1 }
}
Write-Step 'Ставлю зависимости в venv (займёт минуту-другую)...'
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet requests webrtcvad-wheels sounddevice numpy "piper-tts[http]"
if ($LASTEXITCODE -ne 0) { Write-Host 'pip не смог поставить базовые зависимости.' -ForegroundColor Red; exit 1 }
if ($WakewordModels) {
  Write-Step 'Нашёл модель wake-word — ставлю openwakeword...'
  & $Py -m pip install --quiet openwakeword
}

# --- 3. whisper-server.exe ---------------------------------------------------
if (-not (Test-Path $WhisperSrv)) {
  Write-Step "Скачиваю готовый whisper-server ($WhisperVer, ~9МБ)..."
  $url = "https://github.com/ggml-org/whisper.cpp/releases/download/$WhisperVer/whisper-bin-x64.zip"
  try {
    Invoke-WebRequest -Uri $url -OutFile $WhisperZip -UseBasicParsing -TimeoutSec 120
  } catch {
    Write-Host "Не удалось скачать whisper.cpp: $_" -ForegroundColor Red
    Write-Host "Скачай вручную $url и распакуй в $WhisperDir\Release\" -ForegroundColor Yellow
    exit 1
  }
  if (Test-Path $WhisperDir) { Remove-Item $WhisperDir -Recurse -Force }
  Expand-Archive -Path $WhisperZip -DestinationPath $WhisperDir -Force
  Remove-Item $WhisperZip -Force
  # В zip структура Release\... — если вдруг иначе, нормализуем.
  if (-not (Test-Path $WhisperSrv)) {
    $found = Get-ChildItem $WhisperDir -Recurse -Filter 'whisper-server.exe' | Select-Object -First 1
    if ($found) {
      $dest = Join-Path $WhisperDir 'Release'
      New-Item -ItemType Directory -Force -Path $dest | Out-Null
      Copy-Item (Join-Path $found.DirectoryName '*') $dest -Recurse -Force
    }
  }
}
if (-not (Test-Path $WhisperSrv)) {
  Write-Host "whisper-server.exe не оказался в $WhisperSrv — шаг с STT нужен вручную." -ForegroundColor Yellow
}

# --- 4. Модели ---------------------------------------------------------------
if (-not (Test-Path $Model)) {
  Write-Step 'Скачиваю модель Whisper base-q5_1 (~60МБ)...'
  $mUrl = 'https://huggingface.co/ggml-org/whisper.cpp/resolve/main/ggml-base-q5_1.bin'
  try { Invoke-WebRequest -Uri $mUrl -OutFile $Model -UseBasicParsing } catch {
    Write-Host "Модель не скачалась: $_" -ForegroundColor Red; exit 1
  }
}
if (-not (Test-Path $PiperVoice)) {
  Write-Step 'Скачиваю голос Piper ru_RU-dmitri-medium (~63МБ)...'
  $vBase = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium'
  try {
    Invoke-WebRequest -Uri "$vBase/ru_RU-dmitri-medium.onnx" -OutFile $PiperVoice -UseBasicParsing
    Invoke-WebRequest -Uri "$vBase/ru_RU-dmitri-medium.onnx.json" -OutFile "$PiperVoice.json" -UseBasicParsing
  } catch {
    Write-Host "Голос не скачался: $_" -ForegroundColor Red; exit 1
  }
}

# --- 5. Env-файл для LLM (опционально) --------------------------------------
$EnvDir = Join-Path $env:USERPROFILE '.config\jarvis'
$EnvFile = Join-Path $EnvDir 'env'
if (-not (Test-Path $EnvFile)) {
  New-Item -ItemType Directory -Force -Path $EnvDir | Out-Null
  @(
    '# Ключи для LLM-маршрута (по строке KEY=VALUE). Без ключа голосовые'
    '# команды из белого списка работают как обычно, LLM просто выключен.'
    '# OPENROUTER_API_KEY=sk-or-...'
  ) | Set-Content -Path $EnvFile -Encoding UTF8
  Write-Step "Создан шаблон env-файла: $EnvFile"
}

# --- 6. Scheduled Tasks ------------------------------------------------------
if (-not $SkipTasks) {
  Write-Step 'Регистрирую Scheduled Tasks (на вход в систему)...'
  $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
  foreach ($t in $Tasks) {
    if ($t.Name -ne 'jarvis-whisper' -or (Test-Path $WhisperSrv)) {
      if (-not (Test-Path $t.Exe)) {
        Write-Host "   пропуск $($t.Name): нет $($t.Exe)" -ForegroundColor Yellow
        continue
      }
    } else {
      Write-Host "   пропуск jarvis-whisper: нет whisper-server.exe" -ForegroundColor Yellow
      continue
    }
    $action = New-ScheduledTaskAction -Execute $t.Exe -Argument $t.Args -WorkingDirectory $t.Work
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "   задача $($t.Name) зарегистрирована"
  }

  Write-Step 'Запускаю сервисы прямо сейчас...'
  # STT/TTS — синхронно, остальное — сразу после.
  if (Test-Path $WhisperSrv) { Start-ScheduledTask -TaskName 'jarvis-whisper' -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 2
  foreach ($n in @('jarvis-piper', 'jarvis', 'jarvis-autonomy', 'jarvis-hotkey', 'jarvis-wakeword')) {
    Start-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
  }
  Start-Sleep -Seconds 3
}

@"

===========================================================
Установка завершена. Осталось руками:
===========================================================

1) Проверь, что STT/TTS отвечают:
     curl http://127.0.0.1:8081/health
     curl -X POST -H "Content-Type: application/json" -d "{\"text\":\"Привет\"}" http://127.0.0.1:5000/synthesize -o reply.wav

2) Проверь весь конвейер без хоткея:
     venv\Scripts\python.exe jarvis.py --once --debug

3) Дальше — Win+J (хоткей jarvis-hotkey), дождись уведомления
   «Слушаю…», говори команду. Список — commands-win.json.

Ключ LLM (опционально): впиши OPENROUTER_API_KEY=... в
  $EnvFile
и перезапусти задачу jarvis.

Статус задач:
  Get-ScheduledTask jarvis* | Format-Table TaskName, State
Логи:
  %USERPROFILE%\.local\share\jarvis\jarvis.log
Откат:
  powershell -ExecutionPolicy Bypass -File install-win.ps1 -Uninstall
"@
