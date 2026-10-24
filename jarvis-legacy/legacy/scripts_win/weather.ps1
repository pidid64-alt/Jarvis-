$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$loc = if ($env:JARVIS_WEATHER_LOCATION) { $env:JARVIS_WEATHER_LOCATION } else { "Moscow" }
try {
  $w = Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 "https://wttr.in/$loc?format=%C+%t"
  $cond=$w.Content.Trim()
  if (-not $cond) { throw "empty" }
  Write-Output "Погода в ${loc}: $cond."
} catch {
  Write-Output "Не удалось получить прогноз погоды."
}
