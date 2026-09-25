$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
param(
  [switch]$AutoReboot,
  [switch]$SkipUpdate,
  [switch]$OnlyServices
)
# Сэр-стайл интро — рандом
$intros = @(
  "Сэр, я тут подглядел —",
  "Сэр, мне тут птичка нашептала, что",
  "Сэр, докладываю —"
)
function Intro($msg){ return "$(($intros | Get-Random)) $msg" }

$rebootNeeded = $false
$updInstalled = 0
$updTotal = 0

# 1) Поиск обнов через winget (если не SkipUpdate и не OnlyServices)
if (-not $SkipUpdate -and -not $OnlyServices) {
  Write-Output (Intro "ищу обновления...")
  try {
    $out = & winget upgrade --accept-source-agreements --disable-interactivity 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) {
      # Считаем строки с пакетами (пропускаем шапку/черточки/служебные)
      $lines = $out | Where-Object { $_ -match '^\S+\s+\S+' -and $_ -notmatch '^\s*-+' -and $_ -notmatch 'Name\s+Id|Version|Available|upgrades available|No applicable| winget ' }
      # Более надёжно: считаем строки где есть точка в версии
      $candidates = $out | Where-Object { $_ -match '\d+\.\d+' -and $_ -notmatch '^\s*--' }
      $updTotal = @($candidates).Count
      if ($updTotal -eq 0) { $updTotal = @($lines).Count }
      if ($updTotal -gt 20) { $updTotal = @($lines).Count } # защита от ложного подсчёта
    }
  } catch { $updTotal = 0 }

  if ($updTotal -le 0) {
    Write-Output "Сэр, обновлений нет — всё свежо, как с иголочки!"
  } else {
    Write-Output (Intro "нашёл $updTotal обновлений — ставлю, это займёт минутку...")
    try {
      $log = & winget upgrade --all --accept-source-agreements --accept-package-agreements --disable-interactivity --silent 2>&1
      if ($LASTEXITCODE -eq 0) {
        $updInstalled = $updTotal
        Write-Output "Сэр, обновил $updInstalled пакетов — готово."
        # winget иногда пишет что нужен рестарт
        if ($log -match 'restart|reboot|перезагрузк') { $rebootNeeded = $true }
      } else {
        # Часть пакетов могла обновиться даже при ненулевом коде
        $updInstalled = $updTotal
        Write-Output "Сэр, часть обновлений поставил, но некоторые требуют ручной установки — код $LASTEXITCODE."
        if ($log -match 'restart|reboot|перезагрузк') { $rebootNeeded = $true }
      }
    } catch {
      Write-Output "Сэр, не удалось запустить winget — $($_.Exception.Message)"
    }
  }
} elseif ($SkipUpdate) {
  Write-Output (Intro "пропускаю проверку обновлений, сразу к сервисам.")
} else {
  Write-Output (Intro "режим только сервисы — обновления не трогаю.")
}

# 2) Перезапуск упавших сервисов (Auto, не Delayed, не игноры)
$ignore = '^(edgeupdate|edgeupdatem|GoogleUpdater|gupdate|Intel.*TPM|MapsBroker|qcmtsuvc|SCardSvr|sppsvc|sppuinotify)'
$failed = Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object {
  $_.StartMode -eq 'Auto' -and $_.State -ne 'Running' -and -not $_.DelayedAutoStart -and $_.Name -notmatch $ignore
}
if (-not $failed) {
  Write-Output "Сэр, упавших сервисов нет — перезапускать нечего."
} else {
  $names = @($failed | Select-Object -ExpandProperty Name)
  $count = $names.Count
  Write-Output (Intro "нашёл $count упавших сервиса — пробую перезапустить...")
  $ok = 0; $failList = @()
  foreach ($s in $failed) {
    try {
      $svc = Get-Service -Name $s.Name -ErrorAction SilentlyContinue
      if ($svc) {
        Start-Service -Name $s.Name -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 800
        $svc.Refresh()
        if ($svc.Status -eq 'Running') { $ok++ } else { $failList += $s.Name }
      } else {
        # Прямо через CIM
        Invoke-CimMethod -InputObject $s -MethodName StartService -ErrorAction SilentlyContinue | Out-Null
        Start-Sleep -Milliseconds 800
        $check = Get-CimInstance Win32_Service -Filter "Name='$($s.Name)'" -ErrorAction SilentlyContinue
        if ($check -and $check.State -eq 'Running') { $ok++ } else { $failList += $s.Name }
      }
    } catch { $failList += $s.Name }
  }
  if ($ok -eq $count) {
    Write-Output "Сэр, перезапустил все $ok сервисов — $($names -join ', ')."
  } elseif ($ok -gt 0) {
    Write-Output "Сэр, перезапустил $ok из $count: $($names | Where-Object { $_ -notin $failList } | Join-String -Separator ', '). Не завелись: $($failList -join ', ')."
  } else {
    Write-Output "Сэр, ни один из $count не завёлся: $($names -join ', ') — нужен ручной взгляд."
  }
}

# 3) Перезапуск задач Jarvis (если есть)
try {
  $tasks = Get-ScheduledTask -TaskName "jarvis*" -ErrorAction SilentlyContinue
  if ($tasks) {
    foreach ($t in $tasks) {
      try { Start-ScheduledTask -TaskName $t.TaskName -ErrorAction SilentlyContinue } catch {}
    }
    Write-Output "Сэр, задачи Jarvis перезапустил — $($tasks.Count) штук."
  }
} catch {}

# 4) Проверка нужна ли перезагрузка (доп. эвристики)
if (-not $rebootNeeded) {
  # Pending reboot флаги Windows Update
  $pending = $false
  if (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired") { $pending = $true }
  if (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending") { $pending = $true }
  if ($pending) { $rebootNeeded = $true }
}

if ($rebootNeeded) {
  if ($AutoReboot) {
    Write-Output "Сэр, обновления просят перезагрузку — перезагружаю через 30 секунд!"
    shutdown.exe /r /t 30 /c "Jarvis: обновления установлены, перезагрузка"
  } else {
    Write-Output "Сэр, нужна перезагрузка чтобы завершить обновления — скажите 'перезагрузи компьютер' когда будете готовы."
  }
} else {
  if ($updInstalled -gt 0) {
    Write-Output "Сэр, всё обновил и перезапустил — перезагрузка не требуется."
  } elseif ($updTotal -eq 0) {
    Write-Output "Сэр, всё и так было свежо — сервисы проверил."
  }
}
